"""Real-git tests for the board's git hooks and their installer (board/githooks.py and
templates/githooks/hook.sh). Everything runs in temp repos under TMPDIR, with HOME and the global
git config pointed at temp files, so no test reads or writes the user's own git config.
Made-up tickets only."""
import glob
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "bin", "dev-harness-board")
sys.path.insert(0, ROOT)
from board import cli, githooks  # noqa: E402

TRACKER = "# T\n\n| ID | Title | State |\n|---|---|---|\n| XY-1 | Thing one | Planned |\n| XY-2 | Thing two | Planned |\n"
BOARD = "docs/tickets/board.html"


def ticket(tid, state):
    return f"# {tid}: Thing\n\n## Status\n\n{state}\n"


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def tree(folder):
    """{relative path: (mode, bytes or link target)} of everything under `folder`."""
    out = {}
    for d, dirs, files in os.walk(folder):
        for n in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
            p = os.path.join(d, n)
            out[os.path.relpath(p, folder)] = (os.lstat(p).st_mode,
                                               os.readlink(p) if os.path.islink(p) else read(p, "rb"))
    return out


class Env(unittest.TestCase):
    """A temp folder with its own HOME, global git config, TMPDIR and fake bin folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.tmpdir = os.path.join(self.base, "tmp")      # the hooks' own temp files land here
        os.makedirs(self.tmpdir)
        self.gitconfig = os.path.join(self.base, "gitconfig")
        write(self.gitconfig, "")
        self.fakebin = os.path.join(self.base, "fakebin")
        os.makedirs(self.fakebin)
        self.log = os.path.join(self.base, "hooks.log")
        path = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                if p and not os.path.exists(os.path.join(p, "dev-harness-board"))]
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "DEV_HARNESS_", "CLAUDE_"))}
        self.env.update({
            "PATH": os.pathsep.join([self.fakebin] + path), "HOME": self.home, "TMPDIR": self.tmpdir,
            "GIT_CONFIG_GLOBAL": self.gitconfig, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "HOOK_LOG": self.log, "DEV_HARNESS_BOARD": BIN, "DEV_HARNESS_HOOK_TIMEOUT": "20"})
        self.root = os.path.join(self.base, "main")
        self.hooks = os.path.join(self.root, ".git", "dev-harness", "hooks")
        os.makedirs(self.root)
        self.git("init", "-q", "-b", "main")
        write(os.path.join(self.root, ".gitignore"), BOARD + "\n")
        write(os.path.join(self.root, "docs/tickets/tracker.md"), TRACKER)
        write(os.path.join(self.root, "docs/tickets/XY-1.md"), ticket("XY-1", "Planned"))
        write(os.path.join(self.root, "docs/tickets/XY-2.md"), ticket("XY-2", "Planned"))
        write(os.path.join(self.root, "notes.txt"), "a\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

    def git(self, *args, cwd=None, check=True, env=None, input=None, timeout=120):
        p = subprocess.run(["git", *args], cwd=cwd or self.root, env={**self.env, **(env or {})},
                           capture_output=True, text=True, input=input, timeout=timeout)
        if check:
            self.assertEqual(p.returncode, 0, (args, p.stdout, p.stderr))
        return p

    def board(self, *args, cwd=None, env=None):
        p = subprocess.run([BIN, *args], cwd=cwd or self.root, env={**self.env, **(env or {})},
                           capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def config_value(self):
        p = self.git("config", "--local", "--get", "core.hooksPath", check=False)
        return p.stdout.strip() if p.returncode == 0 else None

    def install(self):
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 0, err)
        self.assertIn(f"set core.hooksPath to {self.hooks}", out)
        self.assertEqual(self.config_value(), self.hooks)

    def own_hook(self, name, body, exe=True):
        """An existing hook of the repository's own in .git/hooks."""
        write(os.path.join(self.root, ".git", "hooks", name), "#!/bin/sh\n" + body, 0o755 if exe else 0o644)

    def board_file(self, root=None):
        return os.path.join(root or self.root, BOARD)

    def drop_board(self):
        if os.path.exists(self.board_file()):
            os.unlink(self.board_file())

    def edit_ticket(self, root, tid, state):
        write(os.path.join(root, f"docs/tickets/{tid}.md"), ticket(tid, state))

    def read_log(self):
        try:
            return read(self.log)
        except FileNotFoundError:
            return ""

    def fake_board(self, body, name="fake-board"):
        path = os.path.join(self.base, name)
        write(path, "#!/bin/sh\n" + body, 0o755)
        return path

    def leftovers(self):
        return glob.glob(os.path.join(self.tmpdir, "dev-harness-hook.*"))

    def remote(self):
        remote = os.path.join(self.base, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], env=self.env, check=True)
        self.git("remote", "add", "origin", remote)
        return remote


