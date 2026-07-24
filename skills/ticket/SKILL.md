---
name: ticket
description: Loads and digests a Mantis ticket (or a pasted ticket) into a focused development brief — the goal, what to look at in the codebase, constraints, and acceptance criteria — so the context is in memory before a stack-specific dev session (Drupal, Vue, Laravel, Python/Django, WordPress…). Detects the project stack to route to the right dev skill. Tuned for a Mantis + GitLab workflow. Use before starting work on a ticketed change, or invoke /gm:ticket.
---

# Ticket

Turn a ticket into a working understanding *before* any code is written. The output is a compact brief that primes the rest of the session: when the stack's dev skill runs next, it already knows the intent, the scope, and what "done" means — instead of rediscovering it from the diff.

## Load the ticket

Accept any of: a Mantis id, a Mantis URL, a branch name (`feature|fix/<id>-slug`, also legacy `feat/` → extract the id), or a ticket pasted inline. With an id:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh <id>    # summary, description, steps-to-reproduce, additional info, notes, attachment list
```

It needs `MANTIS_URL` + `MANTIS_TOKEN` in the env. If they're unset or it exits non-zero (exit 2 = no creds, 3 = API error), **ask the user to paste the ticket** rather than guessing the intent. A pasted ticket is a first-class input — digest it the same way.

## Attachments

The digest ends with an `## Attachments` section when the ticket has files — screenshots, mockups, logs. **Check for it first**: only when at least one attachment is listed do you raise the question (no attachments → say nothing, move on). The digest text can't show the files, so ask before loading — via **`AskUserQuestion`**, not a free-text prompt, so the user answers in one click:

- **Question:** `Charger les pièces jointes ?`
- **Options:**
  - `Oui, charger` — put it first and tag it `(recommandé)` when an attachment looks tied to the spec (the discussion says « voir image en PJ », or the file is a mockup/screenshot of the expected UI or the bug).
  - `Non, ignorer`
  - `En parler` — the user wants to weigh which files matter before loading.

Act on the choice:

- **Oui** — download each relevant file into the scratchpad and read it into context (images especially — a mockup or a screenshot of the bug often *is* the spec). Use the file ids from the list:
  ```bash
  bash ${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh <id> --file <file-id> "$SCRATCHPAD/<filename>"
  ```
  Then `Read` each downloaded file and fold what it shows into the brief (e.g. the expected UI, the error in the screenshot). Prefer the images and anything the discussion refers to; skip large binaries with no bearing on the change.
- **Non** — note in the brief that attachments exist but weren't loaded, and carry on.
- **En parler** — list what's attached (names, types) and help the user pick, then download the chosen ones.

For a **pasted** ticket, ask the same way — the user can paste or drop the images directly.

## Detect the stack

Before framing the brief, identify the project's stack so the hand-off points at the right dev skill and the brief uses the right vocabulary. Apply `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`: **actually read** the root manifests (`composer.json`, `package.json`, `pyproject.toml`/`requirements*.txt`, `manage.py`) — don't guess from the repo name. This stays lightweight — no code survey (that's the dev skill's job).

Resolve the dev skill from that file's **Dev skill routing** table (drupal→`/gm:drupal`, vue→`/gm:vue`, laravel→`/gm:laravel`, django→`/gm:django`, python→`/gm:python`, wordpress→`/gm:wordpress`). Note the detected stack and the resolved skill — the brief and the hand-off use them. If detection is ambiguous (monorepo, multiple stacks), note the candidates and let the "À regarder" reflect each; if the stack is generic `php`/`js` or has no dedicated skill yet, say so and plan a generic dev hand-off.

## Understand it

Read the whole ticket, including the notes (they often carry the real decision or a scope change). Extract:

- **Goal** — what the ticket actually asks for, in one or two sentences. For a bug, the wrong behavior; for a feature, the outcome the user wants.
- **Root cause (if a regression)** — what's suspected, and whether the ticket points at a symptom or the cause. Note it as a hypothesis, not a conclusion.
- **Scope** — which functional area / custom code (modules, apps, packages, plugins/theme, or components, per the detected stack) this likely touches. Stay lightweight here — the deep read of the existing code is the dev skill's "Survey the existing custom code first" step; your job is to point it at the right place.
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

- Point the user to the resolved **`/gm:<stack>`** dev skill (from the "Detect the stack" step) to do the work — it will survey the existing custom code against the "À regarder" list, then implement. If the stack has no dedicated dev skill yet (generic `php`/`js`, or a stack we don't cover), say so and hand off to generic dev discipline instead.
- Make the brief's **Contraintes / risques** explicit and specific: the dev skill uses them to decide *which* stack knowledge to load. A ticket flagged security/access, cacheability, or migration is not plain feature work — say so, so the dev skill pulls the matching nature (e.g. its `security`/`review` resource) on top of `dev`, instead of coding it as a vanilla feature.
- Remind that the loop ends with **`/gm:review`** before opening a merge request (then `/gm:merge-request`).

## Non-goals

- Don't design the solution or write code here — that's the stack's dev skill. Keep to intent and scope.
- Don't deep-dive the codebase — surface the pistes; the survey is the dev skill's job.
- Don't review intent blind: if the ticket is unreachable and not pasted, stop and ask.
