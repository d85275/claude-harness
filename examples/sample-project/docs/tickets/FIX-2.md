# FIX-2: Search ignores accented letters

## Status

done (2026-02-17)

## Labels

search

## Scope

Searching for `creme` finds `crème brûlée`. Matching ignores accents and letter case.

## Findings

- Review round 1: the search also matched inside ingredient notes; narrowed to titles and ingredients.

## Evidence at close

Tests cover `creme`, `CRÈME` and `crème`.
