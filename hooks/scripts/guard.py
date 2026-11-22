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
"""
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python3 -I omits it
import harness_config

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
# git/GitHub publishing: governed by pushPolicy ("ask" default, "deny" blocks, "allow" skips this
# hook's prompt). Case-insensitive because a case-insensitive filesystem runs `GIT push` too.
# Option values must not start with "-", and at most 8 options, so the loop cannot backtrack
# far; the word check below has no such limit.
_OPTS = r"(?:\s+-\S+(?:\s+[^-\s]\S*)?){0,8}"
PUBLISH_COMMANDS = [
    r"\bgit\b" + _OPTS + r"\s+push\b",
    r"\bgit-push\b",
    r"\bgh\b" + _OPTS + r"\s+(?:pr\s+(?:create|new|merge)|repo\s+(?:create|new|delete)"
    r"|release\s+(?:create|new)|gist\s+(?:create|new))\b",
]
# Global options that take the next word as their value (`git -C dir push`, `gh -R o/r pr new`).
GIT_OPTIONS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                          "--config-env"}
GH_OPTIONS_WITH_VALUE = {"-R", "--repo", "--hostname"}
GH_PUBLISH = {("pr", "create"), ("pr", "new"), ("pr", "merge"), ("repo", "create"),
              ("repo", "new"), ("repo", "delete"), ("release", "create"), ("release", "new"),
              ("gist", "create"), ("gist", "new")}
PUSH_POLICIES = ("ask", "deny", "allow")
# Shell writes to protected configs (harness.json, settings, protectedConfigs) ask, like an Edit.
CONFIG_WRITE_VERBS = {"cp", "mv", "install", "ln", "rsync", "dd", "truncate", "rm", "unlink",
                      "chmod", "chown", "chgrp", "touch", "ditto", "sponge", "unzip",
                      "vi", "vim", "nvim", "ex", "ed", "nano", "emacs"}
# Verbs whose target may be a folder holding the configs (`rm -rf .`, `git checkout -- .`).
CONFIG_TREE_VERBS = {"rm", "unlink", "chmod", "chown", "chgrp", "checkout", "restore"}
IN_PLACE_EDITORS = {"sed", "gsed", "perl", "ruby", "awk", "gawk"}  # with -i
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
INTERPRETER = re.compile(r"(?:python[\d.]*|node|nodejs|ruby|perl|bun|deno|swift|osascript)$")
CODE_OPTION = re.compile(r"-[a-zA-Z]*[ceE]|--eval|--print|-p")  # the next word is code
RAW_REDIRECT = re.compile(r"(?:>>?|>\||&>>?)[ \t]*[\"']?([^\s;&|<>()\"'`]+)")
PATHLIKE = re.compile(r"[\w.~/*?\[\]@+$%:-]+")
HEREDOC_TOKEN = re.compile(r"<<(-?)[ \t]*((?:[A-Za-z0-9_.\-]|'[^'\n]*'|\"[^\"\n]*\"|\\.)+)")
SCAN_SPECIAL = re.compile(r"[\\'\"#<>\n$`(]")
# A heredoc body may be left out of the checks only when it is plainly data: its command is one
# of these, nothing on the line pipes it on or runs it, and its delimiter is found.
_HD = r"(?:'[A-Za-z_]\w*'|\"[A-Za-z_]\w*\"|\\?[A-Za-z_]\w*)"
_FILE = r"[\w./~+-]+"
_WORD = r"[\w./~+:=@%,-]+"
DATA_HEREDOC = re.compile(
    r"\s*(?:git\s+(?:-C\s+" + _FILE + r"\s+)?(?:commit|tag)(?:\s+" + _WORD + r"){0,12}?"
    r"\s+(?:-F\s*-|--file(?:=|\s+)-)(?:\s+" + _WORD + r"){0,12}\s*<<-?\s*" + _HD +
    r"|cat\s*(?:>>?\s*" + _FILE + r"\s*<<-?\s*" + _HD + r"|<<-?\s*" + _HD + r"\s*>>?\s*" + _FILE + ")"
    r"|tee\s+(?:-a\s+)?" + _FILE + r"\s*<<-?\s*" + _HD + r")\s*")
CASE_INSENSITIVE = sys.platform == "darwin"  # the default macOS filesystem ignores case
CHECK_SECONDS = 1.5  # work budget for one Bash check; past it the command asks
CHECK_STEPS = 400000
LONGEST_WORD = 32768  # shlex is quadratic in the length of one word; longer ones ask
LONGEST_COMMAND = 262144  # longer Bash commands ask without being checked
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


def matches(path, globs, cwd, roots=()):
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
            if matches(t, protected, cwd, PROJECT_ROOTS):
                block(f"Command would read or copy a protected credential path ({t}). "
                      "Report paths only; never print or copy credential contents.")
    try:
        shell = ShellCheck(cfg, cwd)
        shell.run(cmd)
    except CheckTooLarge:
        ask("Command too large to check; approve manually. The push policy and protected-config "
            f"checks did not finish within their work budget. Command: {cmd[:200]}")
    except Exception as e:  # fail closed: a check that cannot decide asks
        ask(f"Command could not be checked ({type(e).__name__}); approve manually. "
            f"Command: {cmd[:200]}")
    if shell.publish:
        policy = push_policy(cfg)
        if policy == "deny":
            block('pushPolicy is "deny": git push and GitHub publishing (gh pr create/merge, gh '
                  "repo create/delete, gh release create, gh gist create) are blocked. The owner "
                  f"pushes by hand; report what is ready instead. Command: {cmd[:200]}")
        if policy != "allow":
            ask(f"Publishing command (pushPolicy \"{policy}\") needs the owner's explicit OK: "
                f"{cmd[:200]}")
        if push_policy_files_changed(cwd):
            ask('pushPolicy is "allow", but the push policy file changed in the working tree '
                "(a harness.json is modified, untracked, deleted or ignored, or git could not "
                f"tell). Approve this publish manually. Command: {cmd[:200]}")
    if shell.config_hit:
        ask(f"Command looks like it writes, moves, deletes or changes the mode of a protected "
            f"config ({shell.config_hit}: lint/test thresholds, validators, or guardrails such "
            "as harness.json). Fix the code instead of weakening the check, unless the owner "
            f"approves. Command: {cmd[:200]}")
    # Reached under "allow" too: a deploy or askCommand chained onto a push still asks.
    for pattern in ALWAYS_ASK_COMMANDS + [re.escape(c) for c in cfg.get("askCommands", [])]:
        if re.search(pattern, cmd):
            ask(f"Outward-facing or destructive command needs the owner's explicit OK: {cmd[:200]}")


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


class CheckTooLarge(Exception):
    pass


def _ansi_c(body):
    """Text of a $'...' string: the common escapes decoded, the rest kept."""
    out, i = [], 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "x" and re.match(r"[0-9a-fA-F]{1,2}", body[i + 2:i + 4]):
                hexits = re.match(r"[0-9a-fA-F]{1,2}", body[i + 2:i + 4]).group()
                out.append(chr(int(hexits, 16)))
                i += 2 + len(hexits)
                continue
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def _data_heredoc(line, quoted, body):
    """True when a heredoc body is plainly data (see DATA_HEREDOC); anything unclear is not."""
    line = line.rstrip("\n")
    if re.search(r"[|`]|\$\(|<\(|>\(", line) or "&" in line.replace("&&", ""):
        return False
    if line.count("<<") != 1 or not DATA_HEREDOC.fullmatch(re.split(r"&&|;", line)[-1]):
        return False
    return quoted or not re.search(r"[$`\\]", body)  # unquoted bodies expand $(...)


def _strip_dot(path):
    while path.startswith("./"):
        path = path[2:]
    return path


def _named_later(text, written):
    """Ordinals of cat/tee data heredocs whose file the command names again after the body (run,
    sourced, chmod-ed...): the same path after dropping "./", or the same file name when either
    side is relative. One pass over the words, so many heredocs stay linear."""
    last_path, last_name, last_relative_name = {}, {}, {}
    for match in PATHLIKE.finditer(text):
        word = _strip_dot(match.group())
        name = os.path.basename(word)
        last_path[word] = last_name[name] = match.start()
        if not word.startswith("/"):
            last_relative_name[name] = match.start()
    named = set()
    for number, target, end in written:
        target = _strip_dot(target)
        name = os.path.basename(target)
        names = last_name if not target.startswith("/") else last_relative_name
        if last_path.get(target, -1) >= end or names.get(name, -1) >= end:
            named.add(number)
    return named


def scan_shell(text):
    """scan_shell_once, repeated while a cat/tee data heredoc writes a file that the rest of the
    command names again (a script written, then run): that body is then checked as commands."""
    not_data = set()
    for _ in range(3):
        result, written = scan_shell_once(text, not_data)
        named = _named_later(text, written) if written else set()
        if not named:
            return result
        not_data |= named
    return scan_shell_once(text, None)[0]  # still unsettled: no heredoc counts as data


def scan_shell_once(text, not_data):
    """((full, main, heredocs, data_spans, longest quote), written) in one linear pass.
    not_data: ordinals of heredocs that must not count as data (None: none may).

    full is the text with backslash-newlines joined and nothing else removed; the publish and
    redirect regexes read it. main is what the word checks read: full without comments (only
    where the quoting is plain), with $'...' decoded, and without the bodies of data heredocs.
    Every other heredoc body stays in main and is checked as commands. data_spans are the
    positions of data heredoc bodies in full; a regex hit there does not count. heredocs is a
    list of (command line, body, is data). written lists (ordinal, file, end of body) for the
    cat/tee data heredocs."""
    full, main, heredocs, spans, written, ordinal = [], [], [], [], [], 0
    flen, pending = 0, []
    i, n = 0, len(text)
    quote, quote_start, longest = None, 0, 0
    unsure, arith = False, True  # unsure: $(, backticks or (( on this line; keep its comments
    line_start = 0  # index into main where the current line starts
    while i < n:
        if quote == "'":
            j = text.find("'", i)
            j = n if j < 0 else j
            chunk = text[i:j + 1]
            full.append(chunk)
            main.append(chunk)
            flen += len(chunk)
            longest = max(longest, j + 1 - quote_start)
            i, quote = j + 1, None
            continue
        m = SCAN_SPECIAL.search(text, i)
        if not m:
            full.append(text[i:])
            main.append(text[i:])
            break
        j, c = m.start(), m.group()
        if j > i:
            full.append(text[i:j])
            main.append(text[i:j])
            flen += j - i
        i = j
        emit, step, to_main, main_text = c, 1, True, None
        if c == "\\":
            emit = "" if text.startswith("\\\n", i) else text[i:i + 2]  # join continuations
            step = 2
        elif c == "`":
            unsure = True
        elif c == "$" and text.startswith("$(", i) and not text.startswith("$((", i):
            unsure, emit, step = True, "$(", 2
        elif quote == '"':
            if c == '"':
                quote = None
                longest = max(longest, i - quote_start)
        elif c in "'\"":
            quote, quote_start = c, i
        elif c in "$(" and arith and text.startswith("((", i + (c == "$")):
            k = text.find("))", i + 2 + (c == "$"))  # arithmetic: no heredocs or comments inside
            if k < 0:
                arith = False
            else:
                emit, step, unsure = text[i:k + 2], k + 2 - i, True
        elif c == "$" and text.startswith("$'", i):  # ANSI-C string: decode for the word check
            k, buf = i + 2, []
            while k < n and text[k] != "'":
                buf.append(text[k:k + 2] if text[k] == "\\" else text[k])
                k += 2 if text[k] == "\\" else 1
            emit, step = text[i:k + 1], k + 1 - i
            main_text = "'" + _ansi_c("".join(buf)).replace("'", "'\\''") + "'"
            longest = max(longest, k - i)
        elif c == "$" and text.startswith('$"', i):
            main_text = ""
        elif c in "<>" and text.startswith("(", i + 1):
            unsure = True
        elif c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i) and (
                i == 0 or text[i - 1] != "<"):
            hd = HEREDOC_TOKEN.match(text, i)
            if hd:
                tok = hd.group(2)
                delim = re.sub(r"'([^']*)'|\"([^\"]*)\"|\\(.)",
                               lambda x: x.group(1) or x.group(2) or x.group(3) or "", tok)
                pending.append((delim, hd.group(1) == "-", any(q in tok for q in "'\"\\"),
                                ordinal))
                ordinal += 1
                emit, step = hd.group(), len(hd.group())
        elif c == "#":
            if (i == 0 or text[i - 1] in " \t\n;&|()") and not unsure:
                k = text.find("\n", i)
                k = n if k < 0 else k
                emit, step, to_main = text[i:k], k - i, False
        elif c == "\n":
            full.append("\n")
            main.append("\n")
            flen += 1
            i += 1
            line = "".join(main[line_start:])
            for delim, strip_tabs, quoted, number in pending:
                body_start, body, found = i, [], False
                while i < n:
                    k = text.find("\n", i)
                    k = n if k < 0 else k
                    ln, i = text[i:k], k + 1
                    if (ln.lstrip("\t") if strip_tabs else ln) == delim:
                        found = True
                        break
                    body.append(ln)
                raw = text[body_start:min(i, n)]
                body = "\n".join(body)
                data = (found and len(pending) == 1 and not_data is not None
                        and number not in not_data and _data_heredoc(line, quoted, body))
                heredocs.append((line, body, data))
                if data:
                    last = re.split(r"&&|;", line.rstrip("\n"))[-1]
                    target = (re.search(r"\btee\s+(?:-a\s+)?(" + _FILE + ")", last)
                              or re.search(r">>?\s*(" + _FILE + ")", last))
                    if target:
                        written.append((number, target.group(1), min(i, n)))
                if data:
                    spans.append((flen, flen + len(raw)))
                else:
                    main.append(raw)
                full.append(raw)
                flen += len(raw)
            pending, unsure, line_start = [], False, len(main)
            continue
        if emit:
            full.append(emit)
            flen += len(emit)
        if to_main:
            main.append(emit if main_text is None else main_text)
        i += step
    if quote:
        longest = max(longest, n - quote_start)
    return ("".join(full), "".join(main), heredocs, spans, longest), written


def shell_words(text):
    """Shell words, with operators (`;`, `&&`, `|`, `>`, newline, ...) as their own words. When
    the quoting does not parse, a rough split with quotes removed (fail closed, not open)."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return [w.replace("'", "").replace('"', "")
                for w in re.findall(r"[^\s;&|<>()]+|[;&|<>()\n]+", text)]


