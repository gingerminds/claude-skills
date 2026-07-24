---
name: archi-c4
description: Prepare, generate and maintain interactive C4 architecture documentation (self-contained HTML) under .archi/. Traces only custom code (contrib/core/vendor shown as black boxes); project-level C1/C2 in index.html plus one page per custom unit for C3 (C4 UML on request). Stack-agnostic via shared/stack-detect.md; loads a stack archi resource when the detected stack provides one. All generated output is written in French. Use when the user asks to set up / generate / update an architecture diagram or C4 model, "documenter l'archi", "à quoi ressemble le projet", or invokes /gm:archi-c4.
---

# Interactive C4 architecture (`.archi/`)

Produce living, interactive architecture docs under the target project's `.archi/`, from a frozen template fed only with data. **All generated output is in French** (leads, drawer text, legends, and the CLAUDE.md block written into the project). Dual use: onboarding (`.archi/index.html`) and up-to-date documentation.

## Mental model

- The **chrome** (interactive CSS+JS) is frozen in `${CLAUDE_SKILL_DIR}/assets/template.html`. Never edit it when generating.
- You only write the **data block**: `META` + the present levels (`CONTEXT`, `CONTAINER`, `COMPONENT`, `CODE`). Contract + full example: `${CLAUDE_SKILL_DIR}/assets/model.example.js` — **read it before generating**. Its French strings show the expected output language.
- **Generate a file** = copy `template.html`, then replace the single line just below the `@@ARCHI-C4:MODEL@@` marker —
  `META = null; CONTEXT = null; CONTAINER = null; COMPONENT = null; CODE = null;`
  — with the data block you wrote. The five globals are pre-declared above the marker, so an absent level may be left `null` (or omitted) → its tab auto-hides, no error. Never touch anything above the marker comment.
- **Scope: custom code only.** Any third party (contrib/core/vendor, `node_modules`, packages) is an `external` node (black box), never decomposed.

## Detect the stack

1. Apply `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.
2. Any stack whose `MAIN.md` exposes an **archi** section → load `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md` for the **archi** nature (where custom lives, source of truth, black box) — `MAIN.md` routes to the right content whatever its form. E.g.:
   - Drupal → `stack/drupal/archi.md`.
   - Vue / Nuxt → the `core` + `archi` sections of `stack/vue/MAIN.md`.
3. Stack with no archi resource/section → **generic method**: ask the user where custom code lives and what the wiring source of truth is (entry points + custom→custom imports/instantiations, stop at the third-party boundary). Never `Read` a non-existent stack resource.

## Choose the flow

- `.archi/` **missing** → **Prepare**, then offer to continue with **Build**.
- `.archi/` **present** and the user wants to (re)document → **Build**.
- `.archi/` **present** and drift is suspected → **Maintain**.

## Flow 1 — Prepare

1. Create `.archi/` and `.archi/units/`.
2. `.gitignore`: ensure `.archi/` **stays committed** (remove/neutralise any rule that would exclude it). `.archi/` **must** be versioned.
3. **Deployment exclusion**: detect the tool and propose the exact edit, **apply only after confirmation**:
   - Deployer (`deploy.php`/`deploy.yaml`) → add `.archi` to excluded paths / `--exclude`.
   - `.dockerignore` → a `.archi/` line.
   - GitLab CI rsync (`.gitlab-ci.yml`) → `--exclude='.archi'` in the sync command.
   - Capistrano (`Capfile`/`config/deploy.rb`) → exclude from the release.
   - No tool detected → say so, ask which one, or document and move on.
4. **Project CLAUDE.md** (root, create if absent): insert this block **idempotently** (do not duplicate if the tags already exist). The block content is in French — it targets the project team:

   ```markdown
   <!-- gm:archi-c4 -->
   ## Architecture (C4)
   - Pour découvrir ce projet, ouvre `.archi/index.html` (vue C4 interactive).
   - À tout changement d'architecture (nouveau module/service/plugin, refactor de câblage, nouvelle dépendance externe), mets à jour le C4 correspondant dans `.archi/` via `/gm:archi-c4`.
   <!-- /gm:archi-c4 -->
   ```

## Flow 2 — Build

1. **List the custom units to trace first** (from the stack resource or the generic method). **Present the list and get it validated** before writing anything.
2. **Project view — `.archi/index.html`**: generate `META` + `CONTEXT` (C1: the system, its `person` actors, its external systems) + `CONTAINER` (C2: each custom unit as `container`/`component` + shared infra as `store`/`external` — DB, search engine, cron, storage). On each custom container, set `doc: "units/<slug>.html"`.
3. **Per unit — `.archi/units/<slug>.html`**: generate `META` + `COMPONENT` (C3, from the source of truth). `<slug>` = kebab-case unit name.
4. **C4 (UML code)**: do NOT produce by default. Offer it via `AskUser`, warning the user (message in French): « Le niveau C4 (UML par classe) est le plus détaillé mais devient obsolète à chaque refactor et coûte cher à (re)générer. L'ajouter pour cette unité ? ». If yes, add `CODE.views` to the unit (see per-view splitting in the stack resource).
5. **Accurate model, not decorative**: every edge maps to a real dependency (DI argument, `extends`, `use`, call). On a large project, per-unit exploration may be delegated to **subagents** (one unit = one agent) then synthesised.
6. **Stamp**: in `META.footer`, record the source of truth used AND the generation stamp: `généré le <YYYY-MM-DD> · commit <short-hash>` (`git rev-parse --short HEAD`).

## Flow 3 — Maintain

1. Read each doc's stamp under `.archi/**` (`généré le … · commit …`).
2. Compare with custom files changed since that commit:
   `git diff --name-only <commit>..HEAD -- <custom paths>`.
3. If custom units changed **or** the generation is old → **`AskUser` « Faut-il vérifier / mettre à jour le C4 ? »** listing the impacted units.
4. Update = regenerate the data blocks of the affected docs (the template never changes). Re-stamp.

## Non-goals

- Never edit `template.html` when generating.
- Never decompose third-party code (black box only).
- Never produce C4 without explicit consent.
- No multi-unit mono-HTML, no external JSON, no build step.
