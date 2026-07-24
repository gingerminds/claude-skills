# Stack: WordPress — specifics

Entry point for WordPress specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. Promote a section to a sibling file (Form 2) later if it grows.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:wordpress` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.

---

## core

- Follow the **WordPress Coding Standards** (WPCS via `phpcs`); PHP 8.x.
- Extend through **hooks** (`add_action`/`add_filter`) — never edit core or a third-party plugin/theme directly.
- Custom code lives in a **custom plugin** and/or the **(child) theme**; prefix functions/classes/handles to avoid collisions.
- User-facing strings through the i18n API (`__()`, `_e()`, `esc_html__()`) with a text domain.
- Detection: `roots/wordpress` in `composer.json`, or a `wp-content/` tree (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`).

---

## dev

Consumed by `/gm:wordpress`.

- **Hooks first**: register behaviour on actions/filters; keep callbacks small and named (no giant closures).
- **Data access**: use the WP APIs (`WP_Query`, `get_posts`, options/transients, `$wpdb->prepare()` for custom SQL) — never string-built SQL.
- **Assets**: enqueue via `wp_enqueue_script`/`wp_enqueue_style`, never hardcoded `<script>`/`<link>`.
- **Custom content**: register CPTs/taxonomies/meta properly; use the Settings API for options screens.
- **Testing**: WP-CLI + PHPUnit (WP test suite) via the runner (`${CLAUDE_SKILL_DIR}/../../shared/runner.md`) where the project supports it.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **Escaping/sanitization** — output not escaped (`esc_html`/`esc_attr`/`esc_url`), input not sanitized (`sanitize_*`).
- **SQL** — custom queries not run through `$wpdb->prepare()`.
- **Core/plugin edits** — changes that should be hooks instead of direct edits to core or third-party code.
- **Enqueue** — assets injected inline instead of enqueued; missing dependencies/versioning.
- **Standards** — WPCS clean; prefixed names; text domain on strings.

---

## security

Consumed by `/gm:security`.

- **Nonces & capabilities** — state-changing actions verify a nonce (`check_admin_referer`/`wp_verify_nonce`) **and** `current_user_can()`.
- **Escaping/sanitization** — escape on output, sanitize on input; flag echo of raw request data.
- **SQL** — `$wpdb->prepare()` on every custom query with variables.
- **Dependencies** — audit plugins/themes and their versions; remove unused ones; keep core patched.
- **File/AJAX endpoints** — validate + authorize `admin-ajax`/REST callbacks; no unauthenticated privileged actions.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

- **Custom code** = the custom plugin(s) under `wp-content/plugins/<custom>` and the (child) theme under `wp-content/themes/<custom>`. Orchestration (`composer.json`, `wp-config` scaffolding, CI) → containers (C2).
- **Never detailed (black box, `type: external`)**: WordPress core, and third-party plugins/themes under `wp-content/` we don't own. Show only what custom code calls (DB via `$wpdb`, external API, core hooks it registers on) as `external` nodes.
- **Wiring source of truth**: hook registrations (`add_action`/`add_filter`) = entry points; custom classes/functions as `component` nodes; CPT/taxonomy/REST registrations as feature entry points; custom→custom calls as `uses` edges. Stop at the core/third-party boundary.