def _is_operator(word):
    return bool(word) and set(word) <= set("();<>|&\n")


def _skip_options(words, i, with_value=()):
    """Index of the first word after the options that start at words[i]."""
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in with_value else 1
    return i


class ConfigMatcher:
    """Does a path (or glob, or folder) written by a command hit a protected config? Matches on
    the trailing path parts, so it holds relative to the working directory and every project
    root; a folder counts when it holds a protected config, a glob when it matches one."""

    def __init__(self, globs, cwd, roots):
        fold = (lambda x: x.lower()) if CASE_INSENSITIVE else (lambda x: x)
        self.fold = fold
        self.tails = []
        for g in globs:
            tail = fold(g).rsplit("**/", 1)[-1]
            parts = [p for p in tail.split("/") if p not in ("", ".")]
            if parts:
                self.tails.append(parts)
        alternatives = "|".join(fnmatch.translate(p) for t in self.tails for p in t) or "(?!)"
        self.any_part = re.compile(alternatives)
        self.ancestors = set()
        for base in [cwd or os.getcwd()] + list(roots or ()):
            d = fold(os.path.abspath(base))
            while True:
                self.ancestors.add(d)
                if os.path.dirname(d) == d:
                    break
                d = os.path.dirname(d)

    def hit(self, path, tree=False):
        path = self.fold(path)
        parts = [p for p in path.split("/") if p]
        name = parts[-1] if parts else ""
        if tree and path in self.ancestors:
            return True
        if not parts or name.endswith((".example", ".sample", ".template")):
            return False
        is_glob = any(ch in path for ch in "*?[")
        if not is_glob and not self.any_part.match(name):
            return False

        def same(a, b):  # a: written part, b: pattern part
            return fnmatch.fnmatchcase(a, b) or (is_glob and fnmatch.fnmatchcase(b, a))

        for tail in self.tails:
            for k in range(1, len(tail) + 1):  # k < len(tail): a folder on the way to the config
                if len(parts) >= k and all(same(a, b) for a, b in zip(parts[-k:], tail[:k])):
                    return True
        return False


