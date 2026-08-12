# Stack: Laravel — specifics

Entry point for Laravel specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. Promote a section to a sibling file (Form 2) later if it grows.

Nature → section:

| Caller | Sections |
| --- | --- |
| `/gm:laravel` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.

---

## core

- PHP 8.x (detect PHP version in local docker configuration or .gitlab-ci.yml main image pull), **PSR-12** coding style; enforce with Laravel Pint (`./vendor/bin/pint`).
- User-facing strings go through the localization helpers (`__()`, `trans(), @lang()`), NEVER hardcoded.
- Detection: `laravel/framework` in `composer.json` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`).

---

## dev

Consumed by `/gm:laravel`.

- **Eloquent**: models thin; Detect if project has guard mass-assignment with `$fillable`/`$guarded`if so use the same pattern for new code, otherwise if PHP attributes are used in the project use them instead; avoid N+1 with `with()` eager loading; use migrations for schema and use seeders/factories for test data or initial data injection (users roles, permissions, products tags, etc.) if needed.
- **HTTP layer**: validate input through Form Request classes, not inline in controllers; keep controllers thin — push domain logic into services/actions. If you modify existing controller and validator you must inform explicitly the user of the change.
- **Service providers** for wiring/bootstrapping; bind interfaces in the container rather than `new`ing dependencies. Use the Services pattern when relevant in new or existing code (you can challenge the legacy codebase to propose a code splitting with services usage).
- **Queues & events** for slow or side-effect work (mail, external calls); jobs are idempotent.
- **Testing**: Pest or PHPUnit via the runner (`${CLAUDE_SKILL_DIR}/../../shared/runner.md`); prefer feature tests hitting routes + `RefreshDatabase`, unit tests for services.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions. The review should also call the `/gm:security` to audit security breaches.

- **Mass-assignment** — request data passed to `create()`/`update()` without `$fillable`/validated data.
- **N+1** — missing eager loads on relations rendered in loops/collections.
- **Fat controllers** — business logic that belongs in a service/action; queries in controllers instead of the model/repository.
- **Code complexity** —  Identify complex code & propose a simplier alternative.
- **Validation** — routes accepting input without a Form Request or `$request->validate()`.
- **Standards** — Pint clean; typed signatures; no logic in Blade beyond presentation.

---

## security

Consumed by `/gm:security`,`/gm:review`, `/gm:merge-review`.

- **Mass-assignment & authorization** — `$fillable`/`$guarded` set; access enforced via Policies/Gates, not just route middleware (depending of the project legacy codebase and policies managment).
- **SQL** — parameter binding via Eloquent/query builder; flag raw `DB::raw`/string-interpolated SQL.
- **Secrets & config** — no secrets in code or committed `.env`; config read via `config()`/`env()` only in config files.
- **Dependencies** — Indicate weak dependiciees through`composer audit`
- **CSRF / XSS** — CSRF middleware on state-changing routes; Blade `{{ }}` auto-escapes — flag `{!! !!}` on untrusted data.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

- **Custom code** = the app repo: `app/` (Models, Http/Controllers, Services/Actions, Jobs, Providers) + `packages/` if existing with splitted app structure by sections,   `routes/`, `database/migrations`. Build/orchestration (`composer.json`, `artisan`, CI) → containers (C2).
- **Never detailed (black box, `type: external`)**: `vendor/**` — the Laravel framework and all packages. Show only what custom code calls (DB, external API, queue/cache backend) as `external` nodes.
- **Wiring source of truth**: routes → controllers → services/actions (entry points + `uses` edges); Eloquent models as `component` nodes; service-provider bindings reveal DI edges; jobs/events as async entry points. Stop at `vendor/`.