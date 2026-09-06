---
name: claudex-loop
description: Four-phase plan hardening (renamed from /crucible 2026-08-16; old triggers still work) — supersedes /grill-me-codex and /grill-with-docs-codex. PHASE 0 RECON — Claude scouts first (codebase + docs on brownfield; prior art, stack, and pitfalls research on greenfield) and drafts an assumptions ledger. PHASE 1 INTERROGATE — confirm the ledger in one batch, then question only the load-bearing decisions one at a time (each with why-it-matters, a recommendation, and what-breaks-if-we-guess-wrong), cosmetic ones batched, with a visible decision map and an accept-all-recommendations escape hatch. PHASE 2 REVIEW — the locked plan goes to PLAN.md and OpenAI Codex adversarially reviews it in a read-only sandbox (VERDICT: APPROVED/REVISE); Claude revises and re-submits to the SAME Codex session until APPROVED or MAX_ROUNDS, then you sign off before any code. PHASE 3 BUILD (optional) — you pick the builder and the models swap jobs: Codex builds via codex-build and Claude reads the full diff + runs the proof itself; Claude builds and a fresh read-only Codex session cross-inspects the diff (on by default, logged opt-out only); either way you approve the final diff. Use when the user says "/claudex-loop", "claudex this", "run the claudex loop", "/crucible" (legacy), "put this through the crucible", "crucible this plan", "grill me then have codex review", "stress-test this plan before we build", or is about to build something high-stakes (auth, schema, concurrency, migrations, payments, greenfield architecture) and wants alignment AND a cross-model sanity check first. Locked plan needing only the Codex loop → /codex-review. Reviewing already-written code → /codex:review. NOT for trivial changes.
---

# Claudex-Loop — Recon, Interrogate, Review, Build

_(Renamed from Crucible 2026-08-16. Old trigger phrases still work.)_

Four phases, four failure modes killed:

- **Phase 0 — RECON** kills *interviewing blind*: Claude scouts the terrain (code or research) before asking you anything, so the interview starts informed instead of generic.
- **Phase 1 — INTERROGATE** kills *building the wrong thing*: Claude interrogates you until intent is locked — but only on decisions that are actually load-bearing.
- **Phase 2 — REVIEW** kills *a plan that sounds right but breaks*: a different model (Codex) attacks the locked plan. Cross-model = no echo chamber.
- **Phase 3 — BUILD** *(optional)* kills *grading your own work*: one model implements the locked plan, the rival model grades the diff — in both directions.

You enter at four points only: confirming the assumptions ledger, answering the fire, signing off the converged plan, and approving the final diff if you build. Codex is read-only throughout recon, interrogation, and review — **no code is written until you sign off the converged plan.**

---

## PHASE 0 — RECON (Claude alone)

Before asking the user a single question, determine the terrain and gather what can be gathered without them.

### Detect the terrain
- **Brownfield** — the working directory has real source code (not just scaffolding/config). Recon the codebase.
- **Greenfield** — empty dir, fresh scaffold, or the user is describing a brand-new project with no repo yet. There is nothing to recon; research replaces it.

### Brownfield recon
1. Explore the codebase: architecture, relevant modules, existing patterns the plan must fit, current schema/auth/infra as applicable.
2. Look for living docs: `CONTEXT.md` (or `CONTEXT-MAP.md` for multi-context repos) and `docs/adr/`. If they exist, load them — the project has a ubiquitous language and prior decisions the plan must respect, and Phase 1 runs **docs-aware** (see below).
3. If the task involves tech or an integration the repo can't answer, open the **research gate** (below) before proceeding.

### Greenfield recon
No code to read, so research carries the phase. Open the **research gate**, then cover:
1. **Prior art** — how do existing tools/products solve this? What's the standard shape?
2. **Stack** — reasonable default stack for this kind of project, with one alternative worth considering.
3. **Known pitfalls** — the 3-5 things people building this class of thing get wrong (search for postmortems, "lessons learned", common gotchas of the candidate stack).

### The research gate (one question, asked at kickoff when external research would help)
Don't silently pick a research depth — offer the tiers with a recommendation based on stakes, and let the user choose:

