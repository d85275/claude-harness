"""Command line for dev-harness-board.

    dev-harness-board [--root DIR] --check | --json

--check   print one line per problem; exit 1 if any, else 0
--json    print the parsed model as JSON

Exit 2: bad usage or unreadable config. The command never writes a file and never runs git.
Add new modes (render, init, install-hooks) in MODES and in `parse_args`.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "hooks", "scripts"))
import harness_config  # noqa: E402

from . import check as checks  # noqa: E402
from . import parse  # noqa: E402

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


def build_model(root, tickets_cfg):
    model = parse.build(root, tickets_cfg)
    checks.merge(model, root, tickets_cfg)
    checks.check(model, root, tickets_cfg)
    return model


def run_check(model, out):
    for p in model.problems:
        out.write(p.line() + "\n")
    return 1 if model.problems else 0


def run_json(model, out):
    json.dump(model.as_dict(), out, indent=2)
    out.write("\n")
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_args(argv):
    ap = _Parser(prog="dev-harness-board", add_help=False)
    ap.add_argument("--root")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    return ap.parse_args(argv)


USAGE = "usage: dev-harness-board [--root DIR] (--check | --json)\n"


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
    if not (args.check or args.json):
        err.write(USAGE)
        return 2
    root = os.path.abspath(args.root) if args.root else find_root(os.getcwd())
    if not os.path.isdir(root):
        err.write(f"dev-harness-board: --root {root} is not a directory\n")
        return 2
    cfg, errors = load_tickets_config(root)
    err.write(harness_config.format_errors(errors))
    if any(f in e for e in errors for f in FATAL_CONFIG):
        return 2
    model = build_model(root, cfg)
    if args.json:
        run_json(model, out)
        return 1 if args.check and model.problems else 0
    return run_check(model, out)

