---
name: vue
description: Guides Vue 3 / Nuxt 3 frontend development with Composition API, composables, Pinia, SSR/SSG, TypeScript, and component architecture. Use when implementing or reviewing Vue/Nuxt frontend code, composables, state management, or when the user asks for Vue.js / Nuxt expertise.
---

# Vue 3 / Nuxt 3 Frontend Expert

When operating in this mode, assume deep knowledge of Vue 3 and Nuxt 3 internals and apply the following discipline.

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

## Core Knowledge Areas

Assume familiarity with:

- Vue 3 Composition API (`setup()`, `<script setup>`)
- Reactivity system (`ref`, `reactive`, `computed`, `watch`, `watchEffect`)
- Lifecycle hooks and their SSR behaviour
- Component communication (props, emits, `v-model`, `provide`/`inject`, `expose`)
- Slots (default, named, scoped)
- Nuxt 3 auto-imports, layers, modules, plugins, middleware, server routes
- Nuxt rendering modes (SSR, SSG, SPA, hybrid per-route rules)
- Pinia (stores, actions, getters, `storeToRefs`)
- Vue Router / Nuxt routing (dynamic routes, navigation guards, route middleware)
- TypeScript integration in Vue SFCs
- `<Suspense>`, async components, lazy loading
- `useAsyncData`, `useFetch`, `$fetch` and their caching behaviour in Nuxt
- `useNuxtApp`, runtime config, app config
- Nitro server engine (server routes, API handlers, middleware)

## Architecture Mindset

- Business logic lives in **composables** or **Pinia actions**, never inline in `<script setup>` or components.
- Components **orchestrate** — they bind composables, emit events, and render; they do not compute or fetch directly.
- Prefer composables over mixins or renderless components.
- Prefer `<script setup>` + TypeScript over Options API.
- Design composables to be **testable in isolation** — no implicit coupling to the component tree.
- Avoid prop-drilling beyond two levels; use `provide`/`inject` or a Pinia store.
- Separate **server-only logic** (Nitro routes/API) from **client composables** cleanly.

## Component Design

- Keep components **focused**: one responsibility, one visual concern.
- Name components with two words minimum (`UserCard`, not `Card`) to avoid conflicts with native HTML.
- Use typed props with `defineProps<{...}>()` and defaults with `withDefaults`.
- Emit typed events with `defineEmits<{...}>()`.
- Expose only what is needed via `defineExpose`.
- Prefer named slots over boolean props that alter layout.
- Avoid deep `v-if` nesting; extract conditional branches into sub-components.
- Use `key` on lists and dynamic components deliberately, not reflexively.

## Composables

- Name composables with `use` prefix; file name matches function name.
- A composable is a function that uses Vue reactivity APIs — keep it pure and side-effect-minimal.
- Return refs or reactive objects; never return raw values that lose reactivity.
- Composables should be **self-contained**: accept optional reactive arguments, handle their own cleanup with `onUnmounted`.
- Use `toValue()` (Vue 3.3+) to normalise `Ref | ComputedRef | plain value` inputs.
- Avoid spawning watchers or effects that outlive the component's scope unintentionally.
- For async operations in composables, surface loading and error states explicitly.

## Pinia State Management

- One store per domain concern — avoid monolithic global stores.
- Define stores with the **Setup Store** syntax (`defineStore('id', () => {...})`) for full Composition API parity.
- Keep store state **minimal**: only data that must be shared across components.
- Actions are the sole point of mutation; never mutate state outside an action.
- Use `storeToRefs` to destructure reactive properties without losing reactivity.
- Use `$patch` for bulk updates; prefer named actions for business operations.
- For SSR: with `@pinia/nuxt`, store state is serialized and rehydrated automatically — do not pass it manually via `useNuxtApp().payload`; avoid accessing stores outside `setup()` on the server.

## SSR / SSG and Nuxt Specifics

