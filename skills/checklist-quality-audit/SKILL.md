---
name: checklist-quality-audit
description: Runs the agency pre-launch (PRE-MEL) quality checklist audit on a project. Checks the workstation, verifies that the preprod runs the audited code, evaluates every row of the project's Notion checklist copy from the code, the preprod, a Docker toolbox and the browser, then writes statuses and evidence comments to that copy and splits the remaining rows between the devs. Use when the lead dev asks to run the quality checklist or a PRE-MEL audit, or invokes /gm:checklist-quality-audit or /gm:cqa.
argument-hint: "[diagnostic | assign] [--phase \"MEL J0\"] [--dry-run]"
disable-model-invocation: true
---

# PRE-MEL quality checklist audit

You orchestrate the audit of a project's quality checklist before launch. You decide and you write. The sub-agents `gm:cqa-auditor` and `gm:cqa-browser` analyse, the Docker toolbox measures, and all of them exchange through files in the run directory `~/.cache/gm-audit/<slug>/<AAAA-MM-JJ-HHMM>/`.

The lead is needed at the start (questions, login in Chrome) and at the end (confirmation of the Notion writes). Everything in between runs without them.

Load each reference with Read when its section starts, not before.

## 1. Imperative rules

- **Read-only (v1).** Never modify a project repo: no checkout, switch, stash, pull, commit, install, file edit, runner start. Never write on a server or in a database, never submit a form, never deploy. When something needs one of these, give the lead the exact command and let them run it.
- **Notion.** Write only through `cqa_notion.py apply` and `cqa_notion.py comment-page`, after the lead's confirmation. Never through another tool, never on the reference template (exit 8). Never create an option, a row or a page; never delete anything.
- **Secrets (spec § 10).** Never print, write or hand to a sub-agent the value of `NOTION_TOKEN`, `GM_SF_LICENCE_USER`, `GM_SF_LICENCE_KEY` or the preprod Basic Auth credentials. Never run `env`, `printenv` or `set`, never `echo` these variables: test their presence only, as in `references/context.md`.
- **Basic Auth credentials.** Asked only after a 401, kept in this session, never stored. They go only as inline environment variables (`GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>'`) to `cqa_plan.py probe-target --auth-env` and `cqa-toolbox.sh`. `GM_AUDIT_AUTH_PASS` alone also goes, the same way, to the secret checks of `cqa_notion.py apply`, `cqa_notion.py comment-page` and `cqa_plan.py scan`. Never in a file, a URL, Notion, a sub-agent prompt or Chrome (the lead types it there).
- **SSH (agency rule).** Ask which alias to use before any SSH command (aliases read in `~/.ssh/config`). Run every server command through `cqa_ssh_ro.py`, except the connection test `ssh -o BatchMode=yes <alias> true`. Exit 4 of the guard means "commande à faire confirmer par le lead": stop and ask, stating the impact, even if the lead asked to go faster (`references/server.md`).
- **Questions.** One at a time, with AskUserQuestion, in French, and only when the answer is missing or must be confirmed. A free answer (URL, identifier) goes in the free-text field of the question.
- **Language.** Everything the lead reads is in French. Never write an em dash: use a colon, a comma or parentheses.
- **Commands.** Shell variables do not survive between Bash calls: start every call with `S="${CLAUDE_SKILL_DIR}/../../scripts"` and, once the run exists, `RUN="<absolute path of the run directory>"`. Quote every path: folder names hold spaces. The references are read with Read, so the `S=` line they show is not expanded: use the one of this file.
- **Sub-agents.** At most 4 at once, and at most one browser lot at a time (Chrome is shared). They never talk to the lead and never write to Notion.
- **Errors.** The scripts print their errors in French on stderr: show them to the lead. Never retry blindly; fix the cause first (exit codes at the end of this file).

## 2. Arguments

Arguments: `$ARGUMENTS` (through the `/gm:cqa` alias, the alias gives them).

- None: full audit, sections 3 to 8.
- `diagnostic`: sections 3 to 5, then stop once the coverage is shown. Nothing is written to Notion.
- `assign`: go to section 9.
- `--phase "<phase>"`: audit only the rows of that phase (`Cadrage`, `Build`, `Recette J-30`, `MEL J0`, `Post-MEL J+7`), stored as `phase_filter`. With `MEL J0` or `Post-MEL J+7`, section 4 asks whether the prod is live: the audit targets the prod only then.
- `--dry-run`: everything except the Notion writes, stored as `dry_run`. Section 7 stops after the local synthesis.
- Anything else: show `/gm:checklist-quality-audit [diagnostic | assign] [--phase "MEL J0"] [--dry-run]` and stop.

## 3. Resume

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" project "$PWD"
```

Keep its output for section 4. With its `slug`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" runs "<slug>"
```

