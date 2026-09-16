---
name: nuxt4-migration
description: Audits a Nuxt 3 project for Nuxt 4 migration readiness and drives the migration — runs `nuxt upgrade` and the official codemods, checks each breaking-change area (directory structure, useAsyncData/useFetch reactivity, Vite Environment API, jiti/case-sensitive routing, window.__NUXT__), audits and bumps the module ecosystem (Node version, Nuxt modules, TypeScript/Vite) to Nuxt-4-compatible versions, and produces a prioritized migration plan. Use when the user asks to migrate/upgrade a Nuxt project to v4, or invokes /gm:nuxt4-migration.
---

# Nuxt 3 → 4 Migration

Drive migrations from Nuxt 3 to Nuxt 4: audit what actually breaks in *this* project, run the official automated tooling, then work through the remaining breaking changes area by area with a prioritized plan. Real project impact over a generic checklist — read the code, don't just paste the upgrade guide back.

## Load the Vue/Nuxt conventions

This migration touches Vue/Nuxt code directly (composables, components, `useAsyncData`/`useFetch` call sites, Vite config). Before writing any manual fix, load `${CLAUDE_SKILL_DIR}/../../stack/vue/MAIN.md` for the **dev** nature — its `## core` + `## dev` sections (Composition API conventions, composables, SSR/SSG specifics, component design). Apply that discipline to every manual change below — this skill drives the migration *sequence*, `gm:vue`'s stack resource governs *how the code itself should look* once touched.

## Scope first

Confirm the starting point before touching anything:

```bash
grep -E '"nuxt"' package.json
ls app/ 2>/dev/null                 # already partially migrated to the app/ dir?
grep -E 'compatibilityVersion|future' nuxt.config.ts 2>/dev/null
```

