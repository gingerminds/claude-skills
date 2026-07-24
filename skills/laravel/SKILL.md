---
name: laravel
description: Guides Laravel backend development with Eloquent, migrations, form requests, service providers, queues, events, and testing. Use when implementing or reviewing Laravel PHP code, controllers, services, models, or when the user asks for Laravel expertise.
---

# Laravel Backend Expert

Entry point for Laravel backend development. This skill carries the **workflow**; the Laravel knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Laravel knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/laravel/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (PSR-12, Eloquent, form requests, services, queues, testing). When the ticket reaches beyond plain feature work (security/access implications, or a change where standards/regressions are the crux), also read the `## security` or `## review` section so you build with the right lens from the start.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's existing app code** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- Skim `app/` (Models, Http/Controllers, Services/Actions, Jobs, Providers), `routes/`, and `database/migrations`.
- Identify reusable **services, actions, models, and traits** you could extend or inject rather than rebuild.
- Note the **local idioms** (naming, where domain logic lives, container bindings) so new code matches the surrounding style.
- Surface any existing code your change should touch, extend, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket`, then **survey** the existing app code (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is domain logic, HTTP/presentation, persistence, or a cross-cutting concern (auth, queue, events).
3. **Propose** the cleanest approach, citing the relevant pattern (form request, service/action, service provider binding, job/event).
4. **Then** provide implementation.
5. **Mention** potential edge cases.
6. **Mention** performance considerations (N+1, caching, queued work).
7. **Mention** tests to add or update, and how to run them (via the runner — see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