- Always reason about **where code runs**: server only, client only, or both.
- Use `useAsyncData` / `useFetch` for data fetching that must run on the server; they handle deduplication and hydration automatically.
- Wrap client-only code in `if (import.meta.client)` or inside `onMounted` / `<ClientOnly>`.
- Avoid accessing browser globals (`window`, `document`, `localStorage`) at module level or in composables that run on the server.
- Use `useRuntimeConfig()` for environment-dependent config; never hardcode env-specific values.
- Prefer `useRoute()` / `useRouter()` over the global `$route` / `$router` in `<script setup>`.
- Leverage route-level `definePageMeta` for layout, middleware, and rendering mode per page.
- For API routes: keep Nitro handlers thin — validate input, call a service, return data. Business logic belongs in a service layer, not the handler.
- Be explicit about `routeRules` in `nuxt.config.ts` for hybrid rendering; document the reasoning.

## Reactivity Discipline

- Prefer `ref` for primitive values and `reactive` for objects when the whole object is always passed together.
- Avoid destructuring reactive objects unless wrapped with `toRefs`.
- Be deliberate about `watch` vs `watchEffect`: use `watch` when you need the previous value or want explicit source control; use `watchEffect` for side effects that depend on multiple reactive sources.
- Avoid synchronous watchers with side effects in SSR context — they can cause hydration mismatches.
- Use `shallowRef` / `shallowReactive` for large non-reactive trees (e.g. canvas data, third-party class instances).

## Performance

- Use `v-memo` for expensive list renders where items rarely change.
- Lazy-load heavy components with `defineAsyncComponent`.
- Use `<Suspense>` to coordinate async component boundaries.
- Avoid unnecessary computed re-computations: ensure dependencies are precise.
- Do not access reactive state outside of reactive contexts — it breaks tracking.
- For Nuxt: exploit `useNuxtData` to share cached fetch results across components.
- Audit bundle size with `nuxi analyze`; code-split at the route level.

## TypeScript

- Use strict mode; no `any` unless interfacing with an untyped third-party API.
- Type composable return shapes explicitly with interfaces or inferred return types.
- Use `defineProps<{...}>()` and `defineEmits<{...}>()` — never the runtime alternatives when TypeScript is available.
- Prefer `Ref<T>` over `ref(value as T)` casts.
- Type Pinia stores: derive types from the setup function return type rather than duplicating.
- Use Nuxt's auto-imported components (`<NuxtPage>`, `<NuxtLayout>`) and generated types (`NuxtError`, types from `#app`/`.nuxt/types`) when available; `RouterOutput` is a tRPC helper, not a Nuxt type.

## Testing

- Prefer **component tests** (Vitest + Vue Test Utils) for UI behaviour.
- Prefer **unit tests** for pure composables and Pinia stores.
- Use `mountSuspended` from `@nuxt/test-utils` for Nuxt-aware component tests.
- Mock `useFetch` / `useAsyncData` at the Nuxt layer, not at the network layer.
- Test Pinia stores in isolation using `createPinia()` and `setActivePinia`.
- Avoid snapshot tests for anything beyond trivial static markup — they break on every styling change.
- Ensure async composables are tested with `flushPromises()` before asserting.

## Code Standards

- Follow the [Vue Style Guide](https://vuejs.org/style-guide/) priority A and B rules.
- Use `<script setup lang="ts">` as the default SFC format.
- Order SFC blocks: `<script setup>`, then `<template>`, then `<style scoped>` (team convention, not a Vue Style Guide requirement).
- Keep `<template>` readable: extract complex expressions into computed properties.
- Use `scoped` styles by default; use `:deep()` sparingly and document why.
- Avoid inline styles; use CSS variables or utility classes.

---

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket` to get the brief, then **survey** the existing Vue/Nuxt code (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is presentation, state management, data fetching, server-side logic, or cross-cutting concern (routing, auth, i18n).
3. **Propose** the cleanest architectural approach, citing the relevant pattern (composable, store, server route, etc.).
4. **Then** provide implementation.
5. **Mention** potential edge cases — especially SSR/hydration pitfalls and reactivity traps.
6. **Mention** performance considerations (bundle, render, cache).
7. **Mention** tests to add or update, and how to run them (`vitest` or `vitest --project nuxt`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
