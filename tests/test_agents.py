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


if __name__ == "__main__":
    unittest.main()
