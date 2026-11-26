# Stack: Drupal — dispatcher

Entry point for Drupal specifics (Form 2: split folder). `MAIN.md` routes; it holds no detail itself.

**Always** load `${CLAUDE_PLUGIN_ROOT}/stack/drupal/core.md` (standard, `t()`, Cache API vocabulary).

**Then** load the nature the caller needs:

| Caller | Nature to load |
|---|---|
| `/gm:drupal` | `${CLAUDE_PLUGIN_ROOT}/stack/drupal/dev.md` |
| `/gm:review`, `/gm:merge-review` | `${CLAUDE_PLUGIN_ROOT}/stack/drupal/review.md` |
| `/gm:security` | `${CLAUDE_PLUGIN_ROOT}/stack/drupal/security.md` |
| `/gm:archi-c4` | `${CLAUDE_PLUGIN_ROOT}/stack/drupal/archi.md` |

Load only the relevant nature(s), not all four — that's what keeps each run focused.

Cross-stack resources (stack-independent): `${CLAUDE_PLUGIN_ROOT}/shared/runner.md`, `${CLAUDE_PLUGIN_ROOT}/shared/stack-detect.md`.

All paths are anchored on `${CLAUDE_PLUGIN_ROOT}` (the plugin install dir) so they resolve identically however this file was loaded.
