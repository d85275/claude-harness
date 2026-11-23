"""CH-05: a `git commit` that adds generated or ignored-looking files asks, against temporary repos.
Run: python3 -m unittest tests.test_commit_files"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "..", "hooks", "scripts", "guard.py")
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True, env=ENV, timeout=20)


def write(root, rel, text="x\n"):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_commit_files_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class CommitFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_guard()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def repo(self, name, stage=(), config=None):
        """A repo with one commit; each name in stage is created and added (-f)."""
        d = os.path.join(self.root, name)
        os.makedirs(d)
        git(d, "init", "-q")
        write(d, ".gitignore", "secret.txt\n*.log\n")
        write(d, "README.md")
        write(d, "old.txt")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "t")
        if config is not None:
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".claude", "harness.json"), "w") as f:
                json.dump(config, f)
        for rel in stage:
            write(d, rel)
            git(d, "add", "-f", rel)
        return d

    def run_guard(self, cmd, cwd):
        g = self.guard
        out = io.StringIO()
        decision = "allow"
        g.CONFIG_WARNING = ""
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                g.guard_bash({"tool_input": {"command": cmd}, "cwd": cwd}, g.load_config(cwd))
                g.emit()  # what run() does for an allowed call
        except SystemExit as e:
            if e.code == 2:
                decision = "block"
            elif '"ask"' in out.getvalue():
                decision = "ask"
        text = out.getvalue()
        return decision, (json.loads(text) if text.strip() else {})

    def decide(self, cmd, cwd):
        return self.run_guard(cmd, cwd)[0]

    def test_generated_folders_and_suffixes_ask(self):
        paths = ["node_modules/a/index.js", "build/out.txt", "dist/app.js", ".gradle/x.bin",
                 "DerivedData/y.txt", "src/__pycache__/m.txt", ".worktrees/w/f.txt", "m.pyc",
                 "src/A.class", "obj/a.o", "secret.txt", "app.log"]
        for i, rel in enumerate(paths):
            with self.subTest(path=rel):
                d = self.repo(f"gen{i}", [rel])
                self.assertEqual(self.decide("git commit -m x", d), "ask")

    def test_commit_forms_ask(self):
        d = self.repo("forms", ["build/out.txt"])
        for cmd in ["git commit", 'git commit -m "msg"', 'git commit -am "msg"', "git commit --all",
                    f"git -C {d} commit -m x", "make && git commit -m x", "git commit -m x; ls",
                    "sh -c 'git commit -m x'"]:
            with self.subTest(cmd=cmd):
                cwd = self.root if cmd.startswith("git -C") else d
                self.assertEqual(self.decide(cmd, cwd), "ask")

    def test_git_dash_c_checks_that_repo_not_cwd(self):
        dirty = self.repo("dirty", ["dist/a.js"])
        clean = self.repo("clean")
        self.assertEqual(self.decide(f"git -C {dirty} commit -m x", clean), "ask")
        self.assertEqual(self.decide(f"git -C {clean} commit -m x", dirty), "allow")

    def test_subfolder_cwd_uses_repo_root(self):
        d = self.repo("sub", ["secret.txt"])
        os.makedirs(os.path.join(d, "inner"))
        self.assertEqual(self.decide("git commit -m x", os.path.join(d, "inner")), "ask")

    def test_configured_board_output_asks(self):
        d = self.repo("board", ["docs/tickets/board.html"])
        self.assertEqual(self.decide("git commit -m x", d), "ask")
        d = self.repo("board2", ["site/b.html", "docs/tickets/board.html"],
                      {"tickets": {"boardPath": "site/b.html"}})
        decision, out = self.run_guard("git commit -m x", d)
        self.assertEqual(decision, "ask")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("site/b.html", reason)
        self.assertNotIn("docs/tickets/board.html", reason)

    def test_prompt_lists_at_most_ten(self):
        d = self.repo("many", [f"build/f{i}.txt" for i in range(12)])
        _, out = self.run_guard("git commit -m x", d)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("12 generated", reason)
        self.assertEqual(reason.count("build/f"), 10)
        self.assertIn("and 2 more", reason)

    def test_ordinary_files_pass(self):
        d = self.repo("ok", ["src/main.py", "docs/guide.md", "lib/builder.py"])
        for cmd in ["git commit -m x", "git commit -am x", "git commit"]:
            self.assertEqual(self.run_guard(cmd, d), ("allow", {}))

    def test_modify_and_delete_only_pass(self):
        d = self.repo("moddel")
        write(d, "README.md", "changed\n")
        git(d, "rm", "-q", "old.txt")
        git(d, "add", "README.md")
        self.assertEqual(self.run_guard("git commit -m x", d), ("allow", {}))

    def test_modifying_a_committed_ignored_looking_file_passes(self):
        d = self.repo("tracked", [])
        write(d, "build/keep.txt")
        git(d, "add", "-f", "build/keep.txt")
        git(d, "commit", "-q", "-m", "x")
        write(d, "build/keep.txt", "changed\n")
        git(d, "add", "-f", "build/keep.txt")
        self.assertEqual(self.decide("git commit -m x", d), "allow")

    def test_amend_no_edit_with_nothing_new_passes(self):
        d = self.repo("amend", [])
        write(d, "build/kept.txt")
        git(d, "add", "-f", "build/kept.txt")
        git(d, "commit", "-q", "-m", "x")
        self.assertEqual(self.run_guard("git commit --amend --no-edit", d), ("allow", {}))

    def test_non_commit_git_does_not_run_git_checks(self):
        d = self.repo("noncommit", ["build/out.txt"])
        with mock.patch.object(self.guard.bash_rules, "_git_bytes",
                               side_effect=AssertionError("no git call expected")):
            for cmd in ["git status", "git add -A", "git commit-tree HEAD^{tree}", "echo git commit"]:
                self.assertEqual(self.decide(cmd, d), "allow", cmd)

    def test_git_failure_allows_with_warning(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        decision, out = self.run_guard("git commit -m x", plain)
        self.assertEqual(decision, "allow")
        self.assertIn("could not check staged files:", out["systemMessage"])
        self.assertIn("could not check staged files:",
                      out["hookSpecificOutput"]["additionalContext"])

    def test_git_deadline_allows_with_warning(self):
        d = self.repo("slow", ["build/out.txt"])
        with mock.patch.object(self.guard.bash_rules.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("git", 3)):
            decision, out = self.run_guard("git commit -m x", d)
        self.assertEqual(decision, "allow")
        self.assertIn("could not check staged files: git took longer than 3 s",
                      out["systemMessage"])

    def test_one_deadline_is_shared(self):
        d = self.repo("shared", ["secret.txt"])
        clock = [0.0]
        real_run = subprocess.run

        def slow_run(*args, **kwargs):  # every git call takes 2 s of a fake clock
            clock[0] += 2
            return real_run(*args, **kwargs)

        fake_time = mock.Mock(monotonic=lambda: clock[0])
        with mock.patch.object(self.guard.bash_rules, "time", fake_time), \
                mock.patch.object(self.guard.bash_rules.subprocess, "run", slow_run):
            decision, out = self.run_guard("git commit -m x", d)
        self.assertEqual(decision, "allow")
        self.assertIn("could not check staged files", out["systemMessage"])

    def test_warning_does_not_leak_between_calls(self):
        plain = os.path.join(self.root, "plain2")
        os.makedirs(plain)
        self.run_guard("git commit -m x", plain)
        d = self.repo("leak")
        self.assertEqual(self.run_guard("git commit -m x", d), ("allow", {}))

    def notices(self, out):
        return out.get("systemMessage", "")

    def failing_check_ignore(self):
        real = self.guard.bash_rules._git_bytes

        def fake(folder, args, deadline, data=None):
            if "check-ignore" in args:
                return subprocess.CompletedProcess(args, 128, b"", b"fatal: boom\n")
            return real(folder, args, deadline, data)
        return mock.patch.object(self.guard.bash_rules, "_git_bytes", fake)

    def test_path_hits_survive_a_later_git_failure(self):
        d = self.repo("partial", ["node_modules/x.js", "ok.txt"])
        with self.failing_check_ignore():
            decision, out = self.run_guard("git commit -m x", d)
        self.assertEqual(decision, "ask")
        self.assertIn("node_modules/x.js", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("could not check staged files: fatal: boom", self.notices(out))

    def test_failing_folder_does_not_stop_the_others(self):
        plain = os.path.join(self.root, "plain3")
        os.makedirs(plain)
        dirty = self.repo("dirty2", ["dist/a.js"])
        decision, out = self.run_guard(f"git -C {plain} commit -m x; git -C {dirty} commit -m y",
                                       self.root)
        self.assertEqual(decision, "ask")
        self.assertIn("could not check staged files", self.notices(out))

    def test_rename_into_generated_folder_asks(self):
        d = self.repo("renamed")
        os.makedirs(os.path.join(d, "dist"))
        git(d, "mv", "old.txt", "dist/old.txt")
        self.assertEqual(self.decide("git commit -m x", d), "ask")
        e = self.repo("copied")
        git(e, "config", "diff.renames", "copies")
        os.makedirs(os.path.join(e, "build"))
        write(e, "build/old.txt")  # same content as old.txt
        git(e, "add", "-f", "build/old.txt")
        self.assertEqual(self.decide("git commit -m x", e), "ask")

    def test_commit_after_cd_is_not_checked_but_says_so(self):
        d = self.repo("cdrepo", ["dist/a.js"])
        decision, out = self.run_guard(f"cd {d} && git commit -m x", self.root)
        self.assertEqual(decision, "allow")
        self.assertIn("commit after cd: staged files not checked", self.notices(out))
        self.assertEqual(self.decide(f"cd {self.root} && git -C {d} commit -m x", self.root), "ask")

    def test_git_dir_and_work_tree_say_not_checked(self):
        d = self.repo("gitdir", ["dist/a.js"])
        for cmd in [f"git --git-dir={d}/.git commit -m x", f"git --work-tree={d} commit -m x",
                    f"GIT_DIR={d}/.git git commit -m x"]:
            with self.subTest(cmd=cmd):
                decision, out = self.run_guard(cmd, d)
                self.assertEqual(decision, "allow")
                self.assertIn("staged files not checked", self.notices(out))

    def test_board_in_a_monorepo_project_folder(self):
        d = self.repo("mono", [], None)
        proj = os.path.join(d, "proj")
        write(proj, ".claude/harness.json", "{}")
        write(proj, "docs/tickets/board.html")
        git(d, "add", "-f", "proj/docs/tickets/board.html")
        self.assertEqual(self.decide("git commit -m x", proj), "ask")
        other = self.repo("mono2", [])
        write(other, "docs/tickets/board.html")
        git(other, "add", "-f", "docs/tickets/board.html")
        self.assertEqual(self.decide("git commit -m x", other), "ask")  # no project root: repo root

    def test_blank_board_path_is_ignored_with_notice(self):
        for value in ("  ", "."):
            d = self.repo(f"blank{len(value)}", ["docs/tickets/board.html"],
                          {"tickets": {"boardPath": value}})
            decision, out = self.run_guard("git commit -m x", d)
            self.assertEqual(decision, "allow")
            self.assertIn("tickets.boardPath is empty", self.notices(out))

    def test_more_than_four_folders_checks_four_and_notes_the_rest(self):
        dirty = self.repo("cap0", ["dist/a.js"])
        others = [self.repo(f"cap{i}") for i in range(1, 5)]
        cmd = "; ".join(f"git -C {x} commit -m x" for x in [dirty] + others)
        decision, out = self.run_guard(cmd, self.root)
        self.assertEqual(decision, "ask")
        self.assertIn("1 folders not checked", self.notices(out))


if __name__ == "__main__":
    unittest.main()
