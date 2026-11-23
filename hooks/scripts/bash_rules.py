"""Bash rules of the dev-harness guard: credential reads, push policy, protected-config writes,
always-ask commands and machine-wide device resets.

check() returns the decision for one command, or None to allow; guard.py prints it and exits.
The shell text is read by shell_scan.py, and the push and config-write pass is ShellCheck in
shell_check.py. The structural rule: the publish and redirect regexes read the full text, and
scanner knowledge only removes a hit inside a proven data heredoc body (see ShellCheck.run).
"""
import fnmatch
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:  # python3 -I omits it
    sys.path.insert(0, _HERE)
import harness_config  # noqa: E402
from shell_check import (DEFAULT_PROTECTED_CONFIGS, GIT_OPTIONS_WITH_VALUE, _OPTS,  # noqa: E402, F401
                         ShellCheck)
from shell_scan import CheckTooLarge  # noqa: E402

DEFAULT_PROTECTED_PATHS = [
    "**/*.p8", "**/*.p12", "**/*.pem", "**/*.key", "**/*.jks", "**/*.keystore",
    "**/*service-account*.json", "**/.env", "**/.env.*",
]
PUSH_POLICIES = ("ask", "deny", "allow")
# Deploys and destructive cloud commands: always ask, whatever pushPolicy says (with askCommands).
ALWAYS_ASK_COMMANDS = [
    r"\bfirebase\s+(deploy|firestore:delete|functions:delete|functions:config:set|hosting:disable|use)\b",
    # gcloud and the verb on one line; the lookahead pins the first gcloud (linear time).
    r"(?m)^(?=([^\n]*?\bgcloud\b))\1[^\n]*\b(deploy|delete|set-iam-policy|update)\b",
    r"\bterraform\s+(apply|destroy)\b",
    r"\bkubectl\s+(apply|delete)\b",
]
READ_VERBS = {
    "cat", "head", "tail", "less", "more", "bat", "cp", "mv", "scp", "rsync", "base64",
    "xxd", "od", "hexdump", "strings", "openssl", "keytool", "jq", "sed", "awk", "grep",
    "rg", "python", "python3", "node", "ruby", "perl", "curl", "pbcopy", "tee", "zip", "tar",
}
# preset -> (reset patterns, pgrep pattern of the build that makes a reset unsafe)
MACHINE_WIDE_RESETS = {
    "apple-simulators": ([
        r"simctl\s+shutdown\s+all\b",
        r"simctl\s+(--set\s+\S+\s+)?(delete|erase)\s+all\b",
        r"\bkillall\s+(Simulator|xcodebuild)\b",
    ], "xcodebuild"),
    "android-emulators": ([
        r"adb\s+emu\s+kill\b",
        r"\bkillall\s+qemu-system\S*",
    ], "gradlew"),
}
# Files that switch a preset on by themselves: (suffixes, exact names).
PRESET_MARKERS = {
    "apple-simulators": ((".xcodeproj", ".xcworkspace"), ()),
    "android-emulators": ((), ("gradlew",)),
}
SKIP_DIRS = ("node_modules",)  # plus every dot folder: never listed one level down
NOTICES = []  # warnings from the last check(); guard.py shows them as systemMessage




def matches(path, globs, cwd, roots=()):
    """Does a path hit one of the globs (also used by the Read and Edit guards)?"""
    if not path or os.path.basename(path).endswith((".example", ".sample", ".template")):
        return False
    path = os.path.expanduser(path)
    absolute = os.path.abspath(os.path.join(cwd or os.getcwd(), path))
    rel = os.path.relpath(absolute, cwd) if cwd else absolute
    candidates = {path, absolute, rel, os.path.basename(path)}
    for root in roots or ():
        candidates.add(os.path.relpath(absolute, root))
    for g in globs:
        variants = {g, g.replace("**/", "")}
        for c in candidates:
            for v in variants:
                if fnmatch.fnmatch(c, v) or fnmatch.fnmatch(c, "*/" + v):
                    return True
    return False


