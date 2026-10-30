---
name: react
description: Guides React frontend development (including Next.js) with hooks, component architecture, state management, data fetching, and TypeScript. Use when implementing or reviewing React/Next.js frontend code, hooks, components, or when the user asks for React expertise.
---

# React Frontend Expert

Entry point for React (and Next.js) frontend development. This skill carries the **workflow**; the React knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the React knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/react/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (conventions, hooks, component design, state/data fetching, Next.js specifics, testing). Apply that discipline throughout the work below.

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

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket` to get the brief, then **survey** the existing React code (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is presentation, client state, server state/data fetching, routing, or a cross-cutting concern (auth, i18n, layout).
3. **Propose** the cleanest approach, citing the relevant pattern (custom hook, context, server component/action, route handler...).
4. **Then** provide implementation.
5. **Mention** potential edge cases — especially stale closures, effect dependency arrays, and hydration pitfalls (Next.js server/client component boundary).
6. **Mention** performance considerations (re-renders, memoization, bundle/code-splitting).
7. **Mention** tests to add or update, and how to run them (Vitest/Jest + React Testing Library, via the runner — see `shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
