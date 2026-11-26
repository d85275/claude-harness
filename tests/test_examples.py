"""Tests for the sample project and the committed board example (CH-24)."""
import json
import shutil
import os
import re
import subprocess
import tempfile
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from test_board import ROOT, run  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-project")
BOARD = os.path.join(ROOT, "docs", "examples", "board.html")
BUILT = re.compile(r"built at [^<]*")


def normalise(html):
    return BUILT.sub("built at X", html)


SCRIPT = os.path.join(ROOT, "scripts", "make-examples.sh")


def build_from_copy(tmp):
    """Build the sample board from a copy outside any repository; return the HTML."""
    copy = os.path.join(tmp, "sample-project")
    shutil.copytree(SAMPLE, copy)
    out = os.path.join(tmp, "board.html")
    code, _, err = run("--root", copy, "--out", out)
    assert code == 0, err
    with open(out, encoding="utf-8") as f:
        return f.read()


def column_of(page, tid):
    for m in re.finditer(r'<section class="col[^"]*"[^>]*aria-label="([^"]+)">(.*?)</section>', page, re.S):
        if f'data-id="{tid}"' in m.group(2):
            return m.group(1)


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=cwd, env=env, check=True, capture_output=True)


def fake_checkout(tmp, as_git_repo=False):
    """A scratch copy of the layout make-examples.sh needs. Returns its folder."""
    fake = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(fake, "scripts"))
    os.makedirs(os.path.join(fake, "bin"))
    for rel in ("scripts/make-examples.sh", "bin/dev-harness-board"):
        shutil.copy(os.path.join(ROOT, rel), os.path.join(fake, rel))
    shutil.copytree(SAMPLE, os.path.join(fake, "examples", "sample-project"))
    os.symlink(os.path.join(ROOT, "board"), os.path.join(fake, "board"))
    os.symlink(os.path.join(ROOT, "hooks"), os.path.join(fake, "hooks"))
    if as_git_repo:
        git(fake, "init", "-q", "-b", "main")
        git(fake, "add", "-A")
        git(fake, "commit", "-q", "-m", "x")
    return fake


def run_script(fake, tmpdir, chrome):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "CHROME"}
    env["CHROME"] = chrome
    env["TMPDIR"] = tmpdir
    return subprocess.run(["sh", os.path.join(fake, "scripts", "make-examples.sh")],
                          capture_output=True, text=True, env=env)


class SampleProjectTest(unittest.TestCase):
    def test_check_reports_exactly_the_planted_mismatch(self):
        code, out, _ = run("--check", "--root", SAMPLE)
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines(), [
            "FIX-5: tracker says Planned but docs/tickets/FIX-5.md says In progress",
        ])

    def test_committed_board_equals_a_fresh_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = build_from_copy(tmp)
        with open(BOARD, encoding="utf-8") as g:
            self.assertEqual(normalise(fresh), normalise(g.read()),
                             "docs/examples/board.html is stale; run scripts/make-examples.sh")

    def test_titles_of_tickets_without_files_come_from_the_task_column(self):
        with open(BOARD, encoding="utf-8") as f:
            page = f.read()
        cards = {m.group(1): m.group(0) for m in re.finditer(
            r'<button type="button" class="card" data-id="([^"]+)".*?</button>', page, re.S)}
        self.assertIn("Set up the build server", cards["OPS-0"])
        self.assertIn("Fix the launch crash on older phones", cards["FIX-0"])
        self.assertEqual(column_of(page, "OPS-0"), "Done")
        code, out, _ = run("--json", "--root", SAMPLE)
        by = {t["id"]: t for t in json.loads(out)["tickets"]}
        for tid in ("OPS-0", "FIX-0"):
            self.assertIsNone(by[tid]["file"])
        self.assertEqual(by["OPS-0"]["title"], "Set up the build server")

    def test_sample_covers_every_column_and_feature(self):
        code, out, _ = run("--json", "--root", SAMPLE)
        tickets = json.loads(out)["tickets"]
        states = {t["state"] for t in tickets}
        self.assertEqual(states, {"Planned", "In progress", "In review", "Fixing", "Owner check",
                                  "Done", "Blocked", "Deferred"})
        self.assertTrue(any(t["archived"] for t in tickets))
        self.assertTrue(any(t["review"] == "high-risk" for t in tickets))
        self.assertTrue(any(t["id"] == "APP-3" for t in tickets) and any(t["id"] == "SITE-3" for t in tickets))

    def test_committed_board_has_no_worktree_state(self):
        with open(BOARD, encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("feature/", page)
        self.assertIn("unknown commit", page)
        self.assertEqual(column_of(page, "FEAT-1"), "Planned")

    def test_sample_ids_are_made_up(self):
        for d, _, names in os.walk(SAMPLE):
            for n in names:
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    self.assertNotRegex(f.read(), r"\bCH-\d", os.path.join(d, n))

    def test_script_is_valid_posix_sh(self):
        self.assertEqual(subprocess.run(["sh", "-n", SCRIPT]).returncode, 0)
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_script_without_chrome_writes_the_board_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = fake_checkout(tmp)
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            p = run_script(fake, work, os.path.join(tmp, "missing"))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Chrome not found", p.stderr)
            self.assertTrue(os.path.exists(os.path.join(fake, "docs", "examples", "board.html")))
            self.assertFalse(os.path.exists(os.path.join(fake, "docs", "images", "board-light.png")))
            self.assertEqual(os.listdir(work), [])      # the temporary copy is removed

    def test_script_reports_a_failing_chrome(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = fake_checkout(tmp)
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            p = run_script(fake, work, "/usr/bin/false")
            if not os.access("/usr/bin/false", os.X_OK):
                self.skipTest("no /usr/bin/false")
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertIn("error: Chrome failed for", p.stderr)

    def test_script_builds_outside_a_parent_repository_worktree(self):
        """A parent worktree on feature/feat-1 must not move the sample's FEAT-1."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = fake_checkout(tmp, as_git_repo=True)
            wt = os.path.join(tmp, "wt")
            git(fake, "worktree", "add", "-q", "-b", "feature/feat-1", wt)
            # Control: built in place, the parent repository's worktree does leak.
            leaky = os.path.join(tmp, "leaky.html")
            code, _, err = run("--root", os.path.join(fake, "examples", "sample-project"), "--out", leaky)
            self.assertEqual(code, 0, err)
            with open(leaky, encoding="utf-8") as f:
                self.assertEqual(column_of(f.read(), "FEAT-1"), "In progress")
            # The script builds from a copy outside any repository.
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            p = run_script(fake, work, os.path.join(tmp, "missing"))
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(os.path.join(fake, "docs", "examples", "board.html"), encoding="utf-8") as f:
                self.assertEqual(column_of(f.read(), "FEAT-1"), "Planned")

    def test_script_refuses_a_temp_folder_inside_a_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = fake_checkout(tmp, as_git_repo=True)
            p = run_script(fake, fake, os.path.join(tmp, "missing"))
            self.assertEqual(p.returncode, 1)
            self.assertIn("inside a git repository", p.stderr)


if __name__ == "__main__":
    unittest.main()
