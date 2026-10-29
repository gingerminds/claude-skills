---
name: ticket-refine
description: Rewrites a Mantis ticket into a private internal note for devs and project managers — plain-language goal, out of scope, open questions ready to forward to the client, how to test, reproduction, testable acceptance criteria — with a readiness verdict (prêt / prêt avec hypothèses / pas prêt), saved to .gm/handoff/<id>/refine.md and posted as a private Mantis note. Use to qualify a ticket before assigning or developing it, or when asked to « reformuler / qualifier ce ticket ». Not for preparing a dev session: use /gm:ticket.
argument-hint: "<mantis-id> [--publish|--dry-run]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh *), Bash(${CLAUDE_SKILL_DIR}/../../scripts/mantis-note.sh *)
---

# Ticket refine

## Critical rules

1. **The note is always private.** Publish only through `mantis-note.sh`, which forces `view_state: private`. Never post a public note, by any other means.
2. **Never invent reproduction steps.** For a bug whose steps the ticket does not give, write `Non reproductible en l'état : <ce qui manque pour reproduire>`. For a ticket that is not a bug (request, configuration, feature), there is nothing to reproduce: write `Sans objet (demande / évolution) : <comment observer l'état actuel, si utile>`, never `Non reproductible en l'état`.
3. **No code pointers.** The note says *what* and *how to verify*, never *where*: no file, module, class, function or route handler. Do not survey the codebase for this skill.
4. **Output in French, following `${CLAUDE_SKILL_DIR}/../../shared/ticket-format.md` exactly.** These instructions are in English; the note and every message to the user are in French. Read that file before writing the note.

Format points that `ticket-format.md` leaves implicit — apply them strictly:

- The note is the template body **without** any surrounding code fence and **without** any preamble. The marker line `[gm:ticket-refine] Note de qualification du <JJ/MM/AAAA>` is the very first line of `refine.md` and of the posted note; the `Verdict :` line is the second. Consumers parse these two lines.
- The verdict line is exactly one of: `Verdict : prêt`, `Verdict : prêt avec hypothèses`, `Verdict : pas prêt — <ce qui manque>`.
- Annotations in `<…>` are guidance, never output. Where the template offers alternatives with `|`, write only one of them.
- When the ticket is not a bug, drop the whole OBSERVÉ / ATTENDU section: the heading, its lines, and the `<section présente uniquement pour un bug>` annotation.
- In REPRODUCTION, environment URLs and role names are allowed; credentials (logins, passwords, tokens) never are, even if the ticket contains them; code pointers never are.
- An empty list is written as the bare placeholder, without a number or dash: `(aucune)`, `(rien à signaler)` — not `1. (aucune)`.
- Every acceptance criterion is checkable by observing the product: a page, a behaviour, a log, a test result. Answering or informing the client is never a criterion: it goes to QUESTIONS OUVERTES or the project-manager part.

## Arguments and mode

`/gm:ticket-refine <mantis-id | Mantis URL | branch name> [--publish | --dry-run]`

- `--dry-run`: build the note and the payload, publish nothing. Wins if both flags are given.
- `--publish`: publish without asking.
- Neither: show the note and ask before publishing.

**Agent mode** = `AskUserQuestion` is totally absent from your tools (you run inside a subagent where that tool is removed). A tool that is deferred or loadable on demand (listed by name only, to be fetched through `ToolSearch` or similar) counts as **available**: load it and use it — that is manual mode. **When in doubt, behave as manual mode** (ask before publishing): a missed agent-mode detection costs a question, a false one publishes without consent.

In agent mode, never ask anything: apply the documented default at every decision point below, and keep the reply to 10 lines at most.

## 1. Load the ticket

Accept the same inputs as `/gm:ticket`:

- a Mantis id (`12345`, `#12345`);
- a Mantis URL (`…/view.php?id=12345`);
- a branch name `feature/<id>-slug` or `fix/<id>-slug` (legacy `feat/<id>-slug` tolerated) → extract the id;
- a ticket pasted inline.

