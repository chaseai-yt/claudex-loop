# grill-me-codex

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

**Two AI models harden your plan — then swap jobs to build it.** A family of Claude Code skills that close the two gaps in AI-assisted coding: the gap between *you and Claude* (do we agree on what to build?) and the gap between *Claude and the quality of what it produces* (is the plan actually correct — and how would you even know?).

Act 1 grills **you** to lock the plan. Act 2 hands that plan to **OpenAI Codex** — a rival, cross-provider model — which adversarially tears it apart over several rounds until both models sign off. Act 3 (optional) flips the roles: **Codex writes the code** from the frozen plan while **Claude reviews the diff** like a contributor PR. Cross-model checks in both directions — nobody grades their own work.

> Why a second model? Because the same model that plans the build and writes the build can't be trusted to *grade its own work* — it's an echo chamber. A different provider catches what Claude structurally can't see in itself.

Built on [Matt Pocock's `grill-me` / `grill-with-docs`](https://github.com/mattpocock/skills) skills (MIT) — Act 1 is his work; the iterative cross-model Codex review (Act 2) and the role-flipped build (Act 3) are the additions. Act 3's delegation pattern is adapted from [Peter Steinberger's `codex-first`](https://github.com/steipete/agent-scripts).

## The skills

| Skill | Act 1 | Act 2 | Act 3 | Use when |
|-------|-------|-------|-------|----------|
| **`grill-me-codex`** | Claude interrogates you one question at a time until the decision tree is resolved | Codex adversarial review loop | optional → `codex-build` | Planning from scratch + want alignment AND a second-model check |
| **`grill-with-docs-codex`** | Same, but challenges your plan against your project's `CONTEXT.md` glossary + writes ADRs inline | Codex review loop | optional → `codex-build` | Same, in a project with established terminology / architecture decisions |
| **`codex-review`** | — (you already have a plan) | Codex review loop | optional → `codex-build` | You have a plan and just want the cross-model stress-test |
| **`codex-build`** | — | — | Codex implements the frozen plan; Claude verifies | You have a reviewed spec and want the second model to type it |

## How Act 2 works (the review)

1. Claude writes the locked plan to `PLAN.md` and starts a log at `PLAN-REVIEW-LOG.md`.
2. **Round 1:** Codex reviews the plan in a **read-only sandbox** and returns `VERDICT: APPROVED` or `VERDICT: REVISE`.
3. **Rounds 2..N:** Claude revises; the *same* Codex session is resumed so it remembers its prior critiques and only checks whether they're addressed.
4. Bounded by `MAX_ROUNDS` (default 5). Terminates on `APPROVED` or the cap (a flagged deadlock beats a fake "approved").
5. **You gate twice only:** kickoff, and final sign-off before any code. Codex is read-only every round and never writes a file.

Two artifacts: `PLAN.md` (the clean final plan — the *what*) and `PLAN-REVIEW-LOG.md` (the full round-by-round argument — the *why*).

## How Act 3 works (the build — roles flip)

1. After you sign off the plan, `codex-build` hands `PLAN.md` to Codex as a frozen spec — Codex gets **full write access** (`--yolo`) and implements it end-to-end, running tests as it goes. Requires a **clean git tree** first, so its diff is isolatable and revertible.
2. Claude — now the critic — reads the **full diff** like a contributor PR and runs the proof test itself. Codex's claims are advisory; Claude's own run is the proof.
3. Problems go back to the *same* Codex session as precise fix rounds, capped at `MAX_FIX_ROUNDS` (default 2) — after that Claude takes over and finishes by hand rather than ping-ponging.
4. **You gate once more:** the diff sign-off. Claude writes the commit; Codex never commits.
5. Build rounds append to the same `PLAN-REVIEW-LOG.md`, so one artifact tells the whole story: grilled → reviewed → built → verified.

Bonus: Codex sessions have a **native image-generation tool** (ChatGPT-account backed, no API key). A spec can include "generate these image assets yourself" steps — sprites, textures, backdrops — with exact paths and dimensions in the build contract.

## Install

Copy the skill folders into your Claude Code skills directory:

```bash
# macOS / Linux
cp -r skills/* ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\* $env:USERPROFILE\.claude\skills\
```

Then invoke in Claude Code: `/grill-me-codex`, `/grill-with-docs-codex`, `/codex-review`, or `/codex-build`.

## Prerequisites

- **Codex CLI ≥ 0.130** — `npm install -g @openai/codex@latest` (older versions error on the default `gpt-5.5` model).
- **Authenticated Codex** — run `codex login` once (a ChatGPT account works; Free/Plus/Pro/Max all fine).
- **Don't pin a model** — ChatGPT-account auth rejects `gpt-5.x-codex` model variants; the skills use your config default.

## Tunables

| Skill | Var | Default | Meaning |
|-------|-----|---------|---------|
| review skills | `MAX_ROUNDS` | `5` | Hard cap on review rounds |
| review skills | `PLAN_FILE` | `PLAN.md` | Where the plan lives |
| all | `LOG_FILE` | `PLAN-REVIEW-LOG.md` | The argument transcript |
| `codex-build` | `SPEC_FILE` | `PLAN.md` | The frozen spec Codex implements |
| `codex-build` | `MAX_FIX_ROUNDS` | `2` | Fix rounds before Claude takes over |
| `codex-build` | `PROOF_CMD` | from spec | Exact test command that counts as proof |

Pass e.g. `rounds=3` when invoking to override.

## Safety

**Review skills (Acts 1–2):** Codex runs **read-only every round** — `-s read-only` on the first call, `-c sandbox_mode="read-only"` on every resume (the `resume` subcommand doesn't accept `-s`, and without forcing read-only it would inherit your `config.toml` sandbox default, which may be `danger-full-access`). The skills handle this for you. No code is ever written until you approve the final plan.

**`codex-build` (Act 3)** deliberately inverts this: Codex gets full write access — which is exactly why the skill gates it hard. Clean git tree required before launch (diff isolation + clean revert), Claude reads every line of the diff and runs the proof itself, fix rounds are bounded, commits are human-gated and Claude-authored. Resume calls need the long flag `--dangerously-bypass-approvals-and-sandbox` (resume has no `--yolo`) — and always resume by explicit `thread_id`, never `--last`: a wrong or missing id can silently land in a different session.

## Credits

- Act 1 (`grill-me`, `grill-with-docs`) © Matt Pocock — https://github.com/mattpocock/skills (MIT). See each skill's `THIRD-PARTY-NOTICES.md`.
- Act 3's Codex-as-builder pattern adapted from Peter Steinberger's [`codex-first`](https://github.com/steipete/agent-scripts).
- Act 2 (iterative Codex review), Act 3 (codex-build), and packaging by [Chase AI](https://youtube.com/@chaseai).
- Want to go deeper? The **Claude Code Masterclass** and a community of builders shipping with agentic AI live inside [Chase AI+](https://www.skool.com/chase-ai/about).

## License

MIT — see [LICENSE](./LICENSE).
