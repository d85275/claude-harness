# Status

Goal: release **1.0** of the `dev-harness` plugin with a ticket system and a Jira-like board, then make the repository public. Agents never push; the owner pushes and flips the repo to public.

## Milestones

- **A. Foundations** (tickets CH-01 to CH-06, [tracker](tickets/tracker.md)): one `harness.json` schema, model tiers, push policy and other guard additions.
- **B. Ticket system and board** (tickets to be written when A is integrated):
  - `/ticket-init`
  - board script: self-contained HTML, `--check`, standard library only
  - git hooks that chain to existing hooks (Git LFS) and work from worktrees
  - `close-ticket` additions
  - release check in `/sync`
  - read-only parser check against real projects to a temp folder (nothing copied into this repo)
  - acceptance: re-run `/ticket-init` on this repo
- **C. Ready to publish**:
  - **README rewrite** (owner request 2026-11-22), so a newcomer understands what the plugin does:
    - an opening paragraph on the problem it solves and who it is for
    - a picture of the plan -> implement -> review -> integrate loop
    - a short worked example of one ticket going through it
    - what the guards stop
    - install and a 5-minute quickstart
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

CH-01 is done (c2efe7d). CH-02 is done (ef867a1). Tickets run one at a time (owner, 2026-11-22). Next: CH-03 (high-risk), then CH-04 to CH-07.

## Owner decisions

- 2026-11-22: commit history keeps the owner's current email; no rewrite before publishing.
- 2026-11-22: when several `harness.json` files apply, they merge and are never weaker (CH-01).
- 2026-11-22: `pushPolicy: "allow"` stays; the guard also asks before shell commands that write protected configs (CH-03). Stricter push detection kept.
- 2026-11-22: device presets switch on automatically in repos with an Xcode project or `gradlew` (CH-06).
- 2026-11-22: deploy commands always ask, whatever `pushPolicy` says (CH-03).

## Updating the installed plugin (later, after Milestone A)

Repos that use the plugin run the installed copy (0.2.0 from GitHub) until the owner updates it, so work here does not affect them before then.

1. Let each repo's coordinator finish and close its current ticket; start no new one.
2. With no implementer or reviewer running anywhere, the owner pushes `main` and runs `/plugin update dev-harness`.
3. Restart every Claude Code session in those repos (and this one).
4. Check in one session: `dev-harness-config get pushPolicy` prints `ask`; asking Claude to run `git push` brings a permission prompt.
5. Resume tickets.
