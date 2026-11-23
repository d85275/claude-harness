"""Adopt mode of `dev-harness-board init`: take over a project that already has a tracker or
ticket folder, without rewriting any ticket.

    dev-harness-board init --mode              print `mode: fresh`, `adopt` or `switched` (+ why); writes nothing
    dev-harness-board init --check [--branch B] [--prefix P[:plat[:kind]]]
                                               the dry run: the list, and its hash; writes nothing
    dev-harness-board init --confirm HASH [--branch B] [--prefix ...]
                                               the switch, only while the list still has that hash

A scan cannot prove it missed nothing, so the owner confirms what the board will show. The dry run
prints the complete list of tickets the plugin's board would show, per column, each with its
source (ticket file and tracker row) and what its state was read from; every file or row it did
not use, and why; and every "could not tell" item. It also compares the list with an old board page
when there is one (information only: the owner confirms the list), says how the settings were
found, ends with a complete `tickets` block for harness.json, and prints a hash of the list, the
settings and the files the switch writes.

The switch (`--confirm HASH`) recomputes all of it and writes only when the hash is the same, so
nothing changes between showing and writing. It writes only: the `tickets` keys missing from
.claude/harness.json (existing keys are kept; this is what makes `dev-harness-board status` say
yes), a Harness section in CLAUDE.md when there is none (printed otherwise), and the board. It never
installs hooks, never deletes the old board script or hooks, and hashes the trackers, ticket files
and TEMPLATE before and after to prove they are unchanged.

The rule the output keeps: every row and every .md file in the scanned area that looks like a
ticket (see ticket_evidence and _row_candidates) is in the list, under Not used with a reason, or
under Could not tell. Hard blockers, which no confirmation lifts: every Not used item marked
[BLOCKS] (unreadable files and folders, non-UTF-8 trackers and tickets, skipped or empty-ID rows,
later or ID-like tables, quoted/list/HTML rows in a tracker, duplicate rows whose states disagree,
duplicate files, tracker-like tables in other files of the ticket folders, files in subfolders the
settings do not read, ticket files outside them), a state the settings do not know, no state or an
empty State cell, no single tracker, a harness.json that cannot be read, merged or written, a
`tickets.idPrefixes` that lacks a prefix in use, an integration branch that is only a guess.

Exit codes: --mode 0; --check 0 when the switch could go ahead once confirmed, 1 when it is
blocked; the switch 0 done, 1 blocked, not confirmed, list changed or failed; 2 bad usage or
unreadable config.
"""
import copy
import glob
import hashlib
import json
import os
import re
import stat
from collections import Counter

from . import check as checks
from . import githooks
from . import parse
from . import render
from .model import KNOWN_COLUMNS

SKIP_DIRS = {"node_modules", "build", ".git", ".gradle", "Pods", "vendor", "DerivedData", ".worktrees", "dist", "target"}
# Never searched at all (tool and package folders); dot folders and deeper folders get a lighter look.
NEVER = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", ".gradle",
         "Pods", "DerivedData", ".worktrees"}
# Folders whose tickets are test data or examples, not this project's own (they never decide the mode).
FIXTURE_DIRS = {"test", "tests", "testdata", "fixtures", "fixture", "examples", "example", "samples", "sample"}
SURVEY_DEPTH = 6
STRONG_HEAD = re.compile(r"^#\s+`?(" + parse.LOOSE_ID + r")`?:")              # the board's `# <ID>:` heading
WEAK_HEAD = re.compile(r"^#{1,6}\s+`?([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*-\d+)`?(?:\s|:|$)")
WEAK_STEM = re.compile(r"^([A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-\d+)(?:[-_ ]|$)")    # AB-12-login, upper case only
SLUG_STEM = re.compile(r"^([a-z]+-\d+)(?:[-_ ]|$)")                                     # ab-12-login
# An ID-like token anywhere in a line: AB-12, AB_20, AB-16b, AB-24.1, K12 (not v2 or 2024).
ID_TOKEN = re.compile(r"(?<![\w.-])([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z][A-Za-z0-9]*)*[-_]\d+[A-Za-z]?(?:\.\d+)*"
                      r"|[A-Z]+\d+[a-z]?)(?![\w-])")
NUM_HEAD = re.compile(r"^#{1,6}\s+#?(\d+)\s*:")
KEY_LINE = re.compile(r"^(?:id|ticket|key|title)\s*:\s*(.+)$", re.I)
ARCHIVE_TOKENS = {"archive", "archived", "archives", "done", "closed", "history", "old", "past"}


def is_archive_name(name):
    """Whole-word test on a folder name: `old-tickets` yes, `threshold` no."""
    return any(t in ARCHIVE_TOKENS or t.startswith("archiv") for t in re.split(r"[-_. ]+", name.lower()) if t)
PLATFORM_WORDS = {"IOS": "ios", "AND": "android", "ANDROID": "android", "API": "backend", "WEB": "web", "DOCS": "docs",
                  "DESK": "desktop", "DESKTOP": "desktop"}
# What an unknown state word most likely means. A guess: the owner confirms it.
SUGGEST_STATE = {
    "integrated": "Owner check", "implemented": "In review", "waiting": "Blocked", "on hold": "Blocked",
    "to do": "Planned", "todo": "Planned", "backlog": "Planned", "proposed": "Planned", "open": "Planned",
    "new": "Planned", "closed": "Done", "complete": "Done", "completed": "Done", "deployed": "Done",
    "released": "Done", "accepted": "Done", "shipped": "Done", "resolved": "Done", "verified": "Done",
    "wip": "In progress", "started": "In progress", "active": "In progress", "review": "In review",
    "testing": "Owner check", "cancelled": "Deferred", "canceled": "Deferred", "dropped": "Deferred",
    "postponed": "Deferred", "parked": "Deferred", "later": "Deferred", "rejected": "Deferred",
    "obsolete": "Deferred", "superseded": "Deferred"}
TICKETS_KEYS = ("dirs", "featureGlobs", "idPrefixes", "twinPlatforms", "archive", "trackerPath",
                "integrationBranch", "boardPath", "stateAliases", "archiveTracker", "archiveDirs", "extraColumns",
                "titleColumn", "notesFiles")
COLUMN_NAMES = [name for name, _ in render.COLUMNS]
LOOSE = re.compile(parse.LOOSE_ID)


def _hc():
    from . import cli
    return cli.harness_config


def _read(path):
    text, _ = parse.read_text(path)
    return text


