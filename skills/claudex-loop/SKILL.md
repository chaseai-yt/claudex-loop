---
name: claudex-loop
description: "Harden a plan with independent Claude/Codex review, then optionally build and cross-inspect it. Start in either Claude Code or Codex: the host plans and the other provider reviews. Use for claudex this plan, claudex-loop, or the legacy crucible trigger; not for trivial edits."
---

# Claudex Loop

The current conversation owns requirements, planning and coordination. The other provider reviews the plan. Either provider can build; the provider that did not build inspects the final code in a fresh session.

## Resolve roles once

Identify the actual host from your runtime, not PATH, installed skills, model-name guesses, or the repository. Both CLIs may be installed. Use `host=claude` in Claude Code and `host=codex` in Codex. If the runtime identity is unavailable, ask which host the user is using once.

| Host | Requirements and plan | Plan reviewer | Default builder | Final inspector |
|---|---|---|---|---|
| Claude Code | Current Claude session | Codex | Claude | Fresh Codex session |
| Codex | Current Codex session | Claude | Codex | Fresh Claude session |

Honor `builder=claude|codex`. The inspector is always the other provider. The host remains coordinator even when the other provider builds. To swap the planner, start the conversation in the other host; do not pretend a CLI reviewer is the user's planning conversation.

Model selection is independent of provider roles. Preserve the host's selected model. Review/build CLI calls inherit their own configuration unless `reviewer_model`, `builder_model`, or `inspector_model` is supplied; map these to the runner's `--model` for that invocation. Apply an explicit `*_effort` similarly. Fable 5.1 and GPT-6 Astra are suitable explicit choices, not mandatory pins. A model in the host UI does not prove which model a separate CLI will use. Report requested and observed model information separately; report an unresolved CLI default honestly. Never silently fall back to another model/provider on a failure.

Read [the runtime reference](references/runtime.md) before launching a CLI. Resolve its runner relative to this installed SKILL.md, never relative to the project being reviewed. Use absolute paths when launching it.

If the user supplies `codex_cli` or `claude_cli`, map the selected provider's executable path to `--cli`. A host app can have a newer working binary than the CLI on PATH; verify the path and version without silently changing global installation or configuration.

## Tunables

| Argument | Default | Meaning |
|---|---|---|
| `PLAN_FILE` / `plan` | `PLAN.md` | Plan path used throughout, including the build handoff |
| `LOG_FILE` / `log` | `PLAN-REVIEW-LOG.md` | Append-only transcript |
| `rounds` / `MAX_ROUNDS` | `5` | Maximum completed plan-review rounds |
| `builder` | host | Provider implementing the plan |
| `research` | proportionate to task | `none`, `web`, or explicit opt-in `deep` |
| `mode` | `full` | `full` includes recon/interview; `review` starts from an existing plan |
| `inspect` | `on` | `off` only when the user explicitly opts out; record it |
| `MAX_FIX_ROUNDS` | `2` | Bounded build-fix attempts before reporting or taking over |
| `MAX_INSPECTION_ROUNDS` | `2` | Initial inspection plus one after fixes |

Echo roles, paths, round limits, requested models and inspection opt-out before starting. Preserve existing authorization: a request to plan does not authorize building; a request to plan and implement does. Do authorized preparation before seeking any remaining sign-off.

## Phase 0 — Recon

For existing projects, inspect relevant code, dependencies, callers and writers of shared state. Read existing `CONTEXT.md` / `CONTEXT-MAP.md` and relevant ADRs. For greenfield work, research prior art, a reasonable stack and concrete failure modes when useful. Respect an explicit research depth. Deep multi-agent research requires explicit opt-in and an available tool; otherwise use supported targeted research, and report the limitation. Do not require a proprietary Workflow tool or hard-code a research-agent model.

Discover relevant skills through the host's available catalog and the other provider's documented skill locations when accessible. Record only relevant proposed dependencies. Do not assume host MCP, browser, credentials or skills transfer to the other CLI. Verify required build capabilities before relying on them.

Present one assumptions ledger with source paths or research links. Ask for corrections to material uncertainties as a batch. Resolve routine reversible choices yourself when the user has authorized the work; silence is not approval of an action requiring approval.

## Phase 1 — Settle requirements

Maintain a short visible decision map. Ask only about unresolved decisions that change the outcome. For each consequential question, give the recommendation, why it matters, and the cost of guessing wrong. Batch independent questions; ask dependent ones sequentially. If the code can answer, inspect it instead. Offer “accept all remaining recommendations” when a long decision list would slow the user down.

Respect existing glossary definitions; resolve ambiguous domain language. Maintain glossary-only context lazily using [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md). Record an ADR only for expensive-to-reverse, non-obvious trade-offs using [ADR-FORMAT.md](ADR-FORMAT.md).

