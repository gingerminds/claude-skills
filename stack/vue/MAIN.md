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
npx nuxt upgrade                             # bump the nuxt package + resolve peer versions (or: npm install nuxt@^4.0.0)
npx codemod@0.18.7 nuxt/4/migration-recipe   # apply the full set of automated codemods
```

Ask before running the codemods on a dirty tree — they rewrite files across the project. The recipe bundles these individual codemods (offer to cherry-pick if the user wants a narrower pass):

- `nuxt/4/file-structure` — moves `components/`, `pages/`, `layouts/`, etc. into `app/`.
- `nuxt/4/default-data-error-value` — adapts code relying on the old `useAsyncData`/`useFetch` default values.
- `nuxt/4/deprecated-dedupe-value` — updates `refresh`/`dedupe` parameter usage.
- `nuxt/4/shallow-function-reactivity` — adapts code that relied on deep reactivity of fetched data.
- `nuxt/4/template-compilation-changes` — migrates EJS-style template usage.
- `nuxt/4/absolute-watch-path` — adapts `builder:watch` hooks to absolute paths.

After running them, diff the result (`git diff --stat`) and read through the non-trivial changes — codemods are mechanical and can mis-transform edge cases.

### Check & update the module ecosystem

`nuxt upgrade` only bumps the `nuxt` package itself — every module/plugin the project depends on needs its own pass, and this is where migrations often get stuck silently:

- **Node version**: confirm the runtime satisfies Nuxt 4's minimum (the official upgrade guide gives no number: read `engines` in the installed `nuxt` package after `nuxt upgrade`, and the Node requirement of each module). Bump `.nvmrc`/`package.json` `engines.node` and the CI/Docker base image together — not just the local machine.
- **Inventory every module**: read `modules` in `nuxt.config.ts` and cross-reference with `package.json` (`@nuxtjs/*`, `@pinia/nuxt`, `@nuxt/image`, `@vueuse/nuxt`, UI kits, etc.).
- **Check Nuxt 4 compatibility per module**: `npm view <package> peerDependencies` (or the module's changelog/GitHub releases) to find the minimum version that declares `"nuxt": "^4"` support. A module still pinned to a Nuxt-3-only peer range will either fail to install or silently misbehave at runtime.
- **Bump each module individually**, not with a blind `npm update` — a wide bump drags unrelated majors along and makes a failure hard to attribute. `npm install <module>@<compat-version>` one at a time, build/test between each.
- **Read the module's own changelog for its bump** — a module's jump to its Nuxt-4-compatible release often carries *its own* breaking changes, independent of anything in the Nuxt core guide below (e.g. a major `@nuxtjs/i18n` or `@pinia/nuxt` bump). Treat these exactly like the Nuxt core breaking changes: find where the project uses the old API and adjust the code — don't just bump the version number and move on.
- **TypeScript / Vite**: if the project pins `typescript` or `vite` directly as a devDependency (rather than relying on Nuxt's bundled version), check their versions against the peer ranges of the installed `nuxt` package (`npm ls typescript vite`) and bump if needed.
- **Swiper**: `grep -E '"(swiper|vue-awesome-swiper)"' package.json` — if either is present, run **`/gm:swiper-migration`** before moving on. It audits every slider for a legacy wrapper/API (`vue-awesome-swiper`, the old single `options` prop, the `v-swiper` directive) and migrates it to the modern `swiper/vue` components. Don't attempt this inline here — it's its own multi-step audit with its own output format.
- **Regenerate and commit the lockfile** after the round of bumps — don't leave it stale relative to `package.json`.

### Audit each breaking-change area

Go through these in order; for each, state **found / not applicable / already compliant** for this project, with the concrete files affected — don't just restate the guide. The catalog follows the official [Nuxt 4 upgrade guide](https://nuxt.com/docs/4.x/getting-started/upgrade) ("Migrating to Nuxt 4"); re-read it if Nuxt has shipped a newer 4.x guide, and flag any v4 change in it that is missing below.

#### 1. Directory structure (impact: significant)
Nuxt 4's default `srcDir` is `app/`. Existing `assets/`, `components/`, `composables/`, `layouts/`, `middleware/`, `pages/`, `plugins/`, `utils/`, `app.vue`, `error.vue`, `app.config.ts` move under `app/`; `nuxt.config.ts`, `server/`, `public/`, `modules/`, `layers/`, `content/` and the new `shared/` (code shared between the Vue app and Nitro, auto-imports for `shared/utils/` and `shared/types/`) stay at the root. The `~` alias now points to `app/`. Nuxt auto-detects the old layout, so moving is not required, but it is the recommended end state (IDE type-safety, faster FS watching). Check:
- `nuxt.config.ts` for a custom `srcDir`/`dir.*` override: with a custom `srcDir`, `modules/`, `public/`, `shared/` and `server/` now resolve from `rootDir`, so `dir.modules`, `dir.public` or `serverDir` may need setting. `serverDir` now defaults to `<rootDir>/server`.
- Third-party config (`tailwindcss`, `eslint`, test runners) with hard-coded paths to the old layout.
- To keep the v3 layout deliberately: `srcDir: '.'` plus `dir: { app: 'app' }` (the latter is where `router.options.ts` and `spa-loading-template.html` are looked up).

#### 2. `useAsyncData` / `useFetch` behaviour (impact: moderate to medium — check carefully, silent bugs here)
- **Shared refs per key** (moderate): calls with the same key share `data`/`error`/`status`. Calls with an explicit key must not have conflicting `deep`, `transform`, `pick`, `getCachedData` or `default` (Nuxt warns) — extract them into one composable.
- **`getCachedData`** is now called on every fetch, including watcher and `refreshNuxtData` triggers, and receives a third `ctx` argument (`ctx.cause`: `'initial' | 'refresh:hook' | 'refresh:manual' | 'watch'`). A custom implementation ignoring it may serve stale cache on manual refresh.
- **Data cleanup**: data is removed once the last consuming component unmounts — code relying on it persisting needs `useState` or an explicit cache.
- **Reactive keys** (`ref`/`computed`/getter) are supported — an opportunity, not a break, where a manual `watch` + re-fetch could simplify.
- **Shallow reactivity** (minimal): `data` is a `shallowRef`. Code mutating nested fields of `data.value` and expecting reactivity needs `deep: true` per call (or replace the whole object). Codemod: `nuxt/4/shallow-function-reactivity`.
- **`pending` alignment** (medium): `pending` is now computed and `true` only while `status` is `'pending'`; with `immediate: false` it stays `false` until the first request. Template logic like `v-if="!pending"` as a "loaded" check should use `status === 'success'`.
- **Key change behaviour** (medium): with `immediate: false`, a key/option change only refetches if a first fetch already happened (`useFetch` now behaves like `useAsyncData`). A non-immediate fetch must be triggered once with `execute()`/`refresh()`.
- **Defaults** (minimal): `data` and `error` default to `undefined`, not `null` — update `=== null` checks. Codemod: `nuxt/4/default-data-error-value`.
- **`dedupe`** (minimal): boolean values for `refresh({ dedupe })` are removed (`true` → `'cancel'`, `false` → `'defer'`). Codemod: `nuxt/4/deprecated-dedupe-value`.
- **Clearing**: `clear`/`clearNuxtData` reset `data` to a custom `default` value instead of unsetting it.
- Opt-outs while migrating: `experimental.granularCachedData: false` + `purgeCachedData: false`, `experimental.pendingWhenIdle: true`, `experimental.alwaysRunFetchOnKeyChange: true`, `experimental.defaults.useAsyncData.deep: true` (not recommended).

#### 3. Normalized component names (impact: moderate)
Vue now generates component names matching Nuxt's auto-import pattern (`components/SomeFolder/MyComponent.vue` is `SomeFolderMyComponent`, no longer `MyComponent`). Grep for `<KeepAlive include/exclude>` filters and `findComponent({ name })` / `findComponent('Name')` in tests that rely on the old name. Opt-out: `experimental.normalizeComponentNames: false`.

#### 4. More granular inline styles (impact: moderate)
Only Vue component CSS is inlined now, not global CSS (global CSS stays as `<link>`). Check for CSP rules, critical-CSS tooling or visual-regression/perf baselines that assumed inlined global CSS. Restore with `features: { inlineStyles: true }`.

#### 5. Shared prerender data (impact: medium, only with prerendering/`nuxt generate`)
`useAsyncData`/`useFetch` data is now shared across prerendered pages. Every key must resolve to the same data: a `useAsyncData` in a dynamic page (`[slug].vue`) whose key does not include the slug is now a bug (wrong data across pages). Disable with `experimental.sharedPrerenderData: false`.

#### 6. Directory index scanning in `app/middleware/` (impact: medium, only with nested middleware folders)
`index` files in child folders of `app/middleware/` are now registered as middleware (consistent with `app/plugins/`). Grep for `middleware/**/index.*` files that were not meant to be middleware; filter them with an `app:resolve` hook if needed.

#### 7. Module loading order in layers (impact: minimal, only with `extends`/`layers/`)
Layer modules now load first (deeper layers first) and project modules last, so the project can override layers (previously reversed). Check modules that depend on a specific order, or work around the old one; use the `modules:done` hook when a module needs to register a hook after all others. Applies to both `modules` in `nuxt.config.ts` and auto-discovered `modules/`.

#### 8. Route and page metadata (impact: minimal)
- `route.meta.name`, `route.meta.path` etc. from `definePageMeta` are only on the route now: use `route.name`.
- Page meta is scanned **after** `pages:extend` (`experimental.scanPageMeta` enabled): move metadata overrides from the `pages:extend` hook to `pages:resolved`. Opt-out: `experimental.scanPageMeta: true`.

#### 9. Unhead v2 (impact: minimal)
Grep `useHead`/`useSeoMeta` calls for the removed props `vmid`, `hid`, `children`, `body`, and for promise input (no longer supported). Tags are sorted with Capo.js by default. Template Params and Alias Tag Sorting must be opted into explicitly (`TemplateParamsPlugin`, `AliasSortingPlugin` from `@unhead/vue/plugins`, registered via `injectHead().use(...)` in a plugin). Prefer importing from `#imports` / `nuxt/app` rather than `@unhead/vue`. Opt-out: `unhead: { legacy: true }`.

