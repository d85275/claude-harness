"""Tests for bin/dev-harness-config and the review command text."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "bin", "dev-harness-config")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.path.realpath(self.tmp.name)
        os.makedirs(os.path.join(self.cwd, ".git"))
        os.makedirs(os.path.join(self.cwd, ".claude"))

    def write(self, text):
        with open(os.path.join(self.cwd, ".claude", "harness.json"), "w") as f:
            f.write(text)

    def run_cli(self, *args, exe=BIN):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR")}
        return subprocess.run([exe, *args], cwd=self.cwd, env=env, capture_output=True, text=True)

    def test_executable(self):
        self.assertTrue(os.access(BIN, os.X_OK))

    def test_default(self):
        r = self.run_cli("get", "highRiskReviewerModel")
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, "opus", ""))

    def test_configured_fable(self):
        self.write('{"highRiskReviewerModel": "fable"}')
        r = self.run_cli("get", "highRiskReviewerModel")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "fable"))

    def test_malformed_prints_default_and_warns(self):
        self.write("{not json")
        r = self.run_cli("get", "highRiskReviewerModel")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "opus"))
        self.assertIn("harness.json", r.stderr)

    def test_unknown_key_and_bad_usage(self):
        self.assertEqual(self.run_cli("get", "nope").returncode, 2)
        self.assertEqual(self.run_cli().returncode, 2)
        self.assertEqual(self.run_cli("set", "x").returncode, 2)

    def test_list_and_object_as_json(self):
        self.write('{"askCommands": ["foo"]}')
        self.assertEqual(self.run_cli("get", "askCommands").stdout.strip(), '["foo"]')
        self.assertIn('"dirs"', self.run_cli("get", "tickets").stdout)

    def test_through_symlink(self):
        link = os.path.join(self.cwd, "link-to-config")
        os.symlink(BIN, link)
        r = self.run_cli("get", "highRiskReviewerModel", exe=link)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "opus"))


class ReviewCommandTextTest(unittest.TestCase):
    def test_review_uses_bin_not_env(self):
        with open(os.path.join(ROOT, "commands", "review.md"), encoding="utf-8") as f:
            t = f.read()
        self.assertIn("dev-harness-config", t)
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", t)
        self.assertNotIn("python3 -I -c", t)


if __name__ == "__main__":
    unittest.main()
