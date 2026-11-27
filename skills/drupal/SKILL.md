---
name: drupal
description: Guides Drupal 10/11 backend development with architecture, services, plugins, events, entities, cache, and security. Use when implementing or reviewing Drupal backend code, services, event subscribers, plugins, entity/configuration logic, or when the user asks for Drupal backend expertise.
---

# Drupal 11 Backend Expert

Entry point for Drupal backend development. This skill carries the **workflow**; the Drupal knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Drupal knowledge

Load `${CLAUDE_PLUGIN_ROOT}/stack/drupal/MAIN.md` for the **dev** nature — it pulls `core.md` (coding standard, `t()`, Cache API vocabulary) + `dev.md` (architecture, entities, cacheability, config, security, testing, service decoration, plugins, event subscribers). Apply that discipline throughout the work below.

**`dev` is the baseline, not always enough.** `MAIN.md` is the dispatcher — it lists every nature the stack exposes (`dev` / `review` / `security` / `archi`, on top of the always-loaded `core`). When the ticket reaches beyond plain feature work, also load the nature(s) it implicates so you build with the right lens from the start, not after the fact:

- Ticket with **security / access / data-exposure** implications (auth, permissions, private data, file handling, an SA to patch) → also load `${CLAUDE_PLUGIN_ROOT}/stack/drupal/security.md`.
- Working against **existing code you must not regress**, or a change where cacheability/standards are the crux → also load `${CLAUDE_PLUGIN_ROOT}/stack/drupal/review.md` to keep its checklist in mind while coding (the formal pass still runs later via `/gm:review`).

Let the ticket brief's **Contraintes / risques** drive this: each concern there maps to a nature. Load what's relevant, skip the rest — don't pull all four by reflex.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's custom modules** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- List the custom modules (typically `web/modules/custom/*`, sometimes `modules/custom/*` or `docroot/...`) and skim each `*.info.yml`, `*.module`, `*.services.yml`, and the `src/` tree.
- Identify reusable **services, plugins, event subscribers, entities, and traits** you could extend or inject rather than rebuild.
- Note the **local idioms** (DI vs procedural, naming, base classes) so new code matches the surrounding style.
- Surface any existing code your change should touch, decorate, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Approach

Classify the problem (domain logic, infrastructure, or presentation) and propose the cleanest architectural approach, applying the `dev.md` architecture/decoration/plugin/event guidance, then implement it. Add or update the tests the change needs and run them (via the runner — see `${CLAUDE_PLUGIN_ROOT}/shared/runner.md`). Raise edge cases and performance risks (cacheability metadata, N+1, batch/queue) only where they bear on this change.

**Touches the rendering** (Twig, Sass/CSS, front JS, images/SVG, email templates, display config)? Follow `${CLAUDE_PLUGIN_ROOT}/shared/visual-evidence.md`: list the affected pages/emails and **capture the "before" screenshots before editing any file**. After implementing, rebuild assets + `drush cr`, then capture the matching "after" screenshots and check the pair actually shows the change.

**Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
