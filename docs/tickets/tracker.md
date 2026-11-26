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
| [CH-09](CH-09.md) | Push policy that lets agents push feature branches, never `main` | high-risk | Deferred | | Parked after 3 high-risk rounds (owner rule); kept on `feature/ch-09` (21c2d14); pushes stay `ask` for 1.0 |
| [CH-10](CH-10.md) | Text written into a file by a data heredoc is not a command | high-risk | Done | 7a42cf4 | 2 high-risk rounds; fail-closed checks for CRLF, comments, shadowed owners |
| [CH-11](CH-11.md) | Protect SSH private keys and read paths inside `$(…)` | standard | Done | 000f1ee | 1 review round; one flaky integrated run under heavy load, reruns OK |
| [CH-12](CH-12.md) | Push and protected-config detection fail closed on unsafe heredocs | standard | Done | a2f0c10 | 1 review round |
| [CH-13](CH-13.md) | Stash and `core.hooksPath` checks fail closed; load-tolerant timing tests | standard | Done | 9283d96 | 2 review rounds; fixed a nested-text bypass inherited from CH-12 |
| [CH-14](CH-14.md) | Ticket parser and `dev-harness-board --check` | standard | Done | c81dfa2 | Milestone B; 1 review round |
| [CH-15](CH-15.md) | Board HTML | standard | Done | ff13924 | Milestone B |
| [CH-16](CH-16.md) | Board reads live worktrees | standard | Done | 62712f1 | Milestone B; owner requirement 2026-11-23 |
| [CH-17](CH-17.md) | Git hooks that rebuild the board | high-risk | Done | 70fd1f6 | Milestone B; chains existing hooks (LFS) |
| [CH-18](CH-18.md) | `/ticket-init` for a new project | standard | Done | 613f82e | Milestone B |
| [CH-19](CH-19.md) | `/ticket-init` adopts existing trackers, with a dry-run migration report | standard + SF | Done | 399c170 | Resumed 2026-11-24 for 1.1.0, owner-confirmed ticket list design |
| [CH-20](CH-20.md) | Start, review and close keep ticket state; release check in `/sync` | standard | Done | ce55063 | Milestone B |
| [CH-21](CH-21.md) | Check against real projects (read-only) and dogfood acceptance | standard | Done | aa306f5 | Milestone B; reduced scope (board only), owner 2026-11-23 |
| [CH-22](CH-22.md) | Guard asks before installing or removing the board's git hooks | standard | Done | 5d4211b | Owner request 2026-11-23 after the CH-17 review; 1 review round |
| [CH-23](CH-23.md) | Board title from a tracker column with another name | standard | Done | b427170 | Owner request 2026-11-23 |
| [CH-24](CH-24.md) | Sample project and board example | standard | Done | 2788679 | Milestone C |
| [CH-25](CH-25.md) | README rewrite with guards and configuration reference pages | standard | Done | 39f9973 | Milestone C |
| [CH-26](CH-26.md) | GitHub Actions running the tests | standard | Done | 2085ff4 | Milestone C |
| [CH-27](CH-27.md) | CHANGELOG, CONTRIBUTING and version 1.0.0 | high-risk | Done | 031ee09 | Milestone C |
| [CH-28](CH-28.md) | Pre-publish privacy sweep of the whole history | high-risk | Done | 2e69f1d | Milestone C |
| [CH-29](CH-29.md) | Plugin ticket commands leave projects that have not switched over alone | standard | Done | 50a3eed | Owner request 2026-11-23, before tagging 1.0 |
| [CH-30](CH-30.md) | Shorter README | self-review | Done | 4a187b3 | Owner request 2026-11-24, before tagging 1.0 |
| [CH-31](CH-31.md) | A wildcard write asks only when it can reach a protected config | high-risk | Done        | bf71a6c | Owner request 2026-11-24, after a false prompt in a real project |
| [CH-32](CH-32.md) | The board's Done column is collapsed by default | standard | Planned | | Owner decision 2026-11-25 |
| [CH-33](CH-33.md) | Fewer prompts during ticket work | high-risk | In review   | | Owner request 2026-11-25 |