Write the resolved `PLAN_FILE` with:
- Goal and observable acceptance criteria.
- Concrete approach, key decisions, trade-offs and non-goals.
- Confirmed assumptions with sources and remaining risks.
- Relevant toolchain requirements per provider, if any.
- Verification: exact proof command(s), expected results, and manual/visual checks when needed.

Derive proof commands from the repository when possible. Ask only when what counts as success remains unclear. Start the append-only `LOG_FILE` with roles, model requests, scope, authorization and round limits. Keep run diagnostics outside the checkout.

With `mode=review`, load the supplied plan, fill only material gaps with the user as needed, and proceed directly to review; do not restart a requirements interview.

## Phase 2 — Independent plan review

Use the shared runner in `review` mode with the actual `--host`, resolved `--plan` and optional model/effort. First round creates a session. Further rounds use `--resume <previous-successful-result.json>` with the same provider/model/effort and a host-authored `--feedback` file containing dispositions. Never use a guessed session id, `--last`, or a build session as a reviewer.

Each successful response contains a verdict, evidence-backed findings, actual coverage and limitations. Preserve the entire response and runner result path in `LOG_FILE`.

- **APPROVED:** no unresolved material defects. Approval is bound to the exact plan path and SHA256. Present remaining low-priority advice and limitations; zero findings is valid and is not proof of exhaustive correctness.
- **REVISE:** the host arbitrates each finding. Implement warranted plan changes; reject unsupported suggestions with reasons. Record dispositions and send the revised plan to the same reviewer. Avoid relitigating resolved points without new evidence.
- **BLOCKED / failed process / malformed result:** never count this as approval. Explain the actual missing evidence or operational failure. Do not burn remaining rounds on blind retries or switch providers silently.

### When the reviewer's provider runs dry

A usage limit is not a verdict, and it must not end the loop. Full protocol: [FALLBACK.md](../../FALLBACK.md).

- **Before round 1 and on any failed round**, read the remaining quota with `python scripts/codex_usage.py` — the 5-hour and weekly windows and their reset times, taken from Codex's local session rollouts, no API call. It answers the question the runner's diagnostics cannot: *when does it come back.*
- **Confirmed exhaustion halts the loop and hands the decision to the user** — never automatically, never silently: **wait** for the reset (resume the same result with `--resume`, session memory survives), **switch** to a configured substitute reviewer, or **skip** the review and take the plan to sign-off explicitly marked not cross-reviewed. Same doctrine as `inspect=off`: skipping yes, silent skipping never.
- **The substitute reviewer is weaker, and the log says so.** `python scripts/fallback_review.py --plan PLAN --log LOG --round N --append-log LOG` talks to any OpenAI-compatible endpoint (LM Studio and Ollama locally, OpenRouter, OpenAI, Gemini, Anthropic) configured through git-ignored `.env` profiles; `--chain` walks the configured order to the first viable provider and reports every skip. It receives **plan and log text only and has no repository access at all** — read-only by construction rather than by sandbox, which is also its limitation: it cannot check a claim against the code. Rounds are logged as `## Round <n> — <model> (via <reviewer>, fallback — plan-text only, no repo access)` and the verdict is bound to the plan's SHA256.

With no `.env` profiles configured nothing changes: the configured reviewer stays the only reviewer.

Stop at `MAX_ROUNDS`. Present unresolved findings and the host's position instead of manufacturing convergence. A changed plan requires another review. Before building, run the approval check on the final plan. If the user explicitly chooses to proceed without independent approval, record that override and use the standalone unreviewed-spec path; never label it approved.

## Phase 3 — Build and inspect

Present the reviewed plan, improvements and remaining limits. If implementation is not already authorized, ask for that final decision. Use the selected builder, defaulting to the host. Read [the build reference](references/build.md).

The host can implement directly with its normal tools. For a different builder, use the shared runner's `build` mode. Either path must capture the pre-build commit, preserve unrelated user work, and carry the same resolved plan and verification contract.

Run the agreed proof checks, inspect all changed files and review the result through the other provider in a **fresh** `inspect` session. Supply the pre-build commit and builder identity. Reinspection after accepted fixes also uses a fresh session. Log findings and dispositions; rerun affected proof checks after fixes.

If the coordinator takes over coding, it has become a builder. Require a fresh other-provider inspection of its changes; never describe the earlier inspection as covering later edits. If both providers contributed code, record authorship and have each inspect the other's changes; do not claim any model independently reviewed code it authored. If the inspection budget is exhausted, report remaining findings and unreviewed edits explicitly for the user's decision.

Present the final diff, proof results, inspection coverage, unresolved findings, deviations and rounds used. Honor existing commit/push authorization; otherwise leave the concrete diff ready for sign-off. External publication is never implied merely by running the loop.
