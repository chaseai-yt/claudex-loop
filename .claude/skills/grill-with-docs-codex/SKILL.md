---
name: grill-with-docs-codex
description: Two-act plan hardening with living documentation. ACT 1 (you ↔ Claude) — Claude interviews you relentlessly about a plan, one question at a time, challenging it against your project's existing domain model and glossary (CONTEXT.md), sharpening fuzzy terms, stress-testing with concrete scenarios, cross-referencing code, and updating CONTEXT.md + ADRs inline as decisions crystallise. ACT 2 (Claude ↔ Codex) — Claude writes the locked plan to PLAN.md and OpenAI Codex adversarially reviews it in a read-only sandbox (VERDICT:APPROVED/REVISE), Claude revises and re-submits to the SAME Codex session until APPROVED or a MAX_ROUNDS cap, then you sign off before any code. Use when the user says "/grill-with-docs-codex", "grill me against the docs then have codex review", "stress-test this against our domain model then get a second model on it", or is about to build something high-stakes in a project with established terminology/ADRs and wants alignment, documentation, AND a cross-model sanity check. Builds on Matt Pocock's grill-with-docs (MIT). NOT for reviewing already-written code (use /codex:review) and NOT for trivial changes.
---

# Grill-with-Docs-Codex — Grill Against Your Domain, Then Get Reviewed

Two acts. Act 1 aligns intent *and* keeps your living docs honest; Act 2 has a different model attack the result.

- **Act 1** is Matt Pocock's `grill-with-docs`, used under MIT (see `THIRD-PARTY-NOTICES.md`). It interrogates you, challenges your plan against `CONTEXT.md`/ADRs, and updates them inline.
- **Act 2** is the original Codex adversarial review loop — cross-model, read-only, bounded.

You enter at two points: answering the grill, and signing off the converged plan.

---

## ACT 1 — GRILL WITH DOCS (you ↔ Claude)

<what-to-do>

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing.

If a question can be answered by exploring the codebase, explore the codebase instead.

</what-to-do>

<supporting-info>

## Domain awareness

During codebase exploration, also look for existing documentation:

### File structure

Most repos have a single context:

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
│       ├── 0001-event-sourced-orders.md
│       └── 0002-postgres-for-write-model.md
└── src/
```

If a `CONTEXT-MAP.md` exists at the root, the repo has multiple contexts. The map points to where each one lives:

```
/
├── CONTEXT-MAP.md
├── docs/
│   └── adr/                          ← system-wide decisions
├── src/
│   ├── ordering/
│   │   ├── CONTEXT.md
│   │   └── docs/adr/                 ← context-specific decisions
│   └── billing/
│       ├── CONTEXT.md
│       └── docs/adr/
```

Create files lazily — only when you have something to write. If no `CONTEXT.md` exists, create one when the first term is resolved. If no `docs/adr/` exists, create it when the first ADR is needed.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing language in `CONTEXT.md`, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y — which is it?"

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account' — do you mean the Customer or the User? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible — which is right?"

### Update CONTEXT.md inline

When a term is resolved, update `CONTEXT.md` right there. Don't batch these up — capture them as they happen. Use the format in [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md).

`CONTEXT.md` should be totally devoid of implementation details. Do not treat `CONTEXT.md` as a spec, a scratch pad, or a repository for implementation decisions. It is a glossary and nothing else.

### Offer ADRs sparingly

Only offer to create an ADR when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If any of the three is missing, skip the ADR. Use the format in [ADR-FORMAT.md](./ADR-FORMAT.md).

</supporting-info>

### Handoff to Act 2

When the decision tree is resolved, the glossary/ADRs are updated, and we're aligned, **write the agreed plan to `PLAN.md`** (use the canonical terms from `CONTEXT.md`), then run Act 2:

```markdown
# Plan: <task>
_Locked via grill-with-docs — by Claude + <user>. Terms per CONTEXT.md._

## Goal
<one paragraph, in the project's ubiquitous language>

## Approach
<numbered, concrete steps>

## Key decisions & tradeoffs
<the contestable choices the grill resolved — link any ADRs created>

## Risks / open questions
<anything still open>

