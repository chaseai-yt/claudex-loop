---
name: codex-build
description: Hand a frozen spec (PLAN.md or any locked plan) to OpenAI Codex to IMPLEMENT with full write access, while Claude stays the spec-writer and reviewer — the exact role-flip of /codex-review. Codex builds from the spec in a --yolo sandbox, Claude reads the full diff like a contributor PR, runs the proof test, and iterates fixes via the SAME Codex session up to MAX_FIX_ROUNDS before taking over. Human approves the diff before any commit. Use when the user says "/codex-build", "have codex build this", "codex implement the plan", "hand the plan to codex", "delegate the build to codex", or right after a plan survives /grill-me-codex, /grill-with-docs-codex, or /codex-review and they choose Codex for implementation (Act 3). Also for standalone delegation: refactors, mechanical migrations, bug fixes with a known repro, test/coverage writing — anything that reads as a work order. NOT for tiny edits (~<20 lines — delegation overhead loses), NOT for design work (if writing the spec forces decisions, that's /grill-me-codex first), NOT for reviewing existing code (/codex:review), and NOT for anything needing Claude-session tools (MCP, secrets, browser).
---

# Codex-Build — Codex Types, Claude Verifies

The role-flip of `/codex-review`: there, Claude builds the plan and Codex critiques read-only. Here, **Codex is the builder with write access; Claude is the spec-writer and reviewer.** Codex implements a frozen spec end-to-end; Claude judges the diff like a contributor PR, demands proof, and iterates fixes in the same Codex session. The human enters at exactly two points: kickoff and diff sign-off.

Adapted from Peter Steinberger's `codex-first` pattern (agent-scripts), rebuilt on this house's verified Codex mechanics.

**Spec quality decides success.** Codex starts with zero session context — everything it needs must be in the prompt. A plan that survived `/grill-me-codex` or `/codex-review` already is a frozen spec; that's the ideal input.

## Prerequisites (verify once, fast)

- `codex --version` ≥ 0.130 (older CLIs error on the default `gpt-5.5` model).
- Codex authenticated (prior `codex login`; ChatGPT account is fine). On auth/model error, surface it — don't silently retry.
- Do NOT pin `-m` or model config (e.g. `model_reasoning_effort`) unless the user asks. Pinning `gpt-5.x-codex` variants 400s on ChatGPT-account auth; config defaults come from `~/.codex/config.toml`.
- **Echo the active model at kickoff** so the user can confirm: read the `model` line from `~/.codex/config.toml` (absent = "CLI default"); state it with the resolved tunables. If the user objects, stop before launching the build.
- **Codex has a native image-generation tool** in `codex exec` sessions (ChatGPT-account backed, no API key; verified 2026-07-08 — it saved a generated PNG to disk headless). Specs may therefore include "generate these image assets yourself" steps: name exact file paths, dimensions, and style in the prompt contract.
- Run from the target repo's root (both `exec` and `resume` then need no `-C`; `resume` doesn't support `-C` anyway).

## Tunables (read from args, else default)

| Var | Default | Meaning |
|-----|---------|---------|
| `SPEC_FILE` | `PLAN.md` | The frozen spec Codex implements. |
| `MAX_FIX_ROUNDS` | `2` | Fix iterations via resume before Claude takes over and finishes directly. |
| `LOG_FILE` | `PLAN-REVIEW-LOG.md` | Append-only build transcript. If it exists (Act 1/2 ran), append `## Act 3 — Build`; else create it. |
| `PROOF_CMD` | from spec | Exact test/verify command Codex must run as proof. If the spec lacks one, ask the user ONE question to get it before launching. |

Echo resolved values before starting.

## Step 0 — Gates (before any Codex launch)

