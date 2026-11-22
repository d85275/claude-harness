"""Push policy tests for the Bash guard (end to end through guard.py).
Run: python3 -m unittest tests.test_push_policy"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # shared helpers in test_guard
from test_guard import GUARD, GuardTestCase, commit_all, git, run  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
