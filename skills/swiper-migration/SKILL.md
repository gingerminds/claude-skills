---
name: swiper-migration
description: Audits a Vue/Nuxt project for legacy Swiper integrations (vue-awesome-swiper, the old single `options` prop, the `v-swiper` directive) and migrates every slider to the modern Swiper.js Vue components (`swiper/vue`) — updates the dependency and rewrites each slider's props, module imports, CSS imports, and instance access. Use when the user asks to migrate/update Swiper sliders, or invokes /gm:swiper-migration. Also invoked by /gm:nuxt4-migration when `swiper`/`vue-awesome-swiper` is detected in package.json.
---

# Swiper.js Migration (legacy wrapper → modern swiper/vue)

Migrate every slider in the project off a legacy Swiper integration (`vue-awesome-swiper`, the old single `options` prop, the `v-swiper` directive) onto the modern, officially-maintained `swiper/vue` component API. Real component-by-component audit over a blanket find/replace — options merge differently now, module imports are explicit, and CSS imports are modular.

## Detect first

```bash
grep -E '"(swiper|vue-awesome-swiper)"' package.json
grep -rl "vue-awesome-swiper\|v-swiper\|swiper/vue\|options=\"swiperOption" --include="*.vue" --include="*.ts" --include="*.js" .
```

- No `swiper`/`vue-awesome-swiper` dependency and no slider usage found → say so and stop, nothing to do.
- `swiper/vue` already used everywhere with no legacy wrapper → confirm compliant; still worth checking the installed `swiper` version is current.
- Otherwise, inventory every file using a slider before touching any of them — this list becomes the migration checklist.

## What "legacy" looks like

Grep patterns to find every instance:

- **Import**: `import { swiper, swiperSlide } from 'vue-awesome-swiper'`, or a global `Vue.use(VueAwesomeSwiper)` registration in a plugin file.
- **Single `options` object prop**: `<swiper :options="swiperOptions">` — the old API merged one big options object; the new API takes each Swiper parameter as its own prop.
- **Directive-based**: `v-swiper:mySwiper="swiperOption"` — the directive API is gone entirely.
- **Instance access via `$refs`**: `this.$refs.mySwiper.$swiper` / `.swiper` — replaced by the `@swiper` event or the `useSwiper()`/`useSwiperSlide()` composables.
- **Bundled CSS import**: `import 'swiper/css/swiper.css'` — replaced by modular per-feature imports.

`vue-awesome-swiper` is deprecated upstream; its own v5 is nothing more than a re-export of `swiper/vue` for Vue 3 — so a project already on that version is one import-path change away from being fully migrated. Anything older carries the real API differences above.

## Migrate each slider

1. **Dependency**: remove `vue-awesome-swiper` from `package.json` if present; make sure `swiper` itself is on a current major (`npm view swiper version`) — it ships the Vue components directly, no separate wrapper needed.
2. **Imports**: `import { Swiper, SwiperSlide } from 'swiper/vue'`. Import only the modules actually used, from `swiper/modules` (`Navigation`, `Pagination`, `Autoplay`, `Scrollbar`, `EffectFade`, etc.) and pass them via the `:modules="[...]"` prop — don't rely on a default bundle, nothing is enabled by default anymore.
3. **Props**: convert the old single `options` object into individual props on `<Swiper>` — `slides-per-view`, `space-between`, `navigation`, `pagination`, `autoplay`, `loop`, `breakpoints`, etc. map 1:1 to former `options.*` keys. Translate the project's `swiperOptions` object key by key — don't guess or drop options silently.
4. **Slides**: each slide becomes a `<SwiperSlide>` child; if the old markup produced slide elements dynamically (`v-for` over `swiper-slide` divs), keep the same `v-for` on `<SwiperSlide>`.
5. **Instance access**: replace `$refs.*.swiper` usage with `@swiper="onSwiper"` (capture the instance in a ref/variable) or `useSwiper()` inside a child component. Route any manual method calls (`.slideNext()`, `.update()`, etc.) through the captured instance.
6. **CSS**: replace the bundled `swiper/css/swiper.css` import with `swiper/css` plus one import per used module (`swiper/css/navigation`, `swiper/css/pagination`, …), or `swiper/css/bundle` if the project prefers one import over precision.
7. **Slot-based state**: if the old code inspected slide state (active/prev/next) via manual classes or watchers, prefer the `SwiperSlide` scoped slot props (`isActive`, `isPrev`, `isNext`, `isVisible`) instead.

Apply `gm:vue`'s conventions (`${CLAUDE_SKILL_DIR}/../../stack/vue/MAIN.md`, `## dev` section) to how the migrated component is structured — extract a composable if the same slider config repeats across pages, type props/emits, etc.

## Verify

Every migrated slider needs a manual look, not just a build check — a slider with a silently-dropped option (e.g. `loop` or `breakpoints` never translated) still renders and still "builds", it just doesn't behave the same. For each migrated slider:

- Confirm every original `options` key has a corresponding prop set on `<Swiper>` — diff the old options object against the new props one by one.
- Exercise it in the browser/dev server: navigation, pagination, autoplay, and `breakpoints`-driven responsive behaviour are all still there.
- Breakpoint configs specifically are a common silent-drop spot — check them explicitly.

## Output format

One entry per slider found, in the audit list:

```
- <file/component> — legacy pattern: <options prop | v-swiper directive | $refs instance access>
  Modules needed: <Navigation, Pagination, ...> · Options translated: <all | N of M — missing: ...>
  Status: migrated | needs manual check: <why>
```

Close with the dependency changes (`vue-awesome-swiper` removed, `swiper` version) and anything not yet verified in-browser.

## Non-goals

- Not a slider redesign — preserve the existing visual behaviour/options; this is an API migration, not a UX change.
- Don't drop an option silently because its new prop name isn't obvious — look it up in the Swiper API docs rather than guessing.
- Don't leave `vue-awesome-swiper` installed once every usage is migrated — remove the dependency.
