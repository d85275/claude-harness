"""Tests for adopt mode (board/adopt.py) and the parser changes it needs. Temp repos only; HOME and
the global git config are isolated. Fixtures are made-up (tests/adopt_fixtures.py)."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "bin", "dev-harness-board")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import adopt_fixtures as fx  # noqa: E402
from board import cli, parse  # noqa: E402
from board.adopt import is_archive_name as is_archive  # noqa: E402


def snapshot(folder):
    out = {}
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x != ".git"]
        for n in files:
            p = os.path.join(d, n)
            try:
                with open(p, "rb") as f:
                    out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
            except OSError as e:                     # an unreadable file a test planted
                out[os.path.relpath(p, folder)] = f"unreadable {os.stat(p).st_mtime_ns} {e.errno}"
    return out


def changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = os.path.realpath(t.name)
        self.repo = os.path.join(self.base, "proj")
        os.makedirs(self.repo)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_"))}
        self.env.update({"HOME": self.base, "GIT_CONFIG_GLOBAL": os.path.join(self.base, "gc"),
                         "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})

    def bin(self, *args):
        p = subprocess.run([BIN, *args], cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def init_repo(self):
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, env=self.env, check=True)

    def path(self, rel):
        return os.path.join(self.repo, rel)

    def write(self, rel, text):
        fx.put(self.repo, rel, text)

    def read(self, rel):
        return fx.read(self.repo, rel)

    def switch(self, *flags):
        """The owner's flow: the dry run shows the list and its hash; the switch confirms that hash."""
        code, out, err = self.bin("init", "--check", *flags)
        m = re.search(r"^list hash: ([0-9a-f]{12})$", out, re.M)
        self.assertIsNotNone(m, (out, err))
        return self.bin("init", "--confirm", m.group(1), *flags)


def suggested(out):
    """The `tickets` block the report ends with."""
    body = out.split("== Suggested settings", 1)[1].split("\n", 1)[1].split("\n\n== ", 1)[0]
    return json.loads(body)


class ParserTests(Base):
    def rows(self, text, **cfg):
        self.write("docs/tickets/tracker.md", text)
        problems = []
        rows = parse.parse_tracker(self.path("docs/tickets/tracker.md"), "docs/tickets/tracker.md", False, problems)
        return rows, problems

    def test_code_span_with_a_bar_stays_in_its_cell(self):
        self.assertEqual(parse.split_row("| a | `x|y` | b \\| c |"), ["a", "`x|y`", "b | c"])
        rows, problems = self.rows("| ID | Title | State |\n|---|---|---|\n| AB-1 | uses `a|b` here | Done |\n")
        self.assertEqual((problems, rows[0].state, rows[0].title), ([], "Done", "uses `a|b` here"))

    def test_fenced_example_table_is_not_the_tracker(self):
        rows, problems = self.rows("```\n| ID | State |\n|---|---|\n| EX-1 | Done |\n```\n\n"
                                   "| ID | Title | State |\n|---|---|---|\n| AB-1 | Real | Planned |\n")
        self.assertEqual([r.id for r in rows], ["AB-1"])

    def test_duplicate_header_names_are_reported_and_the_first_is_used(self):
        rows, problems = self.rows("| ID | State | Note | Note |\n|---|---|---|---|\n| AB-1 | Done | one | two |\n")
        self.assertEqual(rows[0].note, "one")
        self.assertTrue(any("'note' appears 2 times" in p.message for p in problems), problems)

    def test_malformed_row_names_its_ticket_without_a_second_complaint(self):
        self.write("docs/tickets/AB-1.md", fx.ticket("AB-1", "T", "planned"))
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | too | many | cells |\n")
        model = cli.build_model(self.repo, cli.load_tickets_config(self.repo)[0])
        kinds = [(p.kind, p.ticket) for p in model.problems]
        self.assertIn(("row-malformed", "AB-1"), kinds)
        self.assertNotIn("file-no-row", [k for k, _ in kinds])

    def test_unreadable_file_does_not_also_lose_its_row(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.write("docs/tickets/AB-1.md", fx.ticket("AB-1", "T", "planned"))
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | T | Planned |\n")
        os.chmod(self.path("docs/tickets/AB-1.md"), 0)
        self.addCleanup(os.chmod, self.path("docs/tickets/AB-1.md"), 0o644)
        model = cli.build_model(self.repo, cli.load_tickets_config(self.repo)[0])
        kinds = [p.kind for p in model.problems]
        self.assertIn("file-malformed", kinds)
        self.assertNotIn("row-no-file", kinds)

    def test_ticket_id_from_file_name_prefix_or_heading(self):
        self.write(".claude/harness.json", json.dumps({"tickets": {"idPrefixes": {
            "AB": {"platform": "app", "kind": "feature"}, "K": {"platform": "app", "kind": "feature"}}}}))
        self.write("docs/tickets/AB-12-short-title.md", fx.ticket("AB-12", "Named by prefix", "planned"))
        self.write("docs/tickets/notes-on-it.md", fx.ticket("AB-13", "Named by heading only", "planned"))
        self.write("docs/tickets/K7.md", fx.ticket("K7", "No hyphen", "done"))
        self.write("docs/tickets/README.md", "# Folder notes\n")
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-12 | a | Planned |\n"
                   "| AB-13 | b | Planned |\n| K7 | c | Done |\n")
        model = cli.build_model(self.repo, cli.load_tickets_config(self.repo)[0])
        self.assertEqual(model.problems, [])
        self.assertEqual({t["id"]: t["file"] for t in model.tickets},
                         {"AB-12": "docs/tickets/AB-12-short-title.md", "AB-13": "docs/tickets/notes-on-it.md",
                          "K7": "docs/tickets/K7.md"})

    def test_heading_and_file_name_prefix_that_disagree_are_reported(self):
        self.write(".claude/harness.json", json.dumps({"tickets": {"idPrefixes": {"AB": {"platform": "app", "kind": "x"}}}}))
        self.write("docs/tickets/AB-1-x.md", fx.ticket("AB-2", "T", "planned"))
        model = cli.build_model(self.repo, cli.load_tickets_config(self.repo)[0])
        self.assertTrue(any("file name starts with AB-1" in p.message for p in model.problems), model.problems)

    def test_twin_line_pairs_tickets_with_different_numbers(self):
        self.write(".claude/harness.json", json.dumps({"tickets": {"twinPlatforms": ["desktop", "web"], "idPrefixes": {
            "DESK": {"platform": "desktop", "kind": "f"}, "WEB": {"platform": "web", "kind": "f"}}}}))
        rows = ["| ID | Title | State |", "|---|---|---|"]
        for tid, line in (("DESK-12", "Twin: WEB-9"), ("WEB-9", ""), ("WEB-12", "Twin: none (only here)"),
                          ("DESK-30", "Twin: WEB-99")):
            self.write(f"docs/tickets/{tid}.md", fx.ticket(tid, "T", "planned", f"\n{line}\n"))
            rows.append(f"| {tid} | t | Planned |")
        self.write("docs/tickets/tracker.md", "\n".join(rows) + "\n")
        model = cli.build_model(self.repo, cli.load_tickets_config(self.repo)[0])
        got = sorted(p.line() for p in model.problems)
        self.assertEqual(got, ["DESK-30: Twin line names WEB-99, which has no ticket file or row"])

    def test_only_the_first_tracker_table_is_read_and_a_later_one_is_counted(self):
        stats = parse.new_stats()
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n\n"
                   "| ID | Title | State |\n|---|---|---|\n| AB-2 | b | Done |\n")
        problems = []
        rows = parse.parse_tracker(self.path("docs/tickets/tracker.md"), "t.md", False, problems, stats=stats)
        self.assertEqual(([r.id for r in rows], [r.line for r in rows]), (["AB-1"], [3]))
        self.assertEqual((stats["extra_tables"], stats["skipped_rows"]), (["t.md:5"], ["t.md:7"]))


