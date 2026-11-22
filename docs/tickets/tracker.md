# Ticket tracker

This table is the source of truth for ticket state. Each ticket has a file made from [`TEMPLATE.md`](TEMPLATE.md); keep its Status line in step with its row. Update a row in the same commit as the ticket, or in a docs-only commit.

States: `Planned`, `In progress`, `In review`, `Fixing`, `Owner check`, `Done`, `Blocked`, `Deferred`.

| ID | Title | Review | State | Commit | Note |
|---|---|---|---|---|---|
| [CH-01](CH-01.md) | One `harness.json` schema and shared config loader | standard + SF | Planned | | Do first; others depend on it |
| [CH-02](CH-02.md) | Model tiers and implementer routing | standard | Planned | | |
| [CH-03](CH-03.md) | Push policy | high-risk | Planned | | Deploys always ask (owner, 2026-11-22) |
| [CH-04](CH-04.md) | Stash and `core.hooksPath` guards | standard | Planned | | |
| [CH-05](CH-05.md) | Warn on generated / ignored files in commits | standard + SF | Planned | | |
| [CH-06](CH-06.md) | Device rules as opt-in presets | standard | Planned | | Breaking change; CHANGELOG |
