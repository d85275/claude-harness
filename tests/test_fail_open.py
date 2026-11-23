"""CH-07: places where the guard used to turn "could not tell" into "all fine" silently.
Run: python3 -m unittest tests.test_fail_open"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "scripts")
GUARD = os.path.join(SCRIPTS, "guard.py")
sys.path.insert(0, SCRIPTS)
import bash_rules  # noqa: E402
import harness_config  # noqa: E402


def run_guard(mode, payload, script=GUARD, env=None, raw=None):
    base = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    text = raw if raw is not None else json.dumps(payload)
    return subprocess.run([sys.executable, "-I", script, mode], input=text, capture_output=True,
                          text=True, timeout=20, env={**base, **(env or {})})


def decision(proc):
    if proc.returncode == 2:
        return "block"
    if proc.stdout.strip():
        return json.loads(proc.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return "allow"


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cwd = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.cwd, ".claude"))
        self.markers = os.path.join(self.tmp, "tmpdir")  # TMPDIR for the guard: fresh markers
        os.makedirs(self.markers)

    def config_path(self):
        return os.path.join(self.cwd, ".claude", "harness.json")


class ImportFailureTests(TempCase):
    def broken_copy(self, damage):
        scripts = os.path.join(self.tmp, "scripts")
        shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
        damage(scripts)
        return os.path.join(scripts, "guard.py")

    def test_missing_module_asks_for_bash_read_and_edit(self):
        guard = self.broken_copy(lambda d: os.remove(os.path.join(d, "shell_scan.py")))
        for mode, tool_input in (("bash", {"command": "ls"}), ("read", {"file_path": "a.txt"}),
                                 ("edit", {"file_path": "a.txt", "old_string": "a",
                                           "new_string": "b"})):
            with self.subTest(mode=mode):
                proc = run_guard(mode, {"tool_input": tool_input, "cwd": self.cwd}, script=guard)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(decision(proc), "ask")
                reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("dev-harness guard could not load", reason)
                self.assertIn("shell_scan", reason)
                self.assertNotIn("dev-harness] dev-harness", reason)

    def test_syntax_error_in_a_module_asks(self):
        def damage(d):
            with open(os.path.join(d, "bash_rules.py"), "w") as f:
                f.write("def broken(:\n")
        proc = run_guard("bash", {"tool_input": {"command": "ls"}, "cwd": self.cwd},
                         script=self.broken_copy(damage))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(decision(proc), "ask")
        self.assertIn("SyntaxError", proc.stdout)

    def test_exit_while_importing_asks(self):
        def damage(d):
            with open(os.path.join(d, "shell_scan.py"), "w") as f:
                f.write("import sys\nsys.exit(0)\n")
        proc = run_guard("bash", {"tool_input": {"command": "ls"}, "cwd": self.cwd},
                         script=self.broken_copy(damage))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(decision(proc), "ask")

    def test_notification_hook_does_not_ask(self):
        guard = self.broken_copy(lambda d: os.remove(os.path.join(d, "harness_config.py")))
        proc = run_guard("notify", {"message": "x", "cwd": self.cwd}, script=guard)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(decision(proc), "allow")
        self.assertIn("could not load", proc.stdout)


class LoadInputTests(TempCase):
    def test_unreadable_input_is_reported(self):
        for raw in ("", "{not json", "[1, 2]"):
            with self.subTest(raw=raw):
                proc = run_guard("bash", None, raw=raw)
                self.assertEqual(proc.returncode, 0)
                out = json.loads(proc.stdout)
                self.assertIn("hook input", out["systemMessage"])
                self.assertIn("not checked", out["systemMessage"])
                self.assertIn("hook input", out["hookSpecificOutput"]["additionalContext"])

    def test_readable_input_has_no_warning(self):
        proc = run_guard("bash", {"tool_input": {"command": "ls"}, "cwd": self.cwd},
                         env={"TMPDIR": self.markers})
        self.assertEqual(proc.stdout.strip(), "")


class PgrepTests(TempCase):
    def check(self, cmd, **patch):
        cfg = dict(harness_config.DEFAULTS, presets=["apple-simulators", "android-emulators"])
        with mock.patch.object(bash_rules.subprocess, "run", **patch):
            return bash_rules.check(cmd, cfg, self.cwd, [])

    def test_missing_pgrep_cannot_tell(self):
        with mock.patch.object(bash_rules.subprocess, "run",
                               side_effect=FileNotFoundError("pgrep")):
            self.assertIsNone(bash_rules.other_build_running("xcodebuild"))

    def test_timeout_and_error_exit_cannot_tell(self):
        with mock.patch.object(bash_rules.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("pgrep", 5)):
            self.assertIsNone(bash_rules.other_build_running())
        done = subprocess.CompletedProcess([], 2, stdout="", stderr="bad")
        with mock.patch.object(bash_rules.subprocess, "run", return_value=done):
            self.assertIsNone(bash_rules.other_build_running())

    def test_no_match_and_match_still_answer(self):
        for code, out, expected in ((1, "", False), (0, "123\n", True)):
            done = subprocess.CompletedProcess([], code, stdout=out, stderr="")
            with mock.patch.object(bash_rules.subprocess, "run", return_value=done):
                self.assertIs(bash_rules.other_build_running(), expected)

    def test_reset_asks_when_pgrep_cannot_tell(self):
        for cmd in ("xcrun simctl shutdown all", "adb emu kill"):
            with self.subTest(cmd=cmd):
                result = self.check(cmd, side_effect=FileNotFoundError("pgrep"))
                self.assertEqual(result[0], "ask")
                self.assertIn("could not tell", result[1])

    def test_reset_still_passes_when_nothing_runs(self):
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        self.assertIsNone(self.check("xcrun simctl shutdown all", return_value=done))


class NotifyTests(TempCase):
    def notify(self, session, **patch):
        import guard
        with mock.patch.object(guard.sys, "platform", "darwin"), \
                mock.patch.object(guard.tempfile, "tempdir", self.markers), \
                mock.patch.object(guard.subprocess, "run", **patch):
            guard.notify({"message": "hi", "cwd": self.cwd, "session_id": session}, {})

    def run_notify(self, session, **patch):
        import guard
        guard.CONFIG_WARNING = ""
        del guard.PENDING[:]
        self.addCleanup(setattr, guard, "CONFIG_WARNING", "")
        self.notify(session, **patch)
        with mock.patch.object(guard.tempfile, "tempdir", self.markers), \
                contextlib.redirect_stdout(io.StringIO()):
            guard.emit(pre_tool_use=False)  # what run() does; this records a delivered report
        return guard.CONFIG_WARNING

    def test_nonzero_exit_is_reported_once_per_session(self):
        done = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"denied")
        first = self.run_notify("s1", return_value=done)
        self.assertIn("notifications are not working", first)
        self.assertIn("exited with 1", first)
        self.assertEqual(self.run_notify("s1", return_value=done), "")
        self.assertIn("not working", self.run_notify("s2", return_value=done))

    def test_no_session_id_reports_every_time(self):
        done = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"")
        for _ in range(2):
            self.assertIn("not working", self.run_notify(None, return_value=done))

    def test_report_is_not_recorded_until_printed(self):
        import guard
        done = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"")
        guard.CONFIG_WARNING = ""
        self.addCleanup(setattr, guard, "CONFIG_WARNING", "")
        self.notify("s9", return_value=done)
        del guard.PENDING[:]  # never printed
        self.assertIn("not working", self.run_notify("s9", return_value=done))

    def test_missing_osascript_is_reported(self):
        self.assertIn("FileNotFoundError",
                      self.run_notify("s3", side_effect=FileNotFoundError("x")))

    def test_success_is_silent(self):
        done = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        self.assertEqual(self.run_notify("s4", return_value=done), "")


class DuplicateKeyTests(TempCase):
    def test_duplicate_keys_are_reported_and_last_wins(self):
        with open(self.config_path(), "w") as f:
            f.write('{"pushPolicy": "allow", "pushPolicy": "deny", '
                    '"tickets": {"archive": true, "archive": false}}')
        cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(cfg["pushPolicy"], "deny")
        text = "\n".join(errors)
        self.assertIn("duplicate key 'pushPolicy'", text)
        self.assertIn("duplicate key 'tickets.archive'", text)

    def test_a_later_empty_list_says_what_it_drops(self):
        with open(self.config_path(), "w") as f:
            f.write('{"protectedPaths": ["secrets/**"], "protectedPaths": []}')
        cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(cfg["protectedPaths"], [])
        text = "\n".join(errors)
        self.assertIn("duplicate key 'protectedPaths'", text)
        self.assertIn("secrets/**", text)
        self.assertIn("NOT active", text)

    def test_no_duplicates_no_error(self):
        with open(self.config_path(), "w") as f:
            f.write('{"pushPolicy": "deny"}')
        self.assertEqual(harness_config.load_config(self.cwd)[1], [])


class CrashTests(TempCase):
    """cwd=5 makes the guard crash after it has read the input."""

    def crash(self, mode, tool_input):
        return run_guard(mode, {"tool_input": tool_input, "cwd": 5}, env={"TMPDIR": self.markers})

    def test_credential_like_calls_ask(self):
        for mode, tool_input in (("read", {"file_path": "keys/AuthKey.p8"}),
                                 ("read", {"file_path": "/x/.env.local"}),
                                 ("bash", {"command": "cat service-account.json"}),
                                 ("bash", {"command": "cp ~/k/release.keystore /tmp"})):
            with self.subTest(tool_input=tool_input):
                proc = self.crash(mode, tool_input)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(decision(proc), "ask")
                self.assertIn("hook crashed", proc.stdout)
                self.assertIn("credential", proc.stdout)

    def test_other_calls_keep_allow_plus_warning(self):
        for mode, tool_input in (("read", {"file_path": "README.md"}),
                                 ("bash", {"command": "cat README.md"}),
                                 ("bash", {"command": "grep environment notes.txt"})):
            with self.subTest(tool_input=tool_input):
                proc = self.crash(mode, tool_input)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(decision(proc), "allow")
                self.assertIn("hook crashed", json.loads(proc.stdout)["systemMessage"])

    def test_odd_tool_input_does_not_break_the_crash_handler(self):
        for mode, tool_input in (("bash", "x"), ("read", [1]), ("bash", 5)):
            with self.subTest(mode=mode, tool_input=tool_input):
                proc = run_guard(mode, {"tool_input": tool_input, "cwd": "/tmp"},
                                 env={"TMPDIR": self.markers})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("hook crashed", json.loads(proc.stdout)["systemMessage"])

    def test_last_resort_when_the_crash_handler_fails(self):
        import guard
        with mock.patch.object(guard, "run", side_effect=ValueError("boom")), \
                mock.patch.object(guard, "looks_credential", side_effect=RuntimeError("again")), \
                mock.patch.object(guard, "MODE", "bash"), \
                mock.patch.object(guard.sys, "argv", ["guard.py", "bash"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                guard.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("crash handler failed", json.loads(out.getvalue())["systemMessage"])

    def test_edit_crash_stays_allow(self):
        proc = self.crash("edit", {"file_path": "a.pem", "old_string": "a", "new_string": "b"})
        self.assertEqual(decision(proc), "allow")


class LooksCredentialTests(TempCase):
    def looks(self, mode, tool_input, cfg):
        import guard
        with mock.patch.object(guard, "CFG", cfg), mock.patch.object(guard, "PROJECT_ROOTS", []):
            return guard.looks_credential(mode, {"tool_input": tool_input, "cwd": self.cwd})

    def test_configured_glob_with_quoted_path(self):
        cfg = {"protectedPaths": ["secrets/**"]}
        self.assertTrue(self.looks("bash", {"command": "cat 'secrets/token.txt'"}, cfg))
        self.assertTrue(self.looks("bash", {"command": 'cat "secrets/token.txt" | wc'}, cfg))
        self.assertFalse(self.looks("bash", {"command": "cat notes/token.txt"}, cfg))

    def test_unloaded_config_cannot_tell(self):
        self.assertTrue(self.looks("bash", {"command": "cat notes.txt"}, None))

    def test_unbalanced_quote_falls_back_to_plain_words(self):
        cfg = {"protectedPaths": ["secrets/**"]}
        self.assertTrue(self.looks("bash", {"command": "cat secrets/token.txt 'oops"}, cfg))

    def test_odd_input_cannot_tell(self):
        import guard
        self.assertTrue(guard.looks_credential("bash", {"tool_input": "x"}))
        self.assertTrue(guard.looks_credential("read", {"tool_input": [1]}))


class SizeCapTests(TempCase):
    def no_deadline(self):
        """Both real deadlines patched to 60 s, for tests that are not about the deadline."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(bash_rules.shell_scan, "CHECK_SECONDS", 60))
        stack.enter_context(mock.patch.object(bash_rules, "CREDENTIAL_SECONDS", 60))
        return stack

    def check(self, cmd):
        cfg = dict(harness_config.DEFAULTS)
        with mock.patch.object(bash_rules.shlex, "split", side_effect=AssertionError("shlex ran")):
            return bash_rules.check(cmd, cfg, self.cwd, [])

    def test_huge_commands_ask_without_shlex(self):
        over = bash_rules.shell_scan.LONGEST_COMMAND + 1
        for cmd in ("cat " + "a" * over,
                    "python3 - <<'EOF'\n" + "x = 1\n" * (over // 6) + "EOF\n"):
            with self.subTest(size=len(cmd)):
                result = self.check(cmd)
                self.assertEqual(result[0], "ask")
                self.assertIn("too large to check", result[1])

    def test_command_at_the_cap_is_still_checked(self):
        with self.assertRaises(AssertionError):  # shlex runs
            self.check("cat " + "a" * (bash_rules.shell_scan.LONGEST_COMMAND - 4))

    def test_padded_credential_read_still_blocks(self):
        cmd = "cat /tmp/AuthKey_AB.p8 " + "a " * 135000
        self.assertGreater(len(cmd), bash_rules.shell_scan.LONGEST_COMMAND)
        result = bash_rules.check(cmd, dict(harness_config.DEFAULTS), self.cwd, [])
        self.assertEqual(result[0], "block")
        self.assertIn("AuthKey_AB.p8", result[1])

    def test_many_distinct_tokens_with_many_globs_end_in_ask_within_the_deadline(self):
        cfg = dict(harness_config.DEFAULTS, protectedPaths=[f"zone{i}/**/*.sec" for i in range(30)])
        cmd = "cat " + " ".join(f"x{i}" for i in range(30000))  # under the size cap
        self.assertLess(len(cmd), bash_rules.shell_scan.LONGEST_COMMAND)
        with mock.patch.object(bash_rules, "CREDENTIAL_SECONDS", 0.3):
            start = time.process_time()  # CPU time: the deadline is the point, not the wall clock
            result = bash_rules.check(cmd, cfg, self.cwd, [self.cwd, self.tmp])
        self.assertLess(time.process_time() - start, 3)
        self.assertEqual(result[0], "ask")
        self.assertIn("too large to check", result[1])

    def test_repeated_tokens_are_checked_once(self):
        cfg = dict(harness_config.DEFAULTS, protectedPaths=[f"zone{i}/**/*.sec" for i in range(30)])
        cmd = "cat " + "x " * 100000  # under the size cap
        self.assertLess(len(cmd), bash_rules.shell_scan.LONGEST_COMMAND)
        with self.no_deadline():
            start = time.process_time()
            bash_rules.check(cmd, cfg, self.cwd, [self.cwd, self.tmp])
        self.assertLess(time.process_time() - start, 3)

    def test_hit_before_the_deadline_still_blocks_in_a_big_command(self):
        cfg = dict(harness_config.DEFAULTS, protectedPaths=["zone/**"])
        cmd = "cat zone/secret.txt " + " ".join(f"x{i}" for i in range(60000))
        with self.no_deadline():  # the deadline is not the point; only the hit is
            self.assertEqual(bash_rules.check(cmd, cfg, self.cwd, [])[0], "block")

    def test_hit_first_still_blocks_when_the_clock_jumps_past_the_deadline(self):
        # Fake clock: the first readings (the deadline start, then the first token checks) are 0,
        # every later one is far past the deadline. The hit is among the first tokens, so it must
        # be found before the deadline stops the pass.
        cfg = dict(harness_config.DEFAULTS, protectedPaths=["zone/**"])
        cmd = "cat zone/secret.txt " + " ".join(f"x{i}" for i in range(60000))
        readings = iter([0.0] * 4)
        clock = lambda: next(readings, 1000.0)
        with mock.patch.object(bash_rules.time, "monotonic", clock):
            result = bash_rules.check(cmd, cfg, self.cwd, [])
        self.assertEqual(result[0], "block")

    def test_default_deadlines(self):
        self.assertEqual(bash_rules.CREDENTIAL_SECONDS, 4)

    def test_huge_command_is_fast(self):
        start = time.process_time()
        result = bash_rules.check("cat " + "a" * 1_000_000, dict(harness_config.DEFAULTS),
                                  self.cwd, [])
        self.assertEqual(result[0], "ask")
        self.assertLess(time.process_time() - start, 2)


class WarningDedupTests(TempCase):
    def call(self, session="sess", mode="bash", tool_input=None):
        payload = {"tool_input": tool_input or {"command": "ls"}, "cwd": self.cwd}
        if session is not None:
            payload["session_id"] = session
        return run_guard(mode, payload, env={"TMPDIR": self.markers})

    def write(self, text):
        with open(self.config_path(), "w") as f:
            f.write(text)

    def test_warning_shows_once_then_again_when_the_file_changes(self):
        self.write("{not json")
        self.assertIn("harness.json problems", self.call().stdout)
        again = self.call()
        self.assertEqual(again.stdout.strip(), "")
        self.assertIn("harness.json", again.stderr)  # stderr still has it every time
        self.write("{still not json}")
        self.assertIn("harness.json problems", self.call().stdout)

    def test_new_session_shows_it_again(self):
        self.write("{not json")
        self.call()
        self.assertIn("harness.json problems", self.call(session="other").stdout)

    def test_untrusted_marker_folder_warns_every_call(self):
        self.write("{not json")
        uid = os.getuid() if hasattr(os, "getuid") else 0
        path = os.path.join(self.markers, f"dev-harness-{uid}")
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        kinds = {"symlink": lambda: os.symlink(elsewhere, path),
                 "dangling symlink": lambda: os.symlink(os.path.join(self.tmp, "nope"), path),
                 "regular file": lambda: open(path, "w").close()}
        for name, make in kinds.items():
            with self.subTest(kind=name):
                if os.path.lexists(path):
                    os.remove(path)
                make()
                for _ in range(3):
                    self.assertIn("harness.json problems", self.call().stdout)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_block_does_not_use_up_the_warning(self):
        self.write("{not json")
        blocked = self.call(mode="read", tool_input={"file_path": "keys/AuthKey.p8"})
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("harness.json problems", self.call().stdout)
        self.assertEqual(self.call().stdout.strip(), "")  # now it has been shown

    def test_a_notification_does_not_use_up_the_warning(self):
        self.write('{"notify": false, "bogus": 1}')
        first = self.call(mode="notify", tool_input={})
        self.assertIn("bogus", json.loads(first.stdout)["systemMessage"])
        self.assertIn("bogus", self.call().stdout)

    def test_an_ask_delivers_and_records_the_warning(self):
        self.write("{not json")
        proc = self.call(tool_input={"command": "git push origin HEAD"})
        self.assertEqual(decision(proc), "ask")
        self.assertEqual(self.call().stdout.strip(), "")

    def test_no_session_id_warns_every_call(self):
        self.write("{not json")
        for session in (None, None, None, "", "", 5, 5):
            self.assertIn("harness.json problems", self.call(session=session).stdout)

    def test_clean_config_has_no_warning(self):
        self.write('{"pushPolicy": "ask"}')
        self.assertEqual(self.call().stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
