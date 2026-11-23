"""README and reference pages: every config key is documented and every link resolves (CH-25)."""
import os
import re
import sys
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "hooks", "scripts"))
import harness_config  # noqa: E402

PAGES = ["README.md", "docs/guards.md", "docs/configuration.md", "docs/agents.md"]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def outside_fences(text):
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def slug(heading):
    h = heading.strip().lower().replace("`", "")
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(rel):
    return {slug(m.group(1)) for m in re.finditer(r"^#+\s+(.*)$", outside_fences(read(rel)), re.M)}


class ConfigurationPageTest(unittest.TestCase):
    def test_every_default_key_is_documented(self):
        text = read("docs/configuration.md")
        missing = []
        for key, value in harness_config.DEFAULTS.items():
            if f"`{key}`" not in text:
                missing.append(key)
            if key == "tickets":
                missing += [f"tickets.{k}" for k in value if f"`tickets.{k}`" not in text]
        self.assertEqual(missing, [])


class LinkTest(unittest.TestCase):
    def test_relative_links_and_images_resolve(self):
        bad = []
        for page in PAGES:
            base = os.path.dirname(os.path.join(ROOT, page))
            for target in LINK.findall(outside_fences(read(page))):
                if re.match(r"[a-z][a-z0-9+.-]*:", target):
                    continue
                path, _, frag = target.partition("#")
                dest = os.path.normpath(os.path.join(base, path)) if path else os.path.join(ROOT, page)
                if not os.path.exists(dest):
                    bad.append(f"{page}: {target}")
                elif frag and dest.endswith(".md") and frag not in anchors(os.path.relpath(dest, ROOT)):
                    bad.append(f"{page}: {target} (no such heading)")
        self.assertEqual(bad, [])

    def test_draft_is_gone_and_pages_exist(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "docs", "README-draft.md")))
        for page in PAGES:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, page)), page)

    def test_readme_has_no_ticket_ids(self):
        self.assertIsNone(re.search(r"\bCH-\d+", read("README.md")))


if __name__ == "__main__":
    unittest.main()
