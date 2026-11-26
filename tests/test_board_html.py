"""Tests for the board HTML (board/render.py, board/markdown.py, the render mode of the CLI)."""
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__))))
from test_board import ROOT, TempProject, run  # noqa: E402

from board import markdown, render  # noqa: E402

PREFIXES = {"IOS-": {"platform": "ios", "kind": "feature"}, "AND-": {"platform": "android", "kind": "feature"},
            "BUG-": {"platform": "", "kind": "bug"}}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class BoardProject(TempProject):
    def setUp(self):
        super().setUp()
        self.config(idPrefixes=PREFIXES, twinPlatforms=["ios", "android"], extraColumns=["Owner"])
        self.write("docs/tickets/tracker.md",
                   "# T\n\n| ID | Title | Review | State | Commit | Owner |\n|---|---|---|---|---|---|\n"
                   "| [IOS-1](IOS-1.md) | Login screen | high-risk | In progress | abc1234 | Sam |\n"
                   "| [AND-1](AND-1.md) | Login screen | standard | Fixing | | |\n"
                   "| [BUG-2](BUG-2.md) | Crash on <b>start</b> | self-review | Done | f00d | |\n"
                   "| [BUG-3](BUG-3.md) | Blocked one | standard | Deferred | | |\n"
                   "| [BUG-4](BUG-4.md) | Waiting | standard | Owner check | | |\n"
                   "| [BUG-5](BUG-5.md) | Later | standard | Planned | | |\n"
                   "| [BUG-6](BUG-6.md) | Stuck | standard | Blocked | | |\n"
                   "| [BUG-7](BUG-7.md) | Reviewing | standard | In review | | |\n")
        self.ticket("IOS-1", "In progress", title="Login screen")
        self.ticket("AND-1", "Done", title="Login screen")           # mismatch with the row
        for n, st in ((2, "Done"), (3, "Deferred"), (4, "Owner check"), (5, "Planned"), (6, "Blocked"),
                      (7, "In review")):
            self.ticket(f"BUG-{n}", st, title="t")
        self.write("docs/tickets/BUG-2.md",
                   "# BUG-2: Crash on <b>start</b>\n\n## Status\n\nDone\n\n## Labels\n\n- crash, `p1`\n\n## Notes\n\n"
                   "<script>alert(1)</script> \"quoted\" [bad](javascript:alert(1)) [ok](https://example.com/a?b=1&c=2)\n\n"
                   "| a | b |\n|---|---|\n| <img src=x onerror=1> | **bold** |\n")

    def build(self, **kw):
        from board import cli
        cfg, _ = cli.load_tickets_config(self.root)
        model = cli.build_model(self.root, cfg)
        return render.build_html(model, self.root, cfg, kw.get("built_at", "T0"), kw.get("commit", "abc1234"))

    def cards(self, page):
        return {m.group(1): m.group(0) for m in re.finditer(r'<button type="button" class="card" data-id="([^"]+)".*?</button>', page, re.S)}

    def column_of(self, page, tid):
        for m in re.finditer(r'<section class="col[^"]*"[^>]*aria-label="([^"]+)">(.*?)</section>', page, re.S):
            if f'data-id="{tid}"' in m.group(2):
                return m.group(1)


class ColumnTests(BoardProject):
    def test_every_state_in_its_column(self):
        page = self.build()
        self.assertEqual({t: self.column_of(page, t) for t in ("IOS-1", "AND-1", "BUG-2", "BUG-3", "BUG-4", "BUG-5", "BUG-6", "BUG-7")},
                         {"IOS-1": "In progress", "AND-1": "In review", "BUG-2": "Done", "BUG-3": "Blocked",
                          "BUG-4": "Owner check", "BUG-5": "Planned", "BUG-6": "Blocked", "BUG-7": "In review"})

    def test_six_columns_in_order(self):
        names = re.findall(r'<section class="col[^"]*"[^>]*aria-label="([^"]+)"', self.build())
        self.assertEqual(names, ["Planned", "In progress", "In review", "Owner check", "Blocked", "Done"])