- **`none`** — Claude's knowledge + codebase only. Right for medium tasks on familiar ground.
- **`web`** — a handful of targeted WebSearch passes (docs, gotchas, prior art). Minutes, not a project. The default recommendation for most greenfield work.
- **`deep`** — launch a **deep-research dynamic workflow** via the Workflow tool: a multi-agent research orchestration (parallel finder agents each searching a different way — prior art, stack landscape, pitfalls/postmortems, docs — then deep-read agents on the best sources, then one synthesis agent producing the brief). Heavy and token-expensive — recommend only for high-stakes greenfield, unfamiliar tech, or when the landscape itself is the question. The user choosing this tier IS the explicit opt-in the Workflow tool requires. **Model pin:** every `agent()` call in the research workflow MUST pass `model: 'opus'` (finders, deep-readers, and the synthesizer alike) — if the main session is on Fable, letting a dozen research agents inherit it annihilates token usage for what is mostly search-and-summarize work. Leave effort at the default — don't pass an `effort` override. **Args gotcha (found in smoke test 2026-08-13):** the workflow runtime may deliver `args` as a JSON-encoded STRING instead of an object — always open the script with `const A = typeof args === 'string' ? JSON.parse(args) : args` and reference `A.*`, or `pipeline(args.questions, ...)` dies instantly with "expects an array".

If invoked with `research=none|web|deep`, skip the question and use that tier.

**If `deep` is chosen: draft the research prompt and get sign-off before launching.** Show the user the topic framing + the 3-5 specific questions the assumptions ledger needs answered (not a generic "research X" — questions shaped like "what do teams building X get wrong about auth?" / "what's the current standard stack for Y and why?"). The user edits or approves, THEN author the workflow script with the approved questions as its `args` and run it. Save the synthesized brief to `docs/research/YYYY-MM-DD-<slug>-claudex-research.md` (or your notes location of choice, with `## Key Takeaways`) — link it from the ledger entries it sourced and from `PLAN.md`.

### Skill inventory scan (both terrains, after terrain detection)
Both benches carry installed skill packs. Enumerate and match against the task's domain:

