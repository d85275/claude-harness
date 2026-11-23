"""Real-git tests for the board reading live worktrees (board/worktrees.py). Made-up tickets only."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from test_board import run  # noqa: E402

from board import cli, worktrees  # noqa: E402

ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
HEADER = "# T\n\n| ID | Title | State |\n|---|---|---|\n"


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def set_state(root, tid, state, tracker=True, ticket=True):
    """Set the state of a made-up ticket in a checkout's tracker row and/or ticket file."""
    if tracker:
        p = os.path.join(root, "docs/tickets/tracker.md")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(rf"(\| {tid} \| [^|]*\| )[^|]*( \|)", rf"\g<1>{state}\2", text)
        write(root, "docs/tickets/tracker.md", text)
    if ticket:
        write(root, f"docs/tickets/{tid}.md", f"# {tid}: Thing {tid}\n\n## Status\n\n{state}\n")


class LiveBoard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.root = os.path.join(self.base, "main")
        os.makedirs(self.root)
        git(self.root, "init", "-q", "-b", "main")
        rows = "".join(f"| {t} | Thing {t} | {s} |\n" for t, s in
                       (("XY-1", "Planned"), ("XY-2", "Planned"), ("XY-3", "Done"), ("XY-4", "Blocked"),
                        ("XY-5", "Owner check"), ("XY-6", "Deferred")))
        write(self.root, "docs/tickets/tracker.md", HEADER + rows)
        for t, s in (("XY-1", "Planned"), ("XY-2", "Planned"), ("XY-3", "Done"), ("XY-4", "Blocked"),
                     ("XY-5", "Owner check"), ("XY-6", "Deferred")):
            write(self.root, f"docs/tickets/{t}.md", f"# {t}: Thing {t}\n\n## Status\n\n{s}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")

    def add_wt(self, tid, branch=None):
        path = os.path.join(self.base, "wt-" + tid.lower())
        git(self.root, "worktree", "add", "-q", "-b", branch or f"feature/{tid.lower()}", path)
        return path

    def states(self, root=None):
        code, out, err = run("--json", "--root", root or self.root)
        self.assertEqual(code, 0, err)
        import json
        return {t["id"]: t for t in json.loads(out)["tickets"]}

    def test_planned_with_worktree_is_in_progress(self):
        self.add_wt("XY-1")
        t = self.states()
        self.assertEqual(t["XY-1"]["state"], "In progress")
        self.assertEqual(t["XY-1"]["liveBranch"], "feature/xy-1")
        self.assertEqual(t["XY-2"]["state"], "Planned")
        self.assertNotIn("liveBranch", t["XY-2"])

    def test_worktree_row_in_review_committed_and_uncommitted(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "In review")
        self.assertEqual(self.states()["XY-1"]["state"], "In review")     # uncommitted
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "review")
        self.assertEqual(self.states()["XY-1"]["state"], "In review")     # committed

    def test_worktree_file_only_counts_too(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "Fixing", tracker=False)
        self.assertEqual(self.states()["XY-1"]["state"], "Fixing")

    def test_worktree_done_is_capped_at_in_review(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "Done")
        self.assertEqual(self.states()["XY-1"]["state"], "In review")
        set_state(wt, "XY-1", "Owner check")
        self.assertEqual(self.states()["XY-1"]["state"], "In review")

    def test_worktree_blocked_only_counts_as_in_progress(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "Blocked")
        self.assertEqual(self.states()["XY-1"]["state"], "In progress")

    def test_main_done_blocked_owner_check_deferred_do_not_move(self):
        for tid in ("XY-3", "XY-4", "XY-5", "XY-6"):
            wt = self.add_wt(tid)
            set_state(wt, tid, "In review")
        t = self.states()
        self.assertEqual([t[i]["state"] for i in ("XY-3", "XY-4", "XY-5", "XY-6")],
                         ["Done", "Blocked", "Owner check", "Deferred"])
        self.assertTrue(all("liveBranch" not in t[i] for i in t))

    def test_main_ahead_of_worktree_is_not_lowered(self):
        set_state(self.root, "XY-2", "In review")
        self.add_wt("XY-2")
        t = self.states()["XY-2"]
        self.assertEqual(t["state"], "In review")
        self.assertNotIn("liveBranch", t)

    def test_branch_name_matches_ignoring_case_and_ignores_others(self):
        self.add_wt("XY-1", branch="feature/XY-1")
        self.add_wt("XY-2", branch="topic/xy-2")
        self.add_wt("XY-3", branch="feature/unknown-9")
        t = self.states()
        self.assertEqual(t["XY-1"]["state"], "In progress")
        self.assertEqual(t["XY-2"]["state"], "Planned")
        self.assertEqual(set(t), {"XY-1", "XY-2", "XY-3", "XY-4", "XY-5", "XY-6"})

    def test_branch_with_suffix_does_not_match(self):
        self.add_wt("XY-1", branch="feature/xy-1-extra")
        self.assertEqual(self.states()["XY-1"]["state"], "Planned")

    def test_worktree_path_with_newline_is_found(self):
        path = os.path.join(self.base, "wt\nnewline")
        git(self.root, "worktree", "add", "-q", "-b", "feature/xy-1", path)
        self.assertEqual(self.states()["XY-1"]["state"], "In progress")

    def test_line_format_fallback_when_z_unsupported(self):
        self.add_wt("XY-1")
        real = worktrees.git

        def no_z(args, cwd):
            return None if "-z" in args else real(args, cwd)
        with mock.patch.object(worktrees, "git", no_z):
            self.assertIn("feature/xy-1", [b for _, b in worktrees.list_worktrees(self.root)])

    def test_separate_git_dir_main_checkout(self):
        main = os.path.join(self.base, "sep")
        gitdir = os.path.join(self.base, "sep.gitdir")
        os.makedirs(main)
        git(main, "init", "-q", "-b", "main", "--separate-git-dir", gitdir)
        for rel in ("docs/tickets/tracker.md", "docs/tickets/XY-1.md"):
            with open(os.path.join(self.root, rel), encoding="utf-8") as f:
                write(main, rel, f.read())
        git(main, "add", "-A")
        git(main, "commit", "-qm", "init")
        wt = os.path.join(self.base, "sepwt")
        git(main, "worktree", "add", "-q", "-b", "feature/xy-1", wt)
        self.assertEqual(worktrees.main_checkout(main), main)      # .git is a file here, git lists the git dir
        code, _, err = run("--root", main)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(main, "docs/tickets/board.html")))
        # git itself cannot tell a linked worktree where such a work tree is: fall back to the worktree
        self.assertIsNone(worktrees.main_checkout(wt))

    def test_prunable_worktree_is_skipped(self):
        wt = self.add_wt("XY-1")
        subprocess.run(["rm", "-rf", wt], check=True)
        self.assertEqual(self.states()["XY-1"]["state"], "Planned")
        self.assertEqual([b for _, b in worktrees.list_worktrees(self.root)], ["main"])

    def test_card_shows_branch_and_page_builds(self):
        self.add_wt("XY-1")
        out = os.path.join(self.base, "b.html")
        code, _, err = run("--root", self.root, "--out", out)
        self.assertEqual(code, 0, err)
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("from feature/xy-1", page)

    def test_no_git_still_builds(self):
        self.add_wt("XY-1")
        with mock.patch.dict(os.environ, {"PATH": os.devnull}):
            self.assertEqual(worktrees.list_worktrees(self.root), [])
            cfg, _ = cli.load_tickets_config(self.root)
            model = cli.build_model(self.root, cfg, live=True)
        self.assertEqual({t["id"]: t["state"] for t in model.tickets}["XY-1"], "Planned")

    def test_timeout_is_set_on_every_git_call(self):
        calls = []
        real = subprocess.run

        def spy(*a, **kw):
            calls.append(kw.get("timeout"))
            return real(*a, **kw)
        with mock.patch.object(subprocess, "run", spy):
            worktrees.list_worktrees(self.root)
            worktrees.main_checkout(os.path.join(self.base, "x"))
        self.assertTrue(calls and all(c for c in calls))

    def test_not_a_repo_still_builds(self):
        plain = os.path.join(self.base, "plain")
        write(plain, "docs/tickets/tracker.md", HEADER + "| XY-1 | T | Planned |\n")
        write(plain, "docs/tickets/XY-1.md", "# XY-1: T\n\n## Status\n\nPlanned\n")
        os.makedirs(os.path.join(plain, ".git"))     # looks like a checkout but is not a repo
        self.assertEqual(self.states(plain)["XY-1"]["state"], "Planned")

    def test_run_from_worktree_writes_into_main_checkout(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "In review")
        code, out, err = run(cwd=wt)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "docs/tickets/board.html")))
        self.assertFalse(os.path.exists(os.path.join(wt, "docs/tickets/board.html")))
        self.assertIn(os.path.join(self.root, "docs/tickets/board.html"), out)
        with open(os.path.join(self.root, "docs/tickets/board.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("from feature/xy-1", page)

    def test_check_ignores_worktrees(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "In review")
        self.assertEqual(run("--check", "--root", self.root)[:2], (0, ""))
        # an uncommitted mismatch inside the worktree is not main's problem either
        set_state(wt, "XY-1", "Done", tracker=False)
        self.assertEqual(run("--check", "--root", self.root)[:2], (0, ""))

    def test_check_mode_never_applies_the_live_overlay(self):
        self.add_wt("XY-1")
        with mock.patch.object(worktrees, "apply") as apply:
            self.assertEqual(cli.main(["--check", "--root", self.root], out=mock.Mock(), err=mock.Mock()), 0)
        apply.assert_not_called()

    def test_worktree_files_are_not_modified(self):
        wt = self.add_wt("XY-1")
        set_state(wt, "XY-1", "In review")
        before = git(wt, "status", "--porcelain")
        run("--root", self.root, "--out", os.path.join(self.base, "b.html"))
        self.assertEqual(git(wt, "status", "--porcelain"), before)


if __name__ == "__main__":
    unittest.main()