def preset_active(name, cfg, roots, cwd=None):
    """Is a device preset on? Explicit "on" beats "off"; else auto-on when a root or one of its
    direct subfolders holds the preset's marker file. A listing that fails counts as on (and adds
    a notice). A cheap listing only: no deep walk."""
    if name in cfg.get("presets", []):
        return True
    if name in cfg.get("disabledPresets", []):
        return False
    suffixes, names = PRESET_MARKERS[name]

    def hit(entries):
        return any(e.endswith(suffixes) if suffixes else e in names for e in entries)

    def fail(path, err):
        NOTICES.append(f"[dev-harness] could not list {path} ({type(err).__name__}) to look for "
                       f"a mobile project; treating the {name} preset as ON")
        return True

    folders = list(dict.fromkeys(list(roots or ()) + harness_config.detection_roots(cwd)))
    if not folders:
        NOTICES.append("[dev-harness] could not find the project folder to look for a mobile "
                       f"project; treating the {name} preset as ON")
        return True
    for root in folders:
        try:
            with os.scandir(root) as it:
                entries = list(it)
        except OSError as e:
            return fail(root, e)
        if hit(e.name for e in entries):
            return True
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS or e.name.endswith(
                    (".xcodeproj", ".xcworkspace")):
                continue
            try:
                if not e.is_dir():
                    continue
                with os.scandir(e.path) as it:
                    if hit(x.name for x in it):
                        return True
            except OSError as err:
                return fail(e.path, err)
    return False


