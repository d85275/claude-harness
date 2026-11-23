"""Read the tracker and ticket files into a Model. Never raises on bad input: a malformed
row, file or table becomes a Problem."""
import glob
import os
import re

from .model import STATES, KNOWN_COLUMNS, Model, Problem, Row, TicketFile

GENERIC_ID = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*-\d+"
LOOSE_ID = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*-?\d+"   # also K12 (adoption scans)
LINK = re.compile(r"^\[([^\]]*)\]\(([^)]*)\)(?:\s+\S.*)?$")   # text after the link is allowed
LINKS = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]*)")
SPLIT_ID = re.compile(r"^(.*?)-?(\d+)$")
TITLE_SYNONYMS = ("Title", "Issue", "Summary", "Name", "Subject")
_URL = r"\((?:[^()]|\([^()]*\))*\)"            # a link target with one level of parentheses
INLINE_LINK = re.compile(r"(!?)\[([^\]]*)\]" + _URL)


def title_column(lower, configured="", extras=()):
    """Index of the title column in a lower-cased header. A configured name wins. Otherwise
    `Title`, else a synonym that is not listed in `extras` (extraColumns). None when there is none."""
    if configured and configured.strip():
        name = configured.strip().lower()
        return lower.index(name) if name in lower else None
    skip = {e.strip().lower() for e in extras}
    for name in (n.lower() for n in TITLE_SYNONYMS):
        if name in lower and (name == "title" or name not in skip):
            return lower.index(name)
    return None


def plain_title(text):
    """A cell as plain text: Markdown links reduced to their text (an image to its alt text)."""
    return INLINE_LINK.sub(lambda m: m.group(2), text).strip()


def split_id(ticket_id):
    """(prefix, number) of an ID such as AB-12, AB-QA-7 or K12; number is '' when there is none."""
    m = SPLIT_ID.match(ticket_id)
    return (m.group(1), m.group(2)) if m else (ticket_id, "")


def read_text(path):
    """(text, error). CRLF and a BOM are normalised away."""
    try:
        with open(path, encoding="utf-8-sig", newline=None) as f:
            return f.read(), None
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def state_names(aliases):
    """Lower-case spelling -> standard state. Aliases may name a standard state in any case."""
    names = {s.lower(): s for s in STATES}
    for alias, target in (aliases or {}).items():
        std = next((s for s in STATES if s.lower() == str(target).lower()), None)
        if std and alias.strip():
            names[alias.strip().lower()] = std
    return names


def resolve_state(text, names):
    """Standard state for the leading word(s) of text (case-insensitive), else None."""
    t = (text or "").strip().lstrip("*_`~ ").lower()
    for name in sorted(names, key=len, reverse=True):
        if t.startswith(name) and not (len(t) > len(name) and t[len(name)].isalnum()):
            return names[name]
    return None


