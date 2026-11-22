"""One pass over a Bash command for the push policy and protected-config rules: ShellCheck
walks the words that shell_scan.py finds and sets .publish and .config_hit; ConfigMatcher
says whether a written path hits a protected config. No I/O and no exit: bash_rules.check()
decides what a hit means.
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


class ShellCheck(shell_scan.Budget):
    """One pass over a Bash command for push detection and protected-config writes, inside a
    work budget. Sets .publish and .config_hit; raises CheckTooLarge when over budget."""

    def __init__(self, cfg, cwd, roots):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.matcher = ConfigMatcher(DEFAULT_PROTECTED_CONFIGS + cfg.get("protectedConfigs", []),
                                     self.cwd, roots)
        self.publish = False
        self.config_hit = None
        self.seen = set()
        super().__init__()  # the budget starts now

    def run(self, cmd):
        if "\x00" in cmd:  # bash stops reading at a NUL byte; this text is not what would run
            raise ValueError("NUL byte in command")
        if len(cmd) > shell_scan.LONGEST_COMMAND:
            raise CheckTooLarge()
        full, spans = self.analyze(cmd, [self.cwd], 0)

        def counts(match):  # a hit inside a data heredoc body is not a command
            return not any(a <= match.start() and match.end() <= b for a, b in spans)

        # The structural rule: these regexes read the full text; only a data heredoc removes a hit.
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
        full, main, docs, spans, longest = shell_scan.scan_shell(text)
        if max([longest] + [len(w) for w in main.split()]) > shell_scan.LONGEST_WORD:
            raise CheckTooLarge()
        for line, body, data in docs:  # non-data bodies are in main, checked as commands too
            self.tick()
            names = {os.path.basename(w).lower() for w in re.split(r"[\s;&|()<>]+", line)}
            if not data and any(INTERPRETER.match(x) for x in names):
                self.code(body, bases)
        words = shell_scan.shell_words(main)
        self.tick(len(words))
        segments = shell_scan.segments(words)
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
                j = shell_scan.skip_options(words, i + 1, git_values)
                if j < len(words) and words[j] == "push":
                    return True
            if name == "gh":
                j = shell_scan.skip_options(words, i + 1,
                                            {o.lower() for o in GH_OPTIONS_WITH_VALUE})
                if tuple(words[j:j + 2]) in GH_PUBLISH:
                    return True
        return False