def _md(folder):
    try:
        return sorted(n for n in os.listdir(folder) if n.lower().endswith(".md") and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        return []


def _walk(base, depth, errors=None):
    """(folder, subfolders, files) down to `depth` levels, skipping build and hidden folders.
    A folder that cannot be read is appended to `errors` (when given), not skipped silently."""
    top = base.rstrip(os.sep).count(os.sep)
    onerror = (lambda e: errors.append(f"{e.filename}: {e.strerror}")) if errors is not None else None
    for d, dirs, files in os.walk(base, onerror=onerror):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        if d.count(os.sep) - top >= depth:
            dirs[:] = []
        yield d, dirs, files


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


# --------------------------------------------------------------------------- mode

def own_board(root, cfg):
    """Is the file at boardPath a page this plugin wrote (its generator marker)?"""
    text = _read(os.path.join(root, cfg["boardPath"]))
    return bool(text) and 'name="generator" content="dev-harness-board"' in text


def _read_any(path):
    """(text, why not readable): text decoded as UTF-8, or with replacement characters and why='not
    UTF-8'; (None, reason) when the file cannot be opened."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, e.strerror or type(e).__name__
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n"), ""
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace").replace("\r\n", "\n"), "not UTF-8"


def _ticketish_name(rel):
    """A path whose name alone says it may be a ticket or a tracker."""
    name = os.path.basename(rel)
    return bool(LOOSE.fullmatch(name[:-3])) or "tracker" in name.lower() or "tickets" in rel.lower().split("/")[:-1]


def _state_table(text):
    """Does the text hold a table (with a row) with an ID-like header and a state or status header?"""
    lines = text.splitlines()
    for i in range(len(lines) - 2):
        if lines[i].lstrip().startswith("|") and parse._is_separator(parse.split_row(lines[i + 1])) \
                and lines[i + 1].lstrip().startswith("|") and lines[i + 2].lstrip().startswith("|"):   # with a row
            lower = [h.lower().strip("*_` ") for h in parse.split_row(lines[i])]
            if any(h in parse.ID_HEADERS for h in lower) and ("state" in lower or "status" in lower):
                return True
    return False


def _is_fixture(rel):
    return any(seg.lower() in FIXTURE_DIRS for seg in rel.split("/")[:-1])


def ticket_evidence(rel, text):
    """('strong' | 'weak' | '', ID). Strong: the file name is an ID and the heading names it or it
    has a Status section, or the first heading is the board's `# <ID>:`. Weak: the name starts with
    an upper-case ID (`AB-12-login.md`) or a heading names an ID without the colon."""
    stem = os.path.basename(rel)[:-3]
    lines = text.splitlines()
    head = next((l.strip() for l in lines if l.strip()), "")
    if LOOSE.fullmatch(stem) and (parse._heading_id(lines).upper() == stem.upper()
                                  or re.search(r"^##\s+Status\s*$", text, re.M | re.I)):
        return "strong", stem.upper()
    m = STRONG_HEAD.match(head)
    if m:
        return "strong", m.group(1).upper()
    m = WEAK_STEM.match(stem) or WEAK_HEAD.match(head) or SLUG_STEM.match(stem)
    if m:
        return "weak", m.group(1).upper()
    heading = next((l.strip() for l in lines if l.lstrip().startswith("#")), "")
    m = ID_TOKEN.search(heading) or NUM_HEAD.match(heading)
    if m:
        return "weak", m.group(1).upper()
    for l in lines[:15]:                         # front matter or leading `id: AB-12` lines
        k = KEY_LINE.match(l.strip())
        t = ID_TOKEN.search(k.group(1)) if k else None
        if t:
            return "weak", t.group(1).upper()
    if LOOSE.fullmatch(stem):
        return "weak", stem.upper()
    return "", ""


def survey(root, depth=SURVEY_DEPTH):
    """Everything in the project that looks like tickets, and every place the look did not reach.

    tables: md files with a table with an ID-like and a state/status column; ticket_files: strong
    evidence (see ticket_evidence); maybe: weak evidence; fixtures: either kind inside test or
    example folders; unreadable: (md file, why) for a file that cannot be opened, or is not UTF-8
    and looks like a ticket or tracker; odd: *.md entries that are not regular files; folder_errors:
    folders that cannot be listed; symlinks: linked folders (not followed); hidden: (area, [ticket-
    like md files]) for dot folders and folders deeper than `depth`, which get a name-only look;
    not_searched: {folder name: count} of tool folders (node_modules, .git, ...) never searched."""
    out = {"tables": [], "ticket_files": [], "maybe": [], "fixtures": [], "unreadable": [], "odd": [],
           "folder_errors": [], "symlinks": [], "hidden": [], "not_searched": Counter(), "ids": {}}
    errors = []
    top = root.rstrip(os.sep).count(os.sep)
    md = []
    for d, dirs, files in os.walk(root, onerror=lambda e: errors.append(e)):
        keep = []
        for x in sorted(dirs):
            full = os.path.join(d, x)
            rel = _rel(root, full)
            if os.path.islink(full):
                out["symlinks"].append(rel)
            elif x in NEVER:
                out["not_searched"][x] += 1
            elif x.startswith(".") or d.count(os.sep) - top >= depth:
                hits = _name_look(root, full)
                out["hidden"].append((rel, hits))
            else:
                keep.append(x)
        dirs[:] = keep
        for n in sorted(files):
            if n.lower().endswith(".md"):
                p = os.path.join(d, n)
                try:
                    regular = stat.S_ISREG(os.stat(p).st_mode)
                except OSError:
                    regular = False
                (md if regular else out["odd"]).append(_rel(root, p))
    out["folder_errors"] = [f"{_rel(root, e.filename) if e.filename else '?'}: {e.strerror}" for e in errors]
    for rel in md:
        name = os.path.basename(rel)
        text, why = _read_any(os.path.join(root, rel))
        if text is None:
            out["unreadable"].append((rel, why))
            continue
        if name.lower() == "template.md":
            continue
        kind, tid = ticket_evidence(rel, text)
        is_table = not kind and _state_table(text)
        if why and (kind or is_table or _ticketish_name(rel)):
            out["unreadable"].append((rel, why))
        if (kind or is_table) and _is_fixture(rel):
            out["fixtures"].append(rel)
        elif kind == "strong":
            out["ticket_files"].append(rel)
        elif kind == "weak":
            out["maybe"].append(rel)
        elif is_table:
            out["tables"].append(rel)
        if kind:
            out["ids"][rel] = tid
    return out


def _name_look(root, folder):
    """Ticket-like .md files (by file name only) below a folder the survey does not read."""
    hits = []
    for d, dirs, files in os.walk(folder):
        dirs[:] = sorted(x for x in dirs if x not in NEVER and not os.path.islink(os.path.join(d, x)))
        for n in sorted(files):
            if n.lower().endswith(".md") and (LOOSE.fullmatch(n[:-3]) or WEAK_STEM.match(n[:-3]) or SLUG_STEM.match(n[:-3])):
                hits.append(_rel(root, os.path.join(d, n)))
    return hits


def _ticket_folder_files(root, cfg):
    """Markdown files (any depth) in the configured ticket folders, other than TEMPLATE.md."""
    out = []
    for d in cfg["dirs"]:
        base = os.path.join(root, d)
        for dd, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            out.extend(_rel(root, os.path.join(dd, n)) for n in files
                       if n.lower().endswith(".md") and n.lower() != "template.md")
    return out


def decide_mode(root, cfg, fresh_ok=False):
    """(mode, why). Adopt takes over whenever anything in the project looks like tickets: a tracker
    with rows, any md file with an ID and State table, any file named by a ticket ID, or a non-empty
    file at trackerPath that is not init's empty one. harness.json settings prove nothing.
    'switched' means the board at boardPath is this plugin's page, `--check` is clean and nothing
    beside the configured folders was left unread: nothing to do. Fresh only when none of those exist."""
    from . import init as initmod
    defaults = _hc().DEFAULTS["tickets"]
    sv = survey(root)
    tracker_rel, cands = find_tracker(root, {"trackerPath": cfg["trackerPath"]}, defaults, sv)
    trackers = [tracker_rel] if tracker_rel else [c for c in cands if not _is_fixture(c)]
    with_rows = [t for t in trackers if parse.parse_tracker(os.path.join(root, t), t, False, [])]
    tp = os.path.join(root, cfg["trackerPath"])
    odd = os.path.isfile(tp) and _read(tp) != initmod._template("tracker.md") and not with_rows
    bad = [f"{rel} ({why})" for rel, why in sv["unreadable"] if why == "not UTF-8" or _ticketish_name(rel)]
    mine = lambda rels: [r for r in rels if not _is_fixture(r)]           # test and example folders never count
    tables, tfiles, maybe = mine(sv["tables"]), mine(sv["ticket_files"]), mine(sv["maybe"])
    in_folder = [r for r in _ticket_folder_files(root, cfg) if r != cfg["trackerPath"]]   # init's own tracker aside
    if not (with_rows or tables or tfiles or maybe or odd or bad or in_folder or sv["folder_errors"]):
        if sv["fixtures"] and not fresh_ok:
            return "adopt", ("tickets or trackers exist only in test or example folders (" + ", ".join(sv["fixtures"][:3])
                             + "); the list shows them. If they are only test data, run init with --fresh")
        return "fresh", "no tracker with rows, no table with ID and State columns, no file named by a ticket ID" + (
            "; test or example folders hold ticket-like files, taken as test data: " + ", ".join(sv["fixtures"][:5])
            if sv["fixtures"] else "")
    if with_rows and own_board(root, cfg):
        model = _model(root, cfg)
        folders = {_rel(root, p) for p in parse._dirs(root, cfg["dirs"] + (cfg["archiveDirs"] if cfg["archive"] else []),
                                                       cfg["featureGlobs"])[0]}
        read = {t["file"] for t in model.tickets if t["file"]}
        unread = [f for f in tfiles + maybe if f not in read] + [r for r, _ in sv["unreadable"]]
        others = [t for t in tables if os.path.dirname(t) in folders and t not in (cfg["trackerPath"], cfg["archiveTracker"])]
        if not model.problems and not unread and not others and not sv["folder_errors"]:
            return "switched", f"the board at {cfg['boardPath']} is this plugin's page and --check is clean"
    if with_rows:
        return "adopt", f"tracker {with_rows[0]} has rows"
    if odd:
        return "adopt", f"{cfg['trackerPath']} exists, is not init's empty tracker and has no readable table"
    if bad:
        return "adopt", "a file that may be a ticket or tracker cannot be read: " + ", ".join(bad[:3])
    if sv["folder_errors"] and not (tables or tfiles or maybe or in_folder):
        return "adopt", "a folder cannot be read, so it is not known whether it holds tickets: " + ", ".join(sv["folder_errors"][:3])
    found = (tables + tfiles + maybe + in_folder)[:3]
    return "adopt", "found what looks like tickets: " + ", ".join(found)


# ---------------------------------------------------------------------- discovery

def find_tracker(root, given, defaults, sv=None):
    """(relative path or None, candidates). Without a tracker at the configured or default path, the
    only md file with an ID and State table (any name, outside archive folders) is it."""
    rel = given.get("trackerPath") or defaults["trackerPath"]
    if os.path.isfile(os.path.join(root, rel)):
        return rel, []
    cands = [c for c in (sv or survey(root))["tables"]
             if not any(is_archive_name(seg) for seg in c.split("/")[:-1]) and not _is_fixture(c)]
    return (cands[0] if len(cands) == 1 else None), cands


def _tracker_problem(path):
    """Why a file cannot serve as a tracker, or None when it holds a table with ID and State."""
    if _read(path) is None:
        return "it cannot be read"
    out = []
    parse.parse_tracker(path, "", False, out)
    if any(p.kind == "tracker-malformed" and "no table" in p.message for p in out):
        return "it has no table with ID and State columns"
    return None


def _probe(folder):
    """Why `folder` cannot be listed, or None."""
    try:
        os.listdir(folder)
    except OSError as e:
        return f"{e.strerror}"
    return None


def _unconsidered(folder):
    """Entries a ticket folder holds that are not read: .markdown files, broken links, odd files."""
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        p = os.path.join(folder, n)
        if n.lower().endswith(".markdown") or (os.path.islink(p) and not os.path.exists(p)) \
                or (os.path.exists(p) and not os.path.isfile(p) and not os.path.isdir(p)):
            out.append(n)
    return out


def discover(root, tracker_rel, base_dirs):
    """Folders around the tracker: dict with archiveDirs, archiveTracker, featureGlobs and notes."""
    found = {"archiveDirs": [], "archiveTracker": "", "featureGlobs": [], "notes": [], "covered": [],
             "unreadable": [], "could_not": []}
    tdir = os.path.dirname(tracker_rel)
    absdir = os.path.join(root, tdir)
    try:
        subs = sorted(n for n in os.listdir(absdir) if os.path.isdir(os.path.join(absdir, n)) and not n.startswith("."))
    except OSError as e:
        subs = []
        found["unreadable"].append(f"{tdir or '.'}: {e.strerror}")
    for n in subs:
        rel = f"{tdir}/{n}" if tdir else n
        if is_archive_name(n):
            found["archiveDirs"].append(rel)
        elif _md(os.path.join(root, rel)):
            found["notes"].append(f"{rel}: {len(_md(os.path.join(root, rel)))} markdown file(s) in a subfolder that is "
                                  "not archive-like; not read as tickets (add it to featureGlobs or archiveDirs if it holds tickets)")
    trackers = []
    for a in found["archiveDirs"]:
        for n in _md(os.path.join(root, a)):
            if "tracker" in n.lower():
                why = _tracker_problem(os.path.join(root, a, n))
                if why:
                    found["notes"].append(f"found {a}/{n} but could not use it: {why}")
                    found["could_not"].append(f"archive tracker {a}/{n}: {why}")
                else:
                    trackers.append(f"{a}/{n}")
    if trackers:
        found["archiveTracker"] = trackers[0]
        if len(trackers) > 1:
            found["notes"].append("several archive trackers found (" + ", ".join(trackers) + "); suggesting the first")
    base = os.path.dirname(absdir) if tdir else absdir
    if base == root:
        base = absdir
    skip = [os.path.join(root, d) for d in [tdir] + found["archiveDirs"]]
    groups = []
    for d, dirs, _ in _walk(base, 4, found["unreadable"]):
        if os.path.basename(d) == "tickets" and not any(d == s or d.startswith(s + os.sep) for s in skip) and _md(d):
            parent = os.path.dirname(_rel(root, d))
            glob_ = f"{os.path.dirname(parent)}/*/tickets" if os.path.dirname(parent) else f"*/tickets"
            if glob_ not in groups:
                groups.append(glob_)
    for g in groups:                       # a glob that would also match the ticket folder itself is not a feature glob
        hits = [h for h in glob.glob(os.path.join(root, g)) if os.path.isdir(h)]
        found["featureGlobs"].append(g)
        found["covered"].extend(_rel(root, h) for h in hits)
    return found


def _guess(prefix):
    segs = prefix.upper().split("-")
    plat = next((PLATFORM_WORDS[s] for s in segs if s in PLATFORM_WORDS), "app")
    rest = [s.lower() for s in segs if s not in PLATFORM_WORDS]
    return {"platform": plat, "kind": "-".join(rest) if rest and len(segs) > 1 else "feature"}


def state_key(text):
    """The state word of a state or Status text: 'Waiting on x (y)' -> 'Waiting'."""
    t = re.sub(r"[`*_~]", "", text or "").strip()
    head = re.split(r"[(:;,.—–]|\s-\s|\d", t, 1)[0].strip()
    words = head.split()
    if len(words) >= 2 and " ".join(words[:2]).lower() in SUGGEST_STATE:
        return " ".join(words[:2])
    return words[0] if words else ""


def state_word(text, names):
    """The state a tracker cell starts with: a known name when it has one ('In progress'), else
    the first word. Lower case, so 'Planned' and 'planned' count together."""
    t = re.sub(r"[`*_~]", "", text or "").strip()
    for name in sorted(names, key=len, reverse=True):
        if t.lower().startswith(name) and not (len(t) > len(name) and t[len(name)].isalnum()):
            return name
    return state_key(t).lower() or "(empty)"


def _model(root, cfg):
    model = parse.build(root, cfg, strict=True)
    checks.merge(model, root, cfg)
    checks.check(model, root, cfg)
    return model


def _branch(root):
    """(branch or None, why not): the branch the main checkout has checked out."""
    if not os.path.isdir(os.path.join(root, ".git")):
        return None, "not a git repository"
    text = _read(os.path.join(root, ".git", "HEAD"))
    m = re.match(r"ref:\s*refs/heads/(\S+)", text or "")
    return (m.group(1), "") if m else (None, "HEAD is detached or unreadable")


# ---------------------------------------------------------------------- old board

def read_old_board(root, cfg):
    """{'found': False} or {'found': True, 'path', 'tickets': {ID: plugin column} | None, 'why',
    'anomalies'}. A page this plugin wrote is not an old board."""
    cands = [cfg["boardPath"]]
    for d in cfg["dirs"] + ["docs"]:
        cands += [_rel(root, p) for p in sorted(glob.glob(os.path.join(root, d, "*board*.html")))]
    existing = [c for c in dict.fromkeys(cands) if os.path.isfile(os.path.join(root, c))]
    own = [c for c in existing if 'name="generator" content="dev-harness-board"' in (_read(os.path.join(root, c)) or "")]
    existing = [c for c in existing if c not in own]
    if not existing:
        return {"found": False, "own": own}
    path = existing[0]
    out = {"found": True, "path": path, "tickets": None, "why": "", "others": existing[1:], "anomalies": [], "own": own}
    text = _read(os.path.join(root, path))
    m = re.search(r"<script[^>]*\bid=[\"']data[\"'][^>]*>(.*?)</script>", text or "", re.S | re.I)
    try:
        data = json.loads(m.group(1)) if m else None
    except ValueError:
        data = None
    if text is None:
        out["why"] = "the file cannot be read"
    elif not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
        out["why"] = "no embedded ticket data (a <script id=\"data\"> JSON object with a tickets list) found in it"
    else:
        cols = data.get("columns") or []
        labels = {c[0]: c[1] for c in cols if isinstance(c, (list, tuple)) and len(c) == 2}
        tickets, bad, dups = {}, 0, []
        for t in data["tickets"]:
            if not isinstance(t, dict) or not t.get("id"):
                bad += 1
                continue
            label = str(labels.get(t.get("state"), t.get("state") or ""))
            col = next((n for n in COLUMN_NAMES if label.lower().startswith(n.lower())), None)
            tid = str(t["id"]).upper()
            if tid in tickets:
                dups.append(tid)
            tickets[tid] = col or f"(column {label!r} is not a plugin column)"
        if bad:
            out["anomalies"].append(f"{bad} entr{'y' if bad == 1 else 'ies'} of the old board have no id and were not compared")
        if dups:
            out["anomalies"].append("the old board lists these ids more than once (the last one was compared): "
                                    + ", ".join(sorted(set(dups))))
        if not tickets:
            out["why"] = "its ticket list is empty or has no ids"
        else:
            out["tickets"] = tickets
    return out


# ------------------------------------------------------------------ old tooling

SCRIPT_REF = re.compile(r"[\w./${}()-]+\.(?:py|sh|js|rb|pl)\b")


def find_tooling(root):
    """Facts about an old board script and git hooks that the switch would replace."""
    t = {"scripts": [], "folders": {}, "hooks_path": None, "hooks_scope": None, "hooks_path_unknown": False,
         "plugin_hooks": False, "git_hooks": [], "lfs_warnings": [], "common": None}
    for top in ("scripts", "tools", "bin", "docs/tickets", ".githooks"):
        for d, _, files in _walk(os.path.join(root, top), 2) if os.path.isdir(os.path.join(root, top)) else ():
            for n in files:
                low = n.lower()
                if "board" in low and any(w in low for w in ("ticket", "tracker", "kanban")) \
                        and not low.endswith((".html", ".pyc")) and "__pycache__" not in d:
                    t["scripts"].append(_rel(root, os.path.join(d, n)))
    if not os.path.exists(os.path.join(root, ".git")):
        return t
    value, scope = githooks.hooks_path(root)            # the effective value, any scope
    code, _, _ = githooks._git(["config", "--get", "core.hooksPath"], root)
    t["hooks_path"], t["hooks_scope"] = value, scope
    t["hooks_path_unknown"] = code not in (0, 1)        # 1 means "not set"; anything else is git failing
    t["common"] = githooks.common_dir(root)
    candidates = [".githooks", ".husky", "githooks", "git-hooks"]
    if value:
        candidates.insert(0, value)
    ours = githooks.hooks_folder(t["common"]) if t["common"] else None
    seen_real = set()
    for c in dict.fromkeys(candidates):
        folder = os.path.expanduser(c) if os.path.isabs(os.path.expanduser(c)) else os.path.join(root, c)
        if os.path.isdir(folder) and os.path.realpath(folder) in seen_real:
            continue                       # the same folder under another spelling
        if os.path.isdir(folder):
            seen_real.add(os.path.realpath(folder))
        if os.path.isdir(folder) and not (ours and os.path.realpath(folder) == os.path.realpath(ours)):
            kinds = {}
            for n in sorted(os.listdir(folder)):
                if not os.path.isfile(os.path.join(folder, n)):
                    continue
                text = _read(os.path.join(folder, n)) or ""
                low = text.lower()
                kinds[n] = ("git lfs" if "lfs" in low else "") + (" board rebuild" if "board" in low else "")
                for ref in dict.fromkeys(SCRIPT_REF.findall(text)):
                    rel = re.sub(r"^(?:\$\{?[\w]+\}?|\$\([^)]*\))/", "", ref).lstrip("./")
                    if rel and os.path.isfile(os.path.join(root, rel)):
                        t["scripts"].append(f"{rel} (called by hook {c}/{n})")
            t["folders"][c] = kinds
    t["scripts"] = list(dict.fromkeys(t["scripts"]))
    if t["common"]:
        t["git_hooks"] = githooks.own_hooks(t["common"])
        t["plugin_hooks"] = githooks.is_ours(value, githooks.hooks_folder(t["common"]))
        for c, kinds in t["folders"].items():
            for n, k in kinds.items():
                if "git lfs" in k:
                    own = (_read(os.path.join(t["common"], "hooks", n)) or "").lower()
                    if "lfs" not in own:
                        t["lfs_warnings"].append(
                            f"{c}/{n} runs Git LFS, but <git dir>/hooks/{n} "
                            f"{'does not mention it' if own else 'does not exist'}. The board hooks chain to "
                            "<git dir>/hooks, not to the old folder: run `git lfs install` first so that LFS hook is not lost")
    return t


# ------------------------------------------------------------------------ the run

def analyse(root, cfg, args, given, invalid_keys, tracker_template):
    """Everything the report and the switch need, from reading the project only."""
    hc = _hc()
    defaults = copy.deepcopy(hc.DEFAULTS["tickets"])
    res = {"notes": [], "blockers": [], "unknown": []}
    sv = survey(root)
    tracker_rel, cands = find_tracker(root, given, defaults, sv)
    res["survey"] = sv
    res["tracker"], res["tracker_candidates"] = tracker_rel, cands
    if tracker_rel is None:
        res["blockers"].append("no tracker found at " + (given.get("trackerPath") or defaults["trackerPath"])
                               + (f" (candidates: {', '.join(cands)})" if cands else "")
                               + "; set tickets.trackerPath, or create the tracker by hand")
        res["COULD NOT TELL"] = ["which file is the tracker"]
    suggestion = dict(defaults)
    tdir = os.path.dirname(tracker_rel) if tracker_rel else (defaults["dirs"][0])
    suggestion.update({"trackerPath": tracker_rel or defaults["trackerPath"], "dirs": [tdir]})
    if tracker_rel and tdir != os.path.dirname(defaults["trackerPath"]):
        suggestion["boardPath"] = f"{tdir}/board.html" if tdir else "board.html"      # next to the tracker
    disc = discover(root, tracker_rel, suggestion["dirs"]) if tracker_rel else {"archiveDirs": [], "archiveTracker": "",
                                                                                  "featureGlobs": [], "notes": [], "covered": [],
                                                                                  "unreadable": [], "could_not": []}
    res["notes"].extend(disc["notes"])
    res["search_base"] = (os.path.dirname(os.path.dirname(tracker_rel)) or ".") if tracker_rel else "(nowhere: no tracker)"
    suggestion.update({"archiveDirs": disc["archiveDirs"], "archiveTracker": disc["archiveTracker"],
                       "archive": bool(disc["archiveDirs"] or disc["archiveTracker"]),
                       "featureGlobs": disc["featureGlobs"]})
    checked_out, why_not = _branch(root)
    if args.branch:
        suggestion["integrationBranch"], res["branch_source"] = args.branch, "--branch"
    elif checked_out in ("main", "master"):
        suggestion["integrationBranch"], res["branch_source"] = checked_out, "the branch checked out in the main checkout"
    else:
        suggestion["integrationBranch"] = defaults["integrationBranch"]
        res["branch_source"] = f"the default ({why_not})" if not checked_out else f"the default (the checked-out branch {checked_out!r} is not main or master)"
        if checked_out and "integrationBranch" not in given:
            res["blockers"].append(f"the checked-out branch is {checked_out!r}, which may not be the integration branch; "
                                   "pass --branch <name> (the guess is not written)")
    # headers and IDs, before any settings exist
    headers, probe = [], []
    rows = []
    if tracker_rel:
        rows += parse.parse_tracker(os.path.join(root, tracker_rel), tracker_rel, False, probe, headers=headers)
    if suggestion["archiveTracker"]:
        rows += parse.parse_tracker(os.path.join(root, suggestion["archiveTracker"]), suggestion["archiveTracker"], True, probe, headers=headers)
    could_not = list(disc["could_not"]) + list(res.get("COULD NOT TELL", []))
    known = [c.lower() for c in KNOWN_COLUMNS]
    suggestion["extraColumns"] = list(dict.fromkeys(h for h in headers if h and h.lower() not in known))
    folders = suggestion["dirs"] + suggestion["archiveDirs"] + disc["covered"]
    from_rows = Counter(parse.split_id(r.id)[0] for r in rows if LOOSE.fullmatch(r.id))
    confirmed, file_only, heading_only = Counter(), Counter(), Counter()
    for f in folders:
        for n in _md(os.path.join(root, f)):
            path = os.path.join(root, f, n)
            tid, _ = parse.ticket_id_of(path, LOOSE)
            if not tid:
                continue
            text = _read(path)
            heading = parse._heading_id(text.splitlines()).upper() if text else ""
            named = n[:-3].upper().startswith(tid)          # a prefix seen only in a heading is not trusted
            if heading == tid and named:
                confirmed[parse.split_id(tid)[0]] += 1
            else:
                (file_only if named else heading_only)[parse.split_id(tid)[0]] += 1
    seen = sorted(set(from_rows) | set(confirmed))
    res["prefixes_seen"] = {p: from_rows[p] + confirmed[p] for p in seen}
    res["prefixes_file_only"] = {p: c for p, c in sorted(file_only.items()) if p not in seen}
    res["prefixes_heading_only"] = {p: c for p, c in sorted(heading_only.items()) if p not in seen}
    suggestion["idPrefixes"] = {p: _guess(p) for p in seen}
    for spec in args.prefix_specs:
        suggestion["idPrefixes"][spec[0]] = spec[1]
    # settings: what harness.json already has wins; the rest comes from the scan
    wanted = {k: copy.deepcopy(given[k]) if k in given else copy.deepcopy(suggestion[k]) for k in TICKETS_KEYS}
    if "idPrefixes" in given:
        missing = [p for p in seen if p.upper() not in {q.rstrip('-').upper() for q in given["idPrefixes"]}]
        if missing:
            res["notes"].append("IDs with prefixes not in your tickets.idPrefixes: " + ", ".join(missing))
            res["blockers"].append("tickets.idPrefixes in harness.json lacks " + ", ".join(missing)
                                   + ": those tickets are not read (add them, or remove the key to take the suggestion)")
    # states: find what the settings do not know yet
    eff0 = _effective(defaults, wanted)
    m0 = _model(root, eff0)
    names = parse.state_names(eff0["stateAliases"])
    texts = [r.state for r in m0.rows] + [f.status for f in m0.files if f.status]
    unknown = {}
    for t in texts:
        if t.strip() and parse.resolve_state(t, names) is None:
            unknown.setdefault(state_key(t).lower(), [state_key(t), 0])[1] += 1
    aliases, undecided, additions = {}, [], {}
    for low, (word, n) in sorted(unknown.items()):
        target = SUGGEST_STATE.get(low)
        if word and target:
            additions[word] = target
        else:
            undecided.append((word or "(empty)", n))
    if "stateAliases" in given:
        if additions:
            res["notes"].append("add to your tickets.stateAliases: " + json.dumps(additions))
        suggestion["stateAliases"] = {**given["stateAliases"], **additions}
    else:
        wanted["stateAliases"] = suggestion["stateAliases"] = additions
    res["alias_additions"], res["undecided_states"] = additions, undecided
    res["unknown_uses"] = sum(n for _, n in unknown.values())
    # twins
    linked = [f for f in m0.files if f.twins]
    if linked:
        order = []
        for f in sorted(linked, key=lambda f: f.id):
            for tid in [f.id] + f.twins:
                p = parse.prefix_info(tid, wanted["idPrefixes"])[0]
                if p and p not in order:
                    order.append(p)
        if len(order) >= 2:
            # Suggest twin checks only when they hold for every ticket: otherwise `--check` (and so
            # /close-ticket) would stop on old tickets that never had a Twin line.
            trial = _effective(defaults, {**wanted, "twinPlatforms": order})
            lacking = sorted({p.ticket for p in _model(root, trial).problems if p.kind == "twin-missing"},
                             key=checks._sort_key)
            missing = len(lacking)
            if not missing:
                suggestion["twinPlatforms"] = order
                if "twinPlatforms" not in given:
                    wanted["twinPlatforms"] = order
            elif "twinPlatforms" not in given:
                suggestion["twinPlatforms"] = None
                shown = ", ".join(lacking[:8]) + (", ..." if missing > 8 else "")
                res["notes"].append(f"Twin lines found, but twinPlatforms {json.dumps(order)} would report {missing} "
                                    f"twin-missing finding(s) ({shown}); left off. Set it by hand, and add "
                                    "`Twin: <ID>` or `Twin: none` lines to new tickets, if you want twin checks")
        elif "twinPlatforms" not in given:
            res["notes"].append("Twin lines found, but fewer than two platforms could be read from the ID prefixes; "
                                "set tickets.twinPlatforms by hand")
            res["COULD NOT TELL"] = res.get("COULD NOT TELL", []) + ["twin platforms"]
    elif "twinPlatforms" not in given:
        pset = {v["platform"] for v in wanted["idPrefixes"].values()}
        if len(pset - {"app"}) >= 2:
            res["notes"].append("prefixes of several platforms exist but no ticket has a `Twin:` line; twinPlatforms stays off")
    for k in TICKETS_KEYS:
        if k in given and k != "stateAliases" and given[k] != suggestion[k]:
            res["notes"].append(f"kept your tickets.{k} from harness.json ({json.dumps(given[k])}); the scan suggests "
                                f"{json.dumps(suggestion[k])}")
    # the effective config and the final model
    eff = _effective(defaults, wanted)
    model = _model(root, eff)
    res.update(wanted=wanted, eff=eff, model=model, rows=m0.rows, suggestion=suggestion)
    res["unreadable_config"] = bool(invalid_keys)
    if invalid_keys:
        res["blockers"].append("harness.json has invalid tickets key(s): " + ", ".join(sorted(invalid_keys))
                               + "; fix them by hand (see the messages above)")
    # folders the settings do not cover
    covered = {_rel(root, p) for p in parse._dirs(root, eff["dirs"] + (eff["archiveDirs"] if eff["archive"] else []),
                                                    eff["featureGlobs"])[0]}
    res["uncovered"] = [f for f in folders + disc["covered"] if f not in covered]
    res["listed_folders"] = folders
    unreadable = list(disc["unreadable"])
    hash_folders = list(dict.fromkeys(folders + sorted(covered)))
    for f in hash_folders:
        if not os.path.lexists(os.path.join(root, f)):
            continue                          # a folder that is not there holds nothing
        why = _probe(os.path.join(root, f))
        if why:
            unreadable.append(f"{f}: {why}")
    res["unreadable"] = list(dict.fromkeys(u.replace(root + os.sep, "") for u in unreadable))
    res["hash_folders"] = hash_folders
    res["unconsidered"] = [f"{f}/{n}" for f in hash_folders for n in _unconsidered(os.path.join(root, f))]
    res["could_not"] = could_not
    res["stats"] = model.stats
    res["no_state"] = [t["id"] for t in model.tickets if not t["state"] and not t["fileState"]
                       and not (t["rowState"] or "").strip() and not (t["fileStatus"] or "").strip()]
    # markdown files not read as tickets
    read = {t["file"] for t in model.tickets if t["file"]}
    skipped = []
    for f in dict.fromkeys(folders):
        for n in _md(os.path.join(root, f)):
            rel = f"{f}/{n}"
            if rel not in read and os.path.basename(rel).lower() != "template.md":
                skipped.append(rel)
    skipped = [s for s in skipped if s not in {tracker_rel, eff["archiveTracker"]}]
    res["skipped"] = skipped
    # R2-4: rows of another tracker file are not read
    res["other_trackers"] = [t for t in sv["tables"] if "tracker" in os.path.basename(t).lower()
                             and t not in {tracker_rel, eff["archiveTracker"]}]
    # R2-3: files named like tickets that are not read (their prefix is not configured)
    prefixes_cfg = {q.rstrip("-").upper() for q in eff["idPrefixes"]}
    unread = []
    for f in dict.fromkeys(hash_folders):
        for n in _md(os.path.join(root, f)):
            rel = f"{f}/{n}"
            if rel in read or n.lower() == "template.md" or rel in {tracker_rel, eff["archiveTracker"]}:
                continue
            if LOOSE.fullmatch(n[:-3]):
                unread.append((rel, parse.split_id(n[:-3].upper())[0]))
    res["unread_named"] = unread
    res["prefixes_cfg"] = sorted(prefixes_cfg)
    # R2-5: ticket files that cannot be read
    res["cannot_read"] = [p for p in model.problems if p.kind == "file-malformed" and "cannot read ticket file" in p.message]
    # files that look like tickets anywhere in the project but are not read (N2), unreadable ones (N1)
    in_folders = set(hash_folders)
    res["outside"] = [f for f in sv["ticket_files"] if f not in read and os.path.dirname(f) not in in_folders]
    res["inside_unread"] = [f for f in sv["ticket_files"] if f not in read and os.path.dirname(f) in in_folders]
    res["survey_unreadable"] = sv["unreadable"]
    res["odd"] = [f for f in sv["odd"] if os.path.dirname(f) not in in_folders]
    res["other_tables"] = [t for t in sv["tables"] if t not in res["other_trackers"]
                           and t not in {tracker_rel, eff["archiveTracker"]}]
    return res


def _effective(defaults, wanted):
    eff = copy.deepcopy(defaults)
    eff.update(copy.deepcopy(wanted))
    return eff


def column_counts(model):
    return Counter(render.column_for(t) for t in model.tickets)


def report(root, res, old, tooling, out):
    w = out.write
    eff, model = res["eff"], res["model"]
    problems = {}
    for p in model.problems:
        problems.setdefault(p.kind, []).append(p)
    could_not = list(res["could_not"])
    w("-- Tracker and folders --\n")
    w(f"tracker: {res['tracker'] or '(not found)'}  ({sum(1 for r in model.rows if not r.archived)} rows)\n")
    for path, st in res["stats"].items():
        w(f"  {path}: {st['tables']} table(s) with ID and State read, {st['rows']} row(s) read, "
          f"{len(st['skipped_rows'])} row(s) skipped, {len(st['skipped_tables']) + len(st['extra_tables'])} other table(s) skipped\n")
        if st["extra_tables"]:
            w(f"    later tables with ID and State columns (only the first is read) at: {', '.join(st['extra_tables'])}\n")
        if st["skipped_tables"]:
            w(f"    tables without ID and State columns (not read) at: {', '.join(st['skipped_tables'])}\n")
        if st["skipped_rows"]:
            w(f"    rows SKIPPED at: {', '.join(st['skipped_rows'])}\n")
    for u in res["unreadable"]:
        w(f"COULD NOT TELL: cannot read {u}\n")
    if eff["archive"]:
        w(f"archive: tracker {eff['archiveTracker'] or '(none)'}, folders {', '.join(eff['archiveDirs']) or '(none)'}\n")
    w(f"ticket folders: {', '.join(eff['dirs'])}; feature globs: {', '.join(eff['featureGlobs']) or '(none)'}\n")
    w(f"feature folders: searched {res['search_base']} (4 levels deep) for folders named tickets\n")
    w(f"tickets read: {len(model.tickets)} ({len(model.files)} files, {len(model.rows)} rows)\n")
    for f in res["uncovered"]:
        w(f"folder not read by the settings: {f}\n")
    for s in res["skipped"]:
        w(f"markdown file not read as a ticket: {s}\n")
    for s in res["unconsidered"]:
        w(f"COULD NOT TELL: {s} is not read (only .md files are; a .markdown file, broken link or odd file)\n")
        could_not.append(f"{s} is not read")
    for c in res["could_not"]:
        w(f"COULD NOT TELL: {c}\n")
    for t in res["other_trackers"]:
        w(f"rows from another tracker are not read: {t}\n")
    for rel, prefix in res["unread_named"]:
        w(f"file named like a ticket but not read: {rel} (its prefix {prefix} is not in idPrefixes)\n")
    for p in res["cannot_read"]:
        w(f"cannot read ticket file: {p.line()}\n")
    for n in res["notes"]:
        w(f"note: {n}\n")
    w("\n-- Ticket counts --\n")
    new = column_counts(model)
    oldc = Counter(c for c in (old.get("tickets") or {}).values())
    have_old = bool(old.get("tickets"))
    w(f"{'column':<14}{'plugin':>8}{'old board':>11}\n")
    for name in COLUMN_NAMES:
        w(f"{name:<14}{new.get(name, 0):>8}{(oldc.get(name, 0) if have_old else '?'):>11}\n")
    w(f"{'total':<14}{sum(new.values()):>8}{(sum(oldc.values()) if have_old else '?'):>11}\n")
    words = Counter()
    names = parse.state_names(eff["stateAliases"])
    for t in model.tickets:
        text = t["rowState"] if t["rowState"] is not None else (t["fileStatus"] or "")
        words[state_word(text, names)] += 1
    w("state words in the tracker (or the ticket file when there is no row): "
      + ", ".join(f"{k} {v}" for k, v in sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))) + "\n")
    if not old["found"]:
        w("old board: none found, so the columns above cannot be compared with one; check them by eye\n")
    elif not have_old:
        w(f"old board {old['path']}: COULD NOT TELL its counts: {old['why']}\n")
        could_not.append(f"old board {old['path']}: {old['why']}")
    else:
        w(f"old board: {old['path']} ({len(old['tickets'])} tickets)\n")
        for a in old["anomalies"]:
            w(f"COULD NOT TELL: {a}\n")
            could_not.append(a)
    if old.get("own"):
        w(f"the board at {', '.join(old['own'])} was written by this plugin, so it is not an old board\n")
    if old.get("others"):
        w(f"note: other board pages exist and were not compared: {', '.join(old['others'])}\n")
    w("\n-- Differences --\n")
    unknown = problems.get("unknown-state", [])
    w(f"tickets with no readable state (no row state and no Status line): {len(res['no_state'])}"
      + (": " + ", ".join(res["no_state"]) if res["no_state"] else "") + "\n")
    if res["no_state"]:
        w("COULD NOT TELL: the state of those tickets (they would show as Planned)\n")
    w(f"unknown state names: {len(res['alias_additions']) + len(res['undecided_states'])} "
      f"({res['unknown_uses']} tracker rows or status lines use them)\n")
    for word, n in res["undecided_states"]:
        w(f"  {word!r} ({n} uses): no guess; choose a standard state and add it to stateAliases\n")
    for word, target in res["alias_additions"].items():
        w(f"  {word!r} -> suggested stateAliases entry {json.dumps({word: target})} (confirm)\n")
    for p in unknown:
        w(f"  {p.line()}\n")
    for kind, title in (("file-no-row", "ticket files without rows"), ("row-no-file", "tracker rows without files")):
        ps = problems.get(kind, [])
        w(f"{title}: {len(ps)}\n")
        for p in ps:
            hint = ""
            if kind == "row-no-file":
                r = next((r for r in model.rows if r.id == p.ticket), None)
                target = next((l for l in (r.links if r else []) if os.path.exists(os.path.normpath(
                    os.path.join(root, os.path.dirname(r.source), l.split('#')[0])))), None) if r else None
                if target:
                    hint = f" (its link {target!r} exists: that file is not read as a ticket; folder not in the settings, or no ID in its name or heading)"
            w(f"  {p.line()}{hint}\n")
    links = problems.get("missing-link", [])
    w(f"broken links: {len(links)}\n")
    for p in links:
        w(f"  {p.line()}\n")
    rest = [p for p in model.problems if p.kind not in ("unknown-state", "file-no-row", "row-no-file", "missing-link")]
    w(f"other findings of dev-harness-board --check: {len(rest)}\n")
    for p in rest:
        w(f"  [{p.kind}] {p.line()}\n")
    if any(p.kind == "state-mismatch" for p in rest):
        w("  (state-mismatch: the tracker row and the ticket file disagree; that is data, so it is listed, not fixed here)\n")
    w("\nID prefixes seen (platform and kind are guesses from the prefix; edit them): "
      + (", ".join(f"{p}" for p in res["prefixes_seen"]) or "none") + "\n")
    for p, c in res["prefixes_file_only"].items():
        w(f"  {p}: only in file names ({c}), no tracker row or matching heading; not suggested\n")
    for p, c in res["prefixes_heading_only"].items():
        w(f"  {p}: only in a heading ({c}), not in the file name or a tracker row; not suggested\n")
    w(f"archive: {'on' if eff['archive'] else 'off'}; archiveTracker {eff['archiveTracker'] or '(none)'}; "
      f"archiveDirs {eff['archiveDirs']}; featureGlobs {eff['featureGlobs']}\n")
    w(f"extra tracker columns: {eff['extraColumns'] or 'none'}\n")
    w(f"integrationBranch: {eff['integrationBranch']} (from {res['branch_source']}; pass --branch to change)\n")
    twins = sum(1 for t in model.tickets if t["twinOf"])
    w(f"twin tickets: {twins} with a `Twin:` line; twinPlatforms {eff['twinPlatforms']}\n")
    w("\n-- Old board script, hooks --\n")
    if tooling["scripts"]:
        for s in tooling["scripts"]:
            w(f"board script: {s} (the switch leaves it; remove it in the same commit, see below)\n")
    else:
        w("board script: none found by name or by what the hooks call\n")
    scope = tooling["hooks_scope"]
    if tooling["hooks_path"]:
        how = ("run `git config --local --unset core.hooksPath`" if scope in ("local", "unknown")
               else f"it is set in {scope} config: remove it there")
        w(f"core.hooksPath is {tooling['hooks_path']!r} ({scope} config). install-hooks refuses it; after the switch is "
          f"committed {how}, then run `dev-harness-board install-hooks`\n")
    elif not os.path.exists(os.path.join(root, ".git")):
        w("core.hooksPath: not a git repository, so no hooks to look at\n")
    else:
        w("core.hooksPath: not set (any config scope)\n")
    for folder, kinds in tooling["folders"].items():
        w(f"hook folder {folder}: " + ", ".join(f"{n}{' (' + k.strip() + ')' if k else ''}" for n, k in kinds.items()) + "\n")
        others = [n for n, k in kinds.items() if "board" not in k and "lfs" not in k and n in githooks.GIT_HOOKS]
        if others:
            w(f"  hooks that are neither board rebuilds nor LFS (they stop running once core.hooksPath moves): {', '.join(others)}\n")
    if tooling["git_hooks"]:
        w(f"<git dir>/hooks has: {', '.join(tooling['git_hooks'])} (the board hooks run these first)\n")
    for warn in tooling["lfs_warnings"]:
        w(f"warning: {warn}\n")
    if tooling.get("hooks_path_unknown") or (tooling["common"] is None and os.path.exists(os.path.join(root, ".git"))):
        w("hooks: COULD NOT TELL (git did not answer, so core.hooksPath and the git dir's hooks are unknown)\n")
        could_not.append("git hooks state")
    return could_not


def match_check(old, model):
    """[(id, old column or None, plugin column or None)] for tickets that differ."""
    new = {t["id"]: render.column_for(t) for t in model.tickets}
    return [(tid, old["tickets"].get(tid), new.get(tid))
            for tid in sorted(set(old["tickets"]) | set(new), key=checks._sort_key)
            if old["tickets"].get(tid) != new.get(tid)]


def suggested_block(wanted):
    return json.dumps({"tickets": {k: wanted[k] for k in TICKETS_KEYS}}, indent=2, ensure_ascii=False)


def _hash_tree(root, res):
    """{relative path: sha256 or 'UNREADABLE: why'} for every .md and .markdown file under the ticket,
    archive and feature folders, and for every tracker found. Nothing is skipped silently."""
    rels = set()
    for f in res["hash_folders"]:
        for d, _, files in os.walk(os.path.join(root, f)):
            rels.update(_rel(root, os.path.join(d, n)) for n in files if n.lower().endswith((".md", ".markdown")))
    rels.update(t for t in [res["tracker"], res["eff"]["archiveTracker"], *res["tracker_candidates"]] if t)
    out = {}
    for rel in sorted(rels):
        path = os.path.join(root, rel)
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):     # a FIFO would block a read forever
                out[rel] = "UNREADABLE: not a regular file"
                continue
            with open(path, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            out[rel] = f"UNREADABLE: {e.strerror}"
    return out


# ------------------------------------------------------------------ the list

def _row_of(model, tid):
    return next((r for r in model.rows if r.id == tid), None)


def ticket_list(res, old, names, guessed):
    """Lines of the list the owner confirms: every ticket the board would show, per column, with
    its source and what its state was read from. Marks: UNKNOWN (no alias), NO STATE, a suggested
    alias, a row and file that disagree, the old board's column when it differs."""
    model = res["model"]
    old_cols = old.get("tickets") or {}
    by_col = {name: [] for name in COLUMN_NAMES}
    for t in sorted(model.tickets, key=lambda t: checks._sort_key(t["id"])):
        by_col[render.column_for(t)].append(t)
    lines = [f"== Tickets the plugin's board would show: {len(model.tickets)} ==",
             "(from the main checkout; live worktrees can later move a card to In progress or In review)"]
    for name in COLUMN_NAMES:
        lines.append(f"{name} ({len(by_col[name])})")
        for t in by_col[name]:
            rs = [x for x in model.rows if x.id == t["id"]]
            r = rs[0] if rs else None
            row = ", ".join(f"{x.source}:{x.line}" for x in rs) + (" (the first is used)" if len(rs) > 1 else "") \
                if rs else "(no row)"
            text = t["rowState"] if r else (t["fileStatus"] or "")
            src = "row" if r else "file"
            marks = []
            word = state_word(text, names)
            if r and not (text or "").strip() and t["fileState"]:
                marks.append(f"the row has no state; the file's Status ({t['fileState']}) decides")
            elif not (text or "").strip():
                marks.append(f"NO STATE: shown as {name}")
            elif (r and t["state"] is None) or (not r and t["fileState"] is None):
                marks.append(f"UNKNOWN state {state_key(text)!r}: shown as {name}")
            elif word in guessed:
                marks.append(f"{state_key(text)!r} read as {guessed[word]} by a suggested alias")
            if r and t["fileState"] and t["state"] and t["fileState"] != t["state"]:
                marks.append(f"the file says {t['fileState']}; the row wins")
            oc = old_cols.get(t["id"])
            if old_cols and oc != name:
                marks.append(f"old board: {oc or '(not on it)'}")
            lines.append(f"  {t['id']}  {t['title'] or '(no title)'}")
            files = [f.path for f in model.files if f.id == t["id"]]
            fdesc = ", ".join(files) + (" (the first is used)" if len(files) > 1 else "") if files else "(none)"
            lines.append(f"      file {fdesc}; row {row}; state from the {src}: {(text or '').strip()!r}"
                         + "".join(f"; {m}" for m in marks))
    only_old = sorted(set(old_cols) - {t["id"] for t in model.tickets}, key=checks._sort_key)
    if only_old:
        lines.append(f"On the old board but not on the plugin's ({len(only_old)})")
        lines.extend(f"  {tid}  (old board: {old_cols[tid]})" for tid in only_old)
    return lines


ROW_LINE = re.compile(r"^\s*((?:>\s*)+)?((?:[-*+]|\d+[.)])\s+)?\|")
TD = re.compile(r"<td[^>]*>\s*(.*?)\s*</td>", re.I)
TEXT_LEAD = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)]|#{1,6})\s+)?[*_`\[]*")
HEADER_WORDS = set(parse.ID_HEADERS) | {"title", "state", "status", "summary"}


