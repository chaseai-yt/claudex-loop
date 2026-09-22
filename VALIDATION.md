# Validation — bidirectional loop

Development date: 2026-09-06. Tests run in disposable fixtures; production repositories were not built or modified by live smoke tests.

## Automated checks

- `python scripts/validate.py`: active skill frontmatter, local references, both provider manifests and shared-runner presence.
- `python -m unittest discover -s tests -v`: **24 passing tests** with fake CLI executables and real temporary Git repositories, without model calls. The 24th pins the two inspector pairings `claudex-code-review` uses from each host.
- Codex Skill Creator validator: all three active skills at the time of that run.
- Codex Plugin Creator validator: `.codex-plugin/plugin.json`.
- `git diff --check`.

The contract suite covers both host directions, explicit model selection, read-only reviewer argument construction, success/failure parsing, malformed/empty/incomplete output, a failed turn following successful output, session identity on resume, timeout handling, plan hash invalidation, staged/untracked/deleted change coverage, inspection invalidation and preservation of unrelated work during build resumption.

GitHub Actions is configured for Windows, macOS and Linux. Local results establish Windows behavior; cross-platform CI results must be checked on the PR before merge.

## Live model checks

| Check | Result |
|---|---|
| Fable 5.1 reviews a deliberately broken backup plan through the Codex-host route | REVISE; identified deletion-before-read data loss; valid structured output, coverage and session UUID |
| Same Fable session reviews the revised plan | APPROVED with low-priority advice; exact UUID preserved and new plan hash recorded |
| Astra through npm Codex CLI 0.144.5 | Correctly failed, preserving the server error that this model needs a newer CLI |
| Astra through app-bundled Codex CLI 0.153.4 | REVISE; independently identified the seeded data-loss defect; valid structured output |
| Same Astra session reviews the revised plan | APPROVED with zero findings; exact UUID preserved and new plan hash recorded |
| Approval checks on both revised plans | Passed against the actual plan path and current content |
| Fable and Astra separately implement a tiny addition work order | Each created only addition.py; existing acceptance-check file unchanged |
| Host independently runs `python -B check.py` on both implementations | All three acceptance checks passed for each implementation |
| Fresh Astra inspects Fable's code | APPROVED; new untracked addition.py included in the inspected snapshot |
| Fresh Fable inspects Astra's code | APPROVED; new untracked addition.py included in the inspected snapshot |
| Astra (Codex CLI 0.155.1) inspects the `claudex-code-review` branch through the skill's Codex pairing, 2026-09-22 | REVISE with two findings on the skill text, both accepted; valid structured output and fresh session UUID |
| Fable 5.1 (Claude Code 2.1.278) inspects the same snapshot through the skill's `--builder codex` pairing, 2026-09-22 | REVISE; identical snapshot hash to the Astra run, observed model `claude-fable-5-1`. The run smoke-tested the pairing with the same provider as the author; it is not an independent review of the author's code |
| Claude Code host runs `claudex-code-review` end to end on PR #17 with Astra (Codex CLI 0.156.0) and Fable 5.1 (Claude Code 2.1.280), 2026-09-22 | Both reviews and both cross-examinations completed on one snapshot hash; two findings confirmed across providers, one of them reproduced by a host probe. A missing rule for a host-verified finding that the other provider refutes was found and added |

Fable runs used Claude Code 2.1.261. The Astra test used an explicit CLI executable path rather than changing the user's global installation. The model selection was explicit in both adapters.

Both delegated builders reported that their proof commands were blocked locally: Claude needed approval in headless mode; Codex's Windows sandbox could not access the Python executable. Neither denial was bypassed. The coordinating host ran the proof independently and observed passing results. A completed build turn is not a verified build; the mandatory host proof step resolved these gaps before final inspection.

The fixture checks exercise transport and obvious-defect detection, not comparative model quality. No claim is made that one pairing is better or that an APPROVED response proves exhaustive correctness. A future benchmark should compare defect recall, false positives, proof results, time and usage on the same tasks, including sound plans.

## Limits

- Live review tests were run on Windows. Automated fake-CLI coverage is configured for all three operating systems.
- CLI versions, account access and permission behavior can change; diagnostics identify the selected executable and requested model.
- Codex's shell sandbox does not constrain external MCP side effects; review existing tool configuration as described in the runtime reference. Claude's adapter instead removes non-reading tools and MCP from the reviewer.
- Structured-output validation can reject broken transport and inconsistent verdicts, but cannot prove a model's findings or claimed coverage.