def split_row(line):
    """Cells of a table row. `\\|` is a literal bar, and so is a bar inside a code span."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i, n = [], [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and s.startswith("|", i + 1):
            cur.append("|")
            i += 2
        elif c == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            close = s.find(s[i:j], j)
            end = close + (j - i) if close != -1 else j
            cur.append(s[i:end].replace("\\|", "|"))
            i = end
        elif c == "|":
            cells.append("".join(cur).strip())
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    cells.append("".join(cur).strip())
    return cells


def _is_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells)


def new_stats():
    """What parse_tracker read or skipped. `skipped_rows` and `idlike_tables` are rows and tables
    that look like tracker data but are not read; `reasons` says why per place (path:line);
    `fenced` are `|` lines with an ID in their first cell inside a code fence."""
    return {"tables": 0, "rows": 0, "skipped_tables": [], "skipped_rows": [], "extra_tables": [],
            "idlike_tables": [], "fenced": [], "reasons": {}, "unclosed_fence": "", "first_cells": {}}


OPEN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def fence_step(line, open_marker):
    """(marker of the fence open after this line or None, is this line a fence line?). A fence
    closes only with the same character, at least as long, and nothing after it; an inline span
    such as ```make``` opens nothing (CommonMark)."""
    m = OPEN_FENCE.match(line)
    if not m:
        return open_marker, False
    marker, rest = m.group(1), m.group(2)
    if open_marker is None:
        if marker[0] == "`" and "`" in rest:
            return None, False
        return marker, True
    if marker[0] == open_marker[0] and len(marker) >= len(open_marker) and not rest.strip():
        return None, True
    return open_marker, False


def is_fence(line):
    """Does this line open a code fence when none is open? Kept for callers that only toggle."""
    return fence_step(line, None)[1]


def parse_tracker(path, rel, archived, problems, title_col="", extras=(), warnings=None, headers=None, stats=None,
                  strict=False):
    """Rows of the first table (outside code fences) with ID and State columns; a blank line ends a
    table. Problems go to `problems`; the header cells are appended to `headers` and what was read
    or skipped (and where, and why) to `stats`, when given. Everything after the first table is
    only counted in `stats`, as plain `--check` always did; with `strict` (adoption) rows outside
    a table and other tables with an ID-like column are also problems."""
    st = stats if stats is not None else new_stats()
    text, err = read_text(path)
    if err:
        problems.append(Problem("tracker-missing", "", f"{rel}: cannot read tracker ({err})"))
        return []
    lines = text.splitlines()
    rows, fence, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        fence, is_line = fence_step(line, fence)
        fenced = fence is not None
        if is_line:
            if fenced:
                st["fence_at"] = f"{rel}:{i + 1}"
            i += 1
            continue
        if fenced:
            if line.lstrip().startswith("|"):
                cells = split_row(line)
                if cells and re.fullmatch(LOOSE_ID, _cell_id(cells[0]) or "-"):
                    st["fenced"].append(f"{rel}:{i + 1}")
            i += 1
            continue
        if not line.lstrip().startswith("|"):
            i += 1
            continue
        header = split_row(line)
        lower = [h.lower() for h in header]
        if "id" in lower and "state" in lower and not st["tables"]:
            if headers is not None:
                headers.extend(header)
            got, i = _parse_table(lines, i, header, rel, archived, problems, st, title_col, extras, warnings)
            rows.extend(got)
            continue
        start = i
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            i += 1
        if "id" in lower and "state" in lower and i - start >= 2 and _is_separator(split_row(lines[start + 1])):
            st["extra_tables"].append(f"{rel}:{start + 1}")
            for k in range(start + 2, i):
                st["skipped_rows"].append(f"{rel}:{k + 1}")
                st["reasons"][f"{rel}:{k + 1}"] = "in a later table with ID and State columns (only the first is read)"
            continue
        _skipped_block(lines[start:i], start, rel, problems if strict else [], st)
    if fence is not None:
        st["unclosed_fence"] = st.get("fence_at", rel)
    if not st["tables"]:
        problems.append(Problem("tracker-malformed", "", f"{rel}: no table with ID and State columns"))
    return rows


ID_HEADERS = ("id", "ticket", "key", "#")


def _skipped_block(block, start, rel, problems, st):
    """A block of `|` lines that is not a table with ID and State columns. Rows with no header
    (after a blank line, or after prose) and tables with an ID-like column are recorded in `st` and
    become problems (only `problems` given by a strict parse is kept); a plain other table (a
    legend) is only counted."""
    where = f"{rel}:{start + 1}"
    if len(block) < 2 or not _is_separator(split_row(block[1])):
        for k, line in enumerate(block):
            cells = split_row(line)
            tid = _cell_id(cells[0]) if cells and re.fullmatch(LOOSE_ID, _cell_id(cells[0]) or "-") else ""
            at = f"{rel}:{start + k + 1}"
            problems.append(Problem("row-malformed", tid, f"{at}: row is outside any table "
                                    "(after a blank line, or after prose); not read"))
            st["skipped_rows"].append(at)
            st["reasons"][at] = "row is outside any table (after a blank line, or after prose)"
        return
    lower = [h.lower().strip("*_` ") for h in split_row(block[0])]
    first = [_cell_id(split_row(l)[0]) for l in block[2:] if split_row(l)]
    ids = [c for c in first if re.fullmatch(LOOSE_ID, c)]
    looks_ids = bool(first) and len(ids) == len(first)
    st["skipped_tables"].append(where)
    st["first_cells"][where] = [split_row(l)[0] for l in block[2:] if split_row(l) and split_row(l)[0]]
    if ids or any(h in ("id", "ticket") for h in lower) or (looks_ids and any(h in ID_HEADERS for h in lower)):
        st["idlike_tables"].append((where, " | ".join(lower), ids))
        problems.append(Problem("tracker-malformed", "", f"{where}: table with an ID-like first column or header "
                                f"({' | '.join(lower)}) has no ID and State columns; its rows are not read"))


def _cell_id(raw):
    m = LINK.match(raw)
    text = (m.group(1) if m else raw).strip().strip("*_` ")
    first = text.split(None, 1)[0] if text.split() else ""
    return (first if re.fullmatch(LOOSE_ID, first) else text).upper()


def _parse_table(lines, start, header, rel, archived, problems, st, title_col="", extras=(), warnings=None):
    """(rows, index of the first line after the table)."""
    lower = [h.lower() for h in header]
    ti = title_column(lower, title_col, extras)
    if title_col.strip() and warnings is not None and not st["tables"]:
        if ti is None:
            warnings.append(f"warning: tickets.titleColumn {title_col!r} is not a column of {rel}; cards show no tracker title")
        elif lower[ti] in [c.lower() for c in KNOWN_COLUMNS if c != "Title"]:
            warnings.append(f"warning: tickets.titleColumn {title_col!r} names the {header[ti]} column of {rel}, "
                            "which holds something else; cards show it as the title")
    if start + 1 >= len(lines) or not _is_separator(split_row(lines[start + 1])):
        problems.append(Problem("tracker-malformed", "", f"{rel}:{start + 1}: table header has no separator row"))
        st["skipped_tables"].append(f"{rel}:{start + 1}")
        return [], start + 1
    st["tables"] += 1
    for name in dict.fromkeys(lower):
        if lower.count(name) > 1:
            problems.append(Problem("tracker-header", "", f"{rel}:{start + 1}: column {name!r} appears "
                                    f"{lower.count(name)} times in the header; the first one is used"))
    known = [c.lower() for c in KNOWN_COLUMNS]
    id_at = lower.index("id")
    rows = []
    n = start + 2
    while n < len(lines):
        line = lines[n]
        if not line.lstrip().startswith("|"):
            break
        cells = split_row(line)
        where = f"{rel}:{n + 1}"
        n += 1
        if len(cells) != len(header):
            st["skipped_rows"].append(where)
            st["reasons"][where] = f"row has {len(cells)} cells, header has {len(header)}"
            tid = _cell_id(cells[id_at]) if id_at < len(cells) else ""
            problems.append(Problem("row-malformed", tid, f"{where}: row has {len(cells)} cells, header has {len(header)}"))
            continue
        raw_id = cells[id_at]
        link = None
        m = LINK.match(raw_id)
        if m:
            raw_id, link = m.group(1).strip(), m.group(2).strip()
        raw_id = raw_id.strip("*_` ")
        first = raw_id.split(None, 1)[0] if raw_id.split() else ""
        if first != raw_id and re.fullmatch(LOOSE_ID, first):
            raw_id = first                                  # `AB-5 :warning:` is AB-5
        if not raw_id:
            problems.append(Problem("row-malformed", "", f"{where}: row has an empty ID"))
            st["skipped_rows"].append(where)
            st["reasons"][where] = "row has an empty ID cell"
            continue

        def col(name):
            return next((c for h, c in zip(lower, cells) if h == name), "")

        extra = {}
        for h, c in zip(header, cells):
            if h.lower() not in known and h not in extra and h != (header[ti] if ti is not None else None):
                extra[h] = c
        links = list(dict.fromkeys(t for c in cells for t in LINKS.findall(c)))
        rows.append(Row(id=raw_id.upper(), link=link, title=plain_title(cells[ti]) if ti is not None else "",
                        review=col("review"), state=col("state"), commit=col("commit"), note=col("note"),
                        extra=extra, archived=archived, source=rel, links=links, line=n))
        st["rows"] += 1
    return rows, n


def _heading_id(lines):
    """The ID a ticket file's first line names (`# <ID>: <title>`), as written, else ''."""
    head = next((l for l in lines if l.strip()), "")
    m = re.match(r"#\s+(\S+?):\s*(.*)$", head.strip())
    return m.group(1).strip("`") if m else ""


