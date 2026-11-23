"""Ticket state helpers used by the commands (/start-ticket, /review, /close-ticket).

    dev-harness-board set-state <ID> <State> [--commit SHA] [--root DIR]
    dev-harness-board archive <ID> [--root DIR]
    dev-harness-board twin <ID> [--root DIR]

They work on the checkout they run in (the root is found from the current folder or --root;
a linked worktree is NOT redirected to the main checkout, so a worktree's own files change).

set-state  sets the State cell of the ID's tracker row (and the Commit cell with --commit) and
           the first word(s) of the ticket file's Status line, in one step. Everything else in
           both files is kept byte for byte (CRLF, a BOM, column padding, links, the rest of the
           Status line). Both edits are computed first: if either cannot be made nothing is
           written. Unknown states are refused (exit 1); a state already set is "unchanged".
archive    (tickets.archive on, archiveTracker and archiveDirs set) moves the row to the end of
           the archive tracker's table and the ticket file into archiveDirs[0], then fixes the
           relative links to it in the trackers and ticket files, and the moved file's own links.
twin       (tickets.twinPlatforms on) prints whether the ticket's twin on the other platform is
           open. Read-only; it never changes a ticket.

Exit 0 ok, 1 refused (nothing written), 2 bad usage or unreadable config.
"""
import argparse
import os
import re
import sys
import tempfile

from . import parse
from .model import STATES
from .parse import _cell_id, fence_step, split_row

PIPE = re.compile(r"(?<!\\)\|")
COMMIT_RE = re.compile(r"^[0-9A-Za-z]{4,64}$")
MD_LINK = re.compile(r"(\]\()([^)\s#]+)((?:#[^)]*)?\))")


