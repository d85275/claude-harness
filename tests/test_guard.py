"""End-to-end tests for hooks/scripts/guard.py, and the helpers the other guard test files share
(test_push_policy, test_config_write). Run: python3 -m unittest discover -s tests"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

GUARD = os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts", "guard.py")


def run(mode, tool_input, cwd, env=None):
    payload = json.dumps({"tool_input": tool_input, "cwd": cwd})
    proc = subprocess.run([sys.executable, "-I", GUARD, mode], input=payload,
                          capture_output=True, text=True, timeout=20,
                          env={**{k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"},
                               **(env or {})})
    if proc.returncode == 2:
        return "block"
    if proc.returncode == 0 and '"ask"' in proc.stdout:
        return "ask"
    if proc.returncode == 0:
        return "allow"
    raise AssertionError(f"unexpected exit {proc.returncode}: {proc.stderr}")


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        os.makedirs(os.path.join(self.cwd, ".claude"))

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **cfg):
        with open(os.path.join(self.cwd, ".claude", "harness.json"), "w") as f:
            json.dump(cfg, f)

    def bash(self, command):
        return run("bash", {"command": command}, self.cwd)


class BashTests(GuardTestCase):
    def test_credential_reads_are_blocked(self):
        for cmd in ["cat keys/AuthKey_ABC.p8", "cp release.keystore /tmp/",
                    "grep token backend/service-account.json", "head .env"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "block")

    def test_listing_credential_paths_is_allowed(self):
        self.assertEqual(self.bash("ls keys/"), "allow")
        self.assertEqual(self.bash("cat .env.example"), "allow")

    def test_project_protected_paths(self):
        self.config(protectedPaths=["config/secret-*.json"])
        self.assertEqual(self.bash("cat config/secret-prod.json"), "block")

    def test_outward_facing_commands_ask(self):
        for cmd in ["git push origin HEAD", "git -C repo push", "gh pr create --fill",
                    "firebase deploy --only functions", "terraform apply"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "ask")

    def test_routine_commands_are_allowed(self):
        for cmd in ["git status --short", "git log --oneline -3", "gh pr view 12",
                    "firebase emulators:start --project demo-x", "xcodebuild -list"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "allow")

    def test_project_ask_commands(self):
        self.config(askCommands=["./scripts/deploy.sh"])
        self.assertEqual(self.bash("./scripts/deploy.sh prod"), "ask")


GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True, env=GIT_ENV, timeout=20)


def commit_all(cwd):
    """Make cwd a git repository with everything in it committed (local only, no remote)."""
    git(cwd, "init", "-q")
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "--allow-empty", "-m", "test")


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class CostTests(GuardTestCase):
    """A hook that times out does not block the call, so the check must stay fast, and past
    its work budget it must ask (never allow, never block)."""

    def setUp(self):
        super().setUp()
        self.guard = load_guard()

    def inproc(self, cmd, policy="ask"):
        self.config(pushPolicy=policy)
        g = self.guard
        cfg = g.load_config(self.cwd)
        out = io.StringIO()
        start = time.process_time()  # CPU time: stable under load, unlike wall time
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                g.guard_bash({"tool_input": {"command": cmd}, "cwd": self.cwd}, cfg)
            result = "allow"
        except SystemExit as e:
            result = "block" if e.code == 2 else ("ask" if '"ask"' in out.getvalue() else "allow")
        return result, time.process_time() - start, out.getvalue()

    def assertFast(self, cmd, limit=1.0, allowed=("allow", "ask"), policy="ask"):
        result, seconds, _ = self.inproc(cmd, policy)
        self.assertIn(result, allowed)
        self.assertLess(seconds, limit, f"{seconds:.2f}s for {cmd[:60]!r}")
        return result

    def test_many_eval_words_are_cheap(self):
        body = "\n".join(f"- eval run {i}: eval score improved" for i in range(50))
        for cmd in ["git commit -F - <<'EOF'\nfeat: eval\n\n" + body + "\nEOF",
                    "eval " * 100 + "true", "eval 'eval \"eval x\"' " * 30,
                    "cat > docs/eval.md <<'EOF'\n" + body + "\nEOF\nfirebase deploy"]:
            with self.subTest(cmd=cmd[:40]):
                self.assertFast(cmd)

    def test_large_inputs_finish_or_ask(self):
        msg = "\n".join(f"- rm stale entry {i} from data/file_{i}.txt and push" for i in range(2000))
        code = "\n".join(f"x{i} = open('data/file_{i}.txt').read()  # it's line {i}"
                         for i in range(2000))
        cases = {
            "100KB data heredoc": "git commit -F - <<'EOF'\n" + msg + "\nEOF",
            "100KB python3 - heredoc": "python3 - <<'EOF'\n" + code + "\nEOF",
            "rm -f 5000 paths": "rm -f " + " ".join(f"build/obj{i}/file{i}.o" for i in range(5000)),
            "6000 words": "echo " + " ".join(f"dir{i}/file{i}.txt" for i in range(6000)),
            "2000 KEY=VAL heredoc": "cat > config/app.env <<'EOF'\n"
                                    + "\n".join(f"K_{i}=v_{i}" for i in range(2000)) + "\nEOF",
            "1000 heredocs": "\n".join(f"cat > /tmp/f{i} <<E\nx\nE" for i in range(1000)),
        }
        for name, cmd in cases.items():
            with self.subTest(name=name):
                self.assertGreater(len(cmd), 10000)
                self.assertFast(cmd)

    def test_pathological_regex_inputs(self):
        for cmd in ["git " + "-a " * 40 + "status", "true; git " + "-a " * 40 + "status",
                    "gh " + "-a " * 40 + "status", "git " + "-a " * 40 + "log && git push",
                    "env " + "A=' " * 12000, "sed " * 12000, "gcloud " * 7000,
                    "(" * 5000 + "git push" + ")" * 5000, "$(" * 3000 + "rm x" + ")" * 3000,
                    "<<" * 5000, "$'" * 5000, "#" * 50000, "\\\n" * 20000,
                    "git -a " * 9000, "gh -a " * 10000, "$((" * 5000, "x=$((1<<2)) " * 5000]:
            with self.subTest(cmd=cmd[:30]):
                self.assertFast(cmd, allowed=("allow", "ask", "block"), policy="deny")

    def test_very_long_word_asks_quickly(self):
        # shlex is quadratic in one word's length, so a word over the limit asks instead.
        for policy, cmd in [("deny", "git -C " + "x" * 40000 + " push"),
                            ("ask", 'git commit -m "' + "a " * 20000 + '"'),
                            ("ask", "echo '" + "b" * 40000)]:
            with self.subTest(cmd=cmd[:30]):
                result, seconds, out = self.inproc(cmd, policy)
                self.assertEqual(result, "ask")
                self.assertIn("too large to check", out)
                self.assertLess(seconds, 1.0)

    def test_over_budget_asks_never_blocks_or_allows(self):
        scan = sys.modules["shell_scan"]  # shared by every loaded guard: restore the budget after
        self.addCleanup(setattr, scan, "CHECK_STEPS", scan.CHECK_STEPS)
        scan.CHECK_STEPS = 0
        for policy, cmd in [("deny", "git push"), ("ask", "rm .claude/harness.json"),
                            ("ask", "ls " + " ".join(f"f{i}" for i in range(50)))]:
            with self.subTest(cmd=cmd[:30]):
                result, _, out = self.inproc(cmd, policy)
                self.assertEqual(result, "ask")
                self.assertIn("too large to check", out)


class ReadTests(GuardTestCase):
    def test_reading_credentials_is_blocked(self):
        self.assertEqual(run("read", {"file_path": "/x/AuthKey_1.p8"}, self.cwd), "block")

    def test_reading_source_is_allowed(self):
        self.assertEqual(run("read", {"file_path": "src/main.swift"}, self.cwd), "allow")


class EditTests(GuardTestCase):
    def edit(self, path, old, new):
        return run("edit", {"file_path": path, "old_string": old, "new_string": new}, self.cwd)

    def test_protected_configs_ask(self):
        for path in [".swiftlint.yml", "app/eslint.config.js", "ios/Smoke.xctestplan",
                     ".claude/settings.json"]:
            with self.subTest(path=path):
                self.assertEqual(self.edit(path, "a", "b"), "ask")

    def test_adding_a_skip_or_suppression_asks(self):
        cases = [
            ("Tests/A.swift", "func testA() {", "func testA() throws {\n throw XCTSkip(\"later\")"),
            ("tests/test_a.py", "def test_a():", "@unittest.skip('flaky')\ndef test_a():"),
            ("web/a.test.js", "it('works', () => {", "it.skip('works', () => {"),
            ("src/a.ts", "const x = y", "// @ts-ignore\nconst x = y"),
            ("app/A.kt", "fun a() {", "@Suppress(\"UNCHECKED_CAST\")\nfun a() {"),
            ("UITests/Audit.swift", "try app.performAccessibilityAudit()",
             "try app.performAccessibilityAudit { issue in issue.auditType == .textClipped }"),
        ]
        for path, old, new in cases:
            with self.subTest(path=path):
                self.assertEqual(self.edit(path, old, new), "ask")

    def test_moving_or_removing_an_existing_skip_is_allowed(self):
        self.assertEqual(self.edit("tests/test_a.py", "@unittest.skip('x')\ndef test_a():",
                                   "def test_a():"), "allow")
        self.assertEqual(self.edit("tests/test_a.py", "@unittest.skip('x')  # old",
                                   "@unittest.skip('x')  # reason: needs device"), "allow")

    def test_plain_code_edits_are_allowed(self):
        self.assertEqual(self.edit("src/a.swift", "let a = 1", "let a = 2"), "allow")

    def test_write_compares_against_existing_file(self):
        path = os.path.join(self.cwd, "test_b.py")
        with open(path, "w") as f:
            f.write("@unittest.skip('x')\ndef test_b(): pass\n")
        same = run("edit", {"file_path": "test_b.py",
                            "content": "@unittest.skip('x')\ndef test_b(): assert True\n"}, self.cwd)
        more = run("edit", {"file_path": "test_b.py",
                            "content": "@unittest.skip('x')\n@unittest.skip('y')\n"}, self.cwd)
        self.assertEqual((same, more), ("allow", "ask"))

    def test_multiedit_is_checked(self):
        result = run("edit", {"file_path": "a.py", "edits": [
            {"old_string": "x = 1", "new_string": "x = 1  # noqa"}]}, self.cwd)
        self.assertEqual(result, "ask")

    def test_project_markers_and_opt_out(self):
        self.config(weakeningMarkers=[r"\bExclusion\("])
        self.assertEqual(self.edit("Audit.swift", "]", "Exclusion(type: .contrast)]"), "ask")
        self.config(checkWeakening=False)
        self.assertEqual(self.edit("a.py", "x", "x  # noqa"), "allow")


if __name__ == "__main__":
    unittest.main()
