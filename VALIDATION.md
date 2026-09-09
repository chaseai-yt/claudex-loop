# Validation — bidirectional loop

Development date: 2026-09-06. Tests run in disposable fixtures; production repositories were not built or modified by live smoke tests.

## Automated checks

- `python scripts/validate.py`: active skill frontmatter, local references, both provider manifests and shared-runner presence.
- `python -m unittest discover -s tests -v`: **23 passing tests** with fake CLI executables and real temporary Git repositories, without model calls.
- Codex Skill Creator validator: all three active skills.
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

Fable runs used Claude Code 2.1.261. The Astra test used an explicit CLI executable path rather than changing the user's global installation. The model selection was explicit in both adapters.

Both delegated builders reported that their proof commands were blocked locally: Claude needed approval in headless mode; Codex's Windows sandbox could not access the Python executable. Neither denial was bypassed. The coordinating host ran the proof independently and observed passing results. A completed build turn is not a verified build; the mandatory host proof step resolved these gaps before final inspection.

The fixture checks exercise transport and obvious-defect detection, not comparative model quality. No claim is made that one pairing is better or that an APPROVED response proves exhaustive correctness. A future benchmark should compare defect recall, false positives, proof results, time and usage on the same tasks, including sound plans.

## Live model checks — orca executor (`--via orca`)

Development date: 2026-09-08, macOS, real Orca app running.

| Check | Result |
|---|---|
| `--via orca --orca-worktree path:<unregistered-repo>` | Failed cleanly: `selector_not_found`, `failure_kind: "defect"` — no silent fallback to subprocess |
| `--via orca --orca-worktree active` against an unrelated disposable repo, real Astra (Codex CLI 0.153.4) review | `status: completed`, real session UUID, `APPROVED` verdict, real usage tokens |
| `exit_code.txt` under the run's artifact directory | `0`, matching the CLI's actual exit — confirms the file-captured-exit-code path (not Orca's own unreliable `wait` exitCode field) end to end |
| `command.json` for that run | The real `codex exec ...` argv, run wrapped in `cd <repo> && ... < prompt.txt > stdout.txt 2> stderr.txt` inside a real Orca terminal targeting a different worktree than the reviewed repo |

This is the first end-to-end run of `--via orca` against a live Orca app and real Codex CLI (all prior coverage was the fake-CLI/fake-`orca` unit suite, 33 tests). It specifically exercises the `--orca-worktree` vs `--repo` binding fixed after independent Codex review (finding 1) and the exit-code-capture workaround (findings 6/independent verification).

## Limits

- Live review tests were run on Windows (bidirectional loop) and macOS (`--via orca` only). Automated fake-CLI coverage is configured for all three operating systems.
- The orca executor has one live smoke run on `review` mode only; `build`/`inspect` via orca and the Windows-rejection path are unit-tested but not yet live-checked.
- CLI versions, account access and permission behavior can change; diagnostics identify the selected executable and requested model.
- Codex's shell sandbox does not constrain external MCP side effects; review existing tool configuration as described in the runtime reference. Claude's adapter instead removes non-reading tools and MCP from the reviewer.
- Structured-output validation can reject broken transport and inconsistent verdicts, but cannot prove a model's findings or claimed coverage.
