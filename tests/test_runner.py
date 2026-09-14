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

    def invoke(self, host="claude", mode="review", case="ok", extra=()):
        args = [mode, "--host", host, "--repo", str(self.repo), "--plan", str(self.plan),
                "--artifacts", str(self.artifacts), *extra]
        old = set(self.artifacts.glob("*/result.json")) if self.artifacts.exists() else set()
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner, "cli_prefix", return_value=[sys.executable, str(self.cli)]), \
             patch.dict(os.environ, {"FAKE_CASE": case, "FAKE_PLAN": str(self.plan)}), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = runner.main(args)
        new = set(self.artifacts.glob("*/result.json")) - old if self.artifacts.exists() else set()
        path = next(iter(new)) if new else None
        return code, json.loads(path.read_text()) if path else None, path, error.getvalue()

    def finding(self, severity="high", path="plan", ident="F1",
                evidence="Deletion before the copy is verified loses the only copy."):
        return {"id": ident, "severity": severity, "path": path, "evidence": evidence,
                "fix": "Verify the new copy before removing the old one."}

    def write_record(self, name, verdict, findings, plan_sha="a" * 64, **overrides):
        directory = self.artifacts / name
        directory.mkdir(parents=True, exist_ok=True)
        record = {"status": "completed", "mode": "review", "provider": "codex",
                  "repo": str(self.repo), "plan": str(self.plan), "plan_sha256": plan_sha,
                  "response": {"verdict": verdict, "summary": "Inspected the supplied plan.",
                               "findings": findings, "coverage": ["custom plan.md"],
                               "limitations": ["Schema unavailable."] if verdict == "BLOCKED" else []}}
        record.update(overrides)
        path = directory / "result.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return str(path)

    def invoke_progress(self, paths):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = runner.main(["progress", "--host", "claude", "--results", *paths])
        text = output.getvalue().strip()
        return code, json.loads(text) if text else None, error.getvalue()

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

    def test_progress_single_revise_round_reports_progress(self):
        code, report, error = self.invoke_progress([self.write_record("r1", "REVISE", [self.finding()])])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "progress")
        self.assertEqual([report["resolved"], report["new"], report["persisting"],
                          report["reappeared"]], [[], [], [], []])
        self.assertEqual(len(report["rounds"]), 1)
        self.assertEqual(report["rounds"][0]["index"], 1)
        self.assertEqual(report["rounds"][0]["verdict"], "REVISE")
        self.assertEqual(report["rounds"][0]["counts"], {"high": 1, "medium": 0, "low": 0})
        self.assertEqual(len(report["rounds"][0]["fingerprints"]), 1)
        self.assertIn("round budget", report["recommendation"])

    def test_progress_approval_resolves_the_open_finding(self):
        first = self.write_record("r1", "REVISE", [self.finding()])
        second = self.write_record("r2", "APPROVED", [], plan_sha="b" * 64)
        code, report, error = self.invoke_progress([first, second])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "approved")
        self.assertEqual(report["resolved"], report["rounds"][0]["fingerprints"])
        self.assertEqual([report["new"], report["persisting"]], [[], []])
        self.assertEqual(report["rounds"][1]["plan_sha256"], "b" * 64)

    def test_progress_normalizes_evidence_but_keeps_paths_exact(self):
        first = self.write_record("r1", "REVISE", [self.finding(evidence="Deletion  before\n copy.")])
        second = self.write_record("r2", "REVISE", [self.finding(ident="R7", evidence="DELETION BEFORE COPY.")])
        code, report, error = self.invoke_progress([first, second])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "stuck")
        self.assertEqual(report["persisting"], report["rounds"][0]["fingerprints"])
        third = self.write_record("r3", "REVISE", [self.finding(path="src/a-b.py")])
        fourth = self.write_record("r4", "REVISE", [self.finding(path="src/ab.py")])
        code, report, error = self.invoke_progress([third, fourth])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "progress")
        self.assertEqual((len(report["resolved"]), len(report["new"])), (1, 1))

    def test_progress_fingerprints_unencodable_evidence(self):
        path = self.write_record("r1", "REVISE", [self.finding(evidence="\ud800 lone surrogate")])
        code, report, error = self.invoke_progress([path])
        self.assertEqual(code, 0, error)
        self.assertEqual(len(report["rounds"][0]["fingerprints"]), 1)

    def test_progress_detects_a_reappearing_finding(self):
        first = [self.finding(evidence="The rollback path is never exercised.")]
        other = [self.finding(severity="medium", evidence="The timeout default is undocumented.")]
        paths = [self.write_record("r1", "REVISE", first), self.write_record("r2", "REVISE", other),
                 self.write_record("r3", "REVISE", first)]
        code, report, error = self.invoke_progress(paths)
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "oscillating")
        self.assertEqual(report["reappeared"], report["rounds"][0]["fingerprints"])

    def test_progress_detects_regression_in_material_findings(self):
        first = self.write_record("r1", "REVISE", [self.finding(severity="medium")])
        second = self.write_record("r2", "REVISE", [self.finding(ident="A", evidence="First defect."),
                                                    self.finding(ident="B", evidence="Second defect.")])
        code, report, error = self.invoke_progress([first, second])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "regressed")
        self.assertEqual(len(report["new"]), 2)
        self.assertEqual(report["rounds"][1]["counts"], {"high": 2, "medium": 0, "low": 0})

    def test_progress_low_severity_round_is_polish_only(self):
        code, report, error = self.invoke_progress(
            [self.write_record("r1", "REVISE", [self.finding(severity="low")])])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "polish_only")
        self.assertIn("low-severity", report["recommendation"])

    def test_progress_treats_a_blocked_round_as_a_barrier(self):
        open_finding = [self.finding()]
        first = self.write_record("r1", "REVISE", open_finding)
        blocked = self.write_record("r2", "BLOCKED", [])
        code, report, error = self.invoke_progress([first, blocked])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "blocked")
        self.assertEqual(report["resolved"], [])
        third = self.write_record("r3", "REVISE", open_finding)
        code, report, error = self.invoke_progress([first, blocked, third])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "progress")
        self.assertEqual([report["resolved"], report["new"], report["persisting"],
                          report["reappeared"]], [[], [], [], []])
        fourth = self.write_record("r4", "REVISE", open_finding)
        code, report, error = self.invoke_progress([first, blocked, third, fourth])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "stuck")
        self.assertEqual(report["persisting"], report["rounds"][-1]["fingerprints"])

    def test_progress_rejects_foreign_records_and_accepts_a_real_one(self):
        first = self.write_record("r1", "REVISE", [self.finding()])
        foreign = self.write_record("r2", "REVISE", [self.finding()], plan=str(self.root / "other plan.md"))
        code, report, error = self.invoke_progress([first, foreign])
        self.assertEqual(code, 1)
        self.assertIsNone(report)
        self.assertIn(foreign, error)
        self.assertIn("plan", error)
        failed = self.write_record("r3", "REVISE", [self.finding()], status="failed")
        code, _, error = self.invoke_progress([first, failed])
        self.assertEqual(code, 1)
        self.assertIn(failed, error)
        code, _, error = self.invoke_progress([str(self.root / "missing.json")])
        self.assertEqual(code, 1)
        self.assertIn("missing.json", error)
        for name, verdict, findings, overrides in (("r4", "APPROVED", [self.finding()], {}),
                                                   ("r5", "REVISE", [], {}),
                                                   ("r6", "REVISE", [self.finding()], {"plan_sha256": None})):
            with self.subTest(record=name):
                invalid = self.write_record(name, verdict, findings, **overrides)
                code, report, error = self.invoke_progress([invalid])
                self.assertEqual(code, 1)
                self.assertIsNone(report)
                self.assertIn(invalid, error)
        _, record, real, _ = self.invoke(case="revise")
        code, report, error = self.invoke_progress([str(real)])
        self.assertEqual(code, 0, error)
        self.assertEqual(report["classification"], "progress")
        self.assertEqual(report["rounds"][0]["plan_sha256"], record["plan_sha256"])

    def test_progress_writes_no_artifacts_and_needs_no_cli(self):
        paths = [self.write_record("r1", "REVISE", [self.finding()]),
                 self.write_record("r2", "APPROVED", [])]
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob("*")}
        forbidden = Exception("progress must not launch a CLI or write files")
        with patch.object(runner, "cli_prefix", side_effect=forbidden), \
             patch.object(runner.subprocess, "Popen", side_effect=forbidden), \
             patch.object(runner, "save", side_effect=forbidden), \
             patch.object(runner.tempfile, "mkdtemp", side_effect=forbidden):
            code, report, error = self.invoke_progress(paths)
        self.assertEqual(code, 0, error)
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.root.rglob("*")}, before)
        self.assertEqual(report["classification"], "approved")


if __name__ == "__main__":
    unittest.main()