def _twin_lines(lines, pattern):
    """(twin IDs, no twin wanted?) from `Twin: <ID>` lines; `Twin: none` (any text after it) says the
    ticket has no twin."""
    ids, single, fence = [], False, None
    find = re.compile(r"(?<![\w-])(?:" + pattern.pattern + r")(?![\w])", re.I)
    for l in lines:
        fence, is_line = fence_step(l, fence)
        if is_line:
            continue
        t = re.sub(r"[*_`]", "", l).strip()
        m = None if fence is not None else re.match(r"(?:[-+]\s+)?twin\s*:\s*(.*)$", t, re.I)
        if not m:
            continue
        if re.match(r"none\b", m.group(1), re.I):
            single = True
        else:
            ids.extend(x.group(0).upper() for x in find.finditer(m.group(1)) if x.group(0).upper() not in ids)
    return ids, single


def parse_ticket(path, rel, ticket_id, archived, problems, pattern=None):
    """A TicketFile; an unreadable file still gives one (with a problem), so that its tracker row
    is not also reported as having no file."""
    text, err = read_text(path)
    tf = TicketFile(id=ticket_id, path=rel, archived=archived)
    if err:
        problems.append(Problem("file-malformed", ticket_id, f"{rel}: cannot read ticket file ({err})"))
        return tf
    lines = text.splitlines()
    head = next((l for l in lines if l.strip()), "")
    m = re.match(r"#\s+(\S+?):\s*(.*)$", head.strip())
    if m:
        tf.title = m.group(2).strip()
        if m.group(1).strip("`").upper() != ticket_id:
            problems.append(Problem("file-malformed", ticket_id,
                                    f"{rel}: heading names {m.group(1)}, file name says {ticket_id}"))
    else:
        problems.append(Problem("file-malformed", ticket_id, f"{rel}: first line is not '# <ID>: <title>'"))
    status = None
    for i, l in enumerate(lines):
        if re.match(r"##\s+Status\s*$", l.strip(), re.I):
            for nxt in lines[i + 1:]:
                if nxt.startswith("#"):
                    break
                if nxt.strip():
                    status = nxt.strip()
                    break
            break
    tf.labels = _labels(lines)
    tf.twins, tf.single = _twin_lines(lines, pattern or id_pattern({}))
    if status is None:
        problems.append(Problem("file-malformed", ticket_id, f"{rel}: no '## Status' section with a status line"))
    else:
        tf.status = status
    return tf


