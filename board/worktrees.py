"""Live worktrees: a ticket being built shows on the board before it is merged.

Only read-only git commands (`rev-parse`, `worktree list --porcelain`), each with a timeout.
Every failure (no git, not a repo, timeout, odd output) means "no live worktrees"; the board
still builds. Worktree files are only read, never written.
"""
import os
import subprocess

from . import parse
from .parse import resolve_state, state_names

GIT_TIMEOUT = 5
BRANCH_PREFIX = "feature/"
# Unmerged work moves a ticket up this ladder only. Later states are capped at In review.
LADDER = ("Planned", "In progress", "Fixing", "In review")
# States on main that a worktree never moves.
FROZEN = ("Owner check", "Done", "Blocked", "Deferred")


def git(args, cwd):
    """stdout of a read-only git command, or None on any failure or timeout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k == "GIT_EXEC_PATH"}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=GIT_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout if r.returncode == 0 else None


def main_checkout(root):
    """The main checkout's folder when `root` is in a git repo (a linked worktree resolves to
    the main one), else None. A folder whose `.git` is a directory is itself the main checkout."""
    dotgit = os.path.join(root, ".git")
    if os.path.isdir(dotgit):
        return root
    if not os.path.exists(dotgit):
        return None
    first = _entries(root)[:1]
    if not first or first[0]["bare"] or not os.path.isdir(first[0]["path"]):
        return None
    top = first[0]["path"]              # git lists the main worktree first
    if os.path.exists(os.path.join(top, ".git")):
        return top
    # A main checkout with a separate git dir is listed by its git dir. If `root` is that
    # checkout its `.git` file points there; from a linked worktree the work tree is unknown.
    try:
        with open(dotgit, encoding="utf-8") as f:
            target = f.read().strip()
        if target.startswith("gitdir:") and os.path.realpath(
                os.path.join(root, target[7:].strip())) == os.path.realpath(top):
            return root
    except (OSError, ValueError):
        pass
    return None


def _entries(root):
    """Every `git worktree list` entry as {path, branch, skip, bare}. Uses -z (NUL separated, so
    a path may hold a newline); falls back to the line format for git before 2.36."""
    out = git(["worktree", "list", "--porcelain", "-z"], root)
    if out is not None:
        blocks = [b.split("\0") for b in out.split("\0\0")]
    else:
        out = git(["worktree", "list", "--porcelain"], root)
        if out is None:
            return []
        blocks = [b.split("\n") for b in out.split("\n\n")]
    entries = []
    for fields in blocks:
        e = {"path": None, "branch": None, "skip": False, "bare": False}
        for line in fields:
            if line.startswith("worktree "):
                e["path"] = line[9:]
            elif line.startswith("branch "):
                e["branch"] = line[7:]
            elif line == "bare":
                e["bare"] = e["skip"] = True
            elif line.startswith("prunable"):
                e["skip"] = True
        if e["path"]:
            entries.append(e)
    return entries


def list_worktrees(root):
    """[(path, branch)] of live worktrees: prunable, missing, bare and detached ones are skipped."""
    found = []
    for e in _entries(root):
        if e["branch"] and not e["skip"] and os.path.isdir(e["path"]):
            br = e["branch"]
            found.append((e["path"], br[len("refs/heads/"):] if br.startswith("refs/heads/") else br))
    return found


def cap(state):
    """Unmerged work shows at most In review; a state the ladder lacks counts as In review."""
    return state if state in LADDER else "In review"


def _own_state(path, ticket_id, cfg, names):
    """Most advanced capped state in the worktree's own tracker row and ticket file (or None)."""
    model = parse.build(path, cfg)
    found = []
    for r in model.rows:
        if r.id == ticket_id:
            found.append(resolve_state(r.state, names))
    for f in model.files:
        if f.id == ticket_id:
            found.append(resolve_state(f.status, names))
    states = [s for s in found if s and s not in ("Blocked", "Deferred")]
    return max((cap(s) for s in states), key=LADDER.index, default=None)


def apply(model, root, cfg):
    """Raise ticket["state"] from live worktrees; sets ticket["liveBranch"] when the shown state
    came from one. `root` is the main checkout."""
    names = state_names(cfg["stateAliases"])
    by_branch = {BRANCH_PREFIX + t["id"].lower(): t for t in model.tickets}
    here = os.path.realpath(root)
    for path, branch in list_worktrees(root):
        t = by_branch.get(branch.lower())
        if t is None or os.path.realpath(path) == here:
            continue
        main_state = t["state"] or t["fileState"] or "Planned"
        if main_state in FROZEN:
            continue
        try:
            own = _own_state(path, t["id"], cfg, names)
        except Exception:               # a broken worktree must not stop the board
            continue
        best = max(["In progress"] + ([own] if own else []), key=LADDER.index)
        current = t.get("liveState") or (main_state if main_state in LADDER else "Planned")
        if LADDER.index(best) > LADDER.index(current):
            t["state"], t["liveState"], t["liveBranch"] = best, best, branch
    for t in model.tickets:
        t.pop("liveState", None)
