"""Contract tests use real subprocesses and disposable Git repositories, no model calls."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("runner", ROOT / "skills/claudex-loop/scripts/runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
SESSION = "12345678-1234-4567-8123-123456789abc"
GOOD = {"verdict": "APPROVED", "summary": "The supplied acceptance criteria are consistent.",
        "findings": [], "coverage": ["docs/custom plan.md"], "limitations": []}

FAKE_CLI = r'''
import json, os, pathlib, sys, time
if '--version' in sys.argv:
    print('fake-cli 1.0')
    sys.exit(0)
prompt = sys.stdin.read()
case = os.environ.get('FAKE_CASE', 'ok')
if case == 'timeout':
    time.sleep(30)
if case == 'exit':
    print('Authentication failed', file=sys.stderr)
    sys.exit(7)
if case == 'quota':
    print('Error: rate limit exceeded, please retry later (429)', file=sys.stderr)
    sys.exit(7)
if case == 'empty':
    sys.exit(0)
if case == 'mutate_plan':
    pathlib.Path(os.environ['FAKE_PLAN']).write_text('Changed after launch')
if case == 'mutate_code':
    pathlib.Path('new.py').write_text('changed during inspection')
if case == 'build':
    pathlib.Path('built.py').write_text('print(42)\n')
session = '12345678-1234-4567-8123-123456789abc'
if case == 'wrong_session':
    session = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
review = {'verdict':'APPROVED', 'summary':'Inspected supplied plan.',
          'findings':[], 'coverage':['custom plan.md'], 'limitations':[]}
if case == 'revise':
    review.update(verdict='REVISE', findings=[{'id':'R1','severity':'high','path':'plan',
                  'evidence':'Deletion before successful copy loses the only copy.',
                  'fix':'Verify the new copy before removing the old one.'}])
if case == 'blocked':
    review.update(verdict='BLOCKED', coverage=[], limitations=['Required schema unavailable.'])
if case == 'malformed':
    review = {'verdict':'APPROVED'}
if 'exec' in sys.argv:
    output = pathlib.Path(sys.argv[sys.argv.index('-o')+1])
    output.write_text('Built; proof passed.' if case == 'build' else json.dumps(review))
    print(json.dumps({'type':'thread.started', 'thread_id':session}))
    if case == 'turn_failed':
        print(json.dumps({'type':'turn.failed', 'error':{'message':'quota'}}))
    elif case != 'incomplete':
        print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}))
else:
    value = {'type':'result','subtype':'success','is_error':False,'session_id':session,
             'structured_output':review, 'result':'Built; proof passed.',
             'modelUsage':{'claude-test':{'inputTokens':10}},'usage':{'input_tokens':10}}
    if case == 'turn_failed':
        value.update(subtype='error_during_execution',is_error=True)
    print(json.dumps([{'type':'system','subtype':'init'}, value] if case == 'array' else value))
'''

FAKE_ORCA = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path

state_dir = Path(os.environ['FAKE_ORCA_STATE'])
state_dir.mkdir(parents=True, exist_ok=True)


def emit(result):
    print(json.dumps({'ok': True, 'result': result}))


def fail(code, message):
    print(json.dumps({'ok': False, 'error': {'code': code, 'message': message}}))


args = sys.argv[1:]
if args and args[-1] == '--json':
    args = args[:-1]

if args[:2] == ['terminal', 'create']:
    cmd = args[args.index('--command') + 1]
    handle = 'term_' + os.urandom(4).hex()
    proc = subprocess.Popen(['bash', '-c', cmd], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    (state_dir / f'{handle}.pid').write_text(str(proc.pid))
    emit({'terminal': {'handle': handle}})
elif args[:2] == ['terminal', 'wait']:
    handle = args[args.index('--terminal') + 1]
    timeout_ms = int(args[args.index('--timeout-ms') + 1]) if '--timeout-ms' in args else 5000
    pid = int((state_dir / f'{handle}.pid').read_text())
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            emit({'wait': {'status': 'exited', 'exitCode': 0}})
            sys.exit(0)
        time.sleep(0.02)
    fail('timeout', 'timeout')
elif args[:2] == ['terminal', 'close']:
    handle = args[args.index('--terminal') + 1]
    pid_file = state_dir / f'{handle}.pid'
    killed = False
    if pid_file.is_file():
        try:
            os.kill(int(pid_file.read_text()), 9)
            killed = True
        except ProcessLookupError:
            pass
    (state_dir / f'{handle}.closed').write_text('1')
    emit({'close': {'ptyKilled': killed}})
else:
    fail('unsupported', 'unsupported fake orca command')
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claudex-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.plan = self.root / "custom plan.md"
        self.plan.write_text("# Work order\nKeep the original until the copy is verified.\n", encoding="utf-8")
        self.artifacts = self.root / "runs"
        self.cli = self.root / "fake_cli.py"
        self.cli.write_text(FAKE_CLI)
        self.orca = self.root / "fake_orca.py"
        self.orca.write_text(FAKE_ORCA)
        self.orca.chmod(0o755)
        self.orca_state = self.root / "orca-state"
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.repo / "existing.py").write_text("original\n")
        (self.repo / "delete.py").write_text("delete me\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, stderr=subprocess.PIPE).decode()

    def invoke(self, host="claude", mode="review", case="ok", extra=(), plan=None):
        plan = plan or self.plan
        args = [mode, "--host", host, "--repo", str(self.repo), "--plan", str(plan),
                "--artifacts", str(self.artifacts), *extra]
        old = set(self.artifacts.glob("*/result.json")) if self.artifacts.exists() else set()
        output, error = io.StringIO(), io.StringIO()
        env = {"FAKE_CASE": case, "FAKE_PLAN": str(plan),
               "ORCA_CLI_COMMAND": str(self.orca), "FAKE_ORCA_STATE": str(self.orca_state)}
        with patch.object(runner, "cli_prefix", return_value=[sys.executable, str(self.cli)]), \
             patch.dict(os.environ, env), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = runner.main(args)
        new = set(self.artifacts.glob("*/result.json")) - old if self.artifacts.exists() else set()
        path = next(iter(new)) if new else None
        return code, json.loads(path.read_text()) if path else None, path, error.getvalue()

    def test_host_role_defaults_and_builder_override(self):
        self.assertEqual(runner.resolve_roles("claude")["reviewer"], "codex")
        self.assertEqual(runner.resolve_roles("codex")["reviewer"], "claude")
        roles = runner.resolve_roles("codex", builder="claude")
        self.assertEqual((roles["planner"], roles["builder"], roles["inspector"]), ("codex", "claude", "codex"))
        with self.assertRaises(runner.RunError):
            runner.resolve_roles("codex", "codex")

    def test_both_review_adapters_complete_and_bind_custom_plan(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                code, record, path, _ = self.invoke(host)
                self.assertEqual(code, 0, record)
                self.assertEqual(record["session_id"], SESSION)
                self.assertEqual(record["plan"], str(self.plan))
                self.assertEqual(record["plan_sha256"], runner.digest(self.plan.read_bytes()))
                self.assertIn(str(self.plan), (path.parent / "prompt.txt").read_text())
                self.assertEqual(record["response"]["verdict"], "APPROVED")

    def test_unpinned_and_explicit_model_selection(self):
        for provider in runner.PROVIDERS:
            args = runner.command(provider, "review", self.root)
            self.assertNotIn("--model", args)
            self.assertNotIn("-m", args)
            pinned = runner.command(provider, "review", self.root, "chosen-model", "high")
            self.assertIn("chosen-model", pinned)

    def test_claude_exposes_only_read_tools_and_no_mcp(self):
        args = runner.command("claude", "review", self.root)
        self.assertEqual(args[args.index("--tools")+1], "Read,Glob,Grep")
        self.assertIn("--safe-mode", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(args[args.index("--permission-mode")+1], "dontAsk")

    def test_codex_resume_keeps_read_only_and_explicit_session(self):
        args = runner.command("codex", "review", self.root, session=SESSION)
        self.assertEqual(args[:3], ["exec", "resume", SESSION])
        self.assertIn('sandbox_mode="read-only"', args)
        self.assertNotIn("-s", args)
        self.assertNotIn("--last", args)

    def test_failures_never_approve_and_keep_diagnostics(self):
        for host in ("claude", "codex"):
            for case in ("exit", "empty", "malformed", "turn_failed"):
                with self.subTest(host=host, case=case):
                    code, record, path, _ = self.invoke(host, case=case)
                    self.assertEqual(code, 1)
                    self.assertEqual(record["status"], "failed")
                    self.assertTrue((path.parent / "stderr.txt").exists())
                    if case == "exit":
                        self.assertIn("Authentication failed", (path.parent / "stderr.txt").read_text())

    def test_missing_codex_completion_is_failure(self):
        code, record, _, _ = self.invoke(case="incomplete")
        self.assertEqual(code, 1)
        self.assertEqual(record["status"], "failed")

    def test_claude_array_envelope(self):
        code, record, _, _ = self.invoke("codex", case="array")
        self.assertEqual(code, 0)
        self.assertEqual(record["observed_models"], ["claude-test"])

    def test_revise_and_blocked_are_completed_but_not_approval(self):
        for case in ("revise", "blocked"):
            code, record, _, _ = self.invoke(case=case)
            self.assertEqual(code, 0)
            with self.assertRaises(runner.RunError):
                runner.check_approval(record, self.plan, self.repo)

    def test_empty_findings_allowed_but_contradictory_approval_rejected(self):
        runner.validate_review(copy.deepcopy(GOOD))
        value = copy.deepcopy(GOOD)
        value["findings"] = [{"id":"1", "severity":"high", "path":"plan", "evidence":"data loss", "fix":"retain copy"}]
        with self.assertRaises(runner.RunError):
            runner.validate_review(value)

    def test_changed_plan_invalidates_approval(self):
        _, record, _, _ = self.invoke()
        runner.check_approval(record, self.plan, self.repo)
        self.plan.write_text("Different requirements")
        with self.assertRaises(runner.RunError):
            runner.check_approval(record, self.plan, self.repo)

    def test_changed_plan_during_review_fails(self):
        code, record, _, _ = self.invoke(case="mutate_plan")
        self.assertEqual(code, 1)
        self.assertIn("changed during", record["error"])

    def test_resume_revised_plan_same_session(self):
        _, _, previous, _ = self.invoke(case="revise")
        self.plan.write_text("New revision")
        code, record, _, _ = self.invoke(extra=("--resume", str(previous)))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["session_id"], SESSION)

    def test_wrong_session_is_refused(self):
        _, _, previous, _ = self.invoke()
        code, record, _, _ = self.invoke(case="wrong_session", extra=("--resume", str(previous)))
        self.assertEqual(code, 1)
        self.assertIn("different session", record["error"])

    def test_resume_wrong_provider_or_model_rejected_before_launch(self):
        _, _, previous, _ = self.invoke()
        code, record, _, error = self.invoke("codex", extra=("--resume", str(previous)))
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("provider", error)
        code, record, _, error = self.invoke(extra=("--resume", str(previous), "--model", "new-model"))
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("requested_model", error)

    def test_timeout_records_failure(self):
        code, record, _, _ = self.invoke(case="timeout", extra=("--timeout", "1"))
        self.assertEqual(code, 1)
        self.assertIn("timed out", record["error"])

    def test_quota_exhaustion_is_classified_separately_from_defects(self):
        code, record, _, _ = self.invoke(case="quota")
        self.assertEqual(code, 1)
        self.assertEqual(record["failure_kind"], "quota_exhausted")

    def test_generic_cli_failure_defaults_to_defect(self):
        code, record, _, _ = self.invoke(case="exit")
        self.assertEqual(code, 1)
        self.assertEqual(record["failure_kind"], "defect")

    def test_unique_artifacts_and_failed_round_does_not_reuse_reply(self):
        _, _, first, _ = self.invoke()
        code, record, second, _ = self.invoke(case="empty")
        self.assertNotEqual(first, second)
        self.assertEqual(code, 1)
        self.assertNotIn("response", record)

    def test_snapshot_covers_staged_unstaged_deleted_and_new_files(self):
        (self.repo / "existing.py").write_text("staged version\n")
        self.git("add", "existing.py")
        (self.repo / "existing.py").write_text("unstaged final version\n")
        (self.repo / "delete.py").unlink()
        (self.repo / "new.py").write_text("brand new\n")
        snap = runner.snapshot(self.repo, self.base)
        self.assertEqual({f["path"] for f in snap["files"]}, {"existing.py", "delete.py", "new.py"})
        self.assertEqual(next(f for f in snap["files"] if f["path"] == "delete.py")["kind"], "deleted")
        self.assertEqual(next(f for f in snap["files"] if f["path"] == "existing.py")["sha256"],
                         runner.digest((self.repo / "existing.py").read_bytes()))

    def test_inspection_requires_other_provider_and_fresh_session(self):
        code, _, _, error = self.invoke(mode="inspect", extra=("--base", self.base, "--provider", "claude"))
        self.assertEqual(code, 1)
        self.assertIn("opposite the builder", error)
        _, _, previous, _ = self.invoke()
        code, _, _, error = self.invoke(mode="inspect", extra=("--base", self.base, "--resume", str(previous)))
        self.assertEqual(code, 1)
        self.assertIn("fresh session", error)

    def test_changed_code_during_inspection_fails(self):
        (self.repo / "new.py").write_text("original new file")
        code, record, _, _ = self.invoke(mode="inspect", case="mutate_code", extra=("--base", self.base))
        self.assertEqual(code, 1)
        self.assertIn("Code changed", record["error"])

    def test_build_requires_explicit_review_override_and_clean_tree(self):
        code, _, _, error = self.invoke(mode="build", extra=("--proof", "python -m unittest"))
        self.assertEqual(code, 1)
        self.assertIn("--approval", error)
        (self.repo / "user_work.py").write_text("preserve me")
        code, _, _, error = self.invoke(mode="build", extra=("--unreviewed-spec", "--proof", "test"))
        self.assertEqual(code, 1)
        self.assertIn("clean checkout", error)
        self.assertEqual((self.repo / "user_work.py").read_text(), "preserve me")

    def test_build_resume_keeps_initial_baseline_and_existing_build_changes(self):
        extra = ("--builder", "codex", "--unreviewed-spec", "--proof", "python -m unittest")
        code, record, path, _ = self.invoke(mode="build", case="build", extra=extra)
        self.assertEqual(code, 0, record)
        self.assertEqual(record["base"], self.base)
        code, record, _, _ = self.invoke(mode="build", case="build", extra=extra+("--resume", str(path)))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["base"], self.base)
        (self.repo / "user_work.py").write_text("intervening edit")
        code, _, _, error = self.invoke(mode="build", case="build", extra=extra+("--resume", str(path)))
        self.assertEqual(code, 1)
        self.assertIn("Checkout changed", error)

    def test_artifacts_cannot_contaminate_target_checkout(self):
        code, _, _, error = self.invoke(extra=("--artifacts", str(self.repo / "runs")))
        self.assertEqual(code, 1)
        self.assertIn("outside", error)

    @unittest.skipIf(os.name == "nt", "orca executor is validated on POSIX shells only")
    def test_via_orca_and_subprocess_reach_the_same_result(self):
        for via in ("subprocess", "orca"):
            with self.subTest(via=via):
                code, record, _, _ = self.invoke(extra=("--via", via))
                self.assertEqual(code, 0, record)
                self.assertEqual(record["executor"], via)
                self.assertEqual(record["response"]["verdict"], "APPROVED")

    @unittest.skipIf(os.name == "nt", "orca executor is validated on POSIX shells only")
    def test_via_auto_prefers_orca_when_resolvable(self):
        code, record, _, _ = self.invoke(extra=("--via", "auto"))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["executor"], "orca")

    @unittest.skipIf(os.name == "nt", "orca executor is validated on POSIX shells only")
    def test_via_orca_timeout_closes_terminal_and_records_failure(self):
        code, record, _, _ = self.invoke(case="timeout", extra=("--via", "orca", "--timeout", "1"))
        self.assertEqual(code, 1)
        self.assertIn("timed out", record["error"])
        self.assertTrue(list(self.orca_state.glob("*.closed")),
                        "execute_via_orca must close the terminal on timeout")

    @unittest.skipIf(os.name == "nt", "orca executor is validated on POSIX shells only")
    def test_via_orca_uses_file_captured_exit_code_not_orca_reported_one(self):
        # The fake orca's own `wait` reports a fixed dummy exitCode (mirroring
        # real Orca's observed unreliable field); the real exit code must
        # come from the file the launched command wrote, not that field.
        code, record, path, _ = self.invoke(case="exit", extra=("--via", "orca"))
        self.assertEqual(code, 1)
        self.assertEqual(record["exit_code"], 7)
        self.assertIn("Authentication failed", (path.parent / "stderr.txt").read_text())

    @unittest.skipIf(os.name == "nt", "orca executor is validated on POSIX shells only")
    def test_via_orca_handles_shell_metacharacters_in_paths(self):
        # The plan's path only ever appears as inert text *inside*
        # prompt.txt (read over stdin); it's never interpolated into the
        # constructed shell command, so it doesn't exercise shlex quoting.
        # --artifacts does: run_dir (and therefore prompt/stdout/stderr/exit
        # paths embedded directly in the shell command string) is created
        # under it.
        tricky_artifacts = self.root / "art if$acts `tricky`; (parens)"
        args = ["review", "--host", "claude", "--repo", str(self.repo), "--plan", str(self.plan),
                "--artifacts", str(tricky_artifacts), "--via", "orca"]
        env = {"FAKE_CASE": "ok", "FAKE_PLAN": str(self.plan),
               "ORCA_CLI_COMMAND": str(self.orca), "FAKE_ORCA_STATE": str(self.orca_state)}
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner, "cli_prefix", return_value=[sys.executable, str(self.cli)]), \
             patch.dict(os.environ, env), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = runner.main(args)
        results = list(tricky_artifacts.glob("*/result.json")) if tricky_artifacts.exists() else []
        self.assertEqual(code, 0, error.getvalue())
        self.assertTrue(results, "expected a result.json under the tricky --artifacts path")
        record = json.loads(results[0].read_text())
        self.assertEqual(record["response"]["verdict"], "APPROVED")

    def test_via_orca_rejects_on_windows(self):
        with patch.object(runner.os, "name", "nt"):
            with self.assertRaises(runner.RunError):
                runner.execute_via_orca([sys.executable, str(self.cli)], self.root, 5,
                                        str(self.orca), self.repo, f"path:{self.repo}")

    def test_via_auto_skips_orca_on_windows(self):
        # A targeted unit test on the decision itself, not a full run(): a
        # global os.name patch would also flip execute()'s own Windows
        # branch, which references subprocess.CREATE_NEW_PROCESS_GROUP -- a
        # constant that only exists on an actual Windows Python build.
        with patch.object(runner.os, "name", "nt"):
            self.assertFalse(runner.should_try_orca("auto"))
            self.assertTrue(runner.should_try_orca("orca"))
        # Outside the patch this reflects the *real* host platform running
        # the suite, which is "nt" on actual Windows CI -- assert the
        # invariant, not a platform-specific literal.
        self.assertEqual(runner.should_try_orca("auto"), os.name != "nt")

    def test_via_orca_requires_resolvable_cli(self):
        output, error = io.StringIO(), io.StringIO()
        args = ["review", "--host", "claude", "--repo", str(self.repo), "--plan", str(self.plan),
                "--artifacts", str(self.artifacts), "--via", "orca"]
        env = dict(os.environ, FAKE_CASE="ok", FAKE_PLAN=str(self.plan), PATH="/nonexistent")
        env.pop("ORCA_CLI_COMMAND", None)
        env.pop("ORCA_DEV_REPO_ROOT", None)
        with patch.object(runner, "cli_prefix", return_value=[sys.executable, str(self.cli)]), \
             patch.dict(os.environ, env, clear=True), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = runner.main(args)
        self.assertEqual(code, 1)
        # --via orca fails inside run()'s own guarded block, so the message lands
        # in the printed result record (stdout), not a bare stderr traceback.
        self.assertIn("resolvable Orca CLI", json.loads(output.getvalue())["error"])


if __name__ == "__main__":
    unittest.main()