def other_build_running(pattern="xcodebuild"):
    # Idle Gradle daemons live for hours, so only active builds/tests count.
    try:
        out = subprocess.run(["pgrep", "-f", pattern],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return bool(out.strip())


def check(cmd, cfg, cwd, roots):
    """("block" | "ask", message) for the first rule a Bash command hits, or None. The rules run
    in this order: device resets, credential reads, the shell check (push policy, then protected
    configs), always-ask commands. A shell check that cannot decide asks."""
    del NOTICES[:]
    if not cmd:
        return None
    for preset, (patterns, busy) in MACHINE_WIDE_RESETS.items():
        if not any(re.search(p, cmd) for p in patterns):
            continue
        if preset_active(preset, cfg, roots, cwd) and other_build_running(busy):
            return ("block", "Machine-wide simulator/emulator reset blocked while another build or "
                    "test is running (it would kill another agent's devices). Shut down or delete "
                    "only the devices you created, by UDID/name. "
                    f"(Rule: preset {preset}; switch it off with disabledPresets in harness.json.)"
                    + "".join(" " + n for n in NOTICES))
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    words = {os.path.basename(t) for t in tokens}
    protected = DEFAULT_PROTECTED_PATHS + cfg.get("protectedPaths", [])
    if words & READ_VERBS:
        for t in tokens:
            if t.startswith("-"):
                continue
            if matches(t, protected, cwd, roots):
                return ("block", f"Command would read or copy a protected credential path ({t}). "
                        "Report paths only; never print or copy credential contents.")
    try:
        shell = ShellCheck(cfg, cwd, roots)
        shell.run(cmd)
        git_state = git_state_hits(shell)
        commit_dirs = commit_folders(shell)
    except CheckTooLarge:
        return ("ask", "Command too large to check; approve manually. The push policy and "
                "protected-config checks did not finish within their work budget. "
                f"Command: {cmd[:200]}")
    except Exception as e:  # fail closed: a check that cannot decide asks
        return ("ask", f"Command could not be checked ({type(e).__name__}); approve manually. "
                f"Command: {cmd[:200]}")
    if shell.publish:
        policy = push_policy(cfg)
        if policy == "deny":
            return ("block", 'pushPolicy is "deny": git push and GitHub publishing (gh pr '
                    "create/merge, gh repo create/delete, gh release create, gh gist create) are "
                    "blocked. The owner pushes by hand; report what is ready instead. "
                    f"Command: {cmd[:200]}")
        if policy != "allow":
            return ("ask", f"Publishing command (pushPolicy \"{policy}\") needs the owner's "
                    f"explicit OK: {cmd[:200]}")
        if push_policy_files_changed(cwd):
            return ("ask", 'pushPolicy is "allow", but the push policy file changed in the '
                    "working tree (a harness.json is modified, untracked, deleted or ignored, or "
                    f"git could not tell). Approve this publish manually. Command: {cmd[:200]}")
    if shell.config_hit:
        return ("ask", "Command looks like it writes, moves, deletes or changes the mode of a "
                f"protected config ({shell.config_hit}: lint/test thresholds, validators, or "
                "guardrails such as harness.json). Fix the code instead of weakening the check, "
                f"unless the owner approves. Command: {cmd[:200]}")
    if git_state:
        ask = git_state_ask(git_state, cwd, cmd)
        if ask:
            return ask
    if commit_dirs:
        ask = commit_ask(commit_dirs, cfg, cmd, roots)
        if ask:
            return ask
    # Reached under "allow" too: a deploy or askCommand chained onto a push still asks.
    for pattern in ALWAYS_ASK_COMMANDS + [re.escape(c) for c in cfg.get("askCommands", [])]:
        if re.search(pattern, cmd):
            return ("ask", "Outward-facing or destructive command needs the owner's explicit OK: "
                    f"{cmd[:200]}")
    return None


def push_policy(cfg):
    """The configured pushPolicy; anything unexpected counts as "ask" (fail closed)."""
    policy = cfg.get("pushPolicy", "ask")
    return policy if policy in PUSH_POLICIES else "ask"


def push_policy_files_changed(cwd):
    """True unless git confirms that no harness.json in the repository (and none of the loaded
    ones) differs from HEAD: modified, staged, deleted, untracked or ignored all count, and so
    does an index entry marked assume-unchanged or skip-worktree (which hides a change from
    status). A git error counts as changed, so "allow" only skips the prompt with committed
    config files."""
    deadline = time.monotonic() + 3
    try:
        paths, _ = harness_config.locate(cwd)
        git = ["git", "--no-optional-locks", "-C", os.path.realpath(cwd or os.getcwd())]
        specs = ["--", ":(top,glob)**/.claude/harness.json", *(os.path.realpath(p) for p in paths)]
        status = subprocess.run(git + ["status", "--porcelain", "--ignored",
                                       "--untracked-files=all"] + specs,
                                capture_output=True, text=True, timeout=3)
        if status.returncode != 0 or status.stdout.strip():
            return True
        listed = subprocess.run(git + ["ls-files", "-v"] + specs, capture_output=True, text=True,
                                timeout=max(0.1, deadline - time.monotonic()))
    except Exception:
        return True
    if listed.returncode != 0:
        return True
    return any(line[:1].islower() or line.startswith("S ") for line in listed.stdout.splitlines())


# --- git state: `git stash` with several worktrees, and `core.hooksPath` ---------------------
# Regexes read the full text (the structural rule); a hit inside a data heredoc body is dropped.
STASH_REGEX = re.compile(r"\bgit\b" + _OPTS + r"\s+stash\b(?!\s+(?:list|show)\b)", re.IGNORECASE)
GIT_CONFIG_REGEX = re.compile(r"\bgit\b" + _OPTS + r"\s+config\b([^\n;&|]{0,500})", re.IGNORECASE)
GIT_HOOKS_OPTION_REGEX = re.compile(
    r"\bgit\b[^\n;&|]{0,300}?\s(?:-c\s*|--config-env[=\s])['\"]?core\.hookspath\b", re.IGNORECASE)
CONFIG_VALUE_OPTIONS = {"-f", "--file", "--blob", "--type", "-t", "--default"}
CONFIG_READ_OPTIONS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list"}
CONFIG_CHANGE_OPTIONS = {"--unset", "--unset-all", "--replace-all", "--add"}
WORKTREE_SECONDS = 2


def hooks_path_write(args):
    """Do the words after `git config` set or unset core.hooksPath? A read (`--get`, `--list`,
    the key alone) is not a write. Git keys ignore case."""
    positional, flags, i = [], set(), 0
    while i < len(args):
        w = args[i]
        if w.startswith("-") and w != "-":
            flags.add(w)
            i += 2 if w in CONFIG_VALUE_OPTIONS else 1
        else:
            positional.append(w)
            i += 1
    keys = [k for k, w in enumerate(positional) if w.lower() == "core.hookspath"]
    if not keys or flags & CONFIG_READ_OPTIONS:
        return False
    if flags & CONFIG_CHANGE_OPTIONS or "unset" in (w.lower() for w in positional[:keys[0]]):
        return True
    return len(positional) > keys[0] + 1  # key and value; the key alone reads


def git_calls(seg):
    """(global options as (option, value), words from the subcommand on) for each `git` in a
    simple command."""
    for i, w in enumerate(seg):
        if os.path.basename(w).lower() != "git":
            continue
        opts, j = [], i + 1
        while j < len(seg) and seg[j].startswith("-"):
            if seg[j] in GIT_OPTIONS_WITH_VALUE:
                opts.append((seg[j], seg[j + 1] if j + 1 < len(seg) else ""))
                j += 2
            else:
                opts.append((seg[j], ""))
                j += 1
        yield opts, seg[j:]


def hooks_option(opts):
    for opt, value in opts:
        if opt == "-c" or opt == "--config-env":
            key = value
        elif opt.startswith("--config-env="):
            key = opt.split("=", 1)[1]
        else:
            continue
        if key.split("=", 1)[0].lower() == "core.hookspath":
            return True
    return False


def git_state_hits(shell):
    """What a checked command does to shared git state, from the words and text ShellCheck
    already read: {"stash_dirs": folders to count worktrees in or None, "hooks_path": bool}, or
    None. Costs work-budget steps; raises CheckTooLarge like the rest of the shell check."""
    stash_dirs, stash, hooks = [], False, False
    for seg, extra in shell.segments:
        shell.tick()
        for opts, rest in git_calls(seg):
            sub = rest[0].lower() if rest else ""
            if hooks_option(opts):
                hooks = True
            if sub == "config" and hooks_path_write(rest[1:]):
                hooks = True
            if sub == "stash" and (len(rest) < 2 or rest[1].lower() not in ("list", "show")):
                stash = True
                stash_dirs += extra or [shell.cwd]
    for full, spans in shell.texts:
        def counts(m):
            return not any(a <= m.start() and m.end() <= b for a, b in spans)
        for m in STASH_REGEX.finditer(full):
            shell.tick()
            if counts(m) and not stash:
                stash = True
        for m in GIT_HOOKS_OPTION_REGEX.finditer(full):
            shell.tick()
            hooks = hooks or counts(m)
        for m in GIT_CONFIG_REGEX.finditer(full):
            shell.tick()
            words = [w.strip("'\"") for w in m.group(1).split()]
            hooks = hooks or (counts(m) and hooks_path_write(words))
    if not stash and not hooks:
        return None
    return {"stash": stash, "stash_dirs": stash_dirs, "hooks_path": hooks}


def worktree_count(folder):
    """Number of worktrees of the repository at folder, or None when git cannot say."""
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-C", folder, "worktree", "list",
                              "--porcelain"], capture_output=True, text=True,
                             timeout=WORKTREE_SECONDS)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    blocks = out.stdout.strip().split("\n\n")
    return sum(1 for b in blocks if b.startswith("worktree ") and "\nbare" not in "\n" + b)


