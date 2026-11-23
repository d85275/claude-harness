"""set-state, archive, twin and the start -> review -> fix -> review -> close walk-through
(board/state.py). Real git in a temp folder, made-up tickets, isolated HOME and git config."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from board import cli, state  # noqa: E402
BIN = os.path.join(HERE, "..", "bin", "dev-harness-board")

TRACKER = (
    "# Tracker\r\n\r\nStates: Planned.\r\n\r\n"
    "| ID | Title | Review | State | Commit | Note |\r\n"
    "|---|---|---|---|---|---|\r\n"
    "| [XY-1](XY-1.md) | First thing | standard | Planned |  | has \\| bar |\r\n"
    "| [XY-2](XY-2.md) | Second thing | standard | Done    | abc1234 |  |\r\n"
)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        home = os.path.join(self.base, "home")
        os.makedirs(home)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "CLAUDE_PROJECT_DIR"}
        self.env.update(HOME=home, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        self.root = os.path.join(self.base, "proj")
        os.makedirs(self.root)
        write(os.path.join(self.root, ".claude/harness.json"), '{"tickets": {}}\n')

    def git(self, cwd, *args):
        p = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, (args, p.stderr))
        return p.stdout.strip()

    def board(self, *args, cwd=None):
        p = subprocess.run([sys.executable, BIN, *args], cwd=cwd or self.root, env=self.env,
                           capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr


class SetState(Base):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, "docs/tickets/tracker.md"), TRACKER)
        write(os.path.join(self.root, "docs/tickets/XY-1.md"),
              "﻿# XY-1: First thing\r\n\r\n## Status\r\n\r\nplanned (2026-01-01), batch A\r\n\r\n## Findings\r\n\r\nnone\r\n")
        write(os.path.join(self.root, "docs/tickets/XY-2.md"),
              "# XY-2: Second thing\n\n## Status\n\nDone\n")
        self.tracker = os.path.join(self.root, "docs/tickets/tracker.md")
        self.t1 = os.path.join(self.root, "docs/tickets/XY-1.md")

    def test_sets_row_and_status_keeping_everything_else(self):
        code, out, err = self.board("set-state", "xy-1", "In review", "--commit", "deadbee")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(read(self.tracker), TRACKER.replace(
            "| Planned |  |", "| In review | deadbee |"))
        self.assertEqual(read(self.t1), "﻿# XY-1: First thing\r\n\r\n## Status\r\n\r\n"
                                        "in review (2026-01-01), batch A\r\n\r\n## Findings\r\n\r\nnone\r\n")

    def test_width_is_kept_when_shorter(self):
        self.board("set-state", "XY-2", "Fixing")
        self.assertIn("| Fixing  | abc1234 |", read(self.tracker))
        self.board("set-state", "XY-2", "Done")
        self.assertEqual(read(self.tracker), TRACKER)      # round trip restores the bytes

    def test_same_state_is_unchanged(self):
        code, out, _ = self.board("set-state", "XY-2", "done")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("unchanged"), 2)
        self.assertEqual(read(self.tracker), TRACKER)

    def test_unknown_state_refused_and_nothing_written(self):
        code, _, err = self.board("set-state", "XY-1", "Almost")
        self.assertEqual(code, 1)
        self.assertIn("unknown state", err)
        self.assertEqual(read(self.tracker), TRACKER)

    def test_unknown_ticket_and_bad_commit_refused(self):
        code, _, err = self.board("set-state", "XY-9", "Done")
        self.assertEqual(code, 1)
        self.assertIn("XY-9", err)
        code, _, err = self.board("set-state", "XY-1", "Done", "--commit", "a|b")
        self.assertEqual(code, 1)
        self.assertIn("not a commit id", err)
        self.assertEqual(read(self.tracker), TRACKER)

    def test_missing_status_line_writes_nothing(self):
        write(self.t1, "# XY-1: First thing\n\nno status here\n")
        code, _, err = self.board("set-state", "XY-1", "Done")
        self.assertEqual(code, 1)
        self.assertIn("Status", err)
        self.assertEqual(read(self.tracker), TRACKER)

    def test_status_without_a_known_state_is_refused_untouched(self):
        text = "# XY-1: First thing\n\n## Status\n\nAwaiting review (2026-11-01), batch A\n"
        write(self.t1, text)
        code, _, err = self.board("set-state", "XY-1", "Blocked")
        self.assertEqual(code, 1)
        self.assertIn("does not start with a known state", err)
        self.assertEqual(read(self.t1), text)
        self.assertEqual(read(self.tracker), TRACKER)

    def test_only_the_tracker_table_with_id_and_state_is_edited(self):
        other = "| A | B |\n|---|---|\n| XY-1 | Planned |\n\n"
        second = "\n| ID | Title | Review | State | Commit | Note |\n|---|---|---|---|---|---|\n| XY-1 | dup | x | Planned |  |  |\n"
        write(self.tracker, TRACKER.replace("| ID |", other + "| ID |", 1).rstrip("\r\n") + "\r\n" + second.replace("\n", "\r\n"))
        before = read(self.tracker)
        self.assertEqual(self.board("set-state", "XY-1", "Fixing")[0], 0, before)
        after = read(self.tracker)
        self.assertEqual(after.count("Fixing"), 1)
        self.assertIn("| XY-1 | Planned |", after)             # the unrelated table
        self.assertIn("| [XY-1](XY-1.md) | First thing | standard | Fixing  |", after)

    def test_duplicate_row_refused(self):
        write(self.tracker, TRACKER + "| [XY-1](XY-1.md) | again | standard | Planned |  |  |\r\n")
        before = read(self.tracker)
        code, _, err = self.board("set-state", "XY-1", "Done")
        self.assertEqual(code, 1)
        self.assertIn("2 tracker rows", err)
        self.assertEqual(read(self.tracker), before)

    def test_symlink_target_refused(self):
        real = os.path.join(self.base, "real.md")
        write(real, read(self.t1))
        os.unlink(self.t1)
        os.symlink(real, self.t1)
        code, _, err = self.board("set-state", "XY-1", "Done")
        self.assertEqual(code, 1)
        self.assertIn("symlink", err)
        self.assertTrue(os.path.islink(self.t1))
        self.assertEqual(read(self.tracker), TRACKER)

    def test_symlinked_tracker_refused(self):
        real = os.path.join(self.base, "real-tracker.md")
        write(real, TRACKER)
        os.unlink(self.tracker)
        os.symlink(real, self.tracker)
        code, _, err = self.board("set-state", "XY-1", "Done")
        self.assertEqual(code, 1)
        self.assertTrue(os.path.islink(self.tracker))
        self.assertEqual(read(real), TRACKER)

    @unittest.skipIf(os.geteuid() == 0, "root ignores file modes")
    def test_read_only_file_or_folder_refused_before_any_write(self):
        os.chmod(self.t1, 0o444)
        self.addCleanup(os.chmod, self.t1, 0o644)
        code, _, err = self.board("set-state", "XY-1", "Done")
        self.assertEqual(code, 1)
        self.assertIn("not writable", err)
        self.assertEqual(read(self.tracker), TRACKER)          # the tracker was not touched either
        os.chmod(self.t1, 0o644)
        # a ticket in a read-only folder of its own
        feat = os.path.join(self.root, "docs/feat")
        write(os.path.join(feat, "XY-3.md"), "# XY-3: Third\n\n## Status\n\nplanned\n")
        write(os.path.join(self.root, ".claude/harness.json"), json.dumps({"tickets": {"featureGlobs": ["docs/feat"]}}))
        write(self.tracker, TRACKER + "| [XY-3](XY-3.md) | Third | standard | Planned |  |  |\r\n")
        before = read(self.tracker)
        os.chmod(feat, 0o555)
        self.addCleanup(os.chmod, feat, 0o755)
        code, _, err = self.board("set-state", "XY-3", "Done")
        self.assertEqual(code, 1)
        self.assertIn("folder", err)
        self.assertEqual(read(self.tracker), before)

    def test_half_write_is_rolled_back(self):
        cfg, _ = cli.load_tickets_config(self.root)
        real = state._write
        calls = []

        def flaky(path, text):
            calls.append(path)
            if path == self.t1 and len(calls) == 2:
                raise OSError("disk full")
            return real(path, text)
        with mock.patch.object(state, "_write", flaky):
            with self.assertRaises(state.Refused) as cm:
                state.set_state(self.root, cfg, "XY-1", "Done", "abc1234")
        self.assertIn("earlier writes were restored", str(cm.exception))
        self.assertEqual(read(self.tracker), TRACKER)
        self.assertEqual(calls[-1], self.tracker)               # the restore write

    def test_bad_usage(self):
        self.assertEqual(self.board("set-state", "XY-1")[0], 2)


class Archive(Base):
    def setUp(self):
        super().setUp()
        self.cfg = {"tickets": {"archive": True, "archiveTracker": "docs/tickets/archive/tracker.md",
                                "archiveDirs": ["docs/tickets/archive"]}}
        write(os.path.join(self.root, ".claude/harness.json"), json.dumps(self.cfg))
        head = "| ID | Title | State |\n|---|---|---|\n"
        write(os.path.join(self.root, "docs/tickets/tracker.md"),
              "# T\n\n" + head + "| [XY-1](XY-1.md) | One | Done |\n| [XY-2](XY-2.md) | Two | Planned |\n")
        write(os.path.join(self.root, "docs/tickets/archive/tracker.md"),
              "# A\n\n" + head + "| [XY-0](XY-0.md) | Zero | Done |\n\nfooter\n")
        for t, s in (("XY-0", "Done"), ("XY-1", "Done"), ("XY-2", "Planned")):
            base = "docs/tickets/archive" if t == "XY-0" else "docs/tickets"
            extra = "See [XY-1](XY-1.md) and [tracker](tracker.md).\n" if t == "XY-2" else ""
            write(os.path.join(self.root, base, f"{t}.md"), f"# {t}: Thing\n\n## Status\n\n{s}\n\n{extra}")

    def test_moves_row_file_and_links(self):
        self.assertEqual(self.board("--check")[0], 0)
        code, out, err = self.board("archive", "XY-1")
        self.assertEqual((code, err), (0, ""), out)
        main = read(os.path.join(self.root, "docs/tickets/tracker.md"))
        self.assertNotIn("XY-1", main)
        self.assertIn("XY-2", main)
        arch = read(os.path.join(self.root, "docs/tickets/archive/tracker.md"))
        self.assertIn("| [XY-1](XY-1.md) | One | Done |\n\nfooter", arch)    # row stays inside the table
        self.assertFalse(os.path.exists(os.path.join(self.root, "docs/tickets/XY-1.md")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "docs/tickets/archive/XY-1.md")))
        self.assertIn("[XY-1](archive/XY-1.md)", read(os.path.join(self.root, "docs/tickets/XY-2.md")))
        self.assertEqual(self.board("--check"), (0, "", ""))

    def test_refusals(self):
        self.assertEqual(self.board("archive", "XY-1")[0], 0)
        code, _, err = self.board("archive", "XY-1")
        self.assertEqual(code, 1)
        self.assertIn("already archived", err)
        write(os.path.join(self.root, ".claude/harness.json"), json.dumps({"tickets": {}}))
        code, _, err = self.board("archive", "XY-2")
        self.assertEqual(code, 1)
        self.assertIn("archive is off", err)


class ArchiveEdges(Archive):
    def loadcfg(self):
        return cli.load_tickets_config(self.root)[0]

    def test_not_done_refused(self):
        code, _, err = self.board("archive", "XY-2")
        self.assertEqual(code, 1)
        self.assertIn("not Done", err)
        self.assertTrue(os.path.exists(os.path.join(self.root, "docs/tickets/XY-2.md")))

    def test_name_clash_refused_untouched(self):
        write(os.path.join(self.root, "docs/tickets/archive/XY-1.md"), "# other\n")
        before = read(os.path.join(self.root, "docs/tickets/tracker.md"))
        code, _, err = self.board("archive", "XY-1")
        self.assertEqual(code, 1)
        self.assertIn("several ticket files", err)
        self.assertEqual(read(os.path.join(self.root, "docs/tickets/tracker.md")), before)
        self.assertEqual(read(os.path.join(self.root, "docs/tickets/archive/XY-1.md")), "# other\n")

    def test_crlf_archive_tracker_without_final_newline(self):
        a = os.path.join(self.root, "docs/tickets/archive/tracker.md")
        write(a, "# A\r\n\r\n| ID | Title | State |\r\n|---|---|---|\r\n| [XY-0](XY-0.md) | Zero | Done |")
        self.assertEqual(self.board("archive", "XY-1")[0], 0)
        self.assertEqual(read(a), "# A\r\n\r\n| ID | Title | State |\r\n|---|---|---|\r\n"
                                  "| [XY-0](XY-0.md) | Zero | Done |\r\n| [XY-1](XY-1.md) | One | Done |")
        self.assertEqual(self.board("--check")[0], 0)

    def test_crlf_archive_tracker_with_final_newline(self):
        a = os.path.join(self.root, "docs/tickets/archive/tracker.md")
        write(a, "# A\r\n\r\n| ID | Title | State |\r\n|---|---|---|\r\n| [XY-0](XY-0.md) | Zero | Done |\r\n")
        self.assertEqual(self.board("archive", "XY-1")[0], 0)
        self.assertEqual(read(a).count("\r\n"), 6)
        self.assertNotIn("\n|", read(a).replace("\r\n|", ""))

    def test_symlinked_archive_tracker_refused_before_anything_moves(self):
        a = os.path.join(self.root, "docs/tickets/archive/tracker.md")
        real = os.path.join(self.base, "real.md")
        write(real, read(a))
        os.unlink(a)
        os.symlink(real, a)
        before = read(os.path.join(self.root, "docs/tickets/tracker.md"))
        code, _, err = self.board("archive", "XY-1")
        self.assertEqual(code, 1)
        self.assertIn("symlink", err)
        self.assertTrue(os.path.exists(os.path.join(self.root, "docs/tickets/XY-1.md")))
        self.assertEqual(read(os.path.join(self.root, "docs/tickets/tracker.md")), before)

    def test_failed_write_restores_and_removes_the_new_file(self):
        real = state._write
        tracker = os.path.join(self.root, "docs/tickets/tracker.md")
        arch = os.path.join(self.root, "docs/tickets/archive/tracker.md")
        before = (read(tracker), read(arch))

        def flaky(path, text):
            if path == arch:
                raise OSError("disk full")
            return real(path, text)
        with mock.patch.object(state, "_write", flaky):
            with self.assertRaises(state.Refused) as cm:
                state.archive(self.root, self.loadcfg(), "XY-1")
        self.assertIn("restored", str(cm.exception))
        self.assertEqual((read(tracker), read(arch)), before)
        self.assertTrue(os.path.exists(os.path.join(self.root, "docs/tickets/XY-1.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "docs/tickets/archive/XY-1.md")))

    def test_partly_archived_says_what_is_where(self):
        with mock.patch.object(state.os, "unlink", side_effect=OSError("busy")):
            with self.assertRaises(state.Refused) as cm:
                state.archive(self.root, self.loadcfg(), "XY-1")
        msg = str(cm.exception)
        self.assertIn("partly archived", msg)
        self.assertIn("docs/tickets/archive/XY-1.md exists", msg)
        self.assertIn("docs/tickets/XY-1.md could not be removed", msg)


class Twin(Base):
    def setUp(self):
        super().setUp()
        cfg = {"tickets": {"twinPlatforms": ["ios", "android"], "idPrefixes": {
            "IOS-": {"platform": "ios", "kind": "feature"}, "AND-": {"platform": "android", "kind": "feature"}}}}
        write(os.path.join(self.root, ".claude/harness.json"), json.dumps(cfg))
        rows = "".join(f"| {t} | T {t} | {s} |\n" for t, s in
                       (("IOS-1", "In progress"), ("AND-1", "Planned"), ("IOS-2", "Done"), ("AND-2", "Done"), ("IOS-3", "Planned")))
        write(os.path.join(self.root, "docs/tickets/tracker.md"), "# T\n\n| ID | Title | State |\n|---|---|---|\n" + rows)
        for t in ("IOS-1", "AND-1", "IOS-2", "AND-2", "IOS-3"):
            write(os.path.join(self.root, f"docs/tickets/{t}.md"), f"# {t}: T {t}\n\n## Status\n\nPlanned\n")

    def test_open_closed_and_missing_twin(self):
        _, out, _ = self.board("twin", "IOS-1")
        self.assertIn("twin AND-1 (android) is Planned", out)
        self.assertIn("OPEN", out)
        _, out, _ = self.board("twin", "IOS-2")
        self.assertIn("is Done: closed", out)
        _, out, _ = self.board("twin", "IOS-3")
        self.assertIn("no twin ticket", out)

    def test_twin_never_changes_files(self):
        before = read(os.path.join(self.root, "docs/tickets/tracker.md"))
        self.board("twin", "IOS-1")
        self.assertEqual(read(os.path.join(self.root, "docs/tickets/tracker.md")), before)

    def test_off(self):
        write(os.path.join(self.root, ".claude/harness.json"), "{}")
        self.assertIn("twinPlatforms is off", self.board("twin", "IOS-1")[1])


class WalkThrough(Base):
    """start -> review -> fixing -> review -> close on a fixture with real git."""

    def setUp(self):
        super().setUp()
        self.git(self.root, "init", "-q", "-b", "main")
        write(os.path.join(self.root, "docs/tickets/tracker.md"),
              "# T\n\n| ID | Title | Review | State | Commit | Note |\n|---|---|---|---|---|---|\n"
              "| [XY-1](XY-1.md) | Thing | standard | Planned |  |  |\n")
        write(os.path.join(self.root, "docs/tickets/XY-1.md"), "# XY-1: Thing\n\n## Status\n\nplanned\n")
        write(os.path.join(self.root, ".gitignore"), "docs/tickets/board.html\n")
        self.git(self.root, "add", "-A")
        self.git(self.root, "commit", "-qm", "init")
        self.wt = os.path.join(self.base, "wt")

    def column(self):
        code, out, err = self.board("--json")
        self.assertEqual(code, 0, err)
        return next(t for t in json.loads(out)["tickets"] if t["id"] == "XY-1")["state"]

    def row_state(self, root):
        for line in read(os.path.join(root, "docs/tickets/tracker.md")).splitlines():
            if line.startswith("| [XY-1]"):
                return line.split("|")[4].strip()

    def status(self, root):
        return read(os.path.join(root, "docs/tickets/XY-1.md")).split("## Status")[1].split()[0:2]

    def step(self, state, expect_column, worktree=True, **kw):
        root = self.wt if worktree else self.root
        args = ["set-state", "XY-1", state] + (["--commit", kw["commit"]] if "commit" in kw else [])
        code, out, err = self.board(*args, cwd=root)
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.row_state(root), state)
        self.assertTrue(" ".join(self.status(root)).lower().startswith(state.lower()))
        self.assertEqual(self.column(), expect_column)

    def test_full_flow(self):
        self.git(self.root, "worktree", "add", "-q", "-b", "feature/xy-1", self.wt)
        # /start-ticket: In progress in the worktree; main is untouched
        self.step("In progress", "In progress")
        self.assertEqual(self.row_state(self.root), "Planned")
        # implementer: work, In review, one commit
        write(os.path.join(self.wt, "work.txt"), "work\n")
        self.step("In review", "In review")
        self.git(self.wt, "add", "-A")
        self.git(self.wt, "commit", "-qm", "feat: xy-1 thing")
        # /review starts: already In review, so nothing changes
        code, out, _ = self.board("set-state", "XY-1", "In review", cwd=self.wt)
        self.assertEqual(out.count("unchanged"), 2)
        self.assertEqual(self.git(self.wt, "status", "--short"), "")
        # CHANGES REQUIRED: Fixing
        self.step("Fixing", "Fixing")
        # implementer amends the one commit, back to In review
        write(os.path.join(self.wt, "work.txt"), "work, fixed\n")
        self.step("In review", "In review")
        self.git(self.wt, "add", "-A")
        self.git(self.wt, "commit", "-q", "--amend", "--no-edit")
        self.assertEqual(self.git(self.wt, "rev-list", "--count", "main..HEAD"), "1")
        self.assertEqual(self.git(self.wt, "status", "--short"), "")
        # /close-ticket: cherry-pick, then Done + SHA on main
        sha = self.git(self.wt, "rev-parse", "--short", "HEAD")
        self.git(self.root, "cherry-pick", sha)
        landed = self.git(self.root, "rev-parse", "--short", "HEAD")
        self.step("Done", "Done", worktree=False, commit=landed)
        self.assertIn(landed, read(os.path.join(self.root, "docs/tickets/tracker.md")))
        self.git(self.root, "worktree", "remove", self.wt)
        self.assertEqual(self.column(), "Done")
        self.assertEqual(self.board("--check"), (0, "", ""))
        self.assertEqual(self.board()[0], 0)       # the board rebuilds
        cell = next(l for l in read(os.path.join(self.root, "docs/tickets/tracker.md")).splitlines()
                    if l.startswith("| [XY-1]")).split("|")[5].strip()
        self.assertEqual(cell, landed)

    def test_closing_from_fixing_restores_committed_bytes(self):
        self.git(self.root, "worktree", "add", "-q", "-b", "feature/xy-1", self.wt)
        self.step("In review", "In review")
        write(os.path.join(self.wt, "work.txt"), "work\n")
        self.git(self.wt, "add", "-A")
        self.git(self.wt, "commit", "-qm", "feat: xy-1 thing")
        self.board("set-state", "XY-1", "Fixing", "--root", self.wt)
        self.assertNotEqual(self.git(self.wt, "status", "--short"), "")
        code, _, err = self.board("set-state", "XY-1", "In review", "--root", self.wt)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.git(self.wt, "status", "--short"), "")
        self.assertEqual(self.git(self.wt, "diff", "HEAD"), "")

    def test_parallel_tickets_conflict_recipe(self):
        """Two tickets on adjacent tracker rows: the cherry-pick of the second conflicts; taking
        the integration side of the tracker and the ticket file, then Done, ends clean."""
        t = read(os.path.join(self.root, "docs/tickets/tracker.md"))
        t += "| [XY-2](XY-2.md) | Other | standard | Planned |  |  |\n"
        write(os.path.join(self.root, "docs/tickets/tracker.md"), t)
        write(os.path.join(self.root, "docs/tickets/XY-2.md"), "# XY-2: Other\n\n## Status\n\nplanned\n")
        self.git(self.root, "add", "-A")
        self.git(self.root, "commit", "-qm", "second ticket")
        wts = {}
        for tid in ("XY-1", "XY-2"):
            wts[tid] = os.path.join(self.base, "wt-" + tid.lower())
            self.git(self.root, "worktree", "add", "-q", "-b", "feature/" + tid.lower(), wts[tid])
            self.board("set-state", tid, "In review", "--root", wts[tid])
            write(os.path.join(wts[tid], tid + ".txt"), "x\n")
            self.git(wts[tid], "add", "-A")
            self.git(wts[tid], "commit", "-qm", f"feat: {tid.lower()} work")
        self.git(self.root, "cherry-pick", "feature/xy-1")
        self.board("set-state", "XY-1", "Done", "--commit", "abcd123")
        self.git(self.root, "add", "-A")
        self.git(self.root, "commit", "-qm", "docs: close xy-1")
        p = subprocess.run(["git", "cherry-pick", "feature/xy-2"], cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0, "expected the adjacent rows to conflict")
        self.git(self.root, "checkout", "--ours", "docs/tickets/tracker.md", "docs/tickets/XY-2.md")
        self.git(self.root, "add", "-A")
        self.git(self.root, "-c", "core.editor=true", "cherry-pick", "--continue")
        self.assertEqual(self.board("set-state", "XY-2", "Done", "--commit", "beef123")[0], 0)
        self.assertEqual(self.board("--check"), (0, "", ""))
        self.assertEqual(self.git(self.root, "status", "--short"), " M docs/tickets/XY-2.md\n M docs/tickets/tracker.md".lstrip())


if __name__ == "__main__":
    unittest.main()
