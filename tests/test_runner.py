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
if case == 'arrow':
    review['summary'] = 'Sound ' + chr(0x2192) + ' ok'
if '--prompt-file' in sys.argv:
    assert os.environ.get('GROK_CLAUDE_MCPS_ENABLED') == 'false', 'reviewer env not isolated'
    assert '--no-memory' in sys.argv, 'reviewer memory not off'
    assert not {'GROK_FOLDER_TRUST', 'GROK_MEMORY'} & set(os.environ), 'trust/memory override inherited'
    value = {'text': json.dumps(review), 'stopReason': 'end_turn', 'sessionId': session,
             'structuredOutput': review, 'modelUsage': {'grok-test': {'inputTokens': 10}},
             'num_turns': 1 if case == 'no_reads' else 6}
    if case == 'turn_failed':
        value.update(stopReason='max_tokens')
    if case == 'malformed':
        value.pop('structuredOutput')
    print(json.dumps(value))
elif '--disable-slash-commands' in sys.argv:
    assert '--sandbox' in sys.argv, 'agy sandbox not enabled'
    assert '--mode' not in sys.argv, 'agy --mode is inert without slash command expansion'
    settings = json.loads(sys.argv[sys.argv.index('--settings') + 1])
    denied = set(settings.get('permissions', {}).get('deny', []))
    assert {'command(*)', 'write_file(*)', 'read_url(*)', 'execute_url(*)', 'mcp(*)'} <= denied
    assert settings['permissions']['allow'] == ['read_file(' + os.environ['FAKE_PLAN'] + ')'], 'plan not readable'
    assert '--add-dir' in sys.argv, 'agy missing --add-dir for prompt'
    value = {'conversation_id': session, 'status': 'SUCCESS', 'response': json.dumps(review),
             'structured_output': review, 'usage': {'input_tokens': 10, 'output_tokens': 5}}
    if case == 'turn_failed':
        value.update(status='ERROR', error='quota exceeded')
    if case == 'malformed':
        value.pop('structured_output')
    if case == 'denied':
        value.pop('structured_output')
        value.update(response='', denied_actions=[{'action': 'read_file', 'display_name': 'ReadFile'}])
    print(json.dumps(value))