class Rebuilds(Env):
    def setUp(self):
        super().setUp()
        self.install()

    def test_commit_touching_a_ticket_rebuilds_the_board(self):
        self.drop_board()
        self.edit_ticket(self.root, "XY-1", "In progress")
        p = self.git("commit", "-qam", "start xy-1")
        self.assertTrue(os.path.exists(self.board_file()), p.stderr)
        self.assertEqual(p.stderr, "")
        self.assertEqual(self.leftovers(), [])

    def test_commit_touching_no_ticket_file_does_not_rebuild(self):
        self.drop_board()
        write(os.path.join(self.root, "notes.txt"), "b\n")
        self.git("commit", "-qam", "notes")
        self.assertFalse(os.path.exists(self.board_file()))

    def test_amend_rebuilds(self):
        write(os.path.join(self.root, "notes.txt"), "b\n")
        self.git("commit", "-qam", "notes")
        self.drop_board()
        self.edit_ticket(self.root, "XY-2", "In progress")
        self.git("commit", "-q", "-a", "--amend", "--no-edit")
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertEqual(self.leftovers(), [])

    def test_cherry_pick_rebuilds(self):
        self.git("switch", "-q", "-c", "side")
        self.edit_ticket(self.root, "XY-1", "Done")
        self.git("commit", "-qam", "xy-1 done")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("switch", "-q", "main")
        self.drop_board()
        self.git("cherry-pick", sha)
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertIn("XY-1", read(self.board_file()))

    def test_cherry_pick_of_a_range_rebuilds(self):
        # several commits run the sequencer and no post-rewrite: post-commit must still rebuild
        self.git("switch", "-q", "-c", "side")
        write(os.path.join(self.root, "notes.txt"), "side\n")
        self.git("commit", "-qam", "notes")
        self.edit_ticket(self.root, "XY-1", "Done")
        self.git("commit", "-qam", "xy-1 done")
        self.git("switch", "-q", "main")
        self.drop_board()
        self.git("cherry-pick", "main..side")
        self.assertTrue(os.path.exists(self.board_file()))

    def test_worktree_add_rebuilds_the_main_board_and_shows_the_ticket(self):
        self.drop_board()
        wt = os.path.join(self.base, "wt-xy-1")
        self.git("worktree", "add", "-q", "-b", "feature/xy-1", wt)
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertFalse(os.path.exists(self.board_file(wt)))
        self.assertIn("feature/xy-1", read(self.board_file()))

    def test_commit_and_rebase_in_a_worktree_write_the_main_board(self):
        wt = os.path.join(self.base, "wt-xy-2")
        self.git("worktree", "add", "-q", "-b", "feature/xy-2", wt)
        self.drop_board()
        self.edit_ticket(wt, "XY-2", "In review")
        self.git("commit", "-qam", "xy-2 review", cwd=wt)
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertFalse(os.path.exists(self.board_file(wt)))
        write(os.path.join(self.root, "notes.txt"), "main moved\n")
        self.git("commit", "-qam", "main moves")
        self.drop_board()
        self.git("rebase", "-q", "main", cwd=wt)
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertFalse(os.path.exists(self.board_file(wt)))
        self.assertEqual(self.leftovers(), [])

    def test_merge_rebuilds(self):
        self.git("switch", "-q", "-c", "side")
        self.edit_ticket(self.root, "XY-2", "Done")
        self.git("commit", "-qam", "xy-2 done")
        self.git("switch", "-q", "main")
        self.drop_board()
        self.git("merge", "-q", "--no-edit", "side")
        self.assertTrue(os.path.exists(self.board_file()))

    def test_conflicted_merge_finished_with_commit_rebuilds(self):
        self.git("switch", "-q", "-c", "side")
        write(os.path.join(self.root, "notes.txt"), "side\n")
        self.edit_ticket(self.root, "XY-2", "Done")
        self.git("commit", "-qam", "side")
        self.git("switch", "-q", "main")
        write(os.path.join(self.root, "notes.txt"), "main\n")
        self.git("commit", "-qam", "main")
        p = self.git("merge", "side", check=False)
        self.assertNotEqual(p.returncode, 0)        # notes.txt conflicts; XY-2.md merged cleanly
        write(os.path.join(self.root, "notes.txt"), "both\n")
        self.git("add", "notes.txt")
        self.drop_board()
        self.git("commit", "-q", "--no-edit")
        self.assertTrue(os.path.exists(self.board_file()))

    def test_post_commit_waits_for_the_end_of_a_rebase(self):
        hook = os.path.join(self.hooks, "post-commit")
        self.edit_ticket(self.root, "XY-1", "In progress")
        self.git("commit", "-qam", "xy-1", env={"DEV_HARNESS_BOARD": "off"})
        self.drop_board()
        os.makedirs(os.path.join(self.root, ".git", "rebase-merge"))
        p = subprocess.run([hook], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertFalse(os.path.exists(self.board_file()))
        os.rmdir(os.path.join(self.root, ".git", "rebase-merge"))
        subprocess.run([hook], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertTrue(os.path.exists(self.board_file()))

    def test_post_rewrite_reads_the_rewritten_commits(self):
        hook = os.path.join(self.hooks, "post-rewrite")
        write(os.path.join(self.root, "notes.txt"), "b\n")
        self.git("commit", "-qam", "notes")
        notes = self.git("rev-parse", "HEAD").stdout.strip()
        self.edit_ticket(self.root, "XY-1", "In progress")
        self.git("commit", "-qam", "xy-1")
        tick = self.git("rev-parse", "HEAD").stdout.strip()
        run = lambda text: subprocess.run([hook, "rebase"], cwd=self.root, env=self.env, input=text,
                                          capture_output=True, text=True, timeout=60)
        self.drop_board()
        p = run(f"{notes} {notes}\n")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertFalse(os.path.exists(self.board_file()))
        p = run(f"{notes} {tick}\n")             # the old commit or the new one may touch tickets
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertEqual(self.leftovers(), [])

    def test_switch_without_ticket_changes_does_not_rebuild(self):
        self.git("switch", "-q", "-c", "side")
        write(os.path.join(self.root, "notes.txt"), "side\n")
        self.git("commit", "-qam", "side notes")
        self.drop_board()
        self.git("switch", "-q", "main")
        self.assertFalse(os.path.exists(self.board_file()))


class BoardPath(Env):
    """tickets.boardPath may not lead outside the checkout or into .git."""

    def setUp(self):
        super().setUp()
        self.install()

    def use(self, value):
        write(os.path.join(self.root, ".claude/harness.json"), json.dumps({"tickets": {"boardPath": value}}))

    def test_absolute_and_dotdot_paths_are_refused(self):
        outside = os.path.join(self.base, "outside.html")
        for value in (outside, "../outside.html", ".git/board.html", "docs/../.git/x.html"):
            with self.subTest(value=value):
                self.use(value)
                p = self.git("switch", "-q", "-c", "b" + str(abs(hash(value))), check=False)
                self.assertEqual(p.returncode, 0)
                self.assertEqual(len(p.stderr.splitlines()), 1, p.stderr)
                self.assertIn("board not written", p.stderr)
                code, out, err = self.board()
                self.assertEqual(code, 2)
                self.assertIn("board not written", err)
                self.assertFalse(os.path.exists(outside))
                self.assertFalse(os.path.exists(os.path.join(self.root, ".git", "board.html")))
                self.assertFalse(os.path.exists(os.path.join(self.root, ".git", "x.html")))

    def test_symlinked_folder_out_of_the_checkout_is_refused(self):
        os.makedirs(os.path.join(self.base, "elsewhere"))
        os.symlink(os.path.join(self.base, "elsewhere"), os.path.join(self.root, "site"))
        self.use("site/board.html")
        p = self.git("switch", "-q", "-c", "other", check=False)
        self.assertEqual(p.returncode, 0)
        self.assertIn("outside", p.stderr)
        self.assertEqual(os.listdir(os.path.join(self.base, "elsewhere")), [])

    def case_insensitive(self):
        return os.path.isdir(os.path.join(self.root, ".GIT"))

    def test_mixed_case_git_dir_is_refused_in_the_hook(self):
        if not self.case_insensitive():
            self.skipTest("case-sensitive filesystem: .GIT is a different folder from .git here")
        config = os.path.join(self.root, ".git", "config")
        self.own_hook("post-merge", 'echo "own post-merge" >>"$HOOK_LOG"\n')
        self.git("config", "core.hooksPath", self.hooks)        # keep the install after own_hook
        hook_before = read(os.path.join(self.root, ".git", "hooks", "post-merge"))
        for value in (".GIT/config", ".Git/hooks/post-merge"):
            with self.subTest(value=value):
                before = read(config, "rb")
                self.use(value)
                self.git("add", ".claude/harness.json")
                p = self.git("commit", "-qm", "board path " + value, check=False)   # its own post-commit
                self.assertEqual(p.returncode, 0)
                self.assertEqual(len(p.stderr.splitlines()), 1, p.stderr)
                self.assertIn("leads into the git dir", p.stderr)
                self.assertEqual(read(config, "rb"), before)
                self.assertEqual(read(os.path.join(self.root, ".git", "hooks", "post-merge")), hook_before)

    def test_mixed_case_git_dir_is_refused_in_a_manual_run(self):
        if not self.case_insensitive():
            self.skipTest("case-sensitive filesystem: .Git is a different folder from .git here")
        config = os.path.join(self.root, ".git", "config")
        before = read(config, "rb")
        for value in (".Git/config", "docs/../.gIT/description"):
            with self.subTest(value=value):
                self.use(value)
                code, out, err = self.board()
                self.assertEqual(code, 2)
                self.assertIn("leads into the git dir", err)
                self.assertEqual(read(config, "rb"), before)

    def test_git_dir_kept_in_the_tree_under_another_name_is_refused(self):
        # only the samefile walk sees this one: no path part is called .git
        other = os.path.join(self.base, "sep")
        meta = os.path.join(other, "meta")
        subprocess.run(["git", "init", "-q", "-b", "main", "--separate-git-dir", meta, other],
                       env=self.env, check=True, capture_output=True)
        write(os.path.join(other, ".claude/harness.json"), json.dumps({"tickets": {"boardPath": "meta/config"}}))
        write(os.path.join(other, "docs/tickets/tracker.md"), TRACKER)
        before = read(os.path.join(meta, "config"), "rb")
        code, out, err = self.board("--root", other)
        self.assertEqual(code, 2)
        self.assertIn("leads into the git dir", err)
        self.assertEqual(read(os.path.join(meta, "config"), "rb"), before)

    def test_explicit_out_still_writes_anywhere(self):
        self.use("../outside.html")
        target = os.path.join(self.base, "chosen.html")
        code, out, err = self.board("--out", target)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(target))

    def test_a_path_inside_still_works(self):
        self.use("site/board.html")
        code, out, err = self.board()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(self.root, "site/board.html")))