class DoneCollapseTests(BoardProject):
    def sections(self, page):
        return {m.group(1): m.group(0) for m in re.finditer(
            r'<section class="col[^"]*"[^>]*aria-label="([^"]+)">.*?</section>', page, re.S)}

    def test_done_starts_collapsed_with_button(self):
        sec = self.sections(self.build())["Done"]
        self.assertIn('class="col done collapsed"', sec)
        self.assertRegex(sec, r'<button type="button" class="fold" aria-expanded="false" data-fold>')
        self.assertIn('class="count">1<', sec)

    def test_other_columns_have_no_fold(self):
        for name, sec in self.sections(self.build()).items():
            if name != "Done":
                self.assertNotIn("data-fold", sec)
                self.assertNotIn("collapsed", sec)

    def test_done_cards_still_in_page(self):
        page = self.build()
        self.assertEqual(self.column_of(page, "BUG-2"), "Done")
        self.assertIn('data-id="BUG-2"', self.sections(page)["Done"])
        self.assertIn('<section class="issue" data-id="BUG-2" hidden>', page)

    def test_script_remembers_choice_safely(self):
        page = self.build()
        self.assertIn("board-done-open:", page)
        self.assertRegex(page, r"try\{localStorage\.setItem\(foldKey")
        self.assertRegex(page, r"try\{openDone=localStorage\.getItem\(foldKey")


class CardTests(BoardProject):
    def test_badges_and_extras(self):
        cards = self.cards(self.build())
        self.assertIn('class="tag high-risk">high-risk', cards["IOS-1"])
        self.assertIn('class="tag standard">standard', cards["AND-1"])
        self.assertIn('class="tag self-review">self-review', cards["BUG-2"])
        self.assertIn("Owner: Sam", cards["IOS-1"])
        self.assertIn('data-kind="feature"', cards["IOS-1"])
        self.assertIn('data-platform="android"', cards["AND-1"])

    def test_mismatch_badge_only_where_check_reports(self):
        cards = self.cards(self.build())
        self.assertIn('tag mismatch">mismatch', cards["AND-1"])
        self.assertIn('data-mismatch="1"', cards["AND-1"])
        self.assertNotIn("mismatch</span>", cards["IOS-1"])
        self.assertIn('data-mismatch="0"', cards["IOS-1"])

    def test_icon_from_kind_and_labels_from_file(self):
        cards = self.cards(self.build())
        self.assertIn('title="bug">B</span>', cards["BUG-2"])
        self.assertIn(">crash<", cards["BUG-2"])
        self.assertIn(">p1<", cards["BUG-2"])

    def test_kind_from_file_label_without_prefix(self):
        self.config(extraColumns=[])
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| ZZ-1 | x | Planned |\n")
        self.write("docs/tickets/ZZ-1.md", "# ZZ-1: x\n\n## Status\n\nPlanned\n\n## Labels\n\nchore, small\n")
        self.assertIn('title="chore">C</span>', self.build())


