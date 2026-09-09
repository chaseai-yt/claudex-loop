# Build and final inspection

Resolve the shared runner from the installed claudex-loop skill. Keep the spec and run artifacts available to both providers. Preserve user-selected toolchains and carry exact acceptance criteria and proof commands into the build.

## Preparation

Capture the pre-build commit before changing code. For a new project, establish an initial Git baseline with the user's authorization; never claim a full Git diff without one. A host build may preserve and account for existing changes, but a delegated build requires an isolated clean checkout. Use a worktree for unrelated or live changes instead of stashing another session's work. Do not create commits merely to satisfy the gate without authorization.

The runner requires a clean checkout for delegated builds. Plan/log files written by the loop can themselves make it dirty: keep those artifacts outside the build checkout, or include them in an authorized baseline. When moving to a worktree, re-review the copied plan in that worktree before using its approval there; approval is bound to the repository path. Resolve source paths against the build worktree so the builder cannot accidentally target the original checkout. A worktree is isolation for diffs, not an operating-system sandbox.

`--via orca` (see [runtime](runtime.md)) makes a delegated build visible in Orca's terminal list, but it does not provision isolation by itself: it still runs against `--repo` and still requires that same clean-checkout gate. If you want Orca to provision the isolated worktree too, create it yourself first (`orca worktree create`) and pass that path as both `--repo` and `--orca-worktree`.

## Delegated build

```text
python RUNNER build --host claude --builder codex --repo PROJECT --plan PLAN_PATH --approval APPROVED_RESULT --proof "python -m unittest discover"
python RUNNER build --host codex --builder claude --repo PROJECT --plan PLAN_PATH --approval APPROVED_RESULT --proof "npm test"
```

Codex builds with `workspace-write` and noninteractive approvals, not a global sandbox bypass. Claude builds with its normal configured permissions and `acceptEdits`; commands still requiring approval are denied in headless mode. Confirm any required proof-command permissions using the provider's supported configuration before launch. Never respond to a denial by silently turning on permission bypass. If the external builder cannot perform required work, report it or have the already-authorized host perform that portion and log authorship.

For an explicitly requested standalone work order without plan review, replace `--approval` with `--unreviewed-spec`; record the missing review. This does not waive independent inspection of the final code.

Use `--resume PREVIOUS_BUILD_RESULT --feedback FIX_LIST` for fixes. The clean-checkout gate only applies to the first build; resumed fixes must remain against the same recorded baseline, and the host must ensure intervening changes belong to this build. The build result's `base` identifies the initial commit. Do not trust a builder's success report or proof output as independent verification.

## Verify and inspect

Read all changes relative to the pre-build commit, including staged changes, deletions, binary assets and untracked files. For changed tests, check that assertions express the acceptance criteria or valid regressions rather than merely confirming whatever the implementation happens to do. Existing necessary regression tests need not correspond to a new spec sentence. Run the agreed proof commands yourself, and add relevant manual/visual verification when the deliverable calls for it.

```text
python RUNNER inspect --host claude --builder codex --repo PROJECT --plan PLAN_PATH --base BASE_COMMIT
python RUNNER inspect --host codex --builder claude --repo PROJECT --plan PLAN_PATH --base BASE_COMMIT
```

The runner supplies the tracked diff plus a manifest of all changed and untracked files. The reviewer must open added files. It fingerprints the inspected state and refuses approval if code changes during inspection. Ignored files are not enumerated by Git: inspect any ignored build deliverables separately. Changed submodules require an explicit inspection path rather than a silently incomplete diff.

Log findings, coverage, limitations and host dispositions. Fix accepted findings, rerun affected proof checks, then inspect again with a fresh other-provider session. An inspection applies to the recorded snapshot only. Later edits invalidate it. If the host takes over, choose the inspector opposite the new builder. For mixed authorship, log the split and have each provider review the other's changes; disclose remaining gaps if the round budget is exhausted.

Stop at the configured fix and inspection budgets. Report unresolved findings rather than claiming approval. An explicit `inspect=off` remains a logged opt-out. The final human-facing result includes proof, inspected snapshot, deviations, residual findings and any unreviewed edits. Commits, pushes and releases follow the user's existing authorization.