def _labels(lines):
    """Labels from an optional '## Labels' section: comma separated and/or one per bullet."""
    for i, l in enumerate(lines):
        if re.match(r"##\s+Labels\s*$", l.strip(), re.I):
            found = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("#"):
                    break
                for part in re.sub(r"^\s*[-*+]\s+", "", nxt).split(","):
                    part = part.strip().strip("`*_ ")
                    if part and part not in found:
                        found.append(part)
            return found
    return []


def id_pattern(prefixes):
    """Pattern of a whole ID. With configured prefixes the hyphen is optional (AB-12 and K12)."""
    if prefixes:
        alts = "|".join(re.escape(p.rstrip("-")) for p in sorted(prefixes, key=len, reverse=True))
        return re.compile(rf"(?:{alts})-?\d+", re.I)
    return re.compile(GENERIC_ID)


def ticket_id_of(path, pattern, fallback=True):
    """(ID, note) for a markdown file that may be a ticket, else (None, ''). The ID is the whole
    file name when it is one (`AB-12.md`). With `fallback` (configured prefixes, or the adoption
    scan) it is else the `# <ID>:` heading, else an ID the file name starts with
    (`AB-12-short-title.md`); such a file is skipped when `<ID>.md` exists, since that one is the
    ticket. `note` names a disagreement between heading and name."""
    stem = os.path.basename(path)[:-3]
    if pattern.fullmatch(stem):
        return stem.upper(), ""
    if not fallback:
        return None, ""
    m = pattern.match(stem)
    named = m.group(0).upper() if m and (m.end() == len(stem) or stem[m.end()] in "-_. ") else ""
    text, _ = read_text(path)
    heading = _heading_id(text.splitlines()) if text else ""
    heading = heading.upper() if pattern.fullmatch(heading) else ""
    tid = heading or named
    if not tid:
        return None, ""
    try:
        if tid.lower() + ".md" in (n.lower() for n in os.listdir(os.path.dirname(path) or ".")):
            return None, ""
    except OSError:
        pass
    return tid, (f"file name starts with {named}" if heading and named and named != heading else "")


