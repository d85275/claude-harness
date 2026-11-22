"""Tests for hooks/scripts/guard.py. Run: python3 -m unittest discover -s tests"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

GUARD = os.path.join(os.path.dirname(__file__), "..", "hooks", "scripts", "guard.py")


def run(mode, tool_input, cwd, env=None):
    payload = json.dumps({"tool_input": tool_input, "cwd": cwd})
    proc = subprocess.run([sys.executable, "-I", GUARD, mode], input=payload,
                          capture_output=True, text=True, timeout=20,
                          env={**{k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"},
                               **(env or {})})
    if proc.returncode == 2:
        return "block"
    if proc.returncode == 0 and '"ask"' in proc.stdout:
        return "ask"
    if proc.returncode == 0:
        return "allow"
    raise AssertionError(f"unexpected exit {proc.returncode}: {proc.stderr}")


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        os.makedirs(os.path.join(self.cwd, ".claude"))

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **cfg):
        with open(os.path.join(self.cwd, ".claude", "harness.json"), "w") as f:
            json.dump(cfg, f)

    def bash(self, command):
        return run("bash", {"command": command}, self.cwd)


class BashTests(GuardTestCase):
    def test_credential_reads_are_blocked(self):
        for cmd in ["cat keys/AuthKey_ABC.p8", "cp release.keystore /tmp/",
                    "grep token backend/service-account.json", "head .env"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "block")

    def test_listing_credential_paths_is_allowed(self):
        self.assertEqual(self.bash("ls keys/"), "allow")
        self.assertEqual(self.bash("cat .env.example"), "allow")

    def test_project_protected_paths(self):
        self.config(protectedPaths=["config/secret-*.json"])
        self.assertEqual(self.bash("cat config/secret-prod.json"), "block")

    def test_outward_facing_commands_ask(self):
        for cmd in ["git push origin HEAD", "git -C repo push", "gh pr create --fill",
                    "firebase deploy --only functions", "terraform apply"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "ask")

    def test_routine_commands_are_allowed(self):
        for cmd in ["git status --short", "git log --oneline -3", "gh pr view 12",
                    "firebase emulators:start --project demo-x", "xcodebuild -list"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "allow")

    def test_project_ask_commands(self):
        self.config(askCommands=["./scripts/deploy.sh"])
        self.assertEqual(self.bash("./scripts/deploy.sh prod"), "ask")


GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True, env=GIT_ENV, timeout=20)


def commit_all(cwd):
    """Make cwd a git repository with everything in it committed (local only, no remote)."""
    git(cwd, "init", "-q")
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "--allow-empty", "-m", "test")


# Publishing commands in the forms the detection must keep catching. The guard only reads
# these strings; nothing here is executed.
PUBLISH_COMMANDS = [
    "git push", "git push origin HEAD", "git -C dir push", "git -c x=y push",
    "gh pr create --fill", "gh pr merge 12 --squash", "gh repo create demo --private",
    "gh repo delete demo --yes", "gh release create v1.0.0",
    # gh aliases and global options
    "gh pr new --fill", "gh release new v1.0", "gh repo new demo --private", "gh gist create a.txt",
    "gh gist new a.txt", "gh -R o/r pr create", "gh --repo o/r pr create", "gh --repo=o/r pr new",
    "gh --hostname example.com pr create", "GH_REPO=o/r gh pr create", "gh  pr  create",
    # chained, piped, subshells
    "git add -A && git push", "true; git push", "true;git push", "echo y | git push",
    "(git push)", "echo $(git push)", "git commit -m x || git push -f", "git push&&echo ok",
    "nohup git push &", "xargs -n1 git push <<< origin", "(cd repo && exec git push)",
    "bash <<'EOF'\ngit push\nEOF",
    # prefixes and option forms
    "env GIT_TRACE=1 git push", "command git push", "/usr/bin/git push", "sudo git push",
    "git -c x=y -C dir push origin", "git --git-dir=.git --work-tree . push",
    "git -c 'user.name=a b' push", "git --no-pager -c x=y push", "sh -c 'git push'",
    "timeout 60 git push", "builtin git push", "command -p git push", "\\git push",
    "git -c x=y --git-dir .git push", "git --config-env a=B push", "git -C \"my dir\" push",
    # line continuations, comments, quoting
    "git \\\n  push origin HEAD", "git -C dir \\\n  push", "git push \\\n  origin HEAD",
    "git -c \"user.name=A B\" push # don't", "'git' push # it's fine", "git -C dir push; # it's",
    "curl http://x/#frag && git push", "echo a#b; git push", "git $'push'", "git $'\\x70ush'",
    "'git' 'push'", "\"git\" push", "g\\it push", "git p\\ush", "GIT push", "Git Push",
    "/usr/libexec/git-core/git-push origin", "GH pr create",
    # unbalanced quote: shell words fail, the regexes and the rough split still apply
    "git push \"unterminated",
    # review round 2: text the scanner once dropped (nested quotes in $(...), heredocs that run,
    # arithmetic <<), and the heredoc-to-shell forms
    'git commit -m "$(echo "Closes #12")" && git push origin HEAD',
    "x=$((1<<2))\ngit push origin HEAD", "echo $((1 << 3))\ngit push origin HEAD\necho done",
    "cat <<'EOF' | bash\ngit push origin HEAD\nEOF", "cat <<'EOF' | sh -s\ngit push origin HEAD\nEOF",
    "cat <<EOF | bash -s\ngh pr create --fill\nEOF", "source /dev/stdin <<'EOF'\ngit push\nEOF",
    "bash /dev/stdin <<'EOF'\ngit push\nEOF", "cat <<'EOF' | python3\nimport os; os.system('git push')\nEOF",
    "cat <<-EOF | bash\n\tgit push\n\tEOF", "ssh host <<'EOF'\ngit push\nEOF",
    "git commit -F - <<'EOF'\nmsg\nEOF && git push",  # unterminated: not counted as data
    "git commit -F - <<EOF\n$(git push)\nEOF",  # unquoted body expands $(...)
    "git commit -F - <<'EOF' | bash\ngit push\nEOF",
    "python3 -c 'import subprocess; subprocess.run([\"git\",\"push\"])'", "echo push | xargs git",
]
DEPLOY_COMMANDS = ["firebase deploy --only functions", "terraform apply", "terraform destroy",
                   "kubectl delete pod demo", "gcloud run deploy demo"]


class PushPolicyTests(GuardTestCase):
    def check(self, policy, commands, expected):
        if policy is not None:
            self.config(pushPolicy=policy)
        if policy == "allow":
            commit_all(self.cwd)  # "allow" only counts from a committed config
        for cmd in commands:
            with self.subTest(policy=policy, cmd=cmd):
                self.assertEqual(self.bash(cmd), expected)

    def test_ask_prompts_for_every_publishing_form(self):
        self.check("ask", PUBLISH_COMMANDS, "ask")

    def test_deny_blocks_every_publishing_form(self):
        self.check("deny", PUBLISH_COMMANDS, "block")

    def test_allow_leaves_publishing_to_permission_rules(self):
        self.check("allow", PUBLISH_COMMANDS, "allow")

    def test_default_is_ask_without_any_config(self):
        with tempfile.TemporaryDirectory() as bare:  # no .claude folder at all
            for cmd in ["git push", "git -C dir push", "gh pr create", "gh pr merge 3"]:
                with self.subTest(cmd=cmd):
                    self.assertEqual(run("bash", {"command": cmd}, bare), "ask")
        self.check(None, ["git push"], "ask")  # empty .claude folder, no harness.json

    def test_deny_exits_2_and_says_the_owner_pushes(self):
        self.config(pushPolicy="deny")
        proc = subprocess.run([sys.executable, "-I", GUARD, "bash"],
                              input=json.dumps({"tool_input": {"command": "git push"},
                                                "cwd": self.cwd}),
                              capture_output=True, text=True, timeout=20,
                              env={k: v for k, v in os.environ.items()
                                   if k != "CLAUDE_PROJECT_DIR"})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("owner pushes by hand", proc.stderr)
        self.assertNotIn('"ask"', proc.stdout)

    def test_deploys_always_ask_whatever_the_policy(self):
        for policy in ("ask", "deny", "allow"):
            self.check(policy, DEPLOY_COMMANDS, "ask")

    def test_deploy_chained_onto_a_push_still_asks_under_allow(self):
        self.check("allow", ["git push && firebase deploy", "terraform apply; git push"], "ask")
        self.check("deny", ["git push && firebase deploy"], "block")

    def test_ask_commands_always_ask_under_allow(self):
        self.config(pushPolicy="allow", askCommands=["./scripts/deploy.sh", "git push --force"])
        commit_all(self.cwd)
        self.assertEqual(self.bash("./scripts/deploy.sh prod"), "ask")
        self.assertEqual(self.bash("git push --force origin main"), "ask")
        self.assertEqual(self.bash("git push origin main"), "allow")

    def test_allow_does_not_lift_the_credential_guard(self):
        self.check("allow", ["cat .env && git push"], "block")

    def test_non_publishing_commands_are_not_caught(self):
        for policy in ("ask", "deny"):
            self.check(policy, [
                "git status", "git stash push -m wip", "git log --oneline", "gh pr view 12",
                "gh pr list", "gh release view v1", "git fetch origin", "git pull --ff-only",
                "gh pr checks 12", "gh repo view", "gh gist list",
                # a push mentioned only in a heredoc body that is data, not commands
                "git commit -F - <<'EOF'\nfeat: x\n\nAsk before git push; rm the old list.\nEOF",
                "cat > docs/notes.md <<EOF\nrun git push when ready\nEOF",
            ], "allow")

    def test_bad_policy_values_fall_back_to_ask(self):
        for value in ["ALLOW", "yes", "", True, None, 0, ["allow"], {"allow": True}]:
            self.check(value, ["git push"], "ask")

    def test_malformed_config_falls_back_to_ask(self):
        with open(os.path.join(self.cwd, ".claude", "harness.json"), "w") as f:
            f.write('{"pushPolicy": "allow",')  # truncated JSON
        self.assertEqual(self.bash("git push"), "ask")

    def test_nested_config_cannot_weaken_the_root_policy(self):
        os.makedirs(os.path.join(self.cwd, ".git"))
        self.config(pushPolicy="deny")
        sub = os.path.join(self.cwd, "app")
        os.makedirs(os.path.join(sub, ".claude"))
        with open(os.path.join(sub, ".claude", "harness.json"), "w") as f:
            json.dump({"pushPolicy": "allow"}, f)
        self.assertEqual(run("bash", {"command": "git push"}, sub), "block")


class AllowNeedsCommittedConfigTests(GuardTestCase):
    """Under "allow", a publish still asks unless git confirms the config files are unchanged."""

    def setUp(self):
        super().setUp()
        self.config(pushPolicy="allow")

    def test_unchanged_committed_allow_does_not_prompt(self):
        commit_all(self.cwd)
        self.assertEqual(self.bash("git push"), "allow")

    def test_modified_allow_asks(self):
        self.config(pushPolicy="deny")
        commit_all(self.cwd)
        self.config(pushPolicy="allow")  # the attack: switch it on, then push
        self.assertEqual(self.bash("git push"), "ask")

    def test_staged_allow_asks(self):
        self.config(pushPolicy="ask")
        commit_all(self.cwd)
        self.config(pushPolicy="allow")
        git(self.cwd, "add", "-A")
        self.assertEqual(self.bash("git push"), "ask")

    def test_untracked_allow_asks(self):
        git(self.cwd, "init", "-q")
        self.assertEqual(self.bash("git push"), "ask")

    def test_ignored_allow_asks(self):
        with open(os.path.join(self.cwd, ".gitignore"), "w") as f:
            f.write(".claude/\n")
        commit_all(self.cwd)
        self.assertEqual(self.bash("git push"), "ask")

    def test_deleted_stricter_config_asks(self):
        os.makedirs(os.path.join(self.cwd, "app", ".claude"))
        with open(os.path.join(self.cwd, "app", ".claude", "harness.json"), "w") as f:
            json.dump({"pushPolicy": "deny"}, f)
        commit_all(self.cwd)
        os.remove(os.path.join(self.cwd, "app", ".claude", "harness.json"))
        self.assertEqual(self.bash("git push"), "ask")

    def test_assume_unchanged_or_skip_worktree_asks(self):
        # Hides a working-tree change from `git status`; `git ls-files -v` still shows it.
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                self.config(pushPolicy="ask")
                commit_all(self.cwd)
                git(self.cwd, "update-index", flag, ".claude/harness.json")
                self.config(pushPolicy="allow")
                self.assertEqual(self.bash("git push"), "ask")
                git(self.cwd, "update-index", flag.replace("--", "--no-"), ".claude/harness.json")

    def test_git_failure_asks(self):
        self.assertEqual(self.bash("git push"), "ask")  # not a git repository


# Shell writes to a protected config; {p} is the config path. Only read by the guard.
CONFIG_WRITES = [
    # the attack that "allow" made worth closing
    """echo '{"pushPolicy":"allow"}' > {p}""",
    # redirections
    "echo x >> {p}", "echo x >| {p}", "echo x &> {p}", "echo x>{p}", 'echo x > "{p}"',
    "true;>{p}", "printf x 2>{p}", "echo x 1>{p}", "exec 3>{p}", "cat <<EOF > {p}\n{{}}\nEOF",
    "cat <<'EOF' > {p}\n{}\nEOF", "echo x > ./{p}", "echo $'x' > {p}", "echo x | tee >(cat > {p})",
    # tee, in-place editors
    "echo x | tee {p}", "echo x | tee -a {p}", "tee {p} <<'EOF'\n{}\nEOF",
    "sed -i '' 's/a/b/' {p}", "sed -i.bak s/a/b/ {p}", "sed --in-place s/a/b/ {p}",
    "perl -pi -e 's/a/b/' {p}", "ruby -i -pe 'x' {p}",
    # copy, move, link, delete, mode
    "cp /tmp/x {p}", "mv /tmp/x {p}", "install -m 644 /tmp/x {p}", "ln -sf /tmp/x {p}",
    "rsync /tmp/x {p}", "dd if=/tmp/x of={p}", "truncate -s 0 {p}", "rm {p}", "rm -f {p}",
    "unlink {p}", "chmod 000 {p}", "chown nobody {p}", "touch {p}", "git checkout -- {p}",
    "git restore {p}", "git restore --source HEAD~3 -- {p}", "git rm {p}", "ditto /tmp/x {p}",
    "jq . {p} | sponge {p}", "cp /tmp/x {p}.bak && mv {p}.bak {p}",
    "git restore --staged --worktree {p}", "unzip -o /tmp/t.zip {p}", "vim -es -c wq {p}",
    "git update-index --assume-unchanged {p}", "git update-index --skip-worktree {p}",
    # review round 2: heredocs that run, arithmetic <<, nested quotes in $(...)
    "cat <<'EOF' | bash\nrm {p}\nEOF", "cat <<'EOF' | xargs rm -f\n{p}\nEOF",
    "xargs rm -f <<'EOF'\n{p}\nEOF", "n=$((1<<4))\necho x > {p}",
    "git commit -m \"$(printf '%s' 'fix') #12\" && rm {p}", "echo \"$(cat \"a #b.txt\")\" && rm {p}",
    "ed -s {p} <<< $',s/a/b/\\nw'", "gawk -i inplace '{print}' {p}",
    # interpreters: -c/-e code, heredoc bodies, here-strings
    """python3 -c "open('{p}','w').write('x')" """, """node -e "require('fs').writeFileSync('{p}','')" """,
    """ruby -e "File.write('{p}','')" """, """perl -e 'unlink "{p}"'""",
    "python3 - <<'EOF'\nopen('{p}', 'w').write('x')\nEOF", "python3 <<< \"open('{p}','w')\"",
    """osascript -e 'do shell script "rm {p}"'""", """bun -e "Bun.write('{p}','{}')" """,
    """deno eval "Deno.writeTextFileSync('{p}','{}')" """,
    """swift -e 'try! "{}".write(toFile: "{p}", atomically: true, encoding: .utf8)'""",
    # shells, eval, chains, prefixes
    "sh -c 'echo x > {p}'", 'bash -c "rm {p}"', 'bash -lc "cp /tmp/x {p}"', 'eval "rm {p}"',
    "bash <<'EOF'\nrm {p}\nEOF", "cd . && rm {p}", "sudo rm {p}", "env X=1 rm {p}",
    "env -i rm {p}", "sudo -u root touch {p}", "/bin/rm {p}", "echo {p} | xargs rm",
    "rm $(echo {p})", 'rm "$PWD/{p}"', "RM {p}", "rm \\\n  {p}",
    "echo x # it's a comment\nrm {p}", "echo a#b; rm {p}",
    # unbalanced quote: shell words fail, the rough split still applies
    'echo "x > {p}', 'rm {p} "', "echo $'it\\'s' > {p}",
]
# Writes that reach a config through its folder, a glob, or a cd/git -C inside the command.
# {d} is the folder, {f} the file name in it.
CONFIG_TREE_WRITES = [
    "cp /tmp/x {d}/", "mv /tmp/x {d}/", "cp -t {d} /tmp/x", "rsync -a /tmp/cfg/ {d}/",
    "rm -rf {d}", "rm -r {d}/", "sed -i '' s/a/b/ {d}/*", "rm {d}/*", "chmod 644 {d}/*",
    "rm {d}/*.{ext}", "rm {d}/{fq}", "cd {d} && echo x > {f}", "cd {d} && sed -i '' s/a/b/ {f}",
    "(cd {d}; rm {f})", "pushd {d} && rm {f}", "git -C {d} checkout {f}",
    "find {d} -name {f} -delete", "find {d} -name {f} -exec sed -i '' s/a/b/ {{}} +",
    "cp -R /tmp/tpl/{d} .", "rm -rf .", "rm -rf ./", "git checkout -- .",
    "cd -P {d} && rm {f}", "cd -- {d} && rm {f}", "git -C {d}/hooks checkout ../{f}",
    "cp -R /tmp/tpl/. .", "cp -a /tmp/tpl/. ./", "rsync -a /tmp/tpl/ ./", "ditto /tmp/tpl .",
]
# Pure reads of a protected config, everyday commands, and writes elsewhere: none may ask.
CONFIG_READS = [
    "cat {p}", "grep pushPolicy {p}", "jq . {p}", "jq '.pushPolicy' {p}", "git diff {p}",
    "git diff -- {p}", "git log -p -- {p}", "head -5 {p}", "wc -l {p}", "sed -n '1,40p' {p}",
    "dev-harness-config get pushPolicy", "cat {p} > /tmp/copy.json", "cat {p} 2>/dev/null",
    "echo hi > /tmp/out.txt", "cp a.txt b.txt", "rm -f /tmp/scratch.txt", "ls -la .claude",
    "git show HEAD:{p}", "git add {p}", "git log --oneline -5 -- {p}",
    # the reviewer's everyday commands that asked in round 1
    "git restore --staged {p}", "python3 -m pytest -c pytest.ini -q",
    "python3 -m pytest --cov=src --cov-config=.coveragerc",
    "node node_modules/.bin/jest --config jest.config.js", "npx jest --config jest.config.js",
    "python3 -m ruff check --config ruff.toml .", "python3 -m json.tool {p}",
    "cat {p} | python3 -m json.tool", "git log -p -- {p} | tee /tmp/log.txt",
    "mkdir -p build && cp README.md build/ && cat .swiftlint.yml",
    "git add .swiftlint.yml && git commit -m \"Install swiftlint rule\"",
    "git commit -m \"fix: rm duplicate rule\" -- .swiftlint.yml",
    "swiftlint lint --config .swiftlint.yml", "ls -la .claude && rm -rf build",
    "rm -rf node_modules && npm ci", "touch docs/tickets/CH-04.md", "git checkout main",
    "git checkout -b feature/ch-04", "mv docs/a.md docs/done/", "echo 'a#b' > /tmp/x",
    "git commit -F - <<'EOF'\nfix: rm and cp the {p} notes\nEOF",
    "cd a && cd b && cd c && cd d && cd e && cd f && rm g", "cp -R src/. build/",
    "rsync -a src/ build/", "cat > notes.md <<'EOF'\nrm {p}\nEOF",
]
CONFIG_PATHS = [".claude/harness.json", ".claude/settings.json", "scripts/verify_thresholds.py"]


class ConfigWriteTests(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.config(protectedConfigs=["scripts/verify_thresholds.py"])

    def expect(self, commands, expected, cwd=None):
        for cmd in commands:
            with self.subTest(cmd=cmd):
                self.assertEqual(run("bash", {"command": cmd}, cwd or self.cwd), expected)

    def test_shell_writes_to_protected_configs_ask(self):
        self.expect([form.replace("{p}", path) for path in CONFIG_PATHS for form in CONFIG_WRITES],
                    "ask")

    def test_writes_through_folders_globs_and_cd_ask(self):
        cmds = []
        for path in CONFIG_PATHS:
            d, f = path.rsplit("/", 1)
            fq = f[:-2] + "?" + f[-1]  # harness.js?n
            for form in CONFIG_TREE_WRITES:
                cmds.append(form.format(d=d, f=f, fq=fq, ext=f.rsplit(".", 1)[1]))
        self.expect(cmds, "ask")

    @unittest.skipUnless(sys.platform == "darwin", "case-insensitive filesystem")
    def test_case_variants_ask_on_macos(self):
        self.expect(["echo x > .claude/HARNESS.json", "echo x > .Claude/harness.json",
                     "rm SCRIPTS/Verify_Thresholds.py"], "ask")

    def test_absolute_path_and_settings_local(self):
        self.expect([f"rm {self.cwd}/.claude/harness.json",
                     "echo '{}' > .claude/settings.local.json",
                     "echo x >| .claude/settings.local.json"], "ask")

    def test_reads_and_everyday_commands_pass(self):
        self.expect([form.replace("{p}", path) for path in CONFIG_PATHS for form in CONFIG_READS],
                    "allow")

    def test_interpreter_code_naming_a_config_still_asks(self):
        # The code may write; the guard cannot tell a read from a write in code text.
        self.expect(["""python3 -c "import json; print(json.load(open('.claude/harness.json')))" """,
                     "python3 - <<'EOF'\np = '.claude/' + 'harness.json'\nopen(p, 'w')\nEOF",
                     "python3 - <<'EOF'\nimport pathlib\npathlib.Path('.claude', 'harness.json')"
                     ".write_text('{}')\nEOF"], "ask")

    def test_matches_relative_to_the_project_root(self):
        os.makedirs(os.path.join(self.cwd, ".git"))
        sub = os.path.join(self.cwd, "app")
        os.makedirs(sub)
        self.expect(["rm ../scripts/verify_thresholds.py", "echo x > ../.claude/harness.json",
                     "rm -rf .."], "ask", cwd=sub)

    def test_order_credential_block_then_push_policy_then_ask(self):
        self.config(pushPolicy="deny")
        self.assertEqual(self.bash("cat .env > .claude/harness.json"), "block")  # credential
        self.assertEqual(self.bash("rm .claude/harness.json && git push"), "block")  # deny first
        self.config(pushPolicy="allow")
        commit_all(self.cwd)
        self.assertEqual(
            self.bash("""echo '{"pushPolicy":"allow"}' > .claude/harness.json && git push"""),
            "ask")

    def test_unreadable_input_asks(self):
        self.expect(["rm .claude/harness.json\x00", "echo x > ~\x00/a", "rm ~\x00"], "ask")


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class CostTests(GuardTestCase):
    """A hook that times out does not block the call, so the check must stay fast, and past
    its work budget it must ask (never allow, never block)."""

    def setUp(self):
        super().setUp()
        self.guard = load_guard()

    def inproc(self, cmd, policy="ask"):
        self.config(pushPolicy=policy)
        g = self.guard
        cfg = g.load_config(self.cwd)
        out = io.StringIO()
        start = time.process_time()  # CPU time: stable under load, unlike wall time
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                g.guard_bash({"tool_input": {"command": cmd}, "cwd": self.cwd}, cfg)
            result = "allow"
        except SystemExit as e:
            result = "block" if e.code == 2 else ("ask" if '"ask"' in out.getvalue() else "allow")
        return result, time.process_time() - start, out.getvalue()

    def assertFast(self, cmd, limit=1.0, allowed=("allow", "ask"), policy="ask"):
        result, seconds, _ = self.inproc(cmd, policy)
        self.assertIn(result, allowed)
        self.assertLess(seconds, limit, f"{seconds:.2f}s for {cmd[:60]!r}")
        return result

    def test_many_eval_words_are_cheap(self):
        body = "\n".join(f"- eval run {i}: eval score improved" for i in range(50))
        for cmd in ["git commit -F - <<'EOF'\nfeat: eval\n\n" + body + "\nEOF",
                    "eval " * 100 + "true", "eval 'eval \"eval x\"' " * 30,
                    "cat > docs/eval.md <<'EOF'\n" + body + "\nEOF\nfirebase deploy"]:
            with self.subTest(cmd=cmd[:40]):
                self.assertFast(cmd)

    def test_large_inputs_finish_or_ask(self):
        msg = "\n".join(f"- rm stale entry {i} from data/file_{i}.txt and push" for i in range(2000))
        code = "\n".join(f"x{i} = open('data/file_{i}.txt').read()  # it's line {i}"
                         for i in range(2000))
        cases = {
            "100KB data heredoc": "git commit -F - <<'EOF'\n" + msg + "\nEOF",
            "100KB python3 - heredoc": "python3 - <<'EOF'\n" + code + "\nEOF",
            "rm -f 5000 paths": "rm -f " + " ".join(f"build/obj{i}/file{i}.o" for i in range(5000)),
            "6000 words": "echo " + " ".join(f"dir{i}/file{i}.txt" for i in range(6000)),
            "2000 KEY=VAL heredoc": "cat > config/app.env <<'EOF'\n"
                                    + "\n".join(f"K_{i}=v_{i}" for i in range(2000)) + "\nEOF",
            "1000 heredocs": "\n".join(f"cat > /tmp/f{i} <<E\nx\nE" for i in range(1000)),
        }
        for name, cmd in cases.items():
            with self.subTest(name=name):
                self.assertGreater(len(cmd), 10000)
                self.assertFast(cmd)

    def test_pathological_regex_inputs(self):
        for cmd in ["git " + "-a " * 40 + "status", "true; git " + "-a " * 40 + "status",
                    "gh " + "-a " * 40 + "status", "git " + "-a " * 40 + "log && git push",
                    "env " + "A=' " * 12000, "sed " * 12000, "gcloud " * 7000,
                    "(" * 5000 + "git push" + ")" * 5000, "$(" * 3000 + "rm x" + ")" * 3000,
                    "<<" * 5000, "$'" * 5000, "#" * 50000, "\\\n" * 20000,
                    "git -a " * 9000, "gh -a " * 10000, "$((" * 5000, "x=$((1<<2)) " * 5000]:
            with self.subTest(cmd=cmd[:30]):
                self.assertFast(cmd, allowed=("allow", "ask", "block"), policy="deny")

    def test_very_long_word_asks_quickly(self):
        # shlex is quadratic in one word's length, so a word over the limit asks instead.
        for policy, cmd in [("deny", "git -C " + "x" * 40000 + " push"),
                            ("ask", 'git commit -m "' + "a " * 20000 + '"'),
                            ("ask", "echo '" + "b" * 40000)]:
            with self.subTest(cmd=cmd[:30]):
                result, seconds, out = self.inproc(cmd, policy)
                self.assertEqual(result, "ask")
                self.assertIn("too large to check", out)
                self.assertLess(seconds, 1.0)

    def test_over_budget_asks_never_blocks_or_allows(self):
        self.guard.CHECK_STEPS = 0
        for policy, cmd in [("deny", "git push"), ("ask", "rm .claude/harness.json"),
                            ("ask", "ls " + " ".join(f"f{i}" for i in range(50)))]:
            with self.subTest(cmd=cmd[:30]):
                result, _, out = self.inproc(cmd, policy)
                self.assertEqual(result, "ask")
                self.assertIn("too large to check", out)


class ReadTests(GuardTestCase):
    def test_reading_credentials_is_blocked(self):
        self.assertEqual(run("read", {"file_path": "/x/AuthKey_1.p8"}, self.cwd), "block")

    def test_reading_source_is_allowed(self):
        self.assertEqual(run("read", {"file_path": "src/main.swift"}, self.cwd), "allow")


class EditTests(GuardTestCase):
    def edit(self, path, old, new):
        return run("edit", {"file_path": path, "old_string": old, "new_string": new}, self.cwd)

    def test_protected_configs_ask(self):
        for path in [".swiftlint.yml", "app/eslint.config.js", "ios/Smoke.xctestplan",
                     ".claude/settings.json"]:
            with self.subTest(path=path):
                self.assertEqual(self.edit(path, "a", "b"), "ask")

    def test_adding_a_skip_or_suppression_asks(self):
        cases = [
            ("Tests/A.swift", "func testA() {", "func testA() throws {\n throw XCTSkip(\"later\")"),
            ("tests/test_a.py", "def test_a():", "@unittest.skip('flaky')\ndef test_a():"),
            ("web/a.test.js", "it('works', () => {", "it.skip('works', () => {"),
            ("src/a.ts", "const x = y", "// @ts-ignore\nconst x = y"),
            ("app/A.kt", "fun a() {", "@Suppress(\"UNCHECKED_CAST\")\nfun a() {"),
            ("UITests/Audit.swift", "try app.performAccessibilityAudit()",
             "try app.performAccessibilityAudit { issue in issue.auditType == .textClipped }"),
        ]
        for path, old, new in cases:
            with self.subTest(path=path):
                self.assertEqual(self.edit(path, old, new), "ask")

    def test_moving_or_removing_an_existing_skip_is_allowed(self):
        self.assertEqual(self.edit("tests/test_a.py", "@unittest.skip('x')\ndef test_a():",
                                   "def test_a():"), "allow")
        self.assertEqual(self.edit("tests/test_a.py", "@unittest.skip('x')  # old",
                                   "@unittest.skip('x')  # reason: needs device"), "allow")

    def test_plain_code_edits_are_allowed(self):
        self.assertEqual(self.edit("src/a.swift", "let a = 1", "let a = 2"), "allow")

    def test_write_compares_against_existing_file(self):
        path = os.path.join(self.cwd, "test_b.py")
        with open(path, "w") as f:
            f.write("@unittest.skip('x')\ndef test_b(): pass\n")
        same = run("edit", {"file_path": "test_b.py",
                            "content": "@unittest.skip('x')\ndef test_b(): assert True\n"}, self.cwd)
        more = run("edit", {"file_path": "test_b.py",
                            "content": "@unittest.skip('x')\n@unittest.skip('y')\n"}, self.cwd)
        self.assertEqual((same, more), ("allow", "ask"))

    def test_multiedit_is_checked(self):
        result = run("edit", {"file_path": "a.py", "edits": [
            {"old_string": "x = 1", "new_string": "x = 1  # noqa"}]}, self.cwd)
        self.assertEqual(result, "ask")

    def test_project_markers_and_opt_out(self):
        self.config(weakeningMarkers=[r"\bExclusion\("])
        self.assertEqual(self.edit("Audit.swift", "]", "Exclusion(type: .contrast)]"), "ask")
        self.config(checkWeakening=False)
        self.assertEqual(self.edit("a.py", "x", "x  # noqa"), "allow")


if __name__ == "__main__":
    unittest.main()
