# Ticket tracker

This table is the source of truth for ticket state. Each ticket has a file made from [`TEMPLATE.md`](TEMPLATE.md); keep its Status line in step with its row. Update a row in the same commit as the ticket, or in a docs-only commit.

States: `Planned`, `In progress`, `In review`, `Fixing`, `Owner check`, `Done`, `Blocked`, `Deferred`.

| ID | Title | Review | State | Commit | Note |
|---|---|---|---|---|---|
| [CH-01](CH-01.md) | One `harness.json` schema and shared config loader | standard + SF | Done | c2efe7d | 3 review rounds; configs merge (owner) |
| [CH-02](CH-02.md) | Model tiers and implementer routing | standard | Done | ef867a1 | 2 review rounds; adds `bin/dev-harness-config` |
| [CH-03](CH-03.md) | Push policy | high-risk | Done | b999c7d | 3 high-risk rounds + coordinator-verified fix (owner); 224-row guard corpus |
| [CH-04](CH-04.md) | Stash and `core.hooksPath` guards | standard | Done | 19319cb | 1 review round |
| [CH-05](CH-05.md) | Warn on generated / ignored files in commits | standard + SF | Done | a7497ff | 2 review rounds |
| [CH-06](CH-06.md) | Device rules as presets, auto-on for mobile projects | standard | Done | 2b18eb4 | 1 review round; paused once by the old installed guard |
| [CH-07](CH-07.md) | Close remaining fail-open paths in the guard | standard + SF | Done | 3a6ca12 | 2 review rounds; guard blocked its test text twice (owner: file tools for test files) |
| [CH-08](CH-08.md) | Split `guard.py` into scanner, Bash rules and hook entry | high-risk | Done | 99aedc3 | 1 high-risk round; output identical to main |
| [CH-09](CH-09.md) | Push policy that lets agents push feature branches, never `main` | high-risk | Planned | | Owner request 2026-11-23; after CH-12 |
| [CH-10](CH-10.md) | Text written into a file by a data heredoc is not a command | high-risk | Done | 7a42cf4 | 2 high-risk rounds; fail-closed checks for CRLF, comments, shadowed owners |
| [CH-11](CH-11.md) | Protect SSH private keys and read paths inside `$(…)` | standard | Done | 000f1ee | 1 review round; one flaky integrated run under heavy load, reruns OK |
| [CH-12](CH-12.md) | Push and protected-config detection fail closed on unsafe heredocs | standard | In progress | | Found in CH-10 review; owner 2026-11-23: before CH-09 |
