#!/usr/bin/env python3
"""dev-harness safety hooks.

Usage (from hooks.json): guard.py bash|read|edit|notify  < hook-input-json

Exit 0 = allow, exit 2 = block (stderr is shown to Claude). For "ask", print a
PreToolUse JSON decision on stdout so the owner is prompted.

Project overrides: <project>/.claude/harness.json
  {
    "protectedPaths":   ["android/keys/**", "backend/service-account.json"],
    "protectedConfigs": ["ios/scripts/verify_visual_parity_baseline.py"],
    "askCommands":      ["firebase firestore:delete", "./deploy.sh"],
    "checkWeakening":   true,
    "weakeningMarkers": ["\\bExclusion\\("],
    "notify": true,
    "pushPolicy": "ask"
  }
pushPolicy covers git push and GitHub publishing only: "ask" (default) prompts the owner,
"deny" blocks, "allow" leaves it to the user's own permission rules. Deploys, destructive
cloud commands and askCommands always ask.
All keys and defaults: hooks/scripts/harness_config.py. A malformed file or an unknown key
is reported on stderr; built-in rules still apply.

This file is the entry point: hook input, emit/ask/block, the crash handler, dispatch, and the
Read, Edit and Notification guards. The Bash rules are in bash_rules.py and the shell scanner
they use in shell_scan.py.
"""
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python3 -I omits it
# A module that cannot be imported (missing file, syntax error, bad install) must not end in exit 1,
# which Claude Code treats as allow: main() answers "ask" for Bash, Read and Edit instead.
IMPORT_ERROR = None
try:
    import harness_config  # noqa: E402
    import bash_rules  # noqa: E402
    import shell_scan  # noqa: E402
    from bash_rules import DEFAULT_PROTECTED_CONFIGS, DEFAULT_PROTECTED_PATHS, matches  # noqa: E402
except BaseException as _e:  # also SystemExit raised while importing
    IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

# An edit that adds one of these switches a check off instead of fixing the code.
WEAKENING_MARKERS = [
    r"\bXCTSkip\b", r"\bperformAccessibilityAudit\b[^\n]*\{",
    r"@(unittest\.)?skip", r"\bpytest\.mark\.(skip|xfail)\b", r"\b(it|test|describe)\.skip\(",
    r"\bx(it|describe)\(", r"@Ignore\b", r"@Disabled\b", r"@Suppress(Lint|Warnings)?\b",
    r"\bswiftlint:disable\b", r"\beslint-disable", r"@ts-(ignore|nocheck|expect-error)\b",
    r"#\s*noqa\b", r"#\s*type:\s*ignore\b", r"\bnolint\b", r"--no-verify\b",
]


# Set by load_input, load_config and the guards: text shown to the user and the model (exit 0
# stderr reaches neither), and the project root that holds harness.json (for root-relative globs).
CONFIG_WARNING = ""
PROJECT_ROOTS = []
MODE = ""
DATA = {}
CFG = None  # None until the config is loaded


def add_warning(text):
    global CONFIG_WARNING
    CONFIG_WARNING = " ".join(filter(None, [CONFIG_WARNING, text]))


def load_input():
    """The hook input as a dict. Unreadable input becomes {} plus a warning (the call is then not
    checked, and the user is told)."""
    try:
        data = json.load(sys.stdin)
        if isinstance(data, dict):
            return data
        problem = f"hook input is a JSON {type(data).__name__}, not an object"
    except Exception as e:
        problem = f"hook input could not be read ({type(e).__name__})"
    add_warning(f"[dev-harness] {problem}; this call was not checked.")
    return {}


def marker_dir():
    """A private folder for once-only markers. Raises when it cannot be trusted (a file or a symlink
    in its place, another owner, no permission): callers then show the warning."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    path = os.path.join(tempfile.gettempdir(), f"dev-harness-{uid}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass  # checked below: it may be a folder, a file or a symlink
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != uid):
        raise OSError(f"{path} is not a private folder")
    return path


def marker_path(key):
    return os.path.join(marker_dir(), hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:40])


def seen(key):
    """Was this key already delivered? Any trouble reading the marker folder counts as not seen:
    a warning is repeated, never lost."""
    try:
        os.lstat(marker_path(key))
        return True
    except Exception:
        return False


def record(key):
    """Remember that a warning was delivered. Failing to write is harmless (it repeats)."""
    try:
        os.close(os.open(marker_path(key), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except Exception:  # FileExistsError: another call recorded it first
        pass


PENDING = []  # (key, needs_model_context): markers to write once the warning has been printed


def warn_once(key, text, session, model=True):
    """Add a warning unless this session already got it. The marker is only written by emit(),
    after output that carries the warning (with `model`, one the model also sees) was printed, so
    a block or a notification never uses it up. No usable session id: warn every time."""
    usable = isinstance(session, str) and session != ""
    if usable and seen(key):
        return
    add_warning(text)
    if usable:
        PENDING.append((key, model))


def config_key(cwd, errors, session):
    """Identifies one session's view of these config problems: the problems themselves, each loaded
    file's mtime and size, and the session id."""
    parts = [str(session), *errors]
    for path in harness_config.locate(cwd)[0]:
        try:
            st = os.stat(path)
            parts.append(f"{path}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{path}:unreadable")
    return "config\0" + "\0".join(parts)