- **Claude side:** list `~/.claude/skills/` (folder names + frontmatter `description` first lines are enough — don't read full SKILL.mds during recon).
- **Codex side:** list `~/.agents/skills/` (the `skills` CLI's Codex install target).

Filter to skills whose descriptions match the project's domain (e.g. a three.js game matches the `threejs-*` pack; an email feature matches resend skills). Record hits in the Assumptions Ledger as proposed toolchain entries, never auto-loads:

> "threejs-game-skills pack installed on BOTH agents (9 skills incl. aaa-graphics-builder, gameplay-systems, 3d/image/audio generators) — proposing the build phase load graphics-builder + gameplay-systems, and the asset track use the generator skills. — source: skill inventory scan"

If a matched skill exists on only one bench, say which. If a Codex-side skill's loading behavior under headless `codex exec` is unverified, ledger that as an assumption to smoke-test before the build phase counts on it. **Discovery informs the plan; nothing loads unless `PLAN.md`'s `## Toolchain` section names it and survives review.**

### Output: the Assumptions Ledger
End Phase 0 by presenting a single batch — NOT one-at-a-time — of everything Claude resolved on its own:

```markdown
## Assumptions Ledger
_Confirm or correct in one pass. Anything unmarked I treat as confirmed._
1. <assumption> — source: <code path / doc / research finding / convention>
2. ...
```

Each entry cites its source. The user confirms/corrects in one reply. Corrections that open real questions get promoted into the Phase 1 decision map.
 This is the single biggest time-save over a naive grill: the interview never wastes questions on things the repo or the research already answered.

---

## PHASE 1 — INTERROGATE (you ↔ Claude)

The interview. Rebuilt around one principle: **every question must justify its own existence.**

### Open with the Decision Map
Lay out the tree of genuinely open decisions, tiered:

```markdown
## Decision Map
### Load-bearing (asked one at a time)
- [ ] <decision> — irreversible / expensive-if-wrong (schema, auth, data model, concurrency, money, public API)
### Cosmetic (batched with defaults)
- [ ] <decision> — cheap to change later
```

Load-bearing = wrong answer costs a migration, a rewrite, a security hole, or user trust. Cosmetic = renameable, refactorable, swappable. Update the map as questions resolve (check items off, add branches corrections open) so the user can see convergence instead of wondering how many questions are left.

### Load-bearing questions — one at a time, structured
Every question ships in this format:

> **Q<n>: <the question>**
> **Why it matters:** <the dependency or constraint that makes this load-bearing>
> **Recommendation:** <Claude's answer, committed — not a menu>
> **If we guess wrong:** <the concrete failure — migration, rewrite, breach, churn>

Wait for the answer before the next question. If drafting a question and the "if we guess wrong" line comes out weak — the question is cosmetic; demote it to the batch. If mid-interrogation a question turns out answerable from the code or the research, answer it yourself and log it to the ledger instead of asking.

### Cosmetic decisions — one batch
Present the whole cosmetic tier as recommendations with a one-line rationale each. The user vetoes by exception; silence = accepted.

### Escape hatch
At any point the user can say **"accept all remaining recommendations"** — Claude locks every open decision at its recommended answer, logs them as such in the plan, and proceeds. Offer it explicitly if the load-bearing tier exceeds ~8 questions.

### Docs-aware mode (auto-on when Phase 0 found CONTEXT.md/ADRs; offer once on greenfield)
- **Enforce the glossary** — when the user's wording collides with a `CONTEXT.md` definition, stop and resolve it on the spot: quote the glossary's meaning, state the apparent meaning, make them pick.
- **Pin down loose words** — an overloaded or vague term gets a proposed canonical replacement before the conversation continues on top of it.
- **Probe boundaries with scenarios** — when two concepts blur, construct a concrete edge case that forces the line between them to be drawn.
- **Check claims against the code** — when the user asserts how something behaves, verify in the source; a mismatch is surfaced as a question, not silently trusted either way.
- **Maintain `CONTEXT.md` as terms settle** (format: [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md)). Glossary ONLY — never implementation details. Created lazily on the first settled term.
- **Offer ADRs only past the three-part test** — expensive to reverse AND puzzling without context AND a genuine trade-off. Format: [ADR-FORMAT.md](./ADR-FORMAT.md). `docs/adr/` created lazily.

### Lock the plan
When the decision map is fully checked and you're aligned, **write `PLAN.md`**:

```markdown
# Plan: <task>
_Locked via claudex-loop — by Claude + <user>_

## Goal
<one paragraph — reflects what the interrogation actually settled>

## Approach
<numbered, concrete steps>

## Key decisions & tradeoffs
<the contestable choices the interrogation resolved — name them so Codex has something to bite; link any ADRs; mark any locked via the escape hatch>

## Toolchain
<only when the skill inventory scan matched something — which installed skills each build track MUST load and follow, per agent (Claude / Codex), plus any generator skills or MCP capabilities the build depends on. Omit the section entirely on no matches. Reviewable like everything else: Codex should attack unused relevant skills and unjustified inclusions alike>

## Assumptions
<the confirmed ledger — with sources>

## Risks / open questions
<anything still genuinely open>

## Out of scope
<bounds the interrogation established>
```

Initialize `PLAN-REVIEW-LOG.md`:
```markdown
# Plan Review Log: <task>
Phases 0-1 (recon + interrogation) complete — plan locked with the user. MAX_ROUNDS=<n>.
```

---

## PHASE 2 — REVIEW (Claude ↔ Codex)

Hand the locked plan to Codex for adversarial review. Mechanics verified end-to-end (2026-06-04) — do not "improve" the invocations below.

### Prerequisites (verify once, fast)
- `codex --version` ≥ 0.130 (older CLIs error on the CLI's current default model — the default itself changes over time, so don't assume a specific model name; upgrade the CLI rather than pinning `-m` to work around it).
- Codex authenticated (prior `codex login`; ChatGPT account is fine). On auth/model error, surface it — don't silently retry.
- **`--skip-git-repo-check` is REQUIRED on every `codex exec` / `codex exec resume`.** A non-git workspace otherwise dies with `Not inside a trusted directory and --skip-git-repo-check was not specified.` — before the model is reached, with no verdict file and no `thread.started` line, so it looks exactly like an auth failure. `trust_level = "trusted"` in `config.toml` does not substitute for it.
- Do NOT pin `-m`. Use the config default. Pinning `gpt-5.x-codex` variants 400s on ChatGPT-account auth.
- **Echo the active model before Round 1** so the user can confirm: read the `model` line from `~/.codex/config.toml` (absent = "CLI default"); state it with the resolved tunables. If the user objects, stop and let them adjust config before burning a review round.

### Tunables (read from args, else default)
| Var | Default | Meaning |
|-----|---------|---------|
| `MAX_ROUNDS` | `5` | Hard cap on review rounds. The loop ALWAYS terminates here. |
| `PLAN_FILE` | `PLAN.md` | The plan Phase 1 produced. |
| `LOG_FILE` | `PLAN-REVIEW-LOG.md` | Append-only argument transcript. The artifact. |
| `research` | ask | `none` / `web` / `deep` — pre-answers the Phase 0 research gate. `deep` = the deep-research dynamic workflow (prompt still shown for sign-off first). |
| `inspect` | `on` | Post-build cross-inspection of Claude-built code by a fresh read-only Codex session. `off` = skip (logged as an explicit opt-out, never silently). |
| `MAX_INSPECTION_ROUNDS` | `2` | Initial post-build review + one reinspection after accepted fixes. |

If invoked with e.g. `rounds=3`, use that for `MAX_ROUNDS`. Echo resolved values before starting.

### The review prompt (sent each round)
> You are an adversarial reviewer for an implementation plan. Be skeptical and specific — your job is to find what breaks, not to be agreeable. Read the plan at `PLAN.md` (and `CONTEXT.md`/ADRs for domain language, if present) and any repo files you need (you are read-only). Identify concrete flaws: security holes, race conditions, missing edge cases, schema conflicts, wrong assumptions, observability gaps, simpler alternatives. For each, give a one-line fix. Do NOT modify any files. End your reply with EXACTLY one line: `VERDICT: APPROVED` if the plan is sound enough to implement, or `VERDICT: REVISE` if it still has material problems.

(On greenfield there are no repo files — Codex reviews `PLAN.md` and its `## Assumptions` section on their own merits; the assumption sources give it something concrete to attack.)

### Round 1 — fresh session (capture `thread_id`)
```bash
codex exec -s read-only --skip-git-repo-check --json -o /tmp/codex-verdict.txt "$(cat REVIEW_PROMPT)" \
  < /dev/null 2>/dev/null | grep '"type":"thread.started"'
```
Parse `thread_id` from the `{"type":"thread.started","thread_id":"..."}` line → that's `THREAD_ID`. The critique is in `/tmp/codex-verdict.txt`. Confirm success by the verdict file + a `thread.started` line; if neither appears, the run failed (auth/model) — stop and tell the user. `2>/dev/null` suppresses cosmetic MCP/auth stderr noise. **`< /dev/null` is mandatory:** `codex exec` reads stdin *in addition to* the prompt arg, so under a non-interactive driver (Claude Code's Bash tool, CI, any non-TTY pipeline) it blocks forever waiting on stdin EOF — a silent ~0% CPU hang. The redirect gives it immediate EOF.

### Rounds 2..MAX — resume the SAME session (Codex remembers its prior critiques)
```bash
# resume REJECTS -s. Force read-only via -c sandbox_mode, or Codex inherits
# config.toml (possibly danger-full-access) and could WRITE files. This is the
# single most important safety line in the skill — verified 2026-06-04.
codex exec resume "$THREAD_ID" -c sandbox_mode="read-only" --skip-git-repo-check --json \
  -o /tmp/codex-verdict.txt \
  "I revised the plan. Re-review PLAN.md — check whether your prior findings are addressed and flag anything new. End with VERDICT: APPROVED or VERDICT: REVISE." \
  < /dev/null 2>/dev/null >/dev/null
```
Both `codex exec` and `codex exec resume` support `--json` and `-o/--output-last-message`. The `< /dev/null` redirect is required on the resume call too — same non-interactive stdin hang as Round 1.

**Timeout guard (both rounds):** run every `codex exec` / `codex exec resume` with a 10-minute ceiling so any future stall fails loud instead of hanging silently. Via Claude Code's Bash tool, pass `timeout: 600000` on the tool call (the default 2-minute tool timeout is too short for real reviews and would kill them mid-run). In a plain shell, prefix the command with `timeout 600` (Linux / Git Bash) or `gtimeout 600` (macOS via coreutils — stock macOS has no `timeout`). If the ceiling trips, treat it as a failed run: stop and tell the user rather than retrying blind.

### Each round, after Codex returns
1. Read `/tmp/codex-verdict.txt`; append to `LOG_FILE`: `## Round <n> — Codex` + the full critique.
2. Grep the last line for the verdict:
   - `VERDICT: APPROVED` → break to Resolution (converged).
   - `VERDICT: REVISE` → Claude decides **what's actually worth acting on** (Claude is final arbiter — Codex advises, doesn't command). Revise `PLAN_FILE`. Append `### Claude's response` to `LOG_FILE`: what changed, what was rejected, why. Increment round.
