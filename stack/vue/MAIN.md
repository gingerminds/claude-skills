# Stack: Vue / Nuxt — specifics

Entry point for Vue/Nuxt specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. If a section grows too large it can be promoted to a sibling file (Form 2) without touching the callers.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:vue` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |
| `/gm:nuxt4-migration` | core + nuxt4-migration |
| `/gm:swiper-migration` | core + swiper-migration |

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

---

## nuxt4-migration

Consumed by `/gm:nuxt4-migration`. Layered on `core`; every manual code change should also follow `## dev` (Composition API conventions, composables, SSR/SSG specifics) — this section drives the migration *sequence*, `## dev` governs how the touched code should look.

Drive migrations from Nuxt 3 to Nuxt 4: audit what actually breaks in *this* project, run the official automated tooling, then work through the remaining breaking changes area by area with a prioritized plan. Real project impact over a generic checklist — read the code, don't just paste the upgrade guide back.

### Scope first

Confirm the starting point before touching anything:

```bash
grep -E '"nuxt"' package.json
ls app/ 2>/dev/null                 # already partially migrated to the app/ dir?
grep -E 'compatibilityVersion|future' nuxt.config.ts 2>/dev/null
```

- Confirm the project is on Nuxt 3 (or already mid-migration with `app/` partially in place — say so, don't restart from scratch).
- Find the project runner via `${CLAUDE_SKILL_DIR}/../../shared/runner.md` (`make` → `docker compose` → `lando`) — builds/tests should run inside it if one exists.
- Check for a clean git tree (`git status`) before starting — the migration touches many files across the project; the user needs a clean baseline to diff against and revert if something goes wrong.

### Run the official tooling first

Two Nuxt-provided tools do the mechanical work — run them before any manual pass:

```bash
npx nuxt upgrade                             # bump the nuxt package + resolve peer versions
npx codemod@0.18.7 nuxt/4/migration-recipe   # apply the full set of automated codemods
```

Ask before running the codemods on a dirty tree — they rewrite files across the project. The recipe bundles these individual codemods (offer to cherry-pick if the user wants a narrower pass):

- `nuxt/4/file-structure` — moves `components/`, `pages/`, `layouts/`, etc. into `app/`.
- `nuxt/4/default-data-error-value` — adapts code relying on the old `useAsyncData`/`useFetch` default values.
- `nuxt/4/deprecated-dedupe-value` — updates `refresh`/`dedupe` parameter usage.
- `nuxt/4/shallow-function-reactivity` — adapts code that relied on deep reactivity of fetched data.
- `nuxt/4/template-compilation-changes` — migrates EJS-style template usage.

After running them, diff the result (`git diff --stat`) and read through the non-trivial changes — codemods are mechanical and can mis-transform edge cases.

### Check & update the module ecosystem

`nuxt upgrade` only bumps the `nuxt` package itself — every module/plugin the project depends on needs its own pass, and this is where migrations often get stuck silently:

- **Node version**: confirm the runtime satisfies Nuxt 4's minimum, and Node 22.19+ if relying on native config loading (see `jiti` below). Bump `.nvmrc`/`package.json` `engines.node` and the CI/Docker base image together — not just the local machine.
- **Inventory every module**: read `modules` in `nuxt.config.ts` and cross-reference with `package.json` (`@nuxtjs/*`, `@pinia/nuxt`, `@nuxt/image`, `@vueuse/nuxt`, UI kits, etc.).
- **Check Nuxt 4 compatibility per module**: `npm view <package> peerDependencies` (or the module's changelog/GitHub releases) to find the minimum version that declares `"nuxt": "^4"` support. A module still pinned to a Nuxt-3-only peer range will either fail to install or silently misbehave at runtime.
- **Bump each module individually**, not with a blind `npm update` — a wide bump drags unrelated majors along and makes a failure hard to attribute. `npm install <module>@<compat-version>` one at a time, build/test between each.
- **Read the module's own changelog for its bump** — a module's jump to its Nuxt-4-compatible release often carries *its own* breaking changes, independent of anything in the Nuxt core guide below (e.g. a major `@nuxtjs/i18n` or `@pinia/nuxt` bump). Treat these exactly like the Nuxt core breaking changes: find where the project uses the old API and adjust the code — don't just bump the version number and move on.
- **TypeScript / Vite**: if the project pins `typescript` or `vite` directly as a devDependency (rather than relying on Nuxt's bundled version), check they satisfy Nuxt 4's requirements (Vite 6) and bump if needed.
- **Swiper**: `grep -E '"(swiper|vue-awesome-swiper)"' package.json` — if either is present, run **`/gm:swiper-migration`** before moving on. It audits every slider for a legacy wrapper/API (`vue-awesome-swiper`, the old single `options` prop, the `v-swiper` directive) and migrates it to the modern `swiper/vue` components. Don't attempt this inline here — it's its own multi-step audit with its own output format.
- **Regenerate and commit the lockfile** after the round of bumps — don't leave it stale relative to `package.json`.

### Audit each breaking-change area

Go through these in order; for each, state **found / not applicable / already compliant** for this project, with the concrete files affected — don't just restate the guide.

#### 1. Directory structure (impact: significant)
Nuxt 4's default `srcDir` is `app/`. Existing `components/`, `composables/`, `layouts/`, `middleware/`, `pages/`, `plugins/`, `assets/` at the root move under `app/`; `server/` and `public/` stay at the root. Nuxt still auto-detects the old layout for backwards compatibility, but moving explicitly is the recommended end state (better type-safety and IDE performance). Check `nuxt.config.ts` for a custom `srcDir`/`dir.*` override — those need updating too.

#### 2. `jiti` no longer bundled by default (impact: medium)
Config files load natively now (requires Node 22.19+). Check:
- Relative imports inside config files (`nuxt.config.ts`, modules) missing an extension (`./build/my-plugin` → `./build/my-plugin.ts`).
- TypeScript `enum` usage in config-loaded code — replace with `as const` objects.
- If the project genuinely needs `jiti`'s runtime transform behaviour, it can be installed explicitly as a devDependency.

#### 3. Case-sensitive routing (impact: minimal, but breaking for mismatched links)
`/About` no longer matches `pages/about.vue`. Grep for internal links/`navigateTo`/`<NuxtLink>` targets whose casing doesn't match the file-based route.

#### 4. Custom Vite plugins / `extendViteConfig` (impact: medium, only if present)
Nuxt 4 adopts Vite 6's Environment API. Any module calling `extendViteConfig(fn, { server: false })` to target client-only needs rewriting to `addVitePlugin` with `configEnvironment`/`applyToEnvironment`:

```ts
// Before
extendViteConfig((config) => {
  config.optimizeDeps.include.push('my-package')
}, { server: false })

// After
addVitePlugin(() => ({
  name: 'my-plugin',
  configEnvironment(name, config) {
    if (name === 'client') config.optimizeDeps?.include?.push('my-package')
  },
  applyToEnvironment: (environment) => environment.name === 'client',
}))
```

Check the project's local modules and `nuxt.config.ts` for `extendViteConfig` usage.

#### 5. Remote layers via `giget` (impact: minimal, only if used)
If `extends` in `nuxt.config.ts` points at a remote layer (GitHub/git URL), `giget` must now be installed explicitly, or switch the layer to a normal `devDependency` (`"my-theme": "github:org/repo#sha"`) — simpler and lockfile-pinned.

#### 6. `useAsyncData` / `useFetch` behaviour (impact: medium — check carefully, silent bugs here)
- Same key now returns **shared refs** for `data`/`error`/`status` across call sites — code assuming independent instances per call needs review.
- `getCachedData` now receives a `ctx` argument with `ctx.cause` (`'initial' | 'refresh:hook' | 'refresh:manual' | 'watch'`) — a custom `getCachedData` implementation ignoring this may cache/skip incorrectly under the new signature.
- Fetched data is **shallow-reactive** by default now — code mutating nested fields of `data.value` and expecting reactivity needs `data.value = { ...data.value, nested: ... }` or an explicit deep-reactive option.
- Data attached to a key is cleaned up automatically once the last consuming component unmounts — code relying on it persisting after unmount needs `useState` or an explicit cache instead.
- Keys can now be reactive (`ref`/`computed`/getter) — an opportunity, not a break, but worth flagging where a manual `watch` + re-`useFetch` pattern could simplify.

#### 7. `window.__NUXT__` removed (impact: minimal, only if referenced directly)
Grep for `window.__NUXT__` / direct payload access in app code or third-party integrations (analytics snippets, debug tooling) — replace with `useNuxtApp().payload`.

#### 8. Vue Options API (impact: minimal)
Unaffected unless `future.compatibilityVersion: 5` is adopted (see below) — flag only if the project is going that far.

### `future.compatibilityVersion` (opt-in, don't force it)

`nuxt.config.ts` → `future: { compatibilityVersion: 5 }` previews Nuxt 5 behaviour (automatic Vite Environment API, case-sensitive routing on by default, Options API compiled out by default, typed routes, client-only placeholder comments). Only propose this if the user explicitly wants to get ahead of Nuxt 5 — it stacks additional breaking changes on top of the v4 migration and should be a separate, later step, not bundled into the v3→v4 pass.

### Build, test, iterate

After each meaningful chunk, not just at the end:

```bash
<runner> nuxt build   # or dev, per project convention — resolve <runner> via shared/runner.md
<runner> <test command>
```

Fix as you go rather than batching every area then debugging one giant failure. The `useAsyncData`/`useFetch` changes are the likeliest source of silent runtime bugs (shared refs, shallow reactivity) — these won't always fail the build, so exercise the affected pages manually or via existing tests, don't just check for a green build.

### Output format

Lead with a one-line status: how many breaking-change areas apply to this project and the single riskiest one. Then:

**Findings table** — one row per area from the audit above:

| Area | Applies? | Files affected | Risk |
|---|---|---|---|
| Module ecosystem | Yes | `@nuxtjs/i18n` 8→9, `@pinia/nuxt` bump | Medium (module's own breaking changes) |
| Directory structure | Yes | root-level `components/`, `pages/`, … | Low (mechanical) |
| useAsyncData/useFetch | Yes | `composables/useUser.ts`, 3 pages | Medium (shared refs) |
| Vite plugins | No | — | — |

Then a **prioritized action list**, most-breaking-risk first, each with the concrete fix (codemod already applied vs. manual step still needed). Close with what's left to verify manually (pages to click through, tests to add).

### Non-goals

- Not a Nuxt 5 migration — stop at v4 unless the user explicitly asks to also adopt `future.compatibilityVersion: 5`.
- Don't blind `npm update` the whole tree to get modules onto Nuxt 4 — scope each bump to the module that actually needs it, one at a time.
- Don't run the codemods on a dirty git tree without asking first.
- Don't blindly trust codemod output — diff and read the non-trivial transforms, especially around `useAsyncData`/`useFetch` call sites.
- Don't treat a green build as migration-complete — the `useFetch` reactivity changes are runtime-only failure modes, not build errors.

---

## swiper-migration

Consumed by `/gm:swiper-migration` — invoked standalone, or automatically by `## nuxt4-migration` when Swiper is detected. Layered on `core`; apply `## dev` conventions (extract a composable if the same slider config repeats across pages, type props/emits, etc.) to how the migrated component is structured.

Migrate every slider in the project off a legacy Swiper integration (`vue-awesome-swiper`, the old single `options` prop, the `v-swiper` directive) onto the modern, officially-maintained `swiper/vue` component API. Real component-by-component audit over a blanket find/replace — options merge differently now, module imports are explicit, and CSS imports are modular.

### Detect first

```bash
grep -E '"(swiper|vue-awesome-swiper)"' package.json
grep -rl "vue-awesome-swiper\|v-swiper\|swiper/vue\|options=\"swiperOption" --include="*.vue" --include="*.ts" --include="*.js" .
```

- No `swiper`/`vue-awesome-swiper` dependency and no slider usage found → say so and stop, nothing to do.
- `swiper/vue` already used everywhere with no legacy wrapper → confirm compliant; still worth checking the installed `swiper` version is current.
- Otherwise, inventory every file using a slider before touching any of them — this list becomes the migration checklist.

### What "legacy" looks like

Grep patterns to find every instance:

- **Import**: `import { swiper, swiperSlide } from 'vue-awesome-swiper'`, or a global `Vue.use(VueAwesomeSwiper)` registration in a plugin file.
- **Single `options` object prop**: `<swiper :options="swiperOptions">` — the old API merged one big options object; the new API takes each Swiper parameter as its own prop.
- **Directive-based**: `v-swiper:mySwiper="swiperOption"` — the directive API is gone entirely.
- **Instance access via `$refs`**: `this.$refs.mySwiper.$swiper` / `.swiper` — replaced by the `@swiper` event or the `useSwiper()`/`useSwiperSlide()` composables.
- **Bundled CSS import**: `import 'swiper/css/swiper.css'` — replaced by modular per-feature imports.

`vue-awesome-swiper` is deprecated upstream; its own v5 is nothing more than a re-export of `swiper/vue` for Vue 3 — so a project already on that version is one import-path change away from being fully migrated. Anything older carries the real API differences above.

### Migrate each slider

1. **Dependency**: remove `vue-awesome-swiper` from `package.json` if present; make sure `swiper` itself is on a current major (`npm view swiper version`) — it ships the Vue components directly, no separate wrapper needed.
2. **Imports**: `import { Swiper, SwiperSlide } from 'swiper/vue'`. Import only the modules actually used, from `swiper/modules` (`Navigation`, `Pagination`, `Autoplay`, `Scrollbar`, `EffectFade`, etc.) and pass them via the `:modules="[...]"` prop — don't rely on a default bundle, nothing is enabled by default anymore.
3. **Props**: convert the old single `options` object into individual props on `<Swiper>` — `slides-per-view`, `space-between`, `navigation`, `pagination`, `autoplay`, `loop`, `breakpoints`, etc. map 1:1 to former `options.*` keys. Translate the project's `swiperOptions` object key by key — don't guess or drop options silently.
4. **Slides**: each slide becomes a `<SwiperSlide>` child; if the old markup produced slide elements dynamically (`v-for` over `swiper-slide` divs), keep the same `v-for` on `<SwiperSlide>`.
5. **Instance access**: replace `$refs.*.swiper` usage with `@swiper="onSwiper"` (capture the instance in a ref/variable) or `useSwiper()` inside a child component. Route any manual method calls (`.slideNext()`, `.update()`, etc.) through the captured instance.
6. **CSS**: replace the bundled `swiper/css/swiper.css` import with `swiper/css` plus one import per used module (`swiper/css/navigation`, `swiper/css/pagination`, …), or `swiper/css/bundle` if the project prefers one import over precision.
7. **Slot-based state**: if the old code inspected slide state (active/prev/next) via manual classes or watchers, prefer the `SwiperSlide` scoped slot props (`isActive`, `isPrev`, `isNext`, `isVisible`) instead.

### Verify

Every migrated slider needs a manual look, not just a build check — a slider with a silently-dropped option (e.g. `loop` or `breakpoints` never translated) still renders and still "builds", it just doesn't behave the same. For each migrated slider:

- Confirm every original `options` key has a corresponding prop set on `<Swiper>` — diff the old options object against the new props one by one.
- Exercise it in the browser/dev server: navigation, pagination, autoplay, and `breakpoints`-driven responsive behaviour are all still there.
- Breakpoint configs specifically are a common silent-drop spot — check them explicitly.

### Output format

One entry per slider found, in the audit list:

```
- <file/component> — legacy pattern: <options prop | v-swiper directive | $refs instance access>
  Modules needed: <Navigation, Pagination, ...> · Options translated: <all | N of M — missing: ...>
  Status: migrated | needs manual check: <why>
```

Close with the dependency changes (`vue-awesome-swiper` removed, `swiper` version) and anything not yet verified in-browser.

### Non-goals

- Not a slider redesign — preserve the existing visual behaviour/options; this is an API migration, not a UX change.
- Don't drop an option silently because its new prop name isn't obvious — look it up in the Swiper API docs rather than guessing.
- Don't leave `vue-awesome-swiper` installed once every usage is migrated — remove the dependency.
