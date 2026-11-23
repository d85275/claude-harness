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
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python3 -I omits it
import harness_config  # noqa: E402
import bash_rules  # noqa: E402
from bash_rules import DEFAULT_PROTECTED_CONFIGS, DEFAULT_PROTECTED_PATHS, matches  # noqa: E402

# An edit that adds one of these switches a check off instead of fixing the code.
WEAKENING_MARKERS = [
    r"\bXCTSkip\b", r"\bperformAccessibilityAudit\b[^\n]*\{",
    r"@(unittest\.)?skip", r"\bpytest\.mark\.(skip|xfail)\b", r"\b(it|test|describe)\.skip\(",
    r"\bx(it|describe)\(", r"@Ignore\b", r"@Disabled\b", r"@Suppress(Lint|Warnings)?\b",
    r"\bswiftlint:disable\b", r"\beslint-disable", r"@ts-(ignore|nocheck|expect-error)\b",
    r"#\s*noqa\b", r"#\s*type:\s*ignore\b", r"\bnolint\b", r"--no-verify\b",
]


def load_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


# Set by load_config: text shown to the user and the model (exit 0 stderr reaches neither),
# and the project root that holds harness.json (for root-relative globs).
CONFIG_WARNING = ""
PROJECT_ROOTS = []


def load_config(cwd):
    """Config with defaults filled in; problems are kept in CONFIG_WARNING and put on stderr."""
    global CONFIG_WARNING, PROJECT_ROOTS
    cfg, errors, PROJECT_ROOTS = harness_config.load_all(cwd)
    if errors:
        CONFIG_WARNING = "[dev-harness] harness.json problems: " + "; ".join(errors)
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
    global CONFIG_WARNING
    result = bash_rules.check(cmd, cfg, data.get("cwd"), PROJECT_ROOTS)
    if bash_rules.NOTICES:  # e.g. a preset switched on because a folder could not be listed
        CONFIG_WARNING = " ".join(filter(None, [CONFIG_WARNING] + bash_rules.NOTICES))
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
        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
    except Exception:
        pass


def main():
    try:
        run()
    except Exception as e:  # SystemExit (block/ask/allow) passes through
        message = f"[dev-harness] hook crashed: {type(e).__name__}: {e}; this call was not checked"
        if CONFIG_WARNING:
            message += " " + CONFIG_WARNING
        try:
            print(json.dumps({"systemMessage": message}))
        except Exception:
            try:
                sys.stderr.write(message + "\n")
            except Exception:
                pass
    sys.exit(0)


def run():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = load_input()
    cfg = load_config(data.get("cwd"))
    {"bash": guard_bash, "read": guard_read, "edit": guard_edit, "notify": notify}.get(
        mode, lambda d, c: None)(data, cfg)
    if mode in ("bash", "read", "edit", "notify"):  # allowed: only a config warning to show
        emit(pre_tool_use=mode != "notify")


if __name__ == "__main__":
    main()