3. If round > `MAX_ROUNDS` → break to Resolution (deadlock).

### Resolution (you sign off — final gate)
- **APPROVED:** present the final `PLAN_FILE`, a 3-bullet summary of what the loop improved, and the round count. Ask: *"Interrogated + survived N rounds of Codex. Implement it now — Codex builds it (`/codex-build`), Claude builds it, or stop here?"* Code only on a yes.
- **MAX_ROUNDS hit without APPROVED (deadlock):** do NOT fake convergence. List each unresolved point + Claude's counter-position; hand it to the user to break the tie. A flagged disagreement beats a false "approved."

### PHASE 3 (optional) — BUILD (Codex ↔ Claude, roles flipped)

If the user picks Codex: invoke the `codex-build` skill with `SPEC_FILE=PLAN.md` and the same `LOG_FILE` — it appends `## Act 3 — Build` to the log, so one artifact tells the whole story (reconned → interrogated → reviewed → built → verified). Roles flip: Codex writes the code with full access, Claude reviews the diff and runs the proof. If the user picks Claude, implement directly as usual — then run the **post-build cross-inspection** (below).

### Post-build cross-inspection (default on every Claude-built path)

The doctrine is *whoever made the thing never checks the thing* — that applies to Claude's code too. After Claude implements and the proof gates pass:

