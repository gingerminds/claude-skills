# Stack: Drupal — dispatcher

Entry point for Drupal specifics (Form 2: split folder). `MAIN.md` routes; it holds no detail itself.

**Always** load `core.md` (standard, `t()`, Cache API vocabulary).

**Then** load the nature the caller needs:

| Caller | Nature to load |
|---|---|
| `/gm:drupal-11` | `dev.md` |
| `/gm:review`, `/gm:merge-review` | `review.md` |
| `/gm:security` | `security.md` |

Load only the relevant nature(s), not all four — that's what keeps each run focused.

Cross-stack resources (stack-independent): `../../shared/runner.md`, `../../shared/stack-detect.md`.
