# Configuration (`.claude/harness.json`)

The full reference for the plugin's settings file. The guard rules these settings change are described in [guards.md](guards.md). A complete example with every key is in [`templates/harness.json`](../templates/harness.json); the shared loader is `hooks/scripts/harness_config.py` (`DEFAULTS` there is the list of keys and defaults).

Every key is optional; a missing file means all defaults. Config files are merged, never overridden. The sources are `$CLAUDE_PROJECT_DIR/.claude/harness.json` (when present) and the nearest `.claude/harness.json` found walking up from the working directory, stopping at the repository root (first folder containing `.git`, a folder or a worktree file; without one, at `$CLAUDE_PROJECT_DIR` if inside it, else only the working directory). Without `CLAUDE_PROJECT_DIR`, every file up to the repository root counts. A file above the repository root is never loaded; the hook only warns that it exists. Each file loads on its own, so a broken one is reported (an unreadable file counts as present, not missing) while the others still apply. The result is never weaker than any single file: lists (`protectedPaths`, `protectedConfigs`, `askCommands`, `weakeningMarkers`) are unioned, `presets` and `disabledPresets` are unioned too (a nested file can switch a preset on but never off for the root; an explicit on beats an off from a different file, and within one file off wins), `pushPolicy` takes the strictest (`deny` > `ask` > `allow`), `checkWeakening` is on if any file says so, and the remaining keys take the root file's value, falling back to a nested file. So a stricter worktree config applies immediately, and a weaker one changes nothing. Identical copies produce no notice; differing ones produce one notice naming both files. Project globs match relative to each file's project folder as well as to the working directory.

## Keys

Every key is optional. "Merge" says what happens when more than one file applies (see above).

