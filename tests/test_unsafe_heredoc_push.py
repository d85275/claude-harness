"""CH-12: push and protected-config detection read every hit as a hit when a DATA_TEXT_UNSAFE
check holds (the same table the credential rules use). Row-level cases are in
tests/data/guard_corpus.txt; CRLF cases live here because the corpus loader cannot hold "\\r".
Run: python3 -m unittest tests.test_unsafe_heredoc_push"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts"))
import bash_rules  # noqa: E402
import shell_check  # noqa: E402


def run(cmd, cwd):
    check = shell_check.ShellCheck({}, cwd, [cwd])
    check.run(cmd)
    return check


class UnsafeHeredocPushTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = os.path.realpath(self._tmp.name)

    def test_one_table_for_both_rule_sets(self):
        self.assertIs(bash_rules.DATA_TEXT_UNSAFE, shell_check.DATA_TEXT_UNSAFE)

    def test_crlf_push_and_config_write_count(self):
        # bash ends the body at "EOF\r" and runs what follows; the scanner reads on to "EOF".
        for line, field in (("git push origin HEAD", "publish"), ("> .eslintrc.json", "config_hit")):
            for cmd in (f"cat > t.txt <<'EOF'\r\nx\nEOF\r\n{line}\nEOF",
                        f"cat > t.txt <<'EOF'\n{line}\r\nEOF"):
                with self.subTest(cmd=cmd):
                    self.assertTrue(getattr(run(cmd, self.cwd), field))

    def test_plain_data_heredoc_still_passes(self):
        for cmd in ("git commit -F - <<'EOF'\nnote: git push later\nEOF",
                    "cat > t.txt <<'EOF'\n> .eslintrc.json\nEOF"):
            with self.subTest(cmd=cmd):
                check = run(cmd, self.cwd)
                self.assertFalse(check.publish)
                self.assertIsNone(check.config_hit)

    def test_each_check_alone_decides(self):
        cases = {
            "crlf": "cat > t.txt <<'EOF'\ngit push\r\nEOF",
            "comment": "# n\ncat > t.txt <<'EOF'\ngit push\nEOF",
            "shadowed owner": "cat() { bash; }\ncat > t.txt <<'EOF'\ngit push\nEOF",
            "device target": "cat > /dev/stdout <<'EOF'\ngit push\nEOF",
        }
        for name, cmd in cases.items():
            with self.subTest(check=name):
                self.assertTrue(run(cmd, self.cwd).publish)
                others = {k: v for k, v in shell_check.DATA_TEXT_UNSAFE.items() if k != name}
                with mock.patch.dict(shell_check.DATA_TEXT_UNSAFE, others, clear=True):
                    self.assertFalse(run(cmd, self.cwd).publish)


TRICKS = {
    "crlf": "cat > t.txt <<'EOF'\nBODY\r\nEOF",
    "comment": "echo hi # c\ncat > t.txt <<'EOF'\nBODY\nEOF",
    "shadowed owner": "cat() { bash; }\ncat > t.txt <<'EOF'\nBODY\nEOF",
    "device target": "cat > /dev/stdout <<'EOF'\nBODY\nEOF",
}
# A quote-split body line, as it sits inside a bash -c string (the split ends and reopens the
# single quotes of that string, so the outer text never holds the words together).
QUOTE_SPLIT_HOOKS = "git con'\"fig core.hooksPath x\"'"
QUOTE_SPLIT_STASH = "git sta'\"sh pop\"'"
QUOTE_SPLIT_PUSH = "git pu'\"sh origin\"'"
SHADOW = "cat() { bash; }; export -f cat; "


def wrap(line):
    return "bash -c 'cat > f <<EOF\n" + line + "\nEOF'"


HOOKS = "git config core.hooksPath x"
STASH = "git stash pop"


class UnsafeHeredocGitStateTests(unittest.TestCase):
    """CH-13: git_state_hits (stash, core.hooksPath) reads a body as text only when no
    DATA_TEXT_UNSAFE check holds."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = os.path.realpath(self._tmp.name)

    def hits(self, cmd):
        check = run(cmd, self.cwd)
        return bash_rules.git_state_hits(check)

    def test_each_trick_makes_a_hooks_path_or_stash_body_count(self):
        for name, template in TRICKS.items():
            for what, line, field in (("hooks", HOOKS, "hooks_path"), ("stash", STASH, "stash"),
                                      ("option", "git -c core.hooksPath=x log", "hooks_path")):
                cmd = template.replace("BODY", line)
                with self.subTest(check=name, what=what):
                    self.assertTrue(self.hits(cmd)[field])

    def test_each_check_alone_decides(self):
        for name, template in TRICKS.items():
            others = {k: v for k, v in shell_check.DATA_TEXT_UNSAFE.items() if k != name}
            for line, field in ((HOOKS, "hooks_path"), (STASH, "stash")):
                cmd = template.replace("BODY", line)
                with self.subTest(check=name, line=line):
                    self.assertTrue(self.hits(cmd)[field])
                    with mock.patch.dict(shell_check.DATA_TEXT_UNSAFE, others, clear=True):
                        self.assertIsNone(self.hits(cmd))

    def test_crlf_in_the_delimiter_line_and_after_the_body(self):
        for line, field in ((HOOKS, "hooks_path"), (STASH, "stash")):
            for cmd in (f"cat > t.txt <<'EOF'\r\nx\nEOF\r\n{line}\nEOF",
                        f"cat > t.txt <<'EOF'\n{line}\r\nEOF"):
                with self.subTest(cmd=cmd):
                    self.assertTrue(self.hits(cmd)[field])

    def test_plain_data_heredoc_still_passes(self):
        for line in (HOOKS, STASH, "git -c core.hooksPath=x log"):
            with self.subTest(line=line):
                self.assertIsNone(self.hits(f"cat > t.txt <<'EOF'\n{line}\nEOF"))

    def test_unsafe_outer_text_makes_nested_bodies_count(self):
        # bash -c runs the exported `cat` function, so the nested body is code; the outer text has
        # no body, and its literal is quote-split so no regex matches it. Only the nested text
        # (no unsafe marker of its own) holds the hit.
        for split, field in ((QUOTE_SPLIT_HOOKS, "hooks_path"), (QUOTE_SPLIT_STASH, "stash")):
            with self.subTest(what=field):
                self.assertTrue(self.hits(SHADOW + wrap(split))[field])

    def test_unsafe_outer_text_makes_nested_push_count(self):
        self.assertTrue(run(SHADOW + wrap(QUOTE_SPLIT_PUSH), self.cwd).publish)

    def test_nested_plain_body_with_a_safe_outer_text_still_passes(self):
        self.assertFalse(run(wrap(QUOTE_SPLIT_PUSH), self.cwd).publish)
        self.assertIsNone(self.hits(wrap(QUOTE_SPLIT_HOOKS)))
        self.assertIsNone(self.hits(wrap(QUOTE_SPLIT_STASH)))

if __name__ == "__main__":
    unittest.main()
