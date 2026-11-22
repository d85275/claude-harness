"""CH-04: git stash with several worktrees, and core.hooksPath, against temporary repos.
Run: python3 -m unittest tests.test_git_state"""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "..", "hooks", "scripts", "guard.py")
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True, env=ENV, timeout=20)


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_git_state_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class GitStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_guard()
        cls.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(cls.tmp.name)
        cls.single = os.path.join(root, "single")
        cls.multi = os.path.join(root, "multi")
        cls.other = os.path.join(root, "other")  # a second worktree of multi
        cls.plain = os.path.join(root, "plain")  # not a repo
        for d in (cls.single, cls.multi, cls.plain):
            os.makedirs(d)
        for d in (cls.single, cls.multi):
            git(d, "init", "-q")
            git(d, "commit", "-q", "--allow-empty", "-m", "t")
        git(cls.multi, "worktree", "add", "-q", cls.other)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def decide(self, cmd, cwd):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.guard.guard_bash({"tool_input": {"command": cmd}, "cwd": cwd},
                                      self.guard.load_config(cwd))
        except SystemExit as e:
            return "block" if e.code == 2 else ("ask" if '"ask"' in out.getvalue() else "allow")
        return "allow"

    def test_stash_commands_ask_with_two_worktrees(self):
        for cmd in ["git stash", "git stash push", "git stash push -u", 'git stash push -m "msg"',
                    'git stash save "msg"', "git stash pop", "git stash apply", "git stash drop",
                    "git stash clear", "git -C . stash pop", "git add -A && git stash",
                    "make; git stash pop", "sh -c 'git stash'", "sh -c 'git stash pop'",
                    "git stash <<< x"]:
            for cwd in (self.multi, self.other):
                with self.subTest(cmd=cmd, cwd=os.path.basename(cwd)):
                    self.assertEqual(self.decide(cmd, cwd), "ask")

    def test_stash_in_data_heredoc_does_not_count(self):
        self.assertEqual(self.decide("cat > n.md <<'EOF'\ngit stash pop\nEOF", self.multi), "allow")

    def test_git_dash_c_counts_the_target_repo(self):
        self.assertEqual(self.decide(f"git -C {self.multi} stash pop", self.single), "ask")
        self.assertEqual(self.decide(f"git -C {self.single} stash pop", self.multi), "allow")

    def test_stash_reads_pass_with_any_worktree_count(self):
        for cmd in ["git stash list", "git stash show", "git stash show -p"]:
            for cwd in (self.single, self.multi, self.plain):
                with self.subTest(cmd=cmd, cwd=os.path.basename(cwd)):
                    self.assertEqual(self.decide(cmd, cwd), "allow")

    def test_stash_passes_with_one_worktree(self):
        for cmd in ["git stash", "git stash push -m wip", "git stash pop", "git stash clear"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.decide(cmd, self.single), "allow")

    def test_git_failure_asks_could_not_count(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(out):
            self.guard.guard_bash({"tool_input": {"command": "git stash"}, "cwd": self.plain},
                                  self.guard.load_config(self.plain))
        self.assertIn("could not count worktrees", out.getvalue())

    def test_hooks_path_asks_in_any_repo(self):
        for cwd in (self.single, self.multi):
            self.assertEqual(self.decide("git config core.hooksPath x", cwd), "ask")
            self.assertEqual(self.decide("git config --get core.hooksPath", cwd), "allow")


if __name__ == "__main__":
    unittest.main()