def _first_cell_id(cell, any_first):
    """The ID a first cell names, or '' when it names none. With `any_first`, any non-empty cell
    that is not a header word counts (`201`, `#202`, `T-3.1`)."""
    tid = parse._cell_id(cell) if cell else ""
    if tid and LOOSE.fullmatch(tid):
        return tid
    if any_first and tid and tid.lower().strip("*_` ") not in HEADER_WORDS:
        return tid
    return ""


def _row_candidates(text, any_first=False, text_lines=False):
    """[(line number, ID, context)] for every line that looks like a ticket row: a `|` row (also
    quoted or in a list item) or an HTML `<tr>` whose first cell is an ID, and with `text_lines`
    a line that starts with an ID after an optional bullet or heading marker (`AB-2 | ...`,
    `- AB-4: ...`, `## AB-5 ...`). With `any_first`, quoted, list and HTML rows count with any
    non-empty first cell. Context: '', 'fence', 'quote', 'list', 'html' or 'text'."""
    out, fence = [], None
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        fence, is_line = parse.fence_step(line, fence)
        if is_line:
            continue
        fenced = fence is not None
        m = ROW_LINE.match(line)
        if m:
            body = line[m.end() - 1:]
            cells = parse.split_row(body)
            nxt = lines[n] if n < len(lines) else ""
            mn = ROW_LINE.match(nxt)
            header = bool(mn) and parse._is_separator(parse.split_row(nxt[mn.end() - 1:]))
            if parse._is_separator(cells) or header:
                continue
            ctx = "fence" if fenced else "quote" if m.group(1) else "list" if m.group(2) else ""
            tid = _first_cell_id(cells[0] if cells else "", any_first and ctx in ("quote", "list"))
            if tid:
                out.append((n, tid, ctx))
            continue
        td = TD.search(line)
        if td:
            tid = _first_cell_id(re.sub(r"<[^>]+>", "", td.group(1)), any_first)
            if tid:
                out.append((n, tid, "fence" if fenced else "html"))
            continue
        if text_lines and not fenced and line.strip() and not (text_lines == "no-headings" and line.lstrip().startswith("#")):
            rest = line[TEXT_LEAD.match(line).end():]
            t = ID_TOKEN.match(rest) or re.match(parse.LOOSE_ID + r"\b", rest)
            if t and (len(rest) == t.end() or not (rest[t.end()].isalnum() or rest[t.end()] == "-")):
                out.append((n, t.group(0).upper(), "text"))
    return out


