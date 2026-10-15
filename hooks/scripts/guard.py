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
    "notify": true
  }
"""
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys

DEFAULT_PROTECTED_PATHS = [
    "**/*.p8", "**/*.p12", "**/*.pem", "**/*.key", "**/*.jks", "**/*.keystore",
    "**/*service-account*.json", "**/.env", "**/.env.*",
]
DEFAULT_PROTECTED_CONFIGS = [
    "**/.swiftlint.yml", "**/.swiftformat", "**/.eslintrc*", "**/eslint.config.*",
    "**/.prettierrc*", "**/detekt*.yml", "**/lint.xml", "**/.flake8", "**/ruff.toml",
    "**/.claude/settings.json", "**/.claude/settings.local.json", "**/.claude/harness.json",
    "**/*.xctestplan", "**/pytest.ini", "**/.coveragerc", "**/jest.config.*", "**/vitest.config.*",
    "**/codecov.yml",
]
# An edit that adds one of these switches a check off instead of fixing the code.
WEAKENING_MARKERS = [
    r"\bXCTSkip\b", r"\bperformAccessibilityAudit\b[^\n]*\{",
    r"@(unittest\.)?skip", r"\bpytest\.mark\.(skip|xfail)\b", r"\b(it|test|describe)\.skip\(",
    r"\bx(it|describe)\(", r"@Ignore\b", r"@Disabled\b", r"@Suppress(Lint|Warnings)?\b",
    r"\bswiftlint:disable\b", r"\beslint-disable", r"@ts-(ignore|nocheck|expect-error)\b",
    r"#\s*noqa\b", r"#\s*type:\s*ignore\b", r"\bnolint\b", r"--no-verify\b",
]
DEFAULT_ASK_COMMANDS = [
    r"\bgit\b(?:\s+-\S+(?:\s+\S+)?)*\s+push\b",
    r"\bgh\s+pr\s+(create|merge)\b",
    r"\bgh\s+repo\s+(create|delete)\b",
    r"\bgh\s+release\s+create\b",
    r"\bfirebase\s+(deploy|firestore:delete|functions:delete|functions:config:set|hosting:disable|use)\b",
    r"\bgcloud\b.*\b(deploy|delete|set-iam-policy|update)\b",
    r"\bterraform\s+(apply|destroy)\b",
    r"\bkubectl\s+(apply|delete)\b",
]
READ_VERBS = {
    "cat", "head", "tail", "less", "more", "bat", "cp", "mv", "scp", "rsync", "base64",
    "xxd", "od", "hexdump", "strings", "openssl", "keytool", "jq", "sed", "awk", "grep",
    "rg", "python", "python3", "node", "ruby", "perl", "curl", "pbcopy", "tee", "zip", "tar",
}
MACHINE_WIDE_RESETS = [
    r"simctl\s+shutdown\s+all\b",
    r"simctl\s+(--set\s+\S+\s+)?(delete|erase)\s+all\b",
    r"adb\s+emu\s+kill\b",
    r"\bkillall\s+(Simulator|qemu-system\S*|xcodebuild)\b",
]


def load_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def load_config(cwd):
    path = os.path.join(cwd or os.getcwd(), ".claude", "harness.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def matches(path, globs, cwd):
    if not path or os.path.basename(path).endswith((".example", ".sample", ".template")):
        return False
    path = os.path.expanduser(path)
    absolute = os.path.abspath(os.path.join(cwd or os.getcwd(), path))
    rel = os.path.relpath(absolute, cwd) if cwd else absolute
    candidates = {path, absolute, rel, os.path.basename(path)}
    for g in globs:
        variants = {g, g.replace("**/", "")}
        for c in candidates:
            for v in variants:
                if fnmatch.fnmatch(c, v) or fnmatch.fnmatch(c, "*/" + v):
                    return True
    return False


def block(msg):
    print(f"[dev-harness] {msg}", file=sys.stderr)
    sys.exit(2)


def ask(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"[dev-harness] {reason}",
    }}))
    sys.exit(0)


def other_build_running(pattern="xcodebuild"):
    # Idle Gradle daemons live for hours, so only active builds/tests count.
    try:
        out = subprocess.run(["pgrep", "-f", pattern],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return bool(out.strip())


def guard_bash(data, cfg):
    cmd = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd")
    if not cmd:
        return
    for pattern in MACHINE_WIDE_RESETS:
        busy = "xcodebuild" if "simctl" in pattern or "Simulator" in pattern else "xcodebuild|gradlew"
        if re.search(pattern, cmd) and other_build_running(busy):
            block("Machine-wide simulator/emulator reset blocked while another build or test is "
                  "running (it would kill another agent's devices). Shut down or delete only the "
                  "devices you created, by UDID/name.")
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
            if matches(t, protected, cwd):
                block(f"Command would read or copy a protected credential path ({t}). "
                      "Report paths only; never print or copy credential contents.")
    for pattern in DEFAULT_ASK_COMMANDS + [re.escape(c) for c in cfg.get("askCommands", [])]:
        if re.search(pattern, cmd):
            ask(f"Outward-facing or destructive command needs the owner's explicit OK: {cmd[:200]}")


def guard_read(data, cfg):
    path = (data.get("tool_input") or {}).get("file_path", "")
    protected = DEFAULT_PROTECTED_PATHS + cfg.get("protectedPaths", [])
    if matches(path, protected, data.get("cwd")):
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
    return [m for m in WEAKENING_MARKERS + list(extra)
            if len(re.findall(m, after)) > len(re.findall(m, before))]


def guard_edit(data, cfg):
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path", "")
    configs = DEFAULT_PROTECTED_CONFIGS + cfg.get("protectedConfigs", [])
    if matches(path, configs, data.get("cwd")):
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
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = load_input()
    cfg = load_config(data.get("cwd"))
    {"bash": guard_bash, "read": guard_read, "edit": guard_edit, "notify": notify}.get(
        mode, lambda d, c: None)(data, cfg)
    sys.exit(0)


if __name__ == "__main__":
    main()
