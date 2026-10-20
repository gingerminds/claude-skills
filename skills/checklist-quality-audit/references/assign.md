# Assignment (section 9, spec § 5.6)

The lead runs `/gm:checklist-quality-audit assign` after reviewing the statuses and comments in Notion. The skill proposes an assignee for each row left to do, the lead validates in one answer, then the skill writes the assignments and a summary comment on the page of the copy. Every Bash call starts with the `S=` line of SKILL.md (here unexpanded) and `RUN="<run_dir>"`.

## Run directory

1. `cqa_plan.py project "$PWD"` for the slug, then `cqa_plan.py runs "<slug>"`.
2. Take the most recent run whose `context` is true, preferably with `applied` true (the audit that wrote the statuses). Ask « Utiliser le run <run_id> ? » with « Oui » and « Préparer un nouveau run ».
3. New run, or no run: create the folder as in SKILL.md section 3, then only steps 1 (repos) and 2 (Notion, lead and team) of `references/context.md`. Write `context.json` as in `references/context.md` step 7, with the other sections empty (`target` with `null` URLs and access and `prod_live` false, `ssh` `{"alias": null, "app_dirs": {}}`, `test_accounts` both false, `key_pages` and `available_tools` empty), then `cqa_plan.py context "$RUN" --check`.
4. Show the team of `context.json` (name, role, option): « Équipe : <nom> (<rôle>, « <option> »), ... Est-ce à jour ? » with « Oui » and the free-text field. A change: run `cqa_plan.py team` again, or edit `team` in `context.json`, then check it. Never create an option.

## Proposal

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

Reads the copy again (statuses after the lead's review) into `rows.json`. Exit 3: Notion API.

When `lots.json` exists, ask « Ajouter les lignes non évaluées à la répartition ? » (« Oui », « Non »). Without `lots.json` (no audit in this run), do not ask: the rows not evaluated cannot be told.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" assign "$RUN" --include-unknown
```

- `--include-unknown` only when the lead said yes.
- Writes `notion/assign-proposal.json` and prints `{"lead", "include_unknown", "proposal": [{"ref", "page_id", "title", "sections", "assignee"}], "sans_option"}`.
- Kept rows: « À traiter » and « Mise en place partielle », plus the rows not evaluated on request. The rows « Mis en place » stay with the lead, who got them during the audit.
- Rule: Front to the front devs, Back to the back devs, the full-stack devs to balance; the least loaded first; a row without candidate goes to the lead.
- `sans_option`: members without an option in « Assignée à »: they get nothing. Say « <nom> n'a pas d'option dans « Assignée à » : créer l'option dans Notion à la main si besoin, le skill n'en crée jamais. »
- Exit 6: `lead.option` empty in `context.json`: ask the lead's option, fix it, check again.

## Validation by the lead

Show the proposal grouped by person: « <option> (<n> lignes) : CQ-12 <titre>, CQ-15 <titre>, ... ».

Ask « Valider cette répartition ? » with « Valider », « Annuler », and the free-text field for every adjustment in one answer, for example « CQ-12 et CQ-15 à Léa ; CQ-30 à personne ».

- Apply the adjustments to `notion/assign-proposal.json` with Write: change `assignee`, or set it to `null` to leave the row unassigned.
- An assignee must be one of `notion.assignee_options`, written exactly. A name that matches none: ask once which option it is. Never create one.
- « Annuler »: stop, nothing written.

## Plan and writing

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" assign-plan "$RUN"
```

- Writes `notion/assign-plan.json` and prints `{"file", "assigned", "unchanged", "page_comment"}`. The assignee is added to the current ones, never in their place; `unchanged` counts the rows that already had them.
- `page_comment` true: the plan ends with a summary comment on the page of the copy (count per person).
- Exit 2: an assignee is not an option, or a row is unknown or given twice: fix `notion/assign-proposal.json`, run again.

With `--dry-run`: show the plan and stop.

Otherwise, after « Écrire ces <n> assignations dans Notion ? » (« Écrire », « Ne rien écrire »):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" apply "$RUN" --plan notion/assign-plan.json
```

- Prefix `GM_AUDIT_AUTH_PASS='<mot de passe>'` when the session holds it, for the secret check.
- Exit codes and their words for the lead: `references/verdicts.md`, section "For the orchestrator". An `unknown_option`: the option was removed from the copy since the plan; nothing is created.
- End with « <n> lignes assignées. »
