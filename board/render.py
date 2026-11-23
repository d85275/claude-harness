"""Render a Model as one self-contained HTML page, and write it atomically.

Everything dynamic is escaped here at build time; the inline script only toggles visibility.
The output depends only on the model, the ticket files and `built_at`/`commit`, so a rebuild
with the same input differs only in the "built at" line.
"""
import datetime
import os
import re
import tempfile
import zlib

from . import assets
from .markdown import esc, render as markdown
from .parse import prefix_info, read_text

COLUMNS = (
    ("Planned", ("Planned",)),
    ("In progress", ("In progress",)),
    ("In review", ("In review", "Fixing")),
    ("Owner check", ("Owner check",)),
    ("Blocked", ("Blocked", "Deferred")),
    ("Done", ("Done",)),
)
REVIEW_LEVELS = ("high-risk", "standard", "self-review")


def review_level(text):
    """Normalised review level from the Review column: high-risk, standard, self-review or ''."""
    t = (text or "").lower().replace("_", "-")
    if "high" in t:
        return "high-risk"
    if "self" in t:
        return "self-review"
    if "standard" in t:
        return "standard"
    return ""


def column_for(ticket):
    state = ticket["state"] or ticket["fileState"] or "Planned"
    for name, states in COLUMNS:
        if state in states:
            return name
    return "Planned"


def read_commit(root):
    """Short commit of HEAD, read from the .git folder without running git; '' if unknown."""
    try:
        gitdir = os.path.join(root, ".git")
        if os.path.isfile(gitdir):
            with open(gitdir, encoding="utf-8") as f:
                target = f.read().strip()
            if not target.startswith("gitdir:"):
                return ""
            gitdir = os.path.join(root, target[7:].strip())
        common = gitdir
        cfile = os.path.join(gitdir, "commondir")
        if os.path.isfile(cfile):
            with open(cfile, encoding="utf-8") as f:
                common = os.path.join(gitdir, f.read().strip())
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            return head[:7] if re.fullmatch(r"[0-9a-f]{7,64}", head) else ""
        ref = head[4:].strip()
        for base in (gitdir, common):
            path = os.path.join(base, ref)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    return f.read().strip()[:7]
        with open(os.path.join(common, "packed-refs"), encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0][:7]
    except (OSError, ValueError):
        pass
    return ""


def _twins(tickets, cfg):
    """id -> ids of tickets with the same kind and number on another twin platform."""
    plats = cfg["twinPlatforms"] or []
    groups = {}
    for t in tickets:
        if t["platform"] in plats:      # same rule as check.py: the prefix kind, not a file label
            _, kind = prefix_info(t["id"], cfg["idPrefixes"])
            groups.setdefault((kind, t["id"].rpartition("-")[2]), []).append(t)
    out = {}
    for members in groups.values():
        for t in members:
            out[t["id"]] = [o["id"] for o in members if o["platform"] != t["platform"]]
    return out


def _icon(kind):
    hue = zlib.crc32(kind.encode("utf-8")) % 360 if kind else 0
    letter = esc(kind[:1].upper()) if kind else "#"
    style = f"background:hsl({hue},45%,40%)" if kind else "background:#6b7385"
    return f'<span class="icon" style="{style}" title="{esc(kind or "no kind")}">{letter}</span>'


def _card(t, level, mismatch, extra_cols):
    badges = []
    if level:
        badges.append(f'<span class="tag {level}">{esc(level)}</span>')
    if mismatch:
        badges.append('<span class="tag mismatch">mismatch</span>')
    if t["archived"]:
        badges.append('<span class="tag">archived</span>')
    badges += [f'<span class="tag">{esc(l)}</span>' for l in t["labels"]]
    extras = "".join(f'<div class="extra">{esc(c)}: {esc(t["extra"][c])}</div>'
                     for c in extra_cols if t["extra"].get(c))
    if t.get("liveBranch"):
        badges.append(f'<span class="tag live">from {esc(t["liveBranch"])}</span>')
    search = esc((t["id"] + " " + t["title"]).lower())
    return (f'<button type="button" class="card" data-id="{esc(t["id"])}" data-kind="{esc(t["kind"])}" '
            f'data-platform="{esc(t["platform"])}" data-review="{esc(level)}" '
            f'data-mismatch="{1 if mismatch else 0}" data-search="{search}">'
            f'<div class="top">{_icon(t["kind"])}<span class="id">{esc(t["id"])}</span></div>'
            f'<div class="title">{esc(t["title"])}</div>'
            f'<div class="tags">{"".join(badges)}</div>{extras}</button>')