- Confirm the project is on Nuxt 3 (or already mid-migration with `app/` partially in place — say so, don't restart from scratch).
- Find the project runner via `${CLAUDE_SKILL_DIR}/../../shared/runner.md` (`make` → `docker compose` → `lando`) — builds/tests should run inside it if one exists.
- Check for a clean git tree (`git status`) before starting — the migration touches many files across the project; the user needs a clean baseline to diff against and revert if something goes wrong.

## Run the official tooling first

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

## Check & update the module ecosystem

`nuxt upgrade` only bumps the `nuxt` package itself — every module/plugin the project depends on needs its own pass, and this is where migrations often get stuck silently:

- **Node version**: confirm the runtime satisfies Nuxt 4's minimum, and Node 22.19+ if relying on native config loading (see `jiti` below). Bump `.nvmrc`/`package.json` `engines.node` and the CI/Docker base image together — not just the local machine.
- **Inventory every module**: read `modules` in `nuxt.config.ts` and cross-reference with `package.json` (`@nuxtjs/*`, `@pinia/nuxt`, `@nuxt/image`, `@vueuse/nuxt`, UI kits, etc.).
- **Check Nuxt 4 compatibility per module**: `npm view <package> peerDependencies` (or the module's changelog/GitHub releases) to find the minimum version that declares `"nuxt": "^4"` support. A module still pinned to a Nuxt-3-only peer range will either fail to install or silently misbehave at runtime.
- **Bump each module individually**, not with a blind `npm update` — a wide bump drags unrelated majors along and makes a failure hard to attribute. `npm install <module>@<compat-version>` one at a time, build/test between each.
- **Read the module's own changelog for its bump** — a module's jump to its Nuxt-4-compatible release often carries *its own* breaking changes, independent of anything in the Nuxt core guide below (e.g. a major `@nuxtjs/i18n` or `@pinia/nuxt` bump). Treat these exactly like the Nuxt core breaking changes: find where the project uses the old API and adjust the code — don't just bump the version number and move on.
- **TypeScript / Vite**: if the project pins `typescript` or `vite` directly as a devDependency (rather than relying on Nuxt's bundled version), check they satisfy Nuxt 4's requirements (Vite 6) and bump if needed.
- **Swiper**: `grep -E '"(swiper|vue-awesome-swiper)"' package.json` — if either is present, run **`/gm:swiper-migration`** before moving on. It audits every slider for a legacy wrapper/API (`vue-awesome-swiper`, the old single `options` prop, the `v-swiper` directive) and migrates it to the modern `swiper/vue` components. Don't attempt this inline here — it's its own multi-step audit with its own output format.
- **Regenerate and commit the lockfile** after the round of bumps — don't leave it stale relative to `package.json`.

## Audit each breaking-change area

Go through these in order; for each, state **found / not applicable / already compliant** for this project, with the concrete files affected — don't just restate the guide.

### 1. Directory structure (impact: significant)
Nuxt 4's default `srcDir` is `app/`. Existing `components/`, `composables/`, `layouts/`, `middleware/`, `pages/`, `plugins/`, `assets/` at the root move under `app/`; `server/` and `public/` stay at the root. Nuxt still auto-detects the old layout for backwards compatibility, but moving explicitly is the recommended end state (better type-safety and IDE performance). Check `nuxt.config.ts` for a custom `srcDir`/`dir.*` override — those need updating too.

### 2. `jiti` no longer bundled by default (impact: medium)
Config files load natively now (requires Node 22.19+). Check:
- Relative imports inside config files (`nuxt.config.ts`, modules) missing an extension (`./build/my-plugin` → `./build/my-plugin.ts`).
- TypeScript `enum` usage in config-loaded code — replace with `as const` objects.
- If the project genuinely needs `jiti`'s runtime transform behaviour, it can be installed explicitly as a devDependency.

### 3. Case-sensitive routing (impact: minimal, but breaking for mismatched links)
`/About` no longer matches `pages/about.vue`. Grep for internal links/`navigateTo`/`<NuxtLink>` targets whose casing doesn't match the file-based route.

### 4. Custom Vite plugins / `extendViteConfig` (impact: medium, only if present)
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

### 5. Remote layers via `giget` (impact: minimal, only if used)
If `extends` in `nuxt.config.ts` points at a remote layer (GitHub/git URL), `giget` must now be installed explicitly, or switch the layer to a normal `devDependency` (`"my-theme": "github:org/repo#sha"`) — simpler and lockfile-pinned.

### 6. `useAsyncData` / `useFetch` behaviour (impact: medium — check carefully, silent bugs here)
- Same key now returns **shared refs** for `data`/`error`/`status` across call sites — code assuming independent instances per call needs review.
- `getCachedData` now receives a `ctx` argument with `ctx.cause` (`'initial' | 'refresh:hook' | 'refresh:manual' | 'watch'`) — a custom `getCachedData` implementation ignoring this may cache/skip incorrectly under the new signature.
- Fetched data is **shallow-reactive** by default now — code mutating nested fields of `data.value` and expecting reactivity needs `data.value = { ...data.value, nested: ... }` or an explicit deep-reactive option.
- Data attached to a key is cleaned up automatically once the last consuming component unmounts — code relying on it persisting after unmount needs `useState` or an explicit cache instead.
- Keys can now be reactive (`ref`/`computed`/getter) — an opportunity, not a break, but worth flagging where a manual `watch` + re-`useFetch` pattern could simplify.

### 7. `window.__NUXT__` removed (impact: minimal, only if referenced directly)
Grep for `window.__NUXT__` / direct payload access in app code or third-party integrations (analytics snippets, debug tooling) — replace with `useNuxtApp().payload`.

### 8. Vue Options API (impact: minimal)
Unaffected unless `future.compatibilityVersion: 5` is adopted (see below) — flag only if the project is going that far.

## `future.compatibilityVersion` (opt-in, don't force it)

`nuxt.config.ts` → `future: { compatibilityVersion: 5 }` previews Nuxt 5 behaviour (automatic Vite Environment API, case-sensitive routing on by default, Options API compiled out by default, typed routes, client-only placeholder comments). Only propose this if the user explicitly wants to get ahead of Nuxt 5 — it stacks additional breaking changes on top of the v4 migration and should be a separate, later step, not bundled into the v3→v4 pass.

## Build, test, iterate

After each meaningful chunk, not just at the end:

```bash
<runner> nuxt build   # or dev, per project convention — resolve <runner> via shared/runner.md
<runner> <test command>
```

Fix as you go rather than batching every area then debugging one giant failure. The `useAsyncData`/`useFetch` changes are the likeliest source of silent runtime bugs (shared refs, shallow reactivity) — these won't always fail the build, so exercise the affected pages manually or via existing tests, don't just check for a green build.

## Output format

Lead with a one-line status: how many breaking-change areas apply to this project and the single riskiest one. Then:

**Findings table** — one row per area from the audit above:

| Area | Applies? | Files affected | Risk |
|---|---|---|---|
| Module ecosystem | Yes | `@nuxtjs/i18n` 8→9, `@pinia/nuxt` bump | Medium (module's own breaking changes) |
| Directory structure | Yes | root-level `components/`, `pages/`, … | Low (mechanical) |
| useAsyncData/useFetch | Yes | `composables/useUser.ts`, 3 pages | Medium (shared refs) |
| Vite plugins | No | — | — |

Then a **prioritized action list**, most-breaking-risk first, each with the concrete fix (codemod already applied vs. manual step still needed). Close with what's left to verify manually (pages to click through, tests to add).

## Review before wrapping up

Once every area is addressed and the build/tests are green, run **`/gm:review`** on the full diff — it re-applies the `stack/vue/MAIN.md` **review** section (ORM/N+1 doesn't apply here, but SSR/hydration pitfalls, reactivity misuse, and standards do) on top of the generic dimensions. Don't consider the migration done until that review has run; only then move to `/gm:merge-request`.

## Non-goals

- Not a Nuxt 5 migration — stop at v4 unless the user explicitly asks to also adopt `future.compatibilityVersion: 5`.
- Don't blind `npm update` the whole tree to get modules onto Nuxt 4 — scope each bump to the module that actually needs it, one at a time.
- Don't run the codemods on a dirty git tree without asking first.
- Don't blindly trust codemod output — diff and read the non-trivial transforms, especially around `useAsyncData`/`useFetch` call sites.
- Don't treat a green build as migration-complete — the `useFetch` reactivity changes are runtime-only failure modes, not build errors.
