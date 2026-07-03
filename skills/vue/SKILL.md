---
name: vue
description: Guides Vue 3 / Nuxt 3 frontend development with Composition API, composables, Pinia, SSR/SSG, TypeScript, and component architecture. Use when implementing or reviewing Vue/Nuxt frontend code, composables, state management, or when the user asks for Vue.js / Nuxt expertise.
---

# Vue 3 / Nuxt 3 Frontend Expert

Entry point for Vue/Nuxt frontend development. This skill carries the **workflow**; the Vue knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Vue knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/vue/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (conventions, Composition API, composables, Pinia, SSR/SSG specifics, component design, testing). Apply that discipline throughout the work below.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's existing Vue/Nuxt code** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- List the key directories (`components/`, `composables/`, `pages/`, `stores/`, `layouts/`, `plugins/`, `server/`, `utils/`) and skim the structure.
- Identify reusable **composables, stores, components, and utilities** you could extend or reuse rather than rebuild.
- Note the **local idioms** (naming, composition patterns, state access patterns) so new code matches the surrounding style.
- Check the `nuxt.config.ts` / `vite.config.ts` for configured modules, aliases, and auto-imports to understand what is available globally.
- Surface any existing code your change should touch, extend, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket` to get the brief, then **survey** the existing Vue/Nuxt code (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is presentation, state management, data fetching, server-side logic, or cross-cutting concern (routing, auth, i18n).
3. **Propose** the cleanest architectural approach, citing the relevant pattern (composable, store, server route, etc.).
4. **Then** provide implementation.
5. **Mention** potential edge cases — especially SSR/hydration pitfalls and reactivity traps.
6. **Mention** performance considerations (bundle, render, cache).
7. **Mention** tests to add or update, and how to run them (`vitest` or `vitest --project nuxt`, via the runner — see `shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
