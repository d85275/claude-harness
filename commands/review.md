---
description: Send a finished ticket's commit to the right independent reviewer(s), and loop fixes back to the implementer with a round limit.
argument-hint: <ticket-id> [commit-or-range]
---

Review ticket **$ARGUMENTS**.

1. Find the worktree, branch, base, and commit (`git worktree list`, `git log <base>..<branch>`). Read the implementer's report and the ticket's acceptance criteria.
2. Pick reviewers from the diff, not from the report:
   - any file in a high-risk category from the project's Harness section -> `high-risk-reviewer`;
   - otherwise -> `reviewer`;
   - error handling, offline, sync, retry, or recovery changes -> also `silent-failure-hunter`.
   Run independent reviewers in parallel.
3. Brief each reviewer with: worktree path, commit range, ticket/spec reference, where the evidence lives, and specific questions raised by the report (unfixed follow-ups, claims you doubt). Tell them about any other running ticket (no machine-wide resets).
4. On `CHANGES REQUIRED`: send the findings (with file:line and the suggested fix) back to the same implementer to amend its commit. Items that need an owner decision go to the owner, batched.
5. **Round limit: 3.** After a third `CHANGES REQUIRED` on the same ticket, stop and escalate to the owner with the open findings instead of looping.
6. Small, mechanical follow-up fixes after an approving round may be verified by the coordinator directly (read the diff, rerun the focused test) instead of another full review.
7. When the verdict is `ACCEPT` or `ACCEPT WITH MINOR NOTES` with Critical/Important items resolved, report and proceed to `/close-ticket`.
