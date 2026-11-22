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

CH-01 is done (c2efe7d). Next: CH-02 to CH-07; all depend only on CH-01 and may run in parallel (CH-03 is high-risk).

## Owner decisions

- 2026-11-22: commit history keeps the owner's current email; no rewrite before publishing.
- 2026-11-22: when several `harness.json` files apply, they merge and are never weaker (CH-01).
- 2026-11-22: deploy commands always ask, whatever `pushPolicy` says (CH-03).
