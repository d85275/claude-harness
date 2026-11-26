---
description: Send a finished ticket's commit to the right independent reviewer(s), and loop fixes back to the implementer with a round limit.
argument-hint: <ticket-id> [commit-or-range]
---

Review ticket **$ARGUMENTS**.

Run the state and board steps below only when `dev-harness-board status` exits 0 (the project has opted in with a `tickets` section in its `harness.json`); otherwise skip them silently and follow the project's own Harness section.

**Files and shell.** Create and edit files with the Edit and Write tools only, never through the shell (`cat > file <<EOF`, `python3 - <<EOF`, `printf >> file`). Run one plain command per Bash call, with absolute paths, `git -C <absolute path>` and `--root <absolute worktree path>` instead of `cd X && ...` chains. A command that reads the current folder (the Harness Verify commands, test runners, builds) takes the tool's folder option (`python3 -m unittest discover -s <abs>/tests`, `make -C <abs>`, `npm --prefix <abs>`, `go -C <abs>`, `./gradlew -p <abs>`, `git -C <abs>`); if it has none, stop and report rather than run it from the wrong tree. Clean with the build tool's clean task, never `rm` with wildcards or variables. If a command still asks or is denied, stop and say which rule it hit; don't rewrite it to get around the check. Pass these rules into every reviewer brief and every set of findings sent back to the implementer.

1. Find the worktree, branch, base, and commit (`git worktree list`, `git log <base>..<branch>`). Read the implementer's report and the ticket's acceptance criteria.
2. Run `dev-harness-board set-state <ID> "In review" --root <absolute worktree path>` (row and Status line; "unchanged" is fine when the implementer already set it). Never edit the ticket's Findings or Evidence.
3. Pick reviewers from the diff, not from the report:
   - the ticket's Review level is `high-risk`, or any file is in a high-risk category from the project's Harness section -> `high-risk-reviewer`. If the implementer's report says the diff is riskier than planned, set the ticket's Review level to `high-risk` (ticket file, and the tracker row only when `dev-harness-board status` exits 0) so review goes here; whether to redo the work with `implementer-advanced` is the owner's call, so ask;
   - otherwise -> `reviewer`;
   - error handling, offline, sync, retry, or recovery changes -> also `silent-failure-hunter`.
   Run independent reviewers in parallel.
   For `high-risk-reviewer`, run `dev-harness-config get highRiskReviewerModel` (configs merge; do not parse `harness.json` yourself) and pass its output (`opus` or `fable`) as the Agent tool's `model` parameter. If the command is missing or exits non-zero, still pass the printed value if there is one, otherwise pass nothing (the agent's own `opus` applies), and tell the owner the stderr text.
4. Brief each reviewer with: worktree path, commit range, ticket/spec reference, where the evidence lives, and specific questions raised by the report (unfixed follow-ups, claims you doubt). Tell them about any other running ticket (no machine-wide resets), and give them the working rules from **Files and shell** above in a few lines: one plain command per Bash call, absolute paths for `git -C`, the tool's folder option for test runners and builds, no hand-written files through the shell (`printf >> file`, `cat > f <<EOF`) and no deletes, while plain commands that write their own output into the scratchpad are fine (test results, `git archive -o <scratch>/x.tar <sha>`, `tar -x -f <scratch>/x.tar -C <scratch>/base`, `cp` into the scratchpad), a probe that needs a hand-written file goes under "not verified", and a command that asks or is denied is reported, not worked around.
5. On `CHANGES REQUIRED`: run `dev-harness-board set-state <ID> Fixing --root <absolute worktree path>`, then send the findings (with file:line and the suggested fix) back to the same implementer, with the working rules from **Files and shell** above. It amends its one commit (never a second commit) and sets the state back to In review before amending. When the amended commit is back, start the next round at step 2. Items that need an owner decision go to the owner, batched.
6. **Round limit: 3.** After a third `CHANGES REQUIRED` on the same ticket, stop and escalate to the owner with the open findings instead of looping.
7. Small, mechanical follow-up fixes after an approving round may be verified by the coordinator directly (read the diff, rerun the focused test) instead of another full review.
8. When the verdict is `ACCEPT` or `ACCEPT WITH MINOR NOTES` with Critical/Important items resolved, report and proceed to `/close-ticket`.