1. **Spec gate.** `SPEC_FILE` must exist and read as a work order (goal, concrete steps, bounds). No spec → offer `/grill-me-codex` (interview first) or `/codex-review` (have a plan, want it stress-tested) instead. If the user insists on building from a rough idea, write the spec WITH them first — that's design, and design stays with Claude.
2. **Clean-tree gate.** `git status -sb`. Codex writes with full access, so the build must start from a tree where its diff is the only diff — otherwise you cannot isolate what it did or revert it cleanly. That requirement is non-negotiable; how you satisfy it depends on whose the dirt is.
   - **The user's own uncommitted work** → STOP and ask them to commit or stash.
   - **Another session's live work** (a parallel agent, a long-running task, anything you did not put there) → do NOT stash it; that takes work out from under a running session. Build in a detached worktree instead, which satisfies the gate properly: `git worktree add --detach <scratch>/<name> HEAD`, copy in any untracked file the spec needs (an untracked `SPEC_FILE` does not exist in a fresh worktree), and run Codex there. Remove it with `git worktree remove` once the diff is merged or abandoned.
   - Either way, say which you chose and why before launching. Never launch into a dirty tree on the reasoning that the subtree you care about happens to be clean.
3. **Path-scope gate**, and it only bites in a worktree. If `SPEC_FILE` cites findings by absolute path (`/Users/.../repo/src/foo.py:210`), those resolve to the ORIGINAL checkout, and a `--yolo` Codex will happily edit it — silently defeating the isolation you just set up. Grep the spec for absolute paths; if there are any, the prompt contract must say: resolve every such path relative to your own repo root, and never write outside the current working directory. State the forbidden prefixes literally.
4. Confirm scope in one line, then go. No round-by-round approvals; the human gate is at the end.

## Step 1 — The build prompt (contract, via temp file)

Never inline-quote the prompt — write it to a temp file. Fill this contract completely; when chained from a grill/review skill, derive it from the plan's sections:

```bash
P=$(mktemp)
cat >"$P" <<'EOF'
GOAL: <one paragraph — what done looks like>
SPEC: Read <SPEC_FILE> at the repo root. It is a frozen, already-reviewed spec.
  Implement it exactly. If a step is impossible as written, implement the
  closest faithful version and report the deviation — do not redesign.
KEY PATHS: <files/dirs Codex will touch or must read first>
CONSTRAINTS: <"don't touch X", style rules, deps that must not change>
NON-GOALS: <explicitly out of scope — from the plan's Out of scope section>
PROOF: Run `<PROOF_CMD>` and include its full output in your report.
OUTPUT: End with a report — files changed (one line each: path + what/why),
  proof output, and any deviations from the spec with reasons.
EOF
```

## Step 2 — Launch Codex (fresh session, capture `thread_id`)

```bash
codex exec --yolo --json -o /tmp/codex-build.txt - <"$P" 2>/dev/null | grep '"type":"thread.started"'
```

