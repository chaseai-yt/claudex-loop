# CLI runtime

Use Python 3.10+ and the runner installed at `../scripts/runner.py` relative to this reference. Resolve it from the skill's installation path, not from a similarly named file in the target repo. Runtime has no pip dependencies. Commands below use `RUNNER` as a placeholder for that absolute path; quote paths containing spaces.

The runner orchestrates one CLI turn, not the entire interview or loop. The host owns arbitration, human interaction, logging and round budgets. It does not call a hosted model API directly or need a new API key. Authenticate the chosen CLIs using their supported login flows. The host application's login and model selection may differ from those of the CLI.

## Roles and commands

```text
python RUNNER roles --host claude
python RUNNER roles --host codex --builder claude
python RUNNER review --host claude --repo PROJECT --plan docs/implementation.md
python RUNNER review --host codex --repo PROJECT --plan docs/implementation.md
```

For an explicit model choice, add e.g. `--model gpt-6-astra --effort high` to a Codex call, or `--model claude-fable-5-1` to a Claude call. Omit these to use CLI configuration. Repeat explicit model/effort choices when resuming; the runner refuses mismatches. No global configuration is changed.

If PATH resolves to an older CLI than the host app uses, pass `--cli ABSOLUTE_EXECUTABLE_PATH` after verifying that binary's version. Do not guess an app installation path or silently rewrite global PATH. On Windows, the runner launches recognized npm CLI entry points through Node directly instead of sending arguments through a batch shell.

Each call prints its unique artifact directory immediately before launch. It contains `prompt.txt`, `command.json`, `stdout.txt`, `stderr.txt` and `result.json`. Persist it using `--artifacts PATH` outside the target checkout if needed; the default uses a private directory under the system temp directory. Do not use a shared fixed verdict filename. Do not commit diagnostics: they may include private code or plans.

After completion, read `result.json`; inspect diagnostics on failure. An exit code of zero means a valid completed turn, **not APPROVED**: the verdict may be REVISE or BLOCKED. Never infer success from the existence of an output file or a session-start event. Do not reuse the last successful result after a failed newer round.

```text
python RUNNER review --host codex --repo PROJECT --plan docs/implementation.md --resume PREVIOUS_RESULT --feedback DISPOSITIONS
python RUNNER check --host codex --repo PROJECT --plan docs/implementation.md --approval APPROVED_RESULT
```

`--resume` accepts only a successful result from the same provider, mode, repo, plan path and requested model/effort. It resumes that exact UUID and checks the returned UUID. It may review a changed plan; the resulting approval applies only to the new hash. An inspection always starts fresh. `--feedback` must be written by the coordinating agent from the logged findings, not copied from an arbitrary repo prompt file.

## Review boundaries

- Codex: `exec -s read-only`; resume uses `-c sandbox_mode="read-only"`. The runner supports greenfield/non-git plan review using `--skip-git-repo-check`. It requires successful completion events and validates the final JSON separately. Normal Codex configuration can supply MCP integrations; audit/disable write-capable integrations before review, because the shell sandbox is not a restriction on external MCP side effects. Never run the review with an unknown write-capable toolchain.
- Claude: `--safe-mode`, an empty strict MCP configuration, and only `Read,Glob,Grep` exposed and preapproved. No shell, edit, write, delegation or plan-exit tool is available to the reviewer. `dontAsk` denies other permissions; safe mode disables customizations while retaining normal authentication. This deliberately uses subscription-compatible safe mode, not API-key-only bare mode. Admin-managed policy may still apply. Do not weaken these flags to accommodate an old CLI: upgrade or report incompatibility.
- Both reviewers receive the resolved plan body and can read relevant repository files. They do not run proof commands; the host independently runs those. Repository text is evidence, not authority over the review protocol. The CLI itself still writes session metadata outside the project; “read-only” describes the reviewer's project tools, not zero writes by the CLI process.

