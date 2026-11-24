# Changelog

All notable changes to the `dev-harness` plugin. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

A version's date is set when the owner tags the release; until then it reads `Unreleased`. Earlier versions were not tagged; their dates are commit dates. Ticket IDs in parentheses point at [`docs/tickets/`](docs/tickets/).

## [1.0.0] - 2026-11-24

The first release with a ticket system and board. It also makes the guards stricter and the settings file one documented schema. Everything below is new or changed since 0.2.0.

### Added

- **Ticket features are opt-in.** `set-state`, `archive`, `install-hooks`, the git hook mode and the board render act only in a project whose `harness.json` has a `tickets` section; elsewhere they refuse or do nothing, and `/start-ticket`, `/review`, `/close-ticket` and the implementers skip their ticket steps. New `dev-harness-board status` says which case applies. `/plan` writes ticket files and tracker rows only in an opted-in project, and otherwise writes the plan to the project's plan location. (CH-29)

**Guards**

- **Push policy.** A new `pushPolicy` setting decides what happens to `git push` and GitHub publishing (`gh pr create` and `merge`, `gh repo create` and `delete`, `gh release create`, `gh gist create`): `"ask"` (the default) asks you each time, `"deny"` blocks them, `"allow"` lets them through, subagents included, but only while no `harness.json` has uncommitted changes. (CH-03)
- **Deploys always ask.** `firebase deploy`, destructive `gcloud`, `terraform` and `kubectl` commands, and your `askCommands` entries ask whatever `pushPolicy` says. (CH-03)
- **Shell writes to protected configs ask.** Redirects and common shell writes into lint, test and `.claude` settings, or files you list in `protectedConfigs`, now ask, as Edit/Write already did. (CH-03)
- **SSH private keys are protected.** Reading `id_rsa`, `id_ecdsa`, `id_ed25519`, `id_dsa` and their `-sk` variants is blocked in any folder; public keys (`.pub`) still pass. Credential paths inside unquoted `$(...)`, backticks, `( )` and `{ }` are caught too (not inside double quotes; see [docs/guards.md](docs/guards.md)). (CH-11)
- **`git stash` asks when several worktrees exist**, because every worktree shares one stash. (CH-04)
- **Changing `core.hooksPath` asks**, because it silently switches off hooks such as Git LFS's. (CH-04)
- **Commits of generated files ask.** A `git commit` that adds files under `node_modules/`, `build/`, `dist/` and similar folders, compiled files, the board output, or files your `.gitignore` ignores asks first and lists them. (CH-05)
- **Installing or removing the board's git hooks asks**, and so do edits to their folder. (CH-22)
- **Data heredocs are text, not commands.** Text written into a file with `cat > file <<'EOF'`, `tee file` or `git commit -F -` no longer trips the credential, device-reset, push or config rules. (CH-03, CH-10)
- **Fail-closed checks for heredoc tricks.** When CRLF line endings, a `#` inside nested quotes, or a shadowed `cat`, `tee` or `git` (a function, alias, `PATH=` and similar) could make a "data" heredoc run, the exemption above is dropped and every rule counts the text, for credentials, device resets, pushes, protected configs, stash and `core.hooksPath`. (CH-10, CH-12, CH-13)
- **Device presets.** The machine-wide simulator and emulator reset blocks are now two presets, `apple-simulators` and `android-emulators`. Each switches on by itself in a project with an Xcode project or a `gradlew`, and can be forced on or off with `presets` and `disabledPresets`. (CH-06)

**Configuration**

- **One `harness.json` schema** with documented defaults for every key: the existing guard keys plus `pushPolicy`, `presets`, `disabledPresets`, `highRiskReviewerModel` and a `tickets` section. A malformed file, an unknown or repeated key, or a value of the wrong type is reported instead of silently ignored. (CH-01, CH-07)
- **Config files merge and are never weaker.** When a project root and a nested folder both have a `harness.json`, lists are combined, `pushPolicy` takes the strictest value, and a nested file can switch protections on but never off. (CH-01)
- **`dev-harness-config get <key>`** prints one value of the merged config, for commands and scripts. (CH-02)

**Agents and model tiers**

- **`implementer-advanced`** (Opus) builds tickets marked high-risk; `/start-ticket` picks the implementer from the ticket's review level. (CH-02)
- **`highRiskReviewerModel`** lets the high-risk reviewer run on Fable instead of Opus. (CH-02)
- **Upgrade rule.** An implementer that finds its change riskier than planned says so, and the ticket moves to high-risk review. `/plan` marks a ticket high-risk when unsure. (CH-02)

**Ticket system and board**

