---
name: drupal
description: Guides Drupal 10/11 backend development with architecture, services, plugins, events, entities, cache, and security. Use when implementing or reviewing Drupal backend code, services, event subscribers, plugins, entity/configuration logic, or when the user asks for Drupal backend expertise.
---

# Drupal 11 Backend Expert

Entry point for Drupal backend development. This skill carries the **workflow**; the Drupal knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Drupal knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/drupal/MAIN.md` for the **dev** nature — it pulls `core.md` (coding standard, `t()`, Cache API vocabulary) + `dev.md` (architecture, entities, cacheability, config, security, testing, service decoration, plugins, event subscribers). Apply that discipline throughout the work below.

**`dev` is the baseline, not always enough.** `MAIN.md` is the dispatcher — it lists every nature the stack exposes (`core` / `dev` / `review` / `security`). When the ticket reaches beyond plain feature work, also load the nature(s) it implicates so you build with the right lens from the start, not after the fact:

- Ticket with **security / access / data-exposure** implications (auth, permissions, private data, file handling, an SA to patch) → also load `${CLAUDE_SKILL_DIR}/../../stack/drupal/security.md`.
- Working against **existing code you must not regress**, or a change where cacheability/standards are the crux → also load `${CLAUDE_SKILL_DIR}/../../stack/drupal/review.md` to keep its checklist in mind while coding (the formal pass still runs later via `/gm:review`).

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

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket` to get the brief, then **survey** the existing custom modules (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is domain logic, infrastructure, or presentation.
3. **Propose** the cleanest architectural approach (apply the `dev.md` architecture/decoration/plugin/event guidance).
4. **Then** provide implementation.
5. **Mention** potential edge cases.
6. **Mention** performance considerations (cacheability metadata, N+1, batch/queue).
7. **Mention** tests to add or update, and how to run them (via the runner — see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