| Key | Type | Default | Merge | Meaning |
|---|---|---|---|---|
| `version` | number | `1` | root file | Schema version; only `1` exists. |
| `protectedPaths` | list of globs | `[]` | union | Extra globs the guard treats as credentials. Example: `["keys/**"]`. |
| `protectedConfigs` | list of globs | `[]` | union | Extra globs whose edits need the owner's OK. Example: `["scripts/verify_thresholds.py"]`. |
| `askCommands` | list of strings | `[]` | union | Extra command substrings that always need the owner's OK. Example: `["./scripts/deploy.sh"]`. |
| `checkWeakening` | true or false | `true` | on if any file says so | Ask when an edit adds a test skip or lint suppression. |
| `weakeningMarkers` | list of regexes | `[]` | union | Extra regexes counted as weakening. Example: `["TODO_SKIP"]`. |
| `notify` | true or false | `true` | root file | macOS notification when Claude needs you. |
| `pushPolicy` | `"ask"`, `"deny"` or `"allow"` | `"ask"` | strictest wins | How git push and GitHub publishing are treated; see [Push policy](guards.md#push-policy-pushpolicy). |
| `presets` | list of preset names | `[]` | union | Force device presets on: `"apple-simulators"`, `"android-emulators"` (see [Device presets](guards.md#device-presets)). Unknown names are reported. |
| `disabledPresets` | list of preset names | `[]` | union | Force device presets off. Within one file off wins; an "on" from another file beats it. |
| `highRiskReviewerModel` | `"opus"` or `"fable"` | `"opus"` | root file | `/review` passes it as the model of `high-risk-reviewer`. |
| `tickets` | object | see below | per key | Settings for the ticket board and the ticket commands. Each key below sits inside this object. |

### The `tickets` keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tickets.dirs` | list of folders | `["docs/tickets"]` | Folders holding ticket files. |
| `tickets.featureGlobs` | list of globs | `[]` | Extra globs for ticket files kept outside `dirs`: a glob that matches a folder adds that folder, one that matches a file adds the file. Example: `["src/features/*"]`. |
| `tickets.idPrefixes` | object | `{}` | Ticket ID prefix -> `{"platform": ..., "kind": ...}`. The prefix is the letters before the number (`WEB-12` has prefix `WEB`); platform and kind drive the card icons and the twin link. Example: `{"WEB": {"platform": "web", "kind": "feature"}}`. |
| `tickets.twinPlatforms` | list of strings, or `null` | `null` | Platforms that mirror each other, so a ticket on one has a twin on the other. Example: `["ios", "android"]`. |
| `tickets.archive` | true or false | `false` | Move closed tickets to an archive (needs `archiveTracker` and `archiveDirs`). |
| `tickets.trackerPath` | path | `"docs/tickets/tracker.md"` | The tracker file. |
| `tickets.integrationBranch` | branch name | `"main"` | Branch tickets are integrated onto. |
| `tickets.boardPath` | path | `"docs/tickets/board.html"` | The generated board file. A path that leaves the checkout or points into the git dir is refused. |
| `tickets.stateAliases` | object | `{}` | Your own state names mapped to the standard states (Planned, In progress, In review, Fixing, Owner check, Done, Blocked, Deferred). Example: `{"Todo": "Planned", "Shipped": "Done"}`. |
| `tickets.archiveTracker` | path | `""` | The tracker that receives archived rows. Example: `"docs/tickets/archive/tracker.md"`. |
| `tickets.archiveDirs` | list of folders | `[]` | Folders for archived ticket files; `archive` moves files into the first one. Example: `["docs/tickets/archive"]`. |
| `tickets.extraColumns` | list of strings | `[]` | Tracker columns shown on cards and in the issue view besides the standard ones. Example: `["Owner"]`. |
| `tickets.titleColumn` | string | `""` | The tracker column that holds a card's title, when it is not called `Title`. Example: `"Task"`. See [Check and build](#check-and-build) for the lookup order. |

For `tickets.*` keys the first file that sets the key wins, the root file first, then a nested one.

## Errors in the config

A malformed file, an unknown key, a bad regex or a value of the wrong type is never ignored silently. The hook still enforces the built-in rules, falls back to the default for the bad part (for the lists `protectedPaths`, `protectedConfigs`, `askCommands` and `weakeningMarkers` it keeps the valid entries and a bare string counts as a one-item list), and reports the problem three ways: on stderr, as `systemMessage` for the user, and as `additionalContext` for Claude (on a permission prompt it is also appended to the reason; on a block it is appended to the message). The warning says which project protections are not active. Example with every key: [`templates/harness.json`](../templates/harness.json). The shared loader is `hooks/scripts/harness_config.py`. A key repeated in one file is reported too (the last value is used). The `systemMessage` and `additionalContext` copy is shown once per session and config state (a marker file under the system temp folder, keyed by the problems, the files' mtimes and the session id). The marker is written only after output carrying the warning was printed, so a block or a desktop notification does not use it up. stderr still has it on every call. With no usable session id, or a marker folder that cannot be written or trusted (a file or symlink in its place, another owner), the warning is shown on every call.

## The `dev-harness-config` command

`bin/dev-harness-config get <key>` prints one value of the merged config for the current directory (scalars plainly, lists and objects as JSON). Config warnings go to stderr with exit 1 (the value is still printed); an unknown key or bad usage exits 2. Commands use it because `CLAUDE_PLUGIN_ROOT` is not set in the Bash tool's environment.

## The ticket board commands

The `tickets` keys drive `dev-harness-board`. This is what each command reads and writes.

### Opt-in

The plugin's ticket features act only in a project where a `harness.json` (any file the loader reads, not the built-in defaults) has a `tickets` section; `/ticket-init` writes one. A plain subfolder of an opted-in repository counts as opted in; a `harness.json` in a folder above the repository never opts the repository in; a linked worktree follows its main checkout. `dev-harness-board status` prints whether that is so and why (exit 0 yes, 1 no). Without it, `set-state`, `archive` and `install-hooks` refuse with exit 1 and change nothing, the `hook` mode does nothing, and the plain render writes nothing unless `--out` is given; `--check`, `--json`, `twin` and `init` still work. `/start-ticket`, `/review`, `/close-ticket` and the implementer agents skip their state and board steps there.

### Check and build

`bin/dev-harness-board --check [--root DIR]` reads the tracker and ticket files named by the `tickets` settings (`trackerPath`, `dirs`, `featureGlobs`, `idPrefixes`, `twinPlatforms`, `archive`, plus `stateAliases`, `archiveTracker`, `archiveDirs`, `extraColumns`, `titleColumn`) and prints one line per disagreement: a row and its file with different states, a row without a file (except a Done row or one in the archive tracker, which is old history and needs no ticket file) or a file without a row, an unknown state, a duplicate ID, a link to a missing file, a twin ticket missing. Exit 0 when clean, 1 when there are problems, 2 on bad usage or an unreadable config. `--json` prints the parsed model. It writes nothing, ignores live worktrees (it compares only the main checkout's row with its file) and runs no git for its result.

`bin/dev-harness-board [--root DIR] [--out PATH]` (no mode flag) writes the board as one self-contained HTML file to `tickets.boardPath` (or `--out`), atomically. A `tickets.boardPath` that leads outside the checkout or into the git dir (absolute, `../`, through a symlink, `.GIT` on a case-insensitive disk) is refused with exit 2; `--out` names a file anywhere. The board has six columns (In review also holds Fixing; Blocked also holds Deferred), cards with kind icon, labels (an optional `## Labels` section in the ticket file), review badge, a mismatch badge for tickets `--check` reports, and `extraColumns` values; search and kind/platform/review/mismatch filters; an issue view per ticket (rendered Markdown, details, twin link, copy-link, `#<ID>` deep link); light/dark theme; phone layout. A card's title is the ticket file's `# <ID>: <title>` heading, else the tracker's title column. The column is `tickets.titleColumn` (header name, any case) when set; otherwise `Title`; otherwise the first of `Issue`, `Summary`, `Name`, `Subject` that is not listed in `extraColumns`. Markdown links in the cell show as their text (an image as its alt text), and the title column is not repeated as an extra. The ticket file's heading wins over the tracker cell. `--check` prints a `warning:` line on stderr, exit code unchanged, when `titleColumn` names a column the tracker lacks or one of ID, State, Review, Commit, Note. No network and no external assets. It reads the commit from `.git` without running git. Live worktrees: it runs read-only `git rev-parse --git-common-dir` and `git worktree list --porcelain` (5 s timeout each) and, for a worktree on `feature/<id-lowercase>` of a known ticket, shows the most advanced of main's state, In progress and the state in that worktree's own tracker row or ticket file (committed or not), capped at In review (Planned < In progress < Fixing < In review); Owner check, Done, Blocked and Deferred on main never move. The card shows "from feature/<id>". Prunable and missing worktrees are skipped; with no git, no repo or no worktrees the board builds from main alone. Run from a linked worktree it works on the main checkout and writes the board there. `--check` also prints a `warning:` line on stderr (exit code unchanged) when the board file is older than the tracker or a ticket file.

### Setting state, archiving and twins

`bin/dev-harness-board set-state <ID> <State> [--commit SHA]` sets a ticket's state in one step: the State cell of its tracker row (and the Commit cell) and the leading state word(s) of the Status line in its ticket file, in the checkout it runs in (a linked worktree is not redirected to main). Everything else in both files is kept byte for byte (CRLF, BOM, column padding, links, the rest of the Status line); both edits are computed first and nothing is written if either fails; an unknown state or ticket, a missing Status line, one that does not start with a known state (nothing is replaced; write it by hand), a symlinked, read-only or non-regular target, or a bad commit id exits 1 before anything is written; if the second write still fails the first file is restored. `archive <ID>` (only for a Done ticket; needs `tickets.archive`, `archiveTracker`, `archiveDirs`) moves the row into the archive tracker's table and the file into `archiveDirs[0]` and fixes relative links to it (trackers, ticket files, and the moved file's own links). `twin <ID>` (needs `twinPlatforms`) prints whether the twin ticket is open; it changes nothing. The commands use them: `/start-ticket` sets In progress in the new worktree (uncommitted; the implementer's one commit carries it; it refuses Done/Blocked/Deferred tickets unless the owner says so), `/review` sets In review and, on CHANGES REQUIRED, Fixing (the implementer amends its single commit and sets In review again), `/close-ticket` sets Done with the SHA on the integration branch, reports the twin, archives when on, runs `dev-harness-board` then `--check` (output stops the close) and makes one docs close commit; `/sync` runs `--check` and adds a release check listing open Planned and Owner check tickets. No command pushes.

### Setting up a project: `/ticket-init`

`/ticket-init` (command) sets up tickets in a project that has none: it asks for the ID prefix, integration branch, archive, twin platforms and whether to install the hooks, then runs `bin/dev-harness-board init --prefix PREFIX[:platform[:kind]] [--branch B] [--archive] [--twin-platforms a,b] [--dry-run] [--root DIR]`. `init` writes only files that do not exist: `<dirs[0]>/TEMPLATE.md`, the tracker (States line, empty table), `docs/STATUS.md`, `.claude/harness.json` and the Harness section of `CLAUDE.md` (with a "how tickets work" part and the rule that ticket-flow changes go to the plugin as a request). An existing `harness.json` gets only its missing `tickets` keys added (present keys never change, differing ones are reported as kept; the file is rewritten with 2-space indent; a file that is a symlink, read-only, unreadable, has a repeated key or holds NaN is left alone and the section is printed; a symlinked or read-only `CLAUDE.md` likewise); an existing `CLAUDE.md` Harness section is never edited, the missing lines are printed. A tracker or ticket folder that `init` did not make (no `TEMPLATE.md` beside the tracker) (and is not the empty tracker `init` writes) stops it with a message that adopting existing trackers is not available yet, and nothing is written. It builds the board once, commits nothing and does not install hooks (it prints `dev-harness-board install-hooks` as the next step, which the guard asks about). `--dry-run` lists what it would write and writes nothing. A CRLF `CLAUDE.md` is appended to with CRLF. If a write fails it says what was written ("partially done"); fixing the cause and re-running completes it. Running it again changes nothing and says so.

### Board git hooks

`bin/dev-harness-board install-hooks [--check]` makes git rebuild the board by itself, in this clone only. It writes hook scripts (copies of [`templates/githooks/hook.sh`](../templates/githooks/hook.sh)) into an untracked folder inside the git common dir, `.git/dev-harness/hooks`, for `post-checkout` (runs on `git worktree add` and switches), `post-commit`, `post-merge`, `post-rewrite` (amend, rebase) and `pre-push`, plus one for every other executable hook already in `.git/hooks`, and sets the repository's own `core.hooksPath` to that folder's absolute path. Nothing is committed: each clone opts in by running it, every worktree and every branch (also one made before the install) uses the same scripts, and no branch's tracked files can change them. Run from a worktree, it works on the main checkout's repository.

It refuses (exit 1, nothing changed) when `core.hooksPath` already points anywhere else, in any scope (a global value counts as foreign unless it is exactly this folder), and says what to do; when the folder holds anything it did not write, or a symlink; and when the folder or a folder on the way to it is a symlink. `--check` prints what it would do and writes nothing. If the repository moved, `core.hooksPath` still names the old folder and git runs no hooks at all, Git LFS included: `install-hooks --check` and `dev-harness-board --check` warn about it on stderr, and running `install-hooks` again repairs it. `uninstall-hooks [--check]` unsets `core.hooksPath` only when the repository's own config points it at this folder (or at a dev-harness folder that no longer exists), and leaves the scripts, unused. Neither touches `.git/hooks` or global/system config. The guard asks before shell commands that change `core.hooksPath` and before `install-hooks` / `uninstall-hooks` themselves (not `--check`), and before edits to the hooks folder.

What each script does:

- **Chains first.** Setting `core.hooksPath` stops git from running `.git/hooks`, so each script first runs `.git/hooks/<same name>` (for example Git LFS's `post-checkout`, `post-commit`, `post-merge`, `pre-push`) when it exists and is executable, with the same arguments and stdin, and exits with that hook's exit code (a failing `pre-push` still stops the push). A hook added to `.git/hooks` later needs `install-hooks` again before it runs. While that hook runs, `DEV_HARNESS_HOOK_ACTIVE` names it (the repository's common dir plus the hook name); a script that finds its own repository and name there does nothing, so a `.git/hooks` wrapper that hands back to the hooks folder cannot loop. A git command run inside that hook skips the same-named hook of the same repository too; another repository's hooks still run (a `pre-push` that pushes a second repository).
- **Rebuilds only when needed.** `dev-harness-board hook <name> <args>` rebuilds the board in the main checkout when the commit (a merge commit counts against each parent), merge, rewrite or branch switch changed the tracker, a ticket file or `.claude/harness.json`, when a worktree was added, or when it cannot tell. During a rebase, `post-commit` waits and `post-rewrite` rebuilds once at the end. A `tickets.boardPath` that leads outside the checkout or into the git dir (absolute, `../`, through a symlink, `.GIT` on a case-insensitive disk, a git dir kept in the tree under another name) is refused: the hook prints one line and exits 0.
- **Never fails the git command.** The rebuild cannot change the exit code, prints at most one line on stderr (`dev-harness: ...`), and is stopped after `DEV_HARNESS_HOOK_TIMEOUT` seconds (default 5; TERM, then KILL a second later). Temp files are removed on exit and on HUP, INT and TERM.
- **Finding `dev-harness-board`.** The plugin lives in a different place on each machine, so the scripts name no path. They look for `$DEV_HARNESS_BOARD` (a path, or `off` to skip quietly), then `dev-harness-board` on `PATH` (it is there inside Claude Code), then the `dev-harness` entry in Claude Code's plugin list (`${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/installed_plugins.json`). Without the plugin, each of those hooks prints one line, `dev-harness: board not rebuilt: dev-harness-board not found ...`, and the git command goes on as normal (set `DEV_HARNESS_BOARD=off` to silence it).

**Uninstalling.** Run `dev-harness-board uninstall-hooks` before you uninstall the plugin, or later run `git config --unset core.hooksPath`. If the plugin is gone and the hooks are still set, they print one line that the board was not rebuilt, and git carries on. Outside Claude Code, run the command by its path: `python3 <plugin folder>/bin/dev-harness-board`.

**Git LFS.** `git lfs install` writes into `core.hooksPath`, so after `install-hooks` it would find our scripts there: it refuses to replace them (or, with `--force`, replaces them, and `install-hooks` then refuses the folder). To add LFS later, run `dev-harness-board uninstall-hooks`, then `git lfs install` (it writes into `.git/hooks`), then `dev-harness-board install-hooks` (its scripts chain to the LFS hooks). If `--force` already replaced scripts, delete the files `install-hooks` names from `.git/dev-harness/hooks` first.

The scripts need `/bin/sh` and `python3`.

## Plugin layout

```text
.claude-plugin/   plugin.json, marketplace.json
bin/              dev-harness-config, dev-harness-board (on the Bash PATH while the plugin is enabled)
board/            ticket model, parser and checks behind dev-harness-board
agents/           implementer, implementer-advanced, reviewer, high-risk-reviewer, silent-failure-hunter
commands/         plan, ticket-init, start-ticket, review, close-ticket, sync
hooks/            hooks.json and scripts/:
                    guard.py           hook entry: input, ask/block, crash handler, Read/Edit/notify guards
                    bash_rules.py      Bash rules: credential reads, push policy, always-ask
                                       commands, device resets; runs shell_check
                    shell_check.py     one pass over a command: push detection, config writes
                    shell_scan.py      shell scanner (words, operators, heredocs, quoting) and work budget
                    harness_config.py  harness.json schema, loading and merging
templates/        CLAUDE.md Harness section, harness.json, ticket template, tracker and STATUS skeletons, githooks/hook.sh (board git hooks)
tests/            guard (end to end), push policy, config writes, shell scanner, config loader,
                  agent tier and dev-harness-config tests;
                  tests/data/guard_corpus.txt: commands with the expected decision per policy
```