A run of today (`run_id` starting with today's date) whose `synthese` is false is incomplete. Ask « Le run <run_id> du jour n'est pas terminé. » with « Reprendre ce run » and « Lancer un nouveau run ». To resume, `RUN` is its `path`; run `cqa_plan.py context "$RUN" --check`, ask the Basic Auth credentials again when `target.access` is `basic_auth`, then restart at the first stage it lacks:

- `context` false: section 4, in this folder.
- `lots` false: section 5 (the diagnostic is redone: the workstation may have changed).
- `lots` true and `plan` false: section 6. A lot whose `cqa_plan.py validate` passes is not dispatched again; without `tools/manifest.json`, the toolbox is launched again.
- `plan` true: section 7. `apply` skips the writes already listed in `notion/applied.json`.

New run (also for `diagnostic`):

```bash
RUN="$HOME/.cache/gm-audit/<slug>/$(date +%Y-%m-%d-%H%M)"
mkdir -p "$RUN" && echo "$RUN"
```

From then on, write the printed absolute path in every `RUN=` line.

## 4. Context

Read `references/context.md`. In order, one question at a time:

1. Repos: confirm the list given by `cqa_plan.py project` (1 or 2 repos) and the technos.
2. Notion: `cqa_notion.py whoami`, the URL of the copy, `cqa_notion.py resolve` of the copy and of `GM_CQA_REFERENCE` (stop on the template), `cqa_notion.py schema` (exit 6: stop), then `cqa_plan.py team` for the lead and the team.
3. Target: preprod URL read in `PROJECT.md` and confirmed, then `cqa_plan.py probe-target`. Access `basic_auth`: ask the identifier, then the password, and check them with `cqa_plan.py probe-target --auth-env`. Access `ip_filtered`: announce « HTTP indisponible hors réseau agence ».
4. SSH alias, from the list of `~/.ssh/config`, or none.
5. Test accounts (front, back-office).
6. Key pages: one per template, 10 at most, proposed from the sitemap, the links of the home page and the routes of the code, validated by the lead.
7. Write `context.json`, then check it until it passes:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

## 5. Diagnostic

Read `references/diagnostic.md`. It gives the exact command of each check:

| Check | How | Tool covered |
|---|---|---|
| Repos on `develop`, clean, up to date with `origin` | `cqa_plan.py repo-state` | Code |
| Commit deployed in preprod | guard (`git -C <dir> rev-parse HEAD`, `cat <dir>/REVISION`, `docker ps`), then `cqa_plan.py deployed`, then the lead | launch condition |
| Preprod aligned on the audited code | `cqa_plan.py align` | launch condition |
| Runner up | `shared/runner.md` (make, docker compose, lando), never started by the skill | CLI local |
| Preprod reachable | `probe-target` of section 4 | HTTP |
| Claude in Chrome connected | a tab opened on the preprod (`references/browser.md`) | Navigateur |
| Docker, registry, images, Screaming Frog licence | `cqa-toolbox.sh doctor` | Audit web, Crawler |
| SSH alias reachable | `ssh -o BatchMode=yes <alias> true` | SSH serveur |
| Application CLI on the server (drush, wp, artisan) | through the guard | BDD |
| Test accounts | answer of section 4 | Comptes de test |
| Public preprod | no protection in `PROJECT.md` and access `open` | Service externe |
| Notion token, template and copy | section 4 | global prerequisite |

Preprod behind `develop`: list the missing commits and ask « Déployer puis relancer (recommandé) » or « Lancer quand même ». Never deploy. Deployed commit not found: the audit goes on only if the lead confirms the alignment.

Then write `available_tools` and the repo facts in `context.json`, check it, read the copy and compute the coverage:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
python3 "$S/cqa_notion.py" rows "$RUN"
python3 "$S/cqa_plan.py" coverage "$RUN"
```

Show the table of checks, the coverage and the gains as `references/diagnostic.md` words them. `evaluable` already includes `evaluable_human`: never add them. With `diagnostic`, stop here. Otherwise ask « Lancer l'audit » or « Compléter mon poste d'abord » (then stop: a later launch the same day offers to resume).

## 6. Audit

Read `references/lots.md`, then `references/browser.md` and `references/server.md` when their lots come.

1. Prepare the rows, the lots and the probes:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" fiches "$RUN"
python3 "$S/cqa_notion.py" comments "$RUN"
python3 "$S/cqa_plan.py" lots "$RUN"
python3 "$S/cqa_plan.py" probes "$RUN"
```

2. Start the toolbox in the background (Bash with `run_in_background`), the credentials inline only when `target.access` is `basic_auth`. Toolbox not ready: mark each tool skipped with `cqa_plan.py skip` instead (`references/lots.md`).

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' bash "$S/cqa-toolbox.sh" all "$RUN"
```

3. Dispatch the first lots of step 4, then the login pause: open the preprod (and the back-office) in new Chrome tabs and wait for the lead to log in (`references/browser.md`). Skip the pause when "Navigateur" is unavailable.
4. The `server` lot (always `gm:cqa-auditor`; say in the dispatch when "SSH serveur" or "BDD" is unavailable) and the `code` lots whose `needs_toolbox` is false, 4 sub-agents at most at once.
5. The browser lots, one at a time, once the `server` lot is done: `gm:cqa-browser`. A return that starts with `CQA_BROWSER_UNAVAILABLE:` switches to the fallback: read `${CLAUDE_SKILL_DIR}/../../agents/cqa-browser.md` and run it yourself in this session, template by template, for this lot and the next browser lots. "Navigateur" unavailable from the start: give the browser lots to `gm:cqa-auditor`.
6. Once the toolbox has finished, the `-outils` lots (same agents, same limits).
7. After each lot, `cqa_plan.py validate "$RUN" <lot>`. On errors, relaunch that sub-agent once, with the list of errors; then leave it (its invalid rows count as `unknown`).
8. Follow-up: `cqa_plan.py probes "$RUN" --followup`. With probes, `cqa-toolbox.sh run "$RUN" http --followup`, then relaunch once each lot it lists with `Follow-up: yes`, and validate with `cqa_plan.py validate "$RUN" <lot> --followup`.

Dispatch line, exactly, then the extra sentences of `references/lots.md`:

```
Run dir: <run_dir>. Lot: <lot_id>. Follow-up: <yes|no>. Follow your instructions.
```

## 7. Plan and confirmation

Read `references/verdicts.md`, section "For the orchestrator".

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN"
```

1. Show the summary: count per final status, blocking rows not compliant, rows not evaluated, and the explicit list of statuses set by a human that the audit would change.
2. With `--dry-run`: no question. Run `cqa_plan.py synthesis` and `cqa_plan.py scan` (section 8, steps 2 and 4), show `synthese.md`, say that nothing was written to Notion and that the plan stays in `notion/plan.json`, and stop.
3. Otherwise ask « Écrire le plan dans la copie Notion ? » with « Tout écrire », « Sans toucher aux statuts posés par un humain » (only when there are some) and « Ne rien écrire ». For the second, rebuild the plan with `cqa_plan.py plan "$RUN" --skip-human-overrides` so that the synthesis matches what is written. « Ne rien écrire »: as with `--dry-run`.

## 8. Writing and synthesis

One Bash call per command. Prefix `GM_AUDIT_AUTH_PASS='<mot de passe>'` only when the session holds it.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_notion.py" apply "$RUN"
python3 "$S/cqa_plan.py" synthesis "$RUN"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_notion.py" comment-page "$RUN" "$RUN/notion/page-comment.txt"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_plan.py" scan "$RUN"
```

1. `cqa_notion.py apply`, plus `--skip-human-overrides` for the second choice of section 7. Exit 0: all written. Exit 7: some operations blocked (`blocked[].labels`), the others written. Exit 3: stopped on a network or API error (wins over 7); run the same command again, it resumes where it stopped. Exit 8: the copy is the template, nothing written. `references/verdicts.md` gives the words for the lead.
2. `cqa_plan.py synthesis`: writes `synthese.md` and `notion/page-comment.txt`.
3. `cqa_notion.py comment-page`: the short summary on the page of the copy (same exit codes; without `copy_page_id` it writes nothing and says so).
4. `cqa_plan.py scan`: anti-secret check of the run directory. Exit 7: give the lead the files and labels found, never a value.
5. Show `synthese.md`. Under a last heading « Remarques » of `synthese.md`, add what the scripts cannot know: an alignment the lead confirmed without a known deployed commit, and, for the second choice of section 7, the human statuses kept (« CQ-<ref> : « <before> » conservé, l'audit proposait « <after> » »). End with « Relisez les statuts et les commentaires dans Notion, puis lancez /gm:checklist-quality-audit assign pour répartir les lignes restantes. »

## 9. Assignment

Read `references/assign.md` (run directory to use, team, adjustments).

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
python3 "$S/cqa_plan.py" assign "$RUN"
python3 "$S/cqa_plan.py" assign-plan "$RUN"
python3 "$S/cqa_notion.py" apply "$RUN" --plan notion/assign-plan.json
```

1. `cqa_notion.py rows`: read the copy again, as the lead reviewed it.
2. `cqa_plan.py assign`, with `--include-unknown` only if the lead wants the rows not evaluated too. Show the proposal; the lead validates or adjusts it in one answer.
3. `cqa_plan.py assign-plan` after the adjustments are written to `notion/assign-proposal.json`.
4. `cqa_notion.py apply` with `--plan notion/assign-plan.json` (same exit codes as section 8). With `--dry-run`, stop before it.

## Exit codes

| Script | Codes |
|---|---|
| `cqa_notion.py` | 0 success; 2 environment or usage; 3 Notion API or network (token refused, page not shared with the integration); 6 schema of the copy not v2; 7 at least one write blocked; 8 write refused on the template |
| `cqa_plan.py` | 0 success; 2 usage or missing file (the message names the command to run first); 6 invalid `context.json` or findings; 7 secret found by `scan` |
| `cqa_ssh_ro.py` | 0 success; 1 remote failure (command or file missing on the server); 2 usage; 4 refused, to be confirmed by the lead |
| `cqa-toolbox.sh` | 0 success; 1 at least one tool failed (the manifest is written); 2 usage or environment; `run` returns the code of the tool (3: target unreachable or 401) |
