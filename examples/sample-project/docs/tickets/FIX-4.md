# FIX-4: Crash when importing a recipe with no steps

## Status

fixing (2026-03-04)

## Labels

import, crash

## Scope

Importing a recipe file with an empty `steps` list crashes the detail screen. Show the recipe with a note "No steps yet".

## Findings

- Review round 1: the fix is right, but there is no test for the empty list. Added to the fix list.
