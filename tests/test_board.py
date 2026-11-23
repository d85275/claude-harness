"""Tests for board/ and bin/dev-harness-board (--check, --json) and the new tickets settings."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "bin", "dev-harness-board")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)
from board import cli, parse  # noqa: E402


def run(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    p = subprocess.run([BIN, *args], capture_output=True, text=True, cwd=cwd, env=env)
    return p.returncode, p.stdout, p.stderr


class TempProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        os.makedirs(os.path.join(self.root, ".git"))

    def write(self, rel, text, newline="\n"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", newline))

    def config(self, **tickets):
        self.write(".claude/harness.json", json.dumps({"tickets": tickets}))

    def ticket(self, tid, status, folder="docs/tickets", title="Thing", newline="\n"):
        self.write(f"{folder}/{tid}.md", f"# {tid}: {title}\n\n## Status\n\n{status}\n\n## Scope\n\nx\n", newline)

    def tracker(self, *rows, header="| ID | Title | State |\n|---|---|---|\n", path="docs/tickets/tracker.md",
                newline="\n"):
        self.write(path, "# T\n\n" + header + "".join(r + "\n" for r in rows), newline)

    def check(self):
        return run("--check", "--root", self.root)


class FixtureTests(unittest.TestCase):
    def test_clean_project_exit_0(self):
        code, out, err = run("--check", "--root", os.path.join(FIXTURES, "board_clean"))
        self.assertEqual((code, out, err), (0, "", ""))

    def test_every_problem_kind_found(self):
        code, out, _ = run("--check", "--root", os.path.join(FIXTURES, "board_problems"))
        self.assertEqual(code, 1)
        for expected in ("XY-01: tracker says Done but docs/tickets/XY-01.md says In progress",
                         "XY-02: tracker row has no ticket file",
                         "XY-03: ticket file docs/tickets/XY-03.md has no tracker row",
                         "XY-04: tracker row state 'Pondering' is not a known state",
                         "XY-05: appears in 2 tracker rows",
                         "XY-06: link 'missing/XY-06.md' in docs/tickets/tracker.md points to a missing file"):
            self.assertIn(expected, out.splitlines())

    def test_json_model(self):
        code, out, _ = run("--json", "--root", os.path.join(FIXTURES, "board_clean"))
        self.assertEqual(code, 0)
        model = json.loads(out)
        self.assertEqual([t["id"] for t in model["tickets"]], ["AB-01", "AB-02", "AB-03"])
        self.assertEqual(model["tickets"][1]["state"], "In progress")
        self.assertEqual(model["problems"], [])

    def test_json_with_check_exit_code(self):
        code, out, _ = run("--json", "--check", "--root", os.path.join(FIXTURES, "board_problems"))
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(out)["problems"])

    def test_this_repo_is_clean(self):
        self.assertEqual(run("--check", "--root", ROOT)[0], 0)


class UsageTests(TempProject):
    def test_no_mode_is_usage_error(self):
        self.assertEqual(run("--root", self.root)[0], 2)

    def test_unknown_flag(self):
        self.assertEqual(run("--bogus")[0], 2)

    def test_root_not_a_directory(self):
        self.assertEqual(run("--check", "--root", os.path.join(self.root, "nope"))[0], 2)

    def test_unreadable_config_exit_2(self):
        self.write(".claude/harness.json", "{ not json")
        code, _, err = self.check()
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_default_root_is_git_top_level(self):
        self.tracker("| AB-01 | T | Planned |")
        self.ticket("AB-01", "planned")
        os.makedirs(os.path.join(self.root, "sub", "deep"))
        code, out, _ = run("--check", cwd=os.path.join(self.root, "sub", "deep"))
        self.assertEqual((code, out), (0, ""))

    def test_writes_nothing(self):
        self.tracker("| AB-01 | T | Planned |")
        self.ticket("AB-01", "planned")
        before = sorted(os.listdir(os.path.join(self.root, "docs", "tickets")))
        self.check()
        run("--json", "--root", self.root)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "docs", "tickets"))), before)
        self.assertEqual(sorted(os.listdir(self.root)), [".git", "docs"])


class ParsingTests(TempProject):
    def test_aliases(self):
        self.config(stateAliases={"Waiting": "blocked", "parked": "Deferred"})
        self.tracker("| AB-01 | T | Waiting |", "| AB-02 | T | Parked |")
        self.ticket("AB-01", "waiting on the vendor")
        self.ticket("AB-02", "parked (2026-01-01)")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_alias_does_not_hide_mismatch(self):
        self.config(stateAliases={"Waiting": "Blocked"})
        self.tracker("| AB-01 | T | Waiting |")
        self.ticket("AB-01", "done")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("tracker says Blocked but", out)

    def test_multiword_state_and_case(self):
        self.tracker("| AB-01 | T | IN REVIEW |", "| AB-02 | T | owner check |")
        self.ticket("AB-01", "In review (round 1)")
        self.ticket("AB-02", "Owner check")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_state_prefix_needs_word_boundary(self):
        self.tracker("| AB-01 | T | Doneish |")
        self.ticket("AB-01", "done")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("'Doneish' is not a known state", out)

    def test_extra_columns_kept_in_order(self):
        self.tracker("| [AB-01](AB-01.md) | T | Planned | 3 | alice |",
                     header="| ID | Title | State | Points | Owner |\n|---|---|---|---|---|\n")
        self.ticket("AB-01", "planned")
        code, out, _ = run("--json", "--root", self.root)
        t = json.loads(out)["tickets"][0]
        self.assertEqual(list(t["extra"].items()), [("Points", "3"), ("Owner", "alice")])

    def test_linked_id_gives_plain_id(self):
        self.tracker("| [AB-01](AB-01.md) | T | Planned |")
        self.ticket("AB-01", "planned")
        t = json.loads(run("--json", "--root", self.root)[1])["tickets"][0]
        self.assertEqual((t["id"], t["link"]), ("AB-01", "AB-01.md"))

    def test_crlf_files(self):
        self.tracker("| [AB-01](AB-01.md) | T | Planned |", newline="\r\n")
        self.ticket("AB-01", "planned", newline="\r\n")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_column_order_and_other_table_before_tracker(self):
        self.write("docs/tickets/tracker.md",
                   "| A | B |\n|---|---|\n| 1 | 2 |\n\n| State | ID |\n|---|---|\n| Planned | AB-01 |\n")
        self.ticket("AB-01", "planned")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_archive(self):
        self.config(archive=True, archiveTracker="docs/archive/tracker.md", archiveDirs=["docs/archive"])
        self.tracker("| AB-02 | T | Planned |")
        self.ticket("AB-02", "planned")
        self.tracker("| AB-01 | Old | Done |", path="docs/archive/tracker.md")
        self.ticket("AB-01", "done", folder="docs/archive")
        self.assertEqual(self.check()[:2], (0, ""))
        t = json.loads(run("--json", "--root", self.root)[1])["tickets"]
        self.assertEqual([(x["id"], x["archived"]) for x in t], [("AB-01", True), ("AB-02", False)])

    def test_archive_ignored_when_off(self):
        self.config(archive=False, archiveTracker="docs/archive/tracker.md", archiveDirs=["docs/archive"])
        self.tracker("| AB-02 | T | Planned |")
        self.ticket("AB-02", "planned")
        self.ticket("AB-01", "done", folder="docs/archive")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_feature_globs(self):
        self.config(featureGlobs=["src/features/*"])
        self.tracker("| AB-01 | T | Planned |")
        self.ticket("AB-01", "planned", folder="src/features/login")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_id_prefixes_limit_ticket_files_and_give_platform(self):
        self.config(idPrefixes={"WEB": {"platform": "web", "kind": "feature"}})
        self.tracker("| WEB-1 | T | Planned |")
        self.ticket("WEB-1", "planned")
        self.ticket("ZZ-9", "planned")  # not a configured prefix: not a ticket file
        code, out, _ = run("--json", "--root", self.root)
        self.assertEqual(code, 0)
        t = json.loads(out)["tickets"]
        self.assertEqual([(x["id"], x["platform"], x["kind"]) for x in t], [("WEB-1", "web", "feature")])

    def test_twin_one_side_only(self):
        self.config(idPrefixes={"IOS": {"platform": "ios", "kind": "feature"},
                                "AND": {"platform": "android", "kind": "feature"}},
                    twinPlatforms=["ios", "android"])
        self.tracker("| IOS-1 | T | Planned |", "| IOS-2 | T | Planned |", "| AND-2 | T | Planned |")
        for t in ("IOS-1", "IOS-2", "AND-2"):
            self.ticket(t, "planned")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), ["IOS-1: twin ticket missing: no android ticket for feature number 1"])

    def test_twins_off_by_default(self):
        self.config(idPrefixes={"IOS": {"platform": "ios", "kind": "feature"}})
        self.tracker("| IOS-1 | T | Planned |")
        self.ticket("IOS-1", "planned")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_duplicate_file_ids(self):
        self.config(featureGlobs=["src/features/*"])
        self.tracker("| AB-01 | T | Planned |")
        self.ticket("AB-01", "planned")
        self.ticket("AB-01", "planned", folder="src/features/x")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("AB-01: has several files", out)

    def test_external_link_not_checked(self):
        self.tracker("| [AB-01](https://example.com/AB-01) | T | Planned |")
        self.ticket("AB-01", "planned")
        self.assertEqual(self.check()[:2], (0, ""))

    def test_link_anchor_resolves(self):
        self.tracker("| [AB-01](AB-01.md#status) | T | Planned |")
        self.ticket("AB-01", "planned")
        self.assertEqual(self.check()[:2], (0, ""))


class RobustnessTests(TempProject):
    def assert_problem_no_traceback(self, needle):
        code, out, err = self.check()
        self.assertEqual(code, 1, (out, err))
        self.assertIn(needle, out)
        self.assertNotIn("Traceback", out + err)

    def test_missing_tracker_one_line(self):
        code, out, _ = self.check()
        self.assertEqual((code, out), (1, "docs/tickets/tracker.md: tracker not found\n"))

    def test_tracker_without_table(self):
        self.write("docs/tickets/tracker.md", "just words\n")
        self.assert_problem_no_traceback("no table with ID and State columns")

    def test_table_without_separator(self):
        self.write("docs/tickets/tracker.md", "| ID | State |\n| AB-01 | Planned |\n")
        self.assert_problem_no_traceback("no separator row")

    def test_short_and_long_rows(self):
        self.tracker("| AB-01 | T |", "| AB-02 | T | Planned | extra |", "| AB-03 | T | Planned |")
        self.ticket("AB-03", "planned")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("docs/tickets/tracker.md:5: row has 2 cells, header has 3", out)
        self.assertIn("docs/tickets/tracker.md:6: row has 4 cells, header has 3", out)
        self.assertNotIn("AB-03", out)

    def test_empty_id_cell(self):
        self.tracker("|  | T | Planned |")
        self.assert_problem_no_traceback("empty ID")

    def test_bad_utf8_tracker(self):
        os.makedirs(os.path.join(self.root, "docs", "tickets"))
        with open(os.path.join(self.root, "docs/tickets/tracker.md"), "wb") as f:
            f.write(b"| ID | State |\n|---|---|\n| AB-01 | \xff\xfe |\n")
        self.assert_problem_no_traceback("cannot read tracker")

    def test_tracker_is_a_directory(self):
        os.makedirs(os.path.join(self.root, "docs", "tickets", "tracker.md"))
        self.assert_problem_no_traceback("cannot read tracker")

    def test_malformed_ticket_files(self):
        self.tracker("| AB-01 | T | Planned |", "| AB-02 | T | Planned |", "| AB-03 | T | Planned |")
        self.write("docs/tickets/AB-01.md", "no heading\n\n## Status\n\nplanned\n")
        self.write("docs/tickets/AB-02.md", "# AB-02: x\n\nno status section\n")
        self.write("docs/tickets/AB-03.md", "# AB-09: wrong id\n\n## Status\n\nplanned\n")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("AB-01: docs/tickets/AB-01.md: first line is not", out)
        self.assertIn("AB-02: docs/tickets/AB-02.md: no '## Status' section", out)
        self.assertIn("AB-03: docs/tickets/AB-03.md: heading names AB-09", out)
        self.assertNotIn("Traceback", out)

    def test_empty_files(self):
        self.write("docs/tickets/tracker.md", "")
        self.write("docs/tickets/AB-01.md", "")
        code, out, err = self.check()
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out + err)

    def test_bad_ticket_settings_warn_and_default(self):
        self.write(".claude/harness.json", json.dumps({"tickets": {
            "stateAliases": {"x": "Nonsense"}, "archiveDirs": "docs", "extraColumns": 3}}))
        self.tracker("| AB-01 | T | Planned |")
        self.ticket("AB-01", "planned")
        code, out, err = self.check()
        self.assertEqual((code, out), (0, ""))
        self.assertIn("stateAliases", err)

    def test_other_project_dir_env_is_ignored(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        os.makedirs(os.path.join(other.name, ".claude"))
        with open(os.path.join(other.name, ".claude", "harness.json"), "w") as f:
            f.write("{ broken")
        self.tracker("| AB-01 | T | Planned |")
        self.ticket("AB-01", "planned")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=other.name)
        p = subprocess.run([BIN, "--check", "--root", self.root], capture_output=True, text=True, env=env)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))


class StateMatchTests(unittest.TestCase):
    def test_resolve(self):
        names = parse.state_names({"Waiting": "Blocked"})
        for text, want in (("done", "Done"), ("**Done**", "Done"), ("In progress (x)", "In progress"),
                           ("waiting for x", "Blocked"), ("Doneish", None), ("", None), ("Plan", None)):
            self.assertEqual(parse.resolve_state(text, names), want, text)


if __name__ == "__main__":
    unittest.main()
