"""Device-reset rules as presets (CH-06): auto-on detection, explicit on/off, merge precedence,
listing failure, unknown names. The busy check is stubbed; projects are temporary folders."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts"))
import bash_rules  # noqa: E402
import harness_config  # noqa: E402

APPLE = ["xcrun simctl shutdown all", "xcrun simctl delete all", "xcrun simctl erase all",
         "xcrun simctl --set /tmp/x delete all", "killall Simulator", "killall xcodebuild"]
ANDROID = ["adb emu kill", "killall qemu-system-aarch64"]


class DevicePresetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        os.mkdir(os.path.join(self.root, ".git"))
        saved = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_PROJECT_DIR", saved)
                        if saved is not None else os.environ.pop("CLAUDE_PROJECT_DIR", None))

    def touch(self, *parts, folder=False):
        path = os.path.join(self.root, *parts)
        if folder:
            os.makedirs(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()

    def config(self, body, where=None):
        folder = os.path.join(where or self.root, ".claude")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "harness.json"), "w") as f:
            json.dump(body, f)

    def result(self, cmd, busy=True, cwd=None):
        cwd = cwd or self.root
        cfg, _, roots = harness_config.load_all(cwd)
        with mock.patch.object(bash_rules, "other_build_running", return_value=busy):
            return bash_rules.check(cmd, cfg, cwd, roots)

    def assertBlocked(self, cmd, **kw):
        r = self.result(cmd, **kw)
        self.assertEqual(r and r[0], "block", cmd)

    def assertPasses(self, cmd, **kw):
        self.assertIsNone(self.result(cmd, **kw), cmd)

    def test_auto_on_at_root_and_one_level_down(self):
        for marker in (["App.xcodeproj"], ["App.xcworkspace"], ["ios", "App.xcodeproj"]):
            with self.subTest(marker=marker):
                self.touch(*marker, folder=True)
                for cmd in APPLE:
                    self.assertBlocked(cmd)
                for cmd in ANDROID:  # the other preset stays off
                    self.assertPasses(cmd)
                shutil.rmtree(os.path.join(self.root, marker[0]))
        for marker in (["gradlew"], ["android", "gradlew"]):
            with self.subTest(marker=marker):
                self.touch(*marker)
                for cmd in ANDROID:
                    self.assertBlocked(cmd)
                for cmd in APPLE:
                    self.assertPasses(cmd)
                path = os.path.join(self.root, marker[0])
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    def test_two_levels_down_is_not_detected(self):
        self.touch("a", "b", "gradlew")
        self.touch("a", "b", "App.xcodeproj", folder=True)
        for cmd in APPLE + ANDROID:
            self.assertPasses(cmd)

    def test_worktree_root_found_without_any_config_file(self):
        wt = os.path.join(self.root, "wt")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: x")  # a worktree has a .git file
        open(os.path.join(wt, "gradlew"), "w").close()
        self.assertBlocked("adb emu kill", cwd=wt)

    def test_no_mobile_project_and_no_preset_passes(self):
        for cmd in APPLE + ANDROID:
            self.assertPasses(cmd)

    def test_no_build_running_passes(self):
        self.touch("gradlew")
        self.touch("App.xcodeproj", folder=True)
        for cmd in APPLE + ANDROID:
            self.assertPasses(cmd, busy=False)

    def test_per_device_commands_pass(self):
        self.touch("gradlew")
        self.touch("App.xcodeproj", folder=True)
        for cmd in ("xcrun simctl delete 6F2A-1234", "xcrun simctl shutdown 6F2A-1234",
                    "adb -s emulator-5554 emu kill", "killall Safari"):
            self.assertPasses(cmd)

    def test_explicit_on_without_project_files(self):
        self.config({"presets": ["apple-simulators"]})
        for cmd in APPLE:
            self.assertBlocked(cmd)
        for cmd in ANDROID:
            self.assertPasses(cmd)

    def test_explicit_off_beats_auto_on(self):
        self.touch("gradlew")
        self.touch("App.xcodeproj", folder=True)
        self.config({"disabledPresets": ["android-emulators"]})
        for cmd in ANDROID:
            self.assertPasses(cmd)
        for cmd in APPLE:
            self.assertBlocked(cmd)

    def test_busy_check_uses_the_presets_build(self):
        self.config({"presets": ["apple-simulators", "android-emulators"]})
        seen = []
        cfg, _, roots = harness_config.load_all(self.root)
        with mock.patch.object(bash_rules, "other_build_running",
                               side_effect=lambda p: seen.append(p) or False):
            bash_rules.check("xcrun simctl shutdown all", cfg, self.root, roots)
            bash_rules.check("adb emu kill", cfg, self.root, roots)
        self.assertEqual(seen, ["xcodebuild", "gradlew"])

    def test_no_listing_for_ordinary_commands(self):
        self.touch("gradlew")
        cfg, _, roots = harness_config.load_all(self.root)
        with mock.patch.object(bash_rules.os, "scandir", side_effect=AssertionError("listed")):
            self.assertIsNone(bash_rules.check("ls -la", cfg, self.root, roots))

    def test_merge_precedence(self):
        sub = os.path.join(self.root, "pkg")
        os.makedirs(sub)
        os.environ["CLAUDE_PROJECT_DIR"] = self.root
        both = {"presets": ["android-emulators"], "disabledPresets": ["android-emulators"]}
        cases = [  # (root file, nested file, rule on?)
            ({"disabledPresets": ["android-emulators"]}, {"presets": ["android-emulators"]}, True),
            ({"presets": ["android-emulators"]}, {"disabledPresets": ["android-emulators"]}, True),
            ({"disabledPresets": ["android-emulators"]}, {}, False),
            ({}, {"disabledPresets": ["android-emulators"]}, False),
            (both, {}, False),
        ]
        for root_cfg, nested_cfg, expected in cases:
            with self.subTest(root=root_cfg, nested=nested_cfg):
                self.config(root_cfg)
                self.config(nested_cfg, where=sub)
                self.assertEqual(bool(self.result("adb emu kill", cwd=sub)), expected)

    def test_within_one_file_off_wins(self):
        self.config({"presets": ["apple-simulators"], "disabledPresets": ["apple-simulators"]})
        self.touch("App.xcodeproj", folder=True)
        self.assertPasses("killall Simulator")

    def test_listing_failure_means_on_with_a_warning(self):
        cfg, _, roots = harness_config.load_all(self.root)
        with mock.patch.object(bash_rules.os, "scandir", side_effect=PermissionError("no")), \
                mock.patch.object(bash_rules, "other_build_running", return_value=True):
            r = bash_rules.check("adb emu kill", cfg, self.root, roots)
        self.assertEqual(r[0], "block")
        self.assertTrue(bash_rules.NOTICES)
        self.assertIn("treating the android-emulators preset as ON", bash_rules.NOTICES[0])
        self.assertIn(bash_rules.NOTICES[0], r[1])

    def test_subfolder_listing_failure_means_on(self):
        self.touch("sub", folder=True)
        real = os.scandir

        def flaky(path):
            if str(path).endswith("sub"):
                raise OSError("io")
            return real(path)
        cfg, _, roots = harness_config.load_all(self.root)
        with mock.patch.object(bash_rules.os, "scandir", side_effect=flaky), \
                mock.patch.object(bash_rules, "other_build_running", return_value=True):
            self.assertEqual(bash_rules.check("killall Simulator", cfg, self.root, roots)[0],
                             "block")
        self.assertTrue(bash_rules.NOTICES)

    def test_no_project_folder_found_means_on_with_a_warning(self):
        cfg, _, _ = harness_config.load_all(self.root)
        with mock.patch.object(harness_config, "detection_roots", return_value=[]), \
                mock.patch.object(bash_rules, "other_build_running", return_value=True):
            r = bash_rules.check("adb emu kill", cfg, self.root, [])
        self.assertEqual(r[0], "block")
        self.assertIn("could not find the project folder", bash_rules.NOTICES[0])

    def test_notices_are_cleared_by_every_call_even_an_empty_command(self):
        bash_rules.NOTICES.append("stale")
        cfg, _, roots = harness_config.load_all(self.root)
        self.assertIsNone(bash_rules.check("", cfg, self.root, roots))
        self.assertEqual(bash_rules.NOTICES, [])

    def test_unknown_and_bad_preset_names_are_reported(self):
        self.config({"presets": ["apple-simulators", "windows"], "disabledPresets": ["nope"]})
        cfg, errors = harness_config.load_config(self.root)
        self.assertEqual(cfg["presets"], ["apple-simulators"])
        self.assertEqual(cfg["disabledPresets"], [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("'windows'" in e for e in errors))
        self.assertTrue(any("'nope'" in e for e in errors))

    def test_deploy_commands_still_ask(self):
        self.assertEqual(self.result("firebase deploy", busy=False)[0], "ask")
        self.assertEqual(self.result("terraform apply", busy=False)[0], "ask")


if __name__ == "__main__":
    unittest.main()