def _issue(t, root, level, problems, twins, extra_cols):
    body = ""
    if t["file"]:
        text, _ = read_text(os.path.join(root, t["file"]))
        if text:
            lines = text.splitlines()
            if lines and lines[0].lstrip().startswith("# "):
                lines = lines[1:]
            try:
                body = markdown("\n".join(lines))
            except Exception:       # one bad body must not stop the build
                body = "<pre>" + esc("\n".join(lines)) + "</pre>"
    rows = [("State", t["state"] or t["fileState"] or "unknown"), ("Review", t["review"] or level or "-"),
            ("Commit", t["commit"] or "-")]
    if t.get("liveBranch"):
        rows.append(("Live work", "state from " + t["liveBranch"]))
    if t["note"]:
        rows.append(("Note", t["note"]))
    if t["file"]:
        rows.append(("File", t["file"]))
    for c in extra_cols:
        if t["extra"].get(c):
            rows.append((c, t["extra"][c]))
    cells = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows)
    if twins:
        links = ", ".join(f'<a href="#{esc(x)}">{esc(x)}</a>' for x in twins)
        cells += f"<dt>Twin</dt><dd>{links}</dd>"
    warn = ""
    if problems:
        warn = '<div class="warnbox"><strong>Mismatch</strong><ul>' + \
               "".join(f"<li>{esc(p.message)}</li>" for p in problems) + "</ul></div>"
    tid = esc(t["id"])
    return (f'<section class="issue" data-id="{tid}" hidden>'
            f'<div class="bar"><button type="button" data-close>Close</button>'
            f'<button type="button" data-copy="{tid}">Copy link</button></div>'
            f'<h2 class="name" id="title-{tid}">{tid}: {esc(t["title"])}</h2>{warn}'
            f'<dl class="details">{cells}</dl><div class="md">{body}</div></section>')


def _options(label, values):
    opts = "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in sorted(values))
    return f'<option value="">{label}</option>{opts}'


def build_html(model, root, cfg, built_at, commit):
    tickets = model.tickets
    extra_cols = list(cfg["extraColumns"])
    by_ticket = {}
    for p in model.problems:
        if p.ticket:
            by_ticket.setdefault(p.ticket, []).append(p)
    twins = _twins(tickets, cfg)
    columns = {name: [] for name, _ in COLUMNS}
    issues = []
    for t in tickets:
        level = review_level(t["review"])
        probs = by_ticket.get(t["id"], [])
        columns[column_for(t)].append(_card(t, level, bool(probs), extra_cols))
        issues.append(_issue(t, root, level, probs, twins.get(t["id"], []), extra_cols))
    cols_html = "".join(
        f'<section class="col" aria-label="{esc(name)}"><h2><span>{esc(name)}</span><span class="count">'
        f'{len(columns[name])}</span></h2>{"".join(columns[name])}<div class="empty"'
        f'{" hidden" if columns[name] else ""}>Nothing here</div></section>' for name, _ in COLUMNS)
    kinds = {t["kind"] for t in tickets if t["kind"]}
    plats = {t["platform"] for t in tickets if t["platform"]}
    levels = [lv for lv in REVIEW_LEVELS if any(review_level(t["review"]) == lv for t in tickets)]
    built = f"built at {esc(built_at)} from {esc(commit or 'unknown commit')}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket board</title>
<style>{assets.CSS}</style>
</head>
<body>
<header><h1>Ticket board</h1><div><span class="built" id="built">{built}</span>
<button type="button" id="theme">Theme</button></div></header>
<div class="tools">
<input type="search" id="q" placeholder="Search ID or title" aria-label="Search ID or title">
<select id="f-kind" aria-label="Kind">{_options("All kinds", kinds)}</select>
<select id="f-platform" aria-label="Platform">{_options("All platforms", plats)}</select>
<select id="f-review" aria-label="Review level">{_options("All reviews", levels)}</select>
<label><input type="checkbox" id="f-mismatch"> Mismatches only</label>
</div>
<main class="board">{cols_html}</main>
<div id="issues" role="dialog" aria-modal="true" hidden>{"".join(issues)}</div>
<script>{assets.JS}</script>
</body>
</html>
"""


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_atomic(path, text):
    """Write `text` to `path` through a temp file in the same folder and a rename."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".board-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def stale_sources(root, cfg, model, board_path):
    try:
        built = os.path.getmtime(board_path)
    except OSError:
        return []
    paths = [cfg["trackerPath"]] + ([cfg["archiveTracker"]] if cfg["archive"] and cfg["archiveTracker"] else [])
    paths += [t["file"] for t in model.tickets if t["file"]]
    out = []
    for rel in paths:
        try:
            if os.path.getmtime(os.path.join(root, rel)) > built:
                out.append(rel)
        except OSError:
            pass
    return out