class Chaining(Env):
    """The repository's own .git/hooks keep running, with the same arguments, stdin and exit codes."""

    def test_own_post_commit_and_pre_push_run_with_arguments_and_stdin(self):
        self.own_hook("post-commit", 'printf "post-commit args=[%s]\\n" "$*" >>"$HOOK_LOG"\n')
        self.own_hook("pre-push", 'printf "pre-push args=[%s %s]\\n" "$1" "$2" >>"$HOOK_LOG"\n'
                                  'while read -r a b c d; do printf "pre-push stdin=[%s %s %s %s]\\n" '
                                  '"$a" "$b" "$c" "$d" >>"$HOOK_LOG"; done\nexit "${PRE_PUSH_EXIT:-0}"\n')
        self.install()
        self.assertTrue(os.path.exists(os.path.join(self.hooks, "pre-push")))
        self.edit_ticket(self.root, "XY-1", "In progress")
        self.git("commit", "-qam", "start")
        self.assertIn("post-commit args=[]\n", self.read_log())
        self.assertTrue(os.path.exists(self.board_file()))
        remote = self.remote()
        head = self.git("rev-parse", "HEAD").stdout.strip()
        # a failing pre-push still stops the push (exit code passed on)
        p = self.git("push", "-q", "origin", "main", check=False, env={"PRE_PUSH_EXIT": "1"})
        self.assertNotEqual(p.returncode, 0)
        self.assertNotEqual(subprocess.run(["git", "--git-dir", remote, "rev-parse", "--verify", "-q", "main"],
                                           env=self.env, capture_output=True).returncode, 0)
        self.git("push", "-q", "origin", "main")
        log = self.read_log()
        self.assertIn(f"pre-push args=[origin {remote}]\n", log)
        self.assertIn(f"pre-push stdin=[refs/heads/main {head} refs/heads/main {'0' * len(head)}]\n", log)

    def test_branch_made_before_install_still_runs_the_chained_pre_push(self):
        self.git("branch", "old")                   # predates the hooks; nothing is tracked anyway
        self.own_hook("pre-push", 'echo "own pre-push $1" >>"$HOOK_LOG"; cat >/dev/null\n')
        self.install()
        self.remote()
        self.git("switch", "-q", "old")
        self.git("push", "-q", "origin", "old")
        self.assertIn("own pre-push origin", self.read_log())

    def test_tracked_files_of_a_branch_cannot_change_the_hooks(self):
        self.install()
        self.git("switch", "-q", "-c", "evil")
        for rel in (".githooks/pre-push", "dev-harness/hooks/pre-push", ".git-hooks/pre-push"):
            write(os.path.join(self.root, rel), '#!/bin/sh\n# dev-harness-board hook\necho EVIL >>"$HOOK_LOG"\n', 0o755)
        self.git("add", "-A")
        self.git("commit", "-qm", "hooks in the tree")
        self.remote()
        self.git("push", "-q", "origin", "evil")
        self.assertNotIn("EVIL", self.read_log())
        self.assertEqual(self.config_value(), self.hooks)
        self.assertEqual(read(os.path.join(self.hooks, "pre-push")), read(githooks.TEMPLATE))

    def test_own_post_checkout_exit_code_is_passed_on(self):
        self.own_hook("post-checkout", "exit 3\n")
        self.install()
        p = self.git("switch", "-q", "-c", "other", check=False)
        self.assertNotEqual(p.returncode, 0)          # git reports a failing post-checkout as exit 1
        self.assertTrue(os.path.exists(self.board_file()))

    def test_own_post_rewrite_gets_the_same_stdin(self):
        self.own_hook("post-rewrite", 'printf "post-rewrite %s\\n" "$1" >>"$HOOK_LOG"; cat >>"$HOOK_LOG"\n')
        self.install()
        old = self.git("rev-parse", "HEAD").stdout.strip()
        self.edit_ticket(self.root, "XY-1", "In progress")
        self.drop_board()
        self.git("commit", "-q", "-a", "--amend", "--no-edit")
        new = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertIn(f"post-rewrite amend\n{old} {new}\n", self.read_log())
        self.assertTrue(os.path.exists(self.board_file()))
        self.assertEqual(self.leftovers(), [])

    def test_own_post_rewrite_that_ignores_stdin_still_rebuilds(self):
        self.own_hook("post-rewrite", "exit 0\n")
        self.install()
        hook = os.path.join(self.hooks, "post-rewrite")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.drop_board()
        big = "".join(f"{head} {head}\n" for _ in range(20000))    # more than a pipe holds
        p = subprocess.run([hook, "rebase"], cwd=self.root, env=self.env, input=big, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertTrue(os.path.exists(self.board_file()))     # no hang, no broken pipe, still rebuilt
        self.assertEqual(self.leftovers(), [])

    def test_off_makes_no_stdin_copy(self):
        self.own_hook("post-rewrite", 'ls "$TMPDIR" >>"$HOOK_LOG"; cat >/dev/null\n')
        self.install()
        hook = os.path.join(self.hooks, "post-rewrite")
        p = subprocess.run([hook, "amend"], cwd=self.root, env={**self.env, "DEV_HARNESS_BOARD": "off"},
                           input="a b\n", capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertNotIn("dev-harness-hook", self.read_log())

    def test_other_own_hooks_get_a_pass_through(self):
        self.own_hook("commit-msg", 'grep -q ok "$1" || { echo "commit-msg says no" >&2; exit 1; }\n')
        self.own_hook("notahook", "exit 1\n")
        self.own_hook("pre-commit", "exit 1\n", exe=False)          # git ignores it; so do we
        self.install()
        names = sorted(os.listdir(self.hooks))
        self.assertEqual(names, ["commit-msg", "post-checkout", "post-commit", "post-merge", "post-rewrite", "pre-push"])
        write(os.path.join(self.root, "notes.txt"), "c\n")
        p = self.git("commit", "-qam", "bad", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("commit-msg says no", p.stderr)
        self.git("commit", "-qam", "ok")

    def test_a_hook_that_pushes_another_repo_still_runs_that_repos_pre_push(self):
        other = os.path.join(self.base, "other")
        subprocess.run(["git", "init", "-q", "-b", "main", other], env=self.env, check=True)
        self.git("commit", "-q", "--allow-empty", "-m", "b", cwd=other)
        write(os.path.join(other, ".git", "hooks", "pre-push"),
              '#!/bin/sh\necho "B own pre-push" >>"$HOOK_LOG"; cat >/dev/null\n', 0o755)
        code, out, err = self.board("install-hooks", cwd=other)
        self.assertEqual(code, 0, err)
        b_remote = os.path.join(self.base, "b-remote.git")
        subprocess.run(["git", "init", "-q", "--bare", b_remote], env=self.env, check=True)
        self.git("remote", "add", "origin", b_remote, cwd=other)
        self.own_hook("pre-push", f'cat >/dev/null; echo "A own pre-push" >>"$HOOK_LOG"; '
                                  f'git -C "{other}" push -q origin main\n')
        self.install()
        self.remote()
        self.git("push", "-q", "origin", "main")
        log = self.read_log()
        self.assertIn("A own pre-push", log)
        self.assertIn("B own pre-push", log)

    def test_file_size_limit_does_not_cut_the_chained_post_rewrite_stdin(self):
        self.own_hook("post-rewrite", 'n=$(wc -l | tr -d " "); echo "lines=$n" >>"$HOOK_LOG"\n')
        self.install()
        fake = self.fake_board("cat >/dev/null\n")
        hook = os.path.join(self.hooks, "post-rewrite")
        lines = "".join(f"{i:040x} {i + 1:040x}\n" for i in range(5000))      # about 400 KB
        for shell in [s for s in ("/bin/sh", "/bin/dash", "/usr/bin/dash") if os.access(s, os.X_OK)]:
            with self.subTest(shell=shell):
                open(self.log, "w").close()
                p = subprocess.run([shell, "-c", 'ulimit -f 8; exec "$0" "$1" rebase', shell, hook],
                                   cwd=self.root, env={**self.env, "DEV_HARNESS_BOARD": fake}, input=lines,
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("lines=5000", self.read_log())
                self.assertEqual(self.leftovers(), [])

    def test_delegate_wrapper_in_dot_git_hooks_does_not_loop(self):
        # a repository hook that hands back to the hooks folder (a common "delegate" wrapper)
        wrapper = ('h="$(git rev-parse --git-common-dir)/dev-harness/hooks/$(basename "$0")"; '
                   '[ -x "$h" ] && exec "$h" "$@"\nexit 0\n')
        for name in ("commit-msg", "post-commit"):
            self.own_hook(name, 'echo "own %s" >>"$HOOK_LOG"\n' % name + wrapper)
        self.install()
        self.edit_ticket(self.root, "XY-1", "In progress")
        proc = subprocess.Popen(["git", "commit", "-qam", "loop?"], cwd=self.root, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail("the hooks loop")
        self.assertEqual(proc.returncode, 0, err)
        log = self.read_log()
        self.assertEqual((log.count("own commit-msg"), log.count("own post-commit")), (1, 1), log)
        self.assertTrue(os.path.exists(self.board_file()))

    def test_fake_git_lfs_hooks_keep_running(self):
        write(os.path.join(self.fakebin, "git-lfs"),
              '#!/bin/sh\nprintf "lfs %s args=[%s]\\n" "$1" "$*" >>"$HOOK_LOG"\n'
              'if [ "$1" = pre-push ]; then while read -r l; do printf "lfs stdin=[%s]\\n" "$l" >>"$HOOK_LOG"; done; fi\n',
              0o755)
        for name in ("post-checkout", "post-commit", "post-merge", "pre-push"):
            # the shape of the hooks `git lfs install` writes
            self.own_hook(name, "command -v git-lfs >/dev/null 2>&1 || { printf >&2 \"git-lfs not found\\n\"; exit 2; }\n"
                                f'git lfs {name} "$@"\n')
        self.install()
        self.git("switch", "-q", "-c", "side")
        self.edit_ticket(self.root, "XY-1", "Done")
        self.git("commit", "-qam", "xy-1 done")
        self.git("switch", "-q", "main")
        self.git("merge", "-q", "--no-edit", "side")
        remote = self.remote()
        self.git("push", "-q", "origin", "main")
        log = self.read_log()
        for name in ("post-checkout", "post-commit", "post-merge"):
            self.assertIn(f"lfs {name} args=[{name}", log)
        self.assertIn("lfs post-merge args=[post-merge 0]", log)
        self.assertIn(f"lfs pre-push args=[pre-push origin {remote}]", log)
        self.assertIn("lfs stdin=[refs/heads/main ", log)
        self.assertTrue(os.path.exists(self.board_file()))


class NeverFails(Env):
    def setUp(self):
        super().setUp()
        self.install()

    def test_failing_board_does_not_fail_git(self):
        fake = self.fake_board("echo 'boom: it broke' >&2\necho second line >&2\nexit 3\n")
        env = {"DEV_HARNESS_BOARD": fake}
        self.edit_ticket(self.root, "XY-1", "In progress")
        p = self.git("commit", "-qam", "start", env=env)
        self.assertEqual(p.stderr, "dev-harness: boom: it broke\n")
        # post-checkout's exit code is git's exit code, so this is where exit 0 matters
        p = self.git("switch", "-q", "-c", "other", check=False, env=env)
        self.assertEqual((p.returncode, p.stderr), (0, "dev-harness: boom: it broke\n"))
        for name in ("post-checkout", "post-commit", "post-merge", "post-rewrite"):
            p = subprocess.run([os.path.join(self.hooks, name), "x", "y", "1"], cwd=self.root,
                               env={**self.env, **env}, capture_output=True, text=True, input="", timeout=60)
            self.assertEqual(p.returncode, 0, (name, p.stderr))
        self.assertEqual(self.leftovers(), [])

    def test_silent_failing_board_gets_one_line(self):
        fake = self.fake_board("exit 4\n")
        p = self.git("switch", "-q", "-c", "other", check=False, env={"DEV_HARNESS_BOARD": fake})
        self.assertEqual((p.returncode, p.stderr), (0, "dev-harness: board rebuild failed (exit 4)\n"))

    def test_slow_board_is_stopped(self):
        fake = self.fake_board("sleep 30\n")        # a child that outlives the killed script
        env = {"DEV_HARNESS_BOARD": fake, "DEV_HARNESS_HOOK_TIMEOUT": "2"}
        self.edit_ticket(self.root, "XY-1", "In progress")
        t0 = time.monotonic()
        p = self.git("commit", "-qam", "start", env=env, check=False)
        took = time.monotonic() - t0
        self.assertEqual((p.returncode, p.stderr), (0, "dev-harness: board rebuild stopped after 2s\n"))
        self.assertLess(took, 12)
        t0 = time.monotonic()
        p = self.git("switch", "-q", "-c", "other", env=env, check=False)
        self.assertEqual(p.returncode, 0)
        self.assertLess(time.monotonic() - t0, 12)
        self.assertEqual(self.leftovers(), [])

    def test_board_that_ignores_term_is_killed(self):
        fake = self.fake_board("trap '' TERM\nwhile :; do sleep 1; done\n")
        env = {"DEV_HARNESS_BOARD": fake, "DEV_HARNESS_HOOK_TIMEOUT": "1"}
        t0 = time.monotonic()
        p = self.git("switch", "-q", "-c", "other", env=env, check=False, timeout=60)
        self.assertEqual((p.returncode, p.stderr), (0, "dev-harness: board rebuild stopped after 1s\n"))
        self.assertLess(time.monotonic() - t0, 10)

    def test_python_board_stopped_by_term_exits_143(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1])\n"
                "from board import cli, githooks\n"
                "cli.load_tickets_config = lambda root: time.sleep(30)\n"
                "sys.exit(githooks.run_hook(['post-commit'], sys.stdout, sys.stderr))\n")
        proc = subprocess.Popen([sys.executable, "-c", code, ROOT], cwd=self.root, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 143)

    def test_term_to_the_hook_removes_its_temp_files(self):
        fake = self.fake_board("sleep 30\n")
        proc = subprocess.Popen([os.path.join(self.hooks, "post-rewrite"), "rebase"], cwd=self.root,
                                env={**self.env, "DEV_HARNESS_BOARD": fake, "DEV_HARNESS_HOOK_TIMEOUT": "20"},
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdin.write(b"a b\n")
        proc.stdin.close()
        deadline = time.monotonic() + 10
        while len(self.leftovers()) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(len(self.leftovers()), 2)      # stdin copy and stderr file
        proc.send_signal(signal.SIGTERM)
        # Not communicate(): stdin is already closed, and Python 3.9's communicate() flushes it (ValueError).
        proc.wait(timeout=30)
        proc.stdout.close()
        proc.stderr.close()
        self.assertEqual(proc.returncode, 143)
        self.assertEqual(self.leftovers(), [])

    def test_broken_config_is_one_line_and_exit_0(self):
        write(os.path.join(self.root, ".claude/harness.json"), "{ not json")
        p = self.git("switch", "-q", "-c", "other", check=False)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(p.stderr.splitlines()), 1, p.stderr)
        self.assertTrue(p.stderr.startswith("dev-harness: board hook: "), p.stderr)

    def test_missing_board_is_one_line_and_exit_0(self):
        env = {k: v for k, v in self.env.items() if k != "DEV_HARNESS_BOARD"}
        self.edit_ticket(self.root, "XY-1", "In progress")
        p = subprocess.run(["git", "commit", "-qam", "start"], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(p.stderr.splitlines()), 1, p.stderr)
        self.assertIn("dev-harness-board not found", p.stderr)
        self.assertFalse(os.path.exists(self.board_file()))

    def test_bad_board_variable_is_one_line(self):
        p = self.git("switch", "-q", "-c", "other", check=False,
                     env={"DEV_HARNESS_BOARD": os.path.join(self.base, "nope")})
        self.assertEqual((p.returncode, p.stderr),
                         (0, "dev-harness: board not rebuilt: DEV_HARNESS_BOARD is not an executable file\n"))

    def test_off_skips_quietly(self):
        p = self.git("switch", "-q", "-c", "other", check=False, env={"DEV_HARNESS_BOARD": "off"})
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertFalse(os.path.exists(self.board_file()))


class Shells(Env):
    """The hook script under each POSIX shell found here (macOS /bin/sh is bash; Linux is often dash)."""

    def setUp(self):
        super().setUp()
        self.install()
        self.shells = [s for s in ("/bin/sh", "/bin/dash", "/usr/bin/dash", "/bin/bash", "/usr/bin/bash")
                       if os.access(s, os.X_OK)]

    def run_hook(self, shell, name, args, env, stdin=""):
        return subprocess.run([shell, os.path.join(self.hooks, name), *args], cwd=self.root,
                              env={**self.env, **env}, input=stdin, capture_output=True, text=True, timeout=60)

    def test_each_shell(self):
        self.own_hook("post-commit", 'echo "own post-commit $*" >>"$HOOK_LOG"; exit 5\n')
        self.own_hook("pre-push", 'cat >>"$HOOK_LOG"; exit 6\n')
        self.own_hook("post-rewrite", 'cat >>"$HOOK_LOG"\n')
        slow = self.fake_board("trap '' TERM\nwhile :; do sleep 1; done\n")
        bad = self.fake_board("echo broken >&2; exit 1\n", "bad-board")
        for shell in self.shells:
            with self.subTest(shell=shell):
                self.drop_board()
                p = self.run_hook(shell, "post-checkout", ["0" * 40, "1" * 40, "1"], {})
                self.assertEqual((p.returncode, p.stderr), (0, ""))
                self.assertTrue(os.path.exists(self.board_file()))
                p = self.run_hook(shell, "post-commit", ["a"], {})
                self.assertEqual(p.returncode, 5)
                self.assertIn("own post-commit a", self.read_log())
                p = self.run_hook(shell, "pre-push", ["origin", "x"], {}, stdin=f"via {shell}\n")
                self.assertEqual(p.returncode, 6)
                self.assertIn(f"via {shell}\n", self.read_log())
                p = self.run_hook(shell, "post-rewrite", ["rebase"], {}, stdin=f"rewrite {shell}\n")
                self.assertEqual((p.returncode, p.stderr), (0, ""))
                self.assertIn(f"rewrite {shell}\n", self.read_log())
                t0 = time.monotonic()
                p = self.run_hook(shell, "post-merge", ["1"], {"DEV_HARNESS_BOARD": slow, "DEV_HARNESS_HOOK_TIMEOUT": "1"})
                self.assertEqual((p.returncode, p.stderr), (0, "dev-harness: board rebuild stopped after 1s\n"))
                self.assertLess(time.monotonic() - t0, 10)
                p = self.run_hook(shell, "post-merge", ["1"], {"DEV_HARNESS_BOARD": bad})
                self.assertEqual((p.returncode, p.stderr), (0, "dev-harness: broken\n"))
                p = self.run_hook(shell, "post-commit", [], {"DEV_HARNESS_HOOK_ACTIVE": os.path.realpath(os.path.join(self.root, ".git")) + "/post-commit"})
                self.assertEqual((p.returncode, p.stderr), (0, ""))
                self.assertEqual(self.leftovers(), [])


class FindsTheBoard(Env):
    def setUp(self):
        super().setUp()
        self.install()
        self.env.pop("DEV_HARNESS_BOARD")

    def wrapper(self, folder):
        path = os.path.join(folder, "dev-harness-board")
        write(path, f'#!/bin/sh\necho "used {folder}" >>"$HOOK_LOG"\nexec "{BIN}" "$@"\n', 0o755)
        return path

    def test_on_path(self):
        self.wrapper(self.fakebin)
        self.git("switch", "-q", "-c", "other")
        self.assertIn(f"used {self.fakebin}", self.read_log())
        self.assertTrue(os.path.exists(self.board_file()))

    def test_from_the_claude_code_plugin_list(self):
        install = os.path.join(self.base, "plugin cache", "dev-harness", "1.0.0")
        self.wrapper(os.path.join(install, "bin"))
        write(os.path.join(self.home, ".claude/plugins/installed_plugins.json"), json.dumps(
            {"version": 2, "plugins": {"other@x": [{"installPath": "/nowhere"}],
                                       "dev-harness@market": [{"installPath": install, "scope": "user"}]}}))
        p = self.git("switch", "-q", "-c", "other")
        self.assertEqual(p.stderr, "")
        self.assertIn(f"used {os.path.join(install, 'bin')}", self.read_log())
        self.assertTrue(os.path.exists(self.board_file()))

    def test_broken_plugin_list_is_the_missing_note(self):
        write(os.path.join(self.home, ".claude/plugins/installed_plugins.json"), "{ nope")
        p = self.git("switch", "-q", "-c", "other", check=False)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(p.stderr.splitlines()), 1)
        self.assertIn("dev-harness-board not found", p.stderr)


class Installer(Env):
    def snapshot(self):
        return tree(self.root), tree(self.home), read(self.gitconfig, "rb")

    def test_check_writes_nothing(self):
        self.own_hook("pre-commit", "exit 0\n")
        before = self.snapshot()
        code, out, err = self.board("install-hooks", "--check")
        self.assertEqual(code, 0, err)
        self.assertIn(f"would write {self.hooks}/post-commit", out)
        self.assertIn(f"would write {self.hooks}/pre-commit", out)
        self.assertIn(f"would set core.hooksPath to {self.hooks}", out)
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(self.config_value())

    def test_installs_untracked_into_the_common_dir(self):
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.config_value(), self.hooks)
        for n in ("post-checkout", "post-commit", "post-merge", "post-rewrite", "pre-push"):
            path = os.path.join(self.hooks, n)
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)
            self.assertEqual(read(path), read(githooks.TEMPLATE))
        self.assertEqual(self.git("status", "--porcelain").stdout, "")     # nothing to commit
        self.assertEqual(self.git("config", "--global", "--list", check=False).stdout, "")
        code, out, err = self.board("install-hooks")
        self.assertIn(f"core.hooksPath already points at {self.hooks} (local config)", out)
        self.assertIn("unchanged", out)

    def test_refuses_a_foreign_hooks_path(self):
        for value in ("tools/hooks", ".git/dev-harness/hooks", os.path.join(self.base, "shared-hooks")):
            with self.subTest(value=value):
                self.git("config", "core.hooksPath", value)
                before = self.snapshot()
                code, out, err = self.board("install-hooks")
                self.assertEqual(code, 1)
                self.assertIn(f"core.hooksPath is already {value!r} (local config)", err)
                self.assertIn("move those hooks into", err)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.config_value(), value)

    def test_global_hooks_path_is_foreign_unless_exactly_ours(self):
        write(self.gitconfig, "[core]\n\thooksPath = .git/dev-harness/hooks\n")
        before = self.snapshot()
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 1)
        self.assertIn("(global config)", err)
        self.assertEqual(self.snapshot(), before)
        write(self.gitconfig, f"[core]\n\thooksPath = {self.hooks}\n")
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 0, err)
        self.assertIn("already points at", out)
        self.assertIsNone(self.config_value())

    def test_refuses_a_foreign_file_in_the_folder(self):
        write(os.path.join(self.hooks, "post-commit"), "#!/bin/sh\necho mine\n", 0o755)
        before = self.snapshot()
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 1)
        self.assertIn("post-commit was not written by dev-harness", err)
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(self.config_value())

    def test_refuses_any_foreign_entry_even_a_non_hook_name(self):
        write(os.path.join(self.hooks, "README"), "notes\n")
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 1)
        self.assertIn("README was not written by dev-harness", err)
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "post-commit")))

    def test_refuses_a_symlink_in_the_folder(self):
        os.makedirs(self.hooks)
        os.symlink(githooks.TEMPLATE, os.path.join(self.hooks, "pre-push"))
        before = self.snapshot()
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 1)
        self.assertIn("is a symlink", err)
        self.assertEqual(self.snapshot(), before)

    def test_refuses_a_symlinked_folder_or_parent(self):
        elsewhere = os.path.join(self.base, "elsewhere")
        os.makedirs(elsewhere)
        for link in (self.hooks, os.path.dirname(self.hooks)):
            with self.subTest(link=link):
                os.makedirs(os.path.dirname(link), exist_ok=True)
                os.symlink(elsewhere, link)
                before = self.snapshot()
                code, out, err = self.board("install-hooks")
                self.assertEqual(code, 1)
                self.assertIn("is a symlink", err)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(os.listdir(elsewhere), [])
                self.assertIsNone(self.config_value())
                os.unlink(link)
                shutil.rmtree(os.path.dirname(self.hooks), ignore_errors=True)

    def test_updates_its_own_older_file(self):
        self.board("install-hooks")
        path = os.path.join(self.hooks, "post-commit")
        write(path, "#!/bin/sh\n# dev-harness-board hook v0\nexit 0\n", 0o755)
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 0, err)
        self.assertIn(f"write {path}", out)
        self.assertIn(f"unchanged {self.hooks}/pre-push", out)
        self.assertEqual(read(path), read(githooks.TEMPLATE))

    def test_never_touches_dot_git_hooks(self):
        self.own_hook("pre-push", "exit 0\n")
        hooks = os.path.join(self.root, ".git", "hooks")
        before = tree(hooks)
        self.install()
        self.board("uninstall-hooks")
        self.assertEqual(tree(hooks), before)

    def test_from_a_worktree_installs_into_the_common_dir(self):
        wt = os.path.join(self.base, "wt")
        self.git("worktree", "add", "-q", "-b", "feature/xy-1", wt)
        code, out, err = self.board("install-hooks", cwd=wt)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(self.hooks, "post-commit")))
        self.assertEqual(self.git("config", "--get", "core.hooksPath", cwd=wt).stdout.strip(), self.hooks)

    def test_stale_folder_is_reported_and_repaired(self):
        gone = os.path.join(self.base, "moved-away", ".git", "dev-harness", "hooks")
        self.git("config", "core.hooksPath", gone)
        before = self.snapshot()
        code, out, err = self.board("install-hooks", "--check")
        self.assertEqual(code, 0, err)
        self.assertIn(f"warning: core.hooksPath points at {gone}", err)
        self.assertEqual(self.snapshot(), before)
        code, out, err = self.board("--check")
        self.assertEqual((code, out), (0, ""))
        self.assertIn(f"warning: core.hooksPath points at {gone}", err)
        code, out, err = self.board("install-hooks")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.config_value(), self.hooks)
        code, out, err = self.board("--check")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_unknown_option_and_not_a_repo(self):
        self.assertEqual(self.board("install-hooks", "--dir", "x")[0], 2)
        plain = os.path.join(self.base, "plain")
        os.makedirs(plain)
        self.assertEqual(self.board("install-hooks", "--root", plain)[0], 2)


