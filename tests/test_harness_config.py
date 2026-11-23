"""Tests for hooks/scripts/harness_config.py and its use by guard.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts"))
import harness_config

ROOT = os.path.join(os.path.dirname(__file__), "..")
GUARD = os.path.join(ROOT, "hooks", "scripts", "guard.py")


class LoaderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        os.makedirs(os.path.join(self.cwd, ".claude"))
        os.makedirs(os.path.join(self.cwd, ".git"))  # the repo root bounds the config walk
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(os.path.join(self.cwd, ".claude", "harness.json"), "w") as f:
            f.write(text)

    def load(self, **cfg):
        self.write(json.dumps(cfg))
        return harness_config.load_config(self.cwd)


class DefaultTests(LoaderTestCase):
    def test_missing_file_gives_defaults_without_errors(self):
        cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(errors, [])
        self.assertEqual(cfg, harness_config.DEFAULTS)

    def test_missing_directory_gives_defaults(self):
        with tempfile.TemporaryDirectory() as bare:
            cfg, errors = harness_config.load_config(bare)
        self.assertEqual((cfg, errors), (harness_config.DEFAULTS, []))

    def test_each_documented_default_is_pinned(self):
        cfg, errors = self.load()
        self.assertEqual(errors, [])
        self.assertEqual(cfg, {
            "version": 1, "protectedPaths": [], "protectedConfigs": [], "askCommands": [],
            "checkWeakening": True, "weakeningMarkers": [], "notify": True,
            "pushPolicy": "ask", "presets": [], "disabledPresets": [],
            "highRiskReviewerModel": "opus",
            "tickets": {
                "dirs": ["docs/tickets"], "featureGlobs": [], "idPrefixes": {},
                "twinPlatforms": None, "archive": False,
                "trackerPath": "docs/tickets/tracker.md", "integrationBranch": "main",
                "boardPath": "docs/tickets/board.html"}})

    def test_defaults_are_not_shared_between_loads(self):
        cfg, _ = harness_config.load_config(self.cwd)
        cfg["protectedPaths"].append("x")
        cfg["tickets"]["dirs"].append("y")
        again, _ = harness_config.load_config(self.cwd)
        self.assertEqual(again["protectedPaths"], [])
        self.assertEqual(again["tickets"]["dirs"], ["docs/tickets"])

    def test_partial_tickets_keeps_other_defaults(self):
        cfg, errors = self.load(tickets={"archive": True})
        self.assertEqual(errors, [])
        self.assertTrue(cfg["tickets"]["archive"])
        self.assertEqual(cfg["tickets"]["integrationBranch"], "main")

    def test_template_is_valid_and_lists_every_key(self):
        with open(os.path.join(ROOT, "templates", "harness.json")) as f:
            raw = json.load(f)
        _, errors = self.load(**raw)
        self.assertEqual(errors, [])
        self.assertEqual(set(raw), set(harness_config.DEFAULTS))
        self.assertEqual(set(raw["tickets"]), set(harness_config.DEFAULTS["tickets"]))


class ValidationTests(LoaderTestCase):
    def test_malformed_json_is_reported_and_defaults_used(self):
        self.write('{"protectedPaths": [')
        cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(cfg, harness_config.DEFAULTS)
        self.assertEqual(len(errors), 1)
        self.assertIn("harness.json", errors[0])

    def test_non_object_is_reported(self):
        self.write("[]")
        _, errors = harness_config.load_config(self.cwd)
        self.assertEqual(len(errors), 1)

    def test_unknown_keys_are_reported_known_keys_still_apply(self):
        cfg, errors = self.load(askCommand=["x"], notify=False,
                                tickets={"dir": [], "archive": True})
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("askCommand" in e for e in errors))
        self.assertTrue(any("tickets.dir" in e for e in errors))
        self.assertIs(cfg["notify"], False)
        self.assertTrue(cfg["tickets"]["archive"])

    def test_bad_values_are_reported_and_default_used(self):
        bad = [("version", 2), ("pushPolicy", "never"), ("highRiskReviewerModel", "gpt"),
               ("notify", "yes"), ("presets", [1]), ("disabledPresets", "x"),
               ("checkWeakening", 0), ("tickets", [])]
        for key, value in bad:
            with self.subTest(key=key):
                cfg, errors = self.load(**{key: value})
                self.assertEqual(len(errors), 1)
                self.assertEqual(cfg, harness_config.DEFAULTS)

    def test_bad_ticket_values_are_reported(self):
        bad = [("dirs", "docs"), ("idPrefixes", {"A": {"platform": "x"}}),
               ("idPrefixes", {"A": "x"}), ("twinPlatforms", "ios"), ("archive", 1),
               ("trackerPath", None), ("integrationBranch", 3), ("boardPath", [])]
        for key, value in bad:
            with self.subTest(key=key):
                cfg, errors = self.load(tickets={key: value})
                self.assertEqual(len(errors), 1)
                self.assertEqual(cfg["tickets"], harness_config.DEFAULTS["tickets"])

    def test_valid_values_are_accepted(self):
        cfg, errors = self.load(
            pushPolicy="deny", highRiskReviewerModel="fable", presets=["apple-simulators"],
            tickets={"idPrefixes": {"IOS": {"platform": "ios", "kind": "port"}},
                     "twinPlatforms": ["ios", "android"]})
        self.assertEqual(errors, [])
        self.assertEqual(cfg["pushPolicy"], "deny")
        self.assertEqual(cfg["highRiskReviewerModel"], "fable")
        self.assertEqual(cfg["tickets"]["twinPlatforms"], ["ios", "android"])


class SalvageTests(LoaderTestCase):
    def test_bare_string_becomes_one_item_list(self):
        cfg, errors = self.load(protectedPaths="keys/**")
        self.assertEqual(cfg["protectedPaths"], ["keys/**"])
        self.assertEqual(len(errors), 1)

    def test_bad_elements_are_dropped_and_good_ones_kept(self):
        for key in ("protectedPaths", "protectedConfigs", "askCommands"):
            with self.subTest(key=key):
                cfg, errors = self.load(**{key: ["a", 3, "b", None]})
                self.assertEqual(cfg[key], ["a", "b"])
                self.assertEqual(len(errors), 2)
                self.assertTrue(all("NOT active" in e for e in errors))

    def test_wholly_invalid_protection_list_says_it_is_not_active(self):
        cfg, errors = self.load(protectedPaths={"a": 1})
        self.assertEqual(cfg["protectedPaths"], [])
        self.assertEqual(len(errors), 1)
        self.assertIn("NOT active", errors[0])

    def test_invalid_regex_marker_is_dropped_by_name(self):
        cfg, errors = self.load(weakeningMarkers=["(", r"\bOK\b"])
        self.assertEqual(cfg["weakeningMarkers"], [r"\bOK\b"])
        self.assertEqual(len(errors), 1)
        self.assertIn("'('", errors[0])

    def test_recursion_error_from_the_parser_is_reported(self):
        self.write("{}")
        with mock.patch.object(harness_config.json, "load", side_effect=RecursionError):
            cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(cfg, harness_config.DEFAULTS)
        self.assertEqual(len(errors), 1)
        self.assertIn("NOT active", errors[0])

    def test_deeply_nested_json_is_reported_not_a_crash(self):
        self.write("[" * 100000 + "]" * 100000)
        cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(cfg, harness_config.DEFAULTS)
        self.assertEqual(len(errors), 1)


class LocationTests(LoaderTestCase):
    def test_walks_up_from_a_subdirectory(self):
        self.write(json.dumps({"protectedPaths": ["x/**"]}))
        sub = os.path.join(self.cwd, "sub", "deeper")
        os.makedirs(sub)
        cfg, errors = harness_config.load_config(sub)
        self.assertEqual((cfg["protectedPaths"], errors), (["x/**"], []))
        self.assertEqual(harness_config.project_roots(sub), [self.cwd])

    def test_nearest_and_parent_configs_within_the_repo_are_merged(self):
        self.write(json.dumps({"protectedPaths": ["outer/**"]}))
        inner = os.path.join(self.cwd, "wt")
        os.makedirs(os.path.join(inner, ".claude"))
        with open(os.path.join(inner, ".claude", "harness.json"), "w") as f:
            json.dump({"protectedPaths": ["inner/**"]}, f)
        cfg, errors = harness_config.load_config(inner)
        self.assertEqual(cfg["protectedPaths"], ["inner/**", "outer/**"])
        self.assertEqual(len(errors), 1)
        self.assertIn("merged", errors[0])
        self.assertEqual(harness_config.project_roots(inner), [inner, self.cwd])

    def test_config_above_the_repo_root_is_ignored(self):
        repo = os.path.join(self.cwd, "repo", "sub")
        os.makedirs(repo)
        os.makedirs(os.path.join(self.cwd, "repo", ".git"))
        self.write(json.dumps({"protectedPaths": ["above/**"]}))  # parent of the repo
        cfg, errors = harness_config.load_config(repo)
        self.assertEqual(cfg, harness_config.DEFAULTS)
        self.assertEqual(len(errors), 1)
        self.assertIn("NOT using", errors[0])
        self.assertEqual(harness_config.project_roots(repo), [])

    def test_worktree_git_file_stops_the_walk(self):
        wt = os.path.join(self.cwd, "wt")
        os.makedirs(os.path.join(wt, "sub"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /elsewhere\n")
        self.write(json.dumps({"protectedPaths": ["above/**"]}))
        self.assertEqual(harness_config.load_config(os.path.join(wt, "sub"))[0],
                         harness_config.DEFAULTS)
        os.makedirs(os.path.join(wt, ".claude"))
        with open(os.path.join(wt, ".claude", "harness.json"), "w") as f:
            json.dump({"protectedPaths": ["own/**"]}, f)
        cfg, _ = harness_config.load_config(os.path.join(wt, "sub"))
        self.assertEqual(cfg["protectedPaths"], ["own/**"])

    def test_without_git_only_cwd_is_checked(self):
        os.rmdir(os.path.join(self.cwd, ".git"))
        self.write(json.dumps({"protectedPaths": ["x/**"]}))
        sub = os.path.join(self.cwd, "sub")
        os.makedirs(sub)
        self.assertEqual(harness_config.load_config(sub)[0], harness_config.DEFAULTS)
        self.assertEqual(harness_config.load_config(self.cwd)[0]["protectedPaths"], ["x/**"])

    def test_without_git_walk_stops_at_claude_project_dir(self):
        os.rmdir(os.path.join(self.cwd, ".git"))
        self.write(json.dumps({"protectedPaths": ["x/**"]}))
        sub = os.path.join(self.cwd, "sub", "deeper")
        os.makedirs(sub)
        os.environ["CLAUDE_PROJECT_DIR"] = self.cwd
        self.assertEqual(harness_config.load_config(sub)[0]["protectedPaths"], ["x/**"])

    def nested(self, content):
        nested = os.path.join(self.cwd, "pkg")
        os.makedirs(os.path.join(nested, ".claude"))
        with open(os.path.join(nested, ".claude", "harness.json"), "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        os.environ["CLAUDE_PROJECT_DIR"] = self.cwd
        return nested

    def test_root_and_nested_configs_are_merged(self):
        self.write(json.dumps({"protectedPaths": ["rootsecret/**"]}))
        nested = self.nested({"protectedPaths": ["pkgsecret/**"], "askCommands": ["./pkgdeploy"]})
        cfg, errors = harness_config.load_config(nested)
        self.assertEqual(cfg["protectedPaths"], ["rootsecret/**", "pkgsecret/**"])
        self.assertEqual(cfg["askCommands"], ["./pkgdeploy"])
        self.assertEqual(len(errors), 1)
        self.assertIn("merged", errors[0])

    def test_nested_empty_config_keeps_root_rules(self):
        self.write(json.dumps({"protectedPaths": ["root/**"]}))
        cfg, _ = harness_config.load_config(self.nested("{}"))
        self.assertEqual(cfg["protectedPaths"], ["root/**"])

    def test_identical_copies_give_no_notice(self):
        content = {"protectedPaths": ["a/**"], "pushPolicy": "deny"}
        self.write(json.dumps(content))
        cfg, errors = harness_config.load_config(self.nested(json.dumps(content, indent=4)))
        self.assertEqual(errors, [])
        self.assertEqual(cfg["protectedPaths"], ["a/**"])

    def test_a_broken_root_does_not_drop_the_nested_rules(self):
        self.write("{bad")
        cfg, errors = harness_config.load_config(self.nested({"protectedPaths": ["pkgsecret/**"]}))
        self.assertEqual(cfg["protectedPaths"], ["pkgsecret/**"])
        self.assertEqual(len(errors), 1)
        self.assertIn("NOT active", errors[0])

    def test_weaker_nested_push_policy_cannot_relax_the_root(self):
        for root, nested, expected in [("ask", "allow", "ask"), ("allow", "ask", "ask"),
                                       ("ask", "deny", "deny"), ("deny", "allow", "deny")]:
            with self.subTest(root=root, nested=nested):
                self.write(json.dumps({"pushPolicy": root}))
                path = os.path.join(self.cwd, "pkg", ".claude")
                os.makedirs(path, exist_ok=True)
                with open(os.path.join(path, "harness.json"), "w") as f:
                    json.dump({"pushPolicy": nested}, f)
                os.environ["CLAUDE_PROJECT_DIR"] = self.cwd
                cfg, _ = harness_config.load_config(os.path.join(self.cwd, "pkg"))
                self.assertEqual(cfg["pushPolicy"], expected)

    def test_other_merge_rules(self):
        self.write(json.dumps({"checkWeakening": False, "notify": False, "presets": ["apple-simulators"],
                               "weakeningMarkers": ["x"], "highRiskReviewerModel": 5,
                               "tickets": {"archive": True}}))
        cfg, _ = harness_config.load_config(self.nested(
            {"checkWeakening": True, "notify": True, "presets": ["apple-simulators", "android-emulators"],
             "weakeningMarkers": ["y"], "highRiskReviewerModel": "fable",
             "tickets": {"archive": False, "boardPath": "b.html"}}))
        self.assertTrue(cfg["checkWeakening"])      # true if either is true
        self.assertFalse(cfg["notify"])             # root wins
        self.assertEqual(cfg["presets"], ["apple-simulators", "android-emulators"])
        self.assertEqual(cfg["weakeningMarkers"], ["x", "y"])
        self.assertEqual(cfg["highRiskReviewerModel"], "fable")  # root invalid -> nested
        self.assertTrue(cfg["tickets"]["archive"])  # root wins
        self.assertEqual(cfg["tickets"]["boardPath"], "b.html")  # root lacks it

    def test_submodule_with_parent_config_warns_and_does_not_load(self):
        self.write(json.dumps({"protectedPaths": ["parent/**"]}))
        sub = os.path.join(self.cwd, "mod")
        os.makedirs(sub)
        with open(os.path.join(sub, ".git"), "w") as f:
            f.write("gitdir: ../.git/modules/mod\n")
        cfg, errors = harness_config.load_config(sub)
        self.assertEqual(cfg["protectedPaths"], [])
        self.assertEqual(len(errors), 1)
        self.assertIn("NOT using", errors[0])

    def test_worktree_copy_cannot_weaken_the_root(self):
        self.write(json.dumps({"protectedPaths": ["main/**"], "pushPolicy": "ask"}))
        wt = os.path.join(self.cwd, "wt")
        os.makedirs(os.path.join(wt, ".claude"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: x\n")
        with open(os.path.join(wt, ".claude", "harness.json"), "w") as f:
            json.dump({"pushPolicy": "allow"}, f)  # a weakened worktree copy
        os.environ["CLAUDE_PROJECT_DIR"] = self.cwd
        cfg, _ = harness_config.load_config(wt)
        self.assertEqual(cfg["protectedPaths"], ["main/**"])
        self.assertEqual(cfg["pushPolicy"], "ask")

    def test_falls_back_to_claude_project_dir(self):
        self.write(json.dumps({"protectedPaths": ["x/**"]}))
        with tempfile.TemporaryDirectory() as elsewhere:
            os.environ["CLAUDE_PROJECT_DIR"] = self.cwd
            cfg, _ = harness_config.load_config(elsewhere)
            self.assertEqual(cfg["protectedPaths"], ["x/**"])
            self.assertEqual(harness_config.project_roots(elsewhere), [self.cwd])


class UnreachableConfigTests(LoaderTestCase):
    """An existing but unreadable harness.json must never read as "no config"."""

    def target(self):
        return os.path.join(self.cwd, ".claude", "harness.json")

    def check(self):
        cfg, errors = harness_config.load_config(self.cwd)
        self.assertEqual(cfg, harness_config.DEFAULTS)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("NOT active", errors[0])
        payload = json.dumps({"tool_input": {"file_path": os.path.join(self.cwd, "k", "a.pem")},
                              "cwd": self.cwd})
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        proc = subprocess.run([sys.executable, "-I", GUARD, "read"], input=payload,
                              capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(proc.returncode, 2)  # built-in rules still apply
        self.assertIn("NOT active", proc.stderr)

    def test_dangling_symlink(self):
        os.symlink("does-not-exist.json", self.target())
        self.check()

    def test_symlink_loop(self):
        os.symlink("harness.json", self.target())
        self.check()

    def test_directory_named_harness_json(self):
        os.makedirs(self.target())
        self.check()

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores chmod")
    def test_unreadable_claude_folder(self):
        self.write("{}")
        folder = os.path.join(self.cwd, ".claude")
        os.chmod(folder, 0)
        try:
            self.check()
        finally:
            os.chmod(folder, 0o755)


class GuardIntegrationTests(LoaderTestCase):
    def guard(self, mode, tool_input, cwd=None, env=None):
        payload = json.dumps({"tool_input": tool_input, "cwd": cwd or self.cwd})
        base = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        return subprocess.run([sys.executable, "-I", GUARD, mode], input=payload,
                              capture_output=True, text=True, timeout=20,
                              env={**base, **(env or {})})

    def stdout_json(self, proc):
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1, proc.stdout)
        return json.loads(proc.stdout)

    def test_malformed_file_blocks_builtin_and_warns_on_stderr(self):
        self.write("{not json")
        proc = self.guard("read", {"file_path": os.path.join(self.cwd, "keys", "a.pem")})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("harness.json", proc.stderr)

    def test_allow_path_emits_system_message_and_model_context(self):
        self.write("{not json")
        proc = self.guard("bash", {"command": "ls"})
        self.assertEqual(proc.returncode, 0)
        self.assertIn("harness.json", proc.stderr)
        out = self.stdout_json(proc)
        self.assertIn("harness.json", out["systemMessage"])
        hook = out["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "PreToolUse")
        self.assertIn("harness.json", hook["additionalContext"])
        self.assertNotIn("permissionDecision", hook)

    def test_ask_path_is_one_object_with_decision_and_warning(self):
        self.write(json.dumps({"protectedPath": ["x"]}))
        proc = self.guard("bash", {"command": "git push origin HEAD"})
        self.assertEqual(proc.returncode, 0)
        out = self.stdout_json(proc)
        hook = out["hookSpecificOutput"]
        self.assertEqual(hook["permissionDecision"], "ask")
        self.assertIn("protectedPath", hook["permissionDecisionReason"])
        self.assertIn("protectedPath", out["systemMessage"])
        self.assertIn("protectedPath", hook["additionalContext"])

    def test_block_path_appends_warning_to_stderr(self):
        self.write(json.dumps({"protectedPaths": ["secret/**"], "pushPolicy": "bogus"}))
        proc = self.guard("read", {"file_path": os.path.join(self.cwd, "secret", "a.txt")})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Reading protected credential file blocked", proc.stderr)
        self.assertEqual(proc.stderr.count("pushPolicy"), 1)

    def test_crash_in_a_guard_is_reported_and_exits_zero(self):
        payload = json.dumps({"tool_input": {"command": "cat x"}, "cwd": 5})
        proc = subprocess.run([sys.executable, "-I", GUARD, "bash"], input=payload,
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0)
        out = self.stdout_json(proc)
        self.assertIn("hook crashed", out["systemMessage"])
        self.assertIn("not checked", out["systemMessage"])
        self.assertIn("harness.json problems", out["systemMessage"])

    def pkg_fixture(self, root_text):
        self.write(root_text)
        pkg = os.path.join(self.cwd, "pkg")
        os.makedirs(os.path.join(pkg, ".claude"))
        with open(os.path.join(pkg, ".claude", "harness.json"), "w") as f:
            json.dump({"protectedPaths": ["pkgsecret/**"], "askCommands": ["./pkgdeploy"]}, f)
        return pkg, {"CLAUDE_PROJECT_DIR": self.cwd}

    def test_root_and_nested_rules_both_apply(self):
        pkg, env = self.pkg_fixture(json.dumps({"protectedPaths": ["rootsecret/**"]}))
        for cmd in ("cat ../rootsecret/x", "cat pkgsecret/x"):
            with self.subTest(cmd=cmd):
                proc = self.guard("bash", {"command": cmd}, cwd=pkg, env=env)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("merged", proc.stderr)
        proc = self.guard("bash", {"command": "./pkgdeploy"}, cwd=pkg, env=env)
        self.assertEqual(self.stdout_json(proc)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_nested_rules_still_apply_when_the_root_is_malformed(self):
        pkg, env = self.pkg_fixture("{bad")
        proc = self.guard("bash", {"command": "cat pkgsecret/x"}, cwd=pkg, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("NOT active", proc.stderr)
        # CH-07: the warning shows once per config state, so use a fresh marker folder here
        fresh = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fresh, True)
        proc = self.guard("bash", {"command": "ls"}, cwd=pkg, env={**env, "TMPDIR": fresh})
        self.assertIn("NOT active", self.stdout_json(proc)["systemMessage"])

    def test_identical_copies_are_silent(self):
        content = json.dumps({"protectedPaths": ["secret/**"]})
        pkg, env = self.pkg_fixture(content)
        with open(os.path.join(pkg, ".claude", "harness.json"), "w") as f:
            f.write(content)
        proc = self.guard("bash", {"command": "ls"}, cwd=pkg, env=env)
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))

    def test_closed_stderr_does_not_stop_the_warning_reaching_stdout(self):
        self.write("{bad")
        sys.path.insert(0, os.path.dirname(GUARD))
        import guard
        self.addCleanup(lambda: setattr(guard, "CONFIG_WARNING", ""))
        with mock.patch.object(sys, "stderr", None):
            guard.load_config(self.cwd)
        self.assertIn("harness.json problems", guard.CONFIG_WARNING)

    def test_notify_emits_system_message_only(self):
        self.write(json.dumps({"notify": False, "oops": 1}))
        proc = self.guard("notify", {})
        self.assertEqual(self.stdout_json(proc).keys(), {"systemMessage"})

    def test_valid_and_missing_files_are_silent(self):
        self.write(json.dumps({"protectedPaths": ["secret/**"]}))
        proc = self.guard("bash", {"command": "ls"})
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))
        os.remove(os.path.join(self.cwd, ".claude", "harness.json"))
        proc = self.guard("bash", {"command": "ls"})
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))
        proc = self.guard("notify", {})
        self.assertEqual(proc.stdout, "")

    def test_valid_file_keeps_exact_ask_output(self):
        proc = self.guard("bash", {"command": "git push"})
        out = self.stdout_json(proc)
        self.assertEqual(out.keys(), {"hookSpecificOutput"})
        self.assertEqual(out["hookSpecificOutput"].keys(),
                         {"hookEventName", "permissionDecision", "permissionDecisionReason"})

    def test_deeply_nested_file_still_blocks_pem_read_and_warns(self):
        self.write("[" * 100000 + "]" * 100000)
        proc = self.guard("read", {"file_path": os.path.join(self.cwd, "keys", "a.pem")})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("harness.json", proc.stderr)

    def test_bad_marker_does_not_disable_builtin_weakening_check(self):
        self.write(json.dumps({"weakeningMarkers": ["("]}))
        proc = self.guard("edit", {"file_path": "a.py", "old_string": "x = 1",
                                   "new_string": "x = 1  # noqa"})
        out = self.stdout_json(proc)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("'('", out["systemMessage"])

    def test_protection_found_from_a_subdirectory_cwd(self):
        self.write(json.dumps({"protectedPaths": ["sub/secret/**", "secret/**"]}))
        sub = os.path.join(self.cwd, "sub")
        os.makedirs(sub)
        for cmd in ("cat ../secret/x", "cat secret/x", "cat ../sub/secret/y"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.guard("bash", {"command": cmd}, cwd=sub).returncode, 2)
        self.assertEqual(self.guard("bash", {"command": "cat other/x"}, cwd=sub).returncode, 0)

    def test_root_relative_glob_matches_for_reads_and_edits(self):
        self.write(json.dumps({"protectedPaths": ["secret/**"],
                               "protectedConfigs": ["tools/check.py"]}))
        sub = os.path.join(self.cwd, "sub")
        os.makedirs(sub)
        proc = self.guard("read", {"file_path": os.path.join(self.cwd, "secret", "x")}, cwd=sub)
        self.assertEqual(proc.returncode, 2)
        proc = self.guard("edit", {"file_path": os.path.join(self.cwd, "tools", "check.py"),
                                   "old_string": "a", "new_string": "b"}, cwd=sub)
        self.assertEqual(self.stdout_json(proc)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_claude_project_dir_fallback(self):
        self.write(json.dumps({"protectedPaths": ["secret/**"]}))
        with tempfile.TemporaryDirectory() as elsewhere:
            proc = self.guard("bash", {"command": "cat secret/x"}, cwd=elsewhere,
                              env={"CLAUDE_PROJECT_DIR": self.cwd})
            self.assertEqual(proc.returncode, 2)
            proc = self.guard("bash", {"command": "cat secret/x"}, cwd=elsewhere)
            self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
