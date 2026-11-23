"""A small, safe Markdown to HTML renderer for ticket text.

Supports headings, paragraphs, lists, fenced code, block quotes, rules, tables, code spans,
links, bold and italic. All text is HTML-escaped before any markup is added, and a link target
is kept only when it is http, https, a relative path or a `#` anchor. Raw HTML in the source
is shown as text, never passed through.
"""
import html
import re

from .parse import split_row

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_SEPARATOR = re.compile(r"^:?-+:?$")
_LINK = re.compile(r"\[([^\]]{0,500})\]\(([^)\s]*)\)")
_CODE = re.compile(r"(`+)(.+?)\1")


def esc(text):
    return html.escape(text, quote=True)


def safe_url(url):
    """The url when it is http(s), `#...` or a relative path; else None."""
    url = url.strip()
    if not url or _CONTROL.search(url):
        return None
    if url.startswith("#"):
        return url
    if url.startswith("//"):
        return None
    m = _SCHEME.match(url)
    if m:
        return url if m.group(0).lower() in ("http:", "https:") else None
    return url


def inline(text):
    """Escaped HTML for one line of inline Markdown."""
    codes = []

    def stash(m):
        codes.append("<code>" + esc(m.group(2).strip()) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE.sub(stash, text.replace("\x00", ""))
    text = esc(text)

    def link(m):
        url = safe_url(html.unescape(m.group(2)))
        if url is None:
            return m.group(0)          # shown as plain text
        codes.append(f'<a href="{esc(url)}" rel="noopener noreferrer">')
        codes.append("</a>")
        return f"\x00{len(codes) - 2}\x00{m.group(1)}\x00{len(codes) - 1}\x00"

    text = _LINK.sub(link, text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_(?=\S)([^_]+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _is_table_start(lines, i):
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    sep = split_row(lines[i + 1])
    return "|" in lines[i + 1] and all(_SEPARATOR.match(c) for c in sep) and len(sep) == len(split_row(lines[i]))


def _table(lines, i):
    head = split_row(lines[i])
    out = ["<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
    i += 2
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        cells = split_row(lines[i])
        cells = (cells + [""] * len(head))[:len(head)]
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
        i += 1
    out.append("</tbody></table>")
    return "".join(out), i


MAX_QUOTE_DEPTH = 20


def render(text, _depth=0):
    """HTML for a Markdown document. Block quotes nest at most MAX_QUOTE_DEPTH deep."""
    lines = text.splitlines()
    out, para, i = [], [], 0

    def flush():
        if para:
            out.append("<p>" + " ".join(inline(p.strip()) for p in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush()
            marker, body = fence.group(1), []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + esc("\n".join(body)) + "</code></pre>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            level = min(len(m.group(1)) + 1, 6)       # the page owns h1
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if _RULE.match(line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if _is_table_start(lines, i):
            flush()
            block, i = _table(lines, i)
            out.append(block)
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            if _depth >= MAX_QUOTE_DEPTH:
                out.append("<p>" + esc(" ".join(quote)) + "</p>")
            else:
                out.append("<blockquote>" + render("\n".join(quote), _depth + 1) + "</blockquote>")
            continue
        m = _ITEM.match(line)
        if m:
            flush()
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            items = []
            while i < len(lines):
                m = _ITEM.match(lines[i])
                if m and (m.group(2)[0].isdigit()) == (tag == "ol"):
                    items.append(m.group(3))
                    i += 1
                elif items and lines[i].strip() and lines[i].startswith((" ", "\t")) and not _ITEM.match(lines[i]):
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out)
