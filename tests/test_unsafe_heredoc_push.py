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


if __name__ == "__main__":
    unittest.main()
