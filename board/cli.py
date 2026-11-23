"""Command line for dev-harness-board.

    dev-harness-board [--root DIR] [--out PATH]    write the board HTML (default mode)
    dev-harness-board [--root DIR] --check | --json
    dev-harness-board install-hooks | uninstall-hooks [--root DIR] [--check]
    dev-harness-board init [--root DIR] --prefix P [--dry-run]   (new project; see board/init.py)
    dev-harness-board set-state <ID> <State> [--commit SHA] | archive <ID> | twin <ID>   (board/state.py)
    dev-harness-board hook <name> [args]           (run by the installed git hooks)
    dev-harness-board status [--root DIR]          yes (exit 0) or no (exit 1): has the project opted in?

Opt-in: the plugin's ticket features act only where a harness.json (not the built-in defaults) has a
`tickets` section. Without one, set-state, archive, install-hooks refuse (exit 1), hook does nothing,
and the plain render writes nothing unless --out is given; --check, --json, twin and init still work.

(no flag) write tickets.boardPath (or --out) atomically; print the path written
--check   print one line per problem; exit 1 if any, else 0. A board file older than the tracker
          or a ticket file adds a "warning: ..." line on stderr (no effect on the exit code)
--json    print the parsed model as JSON

The board is written only inside the main checkout and never under `.git`: a tickets.boardPath
that leads elsewhere (absolute, `../`, a symlink) is refused unless --out names the file.
--check also warns on stderr when this repository's core.hooksPath names a dev-harness hooks
folder that no longer exists (one `git config` read; not part of the result).

Exit 2: bad usage or unreadable config. Only the render mode writes a file.
Run from a linked worktree, it works on the main checkout (so the board is written there). The
render and json modes run read-only git (`rev-parse`, `worktree list`, each with a timeout) to
show tickets being built in a worktree on feature/<id>; --check runs no git for its result.
Add new modes (init) as a function `run_<mode>(ctx)` registered in MODES, and select it in
`pick_mode`. `ctx` carries root, tickets config, model, args and the streams. Subcommands with
their own arguments (init, install-hooks, uninstall-hooks, hook; see board/githooks.py) are in
COMMANDS as `run(argv, out, err)` and need no model.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "hooks", "scripts"))
import harness_config  # noqa: E402

from . import check as checks  # noqa: E402
from . import githooks  # noqa: E402
from . import init as initmod  # noqa: E402
from . import parse  # noqa: E402
from . import render  # noqa: E402
from . import state  # noqa: E402
from . import worktrees  # noqa: E402

FATAL_CONFIG = ("cannot read", "must hold a JSON object")


def find_root(start):
    """Nearest folder from `start` up that holds `.git` (dir or file); else `start`."""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        if os.path.dirname(d) == d:
            return os.path.abspath(start)
        d = os.path.dirname(d)


def load_tickets_config(root):
    """(tickets config, errors) for the project at `root`, ignoring any outer CLAUDE_PROJECT_DIR."""
    saved = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = root
    try:
        cfg, errors = harness_config.load_config(root)
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = saved
    return cfg["tickets"], errors


def tickets_sources(root):
    """Config files at `root` (outer CLAUDE_PROJECT_DIR ignored) that hold a `tickets` section."""
    saved = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = root
    try:
        return harness_config.tickets_sources(root)
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = saved


NOT_SWITCHED = "this project has not switched to the plugin's ticket system; nothing changed"
GATED = ("set-state", "archive", "install-hooks", "hook")


def _root_arg(argv):
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--root="):
            return a[len("--root="):]
    return None


def _command_root(argv):
    start = os.path.abspath(_root_arg(argv)) if _root_arg(argv) else find_root(os.getcwd())
    # a linked worktree is judged by its main checkout: main decides
    return (worktrees.main_checkout(start) or start) if os.path.isdir(start) else None


def run_status(argv, out, err):
    """Print whether the project uses the plugin's ticket system and why; exit 0 yes, 1 no."""
    root = _command_root(argv)
    if root is None:
        err.write("dev-harness-board status: --root is not a directory\n")
        return 2
    found = tickets_sources(root)
    if found:
        out.write(f"yes: {found[0]} has a tickets section\n")
        return 0
    out.write("no: no harness.json has a tickets section (" + NOT_SWITCHED.split(";")[0] + ")\n")
    return 1


def build_model(root, tickets_cfg, live=False):
    """Parse and check `root`. With `live`, tickets with a worktree on feature/<id> show the
    state of that work (never read by the checks)."""
    model = parse.build(root, tickets_cfg)
    checks.merge(model, root, tickets_cfg)
    checks.check(model, root, tickets_cfg)
    if live:
        worktrees.apply(model, root, tickets_cfg)
    return model


def board_outside(root, path):
    """Why `path` may not hold the board (outside the checkout, or under .git), or None."""
    real_root, real = os.path.realpath(root), os.path.realpath(path)
    try:
        rel = os.path.relpath(real, real_root)
    except ValueError:                  # another drive
        rel = os.pardir
    parts = rel.split(os.sep)
    if rel == os.curdir or parts[0] == os.pardir or os.path.isabs(rel):
        return f"board not written: tickets.boardPath leads outside {real_root}, to {real}"
    into_git = f"board not written: tickets.boardPath leads into the git dir, to {real}"
    if any(p.lower() == ".git" for p in parts):     # `.GIT` is `.git` on a case-insensitive disk
        return into_git
    # The robust check: no existing folder on the way (up to the checkout) may be the git dir or
    # the common dir under another name (case, Unicode form, a separate git dir in the tree).
    gitdirs = git_dirs(root)
    d = real
    while True:
        if os.path.exists(d) and any(_same(d, g) for g in gitdirs):
            return into_git
        if d == real_root or os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    # A hard link to a file in the git dir (a rename would leave that file alone, but refuse it)
    try:
        st = os.stat(real)
    except OSError:
        st = None
    if st is not None and st.st_nlink > 1 and _inode_in(st, gitdirs):
        return into_git
    return None


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def git_dirs(root):
    """The per-worktree git dir and the common dir of the checkout at `root` (those that exist)."""
    found = []
    dotgit = os.path.join(root, ".git")
    if os.path.isdir(dotgit):
        found.append(dotgit)
    for flag in ("--git-dir", "--git-common-dir"):
        code, out, _ = githooks._git(["rev-parse", flag], root)
        if code == 0 and out.strip():
            d = os.path.abspath(os.path.join(root, out.rstrip("\n")))
            if os.path.isdir(d) and d not in found:
                found.append(d)
    return found