def load_config(cwd, session=""):
    """Config with defaults filled in; problems go on stderr every time and into CONFIG_WARNING once
    per session and config state (a marker keyed by the problems, file mtimes and session id)."""
    global PROJECT_ROOTS
    cfg, errors, PROJECT_ROOTS = harness_config.load_all(cwd)
    if errors:
        text = "[dev-harness] harness.json problems: " + "; ".join(errors)
        try:
            warn_once(config_key(cwd, errors, session), text, session)
        except Exception:
            add_warning(text)
        try:
            sys.stderr.write(harness_config.format_errors(errors))
        except Exception:  # stderr may be closed or None; the warning still goes to stdout
            pass
    return cfg


def emit(decision=None, reason=None, pre_tool_use=True):
    """Print exactly one JSON object on stdout (nothing when there is nothing to say)."""
    out = {}
    hook = {}
    if decision:
        hook.update(hookEventName="PreToolUse", permissionDecision=decision,
                    permissionDecisionReason=reason)
    if CONFIG_WARNING:
        out["systemMessage"] = CONFIG_WARNING  # shown to the user
        if pre_tool_use:
            hook.update(hookEventName="PreToolUse", additionalContext=CONFIG_WARNING)  # model
    if hook:
        out["hookSpecificOutput"] = hook
    if out:
        print(json.dumps(out))
        for key, model in PENDING:  # delivered: do not repeat it this session
            if pre_tool_use or not model:
                record(key)
    del PENDING[:]


def block(msg):
    # The config warning is already on stderr (load_config), so it is not repeated here.
    print(f"[dev-harness] {msg}", file=sys.stderr)
    sys.exit(2)


def ask(reason):
    suffix = f" {CONFIG_WARNING}" if CONFIG_WARNING else ""
    emit("ask", f"[dev-harness] {reason}{suffix}")
    sys.exit(0)


def guard_bash(data, cfg):
    """The first Bash rule the command hits (bash_rules.check) becomes a block or an ask."""
    cmd = (data.get("tool_input") or {}).get("command", "")
    result = bash_rules.check(cmd, cfg, data.get("cwd"), PROJECT_ROOTS)
    if bash_rules.NOTICES:  # e.g. a preset switched on because a folder could not be listed
        add_warning(" ".join(bash_rules.NOTICES))
    if result:
        decision, message = result
        if decision == "block":
            block(message)
        ask(message)


def guard_read(data, cfg):
    path = (data.get("tool_input") or {}).get("file_path", "")
    protected = DEFAULT_PROTECTED_PATHS + cfg.get("protectedPaths", [])
    if matches(path, protected, data.get("cwd"), PROJECT_ROOTS):
        block(f"Reading protected credential file blocked: {path}")


def edit_texts(tool_input, path):
    """(before, after) text of the change, as far as the hook input shows it."""
    if "edits" in tool_input:  # MultiEdit
        edits = tool_input.get("edits") or []
        return ("\n".join(e.get("old_string", "") for e in edits),
                "\n".join(e.get("new_string", "") for e in edits))
    if "content" in tool_input:  # Write replaces the whole file
        try:
            with open(path) as f:
                before = f.read()
        except Exception:
            before = ""
        return before, tool_input.get("content", "")
    return tool_input.get("old_string", ""), tool_input.get("new_string", "")


def added_weakening(before, after, extra=()):
    added = []
    for m in WEAKENING_MARKERS + list(extra):
        try:
            if len(re.findall(m, after)) > len(re.findall(m, before)):
                added.append(m)
        except re.error:  # a bad project pattern must not disable the other checks
            continue
    return added


def guard_edit(data, cfg):
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path", "")
    if matches(path, [bash_rules.HOOKS_FOLDER_GLOB], data.get("cwd"), PROJECT_ROOTS):
        ask(bash_rules.HOOKS_FOLDER_MESSAGE.format(path=path))  # CH-22: path shape, no git run
    configs = DEFAULT_PROTECTED_CONFIGS + cfg.get("protectedConfigs", [])
    if matches(path, configs, data.get("cwd"), PROJECT_ROOTS):
        ask(f"{path} is a protected config (lint/test thresholds, validators, or guardrails). "
            "Fix the code instead of weakening the check, unless the owner approves this edit.")
    if cfg.get("checkWeakening", True) is False:
        return
    absolute = os.path.join(data.get("cwd") or os.getcwd(), os.path.expanduser(path))
    added = added_weakening(*edit_texts(tool_input, absolute), cfg.get("weakeningMarkers", []))
    if added:
        ask(f"This edit to {path} adds a test skip, audit exclusion, or lint suppression "
            f"({', '.join(added[:3])}). Fix the code instead, or get the owner's OK and give a "
            "reason next to it.")


