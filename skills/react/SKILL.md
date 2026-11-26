---
name: react
description: Guides React frontend development (including Next.js) with hooks, component architecture, state management, data fetching, and TypeScript. Use when implementing or reviewing React/Next.js frontend code, hooks, components, or when the user asks for React expertise.
---

# React Frontend Expert

Entry point for React (and Next.js) frontend development. This skill carries the **workflow**; the React knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the React knowledge

Load `${CLAUDE_PLUGIN_ROOT}/stack/react/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (conventions, hooks, component design, state/data fetching, Next.js specifics, testing). Apply that discipline throughout the work below.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's existing React code** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- List the key directories (`components/`, `hooks/`, `app/` or `pages/`, `context/` or `store/`, `services/`, `utils/`) and skim the structure.
- Identify reusable **hooks, components, contexts, and utilities** you could extend or reuse rather than rebuild.
- Note the **local idioms** (naming, state-management choice, data-fetching pattern) so new code matches the surrounding style.
- Check `next.config.*` / `vite.config.*` (or CRA config) for aliases, env exposure rules, and configured plugins to understand what's available globally.
- Surface any existing code your change should touch, extend, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Approach

Classify the problem (presentation, client state, server state/data fetching, routing, or a cross-cutting concern: auth, i18n, layout) and propose the cleanest approach, citing the pattern you rely on (custom hook, context, server component/action, route handler...), then implement it. Add or update the tests the change needs and run them (Vitest/Jest + React Testing Library, via the runner — see `${CLAUDE_PLUGIN_ROOT}/shared/runner.md`). Raise edge cases and performance risks (stale closures, effect dependency arrays, hydration pitfalls at the Next.js server/client component boundary, re-renders, memoization, bundle/code-splitting) only where they bear on this change.

**Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