class ModeTests(Base):
    def test_hand_made_tracker_with_template_is_adopted_not_claimed(self):
        self.init_repo()
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | T | Planned |\n")
        self.write("docs/tickets/TEMPLATE.md", "# <ID>: <title>\n")
        self.write("docs/tickets/AB-1.md", fx.ticket("AB-1", "T", "planned"))
        code, out, err = self.bin("init", "--mode")
        self.assertEqual((code, out.splitlines()[0]), (0, "mode: adopt"), err)
        before = snapshot(self.repo)
        code, out, err = self.switch()
        self.assertEqual(code, 0, (out, err))
        self.assertIn("adopt mode", out)
        self.assertEqual(changed(before, snapshot(self.repo)),
                         [".claude/harness.json", "CLAUDE.md", "docs/tickets/board.html"])
        self.assertFalse(os.path.exists(self.path("docs/STATUS.md")))
        self.assertEqual(self.bin("init", "--mode")[1].splitlines()[0], "mode: switched")   # own board, check clean

    def test_fresh_and_resumed_init_stay_fresh(self):
        self.init_repo()
        self.assertEqual(self.bin("init", "--mode")[1].splitlines()[0], "mode: fresh")
        self.write("docs/tickets/tracker.md", fx.read(ROOT, "templates/tracker.md"))     # init's own empty tracker
        self.assertEqual(self.bin("init", "--mode")[1].splitlines()[0], "mode: fresh")
        self.write("docs/tickets/AB-1.md", fx.ticket("AB-1", "T", "planned"))
        self.assertEqual(self.bin("init", "--mode")[1].splitlines()[0], "mode: adopt")

    def test_ticket_folder_without_a_tracker_is_refused_and_writes_nothing(self):
        self.init_repo()
        self.write("docs/tickets/AB-1.md", fx.ticket("AB-1", "T", "planned"))
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check", "--prefix", "AB")
        self.assertEqual(code, 1, (out, err))
        self.assertIn("no tracker found", out)
        self.assertEqual(snapshot(self.repo), before)


def section(out, title):
    """The lines of one `== title ...` section of the output."""
    return out.split("== " + title, 1)[1].split("\n\n", 1)[0]


class AdoptA(Base):
    def make(self, planted):
        self.states = fx.make_a(self.repo, self.env, planted)

    def test_check_finds_every_planted_difference_and_writes_nothing(self):
        self.make(True)
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 1, err)       # blocked: a state with no alias
        self.assertEqual(snapshot(self.repo), before)
        self.assertFalse(os.path.exists(self.path(".claude")))
        for needle in (
                "unknown state names: 3", '"Integrated": "Owner check"', '"Waiting": "Blocked"',
                "'Frozen' (1 uses): no guess",
                "SVC-7: ticket file docs/tickets/SVC-7.md has no tracker row",
                "SVC-8: tracker row has no ticket file",
                "SVC-9: link 'missing-file.md' in docs/tickets/tracker.md points to a missing file",
                'twinPlatforms ["desktop", "web"] would report 1 twin-missing finding(s) (DESK-UI-30); left off',
                "twinPlatforms None",
                "ID prefixes seen", "DESK-UI, FEAT-WEB, SVC, WEB-UI, WIKI",
                "archiveTracker docs/tickets/archive/tracker-closed.md", "docs/features/*/tickets",
                "extra tracker columns: ['Summary', 'Impact']",
                "docs/tickets/archive/notes.md: no ticket ID in its file name or heading",
                "board script: scripts/tracker_board.py", "core.hooksPath is '.githooks'",
                "pre-push (git lfs)", "git lfs install", "old board: docs/tickets/board.html",
                "DIFFERENT SVC-7", "the switch is BLOCKED", "1 state name(s) unknown to the settings (no alias)"):
            self.assertIn(needle, out)
        self.assertIn("Planned", out.split("-- Ticket counts --")[1].split("--")[0])
        listing = section(out, "Tickets the plugin's board would show")
        self.assertIn("UNKNOWN state 'Frozen': shown as Planned", listing)
        self.assertIn("'Integrated' read as Owner check by a suggested alias", listing)
        self.assertIn("file docs/tickets/SVC-7.md; row (no row)", listing)
        self.assertIn("file (none); row docs/tickets/tracker.md:", listing)
        self.assertIn("old board: (not on it)", listing)

    def test_list_names_every_ticket_with_its_column_and_sources(self):
        self.make(False)
        out = self.bin("init", "--check")[1]
        listing = section(out, "Tickets the plugin's board would show")
        self.assertTrue(listing.startswith(": 8 =="), listing)
        cols, col = {}, None
        for line in listing.splitlines()[2:]:
            if not line.startswith(" "):
                col = line.split(" (")[0]
            elif not line.startswith("      "):
                cols[line.split()[0]] = col
        self.assertEqual(cols, self.states)       # the same columns as the old board
        self.assertIn("  WEB-UI-9  Sign-in screen spacing (web)\n      file docs/tickets/WEB-UI-9-short-title.md; "
                      "row docs/tickets/tracker.md:16;", out)
        self.assertIn("row docs/tickets/archive/tracker-closed.md:5", listing)
        unused = section(out, "Not used")
        self.assertTrue(unused.startswith(": 2 =="), unused)
        self.assertIn("docs/tickets/archive/notes.md: no ticket ID in its file name or heading", unused)
        self.assertIn("docs/tickets/TEMPLATE.md: the ticket template", unused)
        self.assertNotIn("[BLOCKS]", unused)
        self.assertIn("suggested state aliases are guesses", section(out, "Could not tell"))

    def test_suggested_settings_make_the_board_check_pass_with_no_ticket_changed(self):
        self.make(False)
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check")
        self.assertEqual((code, snapshot(self.repo)), (0, before), (out, err))
        block = suggested(out)
        t = block["tickets"]
        self.assertEqual(t["archiveDirs"], ["docs/tickets/archive"])
        self.assertEqual(t["featureGlobs"], ["docs/features/*/tickets"])
        self.assertEqual(t["stateAliases"], {"Integrated": "Owner check", "Waiting": "Blocked"})
        self.assertEqual(t["twinPlatforms"], ["desktop", "web"])
        self.assertEqual(set(t["idPrefixes"]), {"WEB-UI", "WIKI", "FEAT-WEB", "DESK-UI", "SVC"})
        self.write(".claude/harness.json", json.dumps(block))        # the owner applies the block
        applied = snapshot(self.repo)
        code, out, err = self.bin("--check")
        self.assertEqual((code, out), (0, ""), err)
        self.assertEqual(changed(before, snapshot(self.repo)), [".claude/harness.json"])
        self.assertEqual(applied, snapshot(self.repo))                # --check wrote nothing either

    def test_switch_needs_the_confirmed_hash_and_proves_tickets_unchanged(self):
        self.make(False)
        before = snapshot(self.repo)
        code, out, err = self.bin("init")                             # no --confirm: shows the list, writes nothing
        self.assertEqual((code, snapshot(self.repo)), (1, before), err)
        self.assertIn("not confirmed: nothing written", out)
        self.assertIn("run: dev-harness-board init --confirm ", out)
        self.assertEqual(self.bin("status")[0], 1)                    # not opted in yet
        code, out, err = self.switch()
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(changed(before, snapshot(self.repo)), [".claude/harness.json", "docs/tickets/board.html"])
        self.assertIn("same sha256 before and after", out)
        self.assertIn("will replace the existing file docs/tickets/board.html", out)
        self.assertIn("CLAUDE.md already has a Harness section; not edited", out)
        self.assertEqual(self.bin("--check")[:2], (0, ""))
        self.assertEqual(self.bin("status")[0], 0)                    # the switch is what opts the project in
        self.assertEqual(self.bin("init", "--mode")[1].splitlines()[0], "mode: switched")
        before = snapshot(self.repo)
        code, out, err = self.bin("init")
        self.assertEqual((code, snapshot(self.repo)), (0, before), err)
        self.assertIn("nothing to change", out)
        # nothing was installed and nothing of the old system was removed
        p = subprocess.run(["git", "config", "--local", "core.hooksPath"], cwd=self.repo, env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), ".githooks")
        self.assertFalse(os.path.exists(self.path(".git/dev-harness")))
        self.assertTrue(os.path.exists(self.path("scripts/tracker_board.py")))

    def test_a_list_that_changed_after_it_was_shown_is_not_applied(self):
        self.make(False)
        out = self.bin("init", "--check")[1]
        shown = re.search(r"^list hash: (\w+)$", out, re.M).group(1)
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md").replace("| Planned |", "| Done |"))
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--confirm", shown)
        self.assertEqual((code, snapshot(self.repo)), (1, before), err)
        self.assertIn(f"the list changed since it was confirmed (confirmed {shown}, now ", out)
        self.assertNotEqual(shown, re.search(r"^list hash: (\w+)$", out, re.M).group(1))

    def test_the_hash_covers_the_settings_written(self):
        self.make(False)
        h = [re.search(r"^list hash: (\w+)$", self.bin("init", "--check", *f)[1], re.M).group(1)
             for f in ((), ("--branch", "main"), ("--branch", "develop"))]
        self.assertEqual(h[0], h[1])
        self.assertNotEqual(h[0], h[2])

    def test_old_board_difference_is_shown_but_the_owner_decides(self):
        self.make(False)
        self.states["SVC-3"] = "Done"                                  # the old board shows it elsewhere
        self.write("docs/tickets/board.html", fx.old_board_html(self.states))
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check")
        self.assertEqual((code, snapshot(self.repo)), (0, before), err)
        self.assertIn("DIFFERENT SVC-3: old board Done, plugin board Planned", out)
        self.assertIn("old board: Done", section(out, "Tickets the plugin's board would show"))
        code, out, err = self.switch()
        self.assertEqual(code, 0, (out, err))

    def test_unreadable_old_board_is_said_and_does_not_block(self):
        self.make(False)
        self.write("docs/tickets/board.html", "<html>no data here</html>")
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0, (out, err))
        self.assertIn("the old board docs/tickets/board.html could not be compared", out)
        self.assertIn("old board docs/tickets/board.html: no embedded ticket data", section(out, "Could not tell"))

    def test_existing_settings_win_and_the_scan_says_so(self):
        self.make(False)
        self.write(".claude/harness.json", json.dumps({"tickets": {"stateAliases": {"Integrated": "Done"}}}))
        code, out, err = self.bin("init", "--check")
        self.assertIn('add to your tickets.stateAliases: {"Waiting": "Blocked"}', out)
        self.assertIn("DIFFERENT", out)          # Integrated as Done is not what the old board shows
        self.assertEqual(code, 1)                # Waiting has no alias in the kept settings


