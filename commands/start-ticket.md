---
description: Launch an implementer agent for one approved ticket in its own worktree, with a short brief.
argument-hint: <ticket-id> [notes for this run]
---

Start ticket **$ARGUMENTS**.

1. Read the project's Harness section (root `CLAUDE.md`) and its handoff/progress doc. Confirm the ticket exists in the spec or plan, its dependencies are integrated, and no owner decision blocks it. If something blocks it, stop and report.
2. Check the machine: `git status --short` on the main checkout is clean; no stale worktree/branch with this ticket's name; enough free disk (`df -h /`). Note any other ticket currently running.
3. Read the ticket's **Review level**: `high-risk` -> agent `implementer-advanced`; anything else (or no level) -> `implementer`.
4. Launch that agent in the background with a brief containing only:
   - the ticket ID and where its spec/acceptance criteria live;
   - the base commit (`git rev-parse HEAD` on the integration branch);
   - owner decisions that apply to this ticket and are not yet in the docs;
   - if another ticket is running: its worktree, and "use your own build cache and devices; no machine-wide simulator/emulator resets";
   - anything unusual about this run.
   Everything else (conventions, safety rules, evidence, commit, report format) is already in the agent definition and the project's CLAUDE.md; do not repeat it.
5. Tell the owner in one or two lines which agent started and why, and what happens next.
