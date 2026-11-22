"""Shared loader for <project>/.claude/harness.json (standard library only).

Callers run under `python3 -I`, which leaves this folder off sys.path, so add it first:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import harness_config

load_config(cwd) returns (config, errors). `config` always holds every key, filled with
the documented default where the file is missing, malformed, or has a bad value.
`errors` lists human-readable problems (malformed JSON, unknown key, wrong type, bad
regex); an absent file is not an error. Callers must report `errors`, never drop them.
The loader never raises: any unexpected failure is reported and defaults are used.

Sources (`locate`): the root config `$CLAUDE_PROJECT_DIR/.claude/harness.json` (when
present) plus the nearest config found walking up from `cwd` to the repo root (first folder
with `.git`; else `$CLAUDE_PROJECT_DIR` if cwd is inside it; else cwd only). With
CLAUDE_PROJECT_DIR unset, or its config absent, every file found up to the repo root is
used. A file above the repo root is never loaded, only noticed. Each file is loaded on its
own; a broken or unreadable one is reported (an entry that exists but cannot be read counts
as present) and the others still apply. Sources merge so the result is never weaker than
any one of them: union (deduplicated) of protectedPaths, protectedConfigs, askCommands,
weakeningMarkers and presets; strictest pushPolicy (deny > ask > allow); checkWeakening
true if any source says true; for the rest the root wins, falling back to nested files.
`project_roots(cwd)` gives each loaded file's project folder, for matching relative globs.
"""
import copy
import json
import os
import re

DEFAULTS = {
    "version": 1,
    "protectedPaths": [],
    "protectedConfigs": [],
    "askCommands": [],
    "checkWeakening": True,
    "weakeningMarkers": [],
    "notify": True,
    "pushPolicy": "ask",
    "presets": [],
    "highRiskReviewerModel": "opus",
    "tickets": {
        "dirs": ["docs/tickets"],
        "featureGlobs": [],
        "idPrefixes": {},
        "twinPlatforms": None,
        "archive": False,
        "trackerPath": "docs/tickets/tracker.md",
        "integrationBranch": "main",
        "boardPath": "docs/tickets/board.html",
    },
}

PUSH_POLICIES = ("ask", "deny", "allow")
REVIEWER_MODELS = ("opus", "fable")


