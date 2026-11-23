## Harness

<!-- claude-harness reads this section. Keep it short and specific to this project. -->

- **Plans / spec**: `<plan or spec path>`. Ticket IDs look like `{{example-id}}`.
- **Handoff / progress doc**: `<handoff doc path>` - current state, active ticket, next step (`docs/STATUS.md` if you use it that way).
- **Evidence**: goes in the ticket file ("Evidence at close") with real commands and results.
- **Worktrees**: `.worktrees/<id-lowercase>` on `feature/<id-lowercase>`, from `{{integration-branch}}`. One commit per ticket, cherry-picked onto `{{integration-branch}}`.
- **Commit format**: `<type>(<area>): <id-lowercase> <summary>`.
- **Verify**:
  - focused: `<command for one test class/file>`
  - full (at gates or end of ticket): `<command>`
  - static: `<lint / format / validators>`
- **Build caches and devices**: shared cache `<path>`; parallel tickets use `<path>-<id>`; cleanup at close: `<commands>`.
- **High-risk categories** (use `high-risk-reviewer`): authentication/accounts, payments/entitlements, deletion/migration, security rules/secrets, shared backend contracts, release/signing.
- **Review policy**: high-risk -> `implementer-advanced` and `high-risk-reviewer`; everything else -> `implementer` and `reviewer` (or coordinator self-review for docs/test-only changes). Tiers: `implementer` Sonnet, `implementer-advanced` Opus, `high-risk-reviewer` Opus (Fable via `highRiskReviewerModel`). Upgrade rule: an implementer that finds its diff riskier than planned says so in its report; the coordinator sets the ticket to `high-risk` so review goes to `high-risk-reviewer`; redoing it with `implementer-advanced` is the owner's call.
- **Coordinator (main session)**: run it on Opus (`/model opus`). It plans, routes tickets, judges review reports and keeps the docs; the plugin's agents pick their own models.
- **Boundaries - stop and ask the owner**: production deploys, shared databases, store consoles, signing keys, anything outward-facing.
- **How tickets work**:
  - Each ticket is a file in `{{tickets-dir}}/` made from `TEMPLATE.md`. One row per ticket in `{{tracker-path}}` is the source of truth for its state; the ticket file's Status line must agree.
  - States: Planned, In progress, In review, Fixing, Owner check, Done, Blocked, Deferred.
  - The board (`dev-harness-board`) shows the tracker as columns of cards; `dev-harness-board --check` lists disagreements between rows and files.
  - `/start-ticket <id>` launches an implementer in its own worktree; `/review <id>` sends the commit to the reviewer; `/close-ticket <id>` integrates the accepted commit and cleans up.
- **Changing the ticket flow**: changes to the flow itself (states, the start/review/close steps, the board, git hooks, guards) go to the dev-harness plugin as a request to its maintainer, not into this project's own scripts. Do not copy or patch the board or the hooks here.
