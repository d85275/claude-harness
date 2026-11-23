---
description: Turn an intent into a task contract (scope, done-criteria, allowed files, verification, stop conditions) for the owner to approve before any implementation starts.
argument-hint: <what you want built or fixed>
---

Plan: **$ARGUMENTS**

1. Read the root `CLAUDE.md` (and its **Harness** section), relevant area `CLAUDE.md` files, and the code involved. If the project already has a spec or ticket covering this, use it and say which; do not invent a second source of truth.
2. Recover what you can from the code and docs before asking anything. Ask only about choices with materially different outcomes.
3. Draft the contract, one section per task:
   - **Goal** - one sentence.
   - **Scope** - what changes; what explicitly does not.
   - **Files allowed** - paths or areas the implementer may touch.
   - **Done when** - testable acceptance criteria.
   - **Verify** - exact commands and the evidence each must produce.
   - **Risk** - `ordinary` or `high-risk` (using the project's categories) -> which reviewer, and which implementer (`high-risk` -> `implementer-advanced`). Mark `high-risk` when unsure, with a one-line reason.
   - **Depends on** - other tasks.
   - **Stop and ask if** - conditions that need the owner (new approvals, ambiguous requirements, production actions).
   - **Unknowns** - things you could not determine; keep them unknown, do not guess.
4. Show the owner a short summary (tasks, risk levels, open questions) and wait for approval or corrections. If the project has a ticket tracker and `dev-harness-board status` exits 0 (its Harness section names one, or `dev-harness-config get tickets` shows a `trackerPath` that exists), also propose the next free ticket ID for each task (read the tracker and `dev-harness-board --json`; keep the project's prefix and numbering). Do not start implementation in this command.
5. After approval, write the contract down:
   - **With a ticket tracker (only when `dev-harness-board status` exits 0; otherwise use the next option and leave the tracker alone):** one ticket file per task in the ticket folder, made from the project's `TEMPLATE.md` (Status `planned`; the contract sections go under the template's headings: Scope, Done criteria, Allowed files, Review level, Depends on; the Verify and Stop-and-ask lines go under Scope and Done criteria), and a `Planned` row for it in the tracker, linking the file. Run `dev-harness-board --check` to confirm the row and file agree. Commit nothing; the owner or coordinator commits before `/start-ticket`, which needs a clean main checkout.
   - **Without one:** write the contract to the project's plan location (Harness section; default `docs/plans/<yyyy-mm-dd>-<slug>.md`).
