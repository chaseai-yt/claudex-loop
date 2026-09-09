# Fallback reviewers — the loop must not dead-end when Codex does

Fixes [#7](https://github.com/chaseai-yt/claudex-loop/issues/7). If Codex hits
its usage limit mid-loop (5-hour or weekly window of the ChatGPT subscription),
runs out of credits, or the service is down, the rounds already spent must not
be wasted and the plan must not sit stranded. This protocol gives the loop
three exits — **wait, switch, or skip** — and hard rules for each.

Two rules hold regardless of which exit is taken (from the discussion in #7):

- **A switch is never automatic, and never silent.** A stumble is not an
  outage: transient failures (network, timeout, unparseable reply) get one
  retry. Only a confirmed terminal failure — real quota exhaustion, or the
  same failure twice in a row — puts the choice to the user, and the user
  chooses.
- **Switching costs something, and the user is told before choosing.** The
  fallback reviewer only sees the plan text (and the log), not the repo. Its
  approval is weaker than one from a reviewer that read the code, and the log
  says so.

## Detecting exhaustion

The shared runner already keeps what this needs: every call prints its artifact
directory, and the `stderr.txt` in it is retained rather than discarded. That
matters because a quota or auth failure can present as a completed-looking CLI
turn with no usable answer -- the actual error (429, "usage limit", 401)
appears **only on stderr**. Discarding it makes exhaustion indistinguishable
from a model that said nothing.

Terminal-failure signals, any one of which triggers the protocol:

- `stderr.txt` contains `429`, `usage limit`, `quota`, or an auth error (`401`);
- the runner reports an empty or malformed result **twice in a row** (one is a
  stumble -- retry once first);
- `scripts/codex_usage.py` reports a window at/over its threshold.

The first two say *the reviewer is gone*. Only the third says *when it comes
back*, which is what the next section is for.

## Knowing when Codex comes back (no API call needed)

Codex CLI writes a `rate_limits` snapshot into its local session rollouts
(`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) after every turn: 5-hour and
weekly window, each with `used_percent` and `resets_at`, plus credit balance.

```bash
python scripts/codex_usage.py           # human-readable; exit 1 at >= 95 % used
python scripts/codex_usage.py --json    # raw snapshot
```

Run it before round 1 (don't start a loop that cannot finish) and after any
suspected exhaustion (tell the user *when* the reviewer returns, not just that
it is gone).

## The three exits (user chooses — present all three with the reset time)

1. **Wait.** The reset time is known. Afterwards the runner's
   `--resume <previous-successful-result.json>` continues with full session
   memory. If that session is gone, start a fresh one and carry the review log
   so far into the `--feedback` file.
2. **Switch to a configured fallback reviewer.** See below. Rounds continue
   counting; the log labels every fallback round.
3. **Skip the review.** Phase 2 ends without a verdict. The log gets an
   explicit entry — `## Review skipped — Codex quota exhausted (<window>,
   resets <time>), decided by <user>, <date>` — and the plan goes to the human
   sign-off gate marked **not cross-reviewed**. Same doctrine as `inspect=off`:
   skipping is allowed, silent skipping never.

## The fallback adapter

One script covers every provider, because they all speak the OpenAI
chat-completions dialect: LM Studio, Ollama (both local — nothing leaves the
machine), OpenRouter, OpenAI, Google Gemini and Anthropic (via their
OpenAI-compatibility endpoints). Reviewers are named profiles in the
environment or a git-ignored `.env` (see [.env.example](./.env.example)):

```bash
python scripts/fallback_review.py --list                    # what is configured
python scripts/fallback_review.py --check                   # preflight all of it
python scripts/fallback_review.py --plan PLAN.md --out "$SCRATCH_DIR/fallback-verdict-r$ROUND.txt"
python scripts/fallback_review.py --plan PLAN.md --log PLAN-REVIEW-LOG.md \
    --round 3 --out "$SCRATCH_DIR/fallback-verdict-r$ROUND.txt"                        # later rounds
python scripts/fallback_review.py --chain --plan PLAN.md --out "$SCRATCH_DIR/fallback-verdict-r$ROUND.txt"
```

With no profiles configured the script refuses with a clear message — the
loop then only has *wait* and *skip*, exactly today's behavior. Backward
compatible by default.

**Preflight (`--check`).** Before burning a round, every provider is probed:
local endpoints (LM Studio/Ollama) only need to be reachable; remote
providers get an auth-validity probe, and **OpenRouter — the only listed
provider with a real balance API — reports remaining credits** (`GET
/credits`). OpenAI, Gemini and Anthropic expose no balance endpoint: there,
exhaustion only shows up as HTTP 402/429 on use, which the chain treats as
terminal for that provider.

**Chain (`--chain`).** The order in `CLAUDEX_REVIEWERS` is a fallback chain:
the script preflights each provider in turn, uses the first viable one, and
moves on when one turns out to be dead, out of credits, or quota-limited
mid-call (terminal: 401/402/403/404/429; transient failures get one retry
first). This does not contradict "never automatic": the user configured the
chain order — that IS the consent — and every skip is printed with its
reason, so the log shows exactly which reviewer ran and which were passed
over and why. If the whole chain fails, the remaining options are *wait* and
*skip*, as above.

Properties, deliberately different from the Codex path:

- **Read-only by construction.** The model gets no file or tool access; the
  plan and the log are inlined into the prompt. #7 demonstrated that a
  vendor's documented "read-only mode" can still write into the repo — so the
  fallback does not have a sandbox to escape in the first place. The price:
  the reviewer cannot open repo files, and its approval is weaker (say so in
  the log).
- **No session memory.** The API is stateless; later rounds pass the log via
  `--log`, which replaces the resumed-session memory Codex had.
- **Rubber-stamping is a real failure mode, but a finding count is not a
  quality score.** #7's second finding: a local model returned a bare
  `VERDICT: APPROVED` -- 17 bytes, zero findings -- on a plan another reviewer
  found 42 problems in, and it passed every naive check. A reply without a
  valid verdict line is therefore always exit 3 = **invalid review**, never
  recorded as a round. A *count* threshold is offered but off by default
  (`--min-findings`, or `CLAUDEX_FALLBACK_MIN_FINDINGS`): zero findings is a
  legitimate verdict, and a sound plan may have no defects. Turn it on when
  the substitute is a small local model, where "looks good to me" is the
  likely output and there is no structured-output validation to catch it.
- **Plan-hash binding.** The SHA256 of the plan is stamped into the verdict
  header and printed with the verdict. An approval is valid for exactly that
  plan state; any later edit self-demotes it — re-review instead of keeping
  the tick.

## Other gates reuse the same machinery

The adapter is not plan-review-specific: `--system-file` swaps in any gate's
own prompt, and `--require-verdicts "NAME:PASSVAL|OTHERVAL,..."` swaps in that
gate's verdict grammar (every named line must appear — missing means invalid,
exit 3; the first value of each entry counts as passing for the rubber-stamp
gate). A downstream acceptance gate uses exactly this to run its
DoD/quality/security verdicts over the same chain.

## Log labeling (mandatory)

Every fallback round is recorded as:

```markdown
## Round <n> — <model> (via <reviewer>, fallback — plan-text only, no repo access)
```

`scripts/fallback_review.py --append-log <LOG_FILE>` writes exactly this entry
(header, status line with the plan hash, full critique) the moment the reply
arrives — including **INVALID attempts**, labeled `INVALID ATTEMPT, does not
count as a round`. **`--append-log` is required for a review run**: the flag was
optional until 2026-08-30, so a rule stated as mandatory here held only for
whoever remembered to pass it, and the heading the code wrote said just
`fallback` — dropping the very limitation this section exists to put in front of
the reader. A rejected reply is still evidence: it shows which model was
tried, why it failed (truncation, missing verdict, rubber stamp), and what the
user decided next. Claude then appends its per-finding dispositions under the
entry, as for any Codex round. Findings never live only in the chat.

and the resolution summary states which rounds ran on which reviewer. A final
APPROVED that includes fallback rounds carries the note that it is weaker than
a full-Codex run; for high-stakes plans, add a confirming Codex round after
the quota resets.
