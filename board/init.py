"""`dev-harness-board init`: set up the ticket system in a project that has none.

    dev-harness-board init [--root DIR] --prefix PREFIX[:platform[:kind]] [--prefix ...]
                           [--branch NAME] [--archive] [--twin-platforms a,b] [--dry-run]

It writes only files that do not exist: <dirs[0]>/TEMPLATE.md, the tracker (empty table),
docs/STATUS.md and .claude/harness.json; it adds the Harness section to CLAUDE.md when the file
has none. It merges missing `tickets` keys into an existing harness.json (keys that are present
are never changed; the file is rewritten with 2-space indent) and never edits an existing
CLAUDE.md Harness section: it prints what that section lacks. If a tracker or ticket folder
exists, init is in adopt mode (board/adopt.py): `--check` prints the list of tickets the board
would show and what was not used, with a hash of that list; `--confirm <hash>` is the switch, and
it writes only while the list still has that hash. Fresh only when there is no tracker with rows (searched, not only at the default
path) and no ticket file; harness.json settings prove nothing. `--mode` prints fresh, adopt or
switched (the board at boardPath is this plugin's page and --check is clean: nothing to change).
Running it again changes nothing.

It never installs the git hooks (that is `install-hooks`, which the guard asks about); it prints
that next step. After writing, it builds the board once. --dry-run lists what it would do and
writes nothing and builds nothing. Exit 0 ok, 1 stopped (unmergeable harness.json; adopt mode
refused or failed), 2 bad usage or unreadable config.
"""
import argparse
import json
import os
import re
import sys
import tempfile

from . import adopt

HERE = os.path.dirname(os.path.realpath(__file__))
TEMPLATES = os.path.join(HERE, "..", "templates")
PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
SECTION_RE = re.compile(r"^#{1,6}[ \t]+Harness[ \t\r]*$", re.M | re.I)
FLOW_MARK = "Changing the ticket flow"
CONFIG_REL = os.path.join(".claude", "harness.json")


VERBS = {"create": "created", "merge": "merged", "append": "appended to"}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_args(argv):
    ap = _Parser(prog="dev-harness-board init", add_help=False)
    ap.add_argument("--root")
    ap.add_argument("--prefix", action="append", default=[])
    ap.add_argument("--branch")
    ap.add_argument("--archive", action="store_true")
    ap.add_argument("--twin-platforms")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--mode", action="store_true")
    ap.add_argument("--confirm")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    return ap.parse_args(argv)


USAGE = ("usage: dev-harness-board init [--root DIR] --prefix PREFIX[:platform[:kind]] [--prefix ...]\n"
         "                              [--branch NAME] [--archive] [--twin-platforms a,b] [--dry-run]\n"
         "                              [--fresh]   (only test or example folders hold tickets: treat them as test data)\n"
         "       dev-harness-board init --mode | --check [--branch NAME] [--prefix ...] [--root DIR]   (which mode; adopt dry run)\n"
         "       dev-harness-board init --confirm LIST-HASH [--branch NAME] [--prefix ...] [--root DIR]   (adopt mode: the switch)\n")


def _template(name, **values):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        text = f.read()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def _prefixes(specs):
    """{PREFIX: {"platform", "kind"}} from PREFIX[:platform[:kind]] specs; ValueError if bad."""
    out = {}
    for spec in specs:
        parts = spec.split(":")
        if len(parts) > 3 or not PREFIX_RE.match(parts[0]) or any(not p for p in parts):
            raise ValueError(f"bad --prefix {spec!r}: use PREFIX[:platform[:kind]], PREFIX letters and digits")
        out[parts[0]] = {"platform": parts[1] if len(parts) > 1 else "app",
                         "kind": parts[2] if len(parts) > 2 else "feature"}
    return out


def _wanted_tickets(args, cfg, prefixes):
    """The `tickets` keys init manages, with the values the owner asked for."""
    twins = [t.strip() for t in args.twin_platforms.split(",") if t.strip()] if args.twin_platforms else None
    wanted = {"dirs": list(cfg["dirs"]), "trackerPath": cfg["trackerPath"], "boardPath": cfg["boardPath"],
              "integrationBranch": args.branch or cfg["integrationBranch"],
              "archive": bool(args.archive), "twinPlatforms": twins}
    if prefixes:
        wanted["idPrefixes"] = prefixes
    return wanted


def _load_json(path):
    """(object, error). A file with a repeated key is refused, since rewriting would drop one."""
    dup = []

    def pairs(items):
        keys = [k for k, _ in items]
        if len(set(keys)) != len(keys):
            dup.append(True)
        return dict(items)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f, object_pairs_hook=pairs)
    except (OSError, ValueError, RecursionError) as e:
        return None, f"cannot read ({type(e).__name__}: {e})"
    if dup:
        return None, "has a repeated key"
    if not isinstance(data, dict):
        return None, "is not a JSON object"
    return data, None