class Uninstall(Env):
    def test_unsets_its_own_setting(self):
        self.install()
        code, out, err = self.board("uninstall-hooks", "--check")
        self.assertIn("would unset core.hooksPath", out)
        self.assertEqual(self.config_value(), self.hooks)
        code, out, err = self.board("uninstall-hooks")
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.config_value())

    def test_unsets_a_stale_dev_harness_folder(self):
        self.git("config", "core.hooksPath", os.path.join(self.base, "gone", ".git", "dev-harness", "hooks"))
        code, out, err = self.board("uninstall-hooks")
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.config_value())

    def test_leaves_foreign_settings_alone(self):
        self.git("config", "core.hooksPath", "tools/hooks")
        code, out, err = self.board("uninstall-hooks")
        self.assertEqual(code, 0)
        self.assertIn("left alone", out)
        self.assertEqual(self.config_value(), "tools/hooks")
        self.git("config", "--unset", "core.hooksPath")
        write(self.gitconfig, f"[core]\n\thooksPath = {self.hooks}\n")
        before = read(self.gitconfig, "rb")
        code, out, err = self.board("uninstall-hooks")
        self.assertIn("global config", out)
        self.assertEqual(read(self.gitconfig, "rb"), before)

    def test_nothing_set(self):
        code, out, err = self.board("uninstall-hooks")
        self.assertEqual((code, out), (0, "core.hooksPath is not set; nothing to do\n"))


