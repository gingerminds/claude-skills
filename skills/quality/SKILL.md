---
name: quality
description: Applies the Gingerminds quality checklist (Checklist Qualité GM — security, SEO, RGPD, accessibility, eco-design/webperf, quality, internal process) to any AI-driven dev task. Rules are filtered by a versioned project profile (.gm/quality-profile.md — e.g. an extranet drops SEO), built on first run from code signals plus a short questionnaire (mode `profile`). Mode `context` (default) primes the session with the rules that apply to the task before code is written, filtered by profile, stack (Laravel, Drupal, Nuxt/Vue, WordPress) and front/back scope; mode `check` audits the current diff against them through the `quality-checker` subagent; mode `sync` refreshes the versioned snapshot from Notion. Use at the start of any implementation, bug fix or refactor in a client project, before committing, or when the user invokes /gm:quality.
---

# Checklist Qualité GM

Every dev task at Gingerminds must respect the **Checklist Qualité GM**. This skill turns it into working context: the applicable rules are known *before* coding, and the diff is checked against them *before* commit. The rules live in one versioned snapshot — `${CLAUDE_SKILL_DIR}/../../shared/quality-checklist.md` — never restate them from memory.

Pick the mode from the argument or the situation:

| Mode | When | Output |
|---|---|---|
| `context` (default) | Start of a dev task, right after `/gm:ticket` or when the intent is known | Short list of applicable rules, kept in context |
| `check` | Change done, before commit — also called by `/gm:review` | Conformity report on the diff |
| `profile` | First run on a project (no profile yet), or the user wants to review/change the project's needs | `.gm/quality-profile.md` in the project |
| `sync` | A maintainer wants to refresh the snapshot from Notion | Updated `shared/quality-checklist.md` |

Already ran `context` in this session for the same task? Don't redo it — reuse what's in context.

## Project profile

The checklist only means something against the project's actual needs: an extranet has no SEO, a site without upload doesn't need SEC-01. Those needs live in **`.gm/quality-profile.md`** at the project root, versioned with the code so the whole team and every agent share them. Its keys and the rules each one gates are defined in the snapshot's « Besoins projet » section.

Every mode except `sync` starts by reading it:

- **Present** → load it; it overrides anything you would infer. Don't re-ask what it already answers.
- **Absent** → in `context` / `profile`, build it now (below) before filtering. In `check`, don't stop the review: evaluate without need-based filtering, say « profil qualité absent — toutes les règles de la stack évaluées », and suggest `/gm:quality profile`.
- **Contradicted by the task** (a ticket adds an API, a payment, a second language to a project whose profile says `non`) → point it out and propose updating the profile; don't silently apply rules the profile excludes, nor silently drop them.

## Mode `profile`

Build (or revise) the profile in two steps: infer, then confirm.

1. **Infer from the code** — cheap, targeted reads only (manifests, config, routes; no full survey). Note each signal, it becomes the proposal's justification:
   - `multilingue` — `@nuxtjs/i18n` with several locales, Laravel `lang/` with several locales, Drupal `language`/`content_translation` enabled in `config/sync/core.extension.yml`, WP Polylang/WPML.
   - `api` — `routes/api.php`, Drupal `jsonapi`/`rest` enabled, custom WP REST routes, Nuxt `server/api/`.
   - `comptes` — Laravel Breeze/Jetstream/Fortify/Sanctum, front login routes, Drupal user registration open, WP front login / WooCommerce accounts.
   - `ecommerce` — `drupal/commerce`, WooCommerce, Stripe/PayPlug/Monetico/Lyra SDKs.
   - `uploads` — file/image fields, `Storage::put`, `<input type="file">`, media library.
   - `tracking` — GTM/GA/Matomo/pixel snippets, a CMP package.
   - `multisite` — Drupal `sites/*/settings.php` beyond `default`, WP `MULTISITE`, a multi-domain config.
   - `seo` / `public` — a global `noindex`, a login wall on every route or an `extranet`/`intranet` name in README → hint `seo: non`, `public: non`. A sitemap module/package → hint `seo: oui`.
   - `starter` — README or git history mentioning a GM starter.
