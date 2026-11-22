"""Shell text scanner for the Bash guard: words, operators, heredocs, comments, quoting, $'...'
and line continuations, plus the work budget the checks run inside.

Pure functions of their input: no policy, no config, no I/O, no exit. The Bash rules
(bash_rules.py) decide what the words mean; this module only says what the words are.
"""
import os
import re
import shlex
import time

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
OPERATOR_CHARS = "();<>|&\n"

# Work budget for one Bash check; past it the command asks.
CHECK_SECONDS = 1.5
CHECK_STEPS = 400000
LONGEST_WORD = 32768  # shlex is quadratic in the length of one word; longer ones ask
LONGEST_COMMAND = 262144  # longer Bash commands ask without being checked


class CheckTooLarge(Exception):
    pass


class Budget:
    """Steps and seconds left for one check; tick() raises CheckTooLarge once either runs out.
    The limits are read when the budget starts (CHECK_STEPS, CHECK_SECONDS)."""

    def __init__(self):
        self.steps = CHECK_STEPS
        self.deadline = time.monotonic() + CHECK_SECONDS

    def tick(self, n=1):
        self.steps -= n
        if self.steps < 0 or time.monotonic() > self.deadline:
            raise CheckTooLarge()


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
    lexer = shlex.shlex(text, posix=True, punctuation_chars=OPERATOR_CHARS)
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return [w.replace("'", "").replace('"', "")
                for w in re.findall(r"[^\s;&|<>()]+|[;&|<>()\n]+", text)]


def is_operator(word):
    return bool(word) and set(word) <= set(OPERATOR_CHARS)


def skip_options(words, i, with_value=()):
    """Index of the first word after the options that start at words[i]."""
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in with_value else 1
    return i


def segments(words):
    """Split shell words into simple commands at `;`, newline, `&&`, `||`, `|`, `|&` and `&`.
    Returns [(words, redirect targets, here-strings)]: the word after a `>`-style operator is a
    redirect target and the word after `<<<` a here-string; both also stay in the words."""
    result, seg, targets, here = [], [], [], []
    for i, w in enumerate(words):
        if is_operator(w):
            nxt = words[i + 1] if i + 1 < len(words) and not is_operator(words[i + 1]) else ""
            if "<<<" in w:
                here.append(nxt)
            elif ">" in w and nxt:
                targets.append(nxt)
            if ";" in w or "\n" in w or "&&" in w or "||" in w or w in ("|", "|&", "&"):
                result.append((seg, targets, here))
                seg, targets, here = [], [], []
            continue
        seg.append(w)
    result.append((seg, targets, here))
    return result
