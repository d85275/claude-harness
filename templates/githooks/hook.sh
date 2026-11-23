#!/bin/sh
# dev-harness-board hook v1. Written by `dev-harness-board install-hooks`; edits are overwritten.
#
# Lives in <git common dir>/dev-harness/hooks (untracked), which core.hooksPath points at.
# Every hook there is this same script; it takes its name from the file name.
# 1. It runs the repository's own hook of the same name (<git common dir>/hooks/<name>, for example
#    Git LFS), when that file exists and is executable, with the same arguments and stdin, and
#    exits with that hook's exit code (0 when there is none).
# 2. For post-checkout, post-commit, post-merge and post-rewrite it then rebuilds the ticket board
#    with `dev-harness-board hook <name> <args>`. The rebuild never changes the exit code, prints
#    at most one line on stderr, and is stopped after DEV_HARNESS_HOOK_TIMEOUT seconds (default 5).
#
# dev-harness-board is looked up in this order: $DEV_HARNESS_BOARD (a path to it, or "off" to skip
# the rebuild quietly), the PATH, then the dev-harness entry in Claude Code's plugin list
# (${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/installed_plugins.json). When none is found the hook
# prints one line and the git command carries on.

name=${0##*/}
common=$(git rev-parse --git-common-dir 2>/dev/null) || common=

# A repository hook that hands back to this script (a "delegate" wrapper) would loop forever.
# Inside our own chained call this repository's hook is marked active (keyed by the common dir,
# so a hook that runs git in another repository still gets that repository's hooks): run
# nothing a second time.
active=
if [ -n "$common" ]; then
    active=$(cd "$common" 2>/dev/null && pwd -P) || active=
fi
active=$active/$name
if [ "${DEV_HARNESS_HOOK_ACTIVE-}" = "$active" ]; then
    exit 0
fi

status=0
input=
errs=

cleanup() {
    if [ -n "$input" ]; then rm -f "$input"; fi
    if [ -n "$errs" ]; then rm -f "$errs"; fi
}
trap cleanup EXIT
trap 'cleanup; trap - EXIT; exit 129' HUP
trap 'cleanup; trap - EXIT; exit 130' INT
trap 'cleanup; trap - EXIT; exit 143' TERM

own=
if [ -n "$common" ] && [ -f "$common/hooks/$name" ] && [ -x "$common/hooks/$name" ] &&
    ! [ "$common/hooks/$name" -ef "$0" ]; then
    own=$common/hooks/$name
fi

case $name in
    post-checkout | post-commit | post-merge | post-rewrite) ;;
    *)
        # Pass-through hook: the repository's own hook decides everything.
        trap - EXIT HUP INT TERM
        if [ -n "$own" ]; then
            DEV_HARNESS_HOOK_ACTIVE=$active exec "$own" "$@"
        fi
        exit 0
        ;;
esac

# post-rewrite gets the rewritten commits on stdin; keep a copy for the board. A copy that could
# not be written in full is removed, and the board then rebuilds without it.
if [ "$name" = post-rewrite ] && [ "${DEV_HARNESS_BOARD-}" != off ]; then
    input=$(mktemp "${TMPDIR:-/tmp}/dev-harness-hook.XXXXXX" 2>/dev/null) || input=
fi

if [ -n "$own" ]; then
    if [ -n "$input" ]; then
        # XFSZ ignored (in this subshell only): a file size limit fails the copy, not the stream
        (
            trap '' XFSZ
            tee "$input" 2>/dev/null || rm -f "$input"
        ) | DEV_HARNESS_HOOK_ACTIVE=$active "$own" "$@"
    else
        DEV_HARNESS_HOOK_ACTIVE=$active "$own" "$@"
    fi
    status=$?
elif [ -n "$input" ]; then
    (
        trap '' XFSZ
        cat >"$input" 2>/dev/null
    ) || rm -f "$input"
fi

find_board() {
    if [ -n "${DEV_HARNESS_BOARD-}" ]; then
        if [ -f "$DEV_HARNESS_BOARD" ] && [ -x "$DEV_HARNESS_BOARD" ]; then
            printf '%s\n' "$DEV_HARNESS_BOARD"
        fi
        return 0
    fi
    found=$(command -v dev-harness-board 2>/dev/null) || found=
    case $found in
        /*)
            printf '%s\n' "$found"
            return 0
            ;;
    esac
    list=${CLAUDE_CONFIG_DIR:-${HOME:-/nonexistent}/.claude}/plugins/installed_plugins.json
    if [ -f "$list" ]; then
        python3 -I -c '
import json, os, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        plugins = json.load(f).get("plugins", {})
    for key, entries in plugins.items():
        if key.split("@")[0] != "dev-harness":
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            p = os.path.join(str(e.get("installPath", "")), "bin", "dev-harness-board")
            if e.get("installPath") and os.path.isfile(p) and os.access(p, os.X_OK):
                print(p)
                sys.exit(0)
except Exception:
    pass
' "$list" 2>/dev/null
    fi
    return 0
}

if [ "${DEV_HARNESS_BOARD-}" != off ]; then
    board=$(find_board)
    if [ -z "$board" ]; then
        if [ -n "${DEV_HARNESS_BOARD-}" ]; then
            echo "dev-harness: board not rebuilt: DEV_HARNESS_BOARD is not an executable file" >&2
        else
            echo "dev-harness: board not rebuilt: dev-harness-board not found (enable the dev-harness plugin, or set DEV_HARNESS_BOARD to its path or to off)" >&2
        fi
    else
        limit=${DEV_HARNESS_HOOK_TIMEOUT:-5}
        case $limit in
            '' | *[!0-9]*) limit=5 ;;
        esac
        from=/dev/null
        if [ -n "$input" ] && [ -f "$input" ]; then
            from=$input
        fi
        errs=$(mktemp "${TMPDIR:-/tmp}/dev-harness-hook.XXXXXX" 2>/dev/null) || errs=
        "$board" hook "$name" "$@" <"$from" >/dev/null 2>"${errs:-/dev/null}" &
        pid=$!
        (
            n=0
            while [ "$n" -lt "$limit" ]; do
                sleep 1
                kill -0 "$pid" 2>/dev/null || exit 0
                n=$((n + 1))
            done
            kill "$pid" 2>/dev/null || exit 0
            sleep 1
            kill -9 "$pid" 2>/dev/null
        ) </dev/null >/dev/null 2>&1 &
        dog=$!
        # stderr off here: some shells report the killed job ("Terminated") on it
        {
            wait "$pid"
            rc=$?
            kill "$dog"
            wait "$dog"
        } 2>/dev/null
        if [ "$rc" -ge 128 ]; then
            echo "dev-harness: board rebuild stopped after ${limit}s" >&2
        elif [ -n "$errs" ] && [ -s "$errs" ]; then
            printf 'dev-harness: %s\n' "$(head -n 1 "$errs")" >&2
        elif [ "$rc" -ne 0 ]; then
            echo "dev-harness: board rebuild failed (exit $rc)" >&2
        fi
    fi
fi

# Only the repository's own hook decides the exit code; the board never fails the git command.
exit "$status"
