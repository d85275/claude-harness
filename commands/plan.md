---
description: Turn an intent into a task contract (scope, done-criteria, allowed files, verification, stop conditions) for the owner to approve before any implementation starts.
argument-hint: <what you want built or fixed>
---

Plan: **$ARGUMENTS**

1. Read the root `CLAUDE.md` (and its **Harness** section), relevant area `CLAUDE.md` files, and the code involved. If the project already has a spec or ticket covering this, use it and say which; do not invent a second source of truth.
2. Recover what you can from the code and docs before asking anything. Ask only about choices with materially different outcomes.
3. Write the contract to the project's plan location (Harness section; default `docs/plans/<yyyy-mm-dd>-<slug>.md`) with one section per task:
   - **Goal** - one sentence.
   - **Scope** - what changes; what explicitly does not.
   - **Files allowed** - paths or areas the implementer may touch.
   - **Done when** - testable acceptance criteria.
   - **Verify** - exact commands and the evidence each must produce.
   - **Risk** - `ordinary` or `high-risk` (using the project's categories) -> which reviewer.
   - **Depends on** - other tasks.
   - **Stop and ask if** - conditions that need the owner (new approvals, ambiguous requirements, production actions).
   - **Unknowns** - things you could not determine; keep them unknown, do not guess.
4. Show the owner a short summary (tasks, risk levels, open questions) and wait for approval or corrections. Do not start implementation in this command.
