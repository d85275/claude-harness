"""Git hooks that rebuild the board, and their installer.

    dev-harness-board hook <name> [git hook args]          (run by the hook scripts)
    dev-harness-board install-hooks [--root DIR] [--check]
    dev-harness-board uninstall-hooks [--root DIR] [--check]

The hook scripts are copies of templates/githooks/hook.sh, kept in an untracked folder inside the
git common dir, `<git common dir>/dev-harness/hooks`, and `core.hooksPath` is set to its absolute
path. Nothing is committed: each clone opts in by running install-hooks, every worktree and branch
uses the same scripts, and no branch's tracked files can change them. Each script chains to the
repository's own `<git common dir>/hooks/<name>` and then calls `hook`, which rebuilds the board in
the main checkout only when the git operation touched the tracker, a ticket file or
`.claude/harness.json` (or when a worktree was added). `hook` always exits 0 and prints at most one
line on stderr.

The installer refuses a `core.hooksPath` that points anywhere else (any scope), a folder that holds
a file it did not write or a symlink, and a folder path that goes through a symlink. It never
touches `<git common dir>/hooks`, global or system config.
"""
import argparse
import fnmatch
import os
import re
import signal
import subprocess
import sys

from . import render

TEMPLATE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "templates", "githooks", "hook.sh")
MARKER = "# dev-harness-board hook"
FOLDER = ("dev-harness", "hooks")          # inside the git common dir
REBUILD_HOOKS = ("post-checkout", "post-commit", "post-merge", "post-rewrite")
ALWAYS = REBUILD_HOOKS + ("pre-push",)      # the four rebuild hooks plus Git LFS's pre-push
# Hook names git runs (githooks(5)); other files in .git/hooks are not hooks.
GIT_HOOKS = frozenset((
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
    "pre-push", "pre-receive", "update", "proc-receive", "post-receive", "post-update",
    "reference-transaction", "push-to-checkout", "pre-auto-gc", "post-rewrite",
    "sendemail-validate", "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist",
    "p4-post-changelist", "p4-pre-submit", "post-index-change"))
GIT_TIMEOUT = 5
MAX_REWRITTEN = 400
NULL_SHA = re.compile(r"0+")
# A rebase runs post-commit for every picked commit and post-rewrite once at the end.
# (A cherry-pick or revert of several commits runs no post-rewrite, so `sequencer` is not here.)
REBASE_DIRS = ("rebase-merge", "rebase-apply")
# Variables git sets for a hook that would point our own git calls at the wrong place.
REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
             "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")


def _git(args, cwd, input=None):
    """(exit code, stdout, stderr) of git in `cwd`; (None, "", reason) when it cannot run."""
    env = {k: v for k, v in os.environ.items() if k not in REPO_VARS}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, env=env, capture_output=True, text=True,
                           input=input, timeout=GIT_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return None, "", str(e)
    return r.returncode, r.stdout, r.stderr


# --- hook: decide and rebuild ----------------------------------------------------------------

def rebase_in_progress(cwd):
    code, out, _ = _git(["rev-parse", "--git-dir"], cwd)
    if code != 0 or not out.strip():
        return False
    gitdir = os.path.join(cwd, out.strip())
    return any(os.path.isdir(os.path.join(gitdir, d)) for d in REBASE_DIRS)


