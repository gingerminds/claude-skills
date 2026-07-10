# Stack: Drupal — dispatcher

Entry point for Drupal specifics (Form 2: split folder). `MAIN.md` routes; it holds no detail itself.

**Always** load `${CLAUDE_SKILL_DIR}/../../stack/drupal/core.md` (standard, `t()`, Cache API vocabulary).

**Then** load the nature the caller needs:

| Caller | Nature to load |
|---|---|
| `/gm:drupal-11` | `${CLAUDE_SKILL_DIR}/../../stack/drupal/dev.md` |
| `/gm:review`, `/gm:merge-review` | `${CLAUDE_SKILL_DIR}/../../stack/drupal/review.md` |
| `/gm:security` | `${CLAUDE_SKILL_DIR}/../../stack/drupal/security.md` |
| `/gm:archi-c4` | `${CLAUDE_SKILL_DIR}/../../stack/drupal/archi.md` |

Load only the relevant nature(s), not all four — that's what keeps each run focused.

Cross-stack resources (stack-independent): `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.

All paths are anchored on `${CLAUDE_SKILL_DIR}` (the calling skill's base dir) so they resolve identically however this file was loaded.
