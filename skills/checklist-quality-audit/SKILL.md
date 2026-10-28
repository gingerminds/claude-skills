---
name: checklist-quality-audit
description: Runs the agency pre-launch (PRE-MEL) quality checklist audit on a project. Checks the workstation, verifies that the preprod runs the audited code, evaluates every row of the project's Notion checklist copy from the code, the preprod, a Docker toolbox and the browser, then writes statuses and evidence comments to that copy and splits the remaining rows between the devs. Use when the lead dev asks to run the quality checklist or a PRE-MEL audit, or invokes /gm:checklist-quality-audit or /gm:cqa.
argument-hint: "[diagnostic | assign | rerun | recette] [--phase \"MEL J0\"] [--dry-run]"
disable-model-invocation: true
---

# PRE-MEL quality checklist audit

You orchestrate the audit of a project's quality checklist before launch. You decide and you write. The sub-agents `gm:cqa-auditor` and `gm:cqa-browser` analyse, the Docker toolbox measures, and all of them exchange through files in the run directory `~/.cache/gm-audit/<slug>/<AAAA-MM-JJ-HHMM>/`.

The lead is needed at the start (questions, login in Chrome) and at the end (confirmation of the Notion writes). Everything in between runs without them.

Load each reference with Read when its section starts, not before.

## 1. Imperative rules