def _inode_in(st, folders):
    key = (st.st_dev, st.st_ino)
    for top in folders:
        for d, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if x != "objects"]
            for n in files:
                try:
                    s = os.lstat(os.path.join(d, n))
                except OSError:
                    continue
                if (s.st_dev, s.st_ino) == key:
                    return True
    return False


def run_check(ctx):
    for p in ctx["model"].problems:
        ctx["out"].write(p.line() + "\n")
    if os.path.exists(os.path.join(ctx["root"], ".git")):
        stale = githooks.stale_warning(ctx["root"])
        if stale:
            ctx["err"].write(stale + "\n")
    for w in ctx["model"].warnings:
        ctx["err"].write(w + "\n")
    board = board_path(ctx)
    for rel in render.stale_sources(ctx["root"], ctx["cfg"], ctx["model"], board):
        ctx["err"].write(f"warning: board file is older than {rel}; run dev-harness-board to rebuild\n")
    return 1 if ctx["model"].problems else 0


def run_json(ctx):
    json.dump(ctx["model"].as_dict(), ctx["out"], indent=2)
    ctx["out"].write("\n")
    return 1 if ctx["args"].check and ctx["model"].problems else 0


def board_path(ctx):
    target = ctx["args"].out or ctx["cfg"]["boardPath"]
    return os.path.abspath(os.path.join(ctx["root"], target))


def run_render(ctx):
    path = board_path(ctx)
    problem = None if ctx["args"].out else board_outside(ctx["root"], path)
    if problem:
        ctx["err"].write(f"dev-harness-board: {problem} (use --out to name a file elsewhere)\n")
        return 2
    html = render.build_html(ctx["model"], ctx["root"], ctx["cfg"], render.now_utc(), render.read_commit(ctx["root"]))
    try:
        render.write_atomic(path, html)
    except OSError as e:
        ctx["err"].write(f"dev-harness-board: cannot write {path}: {e}\n")
        return 2
    ctx["out"].write(f"wrote {path}\n")
    return 0


MODES = {"check": run_check, "json": run_json, "render": run_render}
COMMANDS = {"install-hooks": githooks.run_install, "uninstall-hooks": githooks.run_uninstall,
            "hook": githooks.run_hook, "init": initmod.run,
            "status": run_status, "set-state": state.run_set_state, "archive": state.run_archive, "twin": state.run_twin}


def pick_mode(args):
    if args.json:
        return "json"
    if args.check:
        return "check"
    return "render"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_args(argv):
    ap = _Parser(prog="dev-harness-board", add_help=False)
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    return ap.parse_args(argv)


USAGE = ("usage: dev-harness-board [--root DIR] [--out PATH | --check | --json]\n"
         "       dev-harness-board install-hooks | uninstall-hooks [--root DIR] [--check]\n"
         "       dev-harness-board init --prefix PREFIX [--root DIR] [--dry-run]\n"
         "       dev-harness-board set-state ID STATE [--commit SHA] | archive ID | twin ID  (see board/state.py)\n")


def main(argv, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    if argv and argv[0] in GATED:
        root = _command_root(argv[1:])
        # a broken config: the hook still reports it
        if root is not None and not tickets_sources(root):
            if argv[0] != "hook":
                err.write(f"dev-harness-board {argv[0]}: {NOT_SWITCHED}\n")
                return 1
            if not any(f in e for e in load_tickets_config(root)[1] for f in FATAL_CONFIG):
                return 0
    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](list(argv[1:]), out, err)
    try:
        args = parse_args(argv)
    except ValueError as e:
        err.write(f"dev-harness-board: {e}\n{USAGE}")
        return 2
    if args.help:
        out.write(USAGE)
        return 0
    mode = pick_mode(args)
    if args.out is not None and mode != "render":
        err.write(f"dev-harness-board: --out only applies when writing the board\n{USAGE}")
        return 2
    if args.out == "":
        err.write(f"dev-harness-board: --out needs a path\n{USAGE}")
        return 2
    root = os.path.abspath(args.root) if args.root else find_root(os.getcwd())
    if not os.path.isdir(root):
        err.write(f"dev-harness-board: --root {root} is not a directory\n")
        return 2
    root = worktrees.main_checkout(root) or root     # run from a worktree: work on the main checkout
    cfg, errors = load_tickets_config(root)
    err.write(harness_config.format_errors(errors))
    if any(f in e for e in errors for f in FATAL_CONFIG):
        return 2
    if mode == "render" and args.out is None and not tickets_sources(root):
        err.write(f"dev-harness-board: {NOT_SWITCHED}; use --out to write a board file anyway\n")
        return 1
    ctx = {"root": root, "cfg": cfg, "model": build_model(root, cfg, live=mode != "check"), "args": args, "out": out, "err": err}
    return MODES[mode](ctx)