def notify(data, cfg):
    if cfg.get("notify", True) is False or sys.platform != "darwin":
        return
    message = data.get("message") or "Claude Code needs your attention"
    title = os.path.basename(data.get("cwd") or "") or "Claude Code"
    script = f'display notification {json.dumps(message[:180])} with title {json.dumps(title)}'
    try:
        done = subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
        problem = f"osascript exited with {done.returncode}" if done.returncode else None
    except Exception as e:
        problem = f"{type(e).__name__}: {e}"
    if problem:  # once per session, not per message
        warn_once(f"notify\0{data.get('session_id', '')}",
                  f"[dev-harness] desktop notifications are not working ({problem}); set "
                  '"notify": false in harness.json to silence this.', data.get("session_id"),
                  model=False)


CREDENTIAL_TEXT = re.compile(
    r"(?i)\.(?:p8|p12|pem|key|jks|keystore)(?![\w])|service-account|\.env(?:\.\w+)?(?![\w])")


def looks_credential(mode, data):
    """Does a crashed Read or Bash call look like it touches a credential path? Judged without the
    code that crashed: a plain text pattern, then the configured globs. When this cannot tell
    (odd input, config not loaded, too large, too slow), yes."""
    try:
        tool_input = data.get("tool_input") or {}
        text = tool_input.get("file_path") if mode == "read" else (
            tool_input.get("command") if mode == "bash" else "")
        if not text:
            return False
        if not isinstance(text, str) or CREDENTIAL_TEXT.search(text) or CFG is None:
            return True
        if len(text) > shell_scan.LONGEST_COMMAND:  # too large to look through
            return True
        protected = DEFAULT_PROTECTED_PATHS + CFG.get("protectedPaths", [])
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
        if mode == "read":
            words = [text]
        else:
            try:
                words = shlex.split(text)
            except ValueError:
                words = text.split()
        deadline = time.monotonic() + 3
        for w in dict.fromkeys(words):
            if time.monotonic() > deadline or matches(w, protected, cwd, PROJECT_ROOTS):
                return True
        return False
    except Exception:
        return True


def say(message):
    try:
        print(json.dumps({"systemMessage": message}))
    except Exception:
        try:
            sys.stderr.write(message + "\n")
        except Exception:
            pass


def guard_unavailable():
    """The guard modules did not import: ask for every guarded tool, never allow silently."""
    message = f"dev-harness guard could not load: {IMPORT_ERROR}"
    try:
        sys.stderr.write(f"{message}\n")
    except Exception:
        pass
    if (sys.argv[1] if len(sys.argv) > 1 else "") in ("bash", "read", "edit"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": f"{message}; this call was not checked. "
                                        "Approve manually or fix the plugin install."}}))
    else:
        say(message)
    sys.exit(0)


def main():
    if IMPORT_ERROR:
        guard_unavailable()
    try:
        run()
    except Exception as e:  # SystemExit (block/ask/allow) passes through
        try:
            message = f"[dev-harness] hook crashed: {type(e).__name__}: {e}; this call was not checked"
            if CONFIG_WARNING:
                message += " " + CONFIG_WARNING
            if MODE in ("bash", "read") and looks_credential(MODE, DATA):
                # owner decision: a credential-like call asks instead of passing unchecked
                print(json.dumps({"systemMessage": message, "hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "ask",
                    "permissionDecisionReason": message.replace(
                        "this call was not checked", "it looks like it touches a credential "
                        "path, so approve it manually")}}))
                sys.exit(0)
            say(message)
        except Exception:  # the crash handler itself failed: still say so, still exit 0
            say("[dev-harness] hook crashed and its crash handler failed; this call was not checked")
    sys.exit(0)


def run():
    global MODE, DATA, CFG
    MODE = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = MODE
    DATA = data = load_input()
    CFG = cfg = load_config(data.get("cwd"), data.get("session_id", ""))
    {"bash": guard_bash, "read": guard_read, "edit": guard_edit, "notify": notify}.get(
        mode, lambda d, c: None)(data, cfg)
    if mode in ("bash", "read", "edit", "notify"):  # allowed: only a config warning to show
        emit(pre_tool_use=mode != "notify")


if __name__ == "__main__":
    main()