2. **Confirm with the user** — one `AskUserQuestion` call, at most 4 questions, labels in French. Mark what you detected with « (détecté) » in the option label and put the signal in its description, so the user mostly confirms:
   - **Type de projet** (single choice): `Site public (vitrine / éditorial)` · `E-commerce` · `Extranet / intranet (authentifié)` · `API / back seul`. It sets the defaults: public & e-commerce → `public`, `seo` yes; e-commerce → `ecommerce`, `comptes`, `formulaires`; extranet → `public`, `seo` no, `comptes` yes; API seule → `public`, `seo`, `accessibilite` no, `api` yes.
   - **Audience** (multi): `Indexation SEO` · `Multilingue` · `Multisite` · `Accessibilité`.
   - **Fonctionnalités** (multi): `Formulaires` · `Comptes utilisateurs` · `Upload de fichiers` · `API exposée`.
   - **Autres** (multi): `Back-office` · `Tracking / cookies tiers` · `Listings (recherche, pagination)` · `Médias audio / vidéo`.
   `starter` is not asked — keep the inferred value (default `non`). An answer typed in « Other » (e.g. « SEO mais pas sur l'espace client ») goes in the matching key's justification.
3. **Write `.gm/quality-profile.md`** (French), then show it and tell the user to commit it with the project. Never commit it yourself.

```markdown
# Profil qualité GM — <nom du projet>

Type : <site public | e-commerce | extranet / intranet | API / back seul>
Stack : <laravel (L) + vue (N)>
Mis à jour : <AAAA-MM-JJ>

## Besoins
- seo: non — extranet derrière authentification
- public: non
- multilingue: non
- multisite: non
- accessibilite: oui
- formulaires: oui — formulaires internes de commande
- comptes: oui
- uploads: oui — pièces jointes aux commandes
- api: oui — routes/api.php
- backoffice: oui
- ecommerce: non
- tracking: non
- listings: oui
- medias: non
- starter: non

## Exclusions manuelles
<!-- Règles écartées hors besoins, avec la raison. Ex. : -->
- ECO-14 — pas de .htaccess (nginx)

## Inclusions forcées
<!-- Règles conservées alors qu'un besoin les écarte. Ex. : -->
- SEO-15 — canonical demandé par le client même sur l'extranet
```

Revising an existing profile: show the current values, ask only about what changed, update the date.

## Mode `context`

1. **Profile** — read `.gm/quality-profile.md`, or build it first (« Mode `profile` ») if it's absent.
2. **Stack** — apply `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md` (reuse the detection if `/gm:ticket` or the profile already did it). Map it to the checklist techno with the snapshot's « Correspondance stack → techno » table.
3. **Scope** — from the task intent (ticket brief, user request) decide the section: `F` (templates, components, CSS/JS, pages), `B` (controllers, services, models, API, config), or both. Note the functional areas involved: forms, auth/roles, uploads, API, i18n, images/media, SEO pages (listing, pagination, search), payment, cookies/tracking.
4. **Filter** — read the snapshot and keep the rules whose Technos match (or `*`), whose Sect. matches, whose « Besoins projet » condition holds for the profile (minus its manual exclusions, plus its forced inclusions), **and** that the task can actually affect. The profile removes what never applies to this project; relevance removes what this task can't touch: a CSS tweak doesn't pull SEC-20 (hash algorithms); a new public form pulls SEC-09, SEC-15, RGPD-04/05, A11Y-08/22/24, QUAL-14/27, QUAL-29.
5. **Output** — a compact block, in French, kept in context for the rest of the session. Bloquants first, `code`/`config` rules as constraints to build against, `recette` rules listed separately as things to test by hand. Aim for 5-15 rules; never dump the whole checklist.

```markdown
### Checklist Qualité GM — règles applicables
Stack : <laravel → L> · Périmètre : <F | B | FB> · Profil : <extranet — sans SEO, comptes, uploads> · <zones : formulaire, upload…>

**À respecter dans le code**
- 🔴 SEC-11 — XSS : échapper toute donnée injectée dans le HTML (<où dans cette tâche>)
- 🔴 QUAL-27 — textes fixes via `__()` / `t()`
- · A11Y-08 — label explicite sur chaque champ

**À vérifier en recette** (non vérifiable depuis le code)
- 🔴 QUAL-29 — tester l'envoi du formulaire et la réception de l'email
```

Tie each rule to the concrete spot in this task when you can (« le champ `cv` du formulaire candidature »), not just the generic wording. Then let the dev work proceed — this mode writes no code.

## Mode `check`

Run the audit in an **isolated context** so the checklist and the file reads don't flood the main session:

1. Determine the diff, same order as `/gm:review` (MR / named diff / branch vs base / working tree). Collect the base ref and the list of changed files.
2. Reuse the `context` block if present; otherwise compute it (steps 1-4 above) from the diff itself — with the profile if it exists (see « Project profile » for the absent case).
3. Spawn the **`quality-checker`** subagent (exposed as `gm:quality-checker`) with a prompt containing:
   - the absolute checklist path: `${CLAUDE_SKILL_DIR}/../../shared/quality-checklist.md`
   - the absolute path of `.gm/quality-profile.md`, or « profil absent »,
   - the stack/techno letter and section,
   - the diff command to run (e.g. `git diff develop...HEAD`) and the changed files,
   - the IDs of the applicable rules (from the `context` block) — the agent may add others it finds relevant in the diff.
   If subagents are unavailable, run the same procedure inline (see `agents/quality-checker.md` at the plugin root for it).
4. Relay the agent's report as is (it's already in the expected format). When called from `/gm:review`, hand the findings back to it instead — review maps them to its severities.

Never mark a `recette` rule as satisfied, and never mark a rule ✅ without having read the code that satisfies it.

## Mode `sync` (maintainers)

Refresh the snapshot from Notion. Needs the Notion MCP connected; if it isn't, say so and stop.

1. Query the data source `collection://198af1a3-d8e4-81fb-a01c-000bb7f846b7` (read-only SQL): `Catégorie`, `Sous-catégorie`, `Section`, `Technos concernées`, `Mise en ligne`, `Tâche`. Paginate (`LIMIT`/`OFFSET`) until `has_more` is false; skip empty rows.
2. Diff against the current snapshot, matching rules **by wording** (tolerate small rewordings):
   - existing rule → keep its ID, update its fields;
   - new rule → next free ID in its category, classify its `Vérif` (`code` / `config` / `recette`) and list it as « à valider »;
   - rule removed from Notion → drop it, don't reuse its ID.
   Category `SEO GINGERMINDS` goes under SEO.
3. Rewrite `shared/quality-checklist.md` (same layout), update the « Snapshot du » date and the rule count.
4. Report: added / modified / removed rules, and the new `Vérif` classifications to validate. Then remind the maintainer of the plugin release steps (README, version bump in `marketplace.json` + `plugin.json`, `/gm:review`, MR).

The Notion page's « A ajouter / débattre en réu dev » section is **not** part of the checklist — ignore it until the rules are added to the database.

## Non-goals

- Don't replace `/gm:review` or `/gm:security`: this skill covers the GM checklist; correctness, tests and dependency CVEs stay theirs.
- Don't update the project's Notion checklist (statuses, assignees) — the team does that.
- Don't flag pre-existing violations the change didn't introduce as failures — list them as « existant, hors périmètre ».
