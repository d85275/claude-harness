"""Tests for hooks/scripts/guard.py. Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
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
