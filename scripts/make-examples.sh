#!/bin/sh
# Regenerate the board example and its screenshots from examples/sample-project.
#
#   scripts/make-examples.sh
#
# Writes docs/examples/board.html (always) and docs/images/board-light.png,
# board-dark.png and issue-view.png (only when Chrome is found).
#
# Chrome is looked for in this order: $CHROME, then
#   /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
# then google-chrome, google-chrome-stable, chromium and chromium-browser on PATH.
# If CHROME is set it is the only one tried: when it is not executable, no other
# path is used. With no Chrome the board is still written and a note says the
# screenshots were skipped (exit 0). A Chrome call that fails prints
# "error: Chrome failed for <file>" and exits 1.
#
# The board is built from a temporary copy of examples/sample-project, outside any
# git repository, so this repository's own worktrees (feature/<id> branches) can
# never show up on the sample board, and the build line says "unknown commit".
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
board="$here/docs/examples/board.html"
images="$here/docs/images"

mkdir -p "$here/docs/examples" "$images"
work=$(mktemp -d "${TMPDIR:-/tmp}/make-examples.XXXXXX")
trap 'rm -rf "$work"' EXIT
cp -R "$here/examples/sample-project" "$work/sample-project"
if git -C "$work/sample-project" rev-parse --git-dir >/dev/null 2>&1; then
  echo "error: $work is inside a git repository; set TMPDIR to a folder outside any repository" >&2
  exit 1
fi
python3 "$here/bin/dev-harness-board" --root "$work/sample-project" --out "$board"

chrome=""
if [ -n "${CHROME:-}" ]; then
  if [ -x "$CHROME" ]; then
    chrome=$CHROME
  fi
elif [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  for name in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$name" >/dev/null 2>&1; then
      chrome=$(command -v "$name")
      break
    fi
  done
fi

if [ -z "$chrome" ]; then
  echo "note: Chrome not found (set CHROME=/path/to/chrome); wrote the board only, screenshots skipped" >&2
  exit 0
fi

profile="$work/profile"

shot() {  # shot <output.png> <size> <url> [extra chrome flag]
  "$chrome" --headless=new --disable-gpu --hide-scrollbars --no-first-run \
    --user-data-dir="$profile" --window-size="$2" --screenshot="$1" ${4:-} "$3" >/dev/null 2>&1 ||
    { echo "error: Chrome failed for $1" >&2; exit 1; }
}

url="file://$board"
# Set the scheme explicitly: headless Chrome otherwise follows the machine setting.
shot "$images/board-light.png" 1400,900 "$url" --blink-settings=preferredColorScheme=1
# Blink's preferredColorScheme: 0 is dark, 1 is light.
shot "$images/board-dark.png" 1400,900 "$url" --blink-settings=preferredColorScheme=0
# 770 high: ends after the Scope section, above the done-criteria table that would be cut off.
shot "$images/issue-view.png" 1100,770 "$url#APP-3" --blink-settings=preferredColorScheme=1
echo "wrote $images/board-light.png, board-dark.png, issue-view.png"
