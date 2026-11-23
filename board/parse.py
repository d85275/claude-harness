"""Read the tracker and ticket files into a Model. Never raises on bad input: a malformed
row, file or table becomes a Problem."""
import glob
import os
import re

from .model import STATES, KNOWN_COLUMNS, Model, Problem, Row, TicketFile

GENERIC_ID = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*-\d+"
LINK = re.compile(r"^\[([^\]]*)\]\(([^)]*)\)$")
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
    """Cells of a table row; `\\|` is a literal bar."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _is_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells)


def parse_tracker(path, rel, archived, problems, title_col="", extras=(), warnings=None):
    """Rows of the first table with ID and State columns. Problems go to `problems`."""
    text, err = read_text(path)
    if err:
        problems.append(Problem("tracker-missing", "", f"{rel}: cannot read tracker ({err})"))
        return []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        header = split_row(line)
        lower = [h.lower() for h in header]
        if "id" in lower and "state" in lower:
            return _parse_table(lines, i, header, rel, archived, problems, title_col, extras, warnings)
        # skip the rest of a table that is not the tracker
    problems.append(Problem("tracker-malformed", "", f"{rel}: no table with ID and State columns"))
    return []


def _parse_table(lines, start, header, rel, archived, problems, title_col="", extras=(), warnings=None):
    lower = [h.lower() for h in header]
    ti = title_column(lower, title_col, extras)
    if title_col.strip() and warnings is not None:
        if ti is None:
            warnings.append(f"warning: tickets.titleColumn {title_col!r} is not a column of {rel}; cards show no tracker title")
        elif lower[ti] in [c.lower() for c in KNOWN_COLUMNS if c != "Title"]:
            warnings.append(f"warning: tickets.titleColumn {title_col!r} names the {header[ti]} column of {rel}, "
                            "which holds something else; cards show it as the title")
    if start + 1 >= len(lines) or not _is_separator(split_row(lines[start + 1])):
        problems.append(Problem("tracker-malformed", "", f"{rel}:{start + 1}: table header has no separator row"))
        return []
    rows = []
    for n in range(start + 2, len(lines)):
        line = lines[n]
        if not line.lstrip().startswith("|"):
            break
        cells = split_row(line)
        where = f"{rel}:{n + 1}"
        if len(cells) != len(header):
            problems.append(Problem("row-malformed", "", f"{where}: row has {len(cells)} cells, header has {len(header)}"))
            continue
        by = dict(zip(header, cells))
        raw_id = by[header[lower.index("id")]]
        link = None
        m = LINK.match(raw_id)
        if m:
            raw_id, link = m.group(1).strip(), m.group(2).strip()
        raw_id = raw_id.strip("*_` ")
        if not raw_id:
            problems.append(Problem("row-malformed", "", f"{where}: row has an empty ID"))
            continue

        def col(name):
            return next((v for h, v in by.items() if h.lower() == name), "")

        extra = {h: v for h, v in by.items() if h.lower() not in [c.lower() for c in KNOWN_COLUMNS]
                 and h != (header[ti] if ti is not None else None)}
        rows.append(Row(id=raw_id.upper(), link=link, title=plain_title(cells[ti]) if ti is not None else "", review=col("review"),
                        state=col("state"), commit=col("commit"), note=col("note"),
                        extra=extra, archived=archived, source=rel))
    return rows


def parse_ticket(path, rel, ticket_id, archived, problems):
    text, err = read_text(path)
    if err:
        problems.append(Problem("file-malformed", ticket_id, f"{rel}: cannot read ticket file ({err})"))
        return None
    lines = text.splitlines()
    tf = TicketFile(id=ticket_id, path=rel, archived=archived)
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
    if prefixes:
        alts = "|".join(re.escape(p.rstrip("-")) for p in prefixes)
        return re.compile(rf"(?:{alts})-\d+", re.I)
    return re.compile(GENERIC_ID)


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


def collect_files(root, dirs, globs, pattern, archived, problems, known=frozenset()):
    """Ticket files: a stem that fits `pattern` or equals an ID in `known` (a tracker row ID)."""
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
        stem = os.path.basename(path)[:-3]
        if not os.path.isfile(path) or not (pattern.fullmatch(stem) or stem.upper() in known):
            continue  # TEMPLATE.md, tracker.md, notes
        tf = parse_ticket(path, os.path.relpath(path, root), stem.upper(), archived, problems)
        if tf:
            found.append(tf)
    return found


def prefix_info(ticket_id, prefixes):
    """(platform, kind) for an ID, from the configured prefix table, else ("", "")."""
    prefix = ticket_id.rsplit("-", 1)[0].upper()
    for p, info in prefixes.items():
        if p.rstrip("-").upper() == prefix:
            return info["platform"], info["kind"]
    return "", ""


def build(root, tickets_cfg):
    """Parse everything the config points at into a Model (problems from parsing only)."""
    model = Model()
    t = tickets_cfg
    prefixes = t["idPrefixes"]
    pattern = id_pattern(prefixes)
    tcol = t.get("titleColumn", "")
    tracker = os.path.join(root, t["trackerPath"])
    if not os.path.exists(tracker):
        model.problems.append(Problem("tracker-missing", "", f"{t['trackerPath']}: tracker not found"))
    else:
        model.rows.extend(parse_tracker(tracker, t["trackerPath"], False, model.problems, tcol, t["extraColumns"], model.warnings))
    if t["archive"] and t["archiveTracker"]:
        path = os.path.join(root, t["archiveTracker"])
        if not os.path.exists(path):
            model.problems.append(Problem("tracker-missing", "", f"{t['archiveTracker']}: archive tracker not found"))
        else:
            model.rows.extend(parse_tracker(path, t["archiveTracker"], True, model.problems, tcol, t["extraColumns"], model.warnings))
    # Rows are read first so a file named after a row ID is a ticket even when the ID has no hyphen.
    known = frozenset(r.id for r in model.rows)
    model.files.extend(collect_files(root, t["dirs"], t["featureGlobs"], pattern, False, model.problems, known))
    if t["archive"]:
        model.files.extend(collect_files(root, t["archiveDirs"], [], pattern, True, model.problems, known))
    return model
