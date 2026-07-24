# Stack: Laravel — specifics

Entry point for Laravel specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. Promote a section to a sibling file (Form 2) later if it grows.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:laravel` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.

---

## core

- PHP 8.2+, **PSR-12** coding style; enforce with Laravel Pint (`./vendor/bin/pint`).
- Follow the framework's directory conventions (`app/`, `routes/`, `database/`, `config/`); use artisan generators rather than hand-rolling boilerplate.
- User-facing strings go through the localization helpers (`__()`, `trans()`), never hardcoded.
- Detection: `laravel/framework` in `composer.json` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`).

---

## dev

Consumed by `/gm:laravel`.

- **Eloquent**: models thin; guard mass-assignment with `$fillable`/`$guarded`; avoid N+1 with `with()` eager loading; use migrations + factories/seeders for schema and test data.
- **HTTP layer**: validate input through Form Request classes, not inline in controllers; keep controllers thin — push domain logic into services/actions.
- **Service providers** for wiring/bootstrapping; bind interfaces in the container rather than `new`-ing dependencies.
- **Queues & events** for slow or side-effect work (mail, external calls); jobs are idempotent.
- **Testing**: Pest or PHPUnit via the runner (`${CLAUDE_SKILL_DIR}/../../shared/runner.md`); prefer feature tests hitting routes + `RefreshDatabase`, unit tests for services.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **Mass-assignment** — request data passed to `create()`/`update()` without `$fillable`/validated data.
- **N+1** — missing eager loads on relations rendered in loops/collections.
- **Fat controllers** — business logic that belongs in a service/action; queries in controllers instead of the model/repository.
- **Validation** — routes accepting input without a Form Request or `$request->validate()`.
- **Standards** — Pint clean; typed signatures; no logic in Blade beyond presentation.

---

## security

Consumed by `/gm:security`.

- **Mass-assignment & authorization** — `$fillable`/`$guarded` set; access enforced via Policies/Gates, not just route middleware.
- **SQL** — parameter binding via Eloquent/query builder; flag raw `DB::raw`/string-interpolated SQL.
- **Secrets & config** — no secrets in code or committed `.env`; config read via `config()`/`env()` only in config files.
- **Dependencies** — `composer audit` on the tree; keep `laravel/framework` patched.
- **CSRF / XSS** — CSRF middleware on state-changing routes; Blade `{{ }}` auto-escapes — flag `{!! !!}` on untrusted data.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

- **Custom code** = the app repo: `app/` (Models, Http/Controllers, Services/Actions, Jobs, Providers), `routes/`, `database/migrations`. Build/orchestration (`composer.json`, `artisan`, CI) → containers (C2).
- **Never detailed (black box, `type: external`)**: `vendor/**` — the Laravel framework and all packages. Show only what custom code calls (DB, external API, queue/cache backend) as `external` nodes.
- **Wiring source of truth**: routes → controllers → services/actions (entry points + `uses` edges); Eloquent models as `component` nodes; service-provider bindings reveal DI edges; jobs/events as async entry points. Stop at `vendor/`.