class EscapingTests(BoardProject):
    def test_title_html_escaped(self):
        page = self.build()
        self.assertIn("Crash on &lt;b&gt;start&lt;/b&gt;", page)
        self.assertNotIn("<b>start</b>", page)

    def test_markdown_script_img_and_quotes(self):
        page = self.build()
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&quot;quoted&quot;", page)
        self.assertIn("<td>&lt;img src=x onerror=1&gt;</td>", page)
        self.assertIn("<td><strong>bold</strong></td>", page)

    def test_links_filtered(self):
        page = self.build()
        self.assertNotIn('href="javascript', page)
        self.assertIn("[bad](javascript:alert(1))", page)
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', page)

    def test_quote_cannot_break_out_of_attribute(self):
        html = markdown.inline('[x](https://a.test/"onmouseover="1)')
        self.assertNotIn('"onmouseover', html.replace("&quot;", ""))
        self.assertNotIn(' onmouseover=', html)

    def test_url_rules(self):
        for bad in ("javascript:x", "JavaScript:x", "java\tscript:x", "data:text/html,x", "vbscript:x", "//evil.test", " ", "file:///x"):
            self.assertIsNone(markdown.safe_url(bad), bad)
        for good in ("http://a.test", "https://a.test/x", "docs/x.md", "../x.md", "#BUG-1", "x.md#top"):
            self.assertEqual(markdown.safe_url(good), good)

    def test_extra_and_label_escaped(self):
        self.write("docs/tickets/tracker.md",
                   "| ID | Title | State | Owner |\n|---|---|---|---|\n| BUG-1 | x | Planned | <i>me</i> \"q\" |\n")
        self.write("docs/tickets/BUG-1.md", "# BUG-1: x\n\n## Status\n\nPlanned\n\n## Labels\n\n<u>l</u>\n")
        page = self.build()
        self.assertIn("&lt;i&gt;me&lt;/i&gt; &quot;q&quot;", page)
        self.assertNotIn("<i>me", page)
        self.assertNotIn("<u>l", page)

    def test_no_innerhtml_and_no_network(self):
        page = self.build()
        self.assertNotIn("innerHTML", page)
        self.assertNotRegex(page, r"src=\"https?:")
        for needle in ("<link ", "@import", "fetch(", "XMLHttpRequest", "<script src"):
            self.assertNotIn(needle, page)


class MarkdownTests(unittest.TestCase):
    def test_blocks(self):
        h = markdown.render("# T\n\n## Sub\n\n- a\n- b\n\n1. x\n2. y\n\n```\n<code> & more\n```\n\n> quote\n\ntext *em* **st** `c<d>`\n\n---\n")
        for part in ("<h2>T</h2>", "<h3>Sub</h3>", "<ul><li>a</li><li>b</li></ul>", "<ol><li>x</li><li>y</li></ol>",
                     "<pre><code>&lt;code&gt; &amp; more</code></pre>", "<blockquote><p>quote</p></blockquote>",
                     "<em>em</em>", "<strong>st</strong>", "<code>c&lt;d&gt;</code>", "<hr>"):
            self.assertIn(part, h)

    def test_table_with_escaped_bar(self):
        h = markdown.render("| a | b |\n|---|---|\n| x \\| y | z |\n")
        self.assertIn("<td>x | y</td><td>z</td>", h)


