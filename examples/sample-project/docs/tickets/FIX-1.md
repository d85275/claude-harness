# FIX-1: Shopping list loses items after a screen rotation

## Status

done (2026-02-10)

## Labels

shopping-list, regression

## Scope

Ticked and unticked items are kept in the view model, not in the screen, so a rotation no longer resets them.

## Done criteria

- A test that ticks an item, rotates and checks the item is still ticked fails before the fix.

## Evidence at close

New test passes; the full suite passes.