class AdoptB(Base):
    def make(self, planted):
        fx.make_b(self.repo, self.env, planted)

    def test_check_finds_every_planted_difference_and_writes_nothing(self):
        self.make(True)
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check", "--branch", "integration")
        self.assertEqual((code, snapshot(self.repo)), (1, before), err)
        for needle in ("'Frozen' (1 uses): no guess", '"Integrated": "Owner check"', '"Proposed": "Planned"',
                       "Q9: ticket file docs/tickets/Q9.md has no tracker row", "Q8: tracker row has no ticket file",
                       "K9: link 'K9-missing.md' in docs/tickets/tracker.md points to a missing file",
                       "K, OPS, Q, Z, ZED", "extra tracker columns: ['Weight']",
                       "integrationBranch: integration (from --branch", "no old board found",
                       "board script: none found", "core.hooksPath: not set", "No old board script or hook folder"):
            self.assertIn(needle, out)
        self.assertEqual(suggested(out)["tickets"]["integrationBranch"], "integration")

    def test_suggested_settings_make_the_board_check_pass_with_no_ticket_changed(self):
        self.make(False)
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check", "--branch", "integration")
        self.assertEqual((code, snapshot(self.repo)), (0, before), (out, err))
        self.assertIn("'Proposed' read as Planned by a suggested alias", out)
        block = suggested(out)
        self.write(".claude/harness.json", json.dumps(block))
        self.assertEqual(self.bin("--check"), (0, "", ""))
        self.assertEqual(changed(before, snapshot(self.repo)), [".claude/harness.json"])
        # the same ticket set with the plugin's board: no hyphen IDs are read
        model = cli.build_model(self.repo, cli.load_tickets_config(self.repo)[0])
        self.assertEqual([t["id"] for t in model.tickets], ["K1", "K2", "OPS-01", "Q1", "Q2", "Z1", "ZED-01", "ZED-02"])

    def test_switch_without_an_old_board_goes_ahead_and_says_nothing_was_compared(self):
        self.make(False)
        code, out, err = self.switch("--branch", "integration")
        self.assertEqual(code, 0, (out, err))
        self.assertIn("no old board found: nothing to compare with", out)
        self.assertIn("will create docs/tickets/board.html", out)
        self.assertEqual(json.loads(self.read(".claude/harness.json"))["tickets"]["integrationBranch"], "integration")

    def test_row_without_file_says_when_its_link_points_at_an_unread_file(self):
        self.make(False)
        self.write("docs/elsewhere/Q5.md", fx.ticket("Q5", "Kept in another folder", "planned"))
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md")
                   + "| [Q5](../elsewhere/Q5.md) | Kept elsewhere | Low | self | Planned | | |\n")
        code, out, err = self.bin("init", "--check")
        self.assertIn("Q5: tracker row has no ticket file (its link '../elsewhere/Q5.md' exists", out)
        self.assertIn("docs/elsewhere/Q5.md: a ticket file outside the folders the settings read", out)
        self.assertEqual(code, 1)

    def test_tracker_without_a_table_blocks_the_switch(self):
        self.make(False)
        self.write("docs/tickets/tracker.md", "# Tracker\n\nNo table here.\n")
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 1)
        self.assertIn("the tracker could not be read as a table", out)
        code, out, err = self.bin("init", "--confirm", re.search(r"^list hash: (\w+)$", out, re.M).group(1))
        self.assertEqual((code, snapshot(self.repo)), (1, before))
        self.assertIn("BLOCKED", out)

    def test_prefix_override_replaces_the_guess(self):
        self.make(False)
        code, out, err = self.bin("init", "--check", "--prefix", "K:android:bug")
        self.assertEqual(suggested(out)["tickets"]["idPrefixes"]["K"], {"platform": "android", "kind": "bug"})
        self.assertIn("--confirm ", out)
        self.assertIn(" --prefix K:android:bug", out)                 # the printed command repeats the flags


class AdoptModeRule(Base):
    def make(self, planted):
        self.states = fx.make_a(self.repo, self.env, planted)

    def test_applying_the_suggested_block_keeps_adopt_mode_and_the_list(self):
        self.make(False)
        self.states["SVC-3"] = "Done"
        self.write("docs/tickets/board.html", fx.old_board_html(self.states))
        block = suggested(self.bin("init", "--check")[1])
        self.write(".claude/harness.json", json.dumps(block))          # the owner applies the full block
        self.assertEqual(self.bin("init", "--mode")[1].splitlines()[0], "mode: adopt")
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0, err)
        self.assertIn("DIFFERENT SVC-3", out)
        self.assertEqual(self.switch()[0], 0)
        self.assertFalse(os.path.exists(self.path("docs/STATUS.md")))   # adopt never creates STATUS, TEMPLATE or tracker

    def test_dry_run_says_would_for_every_line(self):
        self.make(False)
        out = self.bin("init", "--check")[1]
        self.assertIn("would replace the existing file docs/tickets/board.html (the board)", out)
        self.assertIn("would create .claude/harness.json", out)

    def test_old_board_with_nameless_and_duplicate_entries_is_could_not_tell(self):
        self.make(False)
        page = fx.old_board_html(self.states)
        page = page.replace('"tickets": [', '"tickets": [{"state": "done"}, {"id": "SVC-3", "state": "planned"}, ')
        self.write("docs/tickets/board.html", page)
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0)
        cnt = section(out, "Could not tell")
        self.assertIn("1 entry of the old board have no id", cnt)
        self.assertIn("more than once (the last one was compared): SVC-3", cnt)

    def test_markdown_extension_broken_links_and_fifos_are_listed(self):
        self.make(False)
        self.write("docs/tickets/SVC-5.markdown", "# SVC-5: t\n")
        os.symlink("nowhere.md", self.path("docs/tickets/SVC-6.md"))
        os.mkfifo(self.path("docs/tickets/SVC-4.md"))                  # never opened: it would block a read
        os.mkfifo(self.path("docs/pipe.md"))
        code, out, err = self.bin("init", "--check")
        unused = section(out, "Not used")
        for rel in ("docs/tickets/SVC-5.markdown", "docs/tickets/SVC-6.md", "docs/tickets/SVC-4.md", "docs/pipe.md"):
            self.assertIn(f"{rel}: not read: only regular .md files are", unused)

    def test_archive_tracker_without_a_table_is_named(self):
        self.make(False)
        self.write("docs/tickets/archive/tracker-old.md", "# nothing here\n")
        code, out, err = self.bin("init", "--check")
        self.assertIn("found docs/tickets/archive/tracker-old.md but could not use it: it has no table", out)
        self.assertIn("archive tracker docs/tickets/archive/tracker-old.md: it has no table", section(out, "Could not tell"))

    def test_hooks_in_any_config_scope_and_called_scripts_are_reported(self):
        self.make(False)
        subprocess.run(["git", "config", "--local", "--unset", "core.hooksPath"], cwd=self.repo, env=self.env, check=True)
        with open(os.path.join(self.base, "gc"), "w") as f:
            f.write("[core]\n\thooksPath = .githooks\n")
        self.write("scripts/render.py", "# made up\n")
        self.write(".githooks/post-merge", "#!/bin/sh\npython3 scripts/render.py\n")
        out = self.bin("init", "--check")[1]
        self.assertIn("core.hooksPath is '.githooks' (global config)", out)
        self.assertIn("remove it there", out)
        self.assertIn("scripts/render.py (called by hook .githooks/post-merge)", out)
        retire = section(out, "Retiring the old board")
        self.assertIn("remove scripts/render.py", retire)
        self.assertIn("git lfs install", retire)

    def test_lfs_warning_is_per_hook_name(self):
        self.make(False)
        out = self.bin("init", "--check")[1]
        self.assertIn(".githooks/pre-push runs Git LFS, but <git dir>/hooks/pre-push does not exist", out)
        self.write(".git/hooks/pre-push", "#!/bin/sh\ngit lfs pre-push\n")
        self.assertNotIn("runs Git LFS", self.bin("init", "--check")[1])
        self.write(".githooks/post-checkout", "#!/bin/sh\ngit lfs post-checkout\n")
        self.assertIn(".githooks/post-checkout runs Git LFS, but <git dir>/hooks/post-checkout does not exist",
                      self.bin("init", "--check")[1])


