"""Consistency checks over a parsed Model."""
from collections import defaultdict
import os
from urllib.parse import unquote

from .model import Problem
from .parse import prefix_info, resolve_state, split_id, state_names


def merge(model, root, cfg):
    """Fill model.tickets: one entry per ID with row and file fields side by side."""
    names = state_names(cfg["stateAliases"])
    files = defaultdict(list)
    for f in model.files:
        files[f.id].append(f)
    rows = defaultdict(list)
    for r in model.rows:
        rows[r.id].append(r)
    out = []
    for tid in sorted(set(files) | set(rows), key=_sort_key):
        row = rows[tid][0] if rows[tid] else None
        tf = files[tid][0] if files[tid] else None
        platform, kind = prefix_info(tid, cfg["idPrefixes"])
        labels = list(tf.labels) if tf else []
        out.append({
            "id": tid,
            "title": (tf.title if tf and tf.title else row.title if row else ""),
            "platform": platform,
            "kind": kind or (labels[0] if labels else ""),
            "labels": labels,
            "twinOf": list(tf.twins) if tf else [],
            "single": bool(tf.single) if tf else False,
            "state": resolve_state(row.state, names) if row else None,
            "rowState": row.state if row else None,
            "fileState": resolve_state(tf.status, names) if tf else None,
            "fileStatus": tf.status if tf else None,
            "review": row.review if row else "",
            "commit": row.commit if row else "",
            "note": row.note if row else "",
            "extra": dict(row.extra) if row else {},
            "link": row.link if row else None,
            "file": tf.path if tf else None,
            "archived": bool(row.archived if row else tf.archived),
        })
    model.tickets = out


def _sort_key(tid):
    prefix, num = split_id(tid)
    return (prefix, int(num) if num.isdigit() else 0, tid)


def check(model, root, cfg):
    """Append consistency problems to model.problems."""
    names = state_names(cfg["stateAliases"])
    add = model.problems.append
    rows, files = defaultdict(list), defaultdict(list)
    for r in model.rows:
        rows[r.id].append(r)
    for f in model.files:
        files[f.id].append(f)
    for tid, rs in rows.items():
        if len(rs) > 1:
            add(Problem("duplicate-id", tid, f"appears in {len(rs)} tracker rows"))
    broken = {p.ticket for p in model.problems if p.kind == "row-malformed" and p.ticket}
    for tid, fs in files.items():
        if len(fs) > 1:
            add(Problem("duplicate-id", tid, "has several files: " + ", ".join(f.path for f in fs)))
    tracker_dir = os.path.join(root, os.path.dirname(cfg["trackerPath"]))
    for tid in sorted(set(rows) | set(files), key=_sort_key):
        rs, fs = rows.get(tid, []), files.get(tid, [])
        row_state = resolve_state(rs[0].state, names) if rs else None
        # Old closed history (a Done row, or any row in the archive tracker) needs no ticket file.
        if rs and not fs and not (row_state == "Done" or rs[0].archived):
            add(Problem("row-no-file", tid, "tracker row has no ticket file"))
        if fs and not rs and tid not in broken:     # a malformed row already says why
            add(Problem("file-no-row", tid, f"ticket file {fs[0].path} has no tracker row"))
        if rs:
            if row_state is None:
                add(Problem("unknown-state", tid, f"tracker row state {rs[0].state!r} is not a known state"))
        file_state = None
        if fs:
            file_state = resolve_state(fs[0].status, names)
            if file_state is None and fs[0].status:
                add(Problem("unknown-state", tid, f"Status line {fs[0].status[:40]!r} in {fs[0].path} "
                                                   "does not start with a known state"))
        if row_state and file_state and row_state != file_state:
            add(Problem("state-mismatch", tid, f"tracker says {row_state} but {fs[0].path} says {file_state}"))
        for r in rs:
            base = os.path.join(root, os.path.dirname(r.source))
            for link in r.links:
                target = unquote(link.split("#", 1)[0])
                if target and not _is_url(link) and not os.path.exists(os.path.normpath(os.path.join(base, target))):
                    add(Problem("missing-link", tid, f"link {link!r} in {r.source} points to a missing file"))
    for tid, message in twin_map(model.tickets, cfg)[1]:
        add(Problem("twin-missing", tid, message))


def _is_url(link):
    return "://" in link or link.startswith("mailto:")


def twin_map(tickets, cfg):
    """(pairs, missing). pairs: id -> IDs of its twins; missing: (id, message) per problem.
    A `Twin: <ID>` line pairs two tickets whatever their numbers; a `Twin: none` line needs
    no twin. A ticket with neither is paired by prefix kind and number, as before."""
    plats = cfg["twinPlatforms"]
    if not plats:
        return {}, []
    by_id = {t["id"]: t for t in tickets}
    pairs, missing = {}, []
    link = lambda a, b: pairs.setdefault(a, []).append(b) if b not in pairs.get(a, []) else None
    for t in tickets:
        for other in t.get("twinOf", []):
            if other in by_id and other != t["id"]:
                link(t["id"], other)
                link(other, t["id"])
            elif t["platform"] in plats and other not in by_id:
                missing.append((t["id"], f"Twin line names {other}, which has no ticket file or row"))
    groups = defaultdict(dict)
    for t in tickets:
        if t["platform"] in plats and not t.get("twinOf") and not t.get("single") and t["id"] not in pairs:
            _, kind = prefix_info(t["id"], cfg["idPrefixes"])
            groups[(kind, split_id(t["id"])[1])][t["platform"]] = t["id"]
    for (kind, num), sides in sorted(groups.items()):
        for tid in sides.values():
            pairs[tid] = [o for o in sides.values() if o != tid]
        absent = [p for p in plats if p not in sides]
        if absent:
            missing.append((next(iter(sides.values())),
                            f"twin ticket missing: no {', '.join(absent)} ticket for {kind} number {num}"))
    return pairs, missing
