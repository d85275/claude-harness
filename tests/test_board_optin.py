"""The plugin's ticket features act only in a project whose harness.json has a `tickets` section."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin", "dev-harness-board")

TRACKER = ("| ID | Title | State | Commit |\n|---|---|---|---|\n"
           "| [XY-1](XY-1.md) | First | Planned |  |\n| [XY-2](XY-2.md) | Second | Done | abc1234 |\n")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def snapshot(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


class OptIn(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = os.path.realpath(t.name)
        self.root = os.path.join(self.base, "proj")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_"))}
        self.env.update(HOME=self.base, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        os.makedirs(self.root)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, env=self.env, check=True)
        write(os.path.join(self.root, "docs/tickets/tracker.md"), TRACKER)
        for t_, s in (("XY-1", "Planned"), ("XY-2", "Done")):
            write(os.path.join(self.root, f"docs/tickets/{t_}.md"), f"# {t_}: Thing\n\n## Status\n\n{s}\n")
        write(os.path.join(self.root, "docs/tickets/board.html"), "<p>my own board</p>\n")

    def run_board(self, *args, cwd=None):
        p = subprocess.run([sys.executable, BIN, *args], cwd=cwd or self.root, env=self.env,
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def opt_in(self, text='{"tickets": {}}\n'):
        write(os.path.join(self.root, ".claude/harness.json"), text)

    def test_not_opted_in_refuses_and_changes_nothing(self):
        for cfg in (None, '{"version": 1, "pushPolicy": "deny"}\n'):
            if cfg:
                write(os.path.join(self.root, ".claude/harness.json"), cfg)
            before = snapshot(self.root)
            git_before = subprocess.run(["git", "config", "--local", "-l"], cwd=self.root, env=self.env,
                                        capture_output=True, text=True).stdout
            for args in (("set-state", "XY-1", "In progress"), ("archive", "XY-2"), ("install-hooks",),
                         ("", "--root", self.root)[1:], ()):
                code, out, err = self.run_board(*args)
                self.assertEqual(code, 1, (args, out, err))
                self.assertIn("has not switched to the plugin's ticket system; nothing changed", err)
            self.assertEqual(snapshot(self.root), before)
            git_after = subprocess.run(["git", "config", "--local", "-l"], cwd=self.root, env=self.env,
                                       capture_output=True, text=True).stdout
            self.assertEqual(git_after, git_before)
            self.assertFalse(os.path.exists(os.path.join(self.root, ".git", "dev-harness")))

    def test_hook_is_a_no_op(self):
        before = snapshot(self.root)
        code, out, err = self.run_board("hook", "post-commit")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(snapshot(self.root), before)

    def test_read_only_modes_and_out_still_work(self):
        before = snapshot(self.root)
        self.assertEqual(self.run_board("--check")[0], 0)
        code, out, _ = self.run_board("--json")
        self.assertEqual(code, 0)
        self.assertIn("XY-1", out)
        self.assertEqual(self.run_board("twin", "XY-1")[0] in (0, 1), True)
        self.assertEqual(snapshot(self.root), before)
        target = os.path.join(self.base, "mine.html")
        code, out, err = self.run_board("--out", target)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(snapshot(self.root), before)

    def test_status(self):
        code, out, _ = self.run_board("status")
        self.assertEqual((code, out.startswith("no:")), (1, True))
        self.opt_in()
        code, out, _ = self.run_board("status")
        self.assertEqual((code, out.startswith("yes:")), (0, True))

    def test_opted_in_everything_works(self):
        self.opt_in()
        self.assertEqual(self.run_board("set-state", "XY-1", "In progress")[0], 0)
        code, out, err = self.run_board()
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.root, "docs/tickets/board.html")) as f:
            self.assertNotEqual(f.read(), "<p>my own board</p>\n")
        self.assertEqual(self.run_board("--check")[0], 0)

    def test_tickets_section_in_a_parent_folder_config_counts(self):
        # the loader walks up to the repo root, so a config above a nested start folder counts
        self.opt_in()
        sub = os.path.join(self.root, "docs")
        code, out, _ = self.run_board("status", cwd=sub)
        self.assertEqual(code, 0, out)

    def test_non_git_and_subfolder_roots_are_gated(self):
        plain = os.path.join(self.base, "plain")
        write(os.path.join(plain, "docs/tickets/tracker.md"), TRACKER)
        write(os.path.join(plain, "docs/tickets/XY-1.md"), "# XY-1: T\n\n## Status\n\nPlanned\n")
        before = snapshot(plain)
        self.assertEqual(self.run_board("set-state", "XY-1", "In progress", "--root", plain)[0], 1)
        self.assertEqual(self.run_board("status", "--root", plain)[0], 1)
        self.assertEqual(snapshot(plain), before)
        sub = os.path.join(self.root, "docs")
        before = snapshot(self.root)
        self.assertEqual(self.run_board("set-state", "XY-1", "In progress", "--root", sub)[0], 1)
        self.assertEqual(snapshot(self.root), before)
        self.opt_in()      # a plain subfolder of an opted-in repo counts
        self.assertEqual(self.run_board("status", "--root", sub)[0], 0)
        # a config in a folder above the repo never opts the repo in
        outer = os.path.join(self.base, "outer")
        os.makedirs(outer)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=outer, env=self.env, check=True)
        write(os.path.join(self.base, ".claude/harness.json"), '{"tickets": {}}\n')
        self.assertEqual(self.run_board("status", cwd=outer)[0], 1)

    def commit_all(self):
        for a in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x")):
            subprocess.run(["git", *a], cwd=self.root, env=self.env, check=True, capture_output=True)

    def test_main_decides_for_a_linked_worktree(self):
        self.commit_all()
        wt = os.path.join(self.base, "wt-xy-1")
        subprocess.run(["git", "worktree", "add", "-q", "-b", "feature/xy-1", wt], cwd=self.root,
                       env=self.env, check=True, capture_output=True)
        self.assertEqual(self.run_board("status", "--root", wt)[0], 1)        # main not opted in
        write(os.path.join(wt, ".claude/harness.json"), '{"tickets": {}}\n')    # worktree alone: still no
        self.assertEqual(self.run_board("status", "--root", wt)[0], 1)
        self.assertEqual(self.run_board("set-state", "XY-1", "In progress", "--root", wt)[0], 1)
        os.remove(os.path.join(wt, ".claude/harness.json"))
        self.opt_in()                                                         # main opts in after the branch
        self.assertEqual(self.run_board("status", "--root", wt)[0], 0)
        code, out, err = self.run_board("set-state", "XY-1", "In progress", "--root", wt)
        self.assertEqual(code, 0, err)
        with open(os.path.join(wt, "docs/tickets/XY-1.md")) as f:
            self.assertIn("In progress", f.read())

    def test_init_works_unswitched_and_opts_in(self):
        fresh = os.path.join(self.base, "fresh")
        os.makedirs(fresh)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=fresh, env=self.env, check=True)
        self.assertEqual(self.run_board("status", cwd=fresh)[0], 1)
        code, out, err = self.run_board("init", "--prefix", "WEB", cwd=fresh)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_board("status", cwd=fresh)[0], 0)
        self.assertTrue(os.path.exists(os.path.join(fresh, "docs/tickets/board.html")))

    def test_this_repo_is_opted_in(self):
        # read the files directly: from a linked worktree the CLI works on the main checkout
        sys.path.insert(0, os.path.join(ROOT, "hooks", "scripts"))
        import harness_config
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "hooks", "scripts"))
        saved = os.environ.get("CLAUDE_PROJECT_DIR")
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PROJECT_DIR", None) if saved is None
                        else os.environ.__setitem__("CLAUDE_PROJECT_DIR", saved))
        os.environ["CLAUDE_PROJECT_DIR"] = ROOT
        self.assertTrue(harness_config.tickets_sources(ROOT))
        sample = os.path.join(ROOT, "examples", "sample-project")
        os.environ["CLAUDE_PROJECT_DIR"] = sample
        self.assertTrue(harness_config.tickets_sources(sample))
        self.assertEqual(self.run_board("status", "--root", sample)[0], 0)


if __name__ == "__main__":
    unittest.main()
