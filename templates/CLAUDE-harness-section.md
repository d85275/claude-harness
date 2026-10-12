## Harness

<!-- claude-harness reads this section. Keep it short and specific to this project. -->

- **Plans / spec**: `docs/plans/` (or the spec path, e.g. `docs/specs/main-spec.md`). Ticket IDs look like `PROJ-12`.
- **Handoff / progress doc**: `docs/handoff.md` - current state, active ticket, next step.
- **Evidence log**: `docs/ticket-evidence.md` - append one entry per ticket with real commands and results.
- **Worktrees**: `.worktrees/<id-lowercase>` on `feature/<id-lowercase>`, from `main`. One commit per ticket, cherry-picked onto `main`.
- **Commit format**: `<type>(<area>): <id-lowercase> <summary>`.
- **Verify**:
  - focused: `<command for one test class/file>`
  - full (at gates or end of ticket): `<command>`
  - static: `<lint / format / validators>`
- **Build caches and devices**: shared cache `<path>`; parallel tickets use `<path>-<id>`; cleanup at close: `<commands>`.
- **High-risk categories** (use `high-risk-reviewer`): authentication/accounts, payments/entitlements, deletion/migration, security rules/secrets, shared backend contracts, release/signing.
- **Review policy**: high-risk -> `high-risk-reviewer`; everything else -> `reviewer` (or coordinator self-review for docs/test-only changes).
- **Boundaries - stop and ask the owner**: production deploys, shared databases, store consoles, signing keys, anything outward-facing.
