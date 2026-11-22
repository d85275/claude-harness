# claude-harness

Claude Code plugin `dev-harness`: agents, commands and guard hooks for a plan -> implement -> review -> integrate loop. This repo will be public: never put private project names, paths, tickets or personal data in tracked files, tests or fixtures.

## Harness

- **Plans / spec**: [`docs/STATUS.md`](docs/STATUS.md) (milestones, next step, pending owner decisions). Ticket IDs look like `CH-12`.
- **Tickets**: one file per ticket in `docs/tickets/` from `TEMPLATE.md`; [`docs/tickets/tracker.md`](docs/tickets/tracker.md) is the source of truth for state. Evidence goes in the ticket file at close.
- **Worktrees**: `.worktrees/<id-lowercase>` on `feature/<id-lowercase>`, from `main`. One commit per ticket, cherry-picked onto `main`.
- **Commit format**: `<type>: <id-lowercase> <summary>` (e.g. `feat: ch-03 push policy`).
- **Verify**:
  - focused: `python3 -m unittest tests.<module>`
  - full: `python3 -m unittest discover -s tests`
  - static: `python3 -m py_compile hooks/scripts/*.py`
- **Dependencies**: Python 3 standard library only at runtime.
- **High-risk categories** (implementer-advanced + high-risk-reviewer): changes that weaken or bypass a guard (push policy, credential reads, protected configs), anything that runs on other people's machines unasked (git hooks, `core.hooksPath`), and release/publishing.
- **Boundaries - stop and ask the owner**: `git push`, changing repo visibility, tags, rewriting history.
