<div align="center">

<img src="assets/logo.svg" alt="CLAUDEX LOOP" width="719">

### Two AI models harden your plan before a line of code exists — then swap jobs to build it.

[![Stars](https://img.shields.io/github/stars/chaseai-yt/claudex-loop?style=flat&color=e8590c)](https://github.com/chaseai-yt/claudex-loop/stargazers)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-skill%20%2B%20plugin-d97757)](https://docs.anthropic.com/en/docs/claude-code)
[![Codex](https://img.shields.io/badge/OpenAI_Codex-adversarial_reviewer-10a37f)](https://github.com/openai/codex)

*The plan that sounds finished usually isn't. In claudex-loop's first real run, a deeply-researched, interview-locked plan still contained **one unbuildable subsystem and six designs that would have corrupted data** — a rival model found all of them before any code existed.*

</div>

---

## Why

AI-assisted coding fails in two places: the gap between **you and Claude** (do we agree on what to build?) and the gap between **Claude and its own output** (is the plan actually correct — and how would you even know?). The model that wrote the plan can't be trusted to grade it. That's an echo chamber.

Claudex-loop closes both gaps: Claude locks intent *with you*, then **OpenAI Codex** — a rival, cross-provider model — attacks the locked plan round after round until it can't find anything else wrong.

```mermaid
flowchart LR
    A["🔍 RECON<br>codebase recon or<br>greenfield research"] --> L["📋 Assumptions Ledger<br>you confirm in one batch"]
    L --> B["🎯 INTERROGATE<br>load-bearing questions<br>one at a time"]
    B --> P["PLAN.md locked"]
    P --> C["⚔️ REVIEW<br>Codex attacks in a<br>read-only sandbox"]
    C -- REVISE --> R["Claude arbitrates<br>and revises"]
    R -- same session --> C
    C -- APPROVED --> S["✍️ You sign off"]
    S -. optional .-> D["🔨 BUILD<br>one model writes"]
    D --> I["🔬 CROSS-INSPECT<br>the other model<br>grades the diff"]
    I --> G["✅ You approve<br>the final diff"]
    classDef claude fill:#d97757,stroke:#7a3a24,color:#fff
    classDef codex fill:#10a37f,stroke:#0a6b54,color:#fff
    classDef human fill:#e8b93e,stroke:#8a6a14,color:#1a1a1a
    classDef artifact fill:#3d3d3d,stroke:#6b6b6b,color:#fff
    class A,B,R claude
    class C,D codex
    class I artifact
    class L,S,G human
    class P artifact
```

**You enter at four points only:** confirming the ledger, answering the interview, signing off the converged plan, and approving the final diff if you build. Codex is read-only throughout review and never touches a file.

## The four phases

| | What happens | What makes it different |
|---|---|---|
| **🔍 0 — RECON** | Claude scouts *before* asking you anything — explores the codebase and living docs, or on greenfield researches prior art, stacks, and known pitfalls (research depth is a gate **you** control, up to a multi-agent deep-research workflow) | Opens with an **Assumptions Ledger**: everything already resolved, batch-confirmed in one reply. The interview never wastes questions the code or research already answered |
| **🎯 1 — INTERROGATE** | A visible **decision map** splits open decisions into load-bearing (asked one at a time) and cosmetic (batched, veto-by-exception) | Every question must justify its existence: *why it matters*, a committed *recommendation*, and *what breaks if we guess wrong*. Escape hatch: "accept all remaining recommendations" |
| **⚔️ 2 — REVIEW** | Codex reviews `PLAN.md` in a read-only sandbox → `VERDICT: APPROVED` or `REVISE` with concrete flaws. Claude arbitrates (rejects bad critiques *with logged reasons*), revises, and resumes the **same Codex session** | The reviewer remembers its prior findings and attacks its own accepted fixes. Bounded by `MAX_ROUNDS` — a flagged deadlock beats a fake "approved" |
| **🔨 3 — BUILD** *(optional)* | You pick the builder. **Codex builds** (`codex-build`, full write access) → Claude reads the entire diff like a contributor PR and runs the proof test itself. **Claude builds** → a *fresh* read-only Codex session cross-inspects the finished diff against the plan — on by default, findings arbitrated and logged | The final code is always graded by the rival model, whichever one wrote it. Skipping the inspection requires an explicit, logged opt-out |

**The invariant across all four:** *whoever made the thing never checks the thing.* Plan by Claude → attacked by Codex. Code by Codex → reviewed by Claude. Code by Claude → inspected by Codex. No one grades their own work, in any path.

Two artifacts every run: `PLAN.md` (the *what*) and `PLAN-REVIEW-LOG.md` (the full round-by-round argument — the *why*).

## Receipts

From the first end-to-end greenfield run (a solo-creator CRM):

<p align="center"><img src="assets/run.svg" alt="A claudex-loop run: recon, interrogate, then five Codex review rounds converging 26 findings to 0 and VERDICT: APPROVED" width="547"></p>

- **55 findings across 5 rounds** — converging 26 → 15 → 12 → 2 → 0
- **1 fatal:** an access-path architecture that could not be built as written (read as completely plausible)
- **~6 wrong models** that would have shipped and corrupted data weeks later
- **~7 missing subsystems**, including the homepage feature that had no backing data source
- **What survived untouched:** every product decision from the interview. The review only ever attacked *how it would break* — the phases genuinely divide the labor

## Install

### Option A — Plugin *(recommended: updates flow automatically)*

```
/plugin marketplace add chaseai-yt/claudex-loop
/plugin install claudex-loop@claudex-loop
```

Skills arrive namespaced: `/claudex-loop:claudex-loop`, `/claudex-loop:codex-review`, `/claudex-loop:codex-build`. (Intent triggering works regardless — say "claudex this plan" or even the legacy "crucible this plan" and the right skill fires.) Enable auto-update for the marketplace in the `/plugin` menu and new versions pull in on their own.

> **Installed back when this was `crucible`?** Your existing marketplace source keeps working (GitHub redirects), but the plugin name changed — re-add with the commands above to pick up the new namespace.

### Option B — Manual copy *(bare skill names)*

```bash
# macOS / Linux
cp -r skills/* ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\* $env:USERPROFILE\.claude\skills\
```

Invoke as `/claudex-loop`, `/codex-review`, `/codex-build`. Update by `git pull` + re-copy.

> **Coming from grill-me-codex or crucible?** This repo *was* both — GitHub redirects the old URLs, so `git pull` in your existing clone just works. The old grill skills live on in [`legacy/`](./legacy/) (copy them only if you want them; `/claudex-loop` doesn't need them).

## Prerequisites

- **Codex CLI ≥ 0.130** — `npm install -g @openai/codex@latest`
- **Authenticated** — `codex login` once (any ChatGPT account: Free/Plus/Pro/Max)
- **Don't pin a model** — ChatGPT-account auth rejects `gpt-5.x-codex` variants; the skills use your config default and echo the active model at kickoff so you can veto before a round burns

## Tunables

| Skill | Var | Default | Meaning |
|-------|-----|---------|---------|
| `claudex-loop` | `research` | ask | `none` / `web` / `deep` — pre-answers the Phase 0 research gate |
| review skills | `MAX_ROUNDS` | `5` | Hard cap on review rounds |
| review skills | `PLAN_FILE` | `PLAN.md` | Where the plan lives |
| all | `LOG_FILE` | `PLAN-REVIEW-LOG.md` | The argument transcript |
| `codex-build` | `SPEC_FILE` | `PLAN.md` | The frozen spec Codex implements |
| `codex-build` | `MAX_FIX_ROUNDS` | `2` | Fix rounds before Claude takes over |
| `codex-build` | `PROOF_CMD` | from spec | Exact test command that counts as proof |

Pass e.g. `rounds=3` when invoking to override.

## When Codex runs dry (fallback reviewers)

The loop must not dead-end when Codex hits its usage limit mid-review ([#7](https://github.com/chaseai-yt/claudex-loop/issues/7)). Two scripts and a protocol handle it — full write-up in [FALLBACK.md](./FALLBACK.md):

- `scripts/codex_usage.py` — remaining 5-hour/weekly quota + reset times, read from Codex's local session rollouts (no API call). Checked before round 1 and consulted on any mid-loop failure.
- `scripts/fallback_review.py` — an optional substitute reviewer over any OpenAI-compatible endpoint (LM Studio and Ollama locally, OpenRouter, OpenAI, Gemini, Anthropic), configured via git-ignored `.env` profiles ([.env.example](./.env.example)); `--check` preflights every provider (reachability, auth, remaining OpenRouter credits) and `--chain` walks the configured order to the first viable one, reporting every skip. Env contract: `CLAUDEX_REVIEWERS=<name,name,…>` (chain order) plus per profile `CLAUDEX_REVIEWER_<NAME>_BASE_URL`, `_MODEL`, and `_API_KEY_ENV` (name of the variable holding the key; optional `_API_KEY` inline works but warns, optional `_TEMPERATURE`/`_MAX_TOKENS`/`_TIMEOUT`). It sees only the plan text — read-only by construction, no vendor sandbox to audit — rejects rubber-stamp approvals (round-1 APPROVED with fewer than 3 findings is invalid), and binds every verdict to the plan's SHA256.
- The rules: a switch is **never automatic and never silent** — on confirmed exhaustion the loop halts and the user picks *wait* (resume the same thread after reset), *switch* (fallback rounds are labeled in the log; the approval is weaker and the log says so), or *skip* (plan goes to sign-off marked not cross-reviewed).

With no `.env` profiles configured, nothing changes — Codex stays the only reviewer and the loop behaves exactly as before.

## Safety

**Review (Phases 0–2):** Codex runs **read-only every round** — `-s read-only` on the first call, `-c sandbox_mode="read-only"` on every resume (the `resume` subcommand doesn't accept `-s`, and without forcing read-only it would inherit your `config.toml` sandbox default, which may be `danger-full-access`). The skills handle this for you. No code is written until you approve the final plan.

**`codex-build` (Phase 3)** deliberately inverts this: Codex gets full write access — which is exactly why the skill gates it hard. Clean git tree before launch, Claude reads every line of the diff and runs the proof itself, fix rounds bounded, commits human-gated and Claude-authored. Resume calls need the long flag `--dangerously-bypass-approvals-and-sandbox` (resume has no `--yolo`) — and always resume by explicit `thread_id`, never `--last`.

## Credits

- The [`legacy/`](./legacy/) skills' Act 1 (`grill-me`, `grill-with-docs`) © [Matt Pocock](https://github.com/mattpocock/skills) (MIT) — see their `THIRD-PARTY-NOTICES.md`. Claudex-loop's interview is an original redesign.
- Phase 3's Codex-as-builder pattern adapted from Peter Steinberger's [`codex-first`](https://github.com/steipete/agent-scripts).
- Claudex-loop, the iterative cross-model review, and packaging by [Chase AI](https://youtube.com/@chaseai).

<div align="center">

**Want to go deeper?** The **Claude Code Masterclass** and a community of builders shipping with agentic AI live inside [**Chase AI+**](https://www.skool.com/chase-ai/about)

*MIT — see [LICENSE](./LICENSE)*

</div>
