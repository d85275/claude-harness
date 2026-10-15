# claude-harness (plugin: `dev-harness`)

A small Claude Code plugin for a disciplined **plan -> implement -> independent review -> integrate** loop. It grew out of porting an Android app to iOS ticket by ticket with Claude Code: one worktree and one commit per ticket, reviews that check evidence rather than claims, and guardrails around anything outward-facing.

## Install

Inside Claude Code:

```text
/plugin marketplace add d85275/claude-harness
/plugin install dev-harness@dev-harness
```

Or from a terminal (the `/plugin` form is not a shell command):

```sh
claude plugin marketplace add d85275/claude-harness
claude plugin install dev-harness@dev-harness
```

Restart running Claude Code sessions to pick up the agents, commands, and hooks. For local development, add the marketplace from a path instead: `claude plugin marketplace add ~/Projects/claude-harness`.

Then add a `## Harness` section to the project's `CLAUDE.md` (template: [`templates/CLAUDE-harness-section.md`](templates/CLAUDE-harness-section.md)) and, optionally, `.claude/harness.json` for project-specific hook rules ([`templates/harness.json`](templates/harness.json)).

## The loop

| Step | Command / agent | Gate |
|---|---|---|
| Plan | `/plan <intent>` | Owner approves scope, done-criteria, allowed files, stop conditions. Skip when a spec already defines the ticket. |
| Implement | `/start-ticket <id>` -> `implementer` (Sonnet) | Own worktree, one commit, fresh evidence, nothing outward-facing. |
| Review | `/review <id>` -> `reviewer` (Sonnet) or `high-risk-reviewer` (Opus), plus `silent-failure-hunter` for error/offline/recovery work | Reviewer verifies evidence itself; fixes loop back to the implementer; max 3 rounds, then the owner decides. |
| Integrate | `/close-ticket <id>` | Cherry-pick, integrated check, remove worktree/branch/caches/test devices. Never pushes. |
| Check drift | `/sync` | Plan and handoff docs vs. what is actually on the integration branch. |

Model choice is deliberate: routine implementation and review run on Sonnet; Opus is reserved for high-risk review, where a missed bug costs the most.

## Agents

- **implementer** - one ticket, one worktree, one commit; reports verified vs. not-run vs. not-possible.
- **reviewer** - read-only; does not trust the implementer's report; judges only against the ticket's criteria.
- **high-risk-reviewer** - read-only senior review for identity, payments, deletion, security, shared contracts, release.
- **silent-failure-hunter** - read-only second lens for swallowed errors, catch-all fallbacks, and "unknown" collapsed into a definite answer.

## Hooks (`hooks/scripts/guard.py`)

| Hook | Behavior |
|---|---|
| Bash: credential reads | Blocks `cat`/`cp`/`grep`/... on keys, keystores, `.p8`, service-account JSON, `.env` (plus `protectedPaths`). |
| Bash: outward-facing commands | Asks the owner before `git push`, `gh pr create/merge`, `gh repo create`, `firebase deploy` and other destructive Firebase/gcloud/terraform/kubectl commands (plus `askCommands`). |
| Bash: machine-wide device resets | Blocks `simctl shutdown all`, `simctl delete/erase all`, `adb emu kill`, killing all simulators, while another `xcodebuild`/`gradlew` run is active, so parallel agents do not kill each other's devices. |
| Read: credentials | Blocks reading protected credential files. |
| Edit/Write: protected configs | Asks before editing lint/format configs, test plans/runner configs, `.claude` settings, and `protectedConfigs` (e.g. validator thresholds), so agents fix code instead of weakening checks. |
| Edit/Write: weakened checks | Asks when an edit *adds* a test skip, accessibility-audit filter, or lint/type suppression (`XCTSkip`, `@skip`, `it.skip`, `@Ignore`, `@Suppress`, `# noqa`, `@ts-ignore`, `eslint-disable`, `--no-verify`, ...; extend with `weakeningMarkers`, turn off with `"checkWeakening": false`). Moving or removing an existing one is allowed. |
| Notification | macOS desktop notification when Claude needs you (`"notify": false` to turn off). |

Hooks are best-effort guardrails, not a sandbox; keep permission deny rules for anything that must never happen.

Tests: `python3 -m unittest discover -s tests`.

## Layout

```text
.claude-plugin/   plugin.json, marketplace.json
agents/           implementer, reviewer, high-risk-reviewer, silent-failure-hunter
commands/         plan, start-ticket, review, close-ticket, sync
hooks/            hooks.json, scripts/guard.py
templates/        CLAUDE.md Harness section, harness.json
tests/            guard.py tests
```

## Credits

Ideas borrowed from [Chachamaru127/claude-code-harness](https://github.com/Chachamaru127/claude-code-harness) (role-based model routing, plan contracts, evidence-only review, sync) and [affaan-m/ECC](https://github.com/affaan-m/ECC) (config protection, silent-failure review, notifications).

## License

MIT
