# Ticket tracker

This table is the source of truth for ticket state. Each ticket has a file made from [`TEMPLATE.md`](TEMPLATE.md); keep its Status line in step with its row. Update a row in the same commit as the ticket, or in a docs-only commit.

States: `Planned`, `In progress`, `In review`, `Fixing`, `Owner check`, `Done`, `Blocked`, `Deferred`.

| ID | Title | Review | State | Commit | Note |
|---|---|---|---|---|---|
| [CH-01](CH-01.md) | One `harness.json` schema and shared config loader | standard + SF | Done | c2efe7d | 3 review rounds; configs merge (owner) |
| [CH-02](CH-02.md) | Model tiers and implementer routing | standard | Done | ef867a1 | 2 review rounds; adds `bin/dev-harness-config` |
| [CH-03](CH-03.md) | Push policy | high-risk | Done | b999c7d | 3 high-risk rounds + coordinator-verified fix (owner); 224-row guard corpus |
| [CH-04](CH-04.md) | Stash and `core.hooksPath` guards | standard | Done | 19319cb | 1 review round |
| [CH-05](CH-05.md) | Warn on generated / ignored files in commits | standard + SF | Planned | | |
| [CH-06](CH-06.md) | Device rules as presets, auto-on for mobile projects | standard | Blocked | | Paused: guard false positive on test text; owner decides how to continue |
| [CH-07](CH-07.md) | Close remaining fail-open paths in the guard | standard + SF | Planned | | From CH-01 review |
| [CH-08](CH-08.md) | Split `guard.py` into scanner, Bash rules and hook entry | high-risk | Done | 99aedc3 | 1 high-risk round; output identical to main |