def _dirs(root, dirs, globs):
    """Existing directories from `dirs` plus directories matched by `globs`; files matched by a
    glob are returned separately."""
    out, files = [], []
    for d in dirs:
        out.append(os.path.join(root, d))
    for g in globs:
        for hit in sorted(glob.glob(os.path.join(root, g), recursive=True)):
            (out if os.path.isdir(hit) else files).append(hit)
    return list(dict.fromkeys(out)), list(dict.fromkeys(files))


def collect_files(root, dirs, globs, pattern, archived, problems, fallback=True, known=frozenset()):
    """Ticket files: a stem that fits `pattern` or equals an ID in `known` (a tracker row ID), else
    (with `fallback`) a heading or file-name prefix ID; see ticket_id_of."""
    found, paths = [], []
    folders, extra_files = _dirs(root, dirs, globs)
    for folder in folders:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue  # a missing folder just holds no tickets
        paths.extend(os.path.join(folder, n) for n in names if n.lower().endswith(".md"))
    paths.extend(p for p in extra_files if p.lower().endswith(".md"))
    for path in dict.fromkeys(paths):
        if not os.path.isfile(path):
            continue
        stem = os.path.basename(path)[:-3]
        tid, note = (stem.upper(), "") if stem.upper() in known else ticket_id_of(path, pattern, fallback)
        if tid is None:
            continue  # TEMPLATE.md, tracker.md, notes
        rel = os.path.relpath(path, root)
        tf = parse_ticket(path, rel, tid, archived, problems, pattern)
        if note:
            problems.append(Problem("file-malformed", tid, f"{rel}: {note}"))
        found.append(tf)
    return found


def prefix_info(ticket_id, prefixes):
    """(platform, kind) for an ID, from the configured prefix table, else ("", "")."""
    prefix = split_id(ticket_id)[0].upper()
    for p, info in prefixes.items():
        if p.rstrip("-").upper() == prefix:
            return info["platform"], info["kind"]
    return "", ""


def build(root, tickets_cfg, strict=False):
    """Parse everything the config points at into a Model (problems from parsing only). `strict`
    (adoption) also makes rows outside the tracker table and ID-like tables problems."""
    model = Model()
    t = tickets_cfg
    prefixes = t["idPrefixes"]
    pattern = id_pattern(prefixes)
    tcol = t.get("titleColumn", "")
    tracker = os.path.join(root, t["trackerPath"])
    if not os.path.exists(tracker):
        model.problems.append(Problem("tracker-missing", "", f"{t['trackerPath']}: tracker not found"))
    else:
        model.stats[t["trackerPath"]] = new_stats()
        model.rows.extend(parse_tracker(tracker, t["trackerPath"], False, model.problems, tcol, t["extraColumns"],
                                        model.warnings, stats=model.stats[t["trackerPath"]], strict=strict))
    if t["archive"] and t["archiveTracker"]:
        path = os.path.join(root, t["archiveTracker"])
        if not os.path.exists(path):
            model.problems.append(Problem("tracker-missing", "", f"{t['archiveTracker']}: archive tracker not found"))
        else:
            model.stats[t["archiveTracker"]] = new_stats()
            model.rows.extend(parse_tracker(path, t["archiveTracker"], True, model.problems, tcol, t["extraColumns"],
                                            model.warnings, stats=model.stats[t["archiveTracker"]], strict=strict))
    # Rows are read first so a file named after a row ID is a ticket even when the ID has no hyphen.
    known = frozenset(r.id for r in model.rows)
    model.files.extend(collect_files(root, t["dirs"], t["featureGlobs"], pattern, False, model.problems, bool(prefixes), known))
    if t["archive"]:
        model.files.extend(collect_files(root, t["archiveDirs"], [], pattern, True, model.problems, bool(prefixes), known))
    return model
