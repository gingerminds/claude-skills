---
name: angular
description: Guides Angular frontend development with standalone components, RxJS, dependency injection, signals, and reactive forms. Use when implementing or reviewing Angular frontend code, components, services, or when the user asks for Angular expertise.
---

# Angular Frontend Expert

Entry point for Angular frontend development. This skill carries the **workflow**; the Angular knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Angular knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/angular/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (conventions, DI, RxJS, signals, component design, forms, testing). Apply that discipline throughout the work below.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's existing Angular code** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- List the key directories (`components/`, `services/`, `guards/`, `interceptors/`, `pipes/`, `directives/`, a `store/` if NgRx/signal-store is used) and skim the structure.
- Identify reusable **services, components, directives, and pipes** you could extend or reuse rather than rebuild.
- Note the **local idioms** (standalone vs NgModules, RxJS vs signals, DI patterns) so new code matches the surrounding style.
- Check `angular.json` and the app's `app.config.ts` / root `AppModule` for configured providers, interceptors, and build targets to understand what's available globally.
- Surface any existing code your change should touch, extend, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket` to get the brief, then **survey** the existing Angular code (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is presentation, a service/business-logic concern, routing/navigation, or a cross-cutting concern (auth, i18n, forms).
3. **Propose** the cleanest approach, citing the relevant pattern (service + DI, guard/resolver, signal store...).
4. **Then** provide implementation.
5. **Mention** potential edge cases — especially RxJS subscription leaks and change-detection surprises.
6. **Mention** performance considerations (change detection strategy, bundle/lazy loading).
7. **Mention** tests to add or update, and how to run them (`ng test` / Jest, via the runner — see `shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