def _json_text(data):
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _blocked(path, rel):
    """Why `rel` must not be rewritten (symlink, read-only), or None."""
    if os.path.islink(path):
        return f"{rel} is a symlink"
    if not os.access(path, os.R_OK | os.W_OK):
        return f"{rel} is not readable and writable"
    return None


def plan_config(root, wanted, notes):
    """(action, content, detail) for harness.json; action is create, merge or None."""
    path = os.path.join(root, CONFIG_REL)
    hand = lambda why: notes.append(f"{CONFIG_REL} {why}; not edited. Add this `tickets` section by hand:\n"
                                    + _json_text({"tickets": wanted}))
    if not os.path.lexists(path):
        return "create", _json_text({"version": 1, "tickets": wanted}), "tickets section"
    why = _blocked(path, CONFIG_REL)
    if why:
        hand(why.split(" ", 1)[1])
        return None, None, None
    data, err = _load_json(path)
    if err:
        hand(err)
        return None, None, None
    before = set(data)
    tickets = data.get("tickets")
    if "tickets" not in data:
        data["tickets"], added = wanted, list(wanted)
    elif not isinstance(tickets, dict):
        notes.append(f"{CONFIG_REL}: 'tickets' is not an object; not edited")
        return None, None, None
    else:
        added = [k for k in wanted if k not in tickets]
        for k in added:
            tickets[k] = wanted[k]
        for k in wanted:
            if k not in added and tickets[k] != wanted[k]:
                notes.append(f"{CONFIG_REL}: kept existing tickets.{k}")
    if not added:
        return None, None, None
    try:
        text = _json_text(data)
    except ValueError as e:
        hand(f"cannot be merged ({e})")
        return None, None, None
    return "merge", text, ("tickets section" if "tickets" not in before else "add tickets." + ", ".join(added))


def _with_endings(text, ending):
    return text.replace("\n", ending) if ending != "\n" else text


def plan_claude_md(root, section):
    """(action, content, note) for CLAUDE.md."""
    path = os.path.join(root, "CLAUDE.md")
    if not os.path.lexists(path):
        return "create", section, None
    why = _blocked(path, "CLAUDE.md")
    if why:
        return None, None, f"{why}; not edited. Add this by hand:\n\n{section}"
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
    except (OSError, ValueError) as e:
        return None, None, f"CLAUDE.md cannot be read ({e}); not edited. Add this by hand:\n\n{section}"
    if SECTION_RE.search(text):
        if FLOW_MARK in text:
            return None, None, None
        i = section.index("- **How tickets work**")
        return None, None, ("CLAUDE.md already has a Harness section; not edited. Add these lines to it:\n\n"
                            + section[i:])
    crlf = text.count("\r\n")
    ending = "\r\n" if crlf and crlf >= text.count("\n") - crlf else "\n"
    sep = "" if not text else (ending if text.endswith("\n") else ending * 2)
    return "append", text + sep + _with_endings(section, ending), None


def _write_new(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="") as f:
        f.write(text)


