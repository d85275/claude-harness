# Pantry tickets

Pantry is a made-up recipe and meal-planning app, used only to show the dev-harness board. This table is the source of truth for ticket state.

States: `Planned`, `In progress`, `In review`, `Fixing`, `Owner check`, `Done`, `Blocked`, `Deferred`.

| ID | Task | Review | State | Commit | Owner | Note |
|---|---|---|---|---|---|---|
| OPS-0 | Set up the build server | standard | Done | 8b2c4d6 | Jo | Old ticket, closed before ticket files were kept |
| FIX-0 | Fix the launch crash on older phones | standard | Done | 3e9a7f1 | Mira | Old ticket, closed before ticket files were kept |
| [DOC-1](DOC-1.md) | Write the contributor guide | self-review | Done | 4be07a1 | Mira | |
| [FIX-1](FIX-1.md) | Shopping list loses items after a screen rotation | standard | Done | 91d3c5e | Jo | |
| [FIX-2](FIX-2.md) | Search ignores accented letters | standard | Done | c20f8ab | Mira | One review round |
| [FEAT-3](FEAT-3.md) | Share a recipe as a link | standard | Done | 7a6e2d4 | Jo | |
| [FIX-3](FIX-3.md) | Cooking timer keeps running after the app closes | standard | In review | | Jo | |
| [FEAT-4](FEAT-4.md) | Nutrition totals for a meal plan | standard | In review | | Priya | |
| [FIX-4](FIX-4.md) | Crash when importing a recipe with no steps | standard | Fixing | | Priya | Reviewer asked for a test |
| [APP-3](APP-3.md) | Meal planner screen | standard | In progress | | Mira | Twin of SITE-3 |
| [FEAT-2](FEAT-2.md) | Dark theme for recipe cards | standard | In progress | | Jo | |
| [OPS-1](OPS-1.md) | Rotate the image-hosting access key | high-risk | Owner check | 5e1b9f3 | Priya | Needs the owner to confirm the new key works |
| [OPS-2](OPS-2.md) | Move recipe photos to a new host | standard | Blocked | | | Waiting for the hosting contract |
| [DOC-2](DOC-2.md) | Translate the help pages | self-review | Deferred | | | After the first release |
| [SITE-3](SITE-3.md) | Meal planner page | standard | Planned | | | Twin of APP-3 |
| [FEAT-1](FEAT-1.md) | Scan a barcode to add a pantry item | standard | Planned | | | |
| [FIX-5](FIX-5.md) | Typo on the welcome screen | self-review | Planned | | | |