def changed_paths(name, args, stdin_text, cwd):
    """Repository paths the git operation behind hook `name` changed: a list, or None when
    unknown (the caller then rebuilds). `-m` lists a merge commit's changes against each parent."""
    args = list(args) + ["", "", ""]
    if name == "post-commit":
        cmd, data = ["diff-tree", "-z", "-r", "-m", "--name-only", "--no-commit-id", "--root", "HEAD"], None
    elif name == "post-merge":
        if args[0] == "1":
            return None                 # squash merge: the result is only in the working tree
        cmd, data = ["diff", "-z", "--name-only", "ORIG_HEAD", "HEAD"], None
    elif name == "post-checkout":
        prev, new, flag = args[:3]
        if flag != "1":
            return []                   # checking out files changes no branch
        if not prev or NULL_SHA.fullmatch(prev) or prev == new:
            return None                 # worktree add, or a new branch (it may be feature/<id>)
        cmd, data = ["diff", "-z", "--name-only", prev, new], None
    elif name == "post-rewrite":
        shas = [w for line in (stdin_text or "").splitlines() for w in line.split()[:2]]
        if not shas or len(shas) > MAX_REWRITTEN or not all(re.fullmatch(r"[0-9a-f]{7,64}", s) for s in shas):
            return None
        # old and new commits: a dropped commit can change the tracker as much as a new one
        cmd = ["diff-tree", "-z", "-r", "-m", "--name-only", "--no-commit-id", "--root", "--stdin"]
        data = "\n".join(shas) + "\n"
    else:
        return []
    code, out, _ = _git(cmd, cwd, data)
    if code != 0:
        return None
    return [p for p in out.split("\0") if p.strip()]


def _norm(p):
    return os.path.normpath(p).replace(os.sep, "/").lstrip("/") if p else ""


def touches_tickets(paths, cfg):
    """Does any of `paths` (relative to the repository top) feed the board?"""
    exact = {_norm(cfg["trackerPath"]), ".claude/harness.json"}
    if cfg.get("archiveTracker"):
        exact.add(_norm(cfg["archiveTracker"]))
    dirs = [_norm(d) for d in list(cfg["dirs"]) + list(cfg.get("archiveDirs") or [])]
    globs = list(cfg.get("featureGlobs") or [])
    for p in paths:
        p = _norm(p)
        if p in exact:
            return True
        if not p.lower().endswith(".md"):
            continue
        if any(d in ("", ".") or p.startswith(d + "/") for d in dirs):
            return True
        if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.dirname(p), g) for g in globs):
            return True
    return False


def _one_line(text):
    return " ".join(str(text).split())[:300]


def run_hook(argv, out, err):
    """`dev-harness-board hook <name> [args]`: rebuild the board when needed. Always returns 0,
    except when stopped by SIGTERM (the hook's time limit): 143, reported by the hook script."""
    from . import cli
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # cleans up the temp board file
    except (ValueError, OSError):
        pass
    try:
        if not argv or argv[0] not in REBUILD_HOOKS:
            return 0
        name, args = argv[0], argv[1:]
        cwd = os.getcwd()
        here = cli.find_root(cwd)
        root = cli.worktrees.main_checkout(here) or here
        cfg, errors = cli.load_tickets_config(root)
        if any(f in e for e in errors for f in cli.FATAL_CONFIG):
            err.write("board hook: " + _one_line(errors[0]) + "\n")
            return 0
        if name == "post-commit" and rebase_in_progress(cwd):
            return 0                    # post-rewrite rebuilds once the rebase is done
        stdin_text = sys.stdin.read(1 << 20) if name == "post-rewrite" and not sys.stdin.isatty() else ""
        paths = changed_paths(name, args, stdin_text, cwd)
        if paths is not None and not touches_tickets(paths, cfg):
            return 0
        path = os.path.abspath(os.path.join(root, cfg["boardPath"]))
        problem = cli.board_outside(root, path)
        if problem:
            err.write("board hook: " + _one_line(problem) + "\n")
            return 0
        model = cli.build_model(root, cfg, live=True)
        html = render.build_html(model, root, cfg, render.now_utc(), render.read_commit(root))
        render.write_atomic(path, html)
    except Exception as e:              # never fail the git command
        err.write("board hook: " + _one_line(f"{type(e).__name__}: {e}") + "\n")
    return 0