def _replace(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".harness-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def run(argv, out, err):
    try:
        args = parse_args(argv)
        prefixes = _prefixes(args.prefix)
    except ValueError as e:
        err.write(f"dev-harness-board init: {e}\n{USAGE}")
        return 2
    if args.help:
        out.write(USAGE)
        return 0
    from . import cli, worktrees       # here: cli imports this module
    root = os.path.abspath(args.root) if args.root else cli.find_root(os.getcwd())
    if not os.path.isdir(root):
        err.write(f"dev-harness-board init: --root {root} is not a directory\n")
        return 2
    root = worktrees.main_checkout(root) or root
    cfg, errors = cli.load_tickets_config(root)
    err.write(cli.harness_config.format_errors(errors))
    if any(f in e for e in errors for f in cli.FATAL_CONFIG):
        return 2

    tickets_dir = cfg["dirs"][0] if cfg["dirs"] else "docs/tickets"
    tracker_rel, template_rel = cfg["trackerPath"], os.path.join(tickets_dir, "TEMPLATE.md")
    tracker, template = os.path.join(root, tracker_rel), os.path.join(root, template_rel)
    mode, why = adopt.decide_mode(root, cfg, fresh_ok=args.fresh)
    if args.mode:
        out.write(f"mode: {mode}\nwhy: {why}\n")
        return 0
    if mode == "fresh" and "test or example folders" in why:
        out.write(f"note: {why.split('; ', 1)[1]}\n")
    if args.confirm and mode != "adopt":
        err.write(f"dev-harness-board init: --confirm is only for adopt mode; this project is {mode} ({why})\n")
        return 2
    if mode == "switched":
        out.write(f"nothing to change: {why}\n")
        return 0
    if mode == "adopt":
        args.prefix_specs = list(prefixes.items())
        return adopt.run(root, cfg, args, out, err, _run_steps, _adopt_steps(tickets_dir, args))
    args.dry_run = args.dry_run or args.check
    if not cfg["idPrefixes"] and not prefixes and not _config_has(root, "idPrefixes"):
        err.write(f"dev-harness-board init: --prefix is required (e.g. --prefix WEB)\n{USAGE}")
        return 2

    wanted = _wanted_tickets(args, cfg, prefixes)
    example = next(iter(prefixes or cfg["idPrefixes"] or {"PROJ": 0})) + "-12"
    values = {"tickets-dir": tickets_dir, "tracker-path": tracker_rel,
              "integration-branch": wanted["integrationBranch"], "example-id": example}
    status_dir = os.path.join(root, "docs")
    tracker_link = os.path.relpath(tracker, status_dir).replace(os.sep, "/")

    notes, steps = [], []       # steps: (verb, rel path, content, detail)
    # Order matters: TEMPLATE last, so a failure never leaves it without the tracker.
    for rel, content in ((tracker_rel, _template("tracker.md")),
                         (os.path.join("docs", "STATUS.md"), _template("STATUS.md", **{"tracker-link": tracker_link}))):
        if not os.path.lexists(os.path.join(root, rel)):
            steps.append(("create", rel, content, ""))
    action, content, detail = plan_config(root, wanted, notes)
    if action:
        steps.append((action, CONFIG_REL, content, detail))
    action, content, note = plan_claude_md(root, _template("CLAUDE-harness-section.md", **values))
    if action:
        steps.append((action, "CLAUDE.md", content, "Harness section"))
    if note:
        notes.append(note)
    if not os.path.lexists(template):
        steps.append(("create", template_rel, _template("ticket-template.md", **values), ""))

    if not steps:
        out.write("nothing to change: the ticket files, harness.json tickets section and Harness section are in place\n")
    if args.dry_run:
        for verb, rel, content, detail in steps:
            out.write(f"would {verb} {rel}" + (f" ({detail})" if detail else "") + "\n")
    elif _run_steps(root, steps, out, err):
        return 1
    for n in notes:
        out.write(n.rstrip("\n") + "\n")
    if args.dry_run:
        out.write("dry run: nothing written\n")
        return 0
    code = 0
    if steps and cli.tickets_sources(root):
        code = cli.main(["--root", root], out, err)     # build the board once
    elif steps:
        out.write("board not built: no harness.json has a tickets section yet; add one by hand, "
                  "then run dev-harness-board\n")
    out.write("Next, if you want git to rebuild the board by itself, run: dev-harness-board install-hooks\n"
              "(init does not install hooks; the guard asks before that command)\n")
    return code


def _run_steps(root, steps, out, err):
    """Write each (verb, rel, content, detail) step; 0 when all went through, else 1."""
    done = []
    for verb, rel, content, detail in steps:
        tail = f" ({detail})" if detail else ""
        try:
            path = os.path.join(root, rel)
            if verb == "create":
                _write_new(path, content)
            else:
                _replace(path, content)
        except OSError as e:
            err.write(f"dev-harness-board init: cannot write {rel}: {e}\n"
                      f"partially done; written: {', '.join(done) or 'nothing'}; fix and re-run\n")
            return 1
        done.append(rel)
        out.write(f"{VERBS[verb]} {rel}{tail}\n")
    return 0


def _adopt_steps(tickets_dir, args):
    """plan_steps for adopt mode: harness.json tickets keys and the CLAUDE.md Harness section."""
    def plan(root, wanted, eff, notes):
        steps, blocker, cfg_notes = [], None, []
        action, content, detail = plan_config(root, wanted, cfg_notes)
        if any("not edited" in n for n in cfg_notes):        # keys are missing but the file cannot be edited
            blocker = (f"{CONFIG_REL} cannot be edited (symlink, read-only, repeated key, NaN or a 'tickets' that is not "
                       "an object), so the settings cannot be written; add the printed block by hand, then re-run")
        notes.extend(cfg_notes)
        if action:
            steps.append((action, CONFIG_REL, content, detail))
        example = (next(iter(wanted["idPrefixes"]), "PROJ")) + "-12"
        values = {"tickets-dir": tickets_dir, "tracker-path": wanted["trackerPath"],
                  "integration-branch": wanted["integrationBranch"], "example-id": example}
        action, content, note = plan_claude_md(root, _template("CLAUDE-harness-section.md", **values))
        if action:
            steps.append((action, "CLAUDE.md", content, "Harness section"))
        if note:
            notes.append(note)
        return steps, blocker
    return plan


def _read(path):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, ValueError):
        return None


def _config_has(root, key):
    path = os.path.join(root, CONFIG_REL)
    data, _ = _load_json(path) if os.path.lexists(path) else (None, None)
    return isinstance(data, dict) and isinstance(data.get("tickets"), dict) and key in data["tickets"]
