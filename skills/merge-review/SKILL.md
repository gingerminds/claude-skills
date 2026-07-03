---
name: merge-review
description: Reviews an existing GitLab merge request end-to-end — content/correctness, form, language, and coding standards — loads the linked Mantis ticket, and drafts a structured review note plus an approve / request-changes recommendation. Drafts only by default; posts the note and approves only on explicit user go-ahead. Tuned for Drupal/PHP + GitLab. Use when the user gives an MR (URL or number) to review as a reviewer, or invokes /gm:merge-review.
---

# Clara — Merge Review

Reviewer-side review of someone's merge request. Produce a review another human could post as-is: a verdict, a per-axis table, and concrete comments. **Outward-facing** — draft everything by default; post the note and approve **only** on explicit user go-ahead, never on your own initiative.

## Load the MR

From the URL or number, pull everything with `glab` (the `-R` and host come from `https://<host>/<group/project>/-/merge_requests/<id>`):

```bash
GITLAB_HOST=<host> glab mr view <id> -R <group/project>      # title, description, author, branches, approvals
GITLAB_HOST=<host> glab mr diff <id> -R <group/project>      # the diff under review
GITLAB_HOST=<host> glab api projects/<id_or_path>/merge_requests/<id>/commits   # commit list (for form/langue checks)
```

Read the changed lines **and** enough surrounding code to judge them. Never review a hunk in isolation.

## Load the ticket

