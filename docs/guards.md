# Guards, rule by rule

This page is the detail behind the guard summary in the [README](../README.md). It lists what each guard rule stops, how it matches, and what it cannot see (the known gaps). The settings that change the rules are in [configuration.md](configuration.md).

Every rule is a hook script in `hooks/scripts/`. Each one either asks you at a permission prompt, blocks the call, or lets it through. Tests: `python3 -m unittest discover -s tests`.

## What the guard does not promise

Hooks are best-effort guardrails, not a sandbox. Claude Code permission deny rules are the hard boundary; keep them for anything that must never happen. A Bash command too large to check (over the check's work budget, a single word over 32 KB, or over 256 KB in all) asks: "command too large to check; approve manually". A check that fails with an error asks too.

## Rules

The rules run on every Bash, Read and Edit/Write call. "Asks" means a permission prompt for you; "blocks" means the call is refused.

### Bash: credential reads

- **Stops:** Reading keys, keystores, `.p8` files, service-account JSON, `.env` files and SSH private keys (the `.pub` files pass), plus your `protectedPaths`, with `cat`, `cp`, `grep` and similar.
- **Ask or block:** Blocks.
- **Known gaps:** Interpreter heredocs (`python3 - <<'EOF'`) that only mention a credential path stay blocked; a read inside `$(...)` in double quotes is not matched; a script written in one command and run in a later one, or run by an indirect runner, is not seen.

#### Details

Blocks `cat`/`cp`/`grep`/... on keys, keystores, `.p8`, service-account JSON, `.env`, SSH private keys (`id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` and the `-sk` variants, in any folder; the `.pub` files pass) (plus `protectedPaths`). Shell punctuation glued to a word (`$(cat .env)`, a backtick read, `{ cat .env; }`) is ignored when matching. A path that appears only in the body of a plain-data heredoc (the same rule as for protected configs below: `cat > file`, `cat >> file`, `tee file`, `git commit -F -`, `git tag -F -`, quoted delimiter or no `$`/backtick in the body, nothing piped onward, the file not named again later) is text being written, not a read, and passes; a path anywhere else in the same command still blocks, at any size. Nothing is exempt over 256 KB, or when the command has a carriage return, a `#` or `${` outside the bodies, an `alias`, a `cat`/`tee`/`git` function, a `PATH=` or `hash` change, `mkfifo` or an `exec` redirect, or a heredoc writing under `/dev/` or `/proc/`. A file written this way and then run is caught only when the same command names the file again (`sh run.sh`); a later command, or an indirect runner (`make`, a glob such as `sh *.sh`, a dotfile that a shell sources, a git hook) is not seen. Known gaps: interpreter heredocs (`python3 - <<'EOF'`, `node - <<'EOF'`) that only mention a credential path stay blocked, so write such test text with the Write/Edit tools; a read inside `$(...)` in double quotes (`echo "$(cat .env)"`) is one shell word and is not matched.

### Bash: publishing

- **Stops:** `git push`, `gh pr create/merge`, `gh repo create/delete`, `gh release create`, `gh gist create` (and gh's `new` aliases).
- **Ask or block:** Asks, blocks, or leaves it to your permission rules, as `pushPolicy` says (default: ask).
- **Known gaps:** See Push policy below: variables, aliases, scripts, `gh api` and some other push forms are not detected.

### Bash: deploys and destructive cloud commands

- **Stops:** `firebase deploy` and destructive Firebase, gcloud, terraform and kubectl commands, plus your `askCommands`. Text about them in a plain-data heredoc body (a checklist being written) passes.
- **Ask or block:** Always asks, whatever `pushPolicy` says.
- **Known gaps:** Detection matches text; scripts that deploy are not seen unless you add them to `askCommands`. A file written by a data heredoc and then run without being named (through a glob, `make` reading a Makefile, `npm test` reading package.json, or a multi-line group piped to a shell) is not seen.

#### Details

Matches `firebase deploy` (and `firestore:delete`, `functions:delete`, `functions:config:set`, `hosting:disable`, `use`), a line with `gcloud` followed by `deploy`, `delete`, `set-iam-policy` or `update`, `terraform apply/destroy`, `kubectl apply/delete`, and each `askCommands` entry as literal text, with backslash-newline continuations joined (`firebase \` + `deploy`). Text that lies entirely inside the body of a plain-data heredoc (a ticket or release checklist written with `cat > docs/release.md <<'EOF'` that mentions `gcloud run services update`) is text being written and passes, under the same conditions as in the credential reads rule: nothing is exempt over 256 KB, or when the command has a carriage return, a `#` or `${` outside the bodies, an `alias`, a `cat`/`tee`/`git` function, a `PATH=` or `hash` change, `mkfifo` or an `exec` redirect, or a heredoc writing under `/dev/` or `/proc/`. The same command still asks when it runs for real: outside the body (before it, after the closing delimiter, or after `;` or `&&` on the heredoc line), in a body that runs as code (`python3 - <<'EOF'`, `sh <<'EOF'`, `cat <<'EOF' | bash`, an unquoted body with `$(...)` or backticks), inside `bash -c '...'`, or in a file written and then named again in the same command (`sh run.sh`).

### Bash: git stash and `core.hooksPath`

- **Stops:** A `git stash` that changes the stash or working tree when the repo has more than one worktree (they share one stash), and any change to `core.hooksPath`.
- **Ask or block:** Asks.
- **Known gaps:** Git aliases, commands built from variables, scripts, editing `.git/config` directly, and a `cd` into another repo before the stash.

#### Details

Asks before a `git stash` that changes the stash or the working tree (bare, `push`, `save`, `pop`, `apply`, `drop`, `clear`, ...) when the repo has more than one worktree, because every worktree shares one stash and another agent could pop it (`git -C <dir>` counts `<dir>`'s repo; `list` and `show` always pass). It runs `git worktree list --porcelain` (2 s timeout) only then; if git cannot count, it asks ("could not count worktrees"). Also asks before `git config core.hooksPath ...` (any scope, `--unset`, `--unset-all`, any letter case) and `git -c core.hooksPath=... <cmd>`, since the setting replaces `.git/hooks` and silently disables hooks such as Git LFS's pre-push; reads (`--get`, `--list`, the key alone) pass. Known gaps: git aliases, commands built from variables, scripts, editing `.git/config` directly, and a `cd` into another repo before the stash (the worktree count is taken where the command starts; `git -C` is followed).

### Bash and Edit/Write: the board's git hooks

- **Stops:** `dev-harness-board install-hooks` and `uninstall-hooks`, and writes to the board's hooks folder.
- **Ask or block:** Asks, under every `pushPolicy`.
- **Known gaps:** A symlink into the folder, a `cd` into it followed by a relative write, commands built from variables.

#### Details

Asks, under every `pushPolicy`, before `dev-harness-board install-hooks` or `uninstall-hooks` (any path to the command, `python3 .../dev-harness-board`, after `&&`, `;`, `env`, `command`, in `bash -c` text), because they change `core.hooksPath` in their own `git config` call, which the rule above cannot see. `--check` (and `--json`, or no subcommand) passes. Also asks before Edit/Write, or a shell write (redirect, `cp`, `mv`, `rm`, `sed -i`, `tee`, `chmod`, ...), on a path ending `dev-harness/hooks/<file>` (the untracked folder `<git dir>/dev-harness/hooks`); reading it passes. Shell writes are matched for the folder itself and the files directly in it; Edit/Write ask at any depth below it. A command's quote or shell punctuation glued to the words (`` `dev-harness-board install-hooks` ``, `$(...)`, `{...}`) is stripped, which also makes `` `git config --unset core.hooksPath` `` and `$(git config --unset core.hooksPath)` ask. The folder is matched by path shape, not by asking git, so nothing runs and it cannot time out; a path like `x/dev-harness/hooks/y` outside a git dir asks too (a harmless extra prompt): a checkout whose own path contains `dev-harness/hooks/`, such as the plugin's own repo, gets an extra Edit prompt for files under it. Known gaps: a symlink into the folder, a `cd` into it followed by a relative write, commands built from variables.

### Bash: commits of generated files

- **Stops:** A `git commit` that would add generated-looking files (`node_modules/`, `build/`, `dist/`, `.gradle/`, `DerivedData/`, `__pycache__/`, `.worktrees/`, `*.pyc`, the board output, ignored files).
- **Ask or block:** Asks.
- **Known gaps:** `git add -A && git commit` in one command, and commits made through aliases, scripts or other tools.

#### Details

Asks before a `git commit` (also `-am`, `--all`, `git -C <dir> commit`, chained after other commands, inside `sh -c`) when it would add files that look generated: anything under `node_modules/`, `build/`, `dist/`, `.gradle/`, `DerivedData/`, `__pycache__/` or `.worktrees/`, `*.pyc`, `*.class`, `*.o`, the configured board output (`tickets.boardPath`), or a file `.gitignore` would ignore (force-added; `git check-ignore --no-index`). Folder names match at any depth (`app/build/x` asks). The prompt lists up to 10 paths. Only newly added files count: modifications, deletions and `git commit --amend --no-edit` with nothing new pass. It runs `git rev-parse`, `git diff --cached --no-renames --name-only --diff-filter=A` (so a rename or copy into `dist/` counts as an add) and `git check-ignore` (one shared 3 s deadline) only when a commit is detected, in the repo the commit targets (`git -C` is followed). If git fails or times out, hits already found still ask, and a warning ("could not check staged files: ...") is shown; with no hits the call is allowed with that warning, never silently. Each `git -C` folder is checked separately (the first 4; the rest are reported as not checked). A commit after a `cd`, or with `--git-dir`, `--work-tree`, `GIT_DIR=` or `GIT_WORK_TREE=`, is not checked and says so in a warning. If an earlier rule in the same command already asks (push, stash, `core.hooksPath`, protected config), the commit's files are not checked separately. Known gaps: `git add -A && git commit` in one command (the hook runs before the add, so it sees only what was staged earlier), and commits made through aliases, scripts or other tools.

### Bash: writes to protected configs

- **Stops:** Shell writes, moves, copies, deletes and mode changes of `.claude/harness.json`, `.claude/settings*.json`, lint and test configs and your `protectedConfigs`.
- **Ask or block:** Asks. Reads pass.
- **Known gaps:** Paths built from variables, scripts, `tar`, `patch`, `git apply`, `git stash pop` with one worktree, formatters such as `prettier --write`; a `#` that the scanner misreads inside `${...}` can hide a write (a known gap). A glob given to `rm`, `cp`, `mv`, `chmod` and similar is checked against the files on disk when the hook runs (see Globs below).

#### Details

Asks when a command looks like it writes to, moves, copies over, deletes or changes the mode of a protected config (`.claude/harness.json`, `.claude/settings*.json`, lint/test configs, `protectedConfigs`): redirections (`>`, `>>`, `>|`, `&>`), `tee`, `sed -i`/`perl -i`/`ruby -i`/`gawk -i inplace`, `cp`/`mv`/`install`/`ln`/`rsync`/`dd of=`/`truncate`/`rm`/`chmod`/`chown`/`touch`/`unzip`, editors (`vim`, `ed`, ...), `git checkout/restore` of the working tree, `sh -c`/`bash -c`/`eval` bodies and shell heredocs, and interpreter code (`python -c`, `node -e`, `ruby -e`, `perl -e`, `python3 - <<EOF`) that names the path. It also counts the config's folder (`rm -rf .claude`, `cp x .claude/`), globs (`.claude/*.json`) a recursive copy into a folder that holds one (`cp -R src/. .`), `git update-index --assume-unchanged`/`--skip-worktree`, and a `cd`/`git -C` earlier in the same command; on macOS, any letter case. Heredoc bodies are checked as commands, except plain data: the body of `git commit -F -`, `git tag -F -`, `cat > file`, `cat >> file` or `tee file`, with a quoted delimiter (or no `$`/backtick in the body), found closed, on a line that pipes nothing onward, and (for `cat`/`tee`) writing a file the rest of the command does not name again, so a script written and then run is checked. Before the word checks, `#` comments are removed on lines without `$(`, backticks or `((`; the scanner can misread quotes nested inside `${...}` or a multi-line `$(...)` and remove too much. No heredoc body is exempt, for push and config writes alike, when the command has a carriage return, a `#` or `${` outside the bodies, an `alias`, a `cat`/`tee`/`git` function, a `PATH=` or `hash` change, `mkfifo` or an `exec` redirect, or a heredoc writing under `/dev/` or `/proc/` (the same conditions as in the credential reads rule): every push and config match counts. A protected-config write hidden by a `#` that the scanner misreads inside `${...}` is therefore still caught when a data heredoc is present; without one, the `#` comment removal above can still hide it (`echo "${x:-"a # b"}"; rm .claude/settings.json`, a known gap). Reads pass: `cat`, `grep`, `jq`, `git diff`, `git restore --staged`, `python3 -m json.tool`, test runners given `--config`. Interpreter code that names a config asks even when it only reads it. This catches common shell writes, not all of them: paths built from variables, scripts, `tar`, `patch`, `git apply`, `git stash pop` in a repo with a single worktree (with several, every stash asks; see Git stash and `core.hooksPath` above), formatters such as `prettier --write`, and other indirect paths get through.

#### Globs

The base rule is the name rule above: a glob asks when it could match a protected config by name (`*.xml` could be `**/lint.xml`), wherever it is written. On one narrow path the guard looks in the folder instead, and asks only when a real path is a protected config. Everything outside that path keeps the name rule, and every check below fails closed: when it cannot decide, it asks.

**When the folder is looked at.** All of these must hold, or the name rule applies:

- the glob is an argument of `rm`, `unlink`, `cp`, `mv`, `ln`, `install`, `touch`, `truncate`, `chmod`, `chown` or `chgrp`, as the first word of the command, spelled exactly so (no `sudo`, `env`, path, `NAME=` prefix or keyword in front);
- the glob is a plain word: no `**`, `[...]`, `..` part, variable, `~`, quotes, braces or other shell syntax, and not joined to other shell text (`x$(...)dir/*.xml`, a zsh qualifier such as `*.xml(D)`);
- the command line is a flat list of simple commands joined by `;`, `&&`, `||`, `|` or newlines, with redirections. Any other syntax asks: `if`, `then`, `while`, `until`, `for`, `select`, `case`, `function`, `{ }`, `( )`, `!`, `time`, `[[`, `coproc`, `&` (background), `|&`, a `NAME=` prefix, `$(...)`, backticks, process substitution, `${...}`, a `#`, a carriage return, an `alias`, a function definition, a `PATH=` or `hash` change, nested shell text (`sh -c`, `eval`), or a heredoc whose body is read as commands or code;
- every other simple command in the line is on these allow-lists, judged by its exact first word (anything else asks):
  - readers and removers: `echo`, `printf` (not `-v`), `true`, `false`, `test`, `[`, `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg` (not `--pre`), `sort` (not `-o`, `--output` or `--compress-program`), `uniq` (one file at most), `date`, `sleep`, `pwd`, `which`, `rm`, `rmdir`, `unlink`;
  - build and test runners, and git reads, take plain arguments only (owner rule): every word after the runner is letters, digits and `_:.,/+@-` (no `=`, quotes, spaces, `~` or `^`), none is `-`, `/dev/...` or `/proc/...`, the runner does not read stdin (no pipe into it, no `<`, `<<<` or heredoc), and a word starting with `-` must be on that runner's exact list (anything else, including a shortened long option such as `--outp`, asks). Task, target and subcommand names are plain words. The runners: `./gradlew`, `gradlew`, `gradle` (`--info`, `-i`, `--stacktrace`, `-s`, `--offline`, `--no-daemon`, `-q`, `--quiet`, `--continue`, `--warn`, `-w`, `--parallel`, `--rerun-tasks`, `--dry-run`, `-m`, `--build-cache`, `--no-build-cache`); `make` (`-k`, `-s`, `-n`, `-B`, `-j`, `-jN`, `--keep-going`, `--silent`); `xcodebuild` (`-scheme`, `-project`, `-workspace`, `-configuration`, `-sdk`, `-quiet`, `-target`, `-alltargets`, `-list`, `-showBuildSettings`); `pytest` and `python3 -m pytest` (`-q`, `-x`, `-v`, `-vv`, `-s`, `-k`, `--lf`, `--ff`); `python3 -m unittest` (`-v`, `-q`, `-f`, `-b`, `-k`, `-s`, `-t`, `-p`); `npm` `test`/`run <name>`, `pnpm`/`yarn` `test`, and `npm`/`pnpm`/`yarn` `install`/`ci`/`i` with no word after them (no options; `npm install ./app` links a folder and `yarn run` or `pnpm run` falls back to any installed CLI, so they ask); `swift build`/`test` (`--parallel`, `-v`, `--verbose`, `-q`, `--quiet`); `cargo build`/`test` (`--release`, `-q`, `--quiet`, `-v`, `--verbose`, `--all`, `--workspace`, `--lib`, `--bins`, `--tests`, `--no-fail-fast`, `--locked`, `--offline`, `--frozen`); `go build`/`test` (`-v`, `-race`, `-short`, `-cover`, `-failfast`, `-count`, `-run`);
  - `git` read subcommands right after `git` (`status`, `log`, `diff`, `show`, `rev-parse`, `ls-files`, `ls-tree`, `blame`, `describe`, `shortlog`, `grep`, `cat-file`, `rev-list`, `merge-base`, `help`, `version`), so no `-c`, `-C`, `--config-env` or other option before the subcommand, with the plain-argument rule above and options from this list: `--oneline`, `--stat`, `--short`, `--porcelain`, `-s`, `--name-only`, `--name-status`, `-b`, `--cached`, `--staged`, `--no-color`, `--graph`, `--decorate`, `--all`, `-p`, `--patch`, `--abbrev-commit`, `-q`, `--quiet`, `--exit-code`, `--numstat`, `--shortstat`, `--branch`, `--show-toplevel`, `--abbrev-ref`, `--verify`, `--is-inside-work-tree`, `--reverse`, `--follow`, `--first-parent`, `--merges`, `--no-merges`, `-l`, `-i`, `-n`, `-N`/`-nN` (a number), `--`. So `git show HEAD~1` or `git log --format=%H` asks;
  - `cd` as the first word with one plain argument: an absolute path, `./path` or `.`, or a plain name (`cd app`) when `CDPATH` is unset or empty in the hook's environment. `cd -`, `cd +1`, `cd` alone, a `..` part, `pushd` and `popd` ask, and so does a `cd` into a folder that does not exist (it fails, or `cdable_vars`/`cdablevars` sends it elsewhere). Earlier folders stay in the list, so the glob is listed in each;
- no command in the line names `CDPATH`, in any letter case and however it is quoted or escaped (`C""DPATH`, `C\DPATH`);
- a redirect of another command does not write into the glob's folder, inside it or into a parent of it (symbolic links are followed when comparing), and its target has no variable, glob, brace or `~`. Copy, move, link and other placing commands (`cp`, `mv`, `ln`, `install`, `rsync`, `tee`, `tar`, `unzip`, ...) are not on the lists, so they always ask.

**What the listing does.** It lists the folders the way the shell would, as a superset: dot files, `.` and `..`, and any letter case count. It looks through every matched folder too, so `rm -rf build/*` asks when a `lint.xml` is anywhere under `build/`. A file reached through a symbolic link counts by its real path too. It asks when a folder to list is outside the working directory and the project roots, cannot be read, or is itself on the way to a protected config (`rm .claude/*.json` asks even with no file there yet); when the glob matches more than 200 paths (counting everything under matched folders), a folder has more than 5000 entries, or the listing runs out of the check's time budget; and when a matched name has glob characters, starts with `-` where it would be read as an option (`rm *` with a file called `-rf`), is `.` or `..`, is a link to a folder, or has other hard links. A folder that does not exist matches nothing; the glob then reaches the command as a literal name, which is checked like any other path.

A brace group (`{a,b}`, `{1..9}`) is read as `*` when names are matched, so `rm .claude/{harness,x}.json` asks (before, brace words were not matched at all). A glob whose name cannot match any protected config (`rm build/*.log`) passes without a listing, as before. The prompt names the command word, the part of the command and the protected pattern, for example ``rm: `rm -f build/out/*.xml` reaches build/out/lint.xml, which matches **/lint.xml``, or says why the folder was not looked at.

Why a build may share the line (owner decision): the guard protects the configs that exist when the command starts, and the listing sees exactly those, so `./gradlew test > /tmp/x.log 2>&1; echo $?; rm -f app/build/test-results/*.xml` passes when that folder holds no protected config. Limits: a file that a listed build or test runner, or another process, creates or moves into the folder before the glob runs is not seen. The runner's own project files decide what it runs, and the guard does not read them: `build.gradle`, `Makefile`, `package.json` scripts, `.cargo/config.toml` (a test `runner`), `conftest.py`, an Xcode scheme. The repository's own git config (`core.fsmonitor`, `diff.external`, `*.textconv`, `filter.*`, `core.pager`) runs during the git reads, and a `git config` that sets one of them earlier is not caught by this rule (`git config` is not on the lists, so it asks in the same line, but not in an earlier command). The same holds for settings that persist in a tool's own config and carry code into a later listed build, for example `go env -w GOFLAGS=...`, `npm config set script-shell ...` or `.cargo/config.toml`. An `rm` or `chmod` redefined in your shell profile (an alias or function that takes patterns) is not known to the guard.

### Bash: machine-wide device resets

- **Stops:** `simctl shutdown all`, `simctl delete/erase all`, `killall Simulator/xcodebuild`, `adb emu kill`, `killall qemu-system*`, while another build of that kind is running.
- **Ask or block:** Blocks.
- **Known gaps:** Indirect runners (`make`, globs, dotfiles, git hooks); a reset split by backslash-newline inside a body that a shell runs (an older gap).

#### Details

Preset rules (see [Device presets](#device-presets)): block `simctl shutdown all`, `simctl delete/erase all`, `killall Simulator/xcodebuild` (preset `apple-simulators`) and `adb emu kill`, `killall qemu-system*` (preset `android-emulators`) while another `xcodebuild` / `gradlew` run is active, so parallel agents do not kill each other's devices. Such a command written as text in a plain-data heredoc body passes, under the same conditions as in the credential reads rule; in `cat <<'EOF' | sh`, `bash <<'EOF'`, or a script written and then named again in the same command (`sh run.sh`), it is still caught. Indirect runners (`make`, globs, dotfiles, git hooks) are a known gap, as in the credential reads rule. Backslash-newline continuations are joined in the command itself (`simctl \` + `shutdown all`), but not inside a body that a shell runs (`bash <<'EOF'`, `sh -c '...'`): a reset split that way is not caught (an older gap).

### Read: credentials

- **Stops:** The Read tool on protected credential files.
- **Ask or block:** Blocks.
- **Known gaps:** Only the Read tool is checked (the hook matches `Read`); other tools that read files, such as Grep, are not. A file is matched by its path against the credential patterns and your `protectedPaths`, so a copy under another name is not caught.

### Edit/Write: protected configs

- **Stops:** Edits to lint and format configs, test plans and runner configs, `.claude` settings and your `protectedConfigs`.
- **Ask or block:** Asks.
- **Known gaps:** Edits made some other way (see the Bash rule for protected configs).

### Edit/Write: weakened checks

- **Stops:** An Edit or Write that adds a test skip, accessibility-audit filter or lint/type suppression (`XCTSkip`, `it.skip`, `@Ignore`, `# noqa`, `@ts-ignore`, `eslint-disable`, ...). It looks at file edits only: `git commit --no-verify` typed in Bash is not stopped.
- **Ask or block:** Asks. Moving or removing an existing marker passes.
- **Known gaps:** Only the listed markers (extend with `weakeningMarkers`; turn off with `checkWeakening`).

#### Details

Asks when an edit *adds* a test skip, accessibility-audit filter, or lint/type suppression (`XCTSkip`, `@skip`, `it.skip`, `@Ignore`, `@Suppress`, `# noqa`, `@ts-ignore`, `eslint-disable`, `--no-verify`, ...; extend with `weakeningMarkers`, turn off with `"checkWeakening": false`). Moving or removing an existing one is allowed.

### Notification

- **Stops:** Nothing. It tells you when Claude needs you.
- **Ask or block:** A macOS desktop notification (`"notify": false` turns it off).
- **Known gaps:** Notifications work on macOS only.

## Device presets

The machine-wide device-reset blocks are two presets. Each blocks its commands only while another build of its kind is running (per-device commands such as `simctl delete <UDID>` always pass).

- `apple-simulators`: `simctl shutdown all`, `simctl delete|erase all` (also with `--set <path>`), `killall Simulator|xcodebuild`; busy check on `xcodebuild`.
- `android-emulators`: `adb emu kill`, `killall qemu-system*`; busy check on `gradlew`.

A preset switches on automatically when a project root (the `CLAUDE_PROJECT_DIR` or repo root, so a worktree counts) or one of its direct subfolders holds an `*.xcodeproj` / `*.xcworkspace` (apple) or a `gradlew` (android). Detection only runs when a reset command is seen and lists just those folders (no deep walk; dot folders and `node_modules` are skipped). If a folder cannot be listed the preset counts as on and a warning is shown. Force a preset on with `"presets": ["apple-simulators"]` or off with `"disabledPresets": ["android-emulators"]` in `harness.json`. Without a mobile project and without the key, the rules are off. Deploy and destructive cloud commands (Firebase, gcloud, terraform, kubectl) stay default asks.

## Push policy (`pushPolicy`)

`pushPolicy`, set in [`.claude/harness.json`](configuration.md#keys), decides how the guard treats git push and GitHub publishing: `git push` (in any form: `git -C dir push`, `git -c k=v push`, after `env`/`command`/`sudo`, chained with `&&`, `;`, `|` or in a subshell, split with `\`-newline), `gh pr create`, `gh pr merge`, `gh repo create`, `gh repo delete`, `gh release create` and `gh gist create`, including gh's `new` aliases and `-R`/`--repo` before the subcommand.

| Value | Behavior |
|---|---|
| `"ask"` (default) | You approve each one at a permission prompt. Also used when no config exists or the value is invalid. |
| `"deny"` | Blocked outright (exit 2). The message tells the agent the owner pushes by hand. |
| `"allow"` | No prompt from this hook, as long as git confirms that no `harness.json` in the repository (and none of the loaded ones) differs from the last commit. A modified, staged, deleted, untracked or ignored `harness.json`, one marked assume-unchanged or skip-worktree in the index, or a git error (for example outside a git repository), still asks: "push policy file changed in the working tree". Your own Claude Code permission rules still apply. Meant for your own interactive sessions. The hook cannot tell a subagent from the main session, so `"allow"` applies to subagents too; agents are kept from pushing by their instructions and your permission rules, not by this hook. |

`pushPolicy` covers only git/GitHub publishing. Deploys and destructive cloud commands (`firebase deploy`, `gcloud ... deploy/delete`, `terraform apply/destroy`, `kubectl apply/delete`) and every `askCommands` entry always ask, whatever the policy, including when chained onto an allowed push; text about them inside a plain data heredoc body does not (see [deploys and destructive cloud commands](#bash-deploys-and-destructive-cloud-commands)). When config files merge, the strictest value wins (`deny` > `ask` > `allow`).

Turning on `"allow"` takes a change to `harness.json`. An Edit/Write of it asks, and so do the common shell writes to it (see the rules above). Whatever shell form was used, a push under `"allow"` still asks until the change is committed, and a commit is something you see. Permission deny rules (for example on `Bash(git push:*)`) are the hard boundary.

Detection matches text, so it errs towards asking: under `"deny"` a command that merely mentions `git push` in quotes (a commit message, a `grep`) is blocked too; reword it or run it yourself. Plain data heredocs (`git commit -F - <<'EOF'`, `cat > notes.md <<'EOF'`, as described in the credential reads rule above, and only when none of the exemption-off conditions listed there holds) are not read as commands; every other heredoc body is, including one piped to a shell or interpreter. Not detected: shell variables, git aliases for push, scripts (`$G push`, `./release.sh`), `gh api` calls, `git lfs push`, `git subtree push` and `git send-pack`; add such scripts to `askCommands` and keep a permission deny rule.

## Other cases the guard reports

The guard reports these instead of allowing silently. Config problems are shown on stderr, as `systemMessage` and as `additionalContext`; whether Claude Code shows `systemMessage` alongside a permission prompt is unverified. The cases: hook input that cannot be read (warning, call unchecked); a guard module that cannot be imported (asks for Bash, Read and Edit with "dev-harness guard could not load"); a crash inside the Bash or Read guard (allowed with a warning, but asks when the command or path looks like a credential file); a Bash command over 262144 characters (the credential pass still runs on plain whitespace-split words and a credential hit still blocks; with no hit, or when its 4 s deadline runs out, it asks "too large to check"); `pgrep` missing, timed out or failing when a machine-wide device reset is seen (asks); and a failing desktop notification (reported once per session).

## Planned after 1.0

Two pieces of work are planned after 1.0 (see the Limits section of the [README](../README.md#limits)):

- Letting agents push feature branches without a prompt ([ticket CH-09](tickets/CH-09.md)). Until it lands, `git push` always asks (or is blocked under `"deny"`), and agents are kept from pushing by their instructions and your permission rules.
- Adopting an existing ticket tracker ([ticket CH-19](tickets/CH-19.md)). `/ticket-init` sets up new projects only.
