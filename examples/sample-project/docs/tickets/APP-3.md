# APP-3: Meal planner screen

## Status

in progress (2026-03-04)

## Labels

meal-plan, screen

## Depends on

none. The web twin, SITE-3, waits for the plan format fixed here.

## Scope

A week view where each day holds up to four meals. Tapping a slot opens a recipe picker.

- Drag a meal to another day to move it.
- Long-press a meal to remove it.
- The plan is saved as JSON, so the web page can read the same file.

```json
{"week": "2026-W10", "days": [{"day": "mon", "meals": ["lentil-soup", "pancakes"]}]}
```

## Done criteria

| Check | How |
|---|---|
| A plan survives closing the app | UI test: fill a plan, restart, compare |
| A meal moves to another day | UI test with a drag gesture |
| The saved file matches the format | Unit test against the example above |

See the [design notes](https://example.com/pantry/meal-planner) for the layout.

## Allowed files

`app/planner/`, `app/tests/planner/`

## Review level

standard
