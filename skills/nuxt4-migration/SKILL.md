---
name: nuxt4-migration
description: Audits a Nuxt 3 project for Nuxt 4 migration readiness and drives the migration — runs `nuxt upgrade` and the official codemods, checks each breaking-change area (directory structure, useAsyncData/useFetch reactivity, Vite Environment API, jiti/case-sensitive routing, window.__NUXT__), audits and bumps the module ecosystem (Node version, Nuxt modules, TypeScript/Vite) to Nuxt-4-compatible versions, and produces a prioritized migration plan. Use when the user asks to migrate/upgrade a Nuxt project to v4, or invokes /gm:nuxt4-migration.
---

# Nuxt 3 → 4 Migration

Entry point for driving a Nuxt 3 → 4 migration. This skill carries the **workflow**; the migration procedure itself lives in the shared stack resource so `/gm:vue`, `/gm:review` and this skill draw from one source, and it stays a single place to update as Nuxt's own migration guidance evolves.

## Load the migration knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/vue/MAIN.md` for the **nuxt4-migration** nature — read its `## core` + `## nuxt4-migration` sections (scope check, official tooling, module ecosystem audit, breaking-change catalog, `future.compatibilityVersion`, build/test/iterate loop, output format, non-goals). Apply that procedure end to end; every manual code change should also follow the `## dev` section (Composition API conventions, composables, SSR/SSG specifics) — this skill drives the migration *sequence*, the stack resource governs both *what breaks* and *how the code itself should look* once touched.

## Review before wrapping up

Once every breaking-change area from the audit is addressed and the build/tests are green, run **`/gm:review`** on the full diff — it re-applies the `stack/vue/MAIN.md` **review** section (SSR/hydration pitfalls, reactivity misuse, standards) on top of the generic dimensions. Don't consider the migration done until that review has run; only then move to `/gm:merge-request`.