1. Launch a **fresh read-only Codex session** (`codex exec -s read-only`, NEW thread — not the Phase 2 thread; the reviewer should see the code cold, not through its own plan critiques). Give it: `PLAN.md`, the base commit, and the code diff. Ask for PR-style findings — correctness, spec fidelity, edge cases, nothing outside scope — no verdict line needed; this is advisory review, not a gate loop.
2. Claude arbitrates each finding: accept (fix it, rerun affected tests) or reject *with a logged reason*. Cap at `MAX_INSPECTION_ROUNDS=2` (initial review + one reinspection after accepted fixes).
3. Append to `LOG_FILE` under `## Post-build inspection`: findings verbatim, Claude's dispositions, rounds used. Present the summary alongside the final diff at the human gate.

Opt-out: `inspect=off` at invocation or the user declining at Resolution. Skipping silently is not allowed — the log must show either the inspection or the explicit opt-out. (Cost: one ~2-5 min Codex invocation at the end of the build; forgetting to ask for review is exactly the failure mode this default exists to prevent.)

---

## Hard rules
- Phases run in order: 0 → 1 → 2. Don't write `PLAN.md` until the interrogation has actually resolved the decision map with the user (or they invoked the escape hatch).
- The assumptions ledger is presented ONCE as a batch — never drip assumptions as individual questions.
- Codex is read-only EVERY round — `-s read-only` first call, `-c sandbox_mode="read-only"` on every resume (resume has no `-s`). It never writes.
- The loop ALWAYS terminates at `MAX_ROUNDS`.
- Claude is final arbiter on every REVISE — incorporate good critiques, reject bad ones *with a logged reason*. Don't cave to everything (defeats the cross-model check) and don't ignore it (defeats the point).
- Code only after the user's final sign-off.
- `LOG_FILE` is the deliverable — keep the whole argument.
- `CONTEXT.md` stays a glossary only — never implementation details.

## What NOT to do
- Don't invoke this skill just to review pre-existing code — that's `/codex:review`. (Code built BY this skill does get reviewed — that's the post-build cross-inspection, and it's on by default.)
- Don't pin a `-codex` model variant on ChatGPT-account auth — it 400s.
- Don't let Codex edit files. Read-only, always.
- Don't skip Phase 1 — the interrogation is half the value.
- Don't ask questions the recon already answered, and don't ask a load-bearing-format question whose "if we guess wrong" is weak — demote it to the cosmetic batch.
- Don't turn Phase 0 into a research project on a medium-stakes task — the research gate exists so the user picks the depth; don't launch the deep-research workflow without an approved prompt.
