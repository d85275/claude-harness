"""CH-22: the guard asks before dev-harness-board install-hooks / uninstall-hooks and before
changes to the board's hooks folder (<git dir>/dev-harness/hooks). The corpus holds the Bash rows;
this file holds the Edit/Write/Read side, CRLF, the policies and the mutation check.
Run: python3 -m unittest tests.test_board_hooks_guard"""
import os
import sys
import unittest
from unittest import mock

from tests.test_guard import GuardTestCase, run
from tests.test_guard_corpus import load_corpus

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "scripts"))
import bash_rules  # noqa: E402

HOOK_FILES = [".git/dev-harness/hooks/post-commit", "sub/clone.git/dev-harness/hooks/pre-push",
              "../main/.git/dev-harness/hooks/post-merge"]


class BoardHooksGuardTests(GuardTestCase):
    def folder(self, rel):
        return os.path.join(self.cwd, rel)

    def test_file_tools_ask_in_the_hooks_folder(self):
        for rel in HOOK_FILES:
            path = self.folder(rel)
            with self.subTest(rel=rel):
                self.assertEqual(run("edit", {"file_path": path, "content": "#!/bin/sh\n"},
                                     self.cwd), "ask")
                self.assertEqual(run("edit", {"file_path": path, "old_string": "a",
                                              "new_string": "b"}, self.cwd), "ask")
                self.assertEqual(run("edit", {"file_path": path, "edits": [
                    {"old_string": "a", "new_string": "b"}]}, self.cwd), "ask")

    def test_relative_path_asks_too(self):
        self.assertEqual(run("edit", {"file_path": ".git/dev-harness/hooks/post-commit",
                                      "content": "x"}, self.cwd), "ask")

    def test_reading_and_other_files_pass(self):
        path = self.folder(".git/dev-harness/hooks/post-commit")
        self.assertEqual(run("read", {"file_path": path}, self.cwd), "allow")
        for rel in (".git/hooks/post-commit", ".git/dev-harness/notes.txt", "dev-harness/x.py",
                    "hooks/dev-harness/a.py", "src/a.py"):
            with self.subTest(rel=rel):
                self.assertEqual(run("edit", {"file_path": self.folder(rel), "content": "x"},
                                     self.cwd), "allow")

    def test_asks_under_every_push_policy(self):
        for policy in ("ask", "deny", "allow"):
            self.config(pushPolicy=policy)
            with self.subTest(policy=policy):
                self.assertEqual(self.bash("dev-harness-board install-hooks"), "ask")
                self.assertEqual(self.bash("echo x > .git/dev-harness/hooks/pre-push"), "ask")
                self.assertEqual(self.bash("dev-harness-board install-hooks --check"), "allow")

    def test_crlf_commands(self):
        for cmd in ("dev-harness-board install-hooks\r\n", "ls\r\ndev-harness-board uninstall-hooks\r\n",
                    "bash -c 'dev-harness-board install-hooks'\r\n",
                    "echo x > .git/dev-harness/hooks/pre-push\r\n"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "ask")
        for cmd in ("dev-harness-board install-hooks --check\r\n", "dev-harness-board --check\r\n"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "allow")

    def test_existing_rules_are_unchanged_by_the_extra_glob(self):
        self.config(protectedConfigs=["scripts/verify.py"])
        self.assertEqual(self.bash("rm scripts/verify.py"), "ask")
        self.assertEqual(self.bash("rm scripts/other.py"), "allow")


class MutationTests(unittest.TestCase):
    """Remove the new rule and its corpus rows fail."""

    @staticmethod
    def rows():
        return [(n, e, cmd) for n, e, _, cmd in load_corpus()
                if ("dev-harness-board" in cmd or "dev-harness/hooks" in cmd)
                and "git" not in cmd.split()[:1]]

    def decisions(self):
        return {n: bool(bash_rules.check(cmd, {}, "/nonexistent-ch22", []))
                for n, _, cmd in self.rows()}

    def test_rows_exist_for_ask_and_pass(self):
        expects = {e["none"] for _, e, _ in self.rows()}
        self.assertEqual(expects, {"ask", "allow"})

    def test_rows_hold_now(self):
        for n, e, cmd in self.rows():
            self.assertEqual(bool(bash_rules.check(cmd, {}, "/nonexistent-ch22", [])),
                             e["none"] == "ask", f"line {n}: {cmd!r}")

    def test_without_the_command_rule_the_command_rows_fail(self):
        with mock.patch.object(bash_rules, "board_hooks_change", return_value=False):
            failed = [n for n, e, cmd in self.rows()
                      if "install-hooks" in cmd and e["none"] == "ask"
                      and not bash_rules.check(cmd, {}, "/nonexistent-ch22", [])]
        total = [n for n, e, cmd in self.rows() if "install-hooks" in cmd and e["none"] == "ask"]
        self.assertEqual(failed, total)
        self.assertGreater(len(total), 10)

    def failing_rows(self, needle):
        return [n for n, e, cmd in self.rows()
                if needle in cmd and e["none"] == "ask"
                and not bash_rules.check(cmd, {}, "/nonexistent-ch22", [])]

    def test_without_the_regex_path_a_row_fails(self):
        with mock.patch.object(bash_rules, "BOARD_HOOKS_REGEX", mock.Mock(finditer=lambda t: iter(()))):
            self.assertGreaterEqual(len(self.failing_rows("-hooks")), 3)

    def test_without_the_words_path_a_row_fails(self):
        with mock.patch.object(bash_rules, "BOARD_COMMAND", "never-matches-ch22"):
            self.assertGreaterEqual(len(self.failing_rows("-hooks")), 2)

    def test_without_the_folder_rule_the_folder_rows_fail(self):
        with mock.patch.object(bash_rules, "HOOKS_FOLDER_GLOB", "**/never-matches-ch22/**"):
            failed = [n for n, e, cmd in self.rows()
                      if "dev-harness/hooks" in cmd and e["none"] == "ask"
                      and not bash_rules.check(cmd, {}, "/nonexistent-ch22", [])]
        total = [n for n, e, cmd in self.rows() if "dev-harness/hooks" in cmd and e["none"] == "ask"]
        self.assertEqual(failed, total)
        self.assertGreater(len(total), 5)


if __name__ == "__main__":
    unittest.main()