class ShapeBase(Base):
    def proj(self, rows, files, tracker="docs/tickets/tracker.md", head="| ID | Title | State |\n|---|---|---|\n", git=True):
        if git:
            self.init_repo()
        folder = os.path.dirname(tracker)
        for tid, status in files.items():
            self.write(f"{folder}/{tid}.md", fx.ticket(tid, "T", status))
        self.write(tracker, "# Tracker\n\n" + head + "".join(r + "\n" for r in rows))

    def mode(self):
        return self.bin("init", "--mode")[1].splitlines()[0]

    def blocked(self, *needles):
        """--check is blocked, names each needle, and nothing gets written even with the shown hash."""
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 1, (out, err))
        for n in needles:
            self.assertIn(n, out)
        shown = re.search(r"^list hash: (\w+)$", out, re.M).group(1)
        code, out2, err = self.bin("init", "--confirm", shown)
        self.assertEqual((code, snapshot(self.repo)), (1, before), out2)
        self.assertIn("the switch is BLOCKED", out2)
        return out


class Shapes(ShapeBase):
    def test_tracker_outside_docs_tickets_is_adopted_and_nothing_empty_is_created(self):
        self.proj(["| PX-1 | a | Done |", "| PX-2 | b | In progress |"], {"PX-1": "done", "PX-2": "in progress"},
                  tracker="planning/tracker.md")
        self.assertEqual(self.mode(), "mode: adopt")
        before = snapshot(self.repo)
        code, out, err = self.switch()
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(changed(before, snapshot(self.repo)), [".claude/harness.json", "CLAUDE.md", "planning/board.html"])
        t = json.loads(self.read(".claude/harness.json"))["tickets"]
        self.assertEqual((t["trackerPath"], t["dirs"], t["boardPath"]), ("planning/tracker.md", ["planning"], "planning/board.html"))
        self.assertFalse(os.path.exists(self.path("docs")))

    def test_partly_configured_project_gets_a_report_and_is_blocked(self):
        self.proj(["| AB-1 | a | Done |", "| ZZ-1 | b | In progress |"], {"AB-1": "done", "ZZ-1": "in progress"})
        self.write(".claude/harness.json", json.dumps({"tickets": {"idPrefixes": {"AB": {"platform": "app", "kind": "f"}}}}))
        self.assertEqual(self.mode(), "mode: adopt")
        self.blocked("tickets.idPrefixes in harness.json lacks ZZ")

    def test_generic_mode_keeps_the_whole_stem_rule(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        for name in ("ISO-8601-notes.md", "phase-2-plan.md", "release-2024-notes.md"):
            self.write(f"docs/tickets/{name}", "# Notes\n\nplain\n")
        self.write("docs/tickets/spec.md", "# UTF-8: encoding notes\n\nplain\n")
        self.assertEqual(self.bin("--check"), (0, "", ""))

    def test_name_prefix_file_is_skipped_when_the_id_file_exists(self):
        self.write(".claude/harness.json", json.dumps({"tickets": {"idPrefixes": {"AB": {"platform": "app", "kind": "f"}}}}))
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/AB-1-handoff.md", "# Handoff for AB-1\n")
        self.assertEqual(self.bin("--check"), (0, "", ""))

    def test_id_cell_with_trailing_text_is_the_id(self):
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| [AB-5](AB-5.md) :warning: | a | Done |\n"
                   "| AB-6 :x: | b | Done |\n")
        rows = parse.parse_tracker(self.path("docs/tickets/tracker.md"), "t", False, [])
        self.assertEqual([(r.id, r.link) for r in rows], [("AB-5", "AB-5.md"), ("AB-6", None)])

    def test_a_later_tracker_table_and_a_legend_are_listed_and_the_later_table_blocks(self):
        self.proj([], {f"AB-{i}": "done" for i in range(1, 4)})
        self.write("docs/tickets/tracker.md", "# T\n\n## Open\n\n| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n\n"
                   "## Later\n\n| ID | Title | State |\n|---|---|---|\n| AB-2 | c | Done |\n\n"
                   "| Key | Meaning |\n|---|---|\n| x | legend |\n")
        out = self.blocked("1 table(s) with ID and State read, 1 row(s) read, 1 row(s) skipped, 2 other table(s) skipped",
                           "under Not used are marked [BLOCKS]")
        unused = section(out, "Not used")
        self.assertIn("docs/tickets/tracker.md:11: a later table with ID and State columns: only the first table", unused)
        self.assertIn("docs/tickets/tracker.md:13: row not read: in a later table with ID and State columns", unused)
        self.assertIn("docs/tickets/tracker.md:15: table without ID and State columns (not a tracker table)", unused)
        self.assertIn("AB-2: ticket file docs/tickets/AB-2.md has no tracker row", out)

    def test_skipped_rows_are_named_and_block(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | too | many | cells |"], {"AB-1": "done", "AB-2": "done"})
        out = self.blocked("rows SKIPPED at: docs/tickets/tracker.md:", "under Not used are marked [BLOCKS]")
        self.assertIn("docs/tickets/tracker.md:6: row not read: row has 4 cells, header has 3 [BLOCKS]", out)

    def test_ticket_without_a_readable_state_is_counted_and_blocks(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | b |  |"], {"AB-1": "done"})
        self.write("docs/tickets/AB-2.md", "# AB-2: b\n\nno status section\n")
        out = self.blocked("tickets with no readable state (no row state and no Status line): 1: AB-2",
                           "ticket(s) have no readable state")
        self.assertIn("NO STATE: shown as Planned", out)
        self.assertIn("AB-2: no state in its row or Status line", section(out, "Could not tell"))

    def test_harness_json_that_cannot_be_edited_blocks_the_switch(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write(".claude/real.json", '{"version": 1}')
        os.symlink("real.json", self.path(".claude/harness.json"))
        self.blocked("harness.json cannot be edited")
        self.assertFalse(os.path.exists(self.path("docs/tickets/board.html")))

    def test_unreadable_file_is_listed_and_blocks_and_the_switch_hashes_every_file(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/notes.md", "# Notes\n")
        self.write("docs/tickets/locked.md", "# Locked\n")
        os.chmod(self.path("docs/tickets/locked.md"), 0)
        self.addCleanup(os.chmod, self.path("docs/tickets/locked.md"), 0o644)
        out = self.blocked("under Not used are marked [BLOCKS] (docs/tickets/locked.md")
        self.assertIn("docs/tickets/locked.md: cannot be read (Permission denied) [BLOCKS]", out)
        os.chmod(self.path("docs/tickets/locked.md"), 0o644)
        code, out, err = self.switch()
        self.assertEqual(code, 0, (out, err))
        self.assertIn("  docs/tickets/notes.md\n", out.split("same sha256 before and after")[1])
        self.assertIn("  docs/tickets/locked.md\n", out.split("same sha256 before and after")[1])

    def test_unreadable_folder_is_named_and_blocks(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/archive/AB-2.md", fx.ticket("AB-2", "T", "done"))
        os.chmod(self.path("docs/tickets/archive"), 0)
        self.addCleanup(os.chmod, self.path("docs/tickets/archive"), 0o755)
        out = self.blocked("COULD NOT TELL: cannot read docs/tickets/archive")
        self.assertIn("docs/tickets/archive: folder cannot be read", section(out, "Not used"))

    def test_guessed_integration_branch_is_not_written(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/work"], cwd=self.repo, env=self.env, check=True)
        self.blocked("the checked-out branch is 'work'")
        self.assertEqual(self.switch("--branch", "work")[0], 0)

    def test_outside_git_says_so(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"}, git=False)
        code, out, err = self.bin("init", "--check")
        self.assertIn("core.hooksPath: not a git repository", out)
        self.assertIn("the default (not a git repository)", out)


class Round2(ShapeBase):
    """Layouts where the dry run once said "would go ahead" (or fresh) on a wrong picture."""

    def test_rows_after_a_blank_line_are_skipped_rows_for_check_and_init(self):
        self.proj([], {"AB-1": "done", "AB-2": "done"})
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n\n| AB-2 | after a blank | Done |\n")
        self.assertEqual(self.bin("--check")[:2], (1, "AB-2: ticket file docs/tickets/AB-2.md has no tracker row\n"))  # as on main
        self.blocked("rows SKIPPED at: docs/tickets/tracker.md:5",
                     "docs/tickets/tracker.md:5: row not read: row is outside any table")

    def test_another_table_with_an_id_like_column_blocks_and_a_status_column_is_not_a_state(self):
        self.proj([], {"AB-1": "done", "AB-2": "in progress"})
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n\nprose\n\n"
                   "| Ticket | Title | Phase |\n|---|---|---|\n| AB-9 | other | x |\n")
        self.blocked("docs/tickets/tracker.md:7: table with an ID-like first column or header (ticket | title | phase)")
        self.write("docs/tickets/tracker.md", "| ID | Title | Status |\n|---|---|---|\n| AB-1 | a | Done |\n| AB-2 | b | In progress |\n")
        self.blocked("table with an ID-like first column or header (id | title | status)")

    def test_tracker_with_other_names_depth_or_folder_words_is_adopted_not_fresh(self):
        self.proj([], {}, tracker="planning/TICKETS.md")
        self.write("planning/PX-1.md", fx.ticket("PX-1", "a", "done"))
        self.write("planning/TICKETS.md", "| ID | Title | State |\n|---|---|---|\n| PX-1 | a | Done |\n")
        self.assertEqual(self.mode(), "mode: adopt")
        self.assertIn("planning/TICKETS.md", self.bin("init", "--check")[1])
        for folder in ("docs/a/b/c/d", "docs/threshold"):
            self.write(f"{folder}/tracker.md", "| ID | Title | State |\n|---|---|---|\n| PX-2 | a | Done |\n")
            self.write(f"{folder}/PX-2.md", fx.ticket("PX-2", "a", "done"))
        os.unlink(self.path("planning/TICKETS.md"))
        os.unlink(self.path("planning/PX-1.md"))
        self.assertEqual(self.mode(), "mode: adopt")
        self.assertTrue(is_archive("old-tickets") and not is_archive("threshold") and not is_archive("folder"))
        for folder in ("docs/a/b/c/d",):
            os.unlink(self.path(f"{folder}/tracker.md"))
        out = self.bin("init", "--check")[1]
        self.assertIn("tracker: docs/threshold/tracker.md", out)
        self.assertIn('"archive": false', out)

    def test_ticket_files_alone_anywhere_make_it_adopt_and_an_odd_tracker_blocks(self):
        self.init_repo()
        self.write("work/notes/PX-4.md", fx.ticket("PX-4", "a", "planned"))
        self.assertEqual(self.mode(), "mode: adopt")
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 1)
        os.unlink(self.path("work/notes/PX-4.md"))
        self.assertEqual(self.mode(), "mode: fresh")
        self.write("docs/tickets/tracker.md", "| Ticket | Status |\n|---|---|\n")
        self.assertEqual(self.mode(), "mode: adopt")
        self.blocked("table with an ID-like first column or header (ticket | status)")

    def test_file_named_by_an_unconfigured_prefix_is_listed_with_why_and_blocks(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/ZZ-1.md", "# not a ticket\n")
        self.blocked("docs/tickets/ZZ-1.md: looks like ticket ZZ-1, but its prefix ZZ is not in idPrefixes")
        self.assertEqual(self.bin("init", "--check", "--prefix", "ZZ:app:feature")[0], 1)    # read now, but no state

    def test_rows_of_another_tracker_file_block(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/tracker-web.md", "| ID | Title | State |\n|---|---|---|\n| AB-20 | sibling | In progress |\n")
        self.blocked("rows from another tracker are not read: docs/tickets/tracker-web.md")

    def test_unreadable_ticket_file_with_a_row_blocks(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | b | Done |"], {"AB-1": "done"})
        with open(self.path("docs/tickets/AB-2.md"), "wb") as f:
            f.write(b"# AB-2: b\n\n## Status\n\nDone \xe9\n")
        out = self.blocked("docs/tickets/AB-2.md: ticket file cannot be read: cannot read ticket file")
        self.assertNotIn("row has no file", out)

    def test_blocked_runs_say_would_and_heading_only_prefixes_are_labelled(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | b |  |"], {"AB-1": "done", "AB-2": "done"})
        self.write("docs/tickets/spec.md", "# UTF-8: encoding notes\n\nplain\n")
        out = self.blocked("would create .claude/harness.json")
        self.assertNotIn("will create", out)
        self.assertIn("UTF: only in a heading (1), not in the file name or a tracker row; not suggested", out)

    def test_unreadable_folder_and_absolute_hooks_path_are_printed_once(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/archive/AB-2.md", fx.ticket("AB-2", "T", "done"))
        os.chmod(self.path("docs/tickets/archive"), 0)
        self.addCleanup(os.chmod, self.path("docs/tickets/archive"), 0o755)
        self.write(".githooks/post-commit", "#!/bin/sh\n")
        subprocess.run(["git", "config", "--local", "core.hooksPath", self.path(".githooks")], cwd=self.repo, env=self.env, check=True)
        out = self.bin("init", "--check")[1]
        self.assertEqual(out.count("COULD NOT TELL: cannot read docs/tickets/archive"), 1)
        self.assertEqual(out.count("hook folder "), 1)


class Round3(ShapeBase):
    """The two shapes that ended on a wrong picture after round 2 (N1, N2)."""

    def test_non_utf8_tracker_is_adopted_and_blocks(self):
        self.init_repo()
        self.write("docs/tickets/AB-1.md", "# Notes\n\nnot a ticket heading\n")     # nothing else looks like tickets
        os.unlink(self.path("docs/tickets/AB-1.md"))
        os.makedirs(self.path("docs/tickets"), exist_ok=True)
        with open(self.path("docs/tickets/tracker.md"), "wb") as f:
            f.write(b"| ID | Title | State |\n|---|---|---|\n| AB-1 | caf\xe9 | Done |\n")
        self.assertEqual(self.mode(), "mode: adopt")                     # was: fresh, and init wrote a new tracker
        before = snapshot(self.repo)
        self.blocked("docs/tickets/tracker.md: cannot be read (not UTF-8) [BLOCKS]")
        self.assertEqual(snapshot(self.repo), before)

    def test_non_utf8_ticket_file_alone_makes_it_adopt(self):
        self.init_repo()
        self.write("notes/README.md", "# Notes\n")
        with open(self.path("notes/PX-3.md"), "wb") as f:
            f.write(b"# PX-3: caf\xe9\n\n## Status\n\nplanned\n")
        self.assertEqual(self.mode(), "mode: adopt")
        self.assertIn("notes/PX-3.md (not UTF-8)", self.bin("init", "--mode")[1])
        with open(self.path("notes/other.md"), "wb") as f:                # not UTF-8 and not ticket-like: ignored
            f.write(b"# caf\xe9\n")
        os.unlink(self.path("notes/PX-3.md"))
        self.assertEqual(self.mode(), "mode: fresh")

    def test_ticket_file_outside_the_configured_folders_is_shown_and_blocks(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | b | Planned |"], {"AB-1": "done"})
        self.write("docs/drafts/AB-2.md", fx.ticket("AB-2", "b", "planned"))
        out = self.blocked("docs/drafts/AB-2.md: a ticket file outside the folders the settings read")
        self.assertIn("docs/drafts/AB-2.md: a ticket file outside the folders the settings read", section(out, "Not used"))
        self.write(".claude/harness.json", json.dumps({"tickets": {"dirs": ["docs/tickets", "docs/drafts"]}}))
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("file docs/drafts/AB-2.md; row docs/tickets/tracker.md:6", out)
        self.assertEqual(self.switch()[0], 0)
        self.assertEqual(self.bin("--check")[:2], (0, ""))


def hashed_part(out):
    """The part of --check output the list hash covers: the list, Not used and Could not tell."""
    return out.split("== Match against the old board", 1)[0]


class Coverage(ShapeBase):
    """The rule: every row and .md file in the scanned area that looks like a ticket is in the hashed
    output, in a column, under Not used with a reason, or under Could not tell."""

    def mixed_project(self):
        self.init_repo()
        tracker = ["# Tracker", "", "```", "| ID | State |", "|---|---|", "| EX-1 | Done |", "```", "",
                   "```make board``` rebuilds it.", "",
                   "| ID | Title | State |", "|---|---|---|",
                   "| AB-1 | one | Done |", "| AB-2 | two | In progress |", "| AB-2 | two again | Blocked |",
                   "|  | blank id | Planned |", "", "| AB-3 | after a blank line | Planned |", "",
                   "> | ID | Title | State |", "> |---|---|---|", "> | AB-4 | quoted | Planned |", "",
                   "<table><tr><td>AB-5</td><td>html</td></tr></table>", "",
                   "| ID | Decision |", "|---|---|", "| AB-6 | a decision |", "",
                   "AB-20 | no leading pipe | Planned |", "- AB-21: bullet", "## AB-22 heading",
                   "> | ID | State |", "> |---|---|", "> | 301 | Planned |", "", "```", "| AB-23 | fenced | Done |"]
        self.write("docs/tickets/tracker.md", "\n".join(tracker) + "\n")
        for tid in ("AB-1", "AB-2"):
            self.write(f"docs/tickets/{tid}.md", fx.ticket(tid, "t", "done"))
        self.write("docs/tickets/archive/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | old | Planned |\n")
        self.write("docs/tickets/ios/AB-7-login.md", fx.ticket("AB-7", "t", "planned"))
        self.write("docs/tickets/ios/notes.md", "# notes\n")
        self.write("docs/tickets/notes.md", "| Key | Summary |\n|---|---|\n| AB-8 | in a notes table |\n")
        self.write("docs/other/AB-9-flow.md", fx.ticket("AB-9", "t", "planned"))
        self.write("docs/other/AB-10.md", "# AB-10 no colon\n")
        self.write(".tickets/AB-11.md", fx.ticket("AB-11", "t", "planned"))
        self.write("a/b/c/d/e/f/g/AB-12.md", fx.ticket("AB-12", "t", "planned"))
        self.write("tests/fixtures/AB-13.md", fx.ticket("AB-13", "t", "planned"))
        self.write("docs/tickets/tracker-ios.md", "| ID | Title | State |\n|---|---|---|\n| 201 | n | Planned |\n")
        self.write("docs/tickets/login.md", "# Login\n\n## Status\n\nPlanned\n")
        self.write("docs/tickets/backlog-list.md", "# Notes\n\n- AB-30: planned\n")
        self.write("docs/other/ab-15-login.md", "# Login\n")
        self.write("docs/other/story2.md", "# Ticket AB-18: prefixed\n")
        self.write("work/ab-14.md", "---\nid: ab-14\n---\n")
        self.write(".hidden/ab-40-x.md", "# x\n")

    def test_every_ticket_like_row_and_file_is_in_the_hashed_output(self):
        from board import adopt
        self.mixed_project()
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 1, (out, err))
        part = hashed_part(out)
        missing = []
        for d, dirs, files in os.walk(self.repo):
            dirs[:] = [x for x in dirs if x != ".git"]
            for n in files:
                if not n.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(d, n), self.repo)
                text = self.read(rel)
                kind, _ = adopt.ticket_evidence(rel, text)
                rows = adopt._row_candidates(text)
                if (kind or rows) and rel not in part:
                    missing.append(rel)
                for line, tid, _ in rows:
                    if f"file {rel}" in part:
                        continue                    # a ticket file read as such
                    if f"{rel}:{line}" not in part:
                        missing.append(f"{rel}:{line} ({tid})")
        self.assertEqual(missing, [], part)

    def test_duplicate_rows_are_marked_listed_and_conflicting_states_block(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | b | In progress |", "| AB-2 | b again | Blocked |"],
                  {"AB-1": "done", "AB-2": "in progress"})
        self.write("docs/tickets/archive/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | old | Planned |\n")
        out = self.blocked("docs/tickets/tracker.md:7: another row of AB-2 (state 'Blocked'); the board uses "
                           "docs/tickets/tracker.md:6 (state 'In progress'): the states disagree [BLOCKS]",
                           "docs/tickets/archive/tracker.md:3: another row of AB-1 (state 'Planned'); the board uses "
                           "docs/tickets/tracker.md:5 (state 'Done'): the states disagree [BLOCKS]")
        self.assertIn("row docs/tickets/tracker.md:6, docs/tickets/tracker.md:7 (the first is used)", out)

    def test_rows_with_an_empty_id_are_listed_and_block(self):
        self.proj(["| AB-1 | a | Done |", "|  | blank | In progress |", "| ** ** | bold | Blocked |", "| [](AB-9.md) | link | Planned |"],
                  {"AB-1": "done"})
        out = self.blocked()
        for n in (6, 7, 8):
            self.assertIn(f"docs/tickets/tracker.md:{n}: row not read: row has an empty ID cell [BLOCKS]", out)

    def test_ticket_folder_without_a_tracker_is_adopt_mode_and_init_writes_nothing(self):
        self.init_repo()
        self.write("docs/tickets/AB-12-login.md", fx.ticket("AB-12", "t", "in progress"))
        self.write("docs/tickets/ios/AB-13-x.md", "# x\n")
        self.write("docs/tickets/7.md", "# 7: numeric\n")
        self.assertEqual(self.mode(), "mode: adopt")                     # was: fresh, and init wrote into the folder
        before = snapshot(self.repo)
        code, out, err = self.bin("init", "--prefix", "AB")
        self.assertEqual((code, snapshot(self.repo)), (1, before), (out, err))
        os.unlink(self.path("docs/tickets/AB-12-login.md"))
        os.unlink(self.path("docs/tickets/ios/AB-13-x.md"))
        self.assertEqual(self.mode(), "mode: adopt")                     # 7.md alone still counts

    def test_survey_uses_the_board_id_rule_and_names_the_places_it_did_not_read(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/other/AB-31-login-flow.md", fx.ticket("AB-31", "slug", "planned"))
        self.write("docs/other/AB-32.md", "# AB-32 no colon\n\nstatus: planned\n")
        self.write("a/b/c/d/e/f/g/AB-35.md", fx.ticket("AB-35", "deep", "planned"))
        self.write(".tickets/AB-44.md", fx.ticket("AB-44", "hidden", "planned"))
        out = self.blocked("docs/other/AB-31-login-flow.md: a ticket file outside the folders the settings read")
        cnt = section(out, "Could not tell")
        self.assertIn("docs/other/AB-32.md: its name, first heading or id line names AB-32", cnt)
        self.assertIn("a/b/c/d/e/f/g: not read (a dot folder or deeper than 6 levels); 1 file(s) named like tickets: "
                      "a/b/c/d/e/f/g/AB-35.md", cnt)
        self.assertIn(".tickets: not read (a dot folder or deeper than 6 levels); 1 file(s) named like tickets: "
                      ".tickets/AB-44.md", cnt)

    def test_files_in_subfolders_block_and_linked_folders_are_could_not_tell(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/ios/AB-41-login.md", fx.ticket("AB-41", "t", "planned"))
        self.write("docs/tickets/ios/note.md", "# note\n")
        self.write(os.path.join("..", "ext", "AB-50.md"), fx.ticket("AB-50", "t", "planned"))
        os.symlink(os.path.join(self.base, "ext"), self.path("docs/tickets/shared"))
        out = self.blocked("docs/tickets/ios/AB-41-login.md: in a subfolder the settings do not read",
                           "docs/tickets/ios/note.md: in a subfolder the settings do not read")
        self.assertIn("docs/tickets/shared: a linked folder, not followed", section(out, "Could not tell"))

    def test_plain_check_ignores_later_tables_and_stray_rows_as_on_main(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md")
                   + "\n| ID | Decision |\n|---|---|\n| AB-1 | keep it |\n\nprose\n| stray |\n")
        self.write(".claude/harness.json", json.dumps({"tickets": {}}))
        self.assertEqual(self.bin("--check"), (0, "", ""))
        out = self.blocked("table with an ID-like column but no State column (id | decision)",
                           "row not read: row is outside any table")

    def test_an_inline_fence_does_not_hide_the_rest_of_the_tracker(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done", "AB-2": "planned"})
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md")
                   + "\n```make board``` rebuilds it.\n\n| ID | Title | State |\n|---|---|---|\n| AB-2 | later | Planned |\n")
        self.blocked("a later table with ID and State columns", "docs/tickets/tracker.md:11: row not read")
        self.write(".claude/harness.json", json.dumps({"tickets": {}}))
        self.write("docs/tickets/tracker.md", "```make board``` first.\n\n| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n")
        self.assertEqual(self.bin("set-state", "AB-1", "In review")[0], 0)    # the table after it is the tracker
        self.assertIn("| AB-1 | a | In review |", self.read("docs/tickets/tracker.md"))

    def test_set_state_finds_a_row_whose_id_cell_has_trailing_text(self):
        self.proj(["| AB-1 :warning: | a | Planned |"], {"AB-1": "planned"})
        self.write(".claude/harness.json", json.dumps({"tickets": {}}))
        code, out, err = self.bin("set-state", "AB-1", "Done")
        self.assertEqual(code, 0, err)
        self.assertIn("| AB-1 :warning: | a | Done", self.read("docs/tickets/tracker.md"))

    def test_blank_row_state_says_the_file_decides_and_how_to_fix_it(self):
        self.proj(["| AB-1 | a | Done |", "| AB-3 | c |  |"], {"AB-1": "done", "AB-3": "done"})
        out = self.blocked("tracker row(s) with an empty State cell: AB-3; fill in the State cell of each row")
        self.assertIn("the row has no state; the file's Status (Done) decides", out)

    def test_title_column_is_part_of_the_settings_and_the_hash(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        out = self.bin("init", "--check")[1]
        self.assertEqual(suggested(out)["tickets"]["titleColumn"], "")
        h1 = re.search(r"^list hash: (\w+)$", out, re.M).group(1)
        self.write(".claude/harness.json", json.dumps({"tickets": {"titleColumn": "Title"}}))
        out = self.bin("init", "--check")[1]
        self.assertEqual(suggested(out)["tickets"]["titleColumn"], "Title")
        self.assertNotEqual(h1, re.search(r"^list hash: (\w+)$", out, re.M).group(1))

    def test_a_failed_board_write_is_said_and_not_verified(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("CLAUDE.md", "# P\n\n## Harness\n\n- x\n")
        os.chmod(self.path("docs/tickets"), 0o555)
        self.addCleanup(os.chmod, self.path("docs/tickets"), 0o755)
        code, out, err = self.switch()
        self.assertNotEqual(code, 0, out)
        self.assertIn("the board could not be written", out)
        self.assertIn("Already written: .claude/harness.json", out)
        self.assertNotIn("verified:", out)
        self.assertNotIn("Next: commit", out)

    def test_an_unreadable_folder_anywhere_blocks(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("specs/locked/AB-70.md", fx.ticket("AB-70", "t", "planned"))
        os.chmod(self.path("specs/locked"), 0)
        self.addCleanup(os.chmod, self.path("specs/locked"), 0o755)
        self.blocked("specs/locked: folder cannot be read: Permission denied [BLOCKS]")

    def test_confirm_outside_adopt_mode_is_bad_usage(self):
        self.init_repo()
        code, out, err = self.bin("init", "--confirm", "abc123")
        self.assertEqual(code, 2)
        self.assertIn("--confirm is only for adopt mode", err)

    def test_tickets_only_in_test_and_example_folders_are_shown_and_fresh_needs_a_flag(self):
        self.init_repo()
        self.write("tests/fixtures/proj/docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n")
        self.write("examples/demo/docs/tickets/AB-1.md", fx.ticket("AB-1", "a", "done"))
        self.assertEqual(self.mode(), "mode: adopt")                        # the list is shown, not skipped
        cnt = section(self.bin("init", "--check")[1], "Could not tell")
        self.assertIn("examples/demo/docs/tickets/AB-1.md: looks like ticket AB-1, in a test or example folder", cnt)
        self.assertIn("tests/fixtures/proj/docs/tickets/tracker.md: has a tracker-like table", cnt)
        code, out, err = self.bin("init", "--fresh", "--dry-run", "--prefix", "WEB")
        self.assertEqual(code, 0, err)
        self.assertIn("note: test or example folders hold ticket-like files, taken as test data", out)
        self.assertIn("would create docs/tickets/tracker.md", out)
        self.write("docs/tickets/AB-2.md", fx.ticket("AB-2", "b", "done"))        # real tickets: --fresh changes nothing
        self.assertEqual(self.bin("init", "--mode", "--fresh")[1].splitlines()[0], "mode: adopt")

    def test_output_ends_with_a_line_that_counts_everything(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        out = self.bin("init", "--check")[1]
        self.assertRegex(out.splitlines()[-1], r"^== end of output: 1 tickets, \d+ not used \(0 blocking\), "
                                                r"\d+ could not tell; list hash [0-9a-f]{12} ==$")


class Round5(ShapeBase):
    """The four shapes the 1.1.0 round-2 hunt found, and the minor notes with them."""

    def test_tracker_lines_that_are_not_pipe_rows_are_listed_and_block(self):
        self.proj(["| AB-1 | first | Done |", "AB-2 | no leading pipe | Planned |", "", "- AB-4: bullet, Blocked",
                   "## AB-5 heading style"], {"AB-1": "done"})
        out = self.blocked()
        self.assertIn("docs/tickets/tracker.md:6: row of AB-2 on a line that is not a table row: not read [BLOCKS]", out)
        # a list or heading line is prose: it names an unknown ID (could not tell) and never blocks
        self.assertNotIn("tracker.md:8: row of AB-4", out)
        self.assertIn("docs/tickets/tracker.md:8: names AB-4 on a line that is not a table row", section(out, "Could not tell"))

    def test_odd_ids_in_quoted_html_rows_and_other_table_files_block_with_counts(self):
        self.proj(["| AB-1 | first | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md")
                   + "\n> | ID | Title | State |\n> |---|---|---|\n> | 101 | quoted | Planned |\n> | #102 | q | Planned |\n\n"
                   "<table><tr><td>12</td><td>html</td></tr></table>\n")
        self.write("docs/tickets/tracker-ios.md", "| ID | Title | State |\n|---|---|---|\n| 201 | n | Planned |\n"
                   "| #202 | h | Blocked |\n| AB-14b | l | Planned |\n")
        self.write("docs/tickets/more.md", "| Ticket | State |\n|---|---|\n| 301 | Planned |\n| T-3.1 | Planned |\n")
        out = self.blocked("docs/tickets/tracker-ios.md: another tracker-like table: 3 row(s) not read "
                           "(first cells 201, #202, AB-14b); if the file is notes and no ticket or tracker, list it in tickets.notesFiles [BLOCKS]",
                           "docs/tickets/more.md: another tracker-like table: 2 row(s) not read (first cells 301, T-3.1); if the file is notes and no ticket or tracker, list it in tickets.notesFiles [BLOCKS]")
        for n, tid in ((9, "101"), (10, "#102")):
            self.assertIn(f"docs/tickets/tracker.md:{n}: row of {tid} in a block quote: not read [BLOCKS]", out)
        self.assertIn("docs/tickets/tracker.md:12: row of 12 an HTML table row: not read [BLOCKS]", out)

    def test_weak_evidence_covers_slugs_headings_and_id_lines(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        files = {"docs/other/ab-15-login.md": "# Login\n", "docs/other/story2.md": "# Ticket AB-18: prefixed\n",
                 "docs/other/story3.md": "# [AB-19] bracket\n", "docs/other/story4.md": "# AB_20: underscore\n",
                 "docs/other/story5.md": "# 21: numeric\n", "docs/other/AB-16b.md": "# AB-16b: letter\n",
                 "work/dotted.md": "# AB-24.1: dotted\n", "work/login.md": "# Login flow\n\nID: AB-23\n",
                 "docs/other/ab-13.md": "---\nid: ab-13\nstatus: planned\n---\n\nbody\n",
                 ".hidden/ab-40-x.md": "# x\n"}
        for rel, text in files.items():
            self.write(rel, text)
        cnt = section(self.bin("init", "--check")[1], "Could not tell")
        for rel in files:
            if not rel.startswith("."):
                self.assertIn(f"{rel}: its name, first heading or id line names", cnt)
        self.assertIn(".hidden: not read (a dot folder or deeper than 6 levels); 1 file(s) named like tickets: "
                      ".hidden/ab-40-x.md", cnt)

    def test_a_project_with_only_a_lower_case_ticket_file_is_not_fresh(self):
        self.init_repo()
        self.write("work/ab-13.md", "---\nid: ab-13\nstatus: planned\n---\n\nbody\n")
        self.assertEqual(self.mode(), "mode: adopt")

    def test_a_root_tracker_lists_subfolders_once_and_skips_tool_folders(self):
        self.init_repo()
        self.write("TICKETS.md", "# Tickets\n\n| ID | Title | State |\n|---|---|---|\n| AB-1 | a | Done |\n")
        self.write("AB-1.md", fx.ticket("AB-1", "a", "done"))
        self.write("docs/guide.md", "# Guide\n")
        self.write("docs/more.md", "# More\n")
        self.write("src/NOTES.md", "# s\n")
        self.write("node_modules/pkg/README.md", "# pkg\n")
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0, out)
        unused = section(out, "Not used")
        self.assertIn("docs: 2 .md file(s) in this folder are not read (add the folder to tickets.dirs", unused)
        self.assertIn("src: 1 .md file(s) in this folder are not read", unused)
        self.assertNotIn("node_modules/pkg", unused)
        self.assertNotIn("\n  : ", unused)

    def test_an_unclosed_fence_and_fenced_rows_in_a_tracker_block(self):
        self.proj(["| AB-1 | first | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md")
                   + "\n```text\n~~~\n```\n\n| ID | Title | State |\n|---|---|---|\n| AB-2 | later | Planned |\n\n```\n| AB-3 | x | Planned |\n")
        out = self.blocked("docs/tickets/tracker.md:15: a code fence opened here is never closed",
                           "docs/tickets/tracker.md:16: ticket-like row inside a code fence in a tracker: not read [BLOCKS]",
                           "docs/tickets/tracker.md:13: row not read: in a later table")

    def test_inside_the_ticket_folder_status_sections_id_headings_and_other_files_are_caught(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/login.md", "# Login redesign\n\n## Status\n\nIn progress\n")
        self.write("docs/tickets/AB-8b.md", "# AB-8b: letter\n\n## Status\n\nPlanned\n")
        self.write("docs/tickets/backlog-list.md", "# Notes\n\n- AB-30: planned\n- AB-31: blocked\n")
        self.write("docs/tickets/tracker.md", self.read("docs/tickets/tracker.md") + "\n| Key | Meaning |\n|---|---|\n| P | planned |\n")
        for n in ("AB-2.txt", "AB-3.mdx", "AB-4.yaml"):
            self.write(f"docs/tickets/{n}", "id: x\n")
        out = self.blocked("docs/tickets/login.md: has a Status section but no ticket ID the board reads",
                           "docs/tickets/AB-8b.md: looks like ticket AB-8B, which is not an ID the board reads")
        self.assertIn("docs/tickets/backlog-list.md:3: names AB-30 on a line that is not a table row", section(out, "Could not tell"))
        self.assertIn("table without ID and State columns (not a tracker table): 1 row(s), first cells P", out)
        self.assertIn("docs/tickets: 3 file(s) that are not .md, not read: AB-2.txt, AB-3.mdx, AB-4.yaml",
                      section(out, "Could not tell"))

    def test_marks_name_the_column_the_card_is_in(self):
        self.proj(["| AB-1 | a | Zork |"], {"AB-1": "done"})
        self.assertIn("UNKNOWN state 'Zork': shown as Done", self.bin("init", "--check")[1])


class Round6(ShapeBase):
    """Notes next to the tracker: made-up shapes of what a long-lived project keeps in its ticket folders."""
    REMEDY = "list it in tickets.notesFiles"

    def test_list_and_prose_lines_in_a_tracker_do_not_block(self):
        self.proj(["| AB-1 | a | Done |", "| AB-2 | b | Planned |", "", "## Notes", "",
                   "1. **AB-1**: closed early", "2. **AB-2**: waits for design", "3. **AB-90**: follow-up idea",
                   "AB-3 | no leading pipe | Planned |"], {"AB-1": "done", "AB-2": "planned"})
        out = self.blocked("tracker.md:13: row of AB-3 on a line that is not a table row: not read [BLOCKS]")
        unused, cnt = section(out, "Not used"), section(out, "Could not tell")
        for n in (10, 11):
            self.assertIn(f"docs/tickets/tracker.md:{n}: AB-{n - 9} mentioned in prose; the ticket comes from its row", unused)
            self.assertNotIn(f"tracker.md:{n}: row of", out)
        self.assertNotIn("tracker.md:12: row of", out)
        self.assertIn("docs/tickets/tracker.md:12: names AB-90 on a line that is not a table row", cnt)
        self.assertEqual(unused.count("[BLOCKS]"), 1, unused)

    def test_prose_only_tracker_notes_let_the_check_pass(self):
        self.proj(["| AB-1 | a | Done |", "", "1. **AB-1**: closed", "2. **AB-90**: idea"], {"AB-1": "done"})
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0, (out, err))

    NOTES = ("# Hand-off notes\n\n| 1 | first thing | done |\n| 2 | second | open |\n\nID: AB-X-01\n"
             + "".join(f"line {i}\n" for i in range(300)))
    NOTES_TABLE = "# Hand-off notes\n\n| ID | What | State |\n|---|---|---|\n| 1 | first | done |\n| 2 | second | open |\n\nID: AB-X-01\n"

    def test_notes_file_with_numbered_table_blocks_until_listed_in_notesfiles(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/handoff.md", self.NOTES_TABLE)
        out = self.blocked("docs/tickets/handoff.md: another tracker-like table: 2 row(s) not read")
        self.assertIn(self.REMEDY, [l for l in out.splitlines() if "handoff.md: another tracker-like" in l][0])
        self.assertIn("handoff.md: looks like ticket", out)
        for l in out.splitlines():
            if l.startswith("  docs/tickets/handoff.md") and "[BLOCKS]" in l:
                self.assertIn(self.REMEDY, l)
        self.write(".claude/harness.json", json.dumps({"tickets": {"notesFiles": ["docs/tickets/handoff.md"]}}))
        code, out, err = self.bin("init", "--check")
        self.assertEqual(code, 0, (out, err))
        self.assertIn("docs/tickets/handoff.md: listed in notesFiles", section(out, "Not used"))
        self.assertNotIn("handoff.md: another", out)

    def test_followups_file_with_foreign_ids_is_not_used_when_listed_and_a_second_tracker_still_blocks(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/followups.md", "# Follow-ups\n\n| ID | Item |\n|---|---|\n| FU-1 | one |\n| FU-2 | two |\n")
        self.write("docs/tickets/tracker-two.md", "| ID | Title | State |\n|---|---|---|\n| ZZ-1 | n | Planned |\n")
        out = self.blocked("docs/tickets/followups.md: has a table of ticket IDs but no ticket ID the board reads",
                           "docs/tickets/tracker-two.md: another tracker-like table")
        self.write(".claude/harness.json", json.dumps({"tickets": {"notesFiles": ["docs/tickets/followups.md"]}}))
        out = self.blocked("docs/tickets/followups.md: listed in notesFiles",
                           "docs/tickets/tracker-two.md: another tracker-like table")
        self.assertNotIn("followups.md: another", out)
        self.assertNotIn("followups.md: has a table", out)

    def test_notesfiles_changes_the_hash_and_must_be_a_list(self):
        self.proj(["| AB-1 | a | Done |"], {"AB-1": "done"})
        self.write("docs/tickets/n.md", "# n\n\nplain\n")
        h1 = re.search(r"^list hash: (\w+)$", self.bin("init", "--check")[1], re.M).group(1)
        self.write(".claude/harness.json", json.dumps({"tickets": {"notesFiles": ["docs/tickets/n.md"]}}))
        h2 = re.search(r"^list hash: (\w+)$", self.bin("init", "--check")[1], re.M).group(1)
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
