"""Consistency checks over a parsed Model."""
from collections import Counter, defaultdict
import os
import re

from .model import Problem
from .parse import prefix_info, resolve_state, state_names


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
    m = re.fullmatch(r"(.*?)-?(\d+)", tid)      # "AB-12" and "L12" both sort by prefix, then number
    return (m.group(1), int(m.group(2)), tid) if m else (tid, 0, tid)


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
    for tid, fs in files.items():
        if len(fs) > 1:
            add(Problem("duplicate-id", tid, "has several files: " + ", ".join(f.path for f in fs)))
    tracker_dir = os.path.join(root, os.path.dirname(cfg["trackerPath"]))
    for tid in sorted(set(rows) | set(files), key=_sort_key):
        rs, fs = rows.get(tid, []), files.get(tid, [])
        if rs and not fs:
            add(Problem("row-no-file", tid, "tracker row has no ticket file"))
        if fs and not rs:
            add(Problem("file-no-row", tid, f"ticket file {fs[0].path} has no tracker row"))
        row_state = None
        if rs:
            row_state = resolve_state(rs[0].state, names)
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
            if r.link and not _is_url(r.link):
                target = r.link.split("#", 1)[0]
                base = tracker_dir if not r.archived else os.path.join(root, os.path.dirname(r.source))
                if target and not os.path.exists(os.path.normpath(os.path.join(base, target))):
                    add(Problem("missing-link", tid, f"link {r.link!r} in {r.source} points to a missing file"))
    _twins(model, cfg, rows, files)


def _is_url(link):
    return "://" in link or link.startswith("mailto:")


def _twins(model, cfg, rows, files):
    plats = cfg["twinPlatforms"]
    if not plats:
        return
    groups = defaultdict(dict)
    for tid in set(rows) | set(files):
        platform, kind = prefix_info(tid, cfg["idPrefixes"])
        if platform in plats:
            groups[(kind, tid.rpartition("-")[2])][platform] = tid
    for (kind, num), sides in sorted(groups.items()):
        missing = [p for p in plats if p not in sides]
        if missing and len(sides) >= 1:
            have = next(iter(sides.values()))
            model.problems.append(Problem(
                "twin-missing", have,
                f"twin ticket missing: no {', '.join(missing)} ticket for {kind} number {num}"))
