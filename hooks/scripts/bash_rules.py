"""Bash rules of the dev-harness guard: credential reads, push policy, protected-config writes,
always-ask commands and machine-wide device resets.

check() returns the decision for one command, or None to allow; guard.py prints it and exits.
The shell text is read by shell_scan.py, and the push and config-write pass is ShellCheck in
shell_check.py. The structural rule: the publish and redirect regexes read the full text, and
scanner knowledge only removes a hit inside a proven data heredoc body (see ShellCheck.run).
"""
import fnmatch
import os
import re
import shlex
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:  # python3 -I omits it
    sys.path.insert(0, _HERE)
import harness_config  # noqa: E402
from shell_check import DEFAULT_PROTECTED_CONFIGS, ShellCheck  # noqa: E402, F401
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
MACHINE_WIDE_RESETS = [
    r"simctl\s+shutdown\s+all\b",
    r"simctl\s+(--set\s+\S+\s+)?(delete|erase)\s+all\b",
    r"adb\s+emu\s+kill\b",
    r"\bkillall\s+(Simulator|qemu-system\S*|xcodebuild)\b",
]




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
    if not cmd:
        return None
    for pattern in MACHINE_WIDE_RESETS:
        busy = "xcodebuild" if "simctl" in pattern or "Simulator" in pattern else "xcodebuild|gradlew"
        if re.search(pattern, cmd) and other_build_running(busy):
            return ("block", "Machine-wide simulator/emulator reset blocked while another build or "
                    "test is running (it would kill another agent's devices). Shut down or delete "
                    "only the devices you created, by UDID/name.")
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