def _present(path):
    """True when something exists at path, or when that cannot be ruled out. Only a clean
    "not there" counts as absent, so an unreachable file is reported, never read as no config."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _search_boundary(start, project):
    """Highest folder the walk may read: the repo root (a `.git` dir, or file in a worktree),
    else CLAUDE_PROJECT_DIR if start is inside it, else start itself."""
    d = start
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if project and _inside(start, project):
        return project
    return start


def _walk(start, boundary):
    """Every present config from start up to and including boundary, nearest first."""
    found, d = [], start
    while True:
        path = os.path.join(d, ".claude", "harness.json")
        if _present(path):
            found.append(path)
        if d == boundary or os.path.dirname(d) == d:
            return found
        d = os.path.dirname(d)


def _same(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def locate(cwd):
    """(paths, notices): the config files to merge, root config first."""
    notices = []
    start = os.path.abspath(cwd or os.getcwd())
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    project = os.path.abspath(project) if project else None
    boundary = _search_boundary(start, project)
    walked = _walk(start, boundary)
    paths = []
    root_config = os.path.join(project, ".claude", "harness.json") if project else None
    if root_config and _present(root_config):
        paths.append(root_config)
        walked = walked[:1]  # root plus the nearest file
    for path in walked:
        if not any(_same(path, p) for p in paths):
            paths.append(path)
    if not paths and not project:
        d = boundary
        while os.path.dirname(d) != d:
            d = os.path.dirname(d)
            above = os.path.join(d, ".claude", "harness.json")
            if _present(above):
                notices.append(f"found {above} above the repository root but NOT using it; "
                               "move it into the repo or set CLAUDE_PROJECT_DIR")
                break
    return paths, notices


def _is_str_list(v):
    return isinstance(v, list) and all(isinstance(i, str) for i in v)


def _check_prefixes(v):
    return isinstance(v, dict) and all(
        isinstance(k, str) and isinstance(e, dict)
        and set(e) == {"platform", "kind"} and all(isinstance(x, str) for x in e.values())
        for k, e in v.items())


def _regex_problem(pattern):
    try:
        re.compile(pattern)
    except re.error as e:
        return str(e)
    return None


# Lists of strings where a bad entry must not switch the whole list off. value: what is lost.
_SALVAGED = {
    "protectedPaths": "that project path protection is NOT active",
    "protectedConfigs": "that project config protection is NOT active",
    "askCommands": "that project ask-command rule is NOT active",
    "weakeningMarkers": "that marker is NOT checked",
}


def _salvage_list(name, value, errors):
    lost = _SALVAGED[name]
    if isinstance(value, str):
        value = [value]
        errors.append(f"{name!r} should be a list; treating the string as a one-item list")
    if not isinstance(value, list):
        errors.append(f"{name!r} must be a list of strings; ALL your {name} entries are "
                      "NOT active (built-in rules still apply)")
        return []
    good = []
    for i, entry in enumerate(value):
        if not isinstance(entry, str):
            errors.append(f"{name!r} entry {i} ({entry!r}) is not a string and is ignored; {lost}")
        elif name == "weakeningMarkers" and _regex_problem(entry):
            errors.append(f"{name!r} entry {entry!r} is not a valid regex "
                          f"({_regex_problem(entry)}) and is ignored; {lost}")
        else:
            good.append(entry)
    return good


# key -> (predicate, expectation shown in the error message); bad value -> default + report
_TOP = {
    "version": (lambda v: v == 1 and not isinstance(v, bool), "1"),
    "checkWeakening": (lambda v: isinstance(v, bool), "true or false"),
    "notify": (lambda v: isinstance(v, bool), "true or false"),
    "pushPolicy": (lambda v: v in PUSH_POLICIES, 'one of "ask", "deny", "allow"'),
    "presets": (_is_str_list, "a list of preset names"),
    "highRiskReviewerModel": (lambda v: v in REVIEWER_MODELS, 'one of "opus", "fable"'),
}
_TICKETS = {
    "dirs": (_is_str_list, "a list of strings"),
    "featureGlobs": (_is_str_list, "a list of strings"),
    "idPrefixes": (_check_prefixes, 'an object of prefix -> {"platform": str, "kind": str}'),
    "twinPlatforms": (lambda v: v is None or _is_str_list(v), "a list of strings or null"),
    "archive": (lambda v: isinstance(v, bool), "true or false"),
    "trackerPath": (lambda v: isinstance(v, str), "a string"),
    "integrationBranch": (lambda v: isinstance(v, str), "a string"),
    "boardPath": (lambda v: isinstance(v, str), "a string"),
}


def _apply(raw, rules, given, prefix, errors):
    for key, value in raw.items():
        name = prefix + key
        if prefix == "" and key in _SALVAGED:
            given[key] = _salvage_list(key, value, errors)
        elif key in rules:
            ok, expected = rules[key]
            if ok(value):
                given[key] = copy.deepcopy(value)
            else:
                errors.append(f"{name!r} must be {expected}; using the default")
        elif not (prefix == "" and key == "tickets"):
            errors.append(f"unknown key {name!r}")


def _load_file(path):
    """(given, raw, errors): only the keys of this file that are present and valid."""
    errors, given = [], {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, RecursionError) as e:  # ValueError: bad JSON or UTF-8
        return {}, None, [f"{path}: cannot read: {type(e).__name__}: {e}; this file's "
                          "protections (protectedPaths, protectedConfigs, askCommands) "
                          "are NOT active"]
    if not isinstance(raw, dict):
        return {}, None, [f"{path}: must hold a JSON object; this file's protections "
                          "are NOT active"]
    _apply(raw, _TOP, given, "", errors)
    if "tickets" in raw:
        if isinstance(raw["tickets"], dict):
            given["tickets"] = {}
            _apply(raw["tickets"], _TICKETS, given["tickets"], "tickets.", errors)
        else:
            errors.append("'tickets' must be an object; using the defaults")
    return given, raw, [f"{path}: {e}" if not e.startswith(path) else e for e in errors]


_UNION = ("protectedPaths", "protectedConfigs", "askCommands", "weakeningMarkers", "presets")
_STRICTNESS = {"allow": 0, "ask": 1, "deny": 2}


def _merge(sources):
    """Merge per-file `given` dicts (root first) over the defaults; never weaker than any one."""
    cfg = copy.deepcopy(DEFAULTS)
    for key in _UNION:
        cfg[key] = list(dict.fromkeys(v for g in sources for v in g.get(key, [])))
    policies = [g["pushPolicy"] for g in sources if "pushPolicy" in g]
    if policies:
        cfg["pushPolicy"] = max(policies, key=_STRICTNESS.get)
    checks = [g["checkWeakening"] for g in sources if "checkWeakening" in g]
    if checks:
        cfg["checkWeakening"] = any(checks)
    for key in ("version", "notify", "highRiskReviewerModel"):
        for g in sources:
            if key in g:
                cfg[key] = g[key]
                break
    for key in DEFAULTS["tickets"]:
        for g in sources:
            if key in g.get("tickets", {}):
                cfg["tickets"][key] = g["tickets"][key]
                break
    return cfg


def load_all(cwd):
    """(config, errors, roots). Never raises; roots are the project folders of loaded files."""
    errors = []
    try:
        paths, notices = locate(cwd)
        errors.extend(notices)
        sources, loaded = [], []
        for path in paths:
            given, raw, errs = _load_file(path)
            sources.append(given)
            errors.extend(errs)
            if raw is not None:
                loaded.append((path, raw))
        if len(loaded) > 1 and any(raw != loaded[0][1] for _, raw in loaded[1:]):
            errors.append("configs differ; merged " + " and ".join(p for p, _ in loaded)
                          + " (union of protections, strictest pushPolicy)")
        roots = [os.path.dirname(os.path.dirname(p)) for p in paths]
        return _merge(sources), errors, roots
    except Exception as e:  # never crash a hook: report and fall back to defaults
        errors.append(f"unexpected {type(e).__name__} while loading harness.json: {e}; using "
                      "defaults, so ALL project protections are NOT active")
        return copy.deepcopy(DEFAULTS), errors, []


def load_config(cwd):
    cfg, errors, _ = load_all(cwd)
    return cfg, errors


def project_roots(cwd):
    return load_all(cwd)[2]


def format_errors(errors):
    return "".join(f"[dev-harness] harness.json: {e}\n" for e in errors)