def _table_rows(text):
    """(row count, first cells) of every table in the text whose header has an ID-like column."""
    lines, count, firsts = text.splitlines(), 0, []
    for i in range(len(lines) - 1):
        m, m2 = ROW_LINE.match(lines[i]), ROW_LINE.match(lines[i + 1])
        if not (m and m2) or not parse._is_separator(parse.split_row(lines[i + 1][m2.end() - 1:])):
            continue
        head = [h.lower().strip("*_` ") for h in parse.split_row(lines[i][m.end() - 1:])]
        if not any(h in parse.ID_HEADERS for h in head):
            continue
        k = i + 2
        while k < len(lines) and ROW_LINE.match(lines[k]):
            mk = ROW_LINE.match(lines[k])
            cells = parse.split_row(lines[k][mk.end() - 1:])
            if cells and cells[0]:
                count += 1
                firsts.append(cells[0])
            k += 1
    return count, firsts


def _area_files(root, folders, broad=()):
    """Every .md file below the ticket area folders (links, dot and tool folders not followed).
    Below a broad folder (the project root, `docs`) only its own files are taken; the files of
    its subfolders are returned in the second value as {subfolder: count}."""
    out, grouped = [], Counter()
    for f in folders:
        base = os.path.join(root, f)
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not os.path.islink(os.path.join(d, x)) and x not in NEVER
                             and not x.startswith("."))
            for n in sorted(files):
                p = os.path.join(d, n)
                try:
                    regular = stat.S_ISREG(os.stat(p).st_mode)
                except OSError:
                    regular = False
                if not (n.lower().endswith(".md") and regular):       # a FIFO would block a read forever
                    continue
                rel = _rel(root, p)
                if f in broad and os.path.normpath(d) != os.path.normpath(base):
                    sub = _rel(root, d).split("/")
                    top = _rel(root, base)
                    depth = 0 if top == "." else len(top.split("/"))
                    grouped["/".join(sub[:depth + 1])] += 1
                else:
                    out.append(rel)
    return list(dict.fromkeys(out)), grouped