#### 10. SPA loading screen location (impact: minimal, only with `ssr: false` + `spa-loading-template.html`)
The template now renders next to the app root (`<div id="__nuxt"></div>` then the template) instead of inside it. Update CSS/`document.querySelector` selectors targeting it, or use `app.spaLoaderTag`/`app.spaLoaderAttrs`; revert with `experimental.spaLoadingTemplateLocation: 'within'`.

#### 11. Parsed `error.data` (impact: minimal)
`error.data` is now parsed into an object. Remove manual `JSON.parse(error.data)` in a custom `error.vue`.

#### 12. `window.__NUXT__` removed (impact: minimal, only if referenced directly)
The global is removed after hydration. Grep for `window.__NUXT__` / direct payload access in app code or third-party integrations (analytics snippets, debug tooling) — replace with `useNuxtApp().payload`.

#### 13. Removed config: `generate` and experimental flags (impact: minimal)
- Top-level `generate` is gone: `generate.exclude` → `nitro.prerender.ignore`, `generate.routes` → `nitro.prerender.routes`.
- `experimental.treeshakeClientOnly` (now `true`), `configSchema` (`true`), `polyfillVueUseHead` (`false`) and `respectNoSSRHeader` (`false`) are no longer configurable; `vite.devBundler` is gone (uses `vite-node`). Remove them from `nuxt.config.ts`; the last two behaviours can be reimplemented in user land (plugin / server middleware) if needed.

