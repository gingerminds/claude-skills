# Stack: Vue / Nuxt — specifics

Entry point for Vue/Nuxt specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. If a section grows too large it can be promoted to a sibling file (Form 2) without touching the callers.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:vue` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md` (anchored on the calling skill's base dir).

---

## core

Shared baseline (all natures):

- **`<script setup lang="ts">`** as the default SFC format; TypeScript strict mode, no `any` unless interfacing an untyped third-party API.
- Follow the [Vue Style Guide](https://vuejs.org/style-guide/) priority A and B rules.
- Component names are **two words minimum** (`UserCard`, not `Card`).
- SFC block order (team convention): `<script setup>`, `<template>`, `<style scoped>`.
- Lint via the project runner (see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`): eslint + prettier, `vue-tsc` for type checks.
- Detection: `vue` / `nuxt` in `package.json` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`).

---

## dev

Consumed by `/gm:vue`.

### Core knowledge areas

- Vue 3 Composition API (`setup()`, `<script setup>`)
- Reactivity (`ref`, `reactive`, `computed`, `watch`, `watchEffect`)
- Lifecycle hooks and their SSR behaviour
- Component communication (props, emits, `v-model`, `provide`/`inject`, `expose`); slots (default, named, scoped)
- Nuxt 3 auto-imports, layers, modules, plugins, middleware, server routes
- Nuxt rendering modes (SSR, SSG, SPA, hybrid per-route)
- Pinia (stores, actions, getters, `storeToRefs`)
- Vue Router / Nuxt routing (dynamic routes, navigation guards, route middleware)
- TypeScript in SFCs; `<Suspense>`, async components, lazy loading
- `useAsyncData`, `useFetch`, `$fetch` and their caching in Nuxt; `useNuxtApp`, runtime config, app config
- Nitro server engine (server routes, API handlers, middleware)

### Architecture mindset

- Business logic lives in **composables** or **Pinia actions**, never inline in `<script setup>`.
- Components **orchestrate** — bind composables, emit events, render; they don't compute or fetch directly.
- Prefer composables over mixins/renderless components; `<script setup>` + TS over Options API.
- Design composables **testable in isolation** — no implicit coupling to the component tree.
- Avoid prop-drilling beyond two levels; use `provide`/`inject` or a Pinia store.
- Separate **server-only logic** (Nitro routes/API) from **client composables** cleanly.

### Component design

- Keep components focused: one responsibility, one visual concern.
- Typed props with `defineProps<{...}>()` + `withDefaults`; typed events with `defineEmits<{...}>()`; expose only what's needed via `defineExpose`.
- Prefer named slots over boolean layout props; avoid deep `v-if` nesting (extract sub-components); use `key` deliberately.

### Composables

- `use` prefix; file name matches function name. A composable uses Vue reactivity APIs — keep it pure and side-effect-minimal.
- Return refs/reactive objects; never raw values that lose reactivity. Accept optional reactive args; handle own cleanup with `onUnmounted`.
- Use `toValue()` (Vue 3.3+) to normalise `Ref | ComputedRef | plain value`. Surface loading/error states for async ops.

### Pinia

- One store per domain concern; **Setup Store** syntax (`defineStore('id', () => {...})`). Keep state minimal.
- Actions are the sole point of mutation; `storeToRefs` to destructure without losing reactivity; `$patch` for bulk updates.
- SSR: with `@pinia/nuxt`, state serializes/rehydrates automatically — don't pass it via `useNuxtApp().payload`; don't access stores outside `setup()` on the server.

### SSR / SSG and Nuxt specifics

- Always reason about **where code runs**: server only, client only, or both.
- `useAsyncData` / `useFetch` for server-side data fetching (dedup + hydration handled). Wrap client-only code in `if (import.meta.client)` / `onMounted` / `<ClientOnly>`.
- Never access browser globals (`window`, `document`, `localStorage`) at module level or in server-running composables.
- `useRuntimeConfig()` for env config; `useRoute()`/`useRouter()` over globals; `definePageMeta` for per-page layout/middleware/rendering. Be explicit about `routeRules` for hybrid rendering.
- Nitro handlers stay thin — validate input, call a service, return data.

### Testing

- **Component tests** (Vitest + Vue Test Utils) for UI behaviour; **unit tests** for pure composables and Pinia stores.
- `mountSuspended` from `@nuxt/test-utils` for Nuxt-aware component tests; mock `useFetch`/`useAsyncData` at the Nuxt layer, not the network.
- Test stores in isolation with `createPinia()` + `setActivePinia`; `flushPromises()` before asserting async; avoid snapshot tests beyond trivial static markup.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **Reactivity** — `ref` vs `reactive` misuse; destructuring reactive objects without `toRefs`; `watch` vs `watchEffect` chosen deliberately; watchers/effects that outlive the component scope (leaks).
- **SSR / hydration** — synchronous watchers with side effects, or browser-global access on the server → hydration mismatches; client-only code not guarded.
- **State** — mutation outside a Pinia action; monolithic global stores; reactivity lost through bad destructuring.
- **Performance** — imprecise `computed` dependencies; missing `v-memo`/`defineAsyncComponent` on heavy renders; bundle not code-split at the route level (`nuxi analyze`).
- **Standards** — `<script setup lang="ts">`, typed props/emits, `scoped` styles, no `any`. Run eslint/`vue-tsc` rather than eyeballing.

---

## security

Consumed by `/gm:security`.

- **XSS via `v-html`** — flag any `v-html` on non-trusted content; prefer text interpolation or a sanitizer.
- **SSR state exposure** — secrets or private data leaking into the serialized SSR payload / `useState`.
- **Dependencies** — `npm audit` (or the lockfile-matching runner) on the JS tree; separate prod from dev/build-time deps.
- **Runtime config** — no secrets in `public` runtime config (it ships to the client); server-only secrets stay in the private config.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

### Where custom code lives (what we detail)

- The whole app repo is custom. Nuxt: `components/`, `composables/`, `stores/` (Pinia), `pages/`, `layouts/`, `middleware/`, `plugins/`, `server/` (Nitro API/routes), `utils/`. Plain Vue: `src/**`.
- Build/orchestration (`nuxt.config.ts`, `vite.config.*`, `Makefile`, CI) → **containers** (C2).

**Never detailed (black box, `type: external`)**: `node_modules/**` — Vue/Nuxt runtime, UI libraries, any dependency. Show only what custom code imports/calls (an external API, the Nitro runtime, a headless CMS, a DB) as `external` nodes.

### Wiring source of truth

1. **Pinia stores** (`defineStore`) — each store = a `component` node; store→store and composable→store calls = `uses` edges.
2. **Composables** (`useX`) — `component` nodes; a component/composable importing another = `uses` edge.
3. **Nitro server routes/handlers** (`server/api/**`, `server/routes/**`) — entry points; external calls (DB, upstream API) = `external` nodes.
4. **Router / pages** (`pages/**`, route config) — entry points; navigation guards/middleware as `component` nodes.
5. **Component import graph** — parent→child and component→composable `uses` edges; stop at `node_modules`.

Typical containers (C2): the Nuxt/Vite app, the Nitro server, and any external API/CMS/DB the app talks to.

### C4 splitting (code level, on request)

Group by feature/domain into `CODE.views` (e.g. `stores`, `composables`, one feature module). Vue has no classes/interfaces: represent composables/stores as `class`-kind nodes and shared TS contracts (interfaces/types) as `interface`-kind nodes, linked by `assoc`/`use`.
