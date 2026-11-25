"""One pass over a Bash command for the push policy and protected-config rules: ShellCheck
walks the words that shell_scan.py finds and sets .publish and .config_hit; ConfigMatcher
says whether a written path hits a protected config. No I/O and no exit: bash_rules.check()
decides what a hit means, and bash_rules.expand_globs lists the folders for the globs that
ShellCheck leaves in .globs (CH-31).
"""
import fnmatch
import os
import re
import sys

import shell_scan
from shell_scan import CheckTooLarge, PATHLIKE

DEFAULT_PROTECTED_CONFIGS = [
    "**/.swiftlint.yml", "**/.swiftformat", "**/.eslintrc*", "**/eslint.config.*",
    "**/.prettierrc*", "**/detekt*.yml", "**/lint.xml", "**/.flake8", "**/ruff.toml",
    "**/.claude/settings.json", "**/.claude/settings.local.json", "**/.claude/harness.json",
    "**/*.xctestplan", "**/pytest.ini", "**/.coveragerc", "**/jest.config.*", "**/vitest.config.*",
    "**/codecov.yml",
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
CASE_INSENSITIVE = sys.platform == "darwin"  # the default macOS filesystem ignores case

# CH-31: a glob that could match a protected config by name is expanded against the folder, and
# asks only when a real path hits, for these commands run directly (the first word, spelled so).
# Their file arguments are plain names: a quoted or unmatched glob reaches them as a literal name.
GLOB_LITERAL_VERBS = {"rm", "unlink", "cp", "mv", "ln", "install", "touch", "truncate", "chmod",
                      "chown", "chgrp"}
# The other simple commands in the line (CH-31, owner decisions 2026-11-25, review round 2).
# A protected config that exists when the command starts is what the guard protects, and the
# listing sees exactly those, so a build or a read elsewhere in the line does not stop the
# expansion. Allow-lists, failing closed: the line must be a flat list of simple commands joined
# by ; && || | or newlines (JOINERS, plus redirections), every other command's first word must be
# on these lists with the arguments they allow (ShellCheck.readable_command), and their redirects
# must stay out of the glob's folder and its parents. Anything else asks.
READERS = {"echo", "true", "false", "test", "[", "ls", "cat", "head", "tail", "wc", "date",
           "sleep", "pwd", "which", "grep", "rm", "rmdir", "unlink"}  # read, or only remove
# Build and test runners and git reads (owner rule, review round 4): every argument after the
# runner word is a plain word (PLAIN_ARG; not -, /dev/... or /proc/...), the runner does not read
# stdin (no pipe into it, no <, <<< or heredoc), and a word starting with - must be on the
# runner's exact option list. Task, target and subcommand names are plain words.
PLAIN_ARG = re.compile(r"[A-Za-z0-9_:.,/+@-]+")
GRADLE_OPTIONS = {"--info", "-i", "--stacktrace", "-s", "--offline", "--no-daemon", "-q",
                  "--quiet", "--continue", "--warn", "-w", "--parallel", "--rerun-tasks",
                  "--dry-run", "-m", "--build-cache", "--no-build-cache"}
PYTEST_OPTIONS = {"-q", "-x", "-v", "-vv", "-s", "-k", "--lf", "--ff"}
BUILDER_OPTIONS = {
    "./gradlew": GRADLE_OPTIONS, "gradlew": GRADLE_OPTIONS, "gradle": GRADLE_OPTIONS,
    "make": {"-k", "-s", "-n", "-B", "-j", "--keep-going", "--silent"},
    "xcodebuild": {"-scheme", "-project", "-workspace", "-configuration", "-sdk", "-quiet",
                   "-target", "-alltargets", "-list", "-showBuildSettings"},
    "pytest": PYTEST_OPTIONS}
BUILDER_NUMBERS = {"make": re.compile(r"-j\d+")}
BUILDER_SUBCOMMANDS = {  # runner: (subcommands, options allowed after one)
    "npm": ({"test", "run", "ci", "install", "i"}, set()),
    "pnpm": ({"test", "ci", "install", "i"}, set()),  # no run: it falls back to any bin
    "yarn": ({"test", "ci", "install", "i"}, set()),
    "swift": ({"build", "test"}, {"--parallel", "-v", "--verbose", "-q", "--quiet"}),
    "cargo": ({"build", "test"}, {"--release", "-q", "--quiet", "-v", "--verbose", "--all",
                                  "--workspace", "--lib", "--bins", "--tests", "--no-fail-fast",
                                  "--locked", "--offline", "--frozen"}),
    "go": ({"build", "test"}, {"-v", "-race", "-short", "-cover", "-failfast", "-count",
                               "-run"})}
INSTALL_SUBCOMMANDS = {"install", "ci", "i"}  # npm/pnpm/yarn: only with no word after them
PYTHON_MODULES = {"unittest": {"-v", "-q", "-f", "-b", "-k", "-s", "-t", "-p"},
                  "pytest": PYTEST_OPTIONS}
GIT_READS = {"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "blame",
             "describe", "shortlog", "grep", "cat-file", "rev-list", "merge-base", "help",
             "version"}
GIT_OPTIONS = {"--oneline", "--stat", "--short", "--porcelain", "-s", "--name-only",
               "--name-status", "-b", "--cached", "--staged", "--no-color", "--graph",
               "--decorate", "--all", "-p", "--patch", "--abbrev-commit", "-q", "--quiet",
               "--exit-code", "--numstat", "--shortstat", "--branch", "--show-toplevel",
               "--abbrev-ref", "--verify", "--is-inside-work-tree", "--reverse", "--follow",
               "--first-parent", "--merges", "--no-merges", "-l", "-i", "--"}
GIT_NUMBER = re.compile(r"-n?\d+|-n")  # log -5, -n 5, -n5
JOINERS = {"&&", "||", "|"}  # plus tokens made only of ; and newlines, and redirections
QUIET_TARGETS = re.compile(r"/dev/(?:null|stdout|stderr)|\d+")
UNREADABLE_PATH = re.compile(r"[$`*?\[{~]")
GLOB_WORD = re.compile(r"[A-Za-z0-9._/,+@%:*?-]+")  # no quotes, $, ~, braces, [ ], = or spaces
BRACE_GROUP = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")  # {a,b} or {1..9}: may be any name
CD_DIR = re.compile(r"[A-Za-z0-9._/,+@%:-]+")
# Shell text after which the files or folders could differ from what the hook sees: substitutions,
# comments, a carriage return, and a function definition (it can redefine rm).
CHANGING_TEXT = re.compile(r"\$\(|`|<\(|>\(|\$\{|#|\r|\(\s*\)|\bfunction\s")

# The data-heredoc owners (cat, tee, git) redefined, or a write target that is not a plain file.
SHADOWED_OWNER = re.compile(
    r"\balias\b|\bfunction\s+(?:cat|tee|git)\b|\b(?:cat|tee|git)\s*\(\s*\)|\bPATH\+?=|\bhash\b"
    r"|\bmkfifo\b|\bexec\b[^\n;&|]*[<>]")
# When one of these holds, the scanner and bash may disagree about where a body is or what writes
# it, so nothing is exempt (fail closed). Each gets (command, text outside the bodies, opening
# lines of the data heredocs). Shared by ShellCheck.run (push and config rules, CH-12) and
# DataText (credential and device rules, CH-10): one table.
DATA_TEXT_UNSAFE = {
    # bash keeps a "\r" in the delimiter word ("EOF\r"); the scanner stops before it.
    "crlf": lambda cmd, outside, openings: "\r" in cmd,
    # The scanner can misread a nested quote in ${...} and take a "#" for a comment.
    "comment": lambda cmd, outside, openings: "#" in outside or "${" in outside,
    "shadowed owner": lambda cmd, outside, openings: SHADOWED_OWNER.search(outside) is not None,
    "device target": lambda cmd, outside, openings: any(
        "/dev/" in line or "/proc/" in line for line in openings),
}




def outside_and_openings(full, spans):
    """The text outside every data heredoc body, and the opening line of each body."""
    pieces, last = [], 0
    for a, b in spans:
        pieces.append(full[last:a])
        last = b
    pieces.append(full[last:])
    openings = [full[full.rfind("\n", 0, a - 1) + 1:a] for a, _ in spans]
    return "".join(pieces), openings


def data_text_unsafe(cmd, outside, openings):
    return any(unsafe(cmd, outside, openings) for unsafe in DATA_TEXT_UNSAFE.values())


class ConfigMatcher:
    """Does a path (or glob, or folder) written by a command hit a protected config? Matches on
    the trailing path parts, so it holds relative to the working directory and every project
    root; a folder counts when it holds a protected config, a glob when it matches one."""

    ANCESTOR = "a folder that holds the working directory or a project root"

    def __init__(self, globs, cwd, roots):
        fold = (lambda x: x.lower()) if CASE_INSENSITIVE else (lambda x: x)
        self.fold = fold
        self.tails = []  # (pattern parts, the protected pattern as configured)
        for g in globs:
            tail = fold(g).rsplit("**/", 1)[-1]
            parts = [p for p in tail.split("/") if p not in ("", ".")]
            if parts:
                self.tails.append((parts, g))
        alternatives = "|".join(fnmatch.translate(p) for t, _ in self.tails for p in t) or "(?!)"
        self.any_part = re.compile(alternatives)
        self.ancestors = set()
        for base in [cwd or os.getcwd()] + list(roots or ()):
            d = fold(os.path.abspath(base))
            while True:
                self.ancestors.add(d)
                if os.path.dirname(d) == d:
                    break
                d = os.path.dirname(d)

    def hit(self, path, tree=False, literal=False):
        """The protected pattern the path hits (a non-empty string), or None. literal: read
        glob characters in the path as plain characters (a name, not a pattern)."""
        path = self.fold(path)
        parts = [p for p in path.split("/") if p]
        name = parts[-1] if parts else ""
        if tree and path in self.ancestors:
            return self.ANCESTOR
        if not parts or name.endswith((".example", ".sample", ".template")):
            return None
        is_glob = not literal and any(ch in path for ch in "*?[")
        if not is_glob and not self.any_part.match(name):
            return None

        def same(a, b):  # a: written part, b: pattern part
            return fnmatch.fnmatchcase(a, b) or (is_glob and fnmatch.fnmatchcase(b, a))

        for tail, pattern in self.tails:
            for k in range(1, len(tail) + 1):  # k < len(tail): a folder on the way to the config
                if len(parts) >= k and all(same(a, b) for a, b in zip(parts[-k:], tail[:k])):
                    return pattern
        return None


class ShellCheck(shell_scan.Budget):
    """One pass over a Bash command for push detection and protected-config writes, inside a
    work budget. Sets .publish and .config_hit; raises CheckTooLarge when over budget."""

    def __init__(self, cfg, cwd, roots):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.roots = list(roots or ())
        self.matcher = ConfigMatcher(DEFAULT_PROTECTED_CONFIGS + cfg.get("protectedConfigs", []),
                                     self.cwd, roots)
        self.publish = False
        self.config_hit = None   # the written word that hit
        self.config_why = None   # what the prompt says about it (see hit_found)
        self.seen = set()
        self.texts = []     # (full text, data heredoc spans) of the command and of each nested body
        self.segments = []  # (words, folders added by `git -C`) of each simple command
        self.effects = []   # per simple command: what it can do to a glob's folder (effect)
        self.plain_cd = None  # a `cd name` that CDPATH could send elsewhere
        self.heredoc_code = None  # a heredoc whose body runs as code: not readable (CH-31)
        self.operators = set()  # operator tokens of every text, for the flat-list rule
        self.deferred = []  # globs to expand once the whole command is read (CH-31)
        self.globs = []     # deferred globs whose folders bash_rules.expand_globs lists
        super().__init__()  # the budget starts now

    def hit_found(self, word, pattern, part, verb, path=None, reason=None):
        """Record the first protected-config hit and the words the prompt shows for it."""
        if self.config_hit:
            return
        self.config_hit = word
        if path:
            what = f"reaches {path}, which matches {pattern}"
        elif reason or any(c in word for c in "*?["):
            what = f"could match {pattern}"
        else:
            what = f"matches {pattern}"
        self.config_why = f"{verb}: `{part}` {what}" + (f" ({reason})" if reason else "")

    def run(self, cmd):
        if "\x00" in cmd:  # bash stops reading at a NUL byte; this text is not what would run
            raise ValueError("NUL byte in command")
        if len(cmd) > shell_scan.LONGEST_COMMAND:
            raise CheckTooLarge()
        self.analyze(cmd, [self.cwd], 0)
        unsafe = self.texts_unsafe()

        # The structural rule: these regexes read the full text of the command and of each nested
        # body; only a data heredoc removes a hit, and none does when any text is unsafe.
        for full, spans in self.texts:
            if unsafe:
                spans = []  # scanner and bash may disagree about the bodies: no hit is text

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
                    self.candidate(match.group(1), [self.cwd],
                                   via=("redirect", "> " + match.group(1)))
        for glob in self.deferred:
            reason = self.glob_changes(glob[-1], unsafe)
            if reason:
                word, _, _, verb, part, pattern, _ = glob
                self.hit_found(word, pattern, part, verb, reason="not expanded: " + reason)
            else:
                self.globs.append(glob)

    def glob_changes(self, index, unsafe):
        """Why the files a deferred glob would match may differ from what is on disk now (the
        glob is in simple command number index), or None. Pure: bash_rules.expand_globs then
        lists the folders for the globs where this is None."""
        if unsafe or any(CHANGING_TEXT.search(full) for full, _ in self.texts):
            return ("the command has $(...), backticks, ${...}, a comment, a carriage return, a "
                    "function or a redefined command, so the folder may not be what the shell "
                    "sees")
        if len(self.texts) != 1 or self.heredoc_code:
            return "the command runs nested shell text or a heredoc the guard does not read"
        odd = sorted(op for op in self.operators if not (
            op in JOINERS or set(op) <= set(";\n")
            or (set(op) <= set("<>&|") and ("<" in op or ">" in op))))
        if odd:
            return ("the command is not a flat list of simple commands (it has "
                    f"`{odd[0].strip()}`)")
        if any("cdpath" in re.sub(r"[\'\"\\]", "", full).lower() for full, _ in self.texts):
            return "the command names CDPATH, which can change where cd goes"
        for j, effect in enumerate(self.effects):
            if j != index and effect and effect[0] == "ask":
                return f"another part of the command may change that folder: {effect[1]}"
        return None

    def texts_unsafe(self):
        """Does a DATA_TEXT_UNSAFE check hold for the command or any nested text? (A nested text
        is read in the light of the outer one: `cat() { bash; }` outside makes bodies inside an
        `bash -c` string code too.)"""
        return any(data_text_unsafe(full, *outside_and_openings(full, spans))
                   for full, spans in self.texts)

    def candidate(self, word, bases, tree=False, via=("write", None), glob=None):
        """Does a written word hit a protected config? via: (verb, the part of the command shown
        in the prompt). glob: (main text, index of the simple command) when the word is an
        argument of a GLOB_LITERAL_VERBS command: a glob there that could match a protected
        config by name, and that the shell expands as it is, is deferred (see run)."""
        if self.config_hit or not word:
            return
        verb, part = via
        values = [word] + ([word.split("=", 1)[1]] if "=" in word else [])
        for value in values:
            if value.startswith("-") or not value:
                continue
            probe = value  # brace expansion: `{harness,x}.json` reads as `*.json` (CH-31)
            for _ in range(8):
                braced = BRACE_GROUP.sub("*", probe)
                if braced == probe:
                    break
                probe = braced
            for base in bases:
                self.tick()
                path = os.path.normpath(os.path.join(base, os.path.expanduser(probe)))
                pattern = self.matcher.hit(path, tree)
                if not pattern:
                    continue
                if not any(c in probe for c in "*?["):
                    self.hit_found(value, pattern, part or value, verb)
                    return
                why = self.glob_unsure(value, glob) if value == word else "a value after ="
                literal = self.matcher.hit(
                    os.path.normpath(os.path.join(base, os.path.expanduser(value))), tree,
                    literal=True)
                if why or literal:
                    self.hit_found(value, literal or pattern, part or value, verb,
                                   reason=f"not expanded: {why}" if why else None)
                    return
                self.deferred.append((value, list(bases), tree, verb, part or value, pattern,
                                      glob[1]))
                break

    @staticmethod
    def glob_unsure(word, glob):
        """Why a glob that could match a protected config cannot be expanded here, or None."""
        if glob is None:
            return ("only a glob given to " + ", ".join(sorted(GLOB_LITERAL_VERBS))
                    + " as the first word of a command is looked up in the folder")
        if "**" in word:
            return "it has **"
        if ".." in word.split("/"):  # `build/*/../x`: the folder listed is not the one written
            return "it has a .. part"
        if "[" in word or "]" in word:
            return "it has a [...] class"
        if not GLOB_WORD.fullmatch(word):
            return "it has a variable, ~, quotes, braces or other shell syntax"
        main, at, found = glob[0], 0, False
        while True:  # every place the word appears must be a whole word of its own
            at = main.find(word, at)
            if at < 0:
                break
            end = at + len(word)
            if (at and main[at - 1] not in " \t\n") or (
                    end < len(main) and main[end] not in " \t\n;&|)<>"):
                return "it is joined to other shell text"
            found, at = True, end
        return None if found else "it is joined to other shell text"

    def code(self, text, bases):
        """Code for an interpreter: any path-like word in it that names a protected config, and
        git push spelled as separate arguments (`["git", "push"]`)."""
        if re.search(r"\bgit\b['\"]?\s*,\s*['\"]push\b", text, re.IGNORECASE):
            self.publish = True
        words = list(dict.fromkeys(PATHLIKE.findall(text)))
        self.tick(len(words))
        for w in words:
            self.candidate(w, bases, via=("interpreter code", w))

    def analyze(self, text, bases, depth):
        """Check one piece of shell text; returns (full text, data heredoc spans)."""
        if text in self.seen:
            return text, []
        self.seen.add(text)
        self.tick(1 + len(text) // 200)
        if depth > 3:
            raise CheckTooLarge()
        full, main, docs, spans, longest = shell_scan.scan_shell(text)
        if max([longest] + [len(w) for w in main.split()]) > shell_scan.LONGEST_WORD:
            raise CheckTooLarge()
        for line, body, data in docs:  # non-data bodies are in main, checked as commands too
            self.tick()
            names = {os.path.basename(w).lower() for w in re.split(r"[\s;&|()<>]+", line)}
            if not data and any(INTERPRETER.match(x) for x in names):
                self.code(body, bases)
            if not data:  # the body is read as commands or code (CH-31)
                self.heredoc_code = self.heredoc_code or line.strip()
        words = shell_scan.shell_words(main)
        self.tick(len(words))
        self.operators |= {w for w in words if shell_scan.is_operator(w)}
        segments = shell_scan.segments(words)
        stdin = self.stdin_flags(words)
        if len(stdin) != len(segments):  # cannot line them up: every command may read stdin
            stdin = [True] * len(segments)
        all_words = [w for s, _, _ in segments for w in s]
        bases = list(bases)
        for (seg, targets, here), reads in zip(segments, stdin):
            self.segment(seg, targets, here, bases, all_words, depth, main, reads)
        self.texts.append((full, spans))
        return full, spans

    @staticmethod
    def stdin_flags(words):
        """For each simple command (as shell_scan.segments splits them): may it read stdin from
        the line? True after a pipe, or with a <, <<, <<< or <& redirection."""
        flags, reads = [], False
        for w in words:
            if not shell_scan.is_operator(w):
                continue
            if "<" in w:
                reads = True
            if ";" in w or "\n" in w or "&&" in w or "||" in w or w in ("|", "|&", "&"):
                flags.append(reads)
                reads = "|" in w.replace("||", "")
        flags.append(reads)
        return flags

    def effect(self, seg, targets, bases, stdin=True):
        """What a simple command can do to the folder of a glob elsewhere in the line (CH-31):
        None, ("ask", why), or ("paths", "a redirect", absolute paths its redirects write;
        bash_rules.folder_changed compares them with the glob's folder)."""
        if not seg:
            return None
        if seg[0] == "cd":  # only as the first word, with one plain argument
            dest = seg[1] if len(seg) == 2 else ""
            if (not CD_DIR.fullmatch(dest) or ".." in dest.split("/")
                    or dest.startswith(("-", "+"))):  # cd - (OLDPWD), zsh cd +1 / -1 (stack)
                return "ask", f"`{' '.join(seg[:3])}` goes to a folder that is not known"
            if not dest.startswith(("/", "./")) and dest != ".":
                self.plain_cd = self.plain_cd or " ".join(seg)  # CDPATH: see expand_globs
        elif not self.readable_command(seg, stdin):
            return "ask", (f"`{seg[0]}` is not on the guard's lists of readers, build and test "
                           "runners and git reads, or has an option, argument or stdin they do "
                           "not allow")
        paths = []
        for t in targets:
            if QUIET_TARGETS.fullmatch(t):
                continue
            if UNREADABLE_PATH.search(t):
                return "ask", f"a redirect writes to {t}, which cannot be read"
            paths.extend(os.path.normpath(os.path.join(b, t)) for b in bases)
        return ("paths", "a redirect", paths) if paths else None

    @staticmethod
    def readable_command(seg, stdin=True):
        """Is a simple command on the CH-31 allow-lists, with arguments they allow? The first
        word must match exactly (no other path, no NAME= prefix, no keyword). stdin: may the
        command read stdin from the line (a pipe or an input redirection)?"""
        cmd, args = seg[0], seg[1:]
        if cmd in READERS:
            return True
        if cmd == "printf":
            return not any(a.startswith("-v") for a in args)
        if cmd == "uniq":  # a second operand is an output file; `-` is an operand too
            return len([a for a in args if a == "-" or not a.startswith("-")]) <= 1
        if cmd == "sort":  # -o writes a file, --compress-program runs one
            return not any(a.startswith(("--o", "--c")) or re.fullmatch(r"-[a-zA-Z]*o.*", a)
                           for a in args)
        if cmd == "rg":
            return not any(a.startswith("--pre") for a in args)

        def plain(words, options, number=None):  # runners and git reads: the owner's rule
            return not stdin and all(
                PLAIN_ARG.fullmatch(w) and w != "-" and not w.startswith(("/dev/", "/proc/"))
                and (not w.startswith("-") or w in options
                     or bool(number and number.fullmatch(w)))
                for w in words)

        if cmd in BUILDER_OPTIONS:
            return plain(args, BUILDER_OPTIONS[cmd], BUILDER_NUMBERS.get(cmd))
        if cmd in BUILDER_SUBCOMMANDS:
            subcommands, options = BUILDER_SUBCOMMANDS[cmd]
            if args[:1] and args[0] in INSTALL_SUBCOMMANDS and len(args) > 1:
                return False  # `npm install ./app` links a folder, `npm i x` fetches a package
            return bool(args) and args[0] in subcommands and plain(args, options)
        if cmd in ("python3", "python"):
            return (args[:1] == ["-m"] and len(args) > 1 and args[1] in PYTHON_MODULES
                    and plain(args[2:], PYTHON_MODULES[args[1]]))
        if cmd == "git":  # a read subcommand right after git: no -c, -C or other global option
            return bool(args) and args[0] in GIT_READS and plain(args, GIT_OPTIONS, GIT_NUMBER)
        return False

    def segment(self, seg, targets, here, bases, all_words, depth, main="", stdin=True):
        names = [os.path.basename(w).lower() for w in seg]
        nameset = set(names)
        self.publish = self.publish or self.publish_words(seg, names) or (
            {"xargs", "git"} <= nameset and "push" in (w.lower() for w in all_words))
        index = len(self.effects)
        shown = " ".join(w if re.fullmatch(r"[\w@%+=:,./*?-]+", w)
                         else "'" + w.replace("'", "'\\''") + "'" for w in seg)

        def part(word):  # the part of the command the prompt quotes
            return shown if len(shown) <= 120 else f"{seg[0]} … {word}"
        verb = next((n for n in names if n in CONFIG_WRITE_VERBS | CONFIG_TREE_VERBS
                     | IN_PLACE_EDITORS | {"find", "git", "xargs"}), names[0] if names else "")
        literal = bool(seg) and seg[0] in GLOB_LITERAL_VERBS and "xargs" not in nameset
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
        self.segments.append((seg, seg_bases[len(bases):]))
        self.effects.append(self.effect(seg, targets, seg_bases, stdin))
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
            self.candidate(t, seg_bases, via=("redirect", "> " + t))
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
                self.candidate(dest, seg_bases, tree=True, via=(verb, part(dest)))
        if "git" in nameset and nameset & {"checkout", "restore"}:
            staged_only = "restore" in nameset and (
                {"--staged", "-S"} & set(seg) and not {"--worktree", "-W"} & set(seg))
            writes = writes or not staged_only
        if writes:
            for w in dict.fromkeys(seg):
                self.candidate(w, seg_bases, tree, via=(verb, part(w)),
                               glob=(main, index) if literal else None)
            if "xargs" in nameset:  # input comes from the rest of the pipeline
                for w in dict.fromkeys(all_words):
                    self.candidate(w, seg_bases, tree, via=(verb, part(w)))
        if "tee" in nameset:
            for w in seg[names.index("tee") + 1:]:
                self.candidate(w, seg_bases, via=("tee", part(w)))
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
                j = shell_scan.skip_options(words, i + 1, git_values)
                if j < len(words) and words[j] == "push":
                    return True
            if name == "gh":
                j = shell_scan.skip_options(words, i + 1,
                                            {o.lower() for o in GH_OPTIONS_WITH_VALUE})
                if tuple(words[j:j + 2]) in GH_PUBLISH:
                    return True
        return False
