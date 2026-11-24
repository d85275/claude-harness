"""Table-driven regression corpus for `guard.py bash` (tests/data/guard_corpus.txt).
Run: python3 -m unittest tests.test_guard_corpus"""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "..", "hooks", "scripts", "guard.py")
CORPUS = os.path.join(HERE, "data", "guard_corpus.txt")
SETUPS = ("none", "ask", "deny", "allow")
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def load_corpus(path=CORPUS):
    """[(line number, expectations {setup: decision}, darwin only, command)]."""
    entries, current = [], None
    with open(path) as f:
        lines = f.read().split("\n")
    for number, line in enumerate(lines, 1):
        if line.startswith("=== "):
            header = line[4:].split("#", 1)[0].split()
            expect, darwin = {}, False
            for token in header:
                if token == "os=darwin":
                    darwin = True
                    continue
                key, value = token.split("=", 1)
                assert value in ("allow", "ask", "block"), f"line {number}: {token}"
                for setup in (SETUPS if key == "all" else (key,)):
                    assert setup in SETUPS, f"line {number}: {token}"
                    expect[setup] = value
            assert set(expect) == set(SETUPS), f"line {number}: every setup needs a decision"
            current = [number, expect, darwin, []]
            entries.append(current)
        elif current is not None:
            current[3].append(line)
        else:
            assert not line or line.startswith("#"), f"line {number}: text before the first entry"
    return [(n, e, d, "\n".join(c).rstrip("\n")) for n, e, d, c in entries]


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_corpus_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_guard()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dirs = {}
        for setup in SETUPS:
            d = os.path.join(cls.tmp.name, setup)
            os.makedirs(os.path.join(d, ".claude"))
            # Both device presets on (marker files), and another build always "running" (the
            # busy check is stubbed below), so a device-reset row blocks wherever the rule fires.
            os.makedirs(os.path.join(d, "App.xcodeproj"))
            open(os.path.join(d, "gradlew"), "w").close()
            if setup != "none":
                with open(os.path.join(d, ".claude", "harness.json"), "w") as f:
                    json.dump({"pushPolicy": setup,
                               "protectedConfigs": ["scripts/verify_thresholds.py"]}, f)
            if setup == "allow":  # "allow" only counts from a committed config
                for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "t"]):
                    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c",
                                    "user.email=t@example.invalid", *args],
                                   check=True, capture_output=True, env=GIT_ENV, timeout=20)
            cls.dirs[setup] = d
        cls.busy = mock.patch.object(cls.guard.bash_rules, "other_build_running",
                                     return_value=True)
        cls.busy.start()

    @classmethod
    def tearDownClass(cls):
        cls.busy.stop()
        cls.tmp.cleanup()

    def decide(self, cmd, cwd):
        g = self.guard
        cfg = g.load_config(cwd)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                g.guard_bash({"tool_input": {"command": cmd}, "cwd": cwd}, cfg)
        except SystemExit as e:
            return "block" if e.code == 2 else ("ask" if '"ask"' in out.getvalue() else "allow")
        return "allow"

    def test_corpus_parses(self):
        entries = load_corpus()
        self.assertGreater(len(entries), 200)
        for number, _, _, cmd in entries:
            self.assertTrue(cmd.strip(), f"line {number}: empty command")

    def test_every_entry_decides_as_recorded(self):
        for number, expect, darwin, cmd in load_corpus():
            if darwin and sys.platform != "darwin":
                continue
            for setup in SETUPS:
                with self.subTest(line=number, setup=setup, cmd=cmd[:80]):
                    self.assertEqual(self.decide(cmd, self.dirs[setup]), expect[setup])

    def test_no_private_data(self):
        with open(CORPUS, encoding="utf-8") as f:
            text = f.read()
        self.assertIsNone(re.search(r"/Users/|/home/[a-z]", text))


if __name__ == "__main__":
    unittest.main()