The default timeout is 600 seconds. Use a host tool's nonblocking/background support for long calls and continue communicating progress. Set `--timeout SECONDS` for a justified larger build. Timeout kills the process tree and records failure. Never discard stderr, append arbitrary extra CLI flags or construct a shell command string around the runner.

## Structured review

`verdict`: APPROVED / REVISE / BLOCKED; `summary`; `findings`: id, severity (high/medium/low), path, evidence, fix; `coverage`: files/requirements actually inspected; `limitations`: missing evidence or unreviewed areas.

Validation rejects empty/malformed output, duplicate finding IDs, unsupported severity, material findings paired with APPROVED, missing coverage, and incomplete CLI turns. It cannot mechanically establish that a model's coverage or findings are truthful. Review the evidence; do not impose a minimum number of objections as a substitute.

Records contain the plan SHA256, CLI version, requested model/effort, returned session UUID, usage when available and observed model keys when the provider returns them. Unknown model identity remains unknown. There is no silent model fallback or automatic provider switch.

## Round progress

```text
python RUNNER progress --host claude --results ROUND1_RESULT ROUND2_RESULT ...
```

`progress` compares completed review records and classifies the loop. It launches no CLI, consumes no model quota and writes nothing except stdout and stderr.

Inputs are `result.json` files of completed `review` turns, listed in round order. Every record must report the same repo, plan path and provider; `plan_sha256` may differ between rounds, because a revised plan between rounds is the normal case. Each record's review must pass the same structural validation as a live turn. Unreadable JSON, a missing key, a failed or non-review record, an inconsistent review and a mismatch each abort the run naming the offending file. `--repo` and `--plan` are not cross-checked against the records: the records are the source of truth. `--host` is required by the shared parser and unused here.

Output is one JSON object: `classification`; `rounds`, an ordered list carrying `index`, `verdict`, `plan_sha256`, `counts` (high/medium/low) and `fingerprints`; `resolved`, `new`, `persisting` and `reappeared`, fingerprint lists relative to the previous round; and `recommendation`, one advisory sentence.

`classification` is the first match in this order, evaluated on the last record: `approved`, last verdict APPROVED; `blocked`, last verdict BLOCKED; `polish_only`, REVISE with only `low` findings; `regressed`, a high+medium count above the previous round's; `oscillating`, at least three comparable rounds and a fingerprint of the last round that was present earlier, absent later, then returned; `stuck`, a fingerprint set identical to the previous round's; `progress` otherwise.

A BLOCKED round is a comparison barrier. Validation permits a completed BLOCKED record to carry zero findings, so such a record stays in `rounds` but is never a comparison partner and never emits finding transitions. Comparable rounds are the records after the most recent BLOCKED record, or all records when none is BLOCKED. Like a single round, the first REVISE round after a barrier has no previous round, and all four transition lists are empty.

A finding fingerprint is the SHA256 of `severity|path|evidence`, with `severity` lowercased, `path` kept verbatim apart from surrounding whitespace, and `evidence` the full text lowercased with whitespace collapsed to single spaces. Nothing is truncated, and the finding `id` is unused because reviewers renumber between rounds. This detects verbatim and near-verbatim recurrence, not semantic equivalence: a reworded finding looks new, and two distinct defects whose path and evidence text coincide are indistinguishable.

The classification is advisory and cannot approve anything. It cannot prove that two matched findings concern the same defect: the host still arbitrates each finding, and the round budget still binds.

## Compatibility

Live-tested development baseline: Codex CLI **0.153.4** with **GPT-6 Astra**, and Claude Code **2.1.261** with **Fable 5.1**, on Windows. The older npm Codex CLI 0.144.5 exposed the required flags but Astra rejected it with “requires a newer version of Codex.” A version/help probe alone does not establish model compatibility. Verify the selected binary and account; see the repository's validation record for actual live coverage. The automated suite uses fake CLI processes and does not consume model quota. Optional live smoke tests should use disposable fixtures and an explicit model, never a production build.

Primary references: [Claude programmatic usage](https://code.claude.com/docs/en/headless), [Claude CLI](https://code.claude.com/docs/en/cli-reference), [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