class HardeningTests(BoardProject):
    def test_deep_quote_nesting_does_not_crash(self):
        h = markdown.render(">" * 6000 + " x")
        self.assertLessEqual(h.count("<blockquote>"), markdown.MAX_QUOTE_DEPTH)
        self.assertIn("x", h)

    def test_bad_body_shown_as_text(self):
        self.write("docs/tickets/BUG-5.md", "# BUG-5: x\n\n## Status\n\nPlanned\n\n<b>raw</b>\n")
        with mock.patch.object(render, "markdown", side_effect=RuntimeError("boom")):
            page = self.build()
        self.assertIn("&lt;b&gt;raw&lt;/b&gt;", page)
        self.assertNotIn("<b>raw", page)

    def test_many_open_brackets_is_fast(self):
        import time
        t0 = time.time()
        markdown.inline("[" * 20000)
        self.assertLess(time.time() - t0, 3)

    def test_emphasis_not_applied_inside_href(self):
        h = markdown.inline("[x](http://a.com/_b_) and _c_")
        self.assertIn('href="http://a.com/_b_"', h)
        self.assertIn("<em>c</em>", h)
        h = markdown.inline("[**x**](http://a.com/*b*/*c*)")
        self.assertIn('href="http://a.com/*b*/*c*" rel="noopener noreferrer"><strong>x</strong></a>', h)

    def test_twin_uses_prefix_kind_like_check(self):
        self.write("docs/tickets/AND-1.md", "# AND-1: x\n\n## Status\n\nDone\n\n## Labels\n\nodd\n")
        self.assertIn('<dt>Twin</dt><dd><a href="#AND-1">', self.build())

    def test_unknown_state_in_planned_with_mismatch_badge(self):
        self.write("docs/tickets/tracker.md", "| ID | Title | State |\n|---|---|---|\n| [BUG-1](BUG-1.md) | w | Pondering |\n")
        self.write("docs/tickets/BUG-1.md", "# BUG-1: w\n\n## Status\n\nPondering\n")
        page = self.build()
        self.assertEqual(self.column_of(page, "BUG-1"), "Planned")
        self.assertIn("tag mismatch", self.cards(page)["BUG-1"])

    def test_archived_tag(self):
        self.config(archive=True, archiveDirs=["docs/old"], archiveTracker="docs/old/tracker.md",
                    idPrefixes=PREFIXES)
        self.write("docs/old/tracker.md", "| ID | Title | State |\n|---|---|---|\n| [BUG-9](BUG-9.md) | old | Done |\n")
        self.write("docs/old/BUG-9.md", "# BUG-9: old\n\n## Status\n\nDone\n")
        cards = self.cards(self.build())
        self.assertIn('<span class="tag">archived</span>', cards["BUG-9"])

    def test_write_atomic_keeps_existing_mode_and_default_mode(self):
        path = os.path.join(self.root, "m.html")
        render.write_atomic(path, "a")
        umask = os.umask(0)
        os.umask(umask)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o666 & ~umask)
        os.chmod(path, 0o640)
        render.write_atomic(path, "b")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)

    def test_a11y_attributes_present(self):
        page = self.build()
        self.assertIn('role="dialog" aria-modal="true"', page)
        self.assertIn('id="title-IOS-1"', page)
        self.assertIn("aria-labelledby", page)
        self.assertIn("opener.focus()", page)


class FeatureTests(BoardProject):
    def test_filters_search_and_toggle_present(self):
        page = self.build()
        for needle in ('id="q"', 'id="f-kind"', 'id="f-platform"', 'id="f-review"', 'id="f-mismatch"',
                       'data-search="login screen"'.replace("login screen", "ios-1 login screen")):
            self.assertIn(needle, page)
        self.assertIn('<option value="android">', page)
        self.assertIn('<option value="high-risk">', page)

    def test_issue_view_details_twin_copy_and_deeplink(self):
        page = self.build()
        self.assertIn('<section class="issue" data-id="IOS-1" hidden>', page)
        self.assertIn('data-copy="IOS-1"', page)
        self.assertIn('<dt>Twin</dt><dd><a href="#AND-1">AND-1</a></dd>', page)
        self.assertIn("<dt>Commit</dt><dd>abc1234</dd>", page)
        self.assertIn("<dt>Owner</dt><dd>Sam</dd>", page)
        self.assertIn("location.hash", page)
        self.assertIn("hashchange", page)
        self.assertIn("fromHash();", page)

    def test_built_line_and_theme(self):
        page = self.build(built_at="2030-01-02 03:04 UTC", commit="deadbee")
        self.assertIn("built at 2030-01-02 03:04 UTC from deadbee", page)
        self.assertIn("prefers-color-scheme:dark", page)
        self.assertIn('id="theme"', page)
        self.assertIn('name="viewport"', page)

    def test_deterministic(self):
        self.assertEqual(self.build(), self.build())
        self.assertNotEqual(self.build(built_at="A"), self.build(built_at="B"))


