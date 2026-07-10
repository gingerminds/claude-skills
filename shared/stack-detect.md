# Stack detection

The generic skills (`review`, `security`, `merge-review`…) stay **stack-agnostic**. Before applying their dimensions, detect the project's stack(s), then load the matching specific resource.

## Signals

| Signal (at the project root) | Stack |
|---|---|
| `composer.json` contains `drupal/core*` | **drupal** |
| `composer.json` contains `roots/wordpress`, or a `wp-content/` tree | **wordpress** |
| `package.json` depends on `vue` / `nuxt` | **vue** |
| `manage.py`, or `django` in `requirements*.txt` / `pyproject.toml` | **django** |
| `composer.json` with no CMS | php (generic) — no `stack/` resource |
| `package.json` with no known front framework | js (generic) — no `stack/` resource |

Actually read the files (`composer.json`, `package.json`) — don't guess from the repo name.

## Loading rule

- **One stack detected** → load its entry point `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md`, stating the **nature** the caller needs (`core` + `dev` | `review` | `security`). `MAIN.md` routes to the right content, whatever its form (single sectioned file or split folder).
- **Multiple stacks** (Drupal + Vue monorepo, a theme with a JS pipeline…) → load each, or ask the user which one governs when the change's scope is ambiguous.
- **Generic `php` / `js`** (a `composer.json`/`package.json` with no CMS or known framework) → there is **no `stack/php` or `stack/js` resource**; treat exactly like *no known stack* below — never attempt to `Read` a non-existent `MAIN.md`.
- **No known stack** → stay generic, load no `stack/` resource, and say so.

The **runner** is cross-cutting (stack-independent): see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`.
