# <ID>: <title>

Copy this file to `docs/tickets/<ID>.md` and add one row for it to `docs/tickets/tracker.md`, linking to the file. The tracker row is the source of truth for state; keep this file's Status line in step with it.

## Status

<planned | in progress | in review | fixing | owner check | done | blocked | deferred>, with date and owner decision if any.

## Depends on

Ticket IDs that must be integrated first, or `none`.

## Scope

What changes and why. What does not change (Don'ts).

## Done criteria

Observable results and the tests or checks that prove each one. A fix includes a test that fails before the fix.

## Allowed files

Paths the implementer may touch. Anything else is a follow-up, not part of this ticket.

## Review level

`high-risk` (implementer-advanced + high-risk-reviewer), `standard` (implementer + reviewer), or `self-review` (trivial, docs- or test-only). Add `+ silent-failure-hunter` for error-handling or recovery work. When unsure, choose `high-risk` and say why in one line.

## Findings

Review findings and how each was resolved.

## Evidence at close

Commit SHA, each command run with its exit code and counts. Separate verified from not run and not possible here.

## Follow-ups

Discovered work that was not done here.
