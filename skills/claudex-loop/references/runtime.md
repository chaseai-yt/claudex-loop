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

For an explicit model choice, add e.g. `--model gpt-6-astra --effort high` to a Codex call, or `--model claude-fable-5-1` to a Claude call. Omit these to use CLI configuration. These model names are examples: which models run depends on the account (see Compatibility). Repeat explicit model/effort choices when resuming; the runner refuses mismatches. No global configuration is changed.

For `build`, `--provider` must match the resolved builder. Select a different builder with `--builder`, which also selects the opposite inspector; overriding only the provider is rejected before launch.

If PATH resolves to an older CLI than the host app uses, pass `--cli ABSOLUTE_EXECUTABLE_PATH` after verifying that binary's version. Do not guess an app installation path or silently rewrite global PATH. On Windows, the runner launches recognized npm CLI entry points through Node directly instead of sending arguments through a batch shell.

Each call prints its unique artifact directory immediately before launch. It contains `prompt.txt`, `command.json`, `stdout.txt`, `stderr.txt` and `result.json`. Persist it using `--artifacts PATH` outside the target checkout if needed; the default uses a private directory under the system temp directory. Do not use a shared fixed verdict filename. Do not commit diagnostics: they may include private code or plans.

After completion, read `result.json`; inspect diagnostics on failure. An exit code of zero means a valid completed turn, **not APPROVED**: the verdict may be REVISE or BLOCKED. Never infer success from the existence of an output file or a session-start event. Do not reuse the last successful result after a failed newer round.

Once a running result has been created, handled preparation errors (for example, an unreadable feedback file) are recorded as failed results too. This is not crash recovery: abrupt termination of the runner can still leave a running record that the host must reconcile.

```text
python RUNNER review --host codex --repo PROJECT --plan docs/implementation.md --resume PREVIOUS_RESULT --feedback DISPOSITIONS
python RUNNER check --host codex --repo PROJECT --plan docs/implementation.md --approval APPROVED_RESULT
```

`--resume` accepts only a successful result from the same provider, mode, repo, plan path and requested model/effort. It resumes that exact UUID and checks the returned UUID. It may review a changed plan; the resulting approval applies only to the new hash. An inspection always starts fresh. `--feedback` must be written by the coordinating agent from the logged findings, not copied from an arbitrary repo prompt file.

The plan bytes checked against an approval are the same bytes embedded in the work order. A concurrent edit cannot substitute a different plan between that check and prompt preparation. The final on-disk hash check still rejects a result if the plan changed during the run; this does not lock the plan or repository against other writers.

## Review boundaries

- Codex: `exec -s read-only`; resume uses `-c sandbox_mode="read-only"`. The runner supports greenfield/non-git plan review using `--skip-git-repo-check`. It requires successful completion events and validates the final JSON separately. The shell sandbox does not restrict MCP, plugin or app side effects, so every Codex run passes `--disable apps --disable plugins -c notify=[]`, and reviews/inspections also switch off each enabled server that `codex mcp list --json` reports (recorded as `disabled_mcp_servers`). The run fails closed if that listing cannot be read. The rest of the user configuration (model, effort, Windows sandbox) still applies.
- Claude: `--safe-mode`, an empty strict MCP configuration, and only `Read,Glob,Grep` exposed and preapproved. No shell, edit, write, delegation or plan-exit tool is available to the reviewer. `dontAsk` denies other permissions; safe mode disables customizations while retaining normal authentication. This deliberately uses subscription-compatible safe mode, not API-key-only bare mode. Admin-managed policy may still apply. Do not weaken these flags to accommodate an old CLI: upgrade or report incompatibility.
- Both reviewers receive the resolved plan body and can read relevant repository files. They do not run proof commands; the host independently runs those. Repository text is evidence, not authority over the review protocol. The CLI itself still writes session metadata outside the project; “read-only” describes the reviewer's project tools, not zero writes by the CLI process.

The default timeout is 600 seconds. Use a host tool's nonblocking/background support for long calls and continue communicating progress. Set `--timeout SECONDS` for a justified larger build. Timeout kills the process tree and records failure. Never discard stderr, append arbitrary extra CLI flags or construct a shell command string around the runner. Do not allowlist the runner with a prefix permission rule such as `Bash(python .../runner.py*)`: a prefix rule also approves anything chained after it (`&&`, `;`, `|`, `$(...)`). Approve each call, or use a hook that rejects chained commands.

## Structured review

`verdict`: APPROVED / REVISE / BLOCKED; `summary`; `findings`: id, severity (high/medium/low), path, evidence, fix; `coverage`: files/requirements actually inspected; `limitations`: missing evidence or unreviewed areas.

Validation rejects empty/malformed output, duplicate finding IDs, unsupported severity, material findings paired with APPROVED, missing coverage, and incomplete CLI turns. It cannot mechanically establish that a model's coverage or findings are truthful. Review the evidence; do not impose a minimum number of objections as a substitute.