class Refused(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def _write(path, text):
    """Replace `path` atomically, keeping its mode."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def check_target(path, root=None):
    """Refuse a file that must not be replaced: a symlink, a non-regular or read-only file, or a
    folder that is not writable (the atomic replace needs the folder)."""
    rel = os.path.relpath(path, root) if root else path
    if os.path.islink(path):
        raise Refused(f"{rel} is a symlink; not touched")
    if not os.path.isfile(path):
        raise Refused(f"{rel} is not a regular file")
    if not os.access(path, os.W_OK):
        raise Refused(f"{rel} is not writable")
    if not os.access(os.path.dirname(path) or ".", os.W_OK | os.X_OK):
        raise Refused(f"the folder of {rel} is not writable")


def apply_writes(ops, root, creates=()):
    """ops: [(path, old text, new text)]. Writes them in order; if one fails, restores the ones
    already written (and removes `creates`) and raises Refused saying so."""
    done = []
    try:
        for path, old, new in ops:
            _write(path, new)
            done.append((path, old))
    except OSError as e:
        failed = []
        for path, old in reversed(done):
            try:
                _write(path, old)
            except OSError:
                failed.append(os.path.relpath(path, root))
        for c in creates:
            try:
                os.unlink(c)
            except OSError:
                pass
        msg = f"write failed ({e}); earlier writes were restored"
        if failed:
            msg = f"write failed ({e}); could NOT restore: {', '.join(failed)}"
        raise Refused(msg)


def _split(text):
    return text.split("\n")


def _cells(body):
    """[(start, end)] of each cell's inner text (between its pipes) in a table row."""
    pos = [m.start() for m in PIPE.finditer(body)]
    if not body.lstrip().startswith("|"):
        pos.insert(0, -1)
    s = body.rstrip()
    if not (s.endswith("|") and not s.endswith("\\|")):
        pos.append(len(body))
    elif pos and pos[-1] < len(s) - 1:
        pos.append(len(body))
    return [(pos[i] + 1, pos[i + 1]) for i in range(len(pos) - 1)]


def _set_cell(body, index, value):
    a, b = _cells(body)[index]
    raw = body[a:b]
    if raw.strip():
        lead = raw[:len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
    else:
        lead, trail = " ", " "
    new = lead + value
    if len(new) + min(len(trail), 1) <= len(raw):
        new = new.ljust(len(raw))            # keep the column width when the new text fits
    else:
        new += trail[:1]
    return body[:a] + new + body[b:]


def _find_table(lines):
    """(header index, header cells) of the first table with ID and State columns, outside code
    fences (the same table parse.parse_tracker reads)."""
    fence = None
    for i, line in enumerate(lines):
        fence, is_line = fence_step(line.rstrip("\r"), fence)
        if is_line:
            continue
        if fence is not None or not line.lstrip().startswith("|"):
            continue
        header = split_row(line.rstrip("\r"))
        lower = [h.lower() for h in header]
        if "id" in lower and "state" in lower:
            return i, header
    return None, None


def _find_row(lines, ticket_id):
    """(header cells, row line index, last table line index) or Nones."""
    h, header = _find_table(lines)
    if h is None:
        return None, None, None
    last, found = h + 1, None
    for n in range(h + 2, len(lines)):
        body = lines[n].rstrip("\r")
        if not body.lstrip().startswith("|"):
            break
        last = n
        cells = split_row(body)
        if len(cells) != len(header):
            continue
        raw = cells[[c.lower() for c in header].index("id")]
        if _cell_id(raw) == ticket_id and found is None:      # the parser's rule: `AB-1 :warning:` is AB-1
            found = n
    return header, found, last


def edit_row(text, ticket_id, state, commit):
    """Text with the row's State (and Commit) cells set, or None if the file has no such row."""
    lines = _split(text)
    header, n, _ = _find_row(lines, ticket_id)
    if n is None:
        return None
    lower = [h.lower() for h in header]
    body = lines[n]
    cr = "\r" if body.endswith("\r") else ""
    body = body.rstrip("\r")
    body = _set_cell(body, lower.index("state"), state)
    if commit is not None:
        if "commit" not in lower:
            raise Refused("the tracker has no Commit column")
        body = _set_cell(body, lower.index("commit"), commit)
    lines[n] = body + cr
    return "\n".join(lines)


def _lead_span(text, names):
    """(start, end) of the state words that start `text`, or None."""
    i = 0
    while i < len(text) and text[i] in "*_`~ ":
        i += 1
    low = text[i:].lower()
    for name in sorted(names, key=len, reverse=True):
        if low.startswith(name) and not (len(low) > len(name) and low[len(name)].isalnum()):
            return i, i + len(name)
    return None


def edit_status(text, state, names):
    """Text with the Status line's leading state set, or None if there is no Status line."""
    lines = _split(text)
    for i, l in enumerate(lines):
        if re.match(r"##\s+Status\s*$", l.strip(), re.I):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("#"):
                    return None
                body = lines[j].rstrip("\r")
                if body.strip():
                    cr = "\r" if lines[j].endswith("\r") else ""
                    span = _lead_span(body, names)
                    if not span:
                        raise Refused(f"the Status line {body.strip()[:50]!r} does not start with a known "
                                      "state; write it by hand (for example 'planned (date)') and run this again")
                    word = state.lower() if body[span[0]].islower() else state
                    body = body[:span[0]] + word + body[span[1]:]
                    lines[j] = body + cr
                    return "\n".join(lines)
            return None
    return None


def standard_state(name):
    for s in STATES:
        if s.lower() == name.strip().lower():
            return s
    raise Refused(f"unknown state {name!r}; use one of: {', '.join(STATES)}")


def _locate(root, cfg, ticket_id):
    """(tracker path, ticket file path) for the ID, either live or archived."""
    problems = []
    model = parse.build(root, cfg)
    files = [f for f in model.files if f.id == ticket_id]
    if not files:
        raise Refused(f"{ticket_id}: no ticket file found")
    if len(files) > 1:
        raise Refused(f"{ticket_id}: several ticket files: " + ", ".join(f.path for f in files))
    rows = [r for r in model.rows if r.id == ticket_id]
    if not rows:
        raise Refused(f"{ticket_id}: no tracker row found")
    if len(rows) > 1:
        raise Refused(f"{ticket_id}: appears in {len(rows)} tracker rows")
    del problems
    return os.path.join(root, rows[0].source), os.path.join(root, files[0].path)


def set_state(root, cfg, ticket_id, state, commit=None):
    """Returns the lines to print. Raises Refused before writing anything."""
    state = standard_state(state)
    if commit is not None and not COMMIT_RE.match(commit):
        raise Refused(f"--commit {commit!r} is not a commit id")
    names = parse.state_names(cfg["stateAliases"])
    tracker, ticket = _locate(root, cfg, ticket_id)
    check_target(tracker, root)
    check_target(ticket, root)
    old_tracker, old_ticket = _read(tracker), _read(ticket)
    new_tracker = edit_row(old_tracker, ticket_id, state, commit)
    new_ticket = edit_status(old_ticket, state, names)
    if new_tracker is None:
        raise Refused(f"{ticket_id}: no row in {os.path.relpath(tracker, root)}")
    if new_ticket is None:
        raise Refused(f"{ticket_id}: {os.path.relpath(ticket, root)} has no Status line")
    ops, out = [], []
    for path, old, new in ((tracker, old_tracker, new_tracker), (ticket, old_ticket, new_ticket)):
        rel = os.path.relpath(path, root)
        if new == old:
            out.append(f"unchanged {rel}")
        else:
            ops.append((path, old, new))
            out.append(f"updated {rel}")
    apply_writes(ops, root)
    return out


def _is_url(t):
    return "://" in t or t.startswith("mailto:") or os.path.isabs(t)


def _relink(text, here, old_dir, mapping):
    """Rewrite relative links in `text` (a file living in `here`, whose links resolve against
    `old_dir`): a target in `mapping` moves, an existing other target is kept pointing at it."""
    def fix(m):
        target = m.group(2)
        if _is_url(target):
            return m.group(0)
        resolved = os.path.normpath(os.path.join(old_dir, target))
        resolved = mapping.get(resolved, resolved if os.path.exists(resolved) else None)
        if resolved is None:
            return m.group(0)
        new = os.path.relpath(resolved, here).replace(os.sep, "/")
        return m.group(0) if new == target else m.group(1) + new + m.group(3)
    return MD_LINK.sub(fix, text)


def archive(root, cfg, ticket_id):
    if not cfg["archive"]:
        raise Refused("tickets.archive is off")
    if not cfg["archiveTracker"] or not cfg["archiveDirs"]:
        raise Refused("tickets.archiveTracker and tickets.archiveDirs must both be set")
    tracker, ticket = _locate(root, cfg, ticket_id)
    live = os.path.join(root, cfg["trackerPath"])
    target_tracker = os.path.join(root, cfg["archiveTracker"])
    if os.path.realpath(tracker) != os.path.realpath(live):
        raise Refused(f"{ticket_id} is already archived")
    if not os.path.exists(target_tracker):
        raise Refused(f"{cfg['archiveTracker']}: archive tracker not found")
    new_ticket = os.path.join(root, cfg["archiveDirs"][0], os.path.basename(ticket))
    if os.path.exists(new_ticket):
        raise Refused(f"{os.path.relpath(new_ticket, root)} already exists")
    names = parse.state_names(cfg["stateAliases"])
    row_obj = next(r for r in parse.build(root, cfg).rows if r.id == ticket_id)
    if parse.resolve_state(row_obj.state, names) != "Done":
        raise Refused(f"{ticket_id} is not Done (row says {row_obj.state!r}); only Done tickets are archived")
    check_target(tracker, root)
    check_target(target_tracker, root)
    check_target(ticket, root)
    new_dir = os.path.dirname(new_ticket)
    probe = new_dir
    while not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    if not os.access(probe, os.W_OK | os.X_OK):
        raise Refused(f"{os.path.relpath(probe, root)} is not writable")
    if not os.access(os.path.dirname(ticket), os.W_OK | os.X_OK):
        raise Refused(f"the folder of {os.path.relpath(ticket, root)} is not writable")
    src_text, dst_text = _read(tracker), _read(target_tracker)
    src_lines, dst_lines = _split(src_text), _split(dst_text)
    _, n, _ = _find_row(src_lines, ticket_id)
    dheader, _, dlast = _find_row(dst_lines, ticket_id)
    if n is None:
        raise Refused(f"{ticket_id}: no row in {cfg['trackerPath']}")
    if dheader is None:
        raise Refused(f"{cfg['archiveTracker']}: no table with ID and State columns")
    row = src_lines[n].rstrip("\r")
    mapping = {os.path.normpath(ticket): os.path.normpath(new_ticket)}
    row = _relink(row, os.path.dirname(target_tracker), os.path.dirname(tracker), mapping)
    cr = "\r" if "\r\n" in dst_text else ""
    del src_lines[n]
    if dlast == len(dst_lines) - 1:               # the table ends the file with no final newline
        dst_lines[dlast] = dst_lines[dlast].rstrip("\r") + cr
        dst_lines.insert(dlast + 1, row)
    else:
        dst_lines.insert(dlast + 1, row + cr)
    ticket_text = _read(ticket)
    new_ticket_text = _relink(ticket_text, new_dir, os.path.dirname(ticket), mapping)
    ops = [(tracker, src_text, "\n".join(src_lines)), (target_tracker, dst_text, "\n".join(dst_lines))]
    others = set()
    for d in list(cfg["dirs"]) + list(cfg["archiveDirs"]):
        folder = os.path.join(root, d)
        if os.path.isdir(folder):
            others.update(os.path.normpath(os.path.join(folder, f)) for f in os.listdir(folder)
                          if f.lower().endswith(".md"))
    others -= {os.path.normpath(p) for p in (tracker, target_tracker, ticket, new_ticket)}
    for path in sorted(others):
        try:
            text = _read(path)
        except (OSError, ValueError):
            continue
        new = _relink(text, os.path.dirname(path), os.path.dirname(path), mapping)
        if new != text:
            check_target(path, root)
            ops.append((path, text, new))
    os.makedirs(new_dir, exist_ok=True)
    try:
        fd = os.open(new_ticket, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(new_ticket_text.encode("utf-8"))
        os.chmod(new_ticket, os.stat(ticket).st_mode & 0o7777)
    except OSError as e:
        raise Refused(f"cannot create {os.path.relpath(new_ticket, root)}: {e}")
    try:
        apply_writes(ops, root, creates=[new_ticket])
    except Refused:
        raise
    old_rel, new_rel = os.path.relpath(ticket, root), os.path.relpath(new_ticket, root)
    try:
        os.unlink(ticket)
    except OSError as e:
        raise Refused(f"partly archived: the row and links are moved and {new_rel} exists, but "
                      f"{old_rel} could not be removed ({e}); remove it by hand")
    return [f"moved {old_rel} -> {new_rel}", f"moved the row to {cfg['archiveTracker']}"]


def twin(root, cfg, ticket_id):
    from . import cli
    plats = cfg["twinPlatforms"]
    if not plats:
        return ["twinPlatforms is off: no twin check"]
    model = cli.build_model(root, cfg, live=True)
    mine = next((t for t in model.tickets if t["id"] == ticket_id), None)
    if mine is None:
        raise Refused(f"{ticket_id}: no such ticket")
    platform, kind = parse.prefix_info(ticket_id, cfg["idPrefixes"])
    num = ticket_id.rpartition("-")[2]
    twins = [t for t in model.tickets if t["id"] != ticket_id and t["kind"] == kind and kind
             and t["platform"] in plats and t["platform"] != platform and t["id"].rpartition("-")[2] == num]
    if platform not in plats or not twins:
        return [f"{ticket_id}: no twin ticket (platform {platform or '?'}, twinPlatforms {', '.join(plats)})"]
    out = []
    for t in twins:
        state = t["state"] or t["fileState"] or "unknown"
        out.append(f"{ticket_id}: twin {t['id']} ({t['platform']}) is {state}: "
                   + ("closed" if state == "Done" else "OPEN; do not close it from here"))
    return out


def _run(argv, out, err, name, need_state):
    ap = _Parser(prog=f"dev-harness-board {name}", add_help=False)
    ap.add_argument("ticket")
    if need_state:
        ap.add_argument("state")
        ap.add_argument("--commit")
    ap.add_argument("--root")
    try:
        a = ap.parse_args(argv)
    except ValueError as e:
        err.write(f"dev-harness-board {name}: {e}\n{USAGE}")
        return 2
    from . import cli
    root = os.path.abspath(a.root) if a.root else cli.find_root(os.getcwd())
    if not os.path.isdir(root):
        err.write(f"dev-harness-board: --root {root} is not a directory\n")
        return 2
    cfg, errors = cli.load_tickets_config(root)
    err.write(cli.harness_config.format_errors(errors))
    if any(f in e for e in errors for f in cli.FATAL_CONFIG):
        return 2
    tid = a.ticket.upper()
    try:
        if name == "set-state":
            lines = set_state(root, cfg, tid, a.state, a.commit)
        elif name == "archive":
            lines = archive(root, cfg, tid)
        else:
            lines = twin(root, cfg, tid)
    except Refused as e:
        err.write(f"dev-harness-board {name}: {e}\n")
        return 1
    except OSError as e:
        err.write(f"dev-harness-board {name}: {e}\n")
        return 1
    for l in lines:
        out.write(l + "\n")
    return 0


USAGE = ("usage: dev-harness-board set-state ID STATE [--commit SHA] [--root DIR]\n"
         "       dev-harness-board archive ID [--root DIR]\n"
         "       dev-harness-board twin ID [--root DIR]\n")


def run_set_state(argv, out, err):
    return _run(argv, out, err, "set-state", True)


def run_archive(argv, out, err):
    return _run(argv, out, err, "archive", False)


def run_twin(argv, out, err):
    return _run(argv, out, err, "twin", False)