def git_state_ask(hits, cwd, cmd):
    if hits["hooks_path"]:
        return ("ask", "Command sets or unsets core.hooksPath, which replaces .git/hooks and "
                "silently disables hooks such as Git LFS's pre-push. Needs the owner's OK. "
                f"Command: {cmd[:200]}")
    if hits["stash"]:
        base = os.path.abspath(cwd or os.getcwd())
        folders = list(dict.fromkeys(
            os.path.abspath(f) for f in hits["stash_dirs"])) or [base]
        if len(folders) > 4:
            folders = folders[:4] + [None]
        for folder in folders:
            count = worktree_count(folder) if folder else None
            if count is None:
                return ("ask", "git stash: could not count worktrees (the stash is shared by "
                        f"every worktree of a repo); approve manually. Command: {cmd[:200]}")
            if count > 1:
                return ("ask", f"git stash in a repo with {count} worktrees: the stash is shared "
                        "by all of them, so another agent could pop or drop it. Needs the "
                        f"owner's OK. Command: {cmd[:200]}")
    return None


# --- git commit: ask when it would add generated or ignored-looking files (CH-05) --------------
GENERATED_DIRS = {"node_modules", "build", "dist", ".gradle", "DerivedData", "__pycache__",
                  ".worktrees"}
GENERATED_SUFFIXES = (".pyc", ".class", ".o")
COMMIT_GIT_SECONDS = 3
COMMIT_FOLDER_LIMIT = 4
LISTED_PATHS = 10
NOT_CHECKED = "[dev-harness] could not check staged files"


def _notice(text):
    msg = f"{NOT_CHECKED}: {text}"
    if msg not in NOTICES:
        NOTICES.append(msg)


def commit_folders(shell):
    """Folders (`git -C` targets, else the working directory) of each `git commit` in the command,
    from the segments ShellCheck already read. A commit whose folder cannot be known (after a
    `cd`, or with --git-dir / --work-tree / GIT_DIR= / GIT_WORK_TREE=) is not checked and adds a
    notice instead."""
    folders, seen_cd = [], False
    for seg, extra in shell.segments:
        shell.tick()
        if seg and os.path.basename(seg[0]).lower() in ("cd", "pushd"):
            seen_cd = True
        for opts, rest in git_calls(seg):
            if not (rest and rest[0].lower() == "commit"):
                continue
            if any(o in ("--git-dir", "--work-tree") or o.startswith(("--git-dir=", "--work-tree="))
                   for o, _ in opts) or any(
                    w.split("=", 1)[0] in ("GIT_DIR", "GIT_WORK_TREE") for w in seg):
                _notice("commit with --git-dir, --work-tree or GIT_DIR: staged files not checked")
            elif seen_cd and not extra:
                _notice("commit after cd: staged files not checked")
            else:
                folders += extra or [shell.cwd]
    return list(dict.fromkeys(os.path.abspath(f) for f in folders))