- Prompt goes via stdin (`- <"$P"`) — this both avoids quoting bugs AND sidesteps the non-TTY stdin hang (`codex exec` blocks forever waiting on stdin EOF under Claude Code's Bash tool; feeding the file gives immediate EOF).
- Parse `thread_id` from the `{"type":"thread.started","thread_id":"..."}` line → `THREAD_ID`. Codex's final report lands in `/tmp/codex-build.txt` — read that file; don't parse the JSONL stream for content.
- `2>/dev/null` suppresses cosmetic MCP/auth stderr noise. Confirm success by the report file + a `thread.started` line; neither → failed run (auth/model) — stop and tell the user.
- **Timing:** foreground with `timeout: 600000` on the Bash tool call (default 2-min tool timeout kills real builds). If the spec is clearly >10 min of work (multi-file feature, migration, anything with image generation), launch with `run_in_background: true` instead and read the `-o` file when it exits. Don't kill a quiet background run early — Codex builds are legitimately slow.
- **Heads-up on completion (required):** when a background Codex run finishes, the FIRST line of your next message to the user must be a loud standalone banner — `🔔 CODEX FINISHED — <what> (exit ok/fail) — verifying now` — BEFORE any verification output. The user is not watching tool calls; never let a completed build slide silently into the verify phase.

## Step 3 — Verify (Claude, always, never delegated)

Codex's report is advisory, and so is any QA, self-review or verification subagent it ran on its own work. It reports PASS on builds that contain defects — expect that, and treat every claim in the report as a lead to check rather than a result. The whole reason roles are split is that whoever built it never grades it, and that applies to Codex grading itself inside its own session.

Verify yourself:

1. `git status -sb` + read the FULL diff (`git diff`). Judge it like a contributor PR: correctness, spec fidelity, style match with surrounding code, nothing touched outside scope.
2. **Trace every changed test back to a spec line.** A green suite proves the code does what its tests say; it does not prove the tests were asked for. The failure mode to hunt is unrequested code paired with a test asserting it, because that combination reads as verified and sails through a proof run. List the tests the diff added, and for each one name the spec item it covers. A test with no spec line behind it is the tell: either Codex solved a problem the spec had already settled a different way, or it invented a requirement. Both are review findings.
3. Run `PROOF_CMD` yourself (or the focused tests for the changed area). Codex's pasted output doesn't count as proof.
4. **Read the "Deviations" section as a claim, not a summary.** "Deviations: None" is common on builds that did deviate — a spec step that turns out impossible gets quietly resolved the right way and never reported. Where the diff diverges from the spec text, decide whether the spec or the code was wrong, and log which.
5. Append to `LOG_FILE` under `## Act 3 — Build`: `### Round <n> — Codex build` + its report summary + `### Claude's verdict` + what passed/failed review.

## Step 4 — Fix loop (same session, bounded)

Problems found → resume the SAME session (Codex keeps its context; cheaper and better than a fresh run). Write the fix list to a temp file (`$P2`), same contract discipline: exact problem, exact file, proof expected.

```bash
# resume has no --yolo and no -C: run from the repo dir and spell the long flag,
# or Codex inherits config.toml's sandbox (possibly read-only) and can't write.
codex exec resume "$THREAD_ID" --dangerously-bypass-approvals-and-sandbox --json \
  -o /tmp/codex-build.txt - <"$P2" 2>/dev/null >/dev/null
```

Re-verify (Step 3) after each round. After `MAX_FIX_ROUNDS` failed rounds: STOP delegating — Claude takes over and finishes the remaining fixes directly. Log the takeover. Ping-ponging trivia through delegation burns more than it saves.

## Step 5 — Human gate (diff sign-off)

Present: 3-bullet summary of what was built, files-changed list, proof-test output (pass/fail, verbatim tail), rounds used, any spec deviations. Ask: *"Codex built it, proof passes, diff reviewed. Commit?"*

- Commit ONLY on yes — and Claude writes the commit, never Codex.
- Rejected → ask what's wrong, route back to Step 4 (or take over directly if fix rounds are spent).

## Hard rules

- Codex's diff is the only diff in the tree it builds in. Always. A worktree is the way to get that when the dirt belongs to another session.
- Claude never skips the diff read. Every Codex claim is advisory until Claude has read the diff and run the proof, and that includes any QA or self-review Codex ran inside its own session.
- Fix loop terminates at `MAX_FIX_ROUNDS` — then Claude takes over. No unbounded delegation ping-pong.
- Commits, pushes, releases, GitHub mutations: Claude-side only, after the human gate. Codex never commits.
- `LOG_FILE` is the deliverable — with Acts 1/2 it tells the whole story: grilled → reviewed → built → verified.

## What NOT to do

- Don't build without a spec — that's designing by delegation, and it fails. Route to `/grill-me-codex` or `/codex-review` first.
- Don't use for ~<20-line single-obvious-change edits — just make the edit.
- Don't pin `-codex` model variants on ChatGPT-account auth — 400s.
- Don't resume with `--last` — capture and use the explicit `THREAD_ID` (parallel sessions make `--last` grab the wrong thread). And ECHO the id into the command visibly before running: `resume` with a missing/garbage id can silently fall back to the most recent session instead of erroring (observed 2026-07-08) — a wrong-target resume looks exactly like a successful one.
- Don't parse the JSONL stream for the report — read the `-o` file.
- Don't let Codex commit, and don't auto-commit yourself — human gate first.
