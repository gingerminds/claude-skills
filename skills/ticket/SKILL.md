---
name: ticket
description: Loads and digests a Mantis ticket (or a pasted ticket) into a focused development brief — the goal, what to look at in the codebase, constraints, and acceptance criteria — so the context is in memory before a /clara:drupal-11 session. Tuned for Drupal/PHP + Mantis + GitLab. Use before starting work on a ticketed change, or invoke /clara:ticket.
---

# Clara — Ticket

Turn a ticket into a working understanding *before* any code is written. The output is a compact brief that primes the rest of the session: when `/clara:drupal-11` runs next, it already knows the intent, the scope, and what "done" means — instead of rediscovering it from the diff.

## Load the ticket

Accept any of: a Mantis id, a Mantis URL, a branch name (`fix|feat/<id>-slug` → extract the id), or a ticket pasted inline. With an id:

```bash
scripts/mantis-issue.sh <id>    # summary, description, steps-to-reproduce, additional info, notes
```

It needs `MANTIS_URL` + `MANTIS_TOKEN` in the env. If they're unset or it exits non-zero (exit 2 = no creds, 3 = API error), **ask the user to paste the ticket** rather than guessing the intent. A pasted ticket is a first-class input — digest it the same way.

## Understand it

Read the whole ticket, including the notes (they often carry the real decision or a scope change). Extract:

- **Goal** — what the ticket actually asks for, in one or two sentences. For a bug, the wrong behavior; for a feature, the outcome the user wants.
- **Root cause (if a regression)** — what's suspected, and whether the ticket points at a symptom or the cause. Note it as a hypothesis, not a conclusion.
- **Scope** — which functional area / custom module(s) this likely touches. Stay lightweight here — the deep read of the existing code is `/clara:drupal-11`'s "Survey the existing custom code first" step; your job is to point it at the right place.
- **Reproduction / context** — the steps to reproduce, the environment, the data conditions.
- **Acceptance criteria** — how anyone will know it's fixed. If the ticket doesn't state them, derive the obvious ones and mark them as inferred.
- **Constraints & risks** — multilingual, cacheability, access/security, data migration, anything that narrows the solution space.
- **Open questions** — genuine ambiguities that should be resolved before coding. Ask the user the blocking ones now; leave the rest as flagged assumptions.

Don't pad. If a field doesn't apply, drop it.

## Output — the development brief

Produce one compact brief and keep it in context for the rest of the session:

```markdown
## Ticket #<id> — <summary>

**Goal:** <one or two lines>
**Type:** bug | feature | refactor    **Status:** <mantis status>

**À regarder** (pistes, à confirmer par la lecture du code)
- <module/zone> — <pourquoi>

**Critères d'acceptation**
- [ ] <critère> <(inféré) si dérivé>

**Contraintes / risques:** <multilingue, cache, accès, migration… ou "aucune notable">
**Questions ouvertes:** <bloquantes posées maintenant — sinon "aucune">
```

State the brief, resolve any blocking open question with the user, then hand off.

## Hand off

This skill stops at understanding — it writes **no code**. Once the brief is set:

- Point the user to **`/clara:drupal-11`** to do the work — it will survey the existing custom modules against the "À regarder" list, then implement.
- Remind that the loop ends with **`/clara:review`** before opening a merge request (then `/clara:merge-request`).

## Non-goals

- Don't design the solution or write code here — that's `/clara:drupal-11`. Keep to intent and scope.
- Don't deep-dive the codebase — surface the pistes; the survey is the dev skill's job.
- Don't review intent blind: if the ticket is unreachable and not pasted, stop and ask.