Normalize the id without leading zeros (`0012345` → `12345`); use this form everywhere (paths, script calls, output line). Then call the script directly (not through `bash …`, so it matches `allowed-tools`):

```bash
${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh <id>    # summary, description, steps to reproduce, additional info, notes, attachment list
```

If it exits non-zero (2 = missing `MANTIS_URL`/`MANTIS_TOKEN` or bad usage, 3 = HTTP/API error):

- **manual use** — ask the user to paste the ticket (description, notes, attachments) and continue with that; never guess the content;
- **agent mode** — stop. Do not create `refine.md`. Reply with exactly `Erreur : ticket <id> inaccessible (<code>)`, where `<code>` is the exit code.

A **pasted** ticket is a first-class input. If it carries no Mantis id:

- **manual use** — ask for it (the id names the handoff folder and is required to publish); if the user has none, use `sans-id`;
- **agent mode** — do not ask; use `sans-id` directly.

With `sans-id`, write to `.gm/handoff/sans-id/refine.md` and **never call `mantis-note.sh`**, not even with `--dry-run` (it rejects non-numeric ids). Skip step 3; the status is `non publiée` whatever the flags.

### Attachments

If the digest ends with an `## Attachments` section, **download and read the attachments by default** — screenshots, mockups and logs often are the spec or the proof of the bug. Download into the session scratchpad directory, as `/gm:ticket` does:

```bash
${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh <id> --file <file-id> "<scratchpad>/<filename>"
```

Then `Read` each file and use what it shows (observed behaviour, expected UI, error message, URL in a screenshot).

Ask only about attachments you judge probably useless (large archives, binaries unrelated to the request, duplicates):

- **manual use** — ask via `AskUserQuestion` whether to load those files, listing them;
- **agent mode** — do not ask and do not load them; list them in the reply as `Pièces jointes non chargées : <noms>`.

A download that fails does not stop the skill: mention the file as not loaded and carry on.

## 2. Write the note

Read the whole ticket: description, steps to reproduce, additional information, **every note**, and the attachments. Notes often carry the real decision, a scope change, or the answer to an earlier question; a previous `[gm:ticket-refine]` note is context to update, not a source of truth.

Decide whether the ticket is a **bug**: it describes a current wrong behaviour (an error, or an observed vs expected difference). Only a bug gets the OBSERVÉ / ATTENDU section. Do not ask the user about this; decide from the ticket.

Then fill the template of `shared/ticket-format.md`, applying its verdict and writing rules and the format points in **Critical rules**:

- **Date**: today, `JJ/MM/AAAA`.
- **Open questions** are for the client, not for the user running the skill: write them in QUESTIONS OUVERTES, worded to be forwarded as-is. Do not ask them in the session.
- **Acceptance criteria** are observable in the product (page, behaviour, log, test result); those derived by you are marked `(inféré)`. A wish to inform or answer the client is an open question, not a criterion.
- **Reproduction**: only what the ticket and attachments state.
  - Bug with missing steps → `Non reproductible en l'état : <ce qui manque>`; that missing information usually becomes an open question.
  - Not a bug → `Sans objet (demande / évolution) : <comment observer l'état actuel, si utile>` (e.g. the URL where the current state is visible). Never `Non reproductible en l'état` for a non-bug.
- **Verdict**: from the rules in `ticket-format.md`. For `pas prêt`, say in a few words what is missing.

### Consistency pass

Before saving, reread the whole note and check that scope agrees across OBJECTIF, HORS PÉRIMÈTRE, QUESTIONS OUVERTES and CRITÈRES D'ACCEPTATION:

