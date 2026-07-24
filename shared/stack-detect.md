# Stack detection

The generic skills (`review`, `security`, `merge-review`…) stay **stack-agnostic**. Before applying their dimensions, detect the project's stack(s), then load the matching specific resource.

## Signals

| Signal (at the project root) | Stack |
|---|---|
| `composer.json` contains `drupal/core*` | **drupal** |
| `composer.json` contains `roots/wordpress`, or a `wp-content/` tree | **wordpress** |
| `composer.json` contains `laravel/framework` | **laravel** |
| `composer.json` with no CMS/framework | php (generic) — no `stack/` resource |
| `package.json` depends on `vue` / `nuxt` | **vue** |
| `package.json` with no known front framework | js (generic) — no `stack/` resource |
| `manage.py`, or `django` in `requirements*.txt` / `pyproject.toml` | **django** |
| `pyproject.toml` / `requirements*.txt` with no django | **python** |

Resolution order matters: test **laravel** before the generic `php`, and **django** before **python** (django is a Python web subset). Actually read the files (`composer.json`, `package.json`, `pyproject.toml`) — don't guess from the repo name.

## Loading rule

- **One stack detected** → load its entry point `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md`, stating the **nature** the caller needs (`core` + `dev` | `review` | `security`). `MAIN.md` routes to the right content, whatever its form (single sectioned file or split folder).
- **Multiple stacks** (Drupal + Vue monorepo, a theme with a JS pipeline…) → load each, or ask the user which one governs when the change's scope is ambiguous.
- **Generic `php` / `js`** (a `composer.json`/`package.json` with no CMS or known framework) → there is **no `stack/php` or `stack/js` resource**; treat exactly like *no known stack* below — never attempt to `Read` a non-existent `MAIN.md`.
- **No known stack** → stay generic, load no `stack/` resource, and say so.

The **runner** is cross-cutting (stack-independent): see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`.

## Dev skill routing

Naming principle: **stack name == dev skill name == `stack/<stack>/` folder**. The main consumer of this mapping is `/gm:ticket`, which detects the stack and hands off to the matching dev skill.

| Stack | Dev skill |
|---|---|
| drupal | `/gm:drupal` |
| wordpress | `/gm:wordpress` |
| vue | `/gm:vue` |
| laravel | `/gm:laravel` |
| django | `/gm:django` |
| python | `/gm:python` |
| php (generic) | — none: generic dev discipline |
| js (generic) | — none: generic dev discipline |

- **Stack with a dev skill** → hand off to `/gm:<stack>`.
- **Generic `php` / `js`, or a detected stack with no dedicated skill yet** → there is no `/gm:<stack>` to route to; say so and fall back to generic dev discipline (plus the `stack/` resource if one exists).
