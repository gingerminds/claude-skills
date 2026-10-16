---
name: quality
description: Applies the Gingerminds quality checklist (Checklist Qualité GM — security, SEO, RGPD, accessibility, eco-design/webperf, quality, internal process) to any AI-driven dev task. Mode `context` (default) primes the session with the rules that apply to the task before code is written, filtered by stack (Laravel, Drupal, Nuxt/Vue, WordPress) and front/back scope; mode `check` audits the current diff against them through the `quality-checker` subagent; mode `sync` refreshes the versioned snapshot from Notion. Use at the start of any implementation, bug fix or refactor in a client project, before committing, or when the user invokes /gm:quality.
---

# Checklist Qualité GM

Every dev task at Gingerminds must respect the **Checklist Qualité GM**. This skill turns it into working context: the applicable rules are known *before* coding, and the diff is checked against them *before* commit. The rules live in one versioned snapshot — `${CLAUDE_SKILL_DIR}/../../shared/quality-checklist.md` — never restate them from memory.

Pick the mode from the argument or the situation:

| Mode | When | Output |
|---|---|---|
| `context` (default) | Start of a dev task, right after `/gm:ticket` or when the intent is known | Short list of applicable rules, kept in context |
| `check` | Change done, before commit — also called by `/gm:review` | Conformity report on the diff |
| `sync` | A maintainer wants to refresh the snapshot from Notion | Updated `shared/quality-checklist.md` |

Already ran `context` in this session for the same task? Don't redo it — reuse what's in context.

## Mode `context`

1. **Stack** — apply `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md` (reuse the detection if `/gm:ticket` already did it). Map it to the checklist techno with the snapshot's « Correspondance stack → techno » table.
2. **Scope** — from the task intent (ticket brief, user request) decide the section: `F` (templates, components, CSS/JS, pages), `B` (controllers, services, models, API, config), or both. Note the functional areas involved: forms, auth/roles, uploads, API, i18n, images/media, SEO pages (listing, pagination, search), payment, cookies/tracking.
3. **Filter** — read the snapshot and keep the rules whose Technos match (or `*`) **and** whose Sect. matches **and** that the task can actually affect. Relevance is the filter that matters: a CSS tweak doesn't pull SEC-20 (hash algorithms); a new public form pulls SEC-09, SEC-15, RGPD-04/05, A11Y-08/22/24, QUAL-14/27, QUAL-29.
4. **Output** — a compact block, in French, kept in context for the rest of the session. Bloquants first, `code`/`config` rules as constraints to build against, `recette` rules listed separately as things to test by hand. Aim for 5-15 rules; never dump the whole checklist.

```markdown
### Checklist Qualité GM — règles applicables
Stack : <laravel → L> · Périmètre : <F | B | FB> · <zones : formulaire public, upload…>

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
2. Reuse the `context` block if present; otherwise compute it (steps 1-3 above) from the diff itself.
3. Spawn the **`quality-checker`** subagent (exposed as `gm:quality-checker`) with a prompt containing:
   - the absolute checklist path: `${CLAUDE_SKILL_DIR}/../../shared/quality-checklist.md`
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