- **Read-only (v1).** Never modify a project repo: no checkout, switch, stash, pull, commit, install, file edit, runner start. Never write on a server or in a database, never submit a form, never deploy. When something needs one of these, give the lead the exact command and let them run it.
- **Notion.** Write only through `cqa_notion.py apply` and `cqa_notion.py comment-page`, after the lead's confirmation. Never through another tool, never on the reference template (exit 8). Never create an option, a row or a page; never delete anything.
- **Secrets (spec § 10).** Never print, write or hand to a sub-agent the value of `NOTION_TOKEN` or the preprod Basic Auth credentials. Never run `env`, `printenv` or `set`, never `echo` these variables: test their presence only, as in `references/context.md`.
- **Basic Auth credentials.** Asked only after a 401, kept in this session, never stored. They go only as inline environment variables (`GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>'`) to `cqa_plan.py probe-target --auth-env` and `cqa-toolbox.sh`, and only when the session holds the Basic Auth credentials (accepted by `probe-target --auth-env`). `GM_AUDIT_AUTH_PASS` alone also goes, the same way, to the secret checks of `cqa_notion.py apply`, `cqa_notion.py comment-page` and `cqa_plan.py scan`. Never in a file, a URL, Notion, a sub-agent prompt or Chrome (the lead types it there).
- **SSH (agency rule).** Ask which alias to use before any SSH command (aliases read in `~/.ssh/config`). Run every server command through `cqa_ssh_ro.py`, except the connection test `ssh -o BatchMode=yes <alias> true`. Exit 4 of the guard means "commande à faire confirmer par le lead": stop and ask, stating the impact, even if the lead asked to go faster (`references/server.md`).
- **Questions.** One at a time, with AskUserQuestion, in French, and only when the answer is missing or must be confirmed. AskUserQuestion takes 2 to 4 options and always adds a free-text answer (« Autre »). For a value to type (URL, SHA, identifier, password, folder), give real options (« Annuler l'audit », « Je ne sais pas », « Continuer sans identifiants »...) and say in the question to type the value in « Autre ». If the free-text answer is not available in this session, ask the same question as a plain message and wait for the reply. Never repeat a password back to the lead, in a message, a summary or a recap of the answers.
- **Language.** Everything the lead reads is in French. Never write an em dash: use a colon, a comma or parentheses.
- **Commands.** One Bash call per command, as the blocks of this file and of the references show; never chain a Notion write with anything else (no `&&`, `;` or pipe around `cqa_notion.py apply` or `comment-page`). Shell variables do not survive between Bash calls: start every call with `S="${CLAUDE_SKILL_DIR}/../../scripts"` and, once the run exists, `RUN="<absolute path of the run directory>"`. Quote every path: folder names hold spaces. The references are read with Read, so the `S=` line they show is not expanded: use the one of this file.
- **Sub-agents.** At most 4 at once, and at most one browser lot at a time (Chrome is shared). They never talk to the lead and never write to Notion.
- **Errors.** The scripts print their errors in French on stderr: show them to the lead. Never retry blindly; fix the cause first (exit codes at the end of this file).

## 2. Arguments

Arguments: `$ARGUMENTS` (through the `/gm:cqa` alias, the alias gives them).

- None: full audit, sections 3 to 8.
- `diagnostic`: sections 3 to 5, then stop once the coverage is shown. Nothing is written to Notion.
- `assign`: go to section 9.
- `rerun`: go to section 10. Re-evaluates a few rows of an existing run, not the whole audit.
- `recette`: go to section 11. Checks the rows the devs fixed (set to « Mis en place » in the copy) and closes them if the fix holds.
- `--phase "<phase>"`: audit only the rows of that phase (`Cadrage`, `Build`, `Recette J-30`, `MEL J0`, `Post-MEL J+7`), stored as `phase_filter`. With `MEL J0` or `Post-MEL J+7`, section 4 asks whether the prod is live: the audit targets the prod only then.
- `--dry-run`: everything except the Notion writes, stored as `dry_run`. Section 7 asks nothing and section 8 skips its Notion steps (apply, page comment); the synthesis and the final scan still run.
- Anything else: show `/gm:checklist-quality-audit [diagnostic | assign | rerun | recette] [--phase "MEL J0"] [--dry-run]` and stop.

## 3. Resume

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" project "$PWD"
```

Exit 2: the current folder is not in a git repo, or its name is not « Client - Projet - Techno ». Show the message and stop (section 4, step 1). Otherwise keep the output for section 4. With its `slug`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" runs "<slug>"
```

A run is complete once its final scan has run: section 8 then writes `done.json` in it, and `runs` reports it as `done`. A run of today (`run_id` starting with today's date) whose `done` is false is incomplete. Ask « Le run <run_id> du jour n'est pas terminé. » with « Reprendre ce run » and « Lancer un nouveau run ». To resume:

1. `RUN` is its `path`.
2. The current invocation wins over what the run stored. A `--phase` other than its `phase_filter` cannot reuse its lots and findings: say « Ce run a été lancé pour une autre phase : nouveau run. » and start a new run. Otherwise write the current `dry_run` in `context.json`.
3. Run `cqa_plan.py context "$RUN" --check`. On errors, ask « Le contexte du run <run_id> est invalide (<erreurs>). » with « Lancer un nouveau run » and « Arrêter ».
4. If `target.access` is `basic_auth`, ask the Basic Auth credentials again (they are never stored) and check them with `probe-target --auth-env`, as in section 4.
5. Restart at the first stage it lacks:
   - `context` false: section 4, in this folder.
   - `lots` false: section 5 (the diagnostic is redone: the workstation may have changed).
   - `lots` true and `plan` false: section 6. A lot whose `cqa_plan.py validate` passes is not dispatched again. Toolbox: no `tools/` folder, launch it again; `tools/` without `tools/manifest.json`, rebuild the manifest with `cqa_plan.py manifest "$RUN"` and relaunch only the missing or failed tools (`references/lots.md`).
   - `plan` true: section 7. `apply` skips the writes already listed in `notion/applied.json`.

An interrupted run may have left its inhibitor running: it runs in its own session and outlives the Claude session. `start` reuses a live one instead of launching another. When the run restarts at section 7, run `start` as at the beginning of section 6 (section 6 does it itself): it keeps the inhibitor still running or launches a new one.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" start "$RUN"
```

New run (also for `diagnostic`):

```bash
RUN="$HOME/.cache/gm-audit/<slug>/$(date +%Y-%m-%d-%H%M)"
mkdir -p "$RUN" && echo "$RUN"
```

From then on, write the printed absolute path in every `RUN=` line.

## 4. Context

Read `references/context.md`. In order, one question at a time:

1. Repos: confirm the list given by `cqa_plan.py project` (1 or 2 repos) and the technos. Exit 2 of `project` (not in a git repo, or a folder not named « Client - Projet - Techno »): show the message and stop; the lead launches the skill again from a repo of the project.
2. Notion: `cqa_notion.py whoami`, the URL of the copy, `cqa_notion.py resolve` of the copy and of `GM_CQA_REFERENCE` (the URL of the template given for the copy: ask again, never go on with it; nothing is written either way), `cqa_notion.py schema` into `schema.json` (exit 6: show its `problems` and stop; exit 3: stop before `team`), then `cqa_plan.py team` for the lead and the team.
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
| Docker, registry, images | `cqa-toolbox.sh doctor` | Audit web, Crawler |
| SSH alias reachable | `ssh -o BatchMode=yes <alias> true` | SSH serveur |
| Application CLI on the server (drush, wp, artisan) | through the guard | BDD |
| Test accounts | answer of section 4 | Comptes de test |
| Public preprod | no protection in `PROJECT.md` and access `open` | Service externe |
| Notion token, template and copy | section 4 | global prerequisite |

Preprod behind `develop`: list the missing commits and ask « Déployer puis relancer (recommandé) » or « Lancer quand même ». Never deploy. Deployed commit not found: the audit goes on only if the lead confirms the alignment.

Then write `available_tools` and the repo facts in `context.json`, and check it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

Read the copy (`rows.json`):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

Compute the coverage:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" coverage "$RUN"
```

Show the table of checks, the coverage and the gains as `references/diagnostic.md` words them. `evaluable` already includes `evaluable_human`: never add them. With `diagnostic`, stop here. Otherwise ask « Lancer l'audit » or « Compléter mon poste d'abord » (then stop: a later launch the same day offers to resume).

## 6. Audit

Read `references/lots.md`, then `references/browser.md` and `references/server.md` when their lots come.

Before step 1, keep the workstation awake (best effort: without an inhibitor the script only warns and exits 0):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" start "$RUN"
```

Then tell the lead, word for word: « Le poste ne se mettra pas en veille pendant l'audit. Gardez l'écran ouvert : sur un Mac, rabattre l'écran le met en veille malgré tout. » If the script warned that no inhibitor exists, show its warning too. Otherwise also tell the lead how to release the workstation by hand if this session ends before the audit does (the inhibitor outlives it), with `<scripts>` and `<run_dir>` replaced by their absolute paths: « Si cette session s'arrête avant la fin de l'audit, libérez le poste avec : bash "<scripts>/cqa-keepawake.sh" stop "<run_dir>" »

From here to the end of section 8, run `stop` (below) on every path that ends the audit: the end of section 8, and every other stop of the audit (section 7 abandoned, exit codes 2, 3 and 8 of section 8, an error that ends the run).

1. Prepare, one call each, in this order: the fiches of the template, the verdicts already in the comments, the lots, the probes (outputs and errors in `references/lots.md`).

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" fiches "$RUN"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" comments "$RUN"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" lots "$RUN"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" probes "$RUN"
```

2. Start the toolbox in the background (Bash with `run_in_background`), with the credentials inline only when the session holds the Basic Auth credentials. Toolbox not ready: mark each tool skipped with `cqa_plan.py skip` instead (`references/lots.md`).

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

Before each new wave of lots (steps 4, 5, 6 and 8), recheck the access, because the workstation may have slept or changed network:
- run `cqa_plan.py probe-target` on the target (`references/context.md`, section 3; the prod URL for a prod run) without credentials, and compare its `access` with the one in `context.json` (also the answer without credentials: `basic_auth` for a preprod behind Basic Auth);
- when the session holds the Basic Auth credentials, run it once more with `--auth-env`: any answer other than `open` means the credentials no longer pass;
- when "SSH serveur" is available, run `ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> true` (`references/diagnostic.md`, section 7).

If the `access` without credentials differs from the one in `context.json`, the check with credentials is not `open`, or the SSH test fails, launch no new lot. Tell the lead in French what changed (« La préprod répond maintenant <access> au lieu de <access du contexte> », « La préprod refuse maintenant les identifiants Basic Auth » or « Connexion SSH impossible avec <alias> »), then ask with « Reprendre » (after the lead restored the access: recheck) and « Continuer quand même » (launch the wave; note it in the « remarques de la synthèse »: the rows concerned may end `unknown`).

Before each browser lot (steps 3 and 5), call `tabs_context_mcp`. If it fails, ask the lead to reconnect Claude in Chrome, with « Reprendre » and « Continuer sans navigateur » (`references/browser.md`).

« Continuer quand même » and « Continuer sans navigateur » hold for the rest of the run, unless the lead says otherwise: at the next waves, ask again only for a change the lead has not answered yet, and after « Continuer sans navigateur » give every later browser lot to `gm:cqa-auditor` without a new check.

Dispatch line, exactly, then the extra sentences of `references/lots.md`:

```
Run dir: <run_dir>. Lot: <lot_id>. Follow-up: <yes|no>. Follow your instructions.
```

For a rerun (section 10) the line carries the rerun number and the rows instead; a recette (section 11) adds `Recette: yes.`.

## 7. Plan and confirmation

Read `references/verdicts.md`, section "For the orchestrator".

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN"
```

1. Show the summary: count per final status, blocking rows not compliant, rows not evaluated, and the explicit list of statuses set by a human that the audit would change.
2. With `--dry-run`: no question, no Notion write. Go to section 8, step 2.
3. Otherwise ask « Écrire le plan dans la copie Notion ? » with « Tout écrire », « Sans toucher aux statuts posés par un humain » (only when there are some) and « Ne rien écrire ».
   - « Tout écrire »: section 8, step 1.
   - « Ne rien écrire »: as with `--dry-run`, section 8, step 2.
   - « Sans toucher aux statuts posés par un humain »: keep the list of those statuses, rebuild the plan with the command below so that the synthesis matches what is written, then section 8, step 1, with `--skip-human-overrides`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN" --skip-human-overrides
```

If the lead abandons the audit at this point, release the workstation first:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" stop "$RUN"
```

## 8. Writing and synthesis

Each step is one Bash call. Prefix `GM_AUDIT_AUTH_PASS='<mot de passe>'` only when the session holds the Basic Auth credentials.

**1. Apply**, only after « Tout écrire » or « Sans toucher aux statuts posés par un humain » in section 7 (add `--skip-human-overrides` for the second):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_notion.py" apply "$RUN"
```

The output lists only the operations written, blocked or failed, then the JSON summary (`done`, `skipped`, `blocked`, `errors`); operations already present are counted in `skipped`, never listed, so read it as is, without a pipe or filter.

Then, by exit code (words for the lead in `references/verdicts.md`):

- 0: everything written. Go on.
- 7: some operations blocked (`blocked[].labels`), the others written. Show each blocked operation (ref, kind, reason, never its text), keep the list for « Remarques », go on.
- 3: stopped on a network or Notion API error, or an operation refused by Notion (wins over 7). Show the message. Once the cause is fixed (network, token, connection of the page), run the same command once more: it resumes where it stopped. Still 3: stop. Say « Écriture interrompue : le run reste reprenable, relancez la commande plus tard. » No synthesis: the run stays incomplete and resumable at section 7. Release the workstation first (`cqa-keepawake.sh stop "$RUN"`, as at the end of this section).
- 8: the copy is the reference template. Nothing was written: release the workstation (`cqa-keepawake.sh stop "$RUN"`), stop, and say « Écriture refusée : la copie est le modèle de référence. Lancez un nouveau run avec l'URL de la copie du projet. »
- 2: show the message, release the workstation (`cqa-keepawake.sh stop "$RUN"`) and stop.

**2. Synthesis**, on every path that reaches this step (written, `--dry-run`, « Ne rien écrire »):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" synthesis "$RUN"
```

It writes `synthese.md` and `notion/page-comment.txt`.

**3. Remarques**, on the same paths. Under a last heading « Remarques » of `synthese.md`, add with Edit what the scripts cannot know, or nothing (no heading) when there is none:
- an alignment the lead confirmed without a known deployed commit;
- for « Sans toucher aux statuts posés par un humain », the human statuses kept: « CQ-<ref> : « <before> » conservé, l'audit proposait « <after> » »;
- the operations blocked at step 1: « CQ-<ref> (<kind>) non écrit : <raison> »;
- for `--dry-run` and « Ne rien écrire »: « Rien n'a été écrit dans Notion : le plan reste dans notion/plan.json. »

**4. Page comment**, only when step 1 ran and wrote or found something (`done` plus `skipped` above 0). Never with `--dry-run` or « Ne rien écrire ».

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_notion.py" comment-page "$RUN" "$RUN/notion/page-comment.txt"
```

Same exit codes as step 1. Without `copy_page_id` it writes nothing and says so. Exit 3 after one more try once the cause is fixed, or exit 7: say that the page comment is missing (its text is in `notion/page-comment.txt`) and go on.

**5. Final scan**, on every path that reached step 2:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_plan.py" scan "$RUN"
```

Anti-secret check of the run directory (the cached template fiches in `fiches/` are excluded). Exit 7: give the lead the files and labels found, never a value.

**6. Completion.** Write `done.json` in the run directory with Write: `{"run_id": "<run_id>", "scan_ok": <true or false>}`. The run is now complete (section 3).

**7. Release the workstation.** The audit is over:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" stop "$RUN"
```

**8. End.** Show `synthese.md`. After a write, end with « Relisez les statuts et les commentaires dans Notion, puis lancez /gm:checklist-quality-audit assign pour répartir les lignes restantes. »

## 9. Assignment

Read `references/assign.md` (run directory to use, team, adjustments, errors). One Bash call per step.

1. Read the copy again, as the lead reviewed it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

2. Propose the assignees, with `--include-unknown` only if the lead wants the rows not evaluated too (asked only when `lots.json` exists):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" assign "$RUN"
```

3. Show the proposal and ask « Valider cette répartition ? » with « Valider » and « Annuler », adjustments in « Autre », in one answer. Write the adjustments to `notion/assign-proposal.json`. « Annuler »: stop, nothing written.
4. Build the plan of the assignments:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" assign-plan "$RUN"
```

5. With `--dry-run`: show the plan and stop. Otherwise ask « Écrire ces <n> assignations dans Notion ? » with « Écrire » and « Ne rien écrire ». « Ne rien écrire »: stop.
6. Only after « Écrire »:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" apply "$RUN" --plan notion/assign-plan.json
```

Exit codes as in section 8, step 1 (prefix `GM_AUDIT_AUTH_PASS` the same way). End with « <n> lignes assignées. »

## 10. Rerun

Re-evaluates a few rows of a finished run after something changed (a cause fixed, a tool rerun, code modified), without redoing the audit. The imperative rules of section 1 and the recheck before each wave (section 6) hold. One Bash call per step.

1. Pick the run: `cqa_plan.py project "$PWD"`, then `cqa_plan.py runs "<slug>"`. Take the most recent run whose `lots` and `plan` are true. Ask « Utiliser le run <run_id> ? » with « Oui » and « Choisir un autre run » (then list the runs with their date and ask which one). No such run: say « Aucun run à relancer : lancez d'abord /gm:checklist-quality-audit. » and stop. Then check the context as in section 3, steps 3 and 4 (`context "$RUN" --check`, Basic Auth credentials asked again if `target.access` is `basic_auth`).
2. Read the Notion copy again, as the lead left it (statuses and comments):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

3. Run the diagnostic of section 5 for the tools only (toolbox, Chrome, SSH), as it may have changed since the audit, and update `available_tools` in `context.json`.
4. Open the rerun. It sets aside the files of an earlier rerun that was never closed (`history/<stamp>/abandoned/`), copies `findings/` and `notion/` into `history/<AAAA-MM-JJ-HHMMSS>/`, and prints `{"snapshot", "rerun", "abandoned"}`: `rerun` is the number `NN` of this rerun, `snapshot` is where the verdicts of before are kept. Tell the lead (in French) which files were set aside, if any.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-start "$RUN"
```

5. List the rows worth evaluating again:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-candidates "$RUN"
```

It prints `{"candidates", "lots", "tools", "estimate_min", "notes"}`. Each candidate has a `ref`, its `lot` (null when no lot can take the row: it cannot be rerun, say so), its `reasons` (`cause_levee`, `outil_relance`, `code_modifie`, `lead`) and their `details`. Show the `notes` too.

6. Present before anything is dispatched, in French: the rows grouped by reason (ref, title, detail), the lots and the tools that would run, and the estimated time (`estimate_min` minutes). Ask « Lancer cette relance ? » with « Lancer », « Modifier la liste » and « Annuler ». « Modifier la liste »: the lead removes refs (drop them from the list) or adds some in « Autre » (run the command again with `--refs CQ-12,CQ-40`, which adds them with the reason `lead`), then present again. « Annuler »: stop, nothing is written to Notion (the snapshot stays).
7. Start the workstation inhibitor as at the beginning of section 6 and stop it on every path that ends the rerun. Then relaunch the tools of `tools` that the lead kept (one call each, credentials inline only when the session holds the Basic Auth credentials). Each call rewrites `tools/manifest.json`. Toolbox not ready: `cqa_plan.py skip` for those tools and say so.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' bash "$S/cqa-toolbox.sh" run "$RUN" <tool>
```

8. Dispatch the lots that still have rows, with the same agent types, the same limits and the same fallbacks as section 6 (`references/lots.md`, section 3). The line, exactly, with the kept rows of that lot only:

```
Run dir: <run_dir>. Lot: <lot_id>. Follow-up: no. Rerun: <NN>. Rows: CQ-3, CQ-12. Follow your instructions.
```

The agent evaluates only those rows and writes `findings/<lot_id>-rerun-<NN>.json` with those rows only.

9. After each lot, with the rows dispatched to it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" validate "$RUN" <lot_id> --rerun <NN> --rows CQ-3,CQ-12
```

It checks only the rows written in the file, and refuses a row that was not dispatched. Errors: relaunch that agent once with the errors (`references/lots.md`, section 5), then validate again.

10. Close the rerun once every lot is validated or left. It moves the files still invalid out of `findings/` (into `history/`), so that their rows keep their previous finding, and records `NN` as closed. Tell the lead in French which files were discarded and that their rows keep their previous verdict:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-close "$RUN" <NN>
```

11. Build the plan, which takes the latest finding of each row, then go through section 7 and section 8 as for an audit (confirmation, writing, synthesis, scan, `done.json`):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN"
```

12. End with the rows whose verdict changed (before, from the snapshot of `rerun-start`, and after) and the rows still unchanged, in French.

## 11. Recette

Checks the corrections after the devs set rows to « Mis en place » in the copy. It reuses the mechanism of the rerun (section 10): same snapshot, same tools, same dispatch, same validation, same closing; only the choice of the rows differs. The imperative rules of section 1 and the recheck before each wave (section 6) hold. One Bash call per step.

1. Pick the run and check the context exactly as in section 10, step 1.
2. Read the Notion copy again and list the rows to check. A row is a candidate when it is « Mis en place » in the copy and the last status the skill wrote for it was something else (« À traiter », « Mise en place partielle », or no status written); a row the skill itself set to « Mis en place » (human validation) is not a fix and stays with the lead. The rows whose code changed since the audited commit are added. For each row the command keeps the last comments that are not the skill's (the devs' notes, the merge request link), at most 5, most recent first:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" recette "$RUN"
```

It writes `notion/recette.json`, `{"rows": [{"ref", "page_id", "status", "skill_last", "comments"}]}`, and prints the refs (`recette`) and those added for a code change (`code_modifie`). Exit codes as in section 8, step 1. A row without a known status of the skill is a candidate: the lead may drop it. The last status the skill wrote comes from the `value` of the status entries of `notion/applied.json`.

3. Run the diagnostic of section 5 for the tools, and also its launch condition « commit deployed in preprod » (`references/diagnostic.md`: the guard commands, then `cqa_plan.py deployed`, then `cqa_plan.py align`): the devs' fixes must be deployed before they are checked, so ask the same « Déployer puis relancer (recommandé) » or « Lancer quand même » when the preprod is behind, and never deploy. Update `available_tools` in `context.json`. Then open the rerun and list the lots of the candidate rows, as in section 10, steps 4 and 5, with the refs of `notion/recette.json`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-start "$RUN"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-candidates "$RUN" --refs CQ-3,CQ-12
```

4. Present before anything is dispatched, in French: for each row, ref, title, the status the skill had written (`skill_last`) and the devs' comments (with the merge request link), then the lots, the tools and the estimated time. Rows with a code change get a reminder that the preprod must run that code (the check of section 5). Ask « Lancer cette recette ? » with « Lancer », « Modifier la liste » and « Annuler », and handle the answers as in section 10, step 6. To drop or add rows, run the command of step 2 again with `--without CQ-3,CQ-12` (drops rows) and `--refs CQ-40` (adds rows the lead names, even if they are not candidates): it re-reads the copy and rewrites `notion/recette.json`. The options are not cumulative between runs: repeat every earlier `--without` and `--refs`. Then run `rerun-candidates` again with the refs of the rewritten file, and present again. « Annuler »: stop, nothing is written to Notion.
5. Relaunch the tools the lead kept and dispatch the lots as in section 10, steps 7 and 8, with the line, exactly (the kept rows of that lot only):

```
Run dir: <run_dir>. Lot: <lot_id>. Follow-up: no. Rerun: <NN>. Rows: CQ-3, CQ-12. Recette: yes. Follow your instructions.
```

The agent reads `notion/recette.json` (the devs' comments, the merge request link) before judging, then writes `findings/<lot_id>-rerun-<NN>.json` with those rows only.

6. After each lot, validate with the rows dispatched to it, as in section 10, step 9:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" validate "$RUN" <lot_id> --rerun <NN> --rows CQ-3,CQ-12
```

When every lot is validated or left, close the rerun and tell the lead which files were discarded:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-close "$RUN" <NN>
```

7. Build the plan with the recette option and the rerun number `NN`: the rows of `notion/recette.json` that are « Mis en place » in the copy and were judged again in `findings/<lot>-rerun-<NN>.json` may change the status a dev set; the others (no new finding, or a status nobody set to « Mis en place ») stay protected and are listed as not evaluated. Then go through section 7 and section 8 as for an audit (confirmation, writing, synthesis, scan, `done.json`). Every other row follows the write mode the lead chose:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN" --recette <NN>
```

The verdicts give: `ok` becomes « Ok / Recetté »; `ok_human` leaves the row « Mis en place » and assigns it to the lead (human judgement needed); `ko` becomes « À traiter » and `partial` « Mise en place partielle », the comment saying what is missing. The synthesis gets a `## Recette` section: validated rows, rows left to human validation, refused rows (with their Réf), rows not evaluated (`unknown`), blocking rows still open.

8. End, in French, with the validated rows, the rows left to the lead, the refused rows with what is missing, and the blocking rows still open.

## Exit codes

| Script | Codes |
|---|---|
| `cqa_notion.py` | 0 success; 2 environment or usage; 3 Notion API or network (token refused, page not shared with the integration); 6 schema of the copy not v2; 7 at least one write blocked; 8 write refused on the template |
| `cqa_plan.py` | 0 success; 2 usage or missing file (the message names the command to run first); 6 invalid `context.json` or findings; 7 secret found by `scan` |
| `cqa_ssh_ro.py` | 0 success; 1 remote failure (command or file missing on the server); 2 usage; 4 refused, to be confirmed by the lead |
| `cqa-keepawake.sh` | 0 success, also without any inhibitor on the workstation (warning only); 2 usage |
| `cqa-toolbox.sh` | 0 success; 1 at least one tool failed (the manifest is written); 2 usage or environment; `run` returns the code of the tool (3: target unreachable or 401) |
