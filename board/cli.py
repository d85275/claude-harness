"""Command line for dev-harness-board.

    dev-harness-board [--root DIR] [--out PATH]    write the board HTML (default mode)
    dev-harness-board [--root DIR] --check | --json

(no flag) write tickets.boardPath (or --out) atomically; print the path written
--check   print one line per problem; exit 1 if any, else 0. A board file older than the tracker
          or a ticket file adds a "warning: ..." line on stderr (no effect on the exit code)
--json    print the parsed model as JSON

Exit 2: bad usage or unreadable config. Only the render mode writes a file.
Run from a linked worktree, it works on the main checkout (so the board is written there). The
render and json modes run read-only git (`rev-parse`, `worktree list`, each with a timeout) to
show tickets being built in a worktree on feature/<id>; --check runs no git for its result.
Add new modes (init, install-hooks) as a function `run_<mode>(ctx)` registered in MODES, and
select it in `pick_mode`. `ctx` carries root, tickets config, model, args and the streams.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "hooks", "scripts"))
import harness_config  # noqa: E402

from . import check as checks  # noqa: E402
from . import parse  # noqa: E402
from . import render  # noqa: E402
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


def build_model(root, tickets_cfg, live=False):
    """Parse and check `root`. With `live`, tickets with a worktree on feature/<id> show the
    state of that work (never read by the checks)."""
    model = parse.build(root, tickets_cfg)
    checks.merge(model, root, tickets_cfg)
    checks.check(model, root, tickets_cfg)
    if live:
        worktrees.apply(model, root, tickets_cfg)
    return model


def run_check(ctx):
    for p in ctx["model"].problems:
        ctx["out"].write(p.line() + "\n")
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
    html = render.build_html(ctx["model"], ctx["root"], ctx["cfg"], render.now_utc(), render.read_commit(ctx["root"]))
    try:
        render.write_atomic(path, html)
    except OSError as e:
        ctx["err"].write(f"dev-harness-board: cannot write {path}: {e}\n")
        return 2
    ctx["out"].write(f"wrote {path}\n")
    return 0


MODES = {"check": run_check, "json": run_json, "render": run_render}


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


USAGE = "usage: dev-harness-board [--root DIR] [--out PATH | --check | --json]\n"


def main(argv, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
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
    ctx = {"root": root, "cfg": cfg, "model": build_model(root, cfg, live=mode != "check"), "args": args, "out": out, "err": err}
    return MODES[mode](ctx)
