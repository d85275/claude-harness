"""CH-10: the credential and device-reset rules skip text that lies entirely inside a proven data
heredoc body (CH-03's spans), and nothing else. Row-level cases are in tests/data/guard_corpus.txt;
these cover the size cap, scan failures and match positions.
Run: python3 -m unittest tests.test_data_heredoc_rules"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts"))
import bash_rules  # noqa: E402
import harness_config  # noqa: E402

KEY = "/tmp/AuthKey_AB.p8"
RESET = "killall Simulator"


class DataHeredocRuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.cwd, "App.xcodeproj"))  # apple-simulators preset on

    def check(self, cmd):
        cfg = dict(harness_config.DEFAULTS)
        with mock.patch.object(bash_rules, "other_build_running", return_value=True):
            return bash_rules.check(cmd, cfg, self.cwd, [self.cwd])

    def decision(self, cmd):
        result = self.check(cmd)
        return result[0] if result else "allow"

    def test_body_text_passes_and_the_same_text_outside_blocks(self):
        body = f"cat {KEY}\n{RESET}\n"
        self.assertEqual(self.decision(f"cat > t.py <<'EOF'\n{body}EOF"), "allow")
        self.assertEqual(self.decision(f"cat > t.py <<'EOF'\nx\nEOF\ncat {KEY}"), "block")
        self.assertEqual(self.decision(f"cat > t.py <<'EOF'\nx\nEOF\n{RESET}"), "block")

    def test_big_body_under_the_cap_is_exempt(self):
        body = f"cat {KEY}\n{RESET}\n" + "x = 1\n" * 30000
        cmd = f"cat > t.py <<'EOF'\n{body}EOF"
        self.assertLess(len(cmd), bash_rules.shell_scan.LONGEST_COMMAND)
        # Real deadlines are not what this test is about: patch them high so a busy machine
        # cannot turn "exempt" into "too large"; the speed assert counts CPU time, not wall time.
        with mock.patch.object(bash_rules.shell_scan, "CHECK_SECONDS", 60), \
                mock.patch.object(bash_rules, "CREDENTIAL_SECONDS", 60):
            start = time.process_time()
            self.assertEqual(self.decision(cmd), "allow")
            self.assertLess(time.process_time() - start, 3)
            self.assertEqual(self.decision(cmd + f"\ncat {KEY}"), "block")

    def test_over_the_cap_nothing_is_exempt(self):
        body = f"cat {KEY}\n" + "x = 1\n" * (bash_rules.shell_scan.LONGEST_COMMAND // 6 + 1)
        cmd = f"cat > t.py <<'EOF'\n{body}EOF"
        self.assertGreater(len(cmd), bash_rules.shell_scan.LONGEST_COMMAND)
        result = self.check(cmd)
        self.assertEqual(result[0], "block")
        self.assertIn("AuthKey_AB.p8", result[1])
        reset = f"cat > t.py <<'EOF'\n{RESET}\n" + "x = 1\n" * (len(cmd) // 6) + "EOF"
        self.assertEqual(self.decision(reset), "block")

    def test_nul_byte_or_scan_error_exempts_nothing(self):
        cmd = f"cat > t.py <<'EOF'\ncat {KEY}\n{RESET}\nEOF"
        self.assertEqual(self.decision(cmd + "\x00"), "block")
        with mock.patch.object(bash_rules.shell_scan, "scan_shell", side_effect=RuntimeError):
            text = bash_rules.DataText(cmd)
            self.assertEqual(text.spans, [])
            self.assertTrue(text.search(r"\bkillall\s+Simulator\b"))
            self.assertEqual(self.check(cmd)[0], "block")

    def test_a_match_that_only_starts_or_ends_in_a_body_counts(self):
        text = bash_rules.DataText("cat > t <<'EOF'\nhello\nEOF")
        self.assertEqual(len(text.spans), 1)
        a, b = text.spans[0]
        self.assertFalse(text.search("hello"))  # entirely inside
        self.assertTrue(text.search(r"EOF'\s+hello"))  # starts before the body
        self.assertTrue(text.search(r"cat"))  # outside
        self.assertTrue(text.inside(a, b))
        self.assertFalse(text.inside(a - 1, b))
        self.assertFalse(text.inside(a, b + 1))

    def test_no_data_heredoc_reads_the_command_as_before(self):
        cmd = f"bash <<'EOF'\n{RESET}\nEOF"
        text = bash_rules.DataText(cmd)
        self.assertEqual((text.spans, text.outside), ([], cmd))
        self.assertEqual(self.decision(cmd), "block")
        self.assertEqual(self.decision(f"python3 - <<'EOF'\np = '{KEY}'\nEOF"), "block")

    def test_crlf_exempts_nothing(self):
        # bash ends this body at "EOF\r" and runs the next line; the scanner reads on to "EOF".
        for cmd in (f"cat > t.py <<'EOF'\r\nx\nEOF\r\n{RESET}\nEOF",
                    f"cat > t.py <<'EOF'\r\nx\nEOF\r\ncat {KEY}\nEOF",
                    f"cat > t.py <<'EOF'\n{RESET}\r\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertEqual(bash_rules.DataText(cmd).spans, [])
                self.assertEqual(self.decision(cmd), "block")
        self.assertEqual(self.decision(f"cat > t.py <<'EOF'\n{RESET}\nEOF"), "allow")

    def test_each_unsafe_check_alone_turns_the_exemption_off(self):
        cases = {
            "crlf": f"cat > t.py <<'EOF'\n{RESET}\r\nEOF",
            "comment": f"# note\ncat > t.py <<'EOF'\n{RESET}\nEOF",
            "shadowed owner": f"cat() {{ bash; }}\ncat > t.py <<'EOF'\n{RESET}\nEOF",
            "device target": f"cat > /dev/stdout <<'EOF'\n{RESET}\nEOF",
        }
        for name, cmd in cases.items():
            with self.subTest(check=name):
                self.assertEqual(self.decision(cmd), "block")
                others = {k: v for k, v in bash_rules.DATA_TEXT_UNSAFE.items() if k != name}
                with mock.patch.dict(bash_rules.DATA_TEXT_UNSAFE, others, clear=True):
                    self.assertEqual(self.decision(cmd), "allow")

    def test_continuation_lines_are_joined_for_the_reset_rules(self):
        self.assertEqual(self.decision("xcrun simctl \\\n  shutdown all"), "block")


if __name__ == "__main__":
    unittest.main()