## Out of scope
<bounds>
```

Initialize `PLAN-REVIEW-LOG.md`:
```markdown
# Plan Review Log: <task>
Act 1 (grill-with-docs) complete — plan locked, CONTEXT.md/ADRs updated. MAX_ROUNDS=<n>.
```

---

## ACT 2 — REVIEW (Claude ↔ Codex)

Hand the locked plan to Codex for adversarial review. Mechanics verified end-to-end (2026-06-04).

### Prerequisites
- `codex --version` ≥ 0.130 (older CLIs error on the default `gpt-5.5` model).
- Codex authenticated (`codex login`; ChatGPT account fine). On auth/model error, surface it — don't silently retry.
- Do NOT pin `-m` (config default is used; `gpt-5.x-codex` variants 400 on ChatGPT-account auth).

### Tunables (args, else default)
| Var | Default | Meaning |
|-----|---------|---------|
| `MAX_ROUNDS` | `5` | Hard cap. Loop ALWAYS terminates here. |
| `PLAN_FILE` | `PLAN.md` | The plan from Act 1. |
| `LOG_FILE` | `PLAN-REVIEW-LOG.md` | Append-only argument transcript. |

Invoked with e.g. `rounds=3` → use it. Echo resolved values first.

### Review prompt (each round)
> You are an adversarial reviewer for an implementation plan. Be skeptical and specific — your job is to find what breaks, not to be agreeable. Read the plan at `PLAN.md` (and `CONTEXT.md`/ADRs for the domain language) and any repo files you need (you are read-only). Identify concrete flaws: security holes, race conditions, missing edge cases, schema conflicts, domain-language mismatches, wrong assumptions, observability gaps, simpler alternatives. For each, give a one-line fix. Do NOT modify any files. End with EXACTLY one line: `VERDICT: APPROVED` or `VERDICT: REVISE`.

### Round 1 — fresh session (capture `thread_id`)
```bash
codex exec -s read-only --json -o /tmp/codex-verdict.txt "$(cat REVIEW_PROMPT)" \
  2>/dev/null | grep '"type":"thread.started"'
```
Parse `thread_id` from the `thread.started` line. Critique in `/tmp/codex-verdict.txt`. No verdict file + no `thread.started` = failed run (auth/model) → stop, tell the user. `2>/dev/null` hides cosmetic MCP/auth noise.

### Rounds 2..MAX — resume SAME session
```bash
# resume REJECTS -s. Force read-only via -c sandbox_mode, or Codex inherits
# config.toml (possibly danger-full-access) and could WRITE files. Critical
# safety line — verified 2026-06-04.
codex exec resume "$THREAD_ID" -c sandbox_mode="read-only" --json \
  -o /tmp/codex-verdict.txt \
  "I revised the plan. Re-review PLAN.md — check prior findings + flag anything new. End with VERDICT: APPROVED or VERDICT: REVISE." \
  2>/dev/null >/dev/null
```

### Each round
1. Read verdict file; append `## Round <n> — Codex` + critique to `LOG_FILE`.
2. Last line verdict: `APPROVED` → Resolution (converged); `REVISE` → Claude decides what's worth acting on (final arbiter), revise `PLAN_FILE`, append `### Claude's response` (what changed/rejected + why), increment.
3. round > `MAX_ROUNDS` → Resolution (deadlock).

### Resolution (you sign off)
- **APPROVED:** present final plan + 3-bullet summary of what the two acts improved + round count. Ask before implementing. No code during either act.
- **Deadlock (cap hit, no APPROVED):** list unresolved points + Claude's counter-position; hand to user. Don't fake convergence.

---

## Hard rules
- Act 1 precedes Act 2. `CONTEXT.md` stays a glossary only — no implementation details.
- Codex read-only EVERY round (`-s read-only` first, `-c sandbox_mode="read-only"` on resume — resume has no `-s`). Never writes.
- Loop ALWAYS terminates at `MAX_ROUNDS`. Claude is final arbiter on REVISE (reject with logged reason). Code only after sign-off. `LOG_FILE` is the deliverable.

## What NOT to do
- Don't review already-written code (`/codex:review`). Don't pin `-codex` variants on ChatGPT auth. Don't let Codex edit files. Don't skip Act 1.
