# Status

Goal: release **1.0** of the `dev-harness` plugin with a ticket system and a Jira-like board, then make the repository public. Agents never push; the owner pushes and flips the repo to public.

## Milestones

- **A. Foundations** (tickets CH-01 to CH-13, [tracker](tickets/tracker.md)): one `harness.json` schema, model tiers, push policy and other guard additions.
- **B. Ticket system and board** (tickets CH-14 to CH-22, approved 2026-11-23):
  - `/ticket-init`
  - board script: self-contained HTML, `--check`, standard library only
    - **reads live worktrees too** (owner, 2026-11-23). Without this, a ticket being built shows as Planned until it is merged.
      - **Matching:** a worktree on `feature/<id>` (the Harness section's branch pattern) maps to a known ticket ID; worktrees matching no ticket are ignored.
      - **Shown state:** the more advanced of main's state, In progress (a live worktree exists), and the state in that worktree's own tracker row or ticket file (committed or not).
      - **Caps:** unmerged work shows at most In review. Owner check, Done and Blocked on main are never moved.
      - **Card:** shows the branch the state came from.
      - **`--check`:** compares only main's row with main's file, so a worktree being ahead is not a mismatch.
      - **When git fails:** the board still builds when git is missing or there are no worktrees; prunable and missing worktrees are skipped.
      - **Rebuilds:** post-checkout (runs on `git worktree add`) and post-commit (runs in worktrees), always written into the main checkout's board; a removed worktree drops off at the next rebuild.
      - **Reference:** a private project already built this; borrow the design, copy no code or names.
  - git hooks that chain to existing hooks (Git LFS) and work from worktrees
  - `close-ticket` additions
  - release check in `/sync`
  - read-only parser check against real projects to a temp folder (nothing copied into this repo)
  - acceptance: re-run `/ticket-init` on this repo
  - `/ticket-init` adopts an existing tracker and ticket files without rewriting them (both private projects already have real tickets)
  - migration of existing tickets (owner, 2026-11-23): dry run first (`/ticket-init --check` writes nothing and reports ticket counts against the old board, unknown states, files without rows, rows without files, broken links); differences are mapped through settings (state names, archive and feature folders, ID prefixes, extra columns, old evidence locations), never by rewriting old tickets; the plugin's board must match the old board before switching; the switch is one revertible commit
  - the Harness section template that `/ticket-init` writes says ticket-flow changes go to the dev-harness plugin as a request, not into the project's own scripts
  - after the 1.0 plugin update, each project switches over in a ticket run by its own coordinator: run `/ticket-init`; a project with its own board script and board hooks removes them and keeps its extra columns through settings (owner, 2026-11-23)
- **C. Ready to publish**:
  - **README rewrite** (owner request 2026-11-22; working draft in [`README-draft.md`](README-draft.md)), so a newcomer understands what the plugin does:
    - an opening paragraph on the problem it solves and who it is for
    - a picture of the plan -> implement -> review -> integrate loop
    - a short worked example of one ticket going through it
    - what the guards stop
    - install and a 5-minute quickstart
    - rule-by-rule guard detail and the full `harness.json` reference move out to `docs/guards.md` and `docs/configuration.md`, linked from the README (owner, 2026-11-23: the current README is hard to read)
    - review includes a first-time-reader pass, not only accuracy
  - **Board example** in the README, built from the sample project:
    - light and dark screenshots plus one issue-view screenshot
    - the generated sample board committed as `docs/examples/board.html`, so people can open a working board (and later via GitHub Pages)
    - the command to regenerate them
  - sample project of made-up tickets (about 15, covering every column, a twin pair, a high-risk ticket, an owner check, a blocked one and one deliberate mismatch)
  - GitHub Actions running the tests
  - CHANGELOG, CONTRIBUTING, 5-minute quickstart, limits
  - pre-publish privacy sweep of the whole history
  - tag `v1.0.0`

## Next

CH-01 is done (c2efe7d). CH-02 is done (ef867a1). CH-03 is done (b999c7d). CH-08 is done (99aedc3). Tickets run one at a time (owner, 2026-11-22), except that CH-06 may run alongside one other ticket (owner, 2026-11-22). CH-04 is done (19319cb). CH-06 is done (2b18eb4). CH-05 is done (a7497ff). CH-07 is done (3a6ca12). CH-10 is done (7a42cf4). CH-11 is done (000f1ee). CH-12 is done (a2f0c10). CH-13 is done (9283d96). CH-09 is parked (Deferred) after three high-risk rounds; its work stays on `feature/ch-09` for after 1.0. CH-14 is done (c81dfa2). CH-15 is done (ff13924). CH-16 is done (62712f1). CH-17 is done (70fd1f6). CH-22 is done (5d4211b). CH-18 is done (613f82e). CH-19 is parked (Deferred) after two rounds and a targeted recheck; its work stays on `feature/ch-19`, so existing projects cannot switch over in 1.0 unless it is resumed. Running: CH-20, then CH-21 (its adopt part waits on CH-19) in order (owner, 2026-11-23). Every remaining ticket has an explicit catch list and at most 2 review rounds (owner, 2026-11-22).

## Owner decisions

- 2026-11-22: commit history keeps the owner's current email; no rewrite before publishing.
- 2026-11-22: when several `harness.json` files apply, they merge and are never weaker (CH-01).
- 2026-11-22: `pushPolicy: "allow"` stays; the guard also asks before shell commands that write protected configs (CH-03). Stricter push detection kept.
- 2026-11-22: device presets switch on automatically in repos with an Xcode project or `gradlew` (CH-06).
- 2026-11-22: deploy commands always ask, whatever `pushPolicy` says (CH-03).
- 2026-11-23: once a project has switched to the plugin's ticket system, ticket-flow changes (states, start/review/close steps, board, git hooks, guards) go through this plugin; projects keep only their settings.
- 2026-11-23: CH-10 (heredoc text is data) runs before CH-09.
- 2026-11-23: guard tickets (CH-12, CH-09) may write any file in their own worktree with the file tools, because their code and tests contain push commands and credential paths as text; a blocked or denied shell command still stops the ticket. CH-11's implementer redid two blocked edits with the file tools before this was allowed; accepted once the review confirmed the diff.
- 2026-11-23: a guard crash on a credential-like call asks instead of allowing (CH-07).
- 2026-11-23: the board's git hooks live in an untracked folder inside the git common dir (`<common-dir>/dev-harness/hooks`), not a tracked `.githooks/`; each clone opts in with `install-hooks` (CH-17 review: a tracked folder vanished on older branches, skipping Git LFS's pre-push, and let any checked-out branch change hook code).
- 2026-11-23: the guard asks before `install-hooks`/`uninstall-hooks` and edits to the hooks folder (CH-22, before 1.0).
- 2026-11-23: new `pushPolicy: "feature-branches"`: agents (main session and subagents) may push feature branches without a prompt, never `main`/`master`; anything the guard can't prove asks (CH-09). Replaces the CH-07 subagent-push item.

## Updating the installed plugin (later, after Milestone A)

Repos that use the plugin run the installed copy (0.2.0 from GitHub) until the owner updates it, so work here does not affect them before then.

1. Let each repo's coordinator finish and close its current ticket; start no new one.
2. With no implementer or reviewer running anywhere, the owner pushes `main` and runs `/plugin update dev-harness`.
3. Restart every Claude Code session in those repos (and this one).
4. Check in one session: `dev-harness-config get pushPolicy` prints `ask`; asking Claude to run `git push` brings a permission prompt.
5. Resume tickets.
