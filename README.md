# claude-harness (plugin: `dev-harness`)

A small Claude Code plugin for a disciplined **plan -> implement -> independent review -> integrate** loop. It grew out of porting an Android app to iOS ticket by ticket with Claude Code: one worktree and one commit per ticket, reviews that check evidence rather than claims, and guardrails around anything outward-facing.

**Nothing leaves your machine without your approval.** By default the guard hook asks you before every `git push`, `gh pr create/merge`, `gh repo create/delete`, `gh release create`, deploy and destructive cloud command. Agents never push; you do. See [Push policy](#push-policy-pushpolicy).

## Install

Inside Claude Code:

```text
/plugin marketplace add d85275/claude-harness
/plugin install dev-harness@dev-harness
```

Or from a terminal (the `/plugin` form is not a shell command):

```sh
claude plugin marketplace add d85275/claude-harness
claude plugin install dev-harness@dev-harness
```

Restart running Claude Code sessions to pick up the agents, commands, and hooks. For local development, add the marketplace from a path instead: `claude plugin marketplace add ~/Projects/claude-harness`.

Then add a `## Harness` section to the project's `CLAUDE.md` (template: [`templates/CLAUDE-harness-section.md`](templates/CLAUDE-harness-section.md)) and, optionally, `.claude/harness.json` for project-specific hook rules ([`templates/harness.json`](templates/harness.json)).

## The loop

| Step | Command / agent | Gate |
|---|---|---|
| Plan | `/plan <intent>` | Owner approves scope, done-criteria, allowed files, stop conditions. Skip when a spec already defines the ticket. |
| Implement | `/start-ticket <id>` -> `implementer` (Sonnet), or `implementer-advanced` (Opus) when the ticket's Review level is `high-risk` | Own worktree, one commit, fresh evidence, nothing outward-facing. |
| Review | `/review <id>` -> `reviewer` (Sonnet) or `high-risk-reviewer` (Opus, or Fable via `highRiskReviewerModel`), plus `silent-failure-hunter` for error/offline/recovery work | Reviewer verifies evidence itself; fixes loop back to the implementer; max 3 rounds, then the owner decides. |
| Integrate | `/close-ticket <id>` | Cherry-pick, integrated check, remove worktree/branch/caches/test devices. Never pushes. |
| Check drift | `/sync` | Plan and handoff docs vs. what is actually on the integration branch. |

Model choice is deliberate, by alias only (never a pinned version), and there is no Haiku tier: routine implementation and review run on Sonnet; Opus is used where a missed bug costs the most.

| Agent | Model / effort |
|---|---|
| `implementer` | Sonnet / medium |
| `implementer-advanced` | Opus / high |
| `reviewer`, `silent-failure-hunter` | Sonnet / high |
| `high-risk-reviewer` | Opus / high; Fable when `highRiskReviewerModel` is `"fable"` |

**Upgrade rule.** An implementer that finds its diff riskier than planned says so in its report. The coordinator then sets the ticket to `high-risk`, so review always goes to `high-risk-reviewer`. Whether to redo the implementation with `implementer-advanced` is the owner's call. `/plan` marks a ticket `high-risk` when unsure, with a one-line reason.

## Agents

- **implementer** - one ticket, one worktree, one commit; reports verified vs. not-run vs. not-possible.
- **implementer-advanced** - same rules, for `high-risk` tickets; Opus, with extra care for guards, credentials, git hooks and release.
- **reviewer** - read-only; does not trust the implementer's report; judges only against the ticket's criteria.
- **high-risk-reviewer** - read-only senior review for identity, payments, deletion, security, shared contracts, release.
- **silent-failure-hunter** - read-only second lens for swallowed errors, catch-all fallbacks, and "unknown" collapsed into a definite answer.

## Hooks (`hooks/scripts/`)

| Hook | Behavior |
|---|---|
| Bash: credential reads | Blocks `cat`/`cp`/`grep`/... on keys, keystores, `.p8`, service-account JSON, `.env` (plus `protectedPaths`). |
| Bash: publishing | `git push`, `gh pr create/merge`, `gh repo create/delete`, `gh release create`, `gh gist create` (and gh's `new` aliases): asks the owner, blocks, or leaves it to your permission rules, per [`pushPolicy`](#push-policy-pushpolicy) (default: ask). |
| Bash: deploys and destructive cloud commands | Always asks before `firebase deploy` and other destructive Firebase/gcloud/terraform/kubectl commands (plus `askCommands`), whatever `pushPolicy` says. |
| Bash: writes to protected configs | Asks when a command looks like it writes to, moves, copies over, deletes or changes the mode of a protected config (`.claude/harness.json`, `.claude/settings*.json`, lint/test configs, `protectedConfigs`): redirections (`>`, `>>`, `>|`, `&>`), `tee`, `sed -i`/`perl -i`/`ruby -i`/`gawk -i inplace`, `cp`/`mv`/`install`/`ln`/`rsync`/`dd of=`/`truncate`/`rm`/`chmod`/`chown`/`touch`/`unzip`, editors (`vim`, `ed`, ...), `git checkout/restore` of the working tree, `sh -c`/`bash -c`/`eval` bodies and shell heredocs, and interpreter code (`python -c`, `node -e`, `ruby -e`, `perl -e`, `python3 - <<EOF`) that names the path. It also counts the config's folder (`rm -rf .claude`, `cp x .claude/`), globs (`.claude/*.json`) a recursive copy into a folder that holds one (`cp -R src/. .`), `git update-index --assume-unchanged`/`--skip-worktree`, and a `cd`/`git -C` earlier in the same command; on macOS, any letter case. Heredoc bodies are checked as commands, except plain data: the body of `git commit -F -`, `git tag -F -`, `cat > file`, `cat >> file` or `tee file`, with a quoted delimiter (or no `$`/backtick in the body), found closed, on a line that pipes nothing onward, and (for `cat`/`tee`) writing a file the rest of the command does not name again, so a script written and then run is checked. Before the word checks, `#` comments are removed on lines without `$(`, backticks or `((`; the scanner can misread quotes nested inside `${...}` or a multi-line `$(...)` and remove too much. Publishing is still caught, because the push regexes read the full text; a protected-config write hidden that way (`echo "${x:-"a # b"}"; rm .claude/settings.json`) is a known gap. Reads pass: `cat`, `grep`, `jq`, `git diff`, `git restore --staged`, `python3 -m json.tool`, test runners given `--config`. Interpreter code that names a config asks even when it only reads it. This catches common shell writes, not all of them: paths built from variables, scripts, `tar`, `patch`, `git apply`, `git stash pop`, formatters such as `prettier --write`, and other indirect paths get through. |
| Bash: machine-wide device resets | Blocks `simctl shutdown all`, `simctl delete/erase all`, `adb emu kill`, killing all simulators, while another `xcodebuild`/`gradlew` run is active, so parallel agents do not kill each other's devices. |
| Read: credentials | Blocks reading protected credential files. |
| Edit/Write: protected configs | Asks before editing lint/format configs, test plans/runner configs, `.claude` settings, and `protectedConfigs` (e.g. validator thresholds), so agents fix code instead of weakening checks. |
| Edit/Write: weakened checks | Asks when an edit *adds* a test skip, accessibility-audit filter, or lint/type suppression (`XCTSkip`, `@skip`, `it.skip`, `@Ignore`, `@Suppress`, `# noqa`, `@ts-ignore`, `eslint-disable`, `--no-verify`, ...; extend with `weakeningMarkers`, turn off with `"checkWeakening": false`). Moving or removing an existing one is allowed. |
| Notification | macOS desktop notification when Claude needs you (`"notify": false` to turn off). |

Hooks are best-effort guardrails, not a sandbox. Claude Code permission deny rules are the hard boundary; keep them for anything that must never happen. A Bash command too large to check (over the check's work budget, a single word over 32 KB, or over 256 KB in all) asks: "command too large to check; approve manually". A check that fails with an error asks too.

Tests: `python3 -m unittest discover -s tests`.

## Configuration (`.claude/harness.json`)

Every key is optional; a missing file means all defaults. Config files are merged, never overridden. The sources are `$CLAUDE_PROJECT_DIR/.claude/harness.json` (when present) and the nearest `.claude/harness.json` found walking up from the working directory, stopping at the repository root (first folder containing `.git`, a folder or a worktree file; without one, at `$CLAUDE_PROJECT_DIR` if inside it, else only the working directory). Without `CLAUDE_PROJECT_DIR`, every file up to the repository root counts. A file above the repository root is never loaded; the hook only warns that it exists. Each file loads on its own, so a broken one is reported (an unreadable file counts as present, not missing) while the others still apply. The result is never weaker than any single file: lists (`protectedPaths`, `protectedConfigs`, `askCommands`, `weakeningMarkers`, `presets`) are unioned, `pushPolicy` takes the strictest (`deny` > `ask` > `allow`), `checkWeakening` is on if any file says so, and the remaining keys take the root file's value, falling back to a nested file. So a stricter worktree config applies immediately, and a weaker one changes nothing. Identical copies produce no notice; differing ones produce one notice naming both files. Project globs match relative to each file's project folder as well as to the working directory.

A malformed file, an unknown key, a bad regex or a value of the wrong type is never ignored silently. The hook still enforces the built-in rules, falls back to the default for the bad part (for the lists `protectedPaths`, `protectedConfigs`, `askCommands` and `weakeningMarkers` it keeps the valid entries and a bare string counts as a one-item list), and reports the problem three ways: on stderr, as `systemMessage` for the user, and as `additionalContext` for Claude (on a permission prompt it is also appended to the reason; on a block it is appended to the message). The warning says which project protections are not active. Combining `systemMessage` with `hookSpecificOutput` in one object is not shown in the Claude Code hooks docs; verify with `claude --debug`. Example with every key: [`templates/harness.json`](templates/harness.json). The shared loader is `hooks/scripts/harness_config.py`.

| Key | Default | Meaning |
|---|---|---|
| `version` | `1` | Schema version; only `1` exists. |
| `protectedPaths` | `[]` | Extra globs the guard treats as credentials. |
| `protectedConfigs` | `[]` | Extra globs whose edits need the owner's OK. |
| `askCommands` | `[]` | Extra command substrings that need the owner's OK. |
| `checkWeakening` | `true` | Ask when an edit adds a test skip or lint suppression. |
| `weakeningMarkers` | `[]` | Extra regexes counted as weakening. |
| `notify` | `true` | macOS notification when Claude needs you. |
| `pushPolicy` | `"ask"` | `"ask"`, `"deny"` or `"allow"` for git push and GitHub publishing; see [Push policy](#push-policy-pushpolicy). |
| `presets` | `[]` | Opt-in rule sets (filled in from CH-06). |
| `highRiskReviewerModel` | `"opus"` | `"opus"` or `"fable"`; `/review` passes it as the model of `high-risk-reviewer`. |
| `tickets.dirs` | `["docs/tickets"]` | Folders holding ticket files. |
| `tickets.featureGlobs` | `[]` | Globs mapping files to features. |
| `tickets.idPrefixes` | `{}` | Ticket ID prefix -> `{"platform": ..., "kind": ...}`. |
| `tickets.twinPlatforms` | `null` | List of platforms that mirror each other, or `null`. |
| `tickets.archive` | `false` | Move closed tickets to an archive. |
| `tickets.trackerPath` | `"docs/tickets/tracker.md"` | Tracker file. |
| `tickets.integrationBranch` | `"main"` | Branch tickets are integrated onto. |
| `tickets.boardPath` | `"docs/tickets/board.html"` | Generated board file. |

## Push policy (`pushPolicy`)

Nothing leaves your machine without your approval. `pushPolicy` decides how the guard treats git push and GitHub publishing: `git push` (in any form: `git -C dir push`, `git -c k=v push`, after `env`/`command`/`sudo`, chained with `&&`, `;`, `|` or in a subshell, split with `\`-newline), `gh pr create`, `gh pr merge`, `gh repo create`, `gh repo delete`, `gh release create` and `gh gist create`, including gh's `new` aliases and `-R`/`--repo` before the subcommand.

| Value | Behavior |
|---|---|
| `"ask"` (default) | You approve each one at a permission prompt. Also used when no config exists or the value is invalid. |
| `"deny"` | Blocked outright (exit 2). The message tells the agent the owner pushes by hand. |
| `"allow"` | No prompt from this hook, as long as git confirms that no `harness.json` in the repository (and none of the loaded ones) differs from the last commit. A modified, staged, deleted, untracked or ignored `harness.json`, one marked assume-unchanged or skip-worktree in the index, or a git error (for example outside a git repository), still asks: "push policy file changed in the working tree". Your own Claude Code permission rules still apply. Meant for your own interactive sessions. The hook cannot tell a subagent from the main session, so `"allow"` applies to subagents too; agents are kept from pushing by their instructions and your permission rules, not by this hook. |

`pushPolicy` covers only git/GitHub publishing. Deploys and destructive cloud commands (`firebase deploy`, `gcloud ... deploy/delete`, `terraform apply/destroy`, `kubectl apply/delete`) and every `askCommands` entry always ask, whatever the policy, including when chained onto an allowed push. When config files merge, the strictest value wins (`deny` > `ask` > `allow`).

Turning on `"allow"` takes a change to `harness.json`. An Edit/Write of it asks, and so do the common shell writes to it (see the hooks table). Whatever shell form was used, a push under `"allow"` still asks until the change is committed, and a commit is something you see. Permission deny rules (for example on `Bash(git push:*)`) are the hard boundary.

Detection matches text, so it errs towards asking: under `"deny"` a command that merely mentions `git push` in quotes (a commit message, a `grep`) is blocked too; reword it or run it yourself. Plain data heredocs (`git commit -F - <<'EOF'`, `cat > notes.md <<'EOF'`, as described in the hooks table) are not read as commands; every other heredoc body is, including one piped to a shell or interpreter. Not detected: shell variables, git aliases for push, scripts (`$G push`, `./release.sh`), `gh api` calls, `git lfs push`, `git subtree push` and `git send-pack`; add such scripts to `askCommands` and keep a permission deny rule.

## Layout

```text
.claude-plugin/   plugin.json, marketplace.json
bin/              dev-harness-config (on the Bash PATH while the plugin is enabled)
agents/           implementer, implementer-advanced, reviewer, high-risk-reviewer, silent-failure-hunter
commands/         plan, start-ticket, review, close-ticket, sync
hooks/            hooks.json and scripts/:
                    guard.py           hook entry: input, ask/block, crash handler, Read/Edit/notify guards
                    bash_rules.py      Bash rules: credential reads, push policy, always-ask
                                       commands, device resets; runs shell_check
                    shell_check.py     one pass over a command: push detection, config writes
                    shell_scan.py      shell scanner (words, operators, heredocs, quoting) and work budget
                    harness_config.py  harness.json schema, loading and merging
templates/        CLAUDE.md Harness section, harness.json
tests/            guard (end to end), push policy, config writes, shell scanner, config loader,
                  agent tier and dev-harness-config tests;
                  tests/data/guard_corpus.txt: commands with the expected decision per policy
```

`bin/dev-harness-config get <key>` prints one value of the merged config for the current directory (scalars plainly, lists and objects as JSON). Config warnings go to stderr with exit 1 (the value is still printed); an unknown key or bad usage exits 2. Commands use it because `CLAUDE_PLUGIN_ROOT` is not set in the Bash tool's environment.

## Credits

Ideas borrowed from [Chachamaru127/claude-code-harness](https://github.com/Chachamaru127/claude-code-harness) (role-based model routing, plan contracts, evidence-only review, sync) and [affaan-m/ECC](https://github.com/affaan-m/ECC) (config protection, silent-failure review, notifications).

## License

MIT
