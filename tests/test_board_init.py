"""Tests for `dev-harness-board init` (board/init.py). Temp repos only; HOME and the global git
config are isolated. Made-up project, no real tickets."""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "bin", "dev-harness-board")


def snapshot(folder):
    out = {}
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x != ".git"]
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


class InitTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = os.path.realpath(t.name)
        self.repo = os.path.join(self.base, "proj")
        os.makedirs(self.repo)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_"))}
        self.env.update({"HOME": self.base, "GIT_CONFIG_GLOBAL": os.path.join(self.base, "gc"),
                         "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, env=self.env, check=True)

    def run_bin(self, *args):
        p = subprocess.run([BIN, *args], cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def init(self, *extra):
        return self.run_bin("init", "--prefix", "WEB", *extra)

    def path(self, rel):
        return os.path.join(self.repo, rel)

    def read(self, rel):
        with open(self.path(rel), encoding="utf-8") as f:
            return f.read()

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8") as f:
            f.write(text)

    def test_fresh_repo_gets_everything_and_check_passes(self):
        code, out, err = self.init("--branch", "trunk", "--archive", "--twin-platforms", "ios,android")
        self.assertEqual(code, 0, (out, err))
        for rel in ("docs/tickets/TEMPLATE.md", "docs/tickets/tracker.md", "docs/STATUS.md",
                    ".claude/harness.json", "CLAUDE.md", "docs/tickets/board.html"):
            self.assertTrue(os.path.exists(self.path(rel)), rel)
        cfg = json.loads(self.read(".claude/harness.json"))
        t = cfg["tickets"]
        self.assertEqual(t["integrationBranch"], "trunk")
        self.assertTrue(t["archive"])
        self.assertEqual(t["twinPlatforms"], ["ios", "android"])
        self.assertEqual(t["idPrefixes"], {"WEB": {"platform": "app", "kind": "feature"}})
        md = self.read("CLAUDE.md")
        self.assertIn("One commit per ticket, cherry-picked onto `trunk`.", md)
        self.assertIn("from `trunk`", md)
        self.assertIn("WEB-12", md)
        self.assertIn("Changing the ticket flow", md)
        self.assertNotIn("{{", md + self.read("docs/tickets/TEMPLATE.md") + self.read("docs/STATUS.md"))
        code, out, err = self.run_bin("--check")
        self.assertEqual((code, out), (0, ""), err)

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.init()[0], 0)
        before = snapshot(self.repo)
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertIn("nothing to change", out)
        self.assertEqual(snapshot(self.repo), before)

    def test_existing_harness_json_keeps_other_keys(self):
        self.write(".claude/harness.json", '{"version": 1, "pushPolicy": "deny", "protectedPaths": ["keys/**"],\n'
                   ' "tickets": {"boardPath": "out/b.html", "integrationBranch": "dev"}}')
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        cfg = json.loads(self.read(".claude/harness.json"))
        self.assertEqual(cfg["pushPolicy"], "deny")
        self.assertEqual(cfg["protectedPaths"], ["keys/**"])
        self.assertEqual(cfg["tickets"]["boardPath"], "out/b.html")
        self.assertEqual(cfg["tickets"]["integrationBranch"], "dev")
        self.assertIn("idPrefixes", cfg["tickets"])
        self.assertTrue(os.path.exists(self.path("out/b.html")))

    def test_broken_harness_json_is_left_alone(self):
        self.write(".claude/harness.json", "{not json")
        code, out, err = self.init()
        self.assertEqual(code, 2, (out, err))      # unreadable config stops before any write
        self.assertEqual(self.read(".claude/harness.json"), "{not json")
        self.assertFalse(os.path.exists(self.path("docs")))

    def test_existing_claude_md_section_untouched(self):
        text = "# Proj\n\n## Harness\n\n- **Verify**: make test\n"
        self.write("CLAUDE.md", text)
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("CLAUDE.md"), text)
        self.assertIn("Changing the ticket flow", out)
        self.assertIn("not edited", out)

    def test_claude_md_without_section_is_appended(self):
        self.write("CLAUDE.md", "# Proj\n\nRules.\n")
        self.assertEqual(self.init()[0], 0)
        md = self.read("CLAUDE.md")
        self.assertTrue(md.startswith("# Proj\n\nRules.\n\n## Harness"))
        before = snapshot(self.repo)
        self.assertEqual(self.init()[0], 0)
        self.assertEqual(snapshot(self.repo), before)

    def test_dry_run_writes_nothing(self):
        before = snapshot(self.repo)
        code, out, err = self.init("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.repo), before)
        self.assertEqual(os.listdir(self.repo), [".git"])
        for rel in ("docs/tickets/tracker.md", ".claude/harness.json", "CLAUDE.md", "docs/STATUS.md"):
            self.assertIn("would create " + rel, out)

    def test_no_hooks_installed(self):
        self.assertEqual(self.init()[0], 0)
        p = subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=self.repo,
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "dev-harness")))

    def test_existing_tracker_goes_to_adopt_mode(self):
        self.write("docs/tickets/tracker.md", "| ID | State |\n|---|---|\n| X-1 | Planned |\n")
        code, out, err = self.run_bin("init", "--mode")
        self.assertEqual((code, out.splitlines()[0]), (0, "mode: adopt"), err)
        before = snapshot(self.repo)
        code, out, err = self.init("--check")
        self.assertEqual(snapshot(self.repo), before)      # the dry run writes nothing
        self.assertIn("adopt mode", out)
        self.assertNotIn("would create docs/STATUS.md", out)

    def test_existing_ticket_folder_without_tracker_is_refused(self):
        self.write("docs/tickets/X-1.md", "# X-1: t\n")
        before = snapshot(self.repo)
        code, out, err = self.init()
        self.assertEqual(code, 1)
        self.assertIn("no tracker found", out)
        self.assertEqual(snapshot(self.repo), before)

    def test_symlinked_targets_are_left_alone(self):
        self.write("AGENTS.md", "# Agents\n")
        self.write("shared.json", '{"version": 1}')
        os.symlink("AGENTS.md", self.path("CLAUDE.md"))
        os.makedirs(self.path(".claude"))
        os.symlink("../shared.json", self.path(".claude/harness.json"))
        code, out, err = self.init("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertNotIn("would merge", out)
        self.assertNotIn("would append", out)
        self.assertIn("CLAUDE.md is a symlink", out)
        self.assertIn(".claude/harness.json is a symlink", out)
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.islink(self.path("CLAUDE.md")))
        self.assertTrue(os.path.islink(self.path(".claude/harness.json")))
        self.assertEqual(self.read("AGENTS.md"), "# Agents\n")
        self.assertEqual(self.read("shared.json"), '{"version": 1}')
        self.assertIn("## Harness", out)

    def test_crlf_claude_md_with_section_is_not_doubled(self):
        text = "# Proj\r\n\r\n## Harness\r\n\r\n- **Verify**: make test\r\n"
        self.write("CLAUDE.md", text)
        with open(self.path("CLAUDE.md"), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        self.assertEqual(self.init()[0], 0)
        with open(self.path("CLAUDE.md"), encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), text)

    def test_crlf_claude_md_without_section_gets_crlf_append(self):
        with open(self.path("CLAUDE.md"), "w", encoding="utf-8", newline="") as f:
            f.write("# Proj\r\n\r\nRules.\r\n")
        self.assertEqual(self.init()[0], 0)
        with open(self.path("CLAUDE.md"), encoding="utf-8", newline="") as f:
            md = f.read()
        self.assertTrue(md.startswith("# Proj\r\n\r\nRules.\r\n\r\n## Harness\r\n"))
        self.assertNotIn("\n", md.replace("\r\n", ""))
        before = snapshot(self.repo)
        self.assertEqual(self.init()[0], 0)
        self.assertEqual(snapshot(self.repo), before)

    def test_read_only_targets_are_left_alone(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.write("CLAUDE.md", "# Proj\n")
        os.chmod(self.path("CLAUDE.md"), 0o444)
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("CLAUDE.md"), "# Proj\n")
        self.assertIn("not edited", out)

    def test_kept_existing_values_are_reported(self):
        self.write(".claude/harness.json", '{"tickets": {"archive": false, "twinPlatforms": null, "integrationBranch": "dev"}}')
        code, out, err = self.init("--archive", "--twin-platforms", "a,b", "--branch", "x")
        self.assertEqual(code, 0, err)
        for k in ("archive", "twinPlatforms", "integrationBranch"):
            self.assertIn("kept existing tickets." + k, out)

    def test_nan_in_harness_json_is_not_merged(self):
        self.write(".claude/harness.json", '{"x": NaN}')
        before = self.read(".claude/harness.json")
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read(".claude/harness.json"), before)
        self.assertIn("by hand", out)

    def test_partial_failure_reports_and_rerun_completes(self):
        self.write(".claude", "i am a file")
        code, out, err = self.init()
        self.assertEqual(code, 1)
        self.assertIn("partially done; written: docs/tickets/tracker.md", err)
        self.assertFalse(os.path.exists(self.path("docs/tickets/TEMPLATE.md")))
        os.unlink(self.path(".claude"))
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(self.path("docs/tickets/TEMPLATE.md")))
        self.assertTrue(os.path.exists(self.path(".claude/harness.json")))

    def test_usage_errors(self):
        self.assertEqual(self.run_bin("init")[0], 2)
        self.assertEqual(self.run_bin("init", "--prefix", "9x")[0], 2)
        self.assertEqual(os.listdir(self.repo), [".git"])


if __name__ == "__main__":
    unittest.main()