class ShellCheck:
    """One pass over a Bash command for push detection and protected-config writes, inside a
    work budget. Sets .publish and .config_hit; raises CheckTooLarge when over budget."""

    def __init__(self, cfg, cwd):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.matcher = ConfigMatcher(DEFAULT_PROTECTED_CONFIGS + cfg.get("protectedConfigs", []),
                                     self.cwd, PROJECT_ROOTS)
        self.publish = False
        self.config_hit = None
        self.seen = set()
        self.steps = CHECK_STEPS
        self.deadline = time.monotonic() + CHECK_SECONDS

    def tick(self, n=1):
        self.steps -= n
        if self.steps < 0 or time.monotonic() > self.deadline:
            raise CheckTooLarge()

    def run(self, cmd):
        if "\x00" in cmd:  # bash stops reading at a NUL byte; this text is not what would run
            raise ValueError("NUL byte in command")
        if len(cmd) > LONGEST_COMMAND:
            raise CheckTooLarge()
        full, spans = self.analyze(cmd, [self.cwd], 0)

        def counts(match):  # a hit inside a data heredoc body is not a command
            return not any(a <= match.start() and match.end() <= b for a, b in spans)

        for pattern in PUBLISH_COMMANDS:
            for match in re.finditer(pattern, full, re.IGNORECASE):
                self.tick()
                if counts(match):
                    self.publish = True
                    break
        for match in RAW_REDIRECT.finditer(full):
            self.tick()
            if counts(match):
                self.candidate(match.group(1), [self.cwd])

    def candidate(self, word, bases, tree=False):
        if self.config_hit or not word:
            return
        values = [word] + ([word.split("=", 1)[1]] if "=" in word else [])
        for value in values:
            if value.startswith("-") or not value:
                continue
            for base in bases:
                self.tick()
                path = os.path.normpath(os.path.join(base, os.path.expanduser(value)))
                if self.matcher.hit(path, tree):
                    self.config_hit = value
                    return

    def code(self, text, bases):
        """Code for an interpreter: any path-like word in it that names a protected config, and
        git push spelled as separate arguments (`["git", "push"]`)."""
        if re.search(r"\bgit\b['\"]?\s*,\s*['\"]push\b", text, re.IGNORECASE):
            self.publish = True
        words = list(dict.fromkeys(PATHLIKE.findall(text)))
        self.tick(len(words))
        for w in words:
            self.candidate(w, bases)

    def analyze(self, text, bases, depth):
        """Check one piece of shell text; returns (full text, data heredoc spans)."""
        if text in self.seen:
            return text, []
        self.seen.add(text)
        self.tick(1 + len(text) // 200)
        if depth > 3:
            raise CheckTooLarge()
        full, main, docs, spans, longest = scan_shell(text)
        if max([longest] + [len(w) for w in main.split()]) > LONGEST_WORD:
            raise CheckTooLarge()
        for line, body, data in docs:  # non-data bodies are in main, checked as commands too
            self.tick()
            names = {os.path.basename(w).lower() for w in re.split(r"[\s;&|()<>]+", line)}
            if not data and any(INTERPRETER.match(x) for x in names):
                self.code(body, bases)
        words = shell_words(main)
        self.tick(len(words))
        segments, seg, targets, here = [], [], [], []
        for i, w in enumerate(words):
            if _is_operator(w):
                nxt = words[i + 1] if i + 1 < len(words) and not _is_operator(words[i + 1]) else ""
                if "<<<" in w:
                    here.append(nxt)
                elif ">" in w and nxt:
                    targets.append(nxt)
                if ";" in w or "\n" in w or "&&" in w or "||" in w or w in ("|", "|&", "&"):
                    segments.append((seg, targets, here))
                    seg, targets, here = [], [], []
                continue
            seg.append(w)
        segments.append((seg, targets, here))
        all_words = [w for s, _, _ in segments for w in s]
        bases = list(bases)
        for seg, targets, here in segments:
            self.segment(seg, targets, here, bases, all_words, depth)
        return full, spans

    def segment(self, seg, targets, here, bases, all_words, depth):
        names = [os.path.basename(w).lower() for w in seg]
        nameset = set(names)
        self.publish = self.publish or self.publish_words(seg, names) or (
            {"xargs", "git"} <= nameset and "push" in (w.lower() for w in all_words))
        seg_bases = list(bases)
        for i, name in enumerate(names):
            if name == "git":
                j = i + 1
                while j < len(seg) and seg[j].startswith("-"):
                    if seg[j] in ("-C",) and j + 1 < len(seg):
                        seg_bases += [os.path.join(b, os.path.expanduser(seg[j + 1]))
                                      for b in bases]
                        j += 2
                    else:
                        j += 2 if seg[j] in GIT_OPTIONS_WITH_VALUE else 1
        if names and names[0] in ("cd", "pushd"):
            k = 1
            while k < len(seg) and seg[k].startswith("-") and seg[k] != "-":
                k += 1
                if seg[k - 1] == "--":
                    break
            dest = seg[k] if k < len(seg) else "~"
            if dest != "-":  # each cd adds one folder; earlier ones stay (subshells, fail closed)
                bases.append(os.path.join(bases[-1], os.path.expanduser(dest)))
                if len(bases) > 64:
                    raise CheckTooLarge()
        for t in targets:
            self.candidate(t, seg_bases)
        tree = bool(nameset & CONFIG_TREE_VERBS)
        writes = bool(nameset & CONFIG_WRITE_VERBS)
        if nameset & IN_PLACE_EDITORS and any(
                re.match(r"-[a-zA-Z]*i", w) or w.startswith("--in-place") for w in seg):
            writes = True
        if "find" in nameset and "-delete" in seg:
            writes = True
        if "git" in nameset and "update-index" in nameset:  # --assume-unchanged hides changes
            writes = True
        if nameset & {"cp", "rsync", "ditto"} and ("ditto" in nameset or any(
                re.fullmatch(r"-[a-zA-Z]*[rRa][a-zA-Z]*", w) or w in ("--recursive", "--archive")
                for w in seg)):  # a recursive copy into a folder that holds a config
            dest = None
            for k, w in enumerate(seg):
                if w == "-t" and k + 1 < len(seg):
                    dest = seg[k + 1]
                elif w.startswith("--target-directory="):
                    dest = w.split("=", 1)[1]
            if dest is None:
                plain = [w for w in seg[1:] if not w.startswith("-")]
                dest = plain[-1] if plain else None
            if dest:
                self.candidate(dest, seg_bases, tree=True)
        if "git" in nameset and nameset & {"checkout", "restore"}:
            staged_only = "restore" in nameset and (
                {"--staged", "-S"} & set(seg) and not {"--worktree", "-W"} & set(seg))
            writes = writes or not staged_only
        if writes:
            for w in dict.fromkeys(seg):
                self.candidate(w, seg_bases, tree)
            if "xargs" in nameset:  # input comes from the rest of the pipeline
                for w in dict.fromkeys(all_words):
                    self.candidate(w, seg_bases, tree)
        if "tee" in nameset:
            for w in seg[names.index("tee") + 1:]:
                self.candidate(w, seg_bases)
        for i, name in enumerate(names):
            if INTERPRETER.match(name):
                j = i + 1
                if j < len(seg) and seg[j] == "eval" and j + 1 < len(seg):  # deno eval CODE
                    self.code(seg[j + 1], seg_bases)
                while j < len(seg) and seg[j].startswith("-") and seg[j] not in ("-", "-m"):
                    if CODE_OPTION.fullmatch(seg[j]) and j + 1 < len(seg):
                        self.code(seg[j + 1], seg_bases)
                        j += 2
                        continue
                    if re.match(r"-[ceE].", seg[j]):  # code glued to the option
                        self.code(seg[j][2:], seg_bases)
                    j += 1
                for h in here:
                    self.code(h, seg_bases)
            if name in SHELLS:
                for j in range(i + 1, len(seg) - 1):
                    if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", seg[j]):
                        self.analyze(seg[j + 1], seg_bases, depth + 1)
                        break
                for h in here:
                    self.analyze(h, seg_bases, depth + 1)
        if "eval" in names:  # once per segment, at the first eval
            self.analyze(" ".join(seg[names.index("eval") + 1:]), seg_bases, depth + 1)

    @staticmethod
    def publish_words(seg, names):
        words = [w.lower() for w in seg]
        git_values = {o.lower() for o in GIT_OPTIONS_WITH_VALUE}
        for i, name in enumerate(names):
            if name == "git-push":
                return True
            if name == "git":
                j = _skip_options(words, i + 1, git_values)
                if j < len(words) and words[j] == "push":
                    return True
            if name == "gh":
                j = _skip_options(words, i + 1, {o.lower() for o in GH_OPTIONS_WITH_VALUE})
                if tuple(words[j:j + 2]) in GH_PUBLISH:
                    return True
        return False


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
