---
description: Send a finished ticket's commit to the right independent reviewer(s), and loop fixes back to the implementer with a round limit.
argument-hint: <ticket-id> [commit-or-range]
---

Review ticket **$ARGUMENTS**.

Run the state and board steps below only when `dev-harness-board status` exits 0 (the project has opted in with a `tickets` section in its `harness.json`); otherwise skip them silently and follow the project's own Harness section.

1. Find the worktree, branch, base, and commit (`git worktree list`, `git log <base>..<branch>`). Read the implementer's report and the ticket's acceptance criteria.
2. Run `dev-harness-board set-state <ID> "In review" --root .worktrees/<id-lowercase>` (row and Status line; "unchanged" is fine when the implementer already set it). Never edit the ticket's Findings or Evidence.
3. Pick reviewers from the diff, not from the report:
   - the ticket's Review level is `high-risk`, or any file is in a high-risk category from the project's Harness section -> `high-risk-reviewer`. If the implementer's report says the diff is riskier than planned, set the ticket's Review level to `high-risk` (ticket file, and the tracker row only when `dev-harness-board status` exits 0) so review goes here; whether to redo the work with `implementer-advanced` is the owner's call, so ask;
   - otherwise -> `reviewer`;
   - error handling, offline, sync, retry, or recovery changes -> also `silent-failure-hunter`.
   Run independent reviewers in parallel.
   For `high-risk-reviewer`, run `dev-harness-config get highRiskReviewerModel` (configs merge; do not parse `harness.json` yourself) and pass its output (`opus` or `fable`) as the Agent tool's `model` parameter. If the command is missing or exits non-zero, still pass the printed value if there is one, otherwise pass nothing (the agent's own `opus` applies), and tell the owner the stderr text.
4. Brief each reviewer with: worktree path, commit range, ticket/spec reference, where the evidence lives, and specific questions raised by the report (unfixed follow-ups, claims you doubt). Tell them about any other running ticket (no machine-wide resets).
5. On `CHANGES REQUIRED`: run `dev-harness-board set-state <ID> Fixing --root .worktrees/<id-lowercase>`, then send the findings (with file:line and the suggested fix) back to the same implementer. It amends its one commit (never a second commit) and sets the state back to In review before amending. When the amended commit is back, start the next round at step 2. Items that need an owner decision go to the owner, batched.
6. **Round limit: 3.** After a third `CHANGES REQUIRED` on the same ticket, stop and escalate to the owner with the open findings instead of looping.
7. Small, mechanical follow-up fixes after an approving round may be verified by the coordinator directly (read the diff, rerun the focused test) instead of another full review.
8. When the verdict is `ACCEPT` or `ACCEPT WITH MINOR NOTES` with Critical/Important items resolved, report and proceed to `/close-ticket`.