class Decide(unittest.TestCase):
    CFG = {"trackerPath": "docs/tickets/tracker.md", "dirs": ["docs/tickets"], "archiveTracker": "",
           "archiveDirs": [], "featureGlobs": ["src/features/*"]}

    def test_touches_tickets(self):
        yes = (["docs/tickets/tracker.md"], ["docs/tickets/XY-9.md"], ["./docs/tickets/XY-9.md"],
               [".claude/harness.json"], ["src/features/login/XY-3.md"], ["a.txt", "docs/tickets/x/XY-1.md"])
        no = ([], ["docs/tickets/board.html"], ["README.md"], ["docs/other/XY-1.md"])
        for paths in yes:
            self.assertTrue(githooks.touches_tickets(paths, self.CFG), paths)
        for paths in no:
            self.assertFalse(githooks.touches_tickets(paths, self.CFG), paths)

    def test_changed_paths_shortcuts(self):
        self.assertEqual(githooks.changed_paths("post-checkout", ["a" * 40, "b" * 40, "0"], "", "."), [])
        self.assertIsNone(githooks.changed_paths("post-checkout", ["0" * 40, "b" * 40, "1"], "", "."))
        self.assertIsNone(githooks.changed_paths("post-checkout", ["b" * 40, "b" * 40, "1"], "", "."))
        self.assertIsNone(githooks.changed_paths("post-merge", ["1"], "", "."))
        self.assertIsNone(githooks.changed_paths("post-rewrite", ["rebase"], "", "."))
        self.assertIsNone(githooks.changed_paths("post-rewrite", ["rebase"], "-p x\n", "."))

    def test_board_outside(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            self.assertIsNone(cli.board_outside(d, os.path.join(d, "docs/b.html")))
            for bad in (os.path.join(d, "..", "b.html"), "/b.html", os.path.join(d, ".git", "b.html"), d):
                self.assertIsNotNone(cli.board_outside(d, bad), bad)

    def test_hook_mode_always_exits_0(self):
        for argv in (["hook"], ["hook", "pre-push"], ["hook", "post-commit"]):
            with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {
                    "HOME": d, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}):
                cwd = os.getcwd()
                os.chdir(d)
                try:
                    self.assertEqual(cli.main(argv, io.StringIO(), io.StringIO()), 0)
                finally:
                    os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