def _non_md(root, folders):
    """Files in the ticket folders (not their subfolders) that are not .md: {folder: [names]}."""
    out = {}
    for f in folders:
        try:
            names = sorted(os.listdir(os.path.join(root, f)))
        except OSError:
            continue
        other = [n for n in names if os.path.isfile(os.path.join(root, f, n)) and not n.lower().endswith(".md")
                 and not n.startswith(".") and n.lower() not in ("board.html",)]
        if other:
            out[f] = other
    return out


NOTES_REMEDY = "; if the file is notes and no ticket or tracker, list it in tickets.notesFiles"


def _notes_files(root, patterns):
    """Regular files matched by the tickets.notesFiles globs (relative to the project root)."""
    out = set()
    for pat in patterns or []:
        for p in glob.glob(os.path.join(root, pat), recursive=True):
            try:
                if stat.S_ISREG(os.stat(p).st_mode):
                    out.add(_rel(root, p))
            except OSError:
                pass
    return out


CONTEXT = {"fence": "inside a code fence", "quote": "in a block quote", "list": "in a list item",
           "html": "an HTML table row", "": "in a table that is not the tracker table",
           "text": "on a line that is not a table row"}


def not_used(root, res):
    """([(where, why, blocks?)], [could-not-tell lines]): every row and file in the scanned area
    that looks like a ticket and is not on the board, and everything the scan could not reach.
    Whatever is uncertain inside the ticket folders blocks; outside them it is listed."""
    model, eff, sv = res["model"], res["eff"], res["survey"]
    items, cnt = [], []
    names = parse.state_names(eff["stateAliases"])
    trackers = [t for t in (res["tracker"], eff["archiveTracker"]) if t]
    area = list(dict.fromkeys(f if f not in ("", ".") else "." for f in res["hash_folders"]))
    broad = {f for f in area if f in (".", "docs")}       # the root or all of docs: subfolders are listed, not blocked
    norm = lambda d: d if d else "."

    def in_area(rel):
        d = norm(os.path.dirname(rel))
        return rel in trackers or any(d == f or (f not in broad and d.startswith(f + "/")) for f in area)
    top_level = set(area)
    notes_files = _notes_files(root, eff.get("notesFiles"))
    ticket_ids = {t["id"].upper() for t in model.tickets}
    # rows: duplicates first (the board uses the first row of an ID; trackers before the archive)
    by_id = {}
    for r in model.rows:
        by_id.setdefault(r.id, []).append(r)
    used_rows = set()
    for tid, rs in by_id.items():
        first = rs[0]
        used_rows.add(f"{first.source}:{first.line}")
        for r in rs[1:]:
            used_rows.add(f"{r.source}:{r.line}")
            same = parse.resolve_state(r.state, names) == parse.resolve_state(first.state, names)
            items.append((f"{r.source}:{r.line}", f"another row of {tid} (state {r.state.strip()!r}); the board uses "
                          f"{first.source}:{first.line} (state {first.state.strip()!r})"
                          + ("" if same else ": the states disagree"), not same))
    files_by_id = {}
    for f in model.files:
        files_by_id.setdefault(f.id, []).append(f)
    for tid, fs in files_by_id.items():
        for f in fs[1:]:
            items.append((f.path, f"another file for {tid}; the board uses {fs[0].path}", True))
    # rows the tracker parse skipped, with its reason
    for path, st in res["stats"].items():
        for where in st["skipped_tables"]:
            if not any(where == w for w, _, _ in st["idlike_tables"]):
                firsts = st["first_cells"].get(where, [])
                items.append((where, "table without ID and State columns (not a tracker table)"
                              + (f": {len(firsts)} row(s), first cells {', '.join(firsts[:12])}" if firsts else ""), False))
        for where, header, ids in st["idlike_tables"]:
            items.append((where, f"table with an ID-like column but no State column ({header}): "
                          f"{len(ids)} row(s) not read" + (f" ({', '.join(ids[:12])})" if ids else ""), True))
        for where in st["extra_tables"]:
            items.append((where, "a later table with ID and State columns: only the first table of a tracker is read", True))
        for where in st["skipped_rows"]:
            items.append((where, "row not read: " + st["reasons"].get(where, "not in the tracker table"), True))
        for where in st["fenced"]:
            items.append((where, "ticket-like row inside a code fence in a tracker: not read", True))
        if st["unclosed_fence"]:
            items.append((st["unclosed_fence"], "a code fence opened here is never closed: everything after it is not "
                          "read", True))
        used_rows.update(st["skipped_rows"])
        used_rows.update(st["fenced"])
    # every other line that looks like a ticket row, in the trackers, the ticket area and any file with a tracker-like table
    read = {t["file"] for t in model.tickets if t["file"]}
    tables = set(sv["tables"])
    area_md, grouped = _area_files(root, area, broad)
    tracker_files = [t for t in trackers if os.path.isfile(os.path.join(root, t))]
    scan = list(dict.fromkeys(tracker_files + area_md + sv["tables"]))
    for rel in scan:
        text, _ = _read_any(os.path.join(root, rel))
        if text is None or rel in read:
            continue
        is_tracker = rel in trackers
        if rel in notes_files and not is_tracker:
            continue                         # listed below, never blocks
        tablefile = rel in tables or (in_area(rel) and _table_rows(text)[0] > 0)
        lines = text.splitlines()
        allc = [(n, tid, ctx) for n, tid, ctx in _row_candidates(text, any_first=is_tracker,
                                                                  text_lines="all" if is_tracker or rel in tables else "no-headings")
                if f"{rel}:{n}" not in used_rows]
        # only a table-shaped line (it has a `|`) that is not read can block; a list or prose line is mentioned only
        prose = [c for c in allc if c[2] == "text" and "|" not in lines[c[0] - 1]]
        cands = [c for c in allc if c not in prose]
        blocks_file = is_tracker or (in_area(rel) and tablefile)
        fix = "" if is_tracker else NOTES_REMEDY
        if not is_tracker and tablefile:
            count, firsts = _table_rows(text)
            items.append((rel, f"another tracker-like table: {count} row(s) not read"
                          + (f" (first cells {', '.join(firsts[:12])}" + (", ..." if len(firsts) > 12 else "") + ")"
                             if firsts else "") + (fix if blocks_file else ""), blocks_file))
        elif not is_tracker and cands:
            ids = list(dict.fromkeys(tid for _, tid, _ in cands))
            items.append((rel, f"holds {len(cands)} ticket-like row(s) that are not read ({', '.join(ids[:12])}"
                          + (", ..." if len(ids) > 12 else "") + ")" + (fix if blocks_file else ""), blocks_file))
        for n, tid, ctx in cands:
            items.append((f"{rel}:{n}", f"row of {tid} {CONTEXT[ctx]}: not read" + (fix if blocks_file else ""), blocks_file))
        for n, tid, ctx in prose:
            if tid.upper() in ticket_ids:
                items.append((f"{rel}:{n}", f"{tid} mentioned in prose; the ticket comes from its row", False))
            else:
                cnt.append(f"{rel}:{n}: names {tid} on a line that is not a table row; not a ticket the board reads")
    for rel in sorted(notes_files):
        if rel not in read and rel not in trackers:
            items.append((rel, "listed in notesFiles (notes, not a ticket or a tracker)", False))
    # files in the ticket area that are not read as tickets
    pattern_ids = {q.rstrip("-").upper() for q in eff["idPrefixes"]}
    for folder, count in sorted(grouped.items()):
        items.append((folder, f"{count} .md file(s) in this folder are not read (add the folder to tickets.dirs if it "
                      "holds tickets)", False))
    for folder, names_ in sorted(_non_md(root, [f for f in area if f not in broad]).items()):
        cnt.append(f"{folder}: {len(names_)} file(s) that are not .md, not read: " + ", ".join(names_[:12])
                   + (", ..." if len(names_) > 12 else ""))
    for rel in area_md:
        if rel in read or rel in trackers or rel in notes_files:
            continue
        name = os.path.basename(rel)
        if name.lower() == "template.md":
            items.append((rel, "the ticket template", False))
            continue
        if norm(os.path.dirname(rel)) not in top_level:
            items.append((rel, "in a subfolder the settings do not read (add it to dirs, archiveDirs or featureGlobs, "
                          "or move it out of the ticket folder in its own commit)" + NOTES_REMEDY, True))
            continue
        text, why = _read_any(os.path.join(root, rel))
        kind, tid = ticket_evidence(rel, text or "")
        if text is None:
            continue                         # listed below as unreadable
        status = bool(re.search(r"^(?:##\s+Status\s*|status\s*:.*)$", text, re.M | re.I))
        id_table = _table_rows(text)[0] > 0 or bool(_row_candidates(text))
        if kind and not parse.split_id(tid)[1]:
            items.append((rel, f"looks like ticket {tid}, which is not an ID the board reads" + NOTES_REMEDY, True))
        elif kind and parse.split_id(tid)[0].upper() not in pattern_ids:
            items.append((rel, f"looks like ticket {tid}, but its prefix {parse.split_id(tid)[0]} is not in idPrefixes" + NOTES_REMEDY,
                          True))
        elif kind:
            items.append((rel, f"looks like ticket {tid} (its name, heading or id line) but is not read" + NOTES_REMEDY, True))
        elif status or id_table:
            items.append((rel, "has " + " and ".join(x for x, y in (("a Status section", status),
                                                                   ("a table of ticket IDs", id_table)) if y)
                          + " but no ticket ID the board reads" + NOTES_REMEDY, True))
        else:
            items.append((rel, "no ticket ID in its file name or heading", False))
    # outside the ticket area
    for rel in sv["ticket_files"]:
        if rel not in read and not in_area(rel) and rel not in notes_files:
            items.append((rel, "a ticket file outside the folders the settings read (dirs, archiveDirs, featureGlobs)"
                          + NOTES_REMEDY, True))
    for rel in sv["maybe"]:
        if rel not in read and not in_area(rel):
            cnt.append(f"{rel}: its name, first heading or id line names {sv['ids'].get(rel, '?')}, outside the ticket "
                       "folders; is it a ticket?")
    for rel in sv["fixtures"]:
        what = f"looks like ticket {sv['ids'][rel]}" if rel in sv["ids"] else "has a tracker-like table"
        cnt.append(f"{rel}: {what}, in a test or example folder; taken as test data")
    for area_rel, hits in sv["hidden"]:
        if hits:
            cnt.append(f"{area_rel}: not read (a dot folder or deeper than {SURVEY_DEPTH} levels); "
                       f"{len(hits)} file(s) named like tickets: " + ", ".join(hits[:10]) + (", ..." if len(hits) > 10 else ""))
    hidden_empty = [a for a, h in sv["hidden"] if not h]
    if hidden_empty:
        cnt.append(f"{len(hidden_empty)} dot or deep folder(s) not read in full; no file named like a ticket in them")
    tools = {k: v for k, v in sv["not_searched"].items() if k != ".git"}
    if tools:
        cnt.append("tool folders not searched: " + ", ".join(f"{k} ({v})" for k, v in sorted(tools.items())))
    for link in sv["symlinks"]:
        cnt.append(f"{link}: a linked folder, not followed")
    for f in res["uncovered"]:
        if f not in ("", "."):
            items.append((f, "folder not read by the settings", False))
    for rel, why in sv["unreadable"]:
        items.append((rel, f"cannot be read ({why})", True))
    for p in res["cannot_read"]:
        items.append((p.message.split(": ", 1)[0], "ticket file cannot be read: " + p.message.split(": ", 1)[-1], True))
    for u in res["unreadable"] + sv["folder_errors"]:
        items.append((u.split(": ", 1)[0], "folder cannot be read: " + u.split(": ", 1)[-1], True))
    for x in res["unconsidered"] + sv["odd"]:
        items.append((x, "not read: only regular .md files are (a .markdown file, broken link, FIFO or other odd file)", False))
    seen, out = {}, []
    for where, why, blocks in items:
        key = (where, why)
        if key in seen:
            continue
        seen[key] = True
        out.append((where, why, blocks))
    return out, cnt


