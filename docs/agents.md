# Agents and models

The plugin's agents, the model each one runs on, and how work is routed to them. The loop they take part in is described in the [README](../README.md#how-it-works).

| Role | Model / effort | Used for |
|---|---|---|
| Coordinator (your main session) | Your choice; Opus recommended | Planning, routing, review decisions, docs upkeep |
| `implementer` | Sonnet / medium | Ordinary tickets |
| `implementer-advanced` | Opus / high | Tickets marked high-risk |
| `reviewer` | Sonnet / high | Ordinary tickets |
| `silent-failure-hunter` | Sonnet / high | Error handling, offline, sync and recovery changes |
| `high-risk-reviewer` | Opus / high; Fable if you set `highRiskReviewerModel` to `"fable"` | Identity, payments, deletion, security, shared contracts, releases |

## What each agent does

- **implementer**: one ticket, one worktree, one commit. Reports what it verified, what it did not run, and what was not possible.
- **implementer-advanced**: the same rules for high-risk tickets, with extra care for guards, credentials, git hooks and release.
- **reviewer**: read-only. Does not trust the implementer's report and judges only against the ticket's criteria.
- **high-risk-reviewer**: read-only senior review for identity, payments, deletion, security, shared contracts and release.
- **silent-failure-hunter**: read-only second look for swallowed errors, catch-all fallbacks, and "unknown" collapsed into a definite answer.

## Files and shell

Every agent and command carries one short **Files and shell** paragraph, so ticket work runs without a stream of approval prompts. Files are created and edited with the Edit and Write tools, never through the shell (`cat > file <<EOF`, `python3 - <<EOF`, `printf >> file`). Each Bash call runs one plain command; since a subagent's shell returns to its start folder between calls, paths are absolute (`git -C <absolute path>`, `--root <absolute worktree path>`) instead of `cd X && ...` chains, and a command that reads the current folder (test runners, builds, the Harness Verify commands) takes the tool's folder option (`python3 -m unittest discover -s <abs>/tests`, `make -C <abs>`, `npm --prefix <abs>`); without one the agent stops and reports, since it would otherwise test the main checkout instead of the worktree. Clean-up uses the build tool's clean task, not `rm` with wildcards or variables, and scratch files stay in the scratchpad. A command that still asks or is denied stops the work and is reported with the rule it hit, never rewritten to get around the check. The reviewers have no Write tool: they probe by rerunning tests or with a short `python3 -c`, may run plain commands that write their own output into the scratchpad (test results, `git archive -o`, `tar -x -C`, `cp`), for example to run new tests against the base tree, and list a probe that needs a hand-written file as not verified. `/start-ticket` and `/review` pass these rules into every implementer and reviewer brief.

## Why these models

Model names are aliases, never pinned versions, so new releases are picked up. There is no Haiku tier: routine implementation and review run on Sonnet, and Opus is used where a missed bug costs the most. Fable is another Anthropic model, used only as an opt-in for the high-risk reviewer (`highRiskReviewerModel`, see [configuration.md](configuration.md#keys)).

The plugin cannot set your main session's model. Opus is recommended for the coordinator because it makes the judgment calls, and a mistake there passes through every later step.

## Routing and the upgrade rule

`/start-ticket` picks the implementer from the ticket's review level. `/review` picks reviewers from the diff, not from the implementer's report. An implementer that finds its change riskier than planned says so in its report. The coordinator then marks the ticket `high-risk`, so review goes to `high-risk-reviewer`. Whether to redo the work with `implementer-advanced` is the owner's call. `/plan` marks a ticket `high-risk` when it is unsure, with a one-line reason.

Findings go back to the same implementer, which amends its one commit. After three rounds of "changes required" the coordinator stops and asks the owner.