#### 14. TypeScript configuration (impact: minimal, mostly opt-in)
- `compilerOptions.noUncheckedIndexedAccess` now defaults to `true`: run a typecheck and fix new errors (recommended), or override via `typescript.tsConfig`.
- Nuxt now generates split configs (`.nuxt/tsconfig.app.json`, `.server.json`, `.node.json`, `.shared.json`, plus the legacy `.nuxt/tsconfig.json`). Existing projects extending `.nuxt/tsconfig.json` keep working. To opt in to project references: replace the root `tsconfig.json` content (remove the `extends`, they are mutually exclusive) with `"files": []` + `references` to the four files, delete manual `server/tsconfig.json` files, switch the script to `vue-tsc -b --noEmit`, and move type augmentations into `app/`, `server/` or `shared/` (augmentations outside them stop working). Customize via `typescript.tsConfig`, `sharedTsConfig`, `nodeTsConfig` and `nitro.typescript.tsConfig`. Update CI typecheck scripts accordingly.

#### 15. Local module authors (impact: minimal, only for modules in `modules/` or published by the project)
- Template compilation: `lodash/template` (EJS `.ejs` sources in `addTemplate`) and the `serialize`/`importName`/`importSources` utilities are removed — use `getContents()`, `es-toolkit/compat` or `knitwork`. Codemod: `nuxt/4/template-compilation-changes`.
- `builder:watch` now emits **absolute** paths: normalize with `relative(nuxt.options.srcDir, resolve(nuxt.options.srcDir, path))`. Codemod: `nuxt/4/absolute-watch-path`.