class TitleColumnTests(TempProject):
    def model(self, header, *rows, **cfg):
        self.config(**cfg)
        self.tracker(*rows, header=header)
        from board import cli
        c, _ = cli.load_tickets_config(self.root)
        m = cli.build_model(self.root, c)
        return {t["id"]: t for t in m.tickets}, c, m

    def test_title_and_synonyms(self):
        for name in ("Title", "title", "Issue", "Summary", "Name", "SUBJECT"):
            t, _, _ = self.model(f"| ID | {name} | State |\n|---|---|---|\n", "| AB-1 | Hello | Planned |")
            self.assertEqual(t["AB-1"]["title"], "Hello", name)
            self.assertEqual(t["AB-1"]["extra"], {}, name)

    def test_title_wins_over_synonym(self):
        t, _, _ = self.model("| ID | Name | Title | State |\n|---|---|---|---|\n", "| AB-1 | N | T | Planned |")
        self.assertEqual(t["AB-1"]["title"], "T")

    def test_title_column_setting(self):
        h = "| ID | Title | Heading | State |\n|---|---|---|---|\n"
        t, _, _ = self.model(h, "| AB-1 | Old | New | Planned |", titleColumn="heading")
        self.assertEqual(t["AB-1"]["title"], "New")
        t, _, _ = self.model(h, "| AB-1 | Old | New | Planned |", titleColumn="Missing")
        self.assertEqual(t["AB-1"]["title"], "")

    def test_file_heading_beats_cell(self):
        self.ticket("AB-1", "Planned", title="From file")
        self.config()
        self.tracker("| AB-1 | From cell | Planned |", "| AB-2 | Only cell | Planned |",
                     header="| ID | Issue | State |\n|---|---|---|\n")
        from board import cli
        c, _ = cli.load_tickets_config(self.root)
        t = {x["id"]: x for x in cli.build_model(self.root, c).tickets}
        self.assertEqual(t["AB-1"]["title"], "From file")
        self.assertEqual(t["AB-2"]["title"], "Only cell")

    def test_link_and_hostile_text(self):
        t, c, m = self.model("| ID | Summary | State |\n|---|---|---|\n",
                             "| AB-1 | [Fix <b>it</b>](x.md) now <script>alert(1)</script> | Planned |")
        self.assertEqual(t["AB-1"]["title"], "Fix <b>it</b> now <script>alert(1)</script>")
        page = render.build_html(m, self.root, c, "T0", "abc1234")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("Fix &lt;b&gt;it&lt;/b&gt; now &lt;script&gt;", page)

    def test_synonym_listed_in_extra_columns_stays_an_extra(self):
        t, _, _ = self.model("| ID | Name | State | Note |\n|---|---|---|---|\n", "| AB-1 | Sam | Planned | n |",
                             extraColumns=["Name"])
        self.assertEqual(t["AB-1"]["title"], "")
        self.assertEqual(t["AB-1"]["extra"], {"Name": "Sam"})
        t, _, _ = self.model("| ID | Name | Title | State |\n|---|---|---|---|\n", "| AB-1 | Sam | T | Planned |",
                             extraColumns=["Name"])
        self.assertEqual(t["AB-1"]["title"], "T")
        t, _, _ = self.model("| ID | Name | State |\n|---|---|---|\n", "| AB-1 | Sam | Planned |",
                             extraColumns=["Name"], titleColumn="Name")
        self.assertEqual(t["AB-1"]["title"], "Sam")

    def test_nested_parentheses_and_image_alt(self):
        t, _, _ = self.model("| ID | Title | State |\n|---|---|---|\n",
                             "| AB-1 | [a](http://x/(y)) z | Planned |", "| AB-2 | ![logo](i.png) mark | Planned |")
        self.assertEqual(t["AB-1"]["title"], "a z")
        self.assertEqual(t["AB-2"]["title"], "logo mark")

    def test_check_warns_on_bad_title_column(self):
        self.ticket("AB-1", "Planned")
        for setting, text in (("Missing", "not a column"), ("State", "names the State column")):
            self.config(titleColumn=setting)
            self.tracker("| AB-1 | x | Planned |")
            code, out, err = self.check()
            self.assertEqual((code, out), (0, ""), setting)
            self.assertIn("warning: tickets.titleColumn", err)
            self.assertIn(text, err)
        self.config(titleColumn="Title")
        self.assertEqual(self.check()[1:], ("", ""))

    def test_check_unchanged_and_exit_0(self):
        self.tracker("| AB-1 | x | Planned |", header="| ID | Issue | State |\n|---|---|---|\n")
        self.ticket("AB-1", "Planned")
        self.assertEqual(self.check()[0], 0)