def _git_bytes(folder, args, deadline, data=None):
    left = deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired("git", COMMIT_GIT_SECONDS)
    return subprocess.run(["git", "--no-optional-locks", "-C", folder, *args], input=data,
                          capture_output=True, timeout=left)


def _git_error(result):
    lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[0] if lines else f"git exited with {result.returncode}"


def generated_looking(path, boards):
    parts = path.split("/")
    return (path in boards or any(p in GENERATED_DIRS for p in parts[:-1])
            or parts[-1] in GENERATED_DIRS  # a submodule or file named like one
            or path.endswith(GENERATED_SUFFIXES))


def board_paths(board_path, root, project_roots):
    """The configured board output as repo-relative paths (a set): an absolute value as is, a
    relative one against each project root (the folder holding harness.json), else the repo."""
    if os.path.isabs(board_path):
        candidates = [os.path.realpath(board_path)]
    else:
        bases = [os.path.realpath(b) for b in project_roots or ()] or [root]
        candidates = [os.path.realpath(os.path.join(b, board_path)) for b in bases]
    found = set()
    for c in candidates:
        rel = os.path.relpath(c, root)
        if not rel.startswith(".."):
            found.add(rel.replace(os.sep, "/"))
    if not found and not os.path.isabs(board_path):  # a project root outside this repo
        found.add(posixpath.normpath(board_path.replace(os.sep, "/")))
    return found


def staged_generated(folder, board_path, project_roots, deadline, found):
    """Add to found the paths the commit in folder would add that look generated or are ignored.
    Hits from the path rules are kept in found even if a later git call raises."""
    top = _git_bytes(folder, ["rev-parse", "--show-toplevel"], deadline)
    if top.returncode != 0:
        raise RuntimeError(_git_error(top))
    root = os.path.realpath(top.stdout.decode("utf-8", "surrogateescape").strip())
    # --no-renames: a rename or copy into dist/ is an add (diff.renames could hide it).
    diff = _git_bytes(root, ["diff", "--cached", "--no-renames", "--name-only",
                             "--diff-filter=A", "-z"], deadline)
    if diff.returncode != 0:
        raise RuntimeError(_git_error(diff))
    added = [p for p in diff.stdout.split(b"\0") if p]
    boards = board_paths(board_path, root, project_roots) if board_path else set()
    names = [p.decode("utf-8", "surrogateescape") for p in added]
    rest = []
    for raw, n in zip(added, names):
        if generated_looking(n, boards):
            if n not in found:
                found.append(n)
        else:
            rest.append(raw)
    if rest:
        ignored = _git_bytes(root, ["check-ignore", "--no-index", "--stdin", "-z"], deadline,
                             b"\0".join(rest) + b"\0")
        if ignored.returncode not in (0, 1):
            raise RuntimeError(_git_error(ignored))
        flagged = {p.decode("utf-8", "surrogateescape") for p in ignored.stdout.split(b"\0") if p}
        found += [n for n in names if n in flagged and n not in found]


def commit_ask(folders, cfg, cmd, roots=()):
    """("ask", message) when a `git commit` would add generated or ignored-looking files. When git
    cannot say, a notice is added (never silent); hits found before the failure still ask."""
    tickets = cfg.get("tickets")
    board = tickets.get("boardPath") if isinstance(tickets, dict) else None
    if not isinstance(board, str):
        board = harness_config.DEFAULTS["tickets"]["boardPath"]
    board = board.strip()
    if board in ("", "."):
        _notice("tickets.boardPath is empty or \".\", so the board output is not checked")
        board = ""
    deadline = time.monotonic() + COMMIT_GIT_SECONDS
    found = []
    if len(folders) > COMMIT_FOLDER_LIMIT:
        _notice(f"{len(folders) - COMMIT_FOLDER_LIMIT} folders not checked (more than "
                f"{COMMIT_FOLDER_LIMIT} commits)")
        folders = folders[:COMMIT_FOLDER_LIMIT]
    for folder in folders:
        try:
            staged_generated(folder, board, roots, deadline, found)
        except subprocess.TimeoutExpired:
            _notice(f"git took longer than {COMMIT_GIT_SECONDS} s")
        except Exception as e:
            _notice(str(e) or type(e).__name__)
    if not found:
        return None
    shown = ", ".join(found[:LISTED_PATHS])
    more = f" (and {len(found) - LISTED_PATHS} more)" if len(found) > LISTED_PATHS else ""
    return ("ask", f"git commit would add {len(found)} generated or ignored-looking file(s): "
            f"{shown}{more}. Build output, caches and force-added ignored files usually do not "
            f"belong in a commit; unstage them or get the owner's OK. Command: {cmd[:200]}")
