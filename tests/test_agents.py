"""Pins the model tiers in agents/*.md frontmatter."""
import glob
import os
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
ALIASES = {"sonnet", "opus", "fable"}
TIERS = {
    "implementer": ("sonnet", "medium"),
    "implementer-advanced": ("opus", "high"),
    "reviewer": ("sonnet", "high"),
    "silent-failure-hunter": ("sonnet", "high"),
    "high-risk-reviewer": ("opus", "high"),
}


def frontmatter(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    assert lines[0] == "---", path
    end = lines.index("---", 1)
    out = {}
    for line in lines[1:end]:
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


class AgentTiersTest(unittest.TestCase):
    def setUp(self):
        self.agents = {}
        for p in glob.glob(os.path.join(ROOT, "agents", "*.md")):
            fm = frontmatter(p)
            self.agents[fm.get("name")] = (os.path.basename(p), fm)

    def test_exactly_the_known_agents(self):
        self.assertEqual(set(self.agents), set(TIERS))

    def test_name_matches_filename(self):
        for name, (fname, _) in self.agents.items():
            self.assertEqual(fname, name + ".md")

    def test_tier_table(self):
        for name, (model, effort) in TIERS.items():
            fm = self.agents[name][1]
            self.assertEqual((fm.get("model"), fm.get("effort")), (model, effort), name)

    def test_models_are_bare_aliases(self):
        for name, (_, fm) in self.agents.items():
            self.assertIn(fm.get("model"), ALIASES, name)


class RoutingTextTest(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_start_ticket_routes_high_risk(self):
        t = self.read("commands/start-ticket.md")
        self.assertIn("implementer-advanced", t)
        self.assertIn("Review level", t)

    def test_review_passes_model_override(self):
        t = self.read("commands/review.md")
        self.assertIn("highRiskReviewerModel", t)
        self.assertIn("`model` parameter", t)
        self.assertIn("dev-harness-config", t)


class FilesAndShellTextTest(unittest.TestCase):
    """CH-33: every agent and command says how to edit files and run commands, in one short
    paragraph, and the coordinator passes the rules into implementer and reviewer briefs."""

    MARK = "**Files and shell.**"
    WRITERS = {"implementer", "implementer-advanced"}  # the agents with the Write and Edit tools
    # Review round 1: the shell returns to the main checkout after every call, so a test runner
    # or build run without its folder option checks main's code, not the worktree's.
    FOLDER_OPTION = ("A command that reads the current folder (the Harness Verify commands, test "
                     "runners, builds) takes the tool's folder option "
                     "(`python3 -m unittest discover -s <abs>/tests`, `make -C <abs>`, "
                     "`npm --prefix <abs>`, `go -C <abs>`, `./gradlew -p <abs>`, `git -C <abs>`); "
                     "if it has none, stop and report rather than run it from the wrong tree.")
    LAUNCHERS = ("commands/start-ticket.md", "commands/review.md")

    def paragraph(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count(self.MARK), 1, rel)
        para = text[text.index(self.MARK):].split("\n")[0]
        self.assertLess(len(para), 1600, f"{rel}: keep the paragraph short")
        return text, para

    def files(self, folder):
        found = sorted(glob.glob(os.path.join(ROOT, folder, "*.md")))
        self.assertTrue(found, folder)
        return [os.path.relpath(p, ROOT) for p in found]

    def test_every_agent_carries_the_rules(self):
        for rel in self.files("agents"):
            with self.subTest(agent=rel):
                _, para = self.paragraph(rel)
                for phrase in ("one plain command per Bash call", "`cd X && ...`",
                               "absolute path", "`git -C <absolute path>`", "which rule it hit",
                               "never rewrite it to get around the check", "wildcards or variables",
                               self.FOLDER_OPTION):
                    self.assertIn(phrase, para)
                name = os.path.basename(rel)[:-3]
                if name in self.WRITERS:
                    self.assertIn("Edit and Write tools only", para)
                    self.assertIn("`--root <absolute worktree path>`", para)
                    self.assertIn("scratchpad", para)
                else:
                    tools = frontmatter(os.path.join(ROOT, rel)).get("tools", "")
                    self.assertNotIn("Write", tools)  # read-only: the text must not need it
                    self.assertIn("no Write tool", para)
                    # Owner decision 2026-11-26: own output into the scratchpad is allowed (to
                    # run new tests on the base tree); hand-written files are not.
                    for phrase in ("write their own output into the scratchpad",
                                   "`git archive -o <scratch>/x.tar <sha>`",
                                   "`tar -x -f <scratch>/x.tar -C <scratch>/base`",
                                   "`cp` into the scratchpad", "no hand-written files",
                                   "`printf >> file`", "`cat > file <<EOF`"):
                        self.assertIn(phrase, para)
                    self.assertNotIn("other writes through the shell", para)

    def test_every_command_carries_the_rules(self):
        for rel in self.files("commands"):
            with self.subTest(command=rel):
                _, para = self.paragraph(rel)
                for phrase in ("one plain command per Bash call", "`cd X && ...`",
                               "absolute paths", "`git -C <absolute path>`",
                               "which rule it hit", "rewrite it to get around the check"):
                    self.assertIn(phrase, para)
                if not rel.endswith("sync.md"):  # read-only: writes no files
                    self.assertIn("Edit and Write tools only", para)
                if rel in self.LAUNCHERS:
                    self.assertIn(self.FOLDER_OPTION, para)

    def test_briefs_carry_the_rules(self):
        text, para = self.paragraph("commands/start-ticket.md")
        self.assertIn("Pass these rules into every implementer brief", para)
        self.assertIn("the working rules from **Files and shell** above", text)
        text, para = self.paragraph("commands/review.md")
        self.assertIn("Pass these rules into every reviewer brief", para)
        self.assertEqual(text.count("the working rules from **Files and shell** above"), 2)
        brief = text[text.index("give them the working rules"):].split("\n")[0]
        for phrase in ("the tool's folder option", "write their own output into the scratchpad",
                       "`git archive -o <scratch>/x.tar <sha>`", "no hand-written files"):
            self.assertIn(phrase, brief)
        text, _ = self.paragraph("commands/start-ticket.md")
        brief = text[text.index("   - the working rules from"):].split("\n")[0]
        self.assertIn("the tool's folder option", brief)

    def test_no_relative_root(self):
        for rel in self.files("agents") + self.files("commands"):
            with self.subTest(file=rel):
                with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                    text = f.read()
                self.assertNotIn("--root .", text.replace(self.paragraph(rel)[1], ""))
                self.assertNotIn("--root <your worktree>", text)


if __name__ == "__main__":
    unittest.main()
