"""CH-31: a glob written by rm, cp, mv, chmod ... asks only when it can reach a protected
config. End to end through guard.py, in a temporary project.
Run: python3 -m unittest tests.test_glob_writes"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # shared helpers in test_guard
from test_guard import GUARD, GuardTestCase  # noqa: E402


def decide(command, cwd, env=None):
    """("allow" | "ask" | "block", the prompt text). CDPATH is unset unless env sets it."""
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    env = {**{k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CDPATH")},
           **(env or {})}
    proc = subprocess.run([sys.executable, "-I", GUARD, "bash"], input=payload,
                          capture_output=True, text=True, timeout=20, env=env)
    if proc.returncode == 2:
        return "block", proc.stderr
    if proc.returncode != 0:
        raise AssertionError(f"unexpected exit {proc.returncode}: {proc.stderr}")
    if '"ask"' in proc.stdout:
        reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        return "ask", reason
    return "allow", ""


def touch(*paths):
    for path in paths:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()


class GlobWriteTests(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.cwd, "build", "out")
        touch(os.path.join(self.out, "a.xml"), os.path.join(self.out, "b.xml"),
              os.path.join(self.out, "notes.txt"))

    def path(self, *parts):
        return os.path.join(self.cwd, *parts)

    def expect(self, commands, expected, cwd=None):
        for cmd in commands:
            with self.subTest(cmd=cmd):
                self.assertEqual(decide(cmd, cwd or self.cwd)[0], expected)

    # --- passes: the expansion is known and reaches no protected config -------------------------

    def test_glob_in_a_build_folder_without_protected_files_passes(self):
        """The CH-31 case: this asked before (`*.xml` could be `**/lint.xml` by name)."""
        self.expect(["rm -f build/out/*.xml"], "allow")

    def test_other_quiet_forms_pass(self):
        touch(self.path("build", "res", "values.xml"))
        self.expect(["rm build/out/*.xml", "rm -f build/out/*.xml build/out/*.txt",
                     "rm -rf build/*", "rm -f build/*/*.xml", "chmod 644 build/out/*.xml",
                     "touch build/out/?.xml", "rm -f build/out/*.xml 2>/dev/null",
                     "ls build/out && rm -f build/out/*.xml", "cd ./build/out && rm -f *.xml",
                     f"cd {self.cwd}/build && rm -f out/*.xml",
                     "rm -f build/out/*.xml; echo done",
                     "rm -f build/out/*.xml && rm -rf build/res",
                     "cp build/out/*.xml /tmp/", "rm -f build/missing-*.xml"], "allow")

    # --- asks: a protected config is there, or could be --------------------------------------

    def test_protected_file_in_the_folder_asks_and_says_why(self):
        touch(os.path.join(self.out, "lint.xml"))
        verdict, reason = decide("rm -f build/out/*.xml", self.cwd)
        self.assertEqual(verdict, "ask")
        self.assertIn("rm: `rm -f build/out/*.xml` reaches build/out/lint.xml", reason)
        self.assertIn("**/lint.xml", reason)

    def test_protected_file_reached_through_a_matched_folder_asks(self):
        touch(self.path("build", "res", "lint.xml"))
        self.expect(["rm -rf build/*", "chmod -R 644 build/*", "mv build/* /tmp/x/"], "ask")

    def test_glob_over_a_folder_holding_harness_json_asks(self):
        touch(self.path("proj", ".claude", "harness.json"), self.path("proj", "src", "a.xml"))
        self.expect(["rm -rf proj/*", "rm -rf proj/.*", "chmod 644 .claude/*",
                     "rm .claude/*.json", "rm -f proj/.claude/*", "rm -rf *"], "ask")

    def test_glob_inside_a_config_folder_asks_even_when_empty(self):
        """`.claude/*.json` with no file yet: a protected config could appear there."""
        verdict, reason = decide("rm -f .claude/*.json", self.cwd)
        self.assertEqual(verdict, "ask")
        self.assertIn("the folder .claude can hold", reason)

    def test_link_to_a_protected_config_asks(self):
        touch(self.path("app", "lint.xml"))
        os.symlink(self.path("app", "lint.xml"), os.path.join(self.out, "l.xml"))
        self.expect(["chmod 644 build/out/*.xml"], "ask")

    def test_link_to_a_folder_asks(self):
        os.makedirs(self.path("app"))
        os.symlink(self.path("app"), os.path.join(self.out, "linked"))
        self.expect(["rm -rf build/out/*"], "ask")

    def test_folder_that_is_a_link_to_the_config_folder_asks(self):
        os.symlink(self.path(".claude"), self.path("cfg"))
        self.expect(["chmod 644 cfg/*", "rm -f cfg/*"], "ask")

    def test_hard_link_to_a_protected_config_asks(self):
        touch(self.path("app", "lint.xml"))
        os.link(self.path("app", "lint.xml"), os.path.join(self.out, "h.xml"))
        verdict, reason = decide("chmod 777 build/out/*.xml", self.cwd)
        self.assertEqual(verdict, "ask")
        self.assertIn("other hard links", reason)

    def test_project_pattern_matching_the_glob_itself_asks(self):
        self.config(protectedConfigs=["**/*.xml"])
        self.expect(["rm -f build/out/*.txt"], "allow")
        self.expect(["rm -f build/out/*.xml", "rm -f build/missing-*.xml"], "ask")

    def test_case_variant_glob_follows_the_filesystem(self):
        """On macOS (case-insensitive) `*.XML` reaches lint.xml, so it asks; on a case-sensitive
        filesystem the shell cannot match it, so it passes."""
        touch(os.path.join(self.out, "lint.xml"))
        expected = "ask" if sys.platform == "darwin" else "allow"
        self.expect(["rm -f build/out/*.XML", "rm -f build/out/LINT.*"], expected)

    # --- fail closed: the expansion is not certain ---------------------------------------------

    def test_variable_tilde_and_substitution_ask(self):
        self.expect(["rm -f $OUT/*.xml", 'rm -f "$OUT"/*.xml', "rm -f ${OUT}/*.xml",
                     "rm -f ~/build/out/*.xml", "rm -f $(pwd)/build/out/*.xml",
                     "rm -f `pwd`/build/out/*.xml", "rm -f build/out/*.xml $(./gen.sh)",
                     "echo $(./gen.sh); rm -f build/out/*.xml",
                     "rm -f build/out/*.xml # cleanup"], "ask")

    def test_double_star_asks(self):
        self.expect(["rm -f build/**/*.xml", "rm -f build/out/**"], "ask")

    def test_brace_and_bracket_ask(self):
        self.expect(["rm -f build/out/{a,b}*.xml", "rm -f build/{out,res}/*.xml",
                     "rm -f build/out/{l,x}*.xml", "rm .claude/{harness,x}.json",
                     "rm -f build/out/{a..z}int.xml", "rm -f build/out/[a-z]*.xml"], "ask")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can list anything")
    def test_unreadable_folder_asks(self):
        locked = self.path("build", "locked")
        os.makedirs(locked)
        os.chmod(locked, 0)
        try:
            verdict, reason = decide("rm -f build/locked/*.xml", self.cwd)
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual(verdict, "ask")
        self.assertIn("could not list build/locked", reason)

    def test_missing_folder_passes(self):
        """Nothing can match in a folder that does not exist; the literal name is checked."""
        self.expect(["rm -f build/nothing/*.xml", "rm -f nothing/deeper/*.xml"], "allow")

    def test_command_placing_files_in_the_folder_or_a_parent_asks(self):
        """Owner decision 2026-11-25: another command that could put files into, or rename files
        in, the glob's folder or a parent of it, before or after the glob, asks."""
        touch(self.path("app", "lint.xml"))
        self.expect(["touch build/out/new.xml && rm -f build/out/*.xml",
                     "cp /tmp/x.xml build/out/ ; rm -f build/out/*.xml",
                     "cp app/lint.xml build/out/x.xml; chmod 644 build/out/*.xml",
                     "mv build/a build/out && rm -f build/out/*.xml",
                     "mv app build/ && rm -rf build/out/*", "cp -R app . ; rm -f build/out/*.xml",
                     "rm -f build/out/*.xml; mv build/out/a.xml build/out/c.xml",
                     "ln -s ../../app/lint.xml build/out/l.xml; chmod 644 build/out/*.xml",
                     "install -m 644 app/lint.xml build/out/; rm -f build/out/*.xml",
                     "rsync -a app/ build/out/ && rm -f build/out/*.xml",
                     "unzip -o r.zip -d build/out && rm -f build/out/*.xml",
                     "tar xf r.tar -C build/out; rm -f build/out/*.xml",
                     "tar xf r.tar; rm -f build/out/*.xml", "unzip r.zip; rm -f build/out/*.xml",
                     "git checkout main && rm -f build/out/*.xml",
                     "git restore . && rm -f build/out/*.xml", "git stash pop; rm -f build/out/*.xml",
                     "echo x > build/out/new.txt; rm -f build/out/*.xml",
                     "echo x | tee build/out/new.txt; rm -f build/out/*.xml",
                     "sed -i '' s/a/b/ build/out/a.xml; rm -f build/out/*.xml",
                     "dd if=/dev/zero of=build/out/z.xml count=1; rm -f build/out/*.xml",
                     "sudo cp app/lint.xml build/out/; rm -f build/out/*.xml",
                     "cd ./build && cp ../app/lint.xml out/; rm -f out/*.xml"], "ask")

    def test_command_whose_target_cannot_be_read_asks(self):
        self.expect(["cp app/x $DEST; rm -f build/out/*.xml",
                     "cp app/x build/*/; rm -f build/out/*.xml",
                     "echo x > $LOG; rm -f build/out/*.xml",
                     "bash gen.sh; rm -f build/out/*.xml", "sh -c 'x'; rm -f build/out/*.xml",
                     "eval x; rm -f build/out/*.xml", "ls | xargs echo; rm -f build/out/*.xml",
                     "find app -name x -exec cp {} build/out \\; ; rm -f build/out/*.xml",
                     "source env.sh; rm -f build/out/*.xml", ". ./env.sh; rm -f build/out/*.xml",
                     "python3 -c 'print(1)'; rm -f build/out/*.xml",
                     "python3 - <<'EOF'\nprint(1)\nEOF\nrm -f build/out/*.xml",
                     "python3 <<'EOF'\nprint(1)\nEOF\nrm -f build/out/*.xml",
                     "bash <<'EOF'\n./gen.sh\nEOF\nrm -f build/out/*.xml",
                     "node -e 'x'; rm -f build/out/*.xml", "shopt -s dotglob; rm -f build/out/*",
                     "set -f; rm -f build/out/*.xml", "command cd /; rm -f build/out/*.xml",
                     "git -C app pull; rm -f build/out/*.xml"], "ask")

    def test_listed_commands_in_the_line_pass(self):
        """A build, a test run, a git read or a reader elsewhere in the line does not stop the
        expansion: the guard protects the configs that exist when the command starts, which the
        listing sees (owner decision 2026-11-25; allow-lists from review round 2)."""
        self.expect(["make; rm -f build/out/*.xml", "npm test; rm -f build/out/*.xml",
                     "npm run lint && rm -f build/out/*.xml", "pnpm install; rm -f build/out/*.xml",
                     "cargo test; rm -f build/out/*.xml", "go test ./...; rm -f build/out/*.xml",
                     "swift build && rm -f build/out/*.xml",
                     "xcodebuild -scheme App test > /tmp/b.log; rm -f build/out/*.xml",
                     "gradle check; rm -f build/out/*.xml", "pytest -q; rm -f build/out/*.xml",
                     "rm -f build/out/*.xml | head -3",
                     "./gradlew test > /tmp/x.log 2>&1; echo $?; rm -f build/out/*.xml",
                     "python3 -m pytest -q; rm -f build/out/*.xml",
                     "python3 -m unittest discover; rm -f build/out/*.xml",
                     "echo x >> build/x.log; rm -f build/out/*.xml",
                     "git status && git diff; rm -f build/out/*.xml",
                     "git log --oneline -3 || true; rm -f build/out/*.xml",
                     "grep -r x src | head; rm -f build/out/*.xml",
                     "sort -u a.txt | uniq -c; rm -f build/out/*.xml",
                     "printf '%s\\n' x; rm -f build/out/*.xml",
                     "rm -rf build/res\nrm -f build/out/*.xml"], "allow")

    def test_unlisted_commands_in_the_line_ask(self):
        """Every other command must be on the allow-lists; anything else asks."""
        self.expect(["./gen.sh; rm -f build/out/*.xml", "rm -f build/out/*.xml; ./gen.sh",
                     "rm -f build/out/*.xml & echo x", "python3 tools/gen.py; rm -f build/out/*.xml",
                     "cp app/x.txt docs/; rm -f build/out/*.xml",
                     "git add -A && git commit -m x; rm -f build/out/*.xml",
                     "while true; do echo; done; rm -f build/out/*.xml",
                     "npx x; rm -f build/out/*.xml", "yarn; rm -f build/out/*.xml",
                     "go run .; rm -f build/out/*.xml", "make -e; rm -f build/out/*.xml",
                     "pytest -c x.ini; rm -f build/out/*.xml",
                     "go test -exec ./x ./...; rm -f build/out/*.xml",
                     "git -C ../other status; rm -f build/out/*.xml",
                     "git diff --output=build/out/d.txt; rm -f build/out/*.xml",
                     "sort -o build/out/s.txt a.txt; rm -f build/out/*.xml",
                     "sort --compress-program=./x a.txt; rm -f build/out/*.xml",
                     "uniq a.txt build/out/u.txt; rm -f build/out/*.xml",
                     "printf -v X y; rm -f build/out/*.xml", "rg --pre ./x pat; rm -f build/out/*.xml",
                     "/usr/bin/git status; rm -f build/out/*.xml",
                     "pushd ./build && rm -f out/*.xml", "(cd ./build; rm -f out/*.xml)",
                     "echo x > build/out/n.txt; rm -f build/out/*.xml",
                     "echo x > build/n.txt; rm -rf build/*",
                     "echo x > $LOG; rm -f build/out/*.xml",
                     "cat <<EOF > notes.txt\n$X\nEOF\nrm -f build/out/*.xml",
                     # a heredoc body read as commands asks even when its lines are listed words
                     # (delimiter `true` so that no line of it is an unlisted word)
                     "pytest -q <<true\necho $X\ntrue\nrm -f build/out/*.xml"], "ask")

    def test_review_round_2_compound_cd_asks(self):
        """Finding 1: a cd that is not the first word of a simple command is not followed."""
        touch(self.path("app", "lint.xml"))
        self.expect(["if true; then cd app; fi; rm -f *.xml",
                     "while true; do cd app; break; done; rm -f *.xml",
                     "until false; do cd app; break; done; rm -f *.xml",
                     "for i in 1; do cd app; done; rm -f *.xml",
                     "select x in a; do cd app; break; done; rm -f *.xml",
                     "case x in x) cd app;; esac; rm -f *.xml", "{ cd app; }; rm -f *.xml",
                     "X=1 cd app; rm -f *.xml", "time cd app; rm -f *.xml",
                     "time -p cd app; rm -f *.xml", "! cd app; rm -f *.xml",
                     "[[ 1 ]] && { cd app; }; chmod 777 *.xml", "coproc cd app; rm -f *.xml"],
                    "ask")

    def test_review_round_2_unlisted_runners_ask(self):
        """Finding 2: runners whose writes cannot be read ask (allow-list, not a deny-list)."""
        touch(self.path("app", "lint.xml"))
        os.makedirs(self.path("build", "out", "app"))
        self.expect(["git -c core.fsmonitor='mv app build/out/;false' status; "
                     "rm -f build/out/*/*.xml",
                     "git -c diff.external=./x diff; rm -f build/out/*/*.xml",
                     "git --config-env=a.b=C status; rm -f build/out/*/*.xml",
                     "awk 'BEGIN{system(\"mv app build/out/\")}'; rm -f build/out/*/*.xml",
                     "sed -n '1e mv app build/out/' x; rm -f build/out/*/*.xml",
                     "env -S 'mv app build/out/'; rm -f build/out/*/*.xml",
                     "watch 'mv app build/out/'; rm -f build/out/*/*.xml",
                     "fish -c 'mv app build/out/'; rm -f build/out/*/*.xml",
                     "csh -c 'mv app build/out/'; rm -f build/out/*/*.xml"], "ask")

    def test_review_round_2_dotdot_after_a_wildcard_asks(self):
        """Finding 3: the shell lists a different folder than the fixed part of the word."""
        touch(self.path("app2", "app", "a.xml"))  # clean: only the .. part makes these ask
        self.expect(["rm -f build/*/../../app2/*/*.xml", "rm -f build/../app2/app/*.xml",
                     "echo x > app2/app/n.txt; rm -f build/*/../../app2/app/*.xml"], "ask")
        touch(self.path("app2", "app", "lint.xml"))
        self.expect(["mv app app2/ && rm -f build/*/../../app2/*/*.xml"], "ask")

    def test_review_round_2_option_attached_folder_asks(self):
        """Finding 4: a placing command asks whatever its argument spelling."""
        touch(self.path("app", "lint.xml"))
        self.expect(["mv -tbuild/out app; rm -f build/out/*/*.xml",
                     "cp -rtbuild/out app; rm -f build/out/*/*.xml",
                     "cp -r --target-directory=build/out app; rm -f build/out/*/*.xml",
                     "ln -sfnt build/out ../app; chmod 777 build/out/*/*.xml"], "ask")

    def test_review_round_4_runner_options_and_stdin_ask(self):
        """Round 3 finding 1 and the owner's rule: build runners take plain words, options only
        from an exact list, and no stdin."""
        touch(self.path("app", "lint.xml"))
        os.makedirs(self.path("build", "out", "app"))
        tail = "; rm -f build/out/*/*.xml"
        self.expect([c + tail for c in [
            "echo 'x: ; mv app build/out/' | make -f - x",
            "make -f - x <<< 'x: ; mv app build/out/'",
            "go build -toolexec='sh -c \"mv app build/out/\"' .",
            "make -f /dev/stdin x", "make -E'x: ; mv app build/out/' x",
            "make --eva=x x", "go test -toolexec=./x ./...",
            "cargo test --config 'target.x.runner=[\"./x\"]'",
            # variants
            "make -f Makefile.other x", "make -C ../other", "make -I inc x", "make FOO=bar x",
            "./gradlew -p ../other build", "./gradlew --init-script x.gradle build",
            "./gradlew -Dorg.x=y build", "./gradlew -Pfoo build", "./gradlew -b x.gradle build",
            "./gradlew --console=plain build", "./gradlew build /dev/fd/0",
            "cat x | ./gradlew build", "./gradlew build < /dev/null",
            "npm run x -- --y", "cargo test --manifest-path x/Cargo.toml",
            "go build -o build/out/x .", "xcodebuild -xcconfig x.xcconfig build",
            "pytest --rootdir=x", "python3 -m pytest -p plugin", "pytest -q -",
            "make -f /proc/self/fd/0 x", "pytest 'a b'"]] + [
            "pytest -q <<'EOF'\nx\nEOF\nrm -f build/out/*/*.xml"], "ask")

    def test_recheck_package_install_and_run_ask(self):
        """Recheck of 654b3cf: `npm install ./app` links a folder into node_modules, and yarn or
        pnpm `run` falls back to any installed CLI. install/ci/i count only with no word after
        them; yarn and pnpm `run` are not listed."""
        touch(self.path("app", "lint.xml"))
        os.makedirs(self.path("node_modules", "x"))
        os.makedirs(self.path("build", "out", "app"))
        self.expect(["npm install ./app && chmod 777 node_modules/*/*.xml",
                     "echo '{\"name\":\"app\",\"version\":\"1.0.0\"}' > app/package.json; "
                     "npm install ./app; rm -f node_modules/*/*.xml",
                     "npm install file:app; rm -f node_modules/*/*.xml",
                     "pnpm install ./app; rm -f node_modules/*/*.xml",
                     "npm install github:someone/pkg; rm -f build/out/*.xml",
                     "yarn run shx mv app build/out; rm -f build/out/*/*.xml",
                     "yarn run cpy app build/out; rm -f build/out/*/*.xml",
                     # variants
                     "npm i ./app; rm -f node_modules/*/*.xml",
                     "npm ci app; rm -f node_modules/*/*.xml",
                     "yarn install app; rm -f node_modules/*/*.xml",
                     "pnpm run build; rm -f build/out/*.xml",
                     "yarn run build; rm -f build/out/*.xml"], "ask")

    def test_recheck_plain_npm_forms_pass(self):
        tail = "; rm -f build/out/*.xml"
        self.expect([c + tail for c in ["npm install", "npm ci", "npm test", "npm run build",
                                        "npm i", "pnpm install", "yarn install", "yarn test"]],
                    "allow")

    def test_review_round_4_git_options_ask(self):
        """Round 3 finding 2: git reads take plain words and options from an exact list, so a
        shortened long option cannot slip through."""
        tail = "; rm -f build/out/*.xml"
        self.expect([c + tail for c in [
            "git grep --open='mv app build/out/;:' x", "git grep --open-files-in=./x x",
            "git diff --outp=build/out/x.xml", "git diff --output=build/out/x.xml",
            "git grep -O x", "git log --format=%H", "git diff --ext-diff",
            "git show HEAD~1", "git log -p --textconv", "echo x | git log --stdin",
            "git diff -", "git diff --no-index a b"]], "ask")

    def test_review_round_4_uniq_dash_is_a_file_asks(self):
        """Round 3 finding 3: `uniq - out` writes out."""
        self.expect(["uniq - build/out/n.xml; rm -f build/out/*.xml",
                     "uniq -c - build/out/n.xml; rm -f build/out/*.xml"], "ask")

    def test_review_round_4_plain_runner_forms_pass(self):
        tail = "; rm -f build/out/*.xml"
        self.expect([c + tail for c in [
            "./gradlew --stacktrace --offline :app:testDebugUnitTest -q",
            "make -j 4 test", "make -j4 -k test", "cargo test --release --no-fail-fast",
            "go test -v -run TestX ./...", "swift test --parallel", "npm ci",
            "python3 -m unittest discover -s tests -v", "python3 -m pytest -q tests/a.py::T::t",
            "xcodebuild -scheme App -configuration Debug test", "git status --porcelain",
            "git log -n 5 --oneline", "git log -3 --stat -- src",
            "git diff --cached --name-only", "git rev-parse --show-toplevel",
            "uniq -c a.txt"]], "allow")

    def test_review_round_2_cdpath_spellings_ask(self):
        """Finding 5: CDPATH named with quotes or backslashes, in any letter case."""
        self.expect(['export C""DPATH=build; cd out; rm -f *.xml',
                     "declare -x C\\DPATH=build; cd out; rm -f *.xml",
                     "export 'C'DPATH=build; cd out; rm -f *.xml",
                     'read C""DPATH <<< build; cd out; rm -f *.xml',
                     'printf -v CD""PATH build; cd out; rm -f *.xml',
                     "echo cdpath; cd build && rm -f out/*.xml",
                     "CDPATH=build cd out; rm -f *.xml"], "ask")

    def test_reported_command(self):
        """The command that prompted CH-31: passes with no protected file in that folder, asks
        with a lint.xml there (owner decision 2026-11-25)."""
        cmd = ("./gradlew testDebugUnitTest assembleDebug > /tmp/x.log 2>&1; echo $?; "
               "rm -f app/build/test-results/testDebugUnitTest/*.xml")
        results = self.path("app", "build", "test-results", "testDebugUnitTest")
        touch(os.path.join(results, "TEST-a.xml"), os.path.join(results, "TEST-b.xml"))
        self.assertEqual(decide(cmd, self.cwd), ("allow", ""))
        touch(os.path.join(results, "lint.xml"))
        verdict, reason = decide(cmd, self.cwd)
        self.assertEqual(verdict, "ask")
        self.assertIn("rm: `rm -f app/build/test-results/testDebugUnitTest/*.xml` reaches "
                      "app/build/test-results/testDebugUnitTest/lint.xml, which matches "
                      "**/lint.xml", reason)

    def test_shell_tricks_and_unknown_folders_ask(self):
        self.expect(["for i in 1 2; do rm -f build/out/*.xml; done",
                     "if true; then rm -f build/out/*.xml; fi", "{ rm -f build/out/*.xml; }",
                     "f() { rm -f build/out/*.xml; }; ./gen.sh; f",
                     "rm() { ./myrm \"$@\"; }; rm -f build/out/*.xml",
                     "function rm { ./myrm; }; rm -f build/out/*.xml",
                     "alias rm='rm -i'; rm -f build/out/*.xml",
                     "PATH=./bin; rm -f build/out/*.xml", "cd - && rm -f build/out/*.xml",
                     "cd ./x/../build && rm -f out/*.xml", "cd && rm -f build/out/*.xml",
                     "cd $D && rm -f build/out/*.xml", "cd ./nothere && rm -f *.xml",
                     "cd build out && rm -f *.xml", "popd; rm -f build/out/*.xml",
                     "cd +1 && rm -f build/out/*.xml", "cd -2 && rm -f build/out/*.xml"], "ask")

    def test_cd_to_a_plain_name_follows_unless_cdpath_is_set(self):
        """Owner decision 2026-11-25: `cd app` is followed when CDPATH is unset or empty."""
        cmd = "cd build && rm -f out/*.xml"
        self.assertEqual(decide(cmd, self.cwd)[0], "allow")
        self.assertEqual(decide(cmd, self.cwd, env={"CDPATH": ""})[0], "allow")
        verdict, reason = decide(cmd, self.cwd, env={"CDPATH": "/tmp"})
        self.assertEqual(verdict, "ask")
        self.assertIn("CDPATH is set", reason)
        self.assertEqual(decide("CDPATH=/tmp; cd build && rm -f out/*.xml", self.cwd)[0], "ask")
        self.assertEqual(decide("export CDPATH=/tmp; cd build && rm -f out/*.xml",
                                self.cwd)[0], "ask")
        self.assertEqual(decide("cd ./build && rm -f out/*.xml", self.cwd,
                                env={"CDPATH": "/tmp"})[0], "allow")
        touch(self.path("build", "out", "lint.xml"))
        self.assertEqual(decide(cmd, self.cwd)[0], "ask")

    def test_outside_the_project_asks(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        touch(os.path.join(other, "a.xml"))
        verdict, reason = decide(f"rm -f {other}/*.xml", self.cwd)
        self.assertEqual(verdict, "ask")
        self.assertIn("outside the working directory and project roots", reason)
        self.expect(["rm -f ../*.xml"], "ask", cwd=self.out)

    def test_over_the_cap_asks(self):
        many = self.path("build", "many")
        touch(*(os.path.join(many, f"r{i}.xml") for i in range(250)))
        verdict, reason = decide("rm -f build/many/*.xml", self.cwd)
        self.assertEqual(verdict, "ask")
        self.assertIn("more than 200", reason)
        deep = self.path("build", "deep")
        touch(*(os.path.join(deep, "d", f"r{i}.txt") for i in range(250)))
        self.expect(["rm -rf build/deep/*"], "ask")

    def test_glob_not_given_to_a_plain_file_command_asks(self):
        self.expect(["find build -name '*.xml' -delete", "find build -name *.xml -delete",
                     "ls build/out/*.xml | xargs rm", "sudo rm -f build/out/*.xml",
                     "env rm -f build/out/*.xml", "X=1 rm -f build/out/*.xml",
                     "bash -c 'rm -f build/out/*.xml'", "eval rm -f build/out/*.xml",
                     "./rm -f build/out/*.xml", "sed -i '' s/a/b/ build/out/*.xml",
                     "rsync -a build/out/*.xml dest/", "echo x > build/out/*.xml",
                     "echo x | tee build/out/*.xml", "dd if=/dev/zero of=build/out/*.xml"], "ask")

    def test_glob_joined_to_other_shell_text_asks(self):
        self.expect(["rm -f 'build/out/*.xml'", "rm -f build/out/*.xml(D)",
                     "rm -f build/out/\\*.xml", "rm -f x\\ build/out/*.xml",
                     "rm -f build/out/*.xml build/out/*.xml$X",
                     "rm -f build/out/*.xml\r"], "ask")

    def test_odd_file_names_in_the_folder_ask(self):
        touch(os.path.join(self.out, "-rf.xml"))
        self.expect(["rm -f *.xml"], "ask", cwd=self.out)
        self.expect(["rm -f build/out/*.xml"], "allow")  # `build/out/-rf.xml` is not an option
        touch(os.path.join(self.out, "*.xml"))
        self.expect(["rm -f build/out/*.xml"], "ask")

    def test_prompt_names_the_verb_and_the_pattern(self):
        for cmd, verb, pattern in [
                ("rm -f build/**/*.xml", "rm", "**/lint.xml"),
                ("chmod 600 .claude/*", "chmod", "**/.claude/"),
                ("find . -name '*.xml' -delete", "find", "**/lint.xml"),
                ("rm .claude/harness.json", "rm", "**/.claude/harness.json"),
                ("echo x > .claude/settings.json", "redirect", "**/.claude/settings.json")]:
            with self.subTest(cmd=cmd):
                verdict, reason = decide(cmd, self.cwd)
                self.assertEqual(verdict, "ask")
                self.assertIn(f"protected config: {verb}: `", reason)
                self.assertIn(pattern, reason)
                self.assertIn(f"Command: {cmd}", reason)


if __name__ == "__main__":
    unittest.main()
