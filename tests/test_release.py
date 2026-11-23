"""The plugin version agrees across plugin.json, marketplace.json and CHANGELOG.md (CH-27)."""
import json
import os
import re
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.M)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def changelog_versions():
    return HEADING.findall(read("CHANGELOG.md"))


class VersionTest(unittest.TestCase):
    def test_versions_agree(self):
        plugin = json.loads(read(".claude-plugin/plugin.json"))
        market = json.loads(read(".claude-plugin/marketplace.json"))
        entries = [p for p in market["plugins"] if p["name"] == plugin["name"]]
        self.assertEqual(len(entries), 1)
        versions = changelog_versions()
        self.assertTrue(versions, "CHANGELOG.md has no '## [x.y.z]' heading")
        self.assertEqual(plugin["version"], versions[0])
        self.assertEqual(entries[0].get("version"), versions[0])

    def test_changelog_is_newest_first_without_repeats(self):
        versions = [tuple(map(int, v.split("."))) for v in changelog_versions()]
        self.assertEqual(versions, sorted(set(versions), reverse=True))


if __name__ == "__main__":
    unittest.main()
