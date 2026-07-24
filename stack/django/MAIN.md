# Stack: Django — specifics

Entry point for Django specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. Promote a section to a sibling file (Form 2) later if it grows.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:django` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.

---

## core

- Python 3.11+, **PEP 8**; format/lint with black + ruff.
- Type hints on new functions; mypy where the project runs it.
- Split by **app** (one Django app per bounded domain), not one monolith app.
- User-facing strings through `gettext` (`_()`), never hardcoded.
- Detection: `manage.py` or `django` in `requirements*.txt` / `pyproject.toml` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`).

---

## dev

Consumed by `/gm:django`.

- **Models**: keep them the single source of domain truth; add DB indexes deliberately; every schema change ships a migration (`makemigrations` + `migrate`).
- **ORM**: avoid N+1 with `select_related` (FK) / `prefetch_related` (M2M); push filtering into querysets, not Python loops.
- **Views**: thin — validate via forms/serializers, delegate logic to services or model methods. DRF serializers/viewsets for APIs.
- **Settings**: split per environment; secrets from env, never committed.
- **Testing**: `pytest`/`manage.py test` via the runner (`${CLAUDE_SKILL_DIR}/../../shared/runner.md`); use factories; test querysets and permissions, not just happy-path views.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **ORM N+1** — related objects accessed in loops/templates without `select_related`/`prefetch_related`.
- **Fat views** — business logic in views instead of services/model methods; querysets built in templates.
- **Migrations** — model change without a matching migration; data migrations mixed with schema without care.
- **Input** — request data used without a form/serializer validation layer.
- **Standards** — black/ruff clean; type hints; `_()` on user-facing strings.

---

## security

Consumed by `/gm:security`.

- **SQL** — ORM/parameterized queries; flag `.raw()`/`.extra()`/string-built SQL on untrusted input.
- **Auth/permissions** — enforced via decorators/DRF permission classes, not just template hiding; object-level checks where needed.
- **Secrets & settings** — `SECRET_KEY`/DB creds from env; `DEBUG=False` and correct `ALLOWED_HOSTS` in prod.
- **CSRF/XSS** — CSRF middleware on POST; templates auto-escape — flag `|safe`/`mark_safe` on untrusted data.
- **Dependencies** — `pip-audit` (or the lockfile-matching tool) on the tree; keep Django patched (LTS awareness).

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

- **Custom code** = the project apps: each app's `models.py`, `views.py`, `serializers.py`, `services*`, `urls.py`, `migrations/`. Orchestration (`manage.py`, `settings/`, CI) → containers (C2).
- **Never detailed (black box, `type: external`)**: `site-packages`/`venv` — Django and all third-party packages. Show only what custom code calls (DB, cache, external API, task broker) as `external` nodes.
- **Wiring source of truth**: `urls.py` → views (entry points); views → services/model methods (`uses` edges); models as `component` nodes; Celery/async tasks as async entry points. Stop at the third-party boundary.