- an item (site, page, block type, environment, use case) is either in scope (OBJECTIF, CRITÈRES) or out of scope (HORS PÉRIMÈTRE), never both;
- an unresolved scope doubt goes to QUESTIONS OUVERTES **only**: do not assert it in OBJECTIF nor exclude it in HORS PÉRIMÈTRE "(à confirmer)"; state the scope that is certain and ask about the rest;
- every criterion covers something the OBJECTIF includes, and COMMENT RECETTER checks the same things as the criteria, in plain words;
- the verdict still matches the rules after these corrections.

### Previous qualification note

In the digest each note is printed as `- [<reporter>] <text>`, so a previous qualification note shows up as a line containing `[gm:ticket-refine] Note de qualification du <JJ/MM/AAAA>` right after that prefix. If there is at least one, add as third line of the new note:

```
Remplace la note de qualification du <JJ/MM/AAAA>.
```

with the date taken from the most recent such marker.

### Save `refine.md`

Write the note to `<root>/.gm/handoff/<id>/refine.md`, where `<root>` is the root of the current git repository (`git rev-parse --show-toplevel`), with the `Write` tool so the text never goes through the shell. Its content is the note and nothing else.

Inside a git repository, make sure `.gm/` is excluded from git without touching the project's `.gitignore`:

```bash
exclude="$(git rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$exclude")"
grep -qxF '.gm/' "$exclude" || echo '.gm/' >> "$exclude"
```

Outside a git repository, `<root>` is the current directory, with no exclusion.

## 3. Publish

Never call `mantis-note.sh` without `--dry-run` until one of the publish conditions below holds. With `sans-id` (pasted ticket without a Mantis id), skip this step entirely: status `non publiée`.

- **`--dry-run`** — run `${CLAUDE_SKILL_DIR}/../../scripts/mantis-note.sh <id> <root>/.gm/handoff/<id>/refine.md --dry-run` and show the JSON it prints, so the user can check `view_state.name = private`. Nothing is sent. Status: `dry-run`.
- **`--publish`, or agent mode** — publish directly: `${CLAUDE_SKILL_DIR}/../../scripts/mantis-note.sh <id> <root>/.gm/handoff/<id>/refine.md`. A `pas prêt` verdict is published too: its questions are the point of the note.
- **Otherwise** — show the note as it will be posted, then ask via `AskUserQuestion`:
  - **Question:** `Publier cette note privée sur le ticket #<id> ?`
  - **Options:** `Publier`, `Modifier d'abord`, `Ne pas publier`.

  Act on the answer:

  - `Publier` → publish as above.
  - `Modifier d'abord` → ask in free text (a plain message, not `AskUserQuestion`) what to change; apply it to `refine.md` (keeping the format and the first-two-lines contract, rerunning the consistency pass and re-evaluating the verdict if the content changed); show the updated note; then ask again with the same three options. Repeat until `Publier` or `Ne pas publier`.
  - `Ne pas publier` → keep the file. Status: `non publiée`.

Publication result:

- **exit 0** — stdout is `Posted private note <note-id> on issue <id>`. Status: `publiée (#<note-id>)`.
- **exit 2 or 3** — keep `refine.md`, show the script's error message, do not retry. Status: `non publiée`.

## 4. Reply

Briefly in French: anything the user must know (attachments not loaded, failed publication message, blocking questions in short). In agent mode, 10 lines at most. Then end with this exact line, nothing after it:

```
Verdict : <prêt | prêt avec hypothèses | pas prêt — ce qui manque> — note <publiée (#<note-id>) | non publiée | dry-run> — .gm/handoff/<id>/refine.md
```

The verdict part repeats the note's second line after `Verdict : `. Examples:

```
Verdict : prêt avec hypothèses — note publiée (#48211) — .gm/handoff/12345/refine.md
Verdict : pas prêt — URL de la page concernée et rôle du compte — note dry-run — .gm/handoff/12345/refine.md
```

## Non-goals

- No code and no code leads: locating and changing the code is the job of `/gm:ticket`, then the stack's dev skill.
- No change to the ticket's status, assignment, priority or any other field: the only write is the private note.
- Never a public note.