elif 'exec' in sys.argv:
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

    def invoke(self, host="claude", mode="review", case="ok", extra=(), env=None):
        args = [mode, "--host", host, "--repo", str(self.repo), "--plan", str(self.plan),
                "--artifacts", str(self.artifacts), *extra]
        old = set(self.artifacts.glob("*/result.json")) if self.artifacts.exists() else set()
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner, "cli_prefix", return_value=[sys.executable, str(self.cli)]), \
             patch.dict(os.environ, {"FAKE_CASE": case, "FAKE_PLAN": str(self.plan),
                                     "GROK_HOME": str(self.root / "grok-home"), **(env or {})}), \
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
        for provider in runner.REVIEWERS:
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

    def test_grok_review_is_read_only_isolated_and_never_builds(self):
        args = runner.command("grok", "review", self.root, session=SESSION)
        self.assertEqual(args[args.index("--tools")+1], "read_file,grep,list_dir")
        self.assertEqual(args[args.index("--disallowed-tools")+1], "search_tool,use_tool")
        self.assertEqual(args[args.index("--sandbox")+1], "read-only")
        self.assertIn("--disable-web-search", args)
        self.assertIn("--no-memory", args)
        self.assertEqual(args[args.index("--resume")+1], SESSION)
        with self.assertRaises(runner.RunError):
            runner.command("grok", "build", self.root)
        code, record, _, _ = self.invoke(mode="build", extra=("--provider", "grok", "--unreviewed-spec",
                                                              "--proof", "true"))
        self.assertEqual(code, 1)
        self.assertIsNone(record)

    def test_grok_review_and_inspection_complete_with_isolated_env(self):
        code, record, _, _ = self.invoke(extra=("--provider", "grok"),
                                         env={"GROK_FOLDER_TRUST": "0", "GROK_MEMORY": "1"})
        self.assertEqual(code, 0, record)
        self.assertEqual((record["provider"], record["roles"]["reviewer"]), ("grok", "grok"))
        self.assertEqual(record["observed_models"], ["grok-test"])
        self.assertEqual(record["num_turns"], 6)
        code, record, _, _ = self.invoke(mode="inspect", extra=("--provider", "grok", "--base", self.base))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["roles"]["inspector"], "grok")

    @unittest.skipIf(sys.version_info < (3, 11), "Grok trust check reads TOML with tomllib")
    def test_grok_refuses_a_repo_grok_trusts(self):
        home = self.root / "grok-home"
        home.mkdir()
        store = home / "trusted_folders.toml"
        cases = {
            "repo": (f"[folders.'{self.repo}']\ntrusted = true\n", "", False),
            "parent": (f"[folders.'{self.root}']\ntrusted = true\n", "", False),
            "revoked": (f"[folders.'{self.repo}']\ntrusted = false\n", "", True),
            "sibling": (f"[folders.'{self.root / 'repo with'}']\ntrusted = true\n", "", True),
            "trust_off": ("", "[folder_trust]\nenabled = false\n", False),
            "malformed": ("[folders.\n", "", False),
        }
        for name, (folders, config, allowed) in cases.items():
            with self.subTest(case=name):
                store.write_text(folders, encoding="utf-8")
                (home / "config.toml").write_text(config, encoding="utf-8")
                code, record, _, error = self.invoke(extra=("--provider", "grok"))
                if allowed:
                    self.assertEqual(code, 0, error)
                else:
                    self.assertEqual(code, 1)
                    self.assertIsNone(record)
                    self.assertIn("Grok", error)

    def test_grok_failures_never_approve(self):
        for case in ("exit", "empty", "malformed", "turn_failed", "no_reads"):
            with self.subTest(case=case):
                code, record, _, _ = self.invoke(case=case, extra=("--provider", "grok"))
                self.assertEqual(code, 1)
                self.assertEqual(record["status"], "failed")

    def test_antigravity_host_roles_and_cross_provider_calls(self):
        roles = runner.resolve_roles("antigravity")
        self.assertEqual((roles["planner"], roles["reviewer"], roles["builder"], roles["inspector"]),
                         ("antigravity", None, "antigravity", None))
        roles = runner.resolve_roles("antigravity", reviewer="claude", builder="codex")
        self.assertEqual((roles["reviewer"], roles["builder"], roles["inspector"]), ("claude", "codex", "claude"))
        with self.assertRaises(runner.RunError):
            runner.resolve_roles("antigravity", "antigravity")
        code, record, _, error = self.invoke(host="antigravity")
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("--provider", error)
        code, record, _, error = self.invoke(host="antigravity", mode="inspect", extra=("--base", self.base))
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("--provider", error)
        for provider in ("claude", "codex", "grok"):
            with self.subTest(provider=provider):
                code, record, _, _ = self.invoke(host="antigravity", extra=("--provider", provider))
                self.assertEqual(code, 0, record)
                self.assertEqual((record["provider"], record["roles"]["reviewer"]), (provider, provider))
                code, record, _, _ = self.invoke(host="antigravity", mode="inspect",
                                                 extra=("--provider", provider, "--base", self.base))
                self.assertEqual(code, 0, record)
                self.assertEqual(record["roles"]["inspector"], provider)
        code, record, _, _ = self.invoke(host="antigravity", mode="build", case="build",
                                         extra=("--builder", "codex", "--unreviewed-spec", "--proof", "true"))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["provider"], "codex")
        code, record, _, error = self.invoke(host="antigravity", mode="build",
                                             extra=("--unreviewed-spec", "--proof", "true"))
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("Antigravity", error)

    def test_antigravity_provider_is_read_only_isolated_and_never_builds(self):
        args = runner.command("antigravity", "review", self.root, model="gemini-test", effort="high",
                              session=SESSION, plan=self.plan)
        self.assertNotIn("--mode", args)
        self.assertIn("--sandbox", args)
        self.assertIn("--disable-slash-commands", args)
        self.assertEqual(args[args.index("--conversation") + 1], SESSION)
        self.assertEqual(args[args.index("--model") + 1], "gemini-test")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertIn("mcp(*)", settings["permissions"]["deny"])
        self.assertIn("write_file(*)", settings["permissions"]["deny"])
        self.assertIn("command(*)", settings["permissions"]["deny"])
        self.assertEqual(settings["permissions"]["allow"], [f"read_file({self.plan})"])
        with self.assertRaises(runner.RunError):
            runner.command("antigravity", "build", self.root)
        code, record, _, _ = self.invoke(mode="build", extra=("--provider", "antigravity", "--unreviewed-spec",
                                                              "--proof", "true"))
        self.assertEqual(code, 1)
        self.assertIsNone(record)

    def test_antigravity_provider_review_resume_inspect_and_failures(self):
        code, record, first, _ = self.invoke(host="claude", case="revise", extra=("--provider", "antigravity"))
        self.assertEqual(code, 0, record)
        self.assertEqual((record["provider"], record["roles"]["reviewer"]), ("antigravity", "antigravity"))
        code, record, _, _ = self.invoke(host="claude", extra=("--provider", "antigravity", "--resume", str(first)))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["session_id"], SESSION)
        code, record, _, _ = self.invoke(host="codex", mode="inspect",
                                         extra=("--provider", "antigravity", "--base", self.base))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["roles"]["inspector"], "antigravity")
        for case in ("exit", "empty", "malformed", "turn_failed"):
            with self.subTest(case=case):
                code, record, _, _ = self.invoke(case=case, extra=("--provider", "antigravity"))
                self.assertEqual(code, 1)
                self.assertEqual(record["status"], "failed")
        code, record, _, _ = self.invoke(case="denied", extra=("--provider", "antigravity"))
        self.assertEqual((code, record["status"]), (1, "failed"))
        self.assertIn("auto-denied read_file", record["error"])

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
        self.assertIsNotNone(next(f for f in snap["files"] if f["path"] == "existing.py")["index"])
        self.assertIsNone(next(f for f in snap["files"] if f["path"] == "new.py")["index"])
        self.assertEqual(snap["divergent_index"], ["existing.py"])

    def test_snapshot_sees_staged_content_hidden_by_working_tree(self):
        clean = runner.snapshot(self.repo, self.base)
        self.assertEqual((clean["files"], clean["divergent_index"]), ([], []))
        (self.repo / "existing.py").write_text("staged version\n")
        self.git("add", "existing.py")
        (self.repo / "existing.py").write_text("original\n")
        self.assertEqual(self.git("status", "--porcelain").strip(), "MM existing.py")
        hidden = runner.snapshot(self.repo, self.base)
        self.assertEqual([f["path"] for f in hidden["files"]], ["existing.py"])
        self.assertEqual(hidden["divergent_index"], ["existing.py"])
        self.assertNotEqual(hidden["sha256"], clean["sha256"])
        self.assertNotEqual(hidden["staged_diff_sha256"], clean["staged_diff_sha256"])
        self.assertEqual(hidden["diff_sha256"], clean["diff_sha256"])
        self.git("add", "existing.py")
        self.assertEqual(runner.snapshot(self.repo, self.base)["sha256"], clean["sha256"])

    def test_snapshot_flags_staged_delete_of_present_file_and_staged_add_of_missing_file(self):
        self.git("rm", "-q", "--cached", "delete.py")
        (self.repo / "new.py").write_text("brand new\n")
        self.git("add", "new.py")
        (self.repo / "new.py").unlink()
        snap = runner.snapshot(self.repo, self.base)
        files = {f["path"]: f for f in snap["files"]}
        self.assertEqual(snap["divergent_index"], ["delete.py", "new.py"])
        self.assertEqual((files["delete.py"]["kind"], files["delete.py"]["index"]), ("file", None))
        self.assertEqual(files["new.py"]["kind"], "deleted")
        self.assertIsNotNone(files["new.py"]["index"])

    def test_staging_unchanged_content_changes_fingerprint(self):
        (self.repo / "existing.py").write_text("edited\n")
        unstaged = runner.snapshot(self.repo, self.base)
        self.git("add", "existing.py")
        staged = runner.snapshot(self.repo, self.base)
        self.assertEqual((unstaged["divergent_index"], staged["divergent_index"]), ([], []))
        self.assertEqual(unstaged["diff_sha256"], staged["diff_sha256"])
        self.assertNotEqual(unstaged["sha256"], staged["sha256"])

    def test_inspection_refuses_divergent_index(self):
        (self.repo / "existing.py").write_text("staged version\n")
        self.git("add", "existing.py")
        (self.repo / "existing.py").write_text("original\n")
        code, record, _, error = self.invoke(mode="inspect", extra=("--base", self.base))
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("Staged content differs", error)
        self.assertIn("existing.py", error)
        self.git("add", "existing.py")
        (self.repo / "existing.py").write_text("final\n")
        code, record, _, _ = self.invoke(mode="inspect", extra=("--base", self.base))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["snapshot"]["divergent_index"], [])

    def test_codex_reviews_ignore_user_config_but_builds_keep_it(self):
        for session in (None, SESSION):
            for mode in ("review", "inspect"):
                args = runner.command("codex", mode, self.root, session=session)
                self.assertIn("--ignore-user-config", args, (mode, session))
                self.assertIn('web_search="disabled"', args)
                self.assertIn("--skip-git-repo-check", args)
            self.assertNotIn("--ignore-user-config", runner.command("codex", "build", self.root, session=session))
        self.assertNotIn("--ignore-user-config", runner.command("claude", "review", self.root))

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

    def test_build_rejects_provider_that_contradicts_builder_before_launch(self):
        with patch.object(runner, "execute", wraps=runner.execute) as launch:
            code, record, _, error = self.invoke(
                host="codex", mode="build", case="build",
                extra=("--builder", "codex", "--provider", "claude",
                       "--unreviewed-spec", "--proof", "python -m unittest"))
        self.assertEqual(code, 1, record)
        launch.assert_not_called()
        self.assertIsNone(record)
        self.assertIn("builder", error.lower())
        self.assertFalse((self.repo / "built.py").exists())
        self.assertFalse(self.artifacts.exists() and any(self.artifacts.iterdir()))

    def test_build_accepts_explicit_provider_matching_builder(self):
        code, record, _, _ = self.invoke(
            host="codex", mode="build", case="build",
            extra=("--builder", "codex", "--provider", "codex",
                   "--unreviewed-spec", "--proof", "python -m unittest"))
        self.assertEqual(code, 0, record)
        self.assertEqual(record["provider"], record["roles"]["builder"])
        self.assertEqual(record["roles"]["inspector"], "claude")
        self.assertTrue((self.repo / "built.py").exists())

    def test_build_dispatches_approved_snapshot_and_rejects_changed_live_plan(self):
        self.plan.write_bytes(b"# Work order\r\nKeep the original until the copy is verified.\r\n")
        approved_bytes = self.plan.read_bytes()
        approved_body = self.plan.read_text(encoding="utf-8")
        _, _, approval, _ = self.invoke(host="codex")
        real_check = runner.check_approval
        unapproved_body = "UNAPPROVED REPLACEMENT: delete the original before verification."

        def change_after_check(*args, **kwargs):
            result = real_check(*args, **kwargs)
            self.plan.write_text(unapproved_body, encoding="utf-8")
            return result

        with patch.object(runner, "check_approval", side_effect=change_after_check):
            code, record, path, _ = self.invoke(
                host="codex", mode="build", case="build",
                extra=("--approval", str(approval), "--proof", "python -m unittest"))
        self.assertIsNotNone(path, record)
        prompt = (path.parent / "prompt.txt").read_text(encoding="utf-8")
        prompt_bytes = (path.parent / "prompt.txt").read_bytes()
        self.assertIn(approved_bytes, prompt_bytes)
        self.assertNotIn(b"\r\r\n", prompt_bytes)
        self.assertNotIn(unapproved_body, prompt)
        self.assertIn(approved_body, prompt)
        self.assertEqual(record["plan_sha256"], runner.digest(approved_bytes))
        self.assertEqual(code, 1, record)
        self.assertEqual(record["status"], "failed")
        self.assertTrue((self.repo / "built.py").exists())

    def test_missing_feedback_finalizes_created_run_as_failed(self):
        with patch.object(runner, "execute", wraps=runner.execute) as launch:
            code, record, _, _ = self.invoke(extra=("--feedback", str(self.root / "missing.txt")))
        self.assertEqual(code, 1)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["error"])
        self.assertGreaterEqual(record["elapsed_seconds"], 0)
        launch.assert_not_called()

    def test_invalid_utf8_feedback_finalizes_created_run_as_failed(self):
        feedback = self.root / "feedback.txt"
        feedback.write_bytes(b"Invalid UTF-8: \xff")
        with patch.object(runner, "execute", wraps=runner.execute) as launch:
            code, record, _, _ = self.invoke(extra=("--feedback", str(feedback)))
        self.assertEqual(code, 1)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["error"])
        self.assertGreaterEqual(record["elapsed_seconds"], 0)
        launch.assert_not_called()

    def test_invalid_utf8_plan_finalizes_created_run_as_failed(self):
        self.plan.write_bytes(b"Invalid UTF-8: \xff")
        with patch.object(runner, "execute", wraps=runner.execute) as launch:
            code, record, _, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["error"])
        self.assertGreaterEqual(record["elapsed_seconds"], 0)
        launch.assert_not_called()

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

    def test_completed_round_exits_0_when_stdout_cannot_encode_the_record(self):
        # Issue #31: a reply character outside the console code page turned a saved,
        # completed round into exit 1. U+2192 is outside cp1252 (an em dash is not).
        out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with patch.object(runner, "cli_prefix", return_value=[sys.executable, str(self.cli)]), \
             patch.dict(os.environ, {"FAKE_CASE": "arrow"}), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = runner.main(["review", "--host", "codex", "--repo", str(self.repo), "--plan",
                                str(self.plan), "--artifacts", str(self.artifacts)])
        record = json.loads(next(self.artifacts.glob("*/result.json")).read_text(encoding="utf-8"))
        self.assertEqual((code, record["status"]), (0, "completed"))

    def test_builders_load_no_user_hooks_or_mcp_tools(self):
        claude = runner.command("claude", "build", self.root)
        self.assertEqual(json.loads(claude[claude.index("--settings") + 1]), {"disableAllHooks": True})
        self.assertEqual(claude[claude.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertIn("--strict-mcp-config", claude)
        codex = runner.command("codex", "build", self.root)
        self.assertEqual(codex.count("--disable"), 2)
        self.assertIn("notify=[]", codex)

    def test_staged_rename_lists_old_path_as_deleted(self):
        self.git("mv", "delete.py", "moved.py")
        files = {f["path"]: f["kind"] for f in runner.snapshot(self.repo, self.base)["files"]}
        self.assertEqual((files.get("delete.py"), files.get("moved.py")), ("deleted", "file"))

    def test_git_subdirectory_is_refused_as_repo(self):
        # Manifest paths are repository-root relative; a subfolder would misread them.
        (self.repo / "sub").mkdir()
        code, _, _, error = self.invoke(mode="inspect", extra=("--base", self.base, "--repo", str(self.repo / "sub")))
        self.assertEqual(code, 1)
        self.assertIn("repository root", error)

    def test_codex_reviews_switch_off_plugins_apps_and_user_mcp_servers(self):
        # Issue #28: MCP and plugin tools act outside the read-only shell sandbox. The fork
        # ignores config.toml instead of naming servers: with the config ignored, Codex 0.171
        # rejects "-c mcp_servers.<name>.enabled=false" as a half-defined server.
        code, record, _, _ = self.invoke()  # Claude host: Codex reviews
        self.assertEqual(code, 0)
        argv = json.loads((Path(record["artifacts"]) / "command.json").read_text(encoding="utf-8"))
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv.count("--disable"), 2)
        self.assertIn("notify=[]", argv)
        self.assertFalse(any(a.startswith("mcp_servers.") for a in argv))
        self.assertNotIn("disabled_mcp_servers", record)

    def test_codex_windows_sandbox_mode_survives_ignored_config(self):
        # --ignore-user-config drops [windows] sandbox; without it Windows Codex blocked every read.
        home = self.root / "codex-home"
        home.mkdir()
        config = home / "config.toml"
        self.assertIsNone(runner.codex_windows_sandbox(home, windows=True))
        config.write_text('model = "x"\n[windows]\nsandbox = "unelevated"\n', encoding="utf-8")
        self.assertEqual(runner.codex_windows_sandbox(home, windows=True), "unelevated")
        self.assertIsNone(runner.codex_windows_sandbox(home, windows=False))
        for text in ('[windows]\nsandbox = "a\\" -c x="y"\n', "[windows\n", 'windows = "flat"\n'):
            config.write_text(text, encoding="utf-8")
            self.assertIsNone(runner.codex_windows_sandbox(home, windows=True), text)
        with patch.object(runner, "codex_windows_sandbox", return_value="unelevated"):
            self.assertIn('windows.sandbox="unelevated"', runner.command("codex", "review", self.root))
            self.assertNotIn('windows.sandbox="unelevated"', runner.command("codex", "build", self.root))
        with patch.object(runner, "codex_windows_sandbox", return_value=None):
            self.assertFalse(any(a.startswith("windows.") for a in runner.command("codex", "review", self.root)))

    def test_codex_records_effective_settings_from_its_rollout(self):
        # Issue #38: the effective model/effort/sandbox live in Codex's session rollout.
        home = self.root / "codex-home"
        day = home / "sessions" / "2026" / "10" / "14"
        day.mkdir(parents=True)
        context = {"type": "turn_context", "payload": {"model": "gpt-test", "effort": "low",
                                                       "sandbox_policy": {"type": "read-only"}}}
        (day / f"rollout-2026-10-14T00-00-00-{SESSION}.jsonl").write_text(
            "\n".join([json.dumps({"type": "session_meta"}), json.dumps(context), ""]), encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            code, record, _, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual((record["effective_model"], record["effective_effort"], record["effective_sandbox"]),
                         ("gpt-test", "low", "read-only"))
        self.assertEqual(record["observed_models"], [])

    def test_codex_run_without_rollout_still_completes(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "missing")}):
            code, record, _, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertNotIn("effective_model", record)

    def test_artifacts_cannot_contaminate_target_checkout(self):
        code, _, _, error = self.invoke(extra=("--artifacts", str(self.repo / "runs")))
        self.assertEqual(code, 1)
        self.assertIn("outside", error)


if __name__ == "__main__":
    unittest.main()
