"""CH-33: the always-ask rule (deploys, destructive cloud commands, askCommands) skips text that
lies entirely inside a proven data heredoc body, like the credential and device-reset rules
(CH-10), with the same fail-closed cases. Code heredocs and everything outside a body still ask.
Run: python3 -m unittest tests.test_data_heredoc_deploy"""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts"))
import bash_rules  # noqa: E402
import harness_config  # noqa: E402

ASK_COMMAND = "./scripts/release.sh"
DEPLOYS = [
    "firebase deploy --only hosting",
    "gcloud run services update demo --region europe-west1",
    "terraform apply",
    "kubectl delete pod demo",
    ASK_COMMAND + " prod",
]
CHECKLIST = "\n".join(["# Release checklist", ""] + [f"- run `{d}`" for d in DEPLOYS]
                      + ["- gcloud run deploy demo \\", "    --region europe-west1"])


class DataHeredocDeployTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = os.path.realpath(self._tmp.name)

    def decision(self, cmd, **cfg):
        config = dict(harness_config.DEFAULTS, askCommands=[ASK_COMMAND], **cfg)
        result = bash_rules.check(cmd, config, self.cwd, [self.cwd])
        return result[0] if result else "allow"

    def assertDecision(self, cmds, expected):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.decision(cmd), expected)

    # --- text being written passes ---------------------------------------------------------
    def test_checklist_written_by_a_data_heredoc_passes(self):
        self.assertDecision([
            f"cat > docs/release.md <<'EOF'\n{CHECKLIST}\nEOF",
            f"cat >> docs/release.md <<'EOF'\n{CHECKLIST}\nEOF",
            f"tee docs/release.md <<'EOF'\n{CHECKLIST}\nEOF",
            f"git commit -F - <<'EOF'\ndocs: release checklist\n\n{CHECKLIST}\nEOF",
            # unquoted delimiter, no $ or backtick in the body: still data
            "cat > docs/release.md <<EOF\n" + "\n".join(f"- {d}" for d in DEPLOYS) + "\nEOF",
        ], "allow")

    def test_each_command_alone_in_a_body_passes(self):
        self.assertDecision([f"cat > docs/t.md <<'EOF'\n{d}\nEOF" for d in DEPLOYS], "allow")

    def test_a_pushpolicy_does_not_change_the_exemption(self):
        cmd = f"cat > docs/release.md <<'EOF'\n{CHECKLIST}\nEOF"
        for policy in ("ask", "deny", "allow"):
            with self.subTest(policy=policy):
                self.assertEqual(self.decision(cmd, pushPolicy=policy), "allow")

    # --- the same commands run for real still ask --------------------------------------------
    def test_plain_commands_ask(self):
        self.assertDecision(DEPLOYS, "ask")

    def test_after_the_heredoc_ends(self):
        self.assertDecision([f"cat > docs/t.md <<'EOF'\nnotes\nEOF\n{d}" for d in DEPLOYS]
                            + [f"cat > docs/t.md <<'EOF'\n{d}\nEOF\n{d}" for d in DEPLOYS], "ask")

    def test_on_the_heredoc_line(self):
        self.assertDecision([f"cat > docs/t.md <<'EOF'; {d}\nnotes\nEOF" for d in DEPLOYS]
                            + [f"cat > docs/t.md <<'EOF' && {d}\nnotes\nEOF" for d in DEPLOYS]
                            + [f"{d} <<'EOF'\nnotes\nEOF" for d in DEPLOYS], "ask")

    def test_code_heredocs(self):
        cmds = []
        for d in DEPLOYS:
            cmds += [f"python3 - <<'EOF'\nimport os\nos.system('{d}')\nEOF",
                     f"sh <<'EOF'\n{d}\nEOF", f"bash <<'EOF'\n{d}\nEOF",
                     f"cat <<'EOF' | sh\n{d}\nEOF", f"cat > /dev/null <<'EOF' | bash\n{d}\nEOF",
                     f"cat > docs/t.md <<EOF\n$({d})\nEOF",  # unquoted body runs $(...)
                     f"cat > docs/t.md <<EOF\n- run `{d}`\nEOF",  # ... and backticks
                     f"cat > docs/t.md <<EOF\n{CHECKLIST}\nEOF",
                     f"ssh host <<'EOF'\n{d}\nEOF"]
        self.assertDecision(cmds, "ask")

    def test_script_written_then_run(self):
        self.assertDecision([f"cat > run.sh <<'EOF'\n{d}\nEOF\nsh run.sh" for d in DEPLOYS], "ask")

    def test_nested_shell_text(self):
        self.assertDecision([f"bash -c 'cat > t.md <<EOF\n{d}\nEOF'" for d in DEPLOYS], "ask")

    def test_unterminated_heredoc(self):
        self.assertDecision([f"cat > docs/t.md <<'EOF'\n{d}\n" for d in DEPLOYS], "ask")

    # --- the fail-closed cases (CH-12/CH-13): nothing is exempt ------------------------------
    def test_each_unsafe_check_alone_turns_the_exemption_off(self):
        for d in DEPLOYS:
            cases = {
                "crlf": f"cat > t.md <<'EOF'\n{d}\r\nEOF",
                "comment": f"echo hi # note\ncat > t.md <<'EOF'\n{d}\nEOF",
                "shadowed owner": f"cat() {{ bash; }}\ncat > t.md <<'EOF'\n{d}\nEOF",
                "device target": f"cat > /dev/stdout <<'EOF'\n{d}\nEOF",
            }
            for name, cmd in cases.items():
                with self.subTest(check=name, cmd=d):
                    self.assertEqual(self.decision(cmd), "ask")
                    others = {k: v for k, v in bash_rules.DATA_TEXT_UNSAFE.items() if k != name}
                    with mock.patch.dict(bash_rules.DATA_TEXT_UNSAFE, others, clear=True):
                        self.assertEqual(self.decision(cmd), "allow")

    def test_more_unsafe_forms(self):
        cmds = []
        for d in DEPLOYS:
            cmds += [f"cat > t.md <<'EOF'\r\nx\nEOF\r\n{d}\nEOF",  # bash ends the body at EOF\r
                     f"x=${{HOME}}\ncat > t.md <<'EOF'\n{d}\nEOF",
                     f"cat() {{ bash; }}; export -f cat; cat > t.md <<'EOF'\n{d}\nEOF",
                     f"alias cat=bash\ncat > t.md <<'EOF'\n{d}\nEOF",
                     f"PATH=/tmp/x:$PATH\ncat > t.md <<'EOF'\n{d}\nEOF",
                     f"cat > /proc/self/fd/1 <<'EOF'\n{d}\nEOF"]
        self.assertDecision(cmds, "ask")

    def test_nul_byte_or_scan_error_exempts_nothing(self):
        cmd = f"cat > t.md <<'EOF'\n{CHECKLIST}\nEOF"
        self.assertEqual(self.decision(cmd + "\x00"), "ask")
        # A failed scan makes the shell check ask on its own, so look at what the rule reads.
        patterns = bash_rules.ALWAYS_ASK_COMMANDS + [re.escape(ASK_COMMAND)]
        with mock.patch.object(bash_rules.shell_scan, "scan_shell", side_effect=RuntimeError):
            for d in DEPLOYS:
                with self.subTest(cmd=d):
                    text = bash_rules.DataText(f"cat > t.md <<'EOF'\n{d}\nEOF")
                    self.assertEqual(text.spans, [])
                    self.assertTrue(any(text.search(p) for p in patterns))

    def test_over_the_size_cap_asks(self):
        body = CHECKLIST + "\n" + "x = 1\n" * (bash_rules.shell_scan.LONGEST_COMMAND // 6 + 1)
        self.assertEqual(self.decision(f"cat > t.md <<'EOF'\n{body}EOF"), "ask")

    def test_continuation_lines_are_joined(self):
        self.assertDecision(["firebase \\\n  deploy", "gcloud run \\\n  deploy demo",
                             "cat > t.md <<'EOF'\nnotes\nEOF\nterraform \\\n  apply"], "ask")


if __name__ == "__main__":
    unittest.main()
