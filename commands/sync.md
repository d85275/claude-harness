---
description: Compare the plan/spec and handoff docs against what is actually integrated, and report drift. Read-only.
argument-hint: [optional scope, e.g. a phase or ticket range]
---

Sync check **$ARGUMENTS**.

1. Read the project's plan/spec, its handoff/progress doc, and its evidence log (Harness section).
2. Gather facts: `git log --oneline` on the integration branch (match commit messages to ticket IDs), `git worktree list`, `git branch --list 'feature/*'`, and unpushed state (`git status -sb`).
3. Report, in a short table:
   - tickets the docs call done but with no commit on the integration branch (or the reverse);
   - committed-but-not-integrated branches and open worktrees, with their state;
   - handoff "active ticket" / "next step" lines that are stale;
   - owner decisions and deferred items recorded as open;
   - counts: done / in progress / remaining.
4. Do not edit anything. Offer the specific doc fixes for the owner to approve.