class CliTests(BoardProject):
    def test_writes_board_path_and_out(self):
        code, out, err = run("--root", self.root)
        self.assertEqual((code, err), (0, ""))
        path = os.path.join(self.root, "docs/tickets/board.html")
        self.assertIn(path, out)
        self.assertTrue(os.path.getsize(path) > 1000)
        code, out, _ = run("--root", self.root, "--out", "x/y/other.html")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "x/y/other.html")))

    def test_rebuild_differs_only_in_built_line(self):
        run("--root", self.root)
        path = os.path.join(self.root, "docs/tickets/board.html")
        first = read(path)
        with mock.patch.object(render, "now_utc", return_value="LATER"):
            from board import cli
            import io
            cli.main(["--root", self.root], io.StringIO(), io.StringIO())
        second = read(path)
        strip = lambda s: re.sub(r'<span class="built" id="built">.*?</span>', "", s)
        self.assertNotEqual(first, second)
        self.assertEqual(strip(first), strip(second))

    def test_atomic_write_leaves_old_file_and_no_temp(self):
        path = os.path.join(self.root, "b.html")
        render.write_atomic(path, "old")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                render.write_atomic(path, "new")
        self.assertEqual(read(path), "old")
        self.assertEqual(os.listdir(self.root).count("b.html"), 1)
        self.assertFalse([n for n in os.listdir(self.root) if n.endswith(".tmp")])

    def test_write_error_exit_2_no_partial(self):
        os.makedirs(os.path.join(self.root, "blocked"))
        code, _, err = run("--root", self.root, "--out", "blocked")     # target is a directory
        self.assertEqual(code, 2)
        self.assertIn("cannot write", err)
        self.assertFalse([n for n in os.listdir(os.path.join(self.root)) if n.endswith(".tmp")])
        self.assertEqual(os.listdir(os.path.join(self.root, "blocked")), [])

    def test_does_not_touch_tickets_and_json_unchanged(self):
        before = read(os.path.join(self.root, "docs/tickets/tracker.md"))
        run("--root", self.root)
        self.assertEqual(read(os.path.join(self.root, "docs/tickets/tracker.md")), before)
        self.assertIn("labels", json.loads(run("--json", "--root", self.root)[1])["tickets"][0])

    def test_stale_warning_in_check(self):
        run("--root", self.root)
        board = os.path.join(self.root, "docs/tickets/board.html")
        _, _, err = run("--check", "--root", self.root)
        self.assertNotIn("older than", err)
        old = os.path.getmtime(board) - 100
        os.utime(board, (old, old))
        _, _, err = run("--check", "--root", self.root)
        self.assertIn("warning: board file is older than docs/tickets/tracker.md", err)
        self.assertIn("older than docs/tickets/IOS-1.md", err)

    def test_no_board_no_warning(self):
        self.assertNotIn("warning", run("--check", "--root", self.root)[2])

    def test_commit_read_without_git(self):
        os.makedirs(os.path.join(self.root, ".git", "refs", "heads"))
        with open(os.path.join(self.root, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")
        with open(os.path.join(self.root, ".git", "refs", "heads", "main"), "w") as f:
            f.write("0123456789abcdef0123456789abcdef01234567\n")
        self.assertEqual(render.read_commit(self.root), "0123456")


if __name__ == "__main__":
    unittest.main()