# --- install-hooks / uninstall-hooks -----------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _setup(prog, argv, err):
    """(args, main checkout, common dir) or (None, exit code, None) after printing why."""
    from . import cli
    ap = _Parser(prog=f"dev-harness-board {prog}", add_help=False)
    ap.add_argument("--root")
    ap.add_argument("--check", action="store_true")
    try:
        args = ap.parse_args(argv)
    except ValueError as e:
        err.write(f"dev-harness-board {prog}: {e}\nusage: dev-harness-board {prog} [--root DIR] [--check]\n")
        return None, 2, None
    start = os.path.abspath(args.root) if args.root else cli.find_root(os.getcwd())
    root = cli.worktrees.main_checkout(start) if os.path.isdir(start) else None
    common = common_dir(root) if root else None
    if root is None or common is None:
        err.write(f"dev-harness-board {prog}: {start} is not in a git checkout\n")
        return None, 2, None
    return args, root, common


def common_dir(root):
    """Absolute git common dir of the checkout at `root` (symlinks not resolved), or None."""
    code, out, _ = _git(["rev-parse", "--git-common-dir"], root)
    if code != 0 or not out.strip():
        return None
    return os.path.abspath(os.path.join(root, out.rstrip("\n")))


def hooks_folder(common):
    return os.path.join(common, *FOLDER)


def hooks_path(root):
    """(value, scope) of the effective core.hooksPath, or (None, None) when unset."""
    code, out, _ = _git(["config", "--show-scope", "--get", "core.hooksPath"], root)
    if code == 0 and "\t" in out:
        scope, value = out.rstrip("\n").split("\t", 1)
        return value, scope
    if code not in (0, 1):              # git before 2.26 has no --show-scope
        code, out, _ = _git(["config", "--get", "core.hooksPath"], root)
        if code == 0:
            return out.rstrip("\n"), "unknown"
    return None, None


def local_hooks_path(root):
    code, out, _ = _git(["config", "--local", "--get", "core.hooksPath"], root)
    return out.rstrip("\n") if code == 0 else None


def is_ours(value, folder):
    """Is a core.hooksPath value exactly our folder (an absolute path to it)?"""
    if not value:
        return False
    v = os.path.expanduser(value)
    return os.path.isabs(v) and os.path.realpath(v) == os.path.realpath(folder)


def is_stale(value):
    """A dev-harness hooks folder that no longer exists (for example after the repo moved)."""
    if not value:
        return False
    v = os.path.normpath(os.path.expanduser(value)).replace(os.sep, "/")
    return os.path.isabs(v) and v.endswith("/" + "/".join(FOLDER)) and not os.path.lexists(v)


def stale_warning(root):
    """One warning line when this repository's core.hooksPath names a missing dev-harness folder."""
    value = local_hooks_path(root)
    if is_stale(value):
        return (f"warning: core.hooksPath points at {value}, a dev-harness hooks folder that no longer "
                "exists, so git runs no hooks here (Git LFS included); run dev-harness-board "
                "install-hooks to repair it")
    return None


def own_hooks(common):
    """Names of executable hooks in the repository's own hooks folder (<common dir>/hooks)."""
    folder = os.path.join(common, "hooks")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names if n in GIT_HOOKS and os.path.isfile(os.path.join(folder, n))
                  and os.access(os.path.join(folder, n), os.X_OK))


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def is_our_file(text):
    return text is not None and MARKER in "".join(text.splitlines(True)[:3])


def folder_problem(common, folder):
    """Why we may not write into `folder`, or None. Every entry must be a regular file we wrote,
    and neither the common dir nor any folder on the way may be a symlink."""
    for p in (common, os.path.dirname(folder), folder):
        if os.path.islink(p):
            return f"{p} is a symlink"
    for p in (os.path.dirname(folder), folder):
        if os.path.lexists(p) and not os.path.isdir(p):
            return f"{p} is not a folder"
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"cannot list {folder}: {e}"
    for n in names:
        p = os.path.join(folder, n)
        if os.path.islink(p):
            return f"{p} is a symlink"
        if not os.path.isfile(p) or not is_our_file(_read(p)):
            return f"{p} was not written by dev-harness"
    return None


def _write_exec(path, text):
    render.write_atomic(path, text)
    os.chmod(path, 0o755)


