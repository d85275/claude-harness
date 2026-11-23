---
description: Launch an implementer agent for one approved ticket in its own worktree, with a short brief.
argument-hint: <ticket-id> [notes for this run]
---

Start ticket **$ARGUMENTS**.

1. Read the project's Harness section (root `CLAUDE.md`) and its handoff/progress doc. Confirm the ticket exists in the spec or plan, its dependencies are integrated, and no owner decision blocks it. If something blocks it, stop and report.
2. Check the machine: `git status --short` on the main checkout is clean; no stale worktree/branch with this ticket's name; enough free disk (`df -h /`). Note any other ticket currently running.
   Read the ticket's state first (`dev-harness-board --json`, or its tracker row). If it is `Done`, `Blocked` or `Deferred`, stop and ask the owner; continue only if they say so.
3. Read the ticket's **Review level**: `high-risk` -> agent `implementer-advanced`; anything else (or no level) -> `implementer`.
4. Create the worktree (`git worktree add .worktrees/<id-lowercase> -b feature/<id-lowercase> <base>`), then in it run `dev-harness-board set-state <ID> "In progress" --root .worktrees/<id-lowercase>`. That edits the worktree's tracker row and the ticket's Status line only; main is untouched until close. Leave the edit uncommitted: the implementer's single commit carries it. The board shows the ticket as In progress.
5. Launch that agent in the background with a brief containing only:
   - the ticket ID and where its spec/acceptance criteria live;
   - the worktree path and the base commit (`git rev-parse HEAD` on the integration branch);
   - owner decisions that apply to this ticket and are not yet in the docs;
   - if another ticket is running: its worktree, and "use your own build cache and devices; no machine-wide simulator/emulator resets";
   - anything unusual about this run.
   Everything else (conventions, safety rules, evidence, commit, report format) is already in the agent definition and the project's CLAUDE.md; do not repeat it.
6. Tell the owner in one or two lines which agent started and why, and what happens next.