Judge content against intent. Extract the Mantis id from the MR description or the branch name (`fix/<id>-slug`), then:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh <id>    # summary, description, steps-to-reproduce, notes
```

**Read the whole ticket, not just the opening request** — the initial description *and* every note/exchange. The real intent often drifts in the discussion: a note narrows the scope, corrects the expected behaviour, drops a requirement, or settles a question raised mid-thread. Judge **Contenu** against the *latest agreed* intent across all notes, not the first description. If notes contradict each other, flag the ambiguity in the review rather than guessing.

Needs `MANTIS_URL` + `MANTIS_TOKEN` in the env. If unset or it exits non-zero (2 = no creds, 3 = API error), ask the user to paste the ticket rather than reviewing intent blind — and ask for the notes too, not only the description.

If the digest lists an `## Attachments` section, **ask the user before loading them** ("Il y a des pièces jointes, dois-je les charger ?"). A mockup or a before/after screenshot is often the real acceptance criterion — download the relevant ones and read them in:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh <id> --file <file-id> "$SCRATCHPAD/<filename>"
```

## Review axes

Apply the **`gm:review` dimensions** — Correctness, Security, Performance & cacheability, Standards, Tests — the **Blocker/Major/Minor/Nit** severity scale, and that skill's **Noise control** rule (Nits folded, Minor one-liners, full Why/Action for Blocker/Major only). Run the project's linters when present (via `lando`/`ddev`/`docker compose exec`). Below are the axes specific to reviewing someone else's MR:

- **Contenu** — Does the diff deliver what the ticket asks, end to end? Root cause vs symptom. This is `gm:review` Correctness, judged against the loaded ticket.
- **Forme** — Commit & MR hygiene:
  - **Atomic commits** — one logical change per commit; flag mixed-concern commits.
  - **Conventional commits** — `type(scope: #ticket): summary`.
  - **Ticket number required in every commit.** If missing, the author can add the Mantis ref as an MR comment; with no Mantis attached at all it's tolerated but should be rare — flag it either way.
  - MR title convention, real ticket link, description complete (Contexte / Cause racine / Correctif / Vérification), branch `fix|feat/<id>-slug`, staging scoped (no stray files).
- **Langue** — Two registers, never mixed:
  - **French** — the MR title/description and your review note (team language).
  - **English** — all code: identifiers, code comments, docblocks, and (Drupal) user-facing strings through `t()`. Non-English code or comments → raise a comment tagged **« à revoir »** at **Minor** — *non-blocking for the verdict*; the "threads must be closed before merge" rule guarantees it gets fixed.
  - Typos / grammar in either register → comment.
- **Standards** — `gm:review` Standards (match the file's local idiom and the `Drupal,DrupalPractice` standard; run `phpcs` / `php -l` rather than eyeballing), **plus the house rule: every function is typed (params + return) and carries a docblock (HEREDOC)** — flag any function missing either.

## Verdict

Mechanical, from the highest severity found:

- Any **Blocker/Major** → **request-changes**.
- Only **Minor/Nit**, or none → **approve** (note nits in the comment).

The real merge gate here is **all threads resolved**, not the approval — every finding you raise as a thread blocks merge until it's closed. So `request-changes` vs `approve` is mostly about whether *you* add an approval: withhold it on real Contenu/Sécurité/Standards defects; language/style remarks tagged **« à revoir »** are Minor and don't withhold approval on their own (their thread still has to be closed).

Never recommend approve on anything you couldn't verify — say what's unverified and default to request-changes.

## Draft the review

Two outputs, both **in the MR's language** (French here):

**1. The global note** — one synthesis comment: verdict, per-axis table, verification status, UAT checklist. No per-finding list here (each finding becomes its own thread). Keep the table to the axes that are ⚠️/❌; fold the clean ones into one line (`✅ Forme, Langue, Tests`).

```markdown
## Revue — <approve | demande de modifs>

| Axe | Verdict |
|---|---|
| <uniquement les axes ⚠️/❌> | ⚠️ / ❌ + une ligne |

✅ <axes OK regroupés sur une ligne>

Détail des points en threads ci-dessous (chaque thread = à résoudre avant merge).

### Vérifié
- ✅ <check empirique qui a tourné> / ⚠️ <ce qui n'a pas pu être vérifié>

### UAT manuelle
- [ ] <étape exacte>
```

**2. One thread per finding** — so each opens a resolvable discussion (the team gate is *all threads closed*, so every thread is effectively blocking-until-resolved). Each thread body:

```markdown
**[Blocker|Major|Minor|Nit|à revoir]** <symptôme>
<pourquoi, et le correctif exact — copiable, jamais « investiguer X »>
```

Anchor the thread to a precise diff line whenever the finding maps to one; otherwise post it as a general thread (commit/branch/description-level findings). Keep threads to genuine findings — **one thread per Blocker/Major/Minor; never a thread per Nit.** Roll the Nits up into a single line at the bottom of the global note (`Nits: <a>; <b>`), so they don't each open a merge-blocking discussion.

Show both the note and the thread list to the user. **Post nothing yet.** State the recommended verdict and what's unverified.

## Post — only on explicit go-ahead

When (and only when) the user explicitly validates, post. Project path is URL-encoded (`gm%2Fjanneau`).

**Global note:**

```bash
GITLAB_HOST=<host> glab mr note <id> -R <group/project> -m "$(cat note.md)"
```

**Per-topic threads** (resolvable discussions). Fetch the diff SHAs once:

```bash
GITLAB_HOST=<host> glab api "projects/<enc-path>/merge_requests/<id>" | jq .diff_refs
# -> base_sha, start_sha, head_sha
```

*Inline thread* anchored to a line — build the JSON with `jq` (safe encoding), POST via `--input` with an explicit content-type (glab drops bracketed `position[...]` form fields, and doesn't set the header itself):

```bash
jq -n --arg body "$BODY" --arg base "$BASE" --arg head "$HEAD" --arg file "<path>" --argjson line <new_line> \
  '{body:$body, position:{position_type:"text", base_sha:$base, start_sha:$base, head_sha:$head, new_path:$file, old_path:$file, new_line:$line}}' > /tmp/disc.json
GITLAB_HOST=<host> glab api -X POST "projects/<enc-path>/merge_requests/<id>/discussions" \
  -H "Content-Type: application/json" --input /tmp/disc.json
```

- `new_line` = line number on the **new** side (added or context lines). For a **removed** line, use `old_line` instead and omit `new_line`.
- An invalid position → HTTP 400 "Note position is invalid". A *silently* position-less thread (`position: null` in the response) means the anchor was dropped — check `.notes[0].position.new_line` came back non-null.

*General thread* (commit / branch / description findings — no line):

```bash
GITLAB_HOST=<host> glab api -X POST "projects/<enc-path>/merge_requests/<id>/discussions" -f "body=$BODY"
```

**Approve** — separate, explicit step, only if the verdict is approve *and* the user said so:

```bash
GITLAB_HOST=<host> glab mr approve <id> -R <group/project>
# request-changes = post the threads, leave unapproved; glab mr revoke <id> -R <…> drops a prior approval
```

Confirm what you posted (note URL, thread count, anchored vs general, approval state). Never merge, never mark Ready.

## Non-goals

- Draft by default. Posting the note and approving each require explicit user go-ahead — approval in one review doesn't carry to the next.
- Never approve what you couldn't verify, and never on Blocker/Major findings.
- Never merge or mark the MR Ready.
- Don't flag pre-existing code the MR didn't touch — note it as out-of-scope instead.
