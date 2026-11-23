# FIX-3: Cooking timer keeps running after the app closes

## Status

in review (2026-03-02)

## Labels

timer, battery

## Scope

Stop the timer's background work when the app is closed, and show a notification instead of ringing in the background.

## Done criteria

- Closing the app cancels the countdown job.
- A finished timer shows one notification.