def run_install(argv, out, err):
    args, root, common = _setup("install-hooks", argv, err)
    if args is None:
        return root
    check = args.check
    would = "would " if check else ""
    folder = hooks_folder(common)
    template = _read(TEMPLATE)
    if template is None or not is_our_file(template):
        err.write(f"dev-harness-board install-hooks: cannot read the hook template {TEMPLATE}\n")
        return 2
    value, scope = hooks_path(root)
    stale = scope == "local" and is_stale(value)
    if stale:
        err.write(stale_warning(root) + "\n")
    elif value is not None and not is_ours(value, folder):
        err.write(
            f"dev-harness-board install-hooks: core.hooksPath is already {value!r} ({scope} config), so "
            f"git runs the hooks there. Nothing was changed. To use the board hooks, move those hooks "
            f"into {os.path.join(common, 'hooks')} (the board hooks run them first, with the same "
            f"arguments), unset core.hooksPath, and run install-hooks again.\n")
        return 1
    problem = folder_problem(common, folder)
    if problem:
        err.write(f"dev-harness-board install-hooks: {problem}; nothing was changed. The folder holds "
                  "only the files install-hooks writes: move any other hook into "
                  f"{os.path.join(common, 'hooks')} (the board hooks run it), remove it from the folder, "
                  "and run install-hooks again.\n")
        return 1
    names = set(ALWAYS) | set(own_hooks(common))
    try:
        names |= set(n for n in os.listdir(folder) if n in GIT_HOOKS)   # ours from an earlier run
    except OSError:
        pass
    plan = []
    for n in sorted(names):
        path = os.path.join(folder, n)
        if _read(path) == template and os.access(path, os.X_OK):
            out.write(f"unchanged {path}\n")
        else:
            plan.append(n)
            out.write(f"{would}write {path}\n")
    for n in own_hooks(common):
        out.write(f"{n} runs {os.path.join(common, 'hooks', n)} first\n")
    if is_ours(value, folder):
        out.write(f"core.hooksPath already points at {folder} ({scope} config)\n")
        if check:
            return 0
    if check:
        out.write(f"would set core.hooksPath to {folder} (this repository only)\n")
        return 0
    try:
        os.makedirs(folder, exist_ok=True)
        problem = folder_problem(common, folder)        # nothing swapped in meanwhile
        if problem:
            raise OSError(problem)
        for n in plan:
            _write_exec(os.path.join(folder, n), template)
    except OSError as e:
        err.write(f"dev-harness-board install-hooks: cannot write the hooks: {e}\n")
        return 1
    if not is_ours(value, folder):
        code, _, e = _git(["config", "--local", "core.hooksPath", folder], root)
        if code != 0:
            err.write(f"dev-harness-board install-hooks: git config failed: {_one_line(e)}\n")
            return 1
        out.write(f"set core.hooksPath to {folder} (this repository only)\n")
    return 0


def run_uninstall(argv, out, err):
    args, root, common = _setup("uninstall-hooks", argv, err)
    if args is None:
        return root
    folder = hooks_folder(common)
    local = local_hooks_path(root)
    if local is None:
        value, scope = hooks_path(root)
        if value is not None:
            out.write(f"core.hooksPath is not set in this repository's config (it is {value!r} in {scope} "
                      "config); left alone\n")
        else:
            out.write("core.hooksPath is not set; nothing to do\n")
        return 0
    if not (is_ours(local, folder) or is_stale(local)):
        out.write(f"core.hooksPath is {local!r}, not {folder}; left alone\n")
        return 0
    if not args.check:
        code, _, e = _git(["config", "--local", "--unset-all", "core.hooksPath"], root)
        if code != 0:
            err.write(f"dev-harness-board uninstall-hooks: git config failed: {_one_line(e)}\n")
            return 1
    out.write(f"{'would unset' if args.check else 'unset'} core.hooksPath (was {local!r}); git runs "
              f"{os.path.join(common, 'hooks')} again. The scripts stay in {folder} (unused).\n")
    return 0