- **`dev-harness-board`**, a standard-library command that reads your tracker and ticket files (CH-14, CH-15):
  - `--check` lists every place a tracker row and its ticket file disagree (state, missing row or file, unknown state, duplicate ID, broken link, missing twin);
  - `--json` prints the parsed tickets;
  - with no flag, it writes the board: one self-contained HTML page with six columns, search and filters, an issue view per ticket, light and dark themes and a phone layout. It needs no network.
- **Live worktrees on the board.** A ticket being built in a `feature/<id>` worktree shows as In progress or In review before it is merged, with the branch the state came from. (CH-16)
- **Board git hooks.** `dev-harness-board install-hooks` makes git rebuild the board after commits, merges, rewrites and checkouts, in your clone only. The hooks chain to existing ones such as Git LFS's, never fail the git command, and are switched off with `uninstall-hooks` (the scripts stay, unused). (CH-17)
- **`dev-harness-board init`** sets up tickets in a new project: template, tracker, `docs/STATUS.md`, the `tickets` settings and a Harness section in `CLAUDE.md`. It never overwrites: it creates missing files and only adds missing settings. (CH-18)
- **`set-state`, `archive` and `twin`** subcommands move a ticket's state in its tracker row and file together, archive Done tickets, and report a twin ticket's state. (CH-20)
- **Ticket settings:** `stateAliases`, `archiveTracker`, `archiveDirs`, `extraColumns` (CH-14), and `titleColumn` for trackers whose title column has another name (CH-23).

**Commands**

- **`/ticket-init`** asks a few questions and runs `dev-harness-board init`. (CH-18)
- **`/plan` writes tickets.** In a project with a tracker, it proposes ticket IDs and, once you approve, writes the ticket files and Planned rows. Without a tracker it writes the plan file, also only after you approve. (CH-25)
- **`/start-ticket`, `/review` and `/close-ticket` keep ticket state**: In progress, In review, Fixing and Done with the commit ID. `/close-ticket` also rebuilds and checks the board, reports the twin, and archives when that is on. (CH-20)
- **`/sync`** runs the board check and lists open Planned and Owner check tickets before a release. (CH-20)

**Docs and CI**

- A rewritten README with the loop, a worked example and a five-minute quickstart, plus reference pages: [docs/guards.md](docs/guards.md), [docs/configuration.md](docs/configuration.md) and [docs/agents.md](docs/agents.md). (CH-25)
- A made-up sample project, its generated board ([docs/examples/board.html](docs/examples/board.html)) and screenshots, rebuilt with `scripts/make-examples.sh`. (CH-24)
- A GitHub Actions workflow runs the tests on Ubuntu and macOS with Python 3.9 and 3.13. (CH-26)
- This changelog and [CONTRIBUTING.md](CONTRIBUTING.md). (CH-27)

### Changed

- **The guard reports what it could not check** instead of allowing silently: unreadable hook input, a guard module that fails to load (asks), `pgrep` failing before a device reset (asks), a failing desktop notification, and a crash on a call that looks like a credential read (asks). A command too large to check in time asks. Config warnings are shown once per session. (CH-07)
- **The guard is split into modules**, a shell scanner, the Bash rules and the hook entry, with the same decisions as before. (CH-08)
- **The device-reset blocks are off in projects without a mobile project** unless a preset is switched on. (CH-06)

### Known limits

- **Every push asks (or is blocked under `"deny"`) unless you set `pushPolicy: "allow"`.** No setting yet lets agents push feature branches without a prompt.
- **`/ticket-init` sets up new projects only.** It stops and writes nothing in a project that already has a ticket tracker or a ticket folder.
- **The guards match command text.** Scripts, aliases and commands built from variables can get through; the gaps of each rule are in [docs/guards.md](docs/guards.md). Claude Code permission deny rules are the hard boundary.
- **Desktop notifications are macOS only.** Everything else is tested on macOS and Linux (Ubuntu) in CI.

### Planned after 1.0

- Letting agents push feature branches, never `main`, without a prompt (CH-09, parked).
- Adopting an existing ticket tracker in `/ticket-init`, with a dry-run report first (CH-19, parked).

## [0.2.0] - 2026-10-15

### Added

- The guard asks when an edit adds a test skip, an audit filter or a lint or type suppression (`weakeningMarkers`, `checkWeakening`), and protects test plans and runner configs.
- Tests for the guard.

## [0.1.0] - 2026-10-12

### Added

- First version: the `implementer`, `reviewer`, `high-risk-reviewer` and `silent-failure-hunter` agents; the `/plan`, `/start-ticket`, `/review`, `/close-ticket` and `/sync` commands; guard hooks for credential reads, outward-facing commands, machine-wide device resets and protected configs; and project templates.