def list_hash(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8") + b"\0")
    return h.hexdigest()[:12]


def retire_text(tooling):
    """What the owner does to retire the old system. The command never deletes anything itself."""
    lines = ["== Retiring the old board (you do this; init never deletes anything) =="]
    scripts = [s.split(" (called by", 1)[0] for s in tooling["scripts"]]
    if scripts or tooling["folders"] or tooling["hooks_path"]:
        lines.append("In the same commit as the switch, remove the old board script(s) and the old board hooks:")
        for s in dict.fromkeys(scripts):
            lines.append(f"  - remove {s}")
        for folder, kinds in tooling["folders"].items():
            board = [n for n, k in kinds.items() if "board" in k]
            if board:
                lines.append(f"  - remove the board rebuild from {folder}: {', '.join(board)}")
        lines.append("Keep Git LFS working: run `git lfs install` before core.hooksPath moves, so <git dir>/hooks has the "
                     "LFS hooks the board hooks chain to; keep any LFS-only hook file the project still needs.")
        if tooling["hooks_path"]:
            lines.append(f"Then unset core.hooksPath ({tooling['hooks_path']!r}, {tooling['hooks_scope']} config) and, if "
                         "the owner wants it, run `dev-harness-board install-hooks` (the guard asks).")
    else:
        lines.append("No old board script or hook folder was found: nothing to retire.")
    return lines


def run(root, cfg, args, out, err, step_runner, plan_steps):
    """The adopt flow. `plan_steps(root, wanted, eff, notes)` -> (steps, blocker) and
    `step_runner(root, steps, out, err)` come from init.py (they write harness.json and CLAUDE.md)."""
    import io
    from . import init as initmod
    path = os.path.join(root, initmod.CONFIG_REL)
    given, invalid, readable = {}, [], True
    if os.path.lexists(path):
        gv, raw, errs = _hc()._load_file(path)
        if raw is None:
            readable = False
        elif isinstance(raw.get("tickets"), dict):
            given = gv.get("tickets", {})
            invalid = [k for k in raw["tickets"] if k in TICKETS_KEYS and k not in given]
    res = analyse(root, cfg, args, given, invalid, None)
    if not readable:
        res["blockers"].append(f"{initmod.CONFIG_REL} cannot be read as JSON; the settings cannot be merged into it")
    old = read_old_board(root, res["eff"])
    tooling = find_tooling(root)
    model, eff = res["model"], res["eff"]
    dry = args.check or args.dry_run
    details = io.StringIO()
    could_not = report(root, res, old, tooling, details)
    names = parse.state_names(eff["stateAliases"])
    guessed = {w.lower(): t for w, t in res["alias_additions"].items() if "stateAliases" not in given}
    listing = ticket_list(res, old, names, guessed)
    unused, cnt_more = not_used(root, res)
    could_not.extend(cnt_more)
    for tid in res["no_state"]:
        t = next(x for x in model.tickets if x["id"] == tid)
        could_not.append(f"{tid}: no state in its row or Status line (shown as {render.column_for(t)})")
    if guessed:
        could_not.append("suggested state aliases are guesses (" + ", ".join(
            f"{w!r} -> {t}" for w, t in res["alias_additions"].items()) + "): each ticket that uses one is marked in the list")
    # hard blockers: no confirmation lifts them
    blockers = list(res["blockers"])
    if res["no_state"]:
        blockers.append(f"{len(res['no_state'])} ticket(s) have no readable state: {', '.join(res['no_state'][:8])}")
    bad = [p for p in model.problems if p.kind in ("tracker-missing", "tracker-malformed")]
    if bad:
        blockers.append("the tracker could not be read as a table with ID and State columns: " + "; ".join(p.message for p in bad))
    blank = [t["id"] for t in model.tickets if t["rowState"] is not None and not t["rowState"].strip()
             and t["id"] not in res["no_state"]]
    if blank:
        blockers.append(f"tracker row(s) with an empty State cell: {', '.join(blank[:8])}; fill in the State cell of each "
                        "row (an alias cannot name an empty state)")
    unknown = [p for p in model.problems if p.kind == "unknown-state"
               and not (p.ticket in blank or p.ticket in res["no_state"])]
    if unknown:
        blockers.append(f"{len(unknown)} state name(s) unknown to the settings (no alias): the board would show them as "
                        "Planned; add each to tickets.stateAliases in harness.json")
    marked = [w for w, _, b in unused if b]
    if marked:
        blockers.append(f"{len(marked)} item(s) under Not used are marked [BLOCKS] (" + ", ".join(marked[:6])
                        + (", ..." if len(marked) > 6 else "") + "); each says why")
    notes = []
    steps, config_blocker = plan_steps(root, res["wanted"], eff, notes)
    if config_blocker:
        blockers.append(config_blocker)
    board = os.path.join(root, eff["boardPath"])
    writes = [f"{verb} {rel}" + (f" ({detail})" if detail else "") for verb, rel, _, detail in steps]
    writes.append(f"{'replace the existing file' if os.path.lexists(board) else 'create'} {eff['boardPath']} (the board)")
    block = suggested_block(res["wanted"])
    unused_lines = [f"== Not used: {len(unused)} =="] + [f"  {w}: {why}" + (" [BLOCKS]" if b else "") for w, why, b in unused]
    cnt_lines = [f"== Could not tell: {len(could_not)} =="] + [f"  {c}" for c in dict.fromkeys(could_not)]
    digest = list_hash("\n".join(listing), "\n".join(unused_lines), "\n".join(cnt_lines), block, "\n".join(writes))

    w = out.write
    w(f"dev-harness-board init: adopt mode ({'dry run, nothing is written' if dry else 'switch'})\nproject: {root}\n\n")
    w("\n".join(listing) + "\n\n")
    w("\n".join(unused_lines) + "\n\n")
    w("\n".join(cnt_lines) + "\n\n")
    w("== Match against the old board (information: the owner confirms the list) ==\n")
    if old.get("tickets"):
        diffs = match_check(old, model)
        w(f"tickets compared: {len(set(old['tickets']) | {t['id'] for t in model.tickets})}; in another column or "
          f"missing: {len(diffs)}\n")
        for tid, o, n in diffs:
            w(f"  DIFFERENT {tid}: old board {o or '(not on the board)'}, plugin board {n or '(not on the board)'}\n")
    elif old["found"]:
        w(f"the old board {old['path']} could not be compared ({old['why']})\n")
    else:
        w("no old board found: nothing to compare with\n")
    w("\n== How the list and the settings were found ==\n" + details.getvalue())
    w("\n== Suggested settings (complete `tickets` block for .claude/harness.json) ==\n" + block + "\n")
    w("\n" + "\n".join(retire_text(tooling)) + "\n")
    w("\n== Result ==\n")
    flags = (f" --branch {eff['integrationBranch']}" if args.branch else "") + "".join(
        f" --prefix {p}:{v['platform']}:{v['kind']}" for p, v in args.prefix_specs)
    w(f"list hash: {digest}\n")
    tense = "would " if dry or blockers or args.confirm != digest else "will "
    w("the switch writes only these files (one commit); no ticket, tracker or template file is touched:\n")
    for line in writes:
        w(f"  {tense}{line}\n")
    for n in notes:
        w(n.rstrip("\n") + "\n")
    def end(code):
        w(f"== end of output: {len(model.tickets)} tickets, {len(unused)} not used ({len(marked)} blocking), "
          f"{len(dict.fromkeys(could_not))} could not tell; list hash {digest} ==\n")
        return code

    if blockers:
        w("the switch is BLOCKED (confirming the list does not lift these; fix them first):\n"
          + "".join(f"  - {b}\n" for b in blockers))
        if dry:
            w("dry run: nothing written\n")
        return end(1)
    if dry or not args.confirm:
        w("Show the owner the whole list above (tickets per column, not used, could not tell) and ask. Only after the "
          f"owner confirms this exact list, run: dev-harness-board init --confirm {digest}{flags}\n")
        w("dry run: nothing written\n" if dry else "not confirmed: nothing written\n")
        return end(0 if dry else 1)
    if args.confirm != digest:
        w(f"the list changed since it was confirmed (confirmed {args.confirm}, now {digest}): nothing written. "
          "Show the owner the new list above and ask again.\n")
        return end(1)
    before = _hash_tree(root, res)
    code = step_runner(root, steps, out, err)
    if code:
        return end(code)
    from . import cli
    board_code = cli.main(["--root", root], out, err)
    after = _hash_tree(root, res)
    changed = [k for k in sorted(set(before) | set(after)) if after.get(k) != before.get(k)]
    if changed:
        err.write("dev-harness-board init: ERROR: these tracker/ticket files changed during the switch: "
                  + ", ".join(changed) + "\n")
        return end(1)
    if board_code:
        written = [rel for _, rel, _, _ in steps]
        w(f"the board could not be written (exit {board_code}; see the error above). Already written: "
          f"{', '.join(written) or 'nothing'}. Fix the cause and run `dev-harness-board` to build the board, or "
          "revert those files.\n")
        return end(board_code)
    w(f"verified: {len(before)} markdown files under the ticket and archive folders (and every tracker found) "
      "have the same sha256 before and after:\n")
    for rel in before:
        w(f"  {rel}" + (f"  ({before[rel]})" if before[rel].startswith("UNREADABLE") else "") + "\n")
    w("Next: commit these files as one commit, together with the removal of the old board script and hooks "
      "(see \"Retiring the old board\" above). init never installs hooks and never deletes files.\n")
    return end(0)
