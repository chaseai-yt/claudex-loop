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

## Detecting exhaustion (why stderr must go to a file)

Redirect Codex stderr to a file instead of `/dev/null`. A quota or auth
failure can present as *exit 0, a valid `thread_id`, and an empty verdict
file* — the actual error (429, "usage limit", 401) appears **only on stderr**.
With `2>/dev/null` exhaustion is indistinguishable from a model that said
nothing.

```bash
codex exec -s read-only --json -o /tmp/codex-verdict.txt "$PROMPT" \
  < /dev/null 2>/tmp/codex-stderr.txt | grep '"type":"thread.started"'
```

Terminal-failure signals, any one of which triggers the protocol:

- stderr contains `429`, `usage limit`, `quota`, or an auth error (`401`);
- the verdict file is empty on exit 0 **twice in a row** (one empty file is a
  stumble — retry once first);
- `scripts/codex_usage.py` reports a window at/over its threshold.

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

1. **Wait.** The reset time is known. Afterwards `codex exec resume $THREAD_ID …`
   continues with full session memory. If the thread is gone, start a fresh
   session and inline the review log so far into the prompt.
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
python scripts/fallback_review.py --plan PLAN.md --out /tmp/verdict.txt
python scripts/fallback_review.py --plan PLAN.md --log PLAN-REVIEW-LOG.md \
    --round 3 --out /tmp/verdict.txt                        # later rounds
python scripts/fallback_review.py --chain --plan PLAN.md --out /tmp/verdict.txt
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
- **Anti-rubber-stamping.** #7's second finding: a local model returned a bare
  `VERDICT: APPROVED` — 17 bytes, zero findings — on a plan another reviewer
  found 42 problems in, and it passed every naive check. Here a reply without
  a valid verdict line, or a round-1 APPROVED with fewer than
  `--min-findings` (default 3) numbered findings, exits 3 = **invalid
  review**, never recorded as a round.
- **Plan-hash binding.** The SHA256 of the plan is stamped into the verdict
  header and printed with the verdict. An approval is valid for exactly that
  plan state; any later edit self-demotes it — re-review instead of keeping
  the tick.

## Log labeling (mandatory)

Every fallback round is recorded as:

```markdown
## Round <n> — <model> (via <reviewer>, fallback — plan-text only, no repo access)
```

and the resolution summary states which rounds ran on which reviewer. A final
APPROVED that includes fallback rounds carries the note that it is weaker than
a full-Codex run; for high-stakes plans, add a confirming Codex round after
the quota resets.
