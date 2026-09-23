---
name: swiper-migration
description: Audits a Vue/Nuxt project for legacy Swiper integrations (vue-awesome-swiper, the old single `options` prop, the `v-swiper` directive) and migrates every slider to the modern Swiper.js Vue components (`swiper/vue`) — updates the dependency and rewrites each slider's props, module imports, CSS imports, and instance access. Use when the user asks to migrate/update Swiper sliders, or invokes /gm:swiper-migration. Also invoked by /gm:nuxt4-migration when `swiper`/`vue-awesome-swiper` is detected in package.json.
---

# Swiper.js Migration (legacy wrapper → modern swiper/vue)

Entry point for migrating a project's sliders off a legacy Swiper integration. This skill carries the **workflow**; the migration procedure itself lives in the shared stack resource so `/gm:vue`, `/gm:nuxt4-migration` and this skill draw from one source.

## Load the migration knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/vue/MAIN.md` for the **swiper-migration** nature — read its `## core` + `## swiper-migration` sections (detection, what "legacy" looks like, per-slider migration steps, verification, output format, non-goals). Apply `## dev` conventions (extract a composable if the same slider config repeats across pages, type props/emits, etc.) to how each migrated component is structured.

## Review before wrapping up

Once every slider found in the audit is migrated and verified in-browser, run **`/gm:review`** on the full diff before considering the migration done — only then move to `/gm:merge-request` (or back to `/gm:nuxt4-migration` if this was triggered from there).
