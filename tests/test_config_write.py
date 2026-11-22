"""Protected-config write tests for the Bash guard (end to end through guard.py).
Run: python3 -m unittest tests.test_config_write"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # shared helpers in test_guard
from test_guard import GuardTestCase, commit_all, run  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
