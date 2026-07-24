---
name: django
description: Guides Django backend development with apps, models, the ORM, migrations, DRF, forms/serializers, and testing. Use when implementing or reviewing Django/Python web code, views, models, serializers, or when the user asks for Django expertise.
---

# Django Backend Expert

Entry point for Django backend development. This skill carries the **workflow**; the Django knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Django knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/django/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (apps, models, ORM, migrations, DRF, testing). When the ticket reaches beyond plain feature work (security/access implications, or a change where standards/regressions are the crux), also read the `## security` or `## review` section so you build with the right lens from the start.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's existing apps** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- List the project apps and skim each app's `models.py`, `views.py`, `serializers.py`, `urls.py`, and any `services*`.
- Identify reusable **models, services, serializers, and mixins** you could extend or reuse rather than rebuild.
- Note the **local idioms** (fat models vs services, naming, settings layout) so new code matches the surrounding style.
- Surface any existing code your change should touch, extend, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket`, then **survey** the existing apps (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is domain/model logic, view/API layer, persistence/migration, or a cross-cutting concern (auth, tasks, i18n).
3. **Propose** the cleanest approach, citing the relevant pattern (model method, service, serializer/viewset, queryset).
4. **Then** provide implementation.
5. **Mention** potential edge cases.
6. **Mention** performance considerations (ORM N+1, indexes, caching).
7. **Mention** tests to add or update, and how to run them (via the runner — see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