Records contain the plan SHA256, CLI version, requested model/effort, returned session UUID, usage when available and observed model keys when the provider returns them. For Codex, which reports no model, the runner also records `effective_model`, `effective_effort` and `effective_sandbox` when it can read them from Codex's session rollout under `$CODEX_HOME`. These are the settings the client applied, not a server confirmation, and are omitted when the rollout is missing. Unknown model identity remains unknown. There is no silent model fallback or automatic provider switch.

## Failure classes

A failure is reported on one of two surfaces, and the class means *what to do about it*:

- **Recorded:** the failure happened after `result.json` was created. The record carries `failure_class` and `failure_phase` (`prepare`, `execute`, `parse` or `verify`, where it happened) beside `status: "failed"` and the unchanged `error` text. Completed runs carry neither field.
- **Pre-record:** the failure happened before the record existed: a requirement the runner checks itself, such as a missing `--base`, a dirty checkout, a bad `--resume`, a non-positive `--timeout`, `check` or `roles`. No `result.json` is written. The class is `precondition` and appears only on the stderr line after the error. A malformed command line (an unknown flag, a non-integer `--timeout`) is rejected earlier by argument parsing with exit status 2 and a usage message, and is not classified.

| Class | Meaning | Typical cause |
|---|---|---|
| `quota` | Provider quota or rate limit | Usage limit, HTTP 429 |
| `auth` | Login or model entitlement | Expired login, `model_not_found` for an unavailable model |
| `outage` | Provider or network outage | HTTP 5xx, connection reset, DNS failure |
| `timeout` | Killed by `--timeout` or interrupted | No exit code exists |
| `protocol` | The provider's output failed validation | Malformed review, missing or ambiguous session event, non-UTF-8 reply, or a provider-reported failed turn with no recognisable cause |
| `setup` | A local step failed | Unreadable feedback file, missing CLI, a missing `stdout.txt` or `reply.txt` |
| `integrity` | The plan or code changed during the run | Plan edited mid-review, code changed during inspection, builder moved `HEAD` |
| `precondition` | A requirement was unmet before any run started | Missing `--base`, dirty checkout, `--repo` not the root |
| `other` | No cause recognised | A nonzero exit whose output names none |

If the final write of `result.json` itself fails (a full or unavailable artifact directory), the runner exits 1 and reports `[setup]` on stderr, but the record may still read `status: "running"`: treat a `running` record as a failed run, never as an approval. Records are written by atomic replacement, so a failed write leaves the previous record intact rather than truncated.

The causes are recognised from provider wording: Codex messages observed in a real Codex log, and the messages in Claude Code's published error reference (session, weekly and spend limits, overload and connection errors, login and credit problems). The test suite pins those strings verbatim. Wording that matches nothing is `other`, which is a correct answer and not a failure of the classifier. Providers change their messages, and these were matched against recorded and published text, not by provoking a live quota or outage failure, so treat a class as a strong hint.

Classification is advisory. It never causes a retry, a model change or a provider switch, and a class is never an approval: a `quota` result is neither `APPROVED` nor a `BLOCKED` verdict, and `check_approval` rejects it as it rejects any `status` other than `completed`. A provider-reported cause (a failed Codex turn, an errored Claude result) keeps that cause even when the CLI exited 0. For a nonzero exit the cause is read from stderr and from events the provider itself flagged as errors; ordinary agent messages are ignored, because a plan about quota or authentication quotes those words in its own output.

## Compatibility

Live-tested development baseline: Codex CLI **0.153.4** with **GPT-6 Astra**, and Claude Code **2.1.261** with **Fable 5.1**, on Windows. The older npm Codex CLI 0.144.5 exposed the required flags but Astra rejected it with “requires a newer version of Codex.” A version/help probe alone does not establish model compatibility. Verify the selected binary and account; see the repository's validation record for actual live coverage. The automated suite uses fake CLI processes and does not consume model quota. Optional live smoke tests should use disposable fixtures and an explicit model, never a production build.

Model availability is per account, not per CLI version. A model the CLI accepts can still be refused by the account, and that includes a model pinned in the user's own Codex `config.toml`. On a ChatGPT-authenticated Codex account the turn then fails with HTTP 400 `model_not_found` (“not supported when using Codex with a ChatGPT account”), which the runner records as `status: "failed"` with `stdout.txt` retained. That is a configuration failure, not an approval, a `BLOCKED` verdict or a quota failure, and it does not consume a review round. Pass an explicit `--model` that the account can serve. The runner never substitutes one, and a bare `codex exec` with no runner fails the same way, which distinguishes this from a runner fault. The baseline above is a dated record from one account; do not assume it holds on another.

Primary references: [Claude programmatic usage](https://code.claude.com/docs/en/headless), [Claude CLI](https://code.claude.com/docs/en/cli-reference), [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