### `future.compatibilityVersion` (opt-in, don't force it)

`nuxt.config.ts` → `future: { compatibilityVersion: 5 }` (Nuxt 4.2+) previews Nuxt 5 behaviour. Per the official guide ("Testing Nuxt 5"), on Nuxt 4 it enables, among others: the Vite Environment API (`experimental.viteEnvironmentApi`; `extendViteConfig(..., { server: false })` then needs a `addVitePlugin` with `configEnvironment`/`applyToEnvironment`), case-sensitive routing (`/About` no longer matches `pages/about.vue`), normalized page component names (`<KeepAlive>` filters), `clearNuxtState` resetting to the `useState` initial value (`{ reset: false }` to keep the old behaviour), serializable page meta extraction, client payload extraction, `navigateTo` early return, no auto-import of `useServerHead*`/`useServerSeoMeta`, PostCSS `autoprefixer`/`cssnano` off by default, typed pages and typed `$fetch`, comment-node placeholders for client-only components, Nitro auto-imports off, Options API compiled out, and a stricter generated `tsconfig.json`. Nuxt 5 also brings changes no flag enables (e.g. Nitro v3). None of this belongs to the v3 → v4 audit: only propose it if the user explicitly wants to get ahead of Nuxt 5, as a separate, later step. Related: from Nuxt 4.6, server code can import handlers from `nuxt/server` to run on both Nuxt 4 (h3 v1) and Nuxt 5 (h3 v2) — optional, also out of scope for the v4 pass.

### Build, test, iterate

After each meaningful chunk, not just at the end:

```bash
<runner> nuxt build   # or dev, per project convention — resolve <runner> via shared/runner.md
<runner> <test command>
```

Fix as you go rather than batching every area then debugging one giant failure. The `useAsyncData`/`useFetch` changes are the likeliest source of silent runtime bugs (shared refs, shallow reactivity, `pending` semantics) — these won't always fail the build, so exercise the affected pages manually or via existing tests, don't just check for a green build.

### Output format

Lead with a one-line status: how many breaking-change areas apply to this project and the single riskiest one. Then:

**Findings table** — one row per area from the audit above:

| Area | Applies? | Files affected | Risk |
|---|---|---|---|
| Module ecosystem | Yes | `@nuxtjs/i18n` 8→9, `@pinia/nuxt` bump | Medium (module's own breaking changes) |
| Directory structure | Yes | root-level `components/`, `pages/`, … | Low (mechanical) |
| useAsyncData/useFetch | Yes | `composables/useUser.ts`, 3 pages | Medium (shared refs) |
| Unhead v2 | No | — | — |

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
