---
name: checklist-quality-audit
description: Runs the agency pre-launch (PRE-MEL) quality checklist audit on a project. Checks the workstation, verifies that the preprod runs the audited code, evaluates every row of the project's Notion checklist copy from the code, the preprod, a Docker toolbox and the browser, then writes statuses and evidence comments to that copy and splits the remaining rows between the devs. Its fix mode corrects the open rows it knows how to fix, in git worktrees of the run, and opens one draft merge request per category and repo. Use when the lead dev asks to run the quality checklist or a PRE-MEL audit, or invokes /gm:checklist-quality-audit or /gm:cqa.
argument-hint: "[diagnostic | assign | rerun | recette | fix] [--phase \"MEL J0\"] [--dry-run]"
disable-model-invocation: true
---

# PRE-MEL quality checklist audit

You orchestrate the audit of a project's quality checklist before launch. You decide and you write. The sub-agents `gm:cqa-auditor` and `gm:cqa-browser` analyse, the Docker toolbox measures, and all of them exchange through files in the run directory `~/.cache/gm-audit/<slug>/<AAAA-MM-JJ-HHMM>/`.

The lead is needed at the start (questions, login in Chrome). Everything in between runs without them. In autonomous mode (section 4) the lead may leave once the questions of section 4 are answered: the audit, diagnostic included, never stops to ask, it sets aside what needs them (`cqa_plan.py defer`) and handles it with them at the end (section 8, step 5).

Load each reference with Read when its section starts, not before.

## 1. Imperative rules

- **Read-only (v1).** Never modify a project repo: no checkout, switch, stash, pull, commit, install, file edit, runner start. Never write on a server or in a database, never submit a form, never deploy. When something needs one of these, give the lead the exact command and let them run it. Exception, the `fix` mode only (section 12): it writes only in its own worktrees, under the run directory (`fix/<repo>/<slug>/wt`, on a branch of its own), and pushes only `feature/checklist-qa-fix-*` branches, after the lead's consent, always through `cqa_fix.py` and its sub-agents; the dev's checkout (working files, current branch, local `develop`, stash) never changes.
- **Notion.** Write only through `cqa_notion.py apply` and `cqa_notion.py comment-page`, after the lead's consent: the answer to the question of section 4, or the confirmation of section 7 when that answer is missing; in the fix mode, the answer to « Ajouter le lien des MR dans Notion ? » (section 12). Never through another tool, never on the reference template (exit 8). Never create an option, a row or a page; never delete anything.
- **Secrets (spec § 10).** Never print, write or hand to a sub-agent the value of `NOTION_TOKEN` or the preprod Basic Auth credentials. Never run `env`, `printenv` or `set`, never `echo` these variables: test their presence only, as in `references/context.md`.
- **Basic Auth credentials.** Asked only after a 401, kept in this session, never stored. They go only as inline environment variables (`GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>'`) to `cqa_plan.py probe-target --auth-env` and `cqa-toolbox.sh`, and only when the session holds the Basic Auth credentials (accepted by `probe-target --auth-env`). `GM_AUDIT_AUTH_PASS` alone also goes, the same way, to the secret checks of `cqa_notion.py apply`, `cqa_notion.py comment-page` and `cqa_plan.py scan`. Never in a file, a URL, Notion, a sub-agent prompt or Chrome (the lead types it there).
- **SSH (agency rule).** Ask which alias to use before any SSH command (aliases read in `~/.ssh/config`). Run every server command through `cqa_ssh_ro.py`, except the connection test `ssh -o BatchMode=yes <alias> true`. Exit 4 of the guard means "commande à faire confirmer par le lead": stop and ask, stating the impact, even if the lead asked to go faster (`references/server.md`). The rule holds in autonomous mode: only read commands run, and a write, a change, a restart or a command of uncertain impact (exit 4, or anything that needs the lead's confirmation) is never run, even in autonomous mode or confirmed in advance. It is recorded with `cqa_plan.py defer --kind ssh_refused` and put to the lead at the end.
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
- `fix`: go to section 12. Corrects the open rows the audit knows how to fix, in worktrees of the run, one draft merge request per category and repo. Its conventions (branches, commits, sub-agents, questions, guard rails) are in `references/fix.md`. With `--dry-run` nothing leaves the workstation (section 12); `--phase` does not apply to it.
- `--phase "<phase>"`: audit only the rows of that phase (`Cadrage`, `Build`, `Recette J-30`, `MEL J0`, `Post-MEL J+7`), stored as `phase_filter`. With `MEL J0` or `Post-MEL J+7`, section 4 asks whether the prod is live: the audit targets the prod only then.
- `--dry-run`: everything except the Notion writes, stored as `dry_run`. Section 4 does not ask the Notion consent. Section 7 asks nothing and section 8 skips its Notion steps (apply, page comment); the synthesis and the final scan still run.
- Anything else: show `/gm:checklist-quality-audit [diagnostic | assign | rerun | recette | fix] [--phase "MEL J0"] [--dry-run]` and stop.

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

When a run of `runs` has `fix_pending` true (a fix pass started on it and not finished), tell the lead once « Une passe fix est inachevée : relancez /gm:checklist-quality-audit fix. » with its `run_id`; it does not change the choice below.

A run is complete once its final scan has run: section 8 then writes `done.json` in it, and `runs` reports it as `done`. A run whose `done` is false is incomplete. Take the most recent incomplete run of `runs`, whatever its date, and ask « Le run <run_id> n'est pas terminé. » with « Reprendre ce run » and « Lancer un nouveau run »: « Reprendre ce run » comes first, as the recommended option, only when the run is of today (`run_id` starting with today's date); for an older run, give its date in the question and put « Lancer un nouveau run » first. To resume:

1. `RUN` is its `path`.
2. The current invocation wins over what the run stored. A `--phase` other than its `phase_filter` cannot reuse its lots and findings: say « Ce run a été lancé pour une autre phase : nouveau run. » and start a new run. Otherwise write the current `dry_run` in `context.json`. `mode` and `notion_write` are those of the invocation being resumed and are reused as stored, without asking again (a new invocation asks both questions again: section 4, section 10 step 6, section 11 step 4; a run without them is interactive and section 7 asks the consent); `deferred.json` keeps its open entries, which section 8, step 5 handles.
3. Run `cqa_plan.py context "$RUN" --check`. On errors, ask « Le contexte du run <run_id> est invalide (<erreurs>). » with « Lancer un nouveau run » and « Arrêter ». `warnings` (a relative `ssh.app_dirs` folder left by an older run) do not block: say them to the lead in one line and ask that folder again before any server command.
4. If `target.access` is `basic_auth`, ask the Basic Auth credentials again (they are never stored) and check them with `probe-target --auth-env`, as in section 4.
5. Restart at the first stage it lacks:
   - `context` false: section 4, in this folder.
   - `lots` false: section 5 (the diagnostic is redone: the workstation may have changed).
   - `lots` true and `plan` false: section 6. A lot whose `cqa_plan.py validate` passes is not dispatched again. Toolbox: no `tools/` folder, launch it again; `tools/` without `tools/manifest.json`, rebuild the manifest with `cqa_plan.py manifest "$RUN"` and relaunch only the missing or failed tools (`references/lots.md`).
   - `plan` true: section 7. `apply` skips the writes already listed in `notion/applied.json` (the plan is rebuilt with the same content, so with the same `plan_id`). When `notion/plan.json` carries `recette.rerun`, every rebuild of the plan passes `--recette <that NN>`.

An interrupted run may have left its inhibitor running: it runs in its own session and outlives the Claude session. `start` reuses a live one instead of launching another. When the run restarts at section 7, run `start` as at the beginning of section 6 (section 6 does it itself): it keeps the inhibitor still running or launches a new one.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" start "$RUN"
```

Earlier runs: when `runs` lists at least one run of the project (done or not, of any date, other than the incomplete run that the lead resumes), offer to start from its answers. Ask « Un run existe déjà pour ce projet. » with « Réutiliser le contexte du run <run_id> » (the most recent one; another run in « Autre ») and « Repartir de zéro ». After creating the new run folder (below), reuse it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" reuse-context "$RUN" "<path du run réutilisé>"
```

Run `cqa_plan.py project "$PWD"` first (section 3 above) and write its repos into `$RUN/context.json` with the other fixed keys (step 8 below fills `schema`, `run_id`, `plugin_version`, `project`, `technos`, `phase_filter`, `dry_run` and `available_tools` around the copied keys). `reuse-context` copies the answers that hold from one run to the next into that file and prints the list taken: `notion` (without `bot_id`), `lead`, `team`, `target` (`preprod_url` and `prod_url` only), `ssh`, `test_accounts`, `key_pages`, `sites`, and from `repos` only the `preprod_branch` and the `third_party_paths` (the repos stay those just read from the folder; an old run's repos are used only when there are none). `prod_live`, `access`, `mode`, `notion_write`, `available_tools`, `bot_id` and what the diagnostic recomputes (`audited_sha`, `deployed_sha`, `aligned`) are never taken. Then, in one message, recap the reused values to the lead (SSH alias, preprod URL, Notion copy, key pages, excluded third-party plugins) and go on. Section 4 asks only what may have changed: `cqa_notion.py whoami` and `cqa_notion.py schema` run again (into `$RUN/schema.json`, no question), the probe of the target (`access`, and the Basic Auth credentials, never stored), `prod_live` (only with `--phase "MEL J0"` or `--phase "Post-MEL J+7"`, then the key pages again on the host of `prod_url`), the key pages that answer 404 (`cqa_plan.py key-pages-check`), and the launch questions (mode, consent). « Repartir de zéro »: section 4 in full.

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
6. Key pages: one per template, 10 at most, proposed from the sitemap, the links of the home page and the routes of the code (WordPress: the « Formulaire » page found through the guard, `references/context.md`, section 6), validated by the lead. Other sites (multisite), still before the launch questions: detect the other domains, then ask for each one its back-office (common or its own) and its SSO login (`references/context.md`, section 6.1). WordPress: in a new run, first write a minimal `$RUN/context.json` holding `repos` (from `project`), `target` and `sites` (`wp-plugins` reads it, and a new run writes the complete file only at step 8, which completes and checks it); on the reuse path the file already exists: add `sites` to it, never overwrite it. Then list the plugin folders with `cqa_plan.py wp-plugins` and ask « Plugins maison à analyser ? » ; the third-party ones go into `repos[].third_party_paths` (`references/context.md`, section 6.1.1). Without `PROJECT.md`, close this step by offering to generate one from the answers (`references/context.md`, section 6.2).
7. Mode and Notion consent, two questions, one at a time, with AskUserQuestion (neither is asked with `diagnostic`, which runs only sections 3 to 5; the second one is not asked with `--dry-run`, which writes nothing). Each invocation asks them again and overwrites both keys: a value stored by an earlier invocation never counts (a resume reuses its own):
   - « Mode 100 % autonome ? » with « Oui, je m'absente » and « Non, je reste disponible ». Store `mode` to `"autonome"` for the first, `"interactif"` for the second.
   - « Écrire dans la copie Notion à la fin de l'audit ? » with « Tout écrire », « Sans toucher aux statuts posés par un humain » and « Ne rien écrire ». Store `notion_write` to `"all"`, `"keep_human"` or `"none"` (`null` when not asked). The copy does not change during the audit, so this consent holds for section 7.
8. Write `context.json`, then check it until it passes:

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
| Key pages and dead links of the home page | `cqa_plan.py key-pages-check`; a 404 is a gap | HTTP |
| Claude in Chrome connected | a tab opened on the preprod (`references/browser.md`) | Navigateur |
| Docker, registry, images | `cqa-toolbox.sh doctor` | Audit web, Crawler |
| SSH alias reachable | `ssh -o BatchMode=yes <alias> true` | SSH serveur |
| Application CLI on the server (drush, wp, artisan) | through the guard | BDD |
| Test accounts | answer of section 4 | Comptes de test |
| Public preprod | no protection in `PROJECT.md` and access `open` | Service externe |
| Notion token, template and copy | section 4 | global prerequisite |

Preprod behind `develop`: list the missing commits and ask « Déployer puis relancer (recommandé) » or « Lancer quand même ». Never deploy. Deployed commit not found: the audit goes on only if the lead confirms the alignment.

In autonomous mode (`mode` is `autonome`) the diagnostic asks nothing: a launch-condition gap (preprod behind `develop`, deployed commit not found) is recorded with `cqa_plan.py defer --kind gap --detail "<texte en français>"` and the audit goes on as with « Lancer quand même » (never deploy); an alignment that would need the lead's confirmation is recorded the same way and treated as not aligned (`aligned` false, never `null`, so that the synthesis reports the gap); every other question of `references/diagnostic.md` takes the non-acting default it gives there, with a gap; « Lancer l'audit » is implied. Interactive mode is unchanged.

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

Show the table of checks, the coverage and the gains as `references/diagnostic.md` words them. `evaluable` already includes `evaluable_human`: never add them. A selected row that needs an account with a restricted role (CQ-115): `references/diagnostic.md`, section 10.1, before « Lancer l'audit ». With `diagnostic`, stop here. Otherwise ask « Lancer l'audit » or « Compléter mon poste d'abord » (then stop: a later launch offers to resume this run); in autonomous mode do not ask, the audit starts.

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

3. Dispatch the first lots of step 4. The front browser lots (`needs_session` false) run without a session, before any login. Then the login pause: open the preprod (and the back-office) in new Chrome tabs and wait for the lead to log in (`references/browser.md`), before the `-session` lots of step 5. Skip the pause when "Navigateur" is unavailable. In autonomous mode the lots whose `needs_session` is true (back-office, test accounts) are held: they are not dispatched during the run, and there is no login pause here: the login pause happens last, at the end of the audit (section 8, step 5), and the held lots are dispatched then. Once every other lot is validated, record one `--kind login` entry (below) with the refs of the held lots.
4. The `server` lot (always `gm:cqa-auditor`; say in the dispatch when "SSH serveur" or "BDD" is unavailable) and the `code` lots whose `needs_toolbox` is false, 4 sub-agents at most at once.
5. The browser lots, one at a time, once the `server` lot is done, the lots with `needs_session` false first, then (after the login pause of step 3) the `-session` lots: `gm:cqa-browser`. A return that starts with `CQA_BROWSER_UNAVAILABLE:` switches to the fallback: read `${CLAUDE_SKILL_DIR}/../../agents/cqa-browser.md` and run it yourself in this session, template by template, for this lot and the next browser lots. "Navigateur" unavailable from the start: give the browser lots to `gm:cqa-auditor`.
6. Once the toolbox has finished, the `-outils` lots (same agents, same limits). They hold the rows that need a toolbox tool, named by their tools or by their `tooling_hint`. A `-session-outils` lot waits for the login as well.
7. After each lot, `cqa_plan.py validate "$RUN" <lot>`. On errors, relaunch that sub-agent once, with the list of errors; then leave it (its invalid rows count as `unknown`).
8. Follow-up: `cqa_plan.py probes "$RUN" --followup`. With probes, `cqa-toolbox.sh run "$RUN" http --followup`, then relaunch once each lot it lists with `Follow-up: yes`, and validate with `cqa_plan.py validate "$RUN" <lot> --followup`.

Before each new wave of lots (steps 4, 5, 6 and 8), recheck the access, because the workstation may have slept or changed network:
- run `cqa_plan.py probe-target` on the target (`references/context.md`, section 3; the prod URL for a prod run) without credentials, and compare its `access` with the one in `context.json` (also the answer without credentials: `basic_auth` for a preprod behind Basic Auth);
- when the session holds the Basic Auth credentials, run it once more with `--auth-env`: any answer other than `open` means the credentials no longer pass;
- when "SSH serveur" is available, run `ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> true` (`references/diagnostic.md`, section 7).

If the `access` without credentials differs from the one in `context.json`, the check with credentials is not `open`, or the SSH test fails, launch no new lot. Tell the lead in French what changed (« La préprod répond maintenant <access> au lieu de <access du contexte> », « La préprod refuse maintenant les identifiants Basic Auth » or « Connexion SSH impossible avec <alias> »), then ask with « Reprendre » (after the lead restored the access: recheck) and « Continuer quand même » (launch the wave; note it in the « remarques de la synthèse »: the rows concerned may end `unknown`).

Before each browser lot (steps 3 and 5), call `tabs_context_mcp`. If it fails, ask the lead to reconnect Claude in Chrome, with « Reprendre » and « Continuer sans navigateur » (`references/browser.md`).

« Continuer quand même » and « Continuer sans navigateur » hold for the rest of the run, unless the lead says otherwise: at the next waves, ask again only for a change the lead has not answered yet, and after « Continuer sans navigateur » give every later browser lot to `gm:cqa-auditor` without a new check.

**Autonomous mode** (`mode` is `autonome`): never ask the lead anything in this section. Set aside what would have needed them, with one call per entry, and go on:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" defer "$RUN" --kind <login|ssh_refused|gap|question> --detail "<texte en français pour le lead>" --refs CQ-3,CQ-12
```

- `--kind ssh_refused`: a command refused by the guard (exit 4) or needing the lead's confirmation. Never run it. The rows concerned stay `unknown`. A refusal a sub-agent reports in its return (the command, its refs, its impact) is recorded the same way, one entry per refused command: `--refs <its refs> --detail "<commande> Impact : <impact>"`. In interactive mode, relay it to the lead as before.
- `--kind gap`: a deviation found on the way (access that changed at the check before a wave, SSH that fails; the preprod behind `develop` is checked in section 5): note it, launch the wave anyway with what is still possible; the rows concerned may end `unknown` and the synthesis remarks say so. Chrome disconnected at `tabs_context_mcp`: `--kind gap`, and the browser lots go to `gm:cqa-auditor` as with « Continuer sans navigateur ».
- `--kind login`: the login pause, recorded once (preprod and back-office in the same entry) with the refs of the held session lots. Those lots have no finding yet: they are dispatched at the end of the audit as normal first-round lots (section 8, step 5), after the lead's login or, if they decline, without a session. In a rerun or a recette (section 10, step 8) the held lots already have their first-round file: the entry lists their kept rows, which join the rerun of section 8, step 5 instead.
- `--kind question`: any other question the orchestrator would have asked.
- `--detail` is short, in French, without any secret; `--refs` lists the rows left `unknown` because of it, when known.

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

1. Interactive mode: show the summary: count per final status, blocking rows not compliant, rows not evaluated, and the explicit list of statuses set by a human that the audit would change. When `mode` is `autonome` there is no presentation: go on to the write mode, the summary comes as a recap after the write (section 8, step 1).
   In a rerun or a recette (sections 10 and 11) whose `notion_write` is `all`, when the plan lists statuses set by a human that it would change (`summary.human_overrides`): in interactive mode, after that list, ask once « <n> statuts posés par un humain seront remplacés (liste ci-dessus). Continuer ? » with « Continuer », « Sans toucher aux statuts posés par un humain » and « Annuler ». « Continuer »: write them, as chosen at launch. « Sans toucher aux statuts posés par un humain »: write `keep_human` in `notion_write` and follow that bullet of step 3 (rebuild with `--skip-human-overrides`, keeping `--recette <NN>` in a recette). « Annuler »: nothing is written; release the workstation (below) and stop. In autonomous mode a rerun or a recette never writes a status set by a human: do not ask, write `keep_human` in `notion_write` and rebuild the same way; the remark of section 8, step 3 lists the kept statuses, and in a recette the synthesis shows the judged ones under « Statut humain conservé ». The recette exemptions (rows set to « Mis en place » by a dev and judged again) are not human overrides: they are written in both cases.
2. With `--dry-run`: no question, no Notion write. Go to section 8, step 2.
3. Otherwise the write mode is the consent given by the current invocation at its start (section 4, section 10 step 6, section 11 step 4), kept in `notion_write`; a value left by an earlier invocation never counts, and a resume reuses the stored one: `all` is « Tout écrire », `keep_human` is « Sans toucher aux statuts posés par un humain », `none` is « Ne rien écrire ». Never ask when `notion_write` is set: the copy does not change during the audit. Ask « Écrire le plan dans la copie Notion ? » with « Tout écrire », « Sans toucher aux statuts posés par un humain » (only when there are some) and « Ne rien écrire », only when `notion_write` is `null` (a run created before the consent question existed), and store the answer in `notion_write`. In interactive mode the summary of step 1 is shown first even when the consent is known.
   - « Tout écrire »: section 8, step 1.
   - « Ne rien écrire »: as with `--dry-run`, section 8, step 2.
   - « Sans toucher aux statuts posés par un humain »: keep the list of those statuses, rebuild the plan with the command below so that the synthesis matches what is written, then section 8, step 1, with `--skip-human-overrides`. In a recette (`notion/plan.json` carries `recette.rerun`), the rebuild keeps `--recette <NN>`.

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

**1. Apply**, only when section 7 settled on « Tout écrire » or « Sans toucher aux statuts posés par un humain » (`all` or `keep_human`; add `--skip-human-overrides` for the second):

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

In autonomous mode, once the write is done, show the recap that section 7 skipped (counts per status, blocking rows not compliant, rows not evaluated, human statuses changed, operations blocked).

**2. Synthesis**, on every path that reaches this step (written, `--dry-run`, « Ne rien écrire »):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" synthesis "$RUN"
```

It writes `synthese.md` and `notion/page-comment.txt`.

**3. Remarques**, on the same paths. Write what the scripts cannot know to `<run_dir>/remarques.md` (one bullet per line, no heading), or write nothing when there is none. Never edit `synthese.md`: the synthesis is regenerated, it injects `remarques.md` as its last section « Remarques » and never erases it, so run step 2 again after writing the file. Write:
- an alignment the lead confirmed without a known deployed commit, and, in autonomous mode, an alignment not confirmed (the repo is treated as not aligned);
- for « Sans toucher aux statuts posés par un humain », the human statuses kept: « CQ-<ref> : « <before> » conservé, l'audit proposait « <after> » »;
- the operations blocked at step 1: « CQ-<ref> (<kind>) non écrit : <raison> »;
- for `--dry-run` and « Ne rien écrire »: « Rien n'a été écrit dans Notion : le plan reste dans notion/plan.json. »;
- the notes asked by section 6 (« Continuer quand même »).

The entries set aside in autonomous mode (gaps, `ssh_refused`, questions, logins) need no remark: the synthesis itself lists every entry of `deferred.json` under « Décisions mises de côté », open or resolved.

**4. Page comment**, only when step 1 ran and wrote or found something (`done` plus `skipped` above 0). Never with `--dry-run` or « Ne rien écrire ». When `cqa_plan.py deferred "$RUN"` lists open entries (in either mode), skip it now: it is written once, after the end block (step 5).

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_notion.py" comment-page "$RUN" "$RUN/notion/page-comment.txt"
```

Same exit codes as step 1. Without `copy_page_id` it writes nothing and says so. Exit 3 after one more try once the cause is fixed, or exit 7: say that the page comment is missing (its text is in `notion/page-comment.txt`) and go on.

**5. Deferred decisions and rerun**, in both modes, whenever `deferred.json` has open entries (an interactive run records some too: the restricted-role account, `references/diagnostic.md`, section 10.1), after steps 1 to 4 (with `--dry-run` or `none`, the same, minus the writes). Without any open entry, go to step 6.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" deferred "$RUN"
```

It prints `{"items"}`, the open entries of the audit (`id`, `kind`, `detail`, `refs`). The kinds starting with `fix_` belong to the fix mode and are never listed here (`deferred --fix` lists them); ignore them in this step. The lead is back at this point: the interactive rules of section 6 apply to what is dispatched here (the checks before a wave, the `tabs_context_mcp` call before a browser lot, the answers « Reprendre » and « Continuer sans navigateur »). Go through the entries with the lead, one question at a time, in French, with AskUserQuestion. The `login` entry comes first, then the others:

- `login`: call `tabs_context_mcp` first (it fails: ask the lead to reconnect Claude in Chrome, as in section 6), open the preprod (and the back-office) in new Chrome tabs (`references/browser.md`), then ask « Connectez-vous dans Chrome, dans les onglets ouverts : Basic Auth de la préprod si elle le demande, puis vos comptes de test. N'utilisez pas cette fenêtre Chrome pendant les lots navigateur. » with « C'est fait » and « Passer ». Then the held lots run, under the session, or without a session on « Passer » or when the lead cannot log in (the agents then write `unknown` with the reason, so every row still has a finding):
  - Held lots of an audit (no `findings/<lot_id>.json` yet): dispatch them as normal first-round lots (`Run dir: <run_dir>. Lot: <lot_id>. Follow-up: no.`, no rerun number), validate each with `cqa_plan.py validate "$RUN" <lot_id>` and relaunch once on errors as in section 6, step 7. Then run the probe follow-up of section 6, step 8, and relaunch with `Follow-up: yes` only the held lots among the `lots` that `probes --followup` lists: the other lots already had their only follow-up.
  - Held lots of a rerun or a recette (their `findings/<lot_id>.json` exists: section 10, step 8 held them): never as first-round lots, which would replace the first-round file. Their kept rows, the `refs` of the entry, join the rerun below: add them to its `--refs` and dispatch them with the `Rerun:` line of that rerun.
  Close the tabs once those lots are done.
- `ssh_refused`: as `references/server.md` (exit 4): the command, its impact, « Passer » and the lead's result in « Autre ». The command is never run by the skill; a result the lead gives is a short fact for the dispatch, never raw output in the run directory.
- `gap`: say what was noted and ask « Relancer les lignes concernées » or « Passer ».
- `question`: ask the question as recorded. For a restricted-role account (`refs` with the row), ask « Connectez-vous dans Chrome avec le compte à rôle restreint (<rôle>), puis répondez. » with « C'est fait » and « Passer »: after « C'est fait » the rows are rerun (below) under that session, after the other session lots; « Passer »: the row stays `unknown`.

Close an entry only once it is settled: a `login` entry when its held lots are validated (in a rerun or a recette, when the rerun below is closed), the others when their rerun is closed (an entry passed by the lead is closed at once):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" deferred "$RUN" --resolve <id>
```

Then, without presenting anything to the lead, rerun and write again, with the mechanism of section 10 (steps 4 and 7 to 10), in this order. The rows to rerun are the `refs` of the entries answered (« C'est fait », a result, « Relancer »), plus, in a rerun or a recette, the refs of the `login` entry; the `login` entry of an audit adds none (its held lots ran above as first-round lots). Restricted-role `question` entries (the `question` bullet above) are handled last: ask them after every other entry, and rerun their rows in a rerun of their own (1 to 4 again), after the rerun of the other answered entries is closed, so that back-office rows never run under the restricted session.

1. Open the rerun when there are rows to rerun, and relaunch the tools that need it (section 10, step 7):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-start "$RUN"
```

2. List the rows with those refs, then keep only the candidates whose reasons include `lead` (the refs you gave); a row without a lot is dropped, say so:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-candidates "$RUN" --refs CQ-3,CQ-12
```

3. Dispatch the lots with `Rerun: <NN>. Rows: CQ-3, CQ-12.` (an extra sentence gives the lead's fact when there is one, `references/lots.md`), then validate each lot with its rows:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" validate "$RUN" <lot_id> --rerun <NN> --rows CQ-3,CQ-12
```

4. Close the rerun with the union of the rows dispatched (section 10, step 10). Exit 2 with « aucun fichier de la relance NN » (no lot wrote anything): do not close it, tell the lead « Aucune ligne n'a été réévaluée. » and go on with 5:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-close "$RUN" <NN> --rows CQ-3,CQ-12
```

5. Rebuild the plan with the write mode of `notion_write` (`--skip-human-overrides` for `keep_human`); in a recette, pass `--recette <recette.rerun of notion/plan.json>` (the rerun of the recette, never the number of this rerun: the plan counts the rows this later closed rerun judged):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN"
```

6. Write it again without presenting it (`all` or `keep_human`; the comments of the rewritten rows are replaced), run steps 2 to 4 again (the page comment is written here, once), and show a second recap:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_notion.py" apply "$RUN"
```

When no row is left to rerun (the lead answered « Passer » to everything, or no answered row has a lot), skip 1 to 4 only: no rerun is dispatched or closed (a rerun opened at 1 with nothing to dispatch has no file and needs no closing). After a `login` entry, whatever its answer, always run 5 and 6: its held lots wrote new findings that the plan must take. Without a `login` entry and without rows, skip 5 and 6 too: run the synthesis again (step 2, as below), run step 4 now (page comment), then go to step 6.

Rows whose entry was passed stay `unknown`. Never run a command the guard refused.

Once the entries are handled (and any rerun is written), run the synthesis (step 2) again, so that « Décisions mises de côté » shows their fresh statuses, then step 4 as said above.

**6. Final scan**, on every path that reached step 2:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_plan.py" scan "$RUN"
```

Anti-secret check of the run directory (the cached template fiches in `fiches/` are excluded). Exit 7: give the lead the files and labels found, never a value.

**7. Completion.** Write `done.json` in the run directory with Write: `{"run_id": "<run_id>", "scan_ok": <true or false>}`. The run is now complete (section 3).

**8. Release the workstation.** The audit is over:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" stop "$RUN"
```

**9. End.** Show `synthese.md`. After a write, end with « Relisez les statuts et les commentaires dans Notion, puis lancez /gm:checklist-quality-audit assign pour répartir les lignes restantes. »

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

Until the questions of step 6, this invocation is interactive, whatever `mode` an earlier invocation stored: steps 1 to 5 (the run, the diagnostic, the presentation) ask the lead. With `--dry-run`, write `dry_run` in `context.json` at step 1 (true, and false without `--dry-run`, as section 3, step 2 does), and the consent question is not asked at step 6, as in section 4, step 7 (`notion_write` `null`): sections 7 and 8 then write nothing to Notion.

1. Pick the run: `cqa_plan.py project "$PWD"`, then `cqa_plan.py runs "<slug>"`. Take the most recent run whose `lots` and `plan` are true. Ask « Utiliser le run <run_id> ? » with « Oui » and « Choisir un autre run » (then list the runs with their date and ask which one). No such run: say « Aucun run à relancer : lancez d'abord /gm:checklist-quality-audit. » and stop. Then check the context as in section 3, steps 3 and 4 (`context "$RUN" --check`, Basic Auth credentials asked again if `target.access` is `basic_auth`). When `runs` gives that run `write_pending` true (its last plan was to be written and `notion/applied.json` lacks some of its operations: a write that stopped, exit 3, or an operation blocked), ask « L'écriture de la dernière relance est incomplète. » with « Reprendre l'écriture » and « Ouvrir une nouvelle relance ». « Reprendre l'écriture »: start the inhibitor as at the beginning of section 6, then run section 8, step 1 with the write mode stored in `notion_write` (`--skip-human-overrides` for `keep_human`), without rebuilding the plan (`apply` skips what the journal holds), then section 8 from step 2; this invocation ends there. « Ouvrir une nouvelle relance »: go on with step 2.
2. Read the Notion copy again, as the lead left it (statuses and comments):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

3. Run the diagnostic of section 5 for the tools only (toolbox, Chrome, SSH), as it may have changed since the audit, and update `available_tools` in `context.json`. Also run `cqa_plan.py repo-state` on each repo (`references/diagnostic.md`, section 1): a rerun judges the code as it is now, so a repo not on `develop` up to date gets the same question, « C'est corrigé, revérifier » or « Continuer avec l'état actuel ». `audited_sha` stays the commit of the audit (the evidence links and the code changes compare with it); the comments of the rows judged again carry the date of the rerun in their header instead of the date of the audit.
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

It prints `{"candidates", "lots", "tools", "estimate_min", "notes"}`. `tools` lists the tools already relaunched since the findings (their results are newer), not the tools to relaunch. Each candidate has a `ref`, its `lot` (null when no lot can take the row: it cannot be rerun, say so), its `reasons` (`cause_levee`, `outil_relance`, `code_modifie`, `lead`) and their `details`. Show the `notes` too.

6. Present before anything is dispatched, in French: the rows grouped by reason (ref, title, detail), the lots, the tools already relaunched (`tools`), the estimated time (`estimate_min` minutes), then ask which tools to relaunch before the rows are judged again (for example viewport after a front fix, lighthouse after a performance fix; crawl only when the site changed). Offer `stylelint` and `deps` only when the run has no `tools/<tool>/result.json` for them and `agence_1_2` is true (diagnostic of step 3): a run made with a toolbox before 1.2 never ran them. Never offer them when they already ran: they read the commit at `repos[].audited_sha`, so relaunching them reads the same commit and is useless. Their results describe `repos[].audited_sha`, so the rule of the auditor still holds: when the repo HEAD differs from `audited_sha`, CQ-40, CQ-66 and CQ-113 are checked again on the current files, not judged from them. When the lead names `stylelint` or `deps` and a WordPress repo (`stack` `wordpress`) of the run is without `third_party_paths`, before the relaunch run `python3 "$S/cqa_plan.py" wp-plugins "$RUN"` (the `context.json` of the run already exists) and ask « Plugins maison à analyser ? » with the rules of `references/context.md`, section 6.1.1 (the lead is present: this is part of the interactive part of the rerun, before « Lancer »); write the plugins left unchecked into `repos[].third_party_paths` of their repo in `context.json`, then run `context "$RUN" --check`. No plugin found: no question. Ask « Lancer cette relance ? » with « Lancer », « Modifier la liste » and « Annuler ». « Modifier la liste »: the lead removes refs (drop them from the list) or adds some in « Autre » (run the command again with `--refs CQ-12,CQ-40`, which adds them with the reason `lead`), then present again. « Annuler »: stop, nothing is written to Notion (the snapshot stays). Once the list is accepted (« Lancer »), ask the two launch questions of section 4, step 7, one at a time, and overwrite `mode` and `notion_write` in `context.json`: this invocation's answers rule sections 7 and 8, whatever an earlier invocation stored.
7. Start the workstation inhibitor as at the beginning of section 6 and stop it on every path that ends the rerun. Then relaunch the tools the lead named in step 6 (`stylelint` and `deps` follow the same rule as step 6: only when the run has no result for them, `agence_1_2` is true and the lead named them, after the plugins question when it applies; one call each, credentials inline only when the session holds the Basic Auth credentials). Each call rewrites `tools/manifest.json`. Toolbox not ready: `cqa_plan.py skip` for those tools and say so.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' bash "$S/cqa-toolbox.sh" run "$RUN" <tool>
```

8. Dispatch the lots that still have rows, with the same agent types, the same limits and the same fallbacks as section 6 (`references/lots.md`, section 3). In autonomous mode the lots whose `needs_session` is true are held as in section 6, step 3: none of them is dispatched now, and once every other lot is validated record one `--kind login` entry whose `--refs` are their kept rows; section 8, step 5 reruns those rows after the login (they join its rerun). The line, exactly, with the kept rows of that lot only:

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

10. Close the rerun once every lot is validated or left, with `--rows` set to the union of the rows dispatched in this rerun (every lot). It moves the files still invalid out of `findings/` (into `history/`), and a file that writes a row not dispatched, so that their rows keep their previous finding, and records `NN` as closed. Tell the lead in French which files were discarded and that their rows keep their previous verdict:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-close "$RUN" <NN> --rows CQ-3,CQ-12
```

Exit 2 with « aucun fichier de la relance NN » (no lot wrote anything, every agent failed): do not close it; tell the lead « Aucune ligne n'a été réévaluée : les verdicts restent ceux d'avant la relance. », then build the plan without closing it (step 11).

11. Build the plan, which takes the latest finding of each row, then go through section 7 and section 8 as for an audit (confirmation, writing, synthesis, scan, `done.json`):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN"
```

12. End with the rows whose verdict changed (before, from the snapshot of `rerun-start`, and after) and the rows still unchanged, in French.

## 11. Recette

Checks the corrections after the devs set rows to « Mis en place » in the copy. It reuses the mechanism of the rerun (section 10): same snapshot, same tools, same dispatch, same validation, same closing; only the choice of the rows differs. The imperative rules of section 1 and the recheck before each wave (section 6) hold. One Bash call per step.

Until the questions of step 4, this invocation is interactive, whatever `mode` an earlier invocation stored: steps 1 to 3 and the presentation ask the lead. With `--dry-run`, write `dry_run` in `context.json` at step 1 (true, and false without `--dry-run`), and the consent question is not asked at step 4, as in section 4, step 7 (`notion_write` `null`): sections 7 and 8 then write nothing to Notion.

1. Pick the run and check the context exactly as in section 10, step 1, including the question on a run whose `write_pending` is true.
2. Read the Notion copy again and list the rows to check. A row is a candidate when it is « Mis en place » in the copy and the last status the skill wrote for it was something else (« À traiter », « Mise en place partielle », or no status written); a row the skill itself set to « Mis en place » (human validation) is not a fix and stays with the lead. The rows whose code changed since the audited commit are added. For each row the command keeps the last comments that are not the skill's (the devs' notes, the merge request link), at most 5, most recent first:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" recette "$RUN"
```

It writes `notion/recette.json`, `{"rows": [{"ref", "page_id", "status", "skill_last", "comments", "mr_links"}]}` (`mr_links` lists the merge request URLs of the fix pass for the row, empty without any, and the agents read them as the devs' notes), and prints the refs (`recette`) and those added for a code change (`code_modifie`). Exit codes as in section 8, step 1. A row without a known status of the skill is a candidate: the lead may drop it. The last status the skill wrote comes from the `value` of the status entries of `notion/applied.json`.

3. Run the diagnostic of section 5 for the tools, and also its launch condition « commit deployed in preprod » (`references/diagnostic.md`: the guard commands, then `cqa_plan.py deployed`, then `cqa_plan.py align`): the devs' fixes must be deployed before they are checked, so ask the same « Déployer puis relancer (recommandé) » or « Lancer quand même » when the preprod is behind, and never deploy. Update `available_tools` in `context.json`. Run `cqa_plan.py repo-state` on each repo as in section 10, step 3 (« C'est corrigé, revérifier » or « Continuer avec l'état actuel »; the comments of the rows judged again carry the date of the rerun). Then open the rerun and list the lots of the candidate rows, as in section 10, steps 4 and 5, with the refs of `notion/recette.json`, and keep only the candidates whose reasons include `lead` (those refs; the rows whose code changed are already in `notion/recette.json`): `rerun-candidates` also lists every row of the run still unknown, which the recette does not check. A row without a lot is dropped, say so:

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

4. Present before anything is dispatched, in French: for each row, ref, title, the status the skill had written (`skill_last`) and the devs' comments (with the merge request link), then the lots, the tools already relaunched and the estimated time, and ask which tools to relaunch (`stylelint` and `deps` as in section 10, step 6: offered only when the run has no `tools/<tool>/result.json` for them and `agence_1_2` is true, never when they already ran, since they read `repos[].audited_sha`; their results describe that commit, so the auditor still checks CQ-40, CQ-66 and CQ-113 itself on the current files when HEAD differs. When the lead names one and a WordPress repo is without `third_party_paths`, ask « Plugins maison à analyser ? » first, as in section 10, step 6 (`wp-plugins`, `references/context.md`, section 6.1.1, then `context "$RUN" --check`)). Rows with a code change get a reminder that the preprod must run that code (the check of section 5). Ask « Lancer cette recette ? » with « Lancer », « Modifier la liste » and « Annuler », and handle the answers as in section 10, step 6. To drop or add rows, run the command of step 2 again with `--without CQ-3,CQ-12` (drops rows) and `--refs CQ-40` (adds rows the lead names, even if they are not candidates): it re-reads the copy and rewrites `notion/recette.json`. The options are not cumulative between runs: repeat every earlier `--without` and `--refs`. Then run `rerun-candidates` again with the refs of the rewritten file, keep only the candidates whose reasons include `lead` as in step 3, and present again. « Annuler »: stop, nothing is written to Notion. Once the list is accepted (« Lancer »), ask the two launch questions of section 4, step 7, one at a time, and overwrite `mode` and `notion_write` in `context.json`: this invocation's answers rule sections 7 and 8, whatever an earlier invocation stored.
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

When every lot is validated or left, close the rerun with the union of the rows dispatched (as in section 10, step 10) and tell the lead which files were discarded:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" rerun-close "$RUN" <NN> --rows CQ-3,CQ-12
```

Exit 2 with « aucun fichier de la relance NN » (no lot wrote anything): do not close it; tell the lead « Aucune ligne n'a été recettée : aucun lot n'a rien écrit. », build the plan without `--recette` (no rerun of this recette is closed) and go through sections 7 and 8 as below; the synthesis then has no `## Recette` section.

7. Build the plan with the recette option and the rerun number `NN`: the rows of `notion/recette.json` that are « Mis en place » in the copy and were judged again in `findings/<lot>-rerun-<NN>.json`, or in the file of a later closed rerun (the deferred rerun of section 8, step 5), may change the status a dev set; the others (no new finding, or a status nobody set to « Mis en place ») stay protected. Every rebuild of the plan keeps `--recette <NN>` (the rebuild of section 7, the rebuild of section 8 step 5 and the resume of section 3); `<NN>` is the rerun of this recette, closed in reruns.json, never the number of a later rerun. The synthesis lists the rows not judged as « Non évaluées », and the judged rows whose human status was kept (« Sans toucher ») as « Statut humain conservé ». Then go through section 7 and section 8 as for an audit (confirmation, writing, synthesis, scan, `done.json`). Every other row follows the write mode the lead chose:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN" --recette <NN>
```

The verdicts give: `ok` becomes « Ok / Recetté »; `ok_human` leaves the row « Mis en place » and assigns it to the lead (human judgement needed); `ko` becomes « À traiter » and `partial` « Mise en place partielle », the comment saying what is missing. The synthesis gets a `## Recette` section: validated rows, rows left to human validation, refused rows (with their Réf), rows not evaluated (`unknown`), blocking rows still open.

8. End, in French, with the validated rows, the rows left to the lead, the refused rows with what is missing, and the blocking rows still open.

## 12. Fix

Corrects the open rows of an audited run that the skill knows how to fix, in git worktrees of the run directory, and delivers one draft merge request per key (one checklist category in one repo). Read `references/fix.md` first: its conventions (keys, passes, branches, commits, questions, correction pass, guard rails, report and review files) hold for every step below. The imperative rules of section 1 hold, with the `fix` exception of the read-only rule. One Bash call per step.

- The fix mode never contacts the preprod, the prod or a server: no SSH, no Chrome, and it never asks the Basic Auth credentials. It never runs a command of the project (`docker compose`, `make`, `composer`, `npm`, its tests, a build).
- On GitLab it only pushes `feature/checklist-qa-fix-*` branches as fast-forwards (never `--force`), creates draft merge requests assigned to the lead and adds to their description: it never marks a merge request Ready, never approves, merges or closes one. In Notion it only adds the « MR : » block to the skill's comments: no status, no assignment.
- Sub-agents: `gm:cqa-fixer` and `gm:cqa-fix-reviewer`, one sub-agent at a time, one key at a time.
- `--dry-run`: fixes, commits, checks and reviews run, the branches (`dry-run/checklist-qa-fix-<slug>`) stay in the dev's repo, nothing is pushed, no merge request, nothing in Notion: the push and Notion questions of step 3 are not asked. `--phase` does not apply: the pass takes the rows of the chosen run (a run audited with `--phase` gives only that phase's rows).
- Until the launch questions of step 3, this invocation is interactive, whatever `mode` the audit stored. The answers of step 3 live in `fix/state.json` (`mode`, `push`, `notion`), never in `context.json`.

**1. Run.**

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" project "$PWD"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" runs "<slug>"
```

Take the most recent run whose `fix_pending` is true (a new pass on another run would collide with the branches still checked out in that run's worktrees: git checks a branch out in one worktree only), else the most recent run whose `lots` and `plan` are true, and ask « Utiliser le run <run_id> ? » with « Oui » and « Choisir un autre run » (then list the runs with their date and ask which one). No such run: say « Aucun run à corriger : lancez d'abord /gm:checklist-quality-audit. » and stop. Check its context, without asking the Basic Auth credentials (errors: show them and stop):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

When the run has `write_pending` true, say « Terminez d'abord l'écriture de l'audit (/gm:checklist-quality-audit rerun). » and stop. When it has `fix_pending` true, read `push` in `fix/state.json`. With `--dry-run` and a stored `push` other than `dry_run`, say « Une passe fix réelle est inachevée sur ce run : relancez /gm:checklist-quality-audit fix sans --dry-run pour la reprendre ou l'abandonner. » and stop. Otherwise ask « Une passe fix est inachevée. », followed in the same question by its stored push mode (« Publication : MR en brouillon. » for `mr`, « Publication : branches locales. » for `local`, « Publication : --dry-run, rien n'est poussé. » for `dry_run`: the stored mode wins over the invocation), with « Reprendre les correctifs » and « Abandonner »:

- « Abandonner »: abandon the pass, then go on with step 2 for a new pass. It removes the worktrees of the pass and prints `{"removed", "unpushed"}`: tell the lead which local branches keep commits not pushed (`unpushed`); they are never deleted.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" abandon "$RUN"
```

- « Reprendre les correctifs »: no launch question: `mode`, `push` and `notion` come from `fix/state.json`. Start the inhibitor (step 4), read `fix/state.json`, and resume at its `stage`: `keys` at step 5, `questions` at step 6, `notion` at step 7, `summary` at step 8. At the stages `keys` and `questions`, first take the keys in the order of `keys`, each from its `step`:
  - `selected`: 5.a (`start`), then the rest of step 5.
  - `started`: a fixer pass on the rows still `pending` (5.b).
  - `fixed`: first the questions not handled yet (5.c: present mode, the rows still `question`; autonomous mode, those not set aside yet, as the list of step 6 shows), then 5.d.
  - `checked`: `check` and `guard` again (5.d: `failed_refs` is not stored), then the review of each pass whose rows are fixed and whose review is not in `reviews` (5.e), then the problems (5.f).
  - `reviewed`: `check` and `guard` again (5.d), then the problems (5.f: a key reaches `reviewed` before 5.f has run), before any publication: present mode, then 5.g; autonomous mode, then nothing until step 6.
  - `published`: `finish` (5.g).
  - `done`: nothing.

  Before the step, a pass started without its report (a pass in `bases` above the key's `pass`) is run again from its `pass-start` (5.b), with its rows and its notes file; a row already sent in the `problems` of a `notes-<M>.json` has had its correction pass (5.f). At step 6, an open entry whose row is no longer `question` in `fix/state.json` is settled: close it without asking.

**2. Diagnostic.** Read the copy again, as the devs left it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

GitLab access and state of each repo (its `git fetch origin` only moves the remote refs of the dev's repo):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" doctor "$RUN"
```

It writes `fix/doctor.json` and prints `{"repos": [{"repo", "host", "project", "status", "user", "base", "identity", "fetched", "ahead"}]}`. Keep for the presentation, in French:

- `status`: `ok` (push and draft merge request possible; the push itself is a `git push origin` with the remote's own credentials, SSH key or credential helper, and must work without a prompt); `no_push` (no right to push on the project), `glab_missing` (`glab` not installed), `unauthenticated` (GitLab refused the token, HTTP 401 or 403: the lead runs `glab auth login --hostname <host>`), `unreachable` (« GitLab injoignable » : `glab` never reached the host, a network, VPN, proxy or DNS failure, not a login problem; the lead checks the connection, then `doctor` runs again): local branches only for that repo; `not_gitlab`: its rows are left to the devs.
- `identity` false: no `user.name` or `user.email` in the repo, the commits would fail: the lead sets them before the launch (the commits are theirs).
- `fetched` false: `git fetch origin` failed, the branches would start from a stale `origin/<base>`.
- `ahead`: the commits of `origin/<base>` since the audited commit; every row is checked again on the current code before it is fixed.

Commit signing, one call per repo (`path` of `context.json`):

```bash
git -C "<path>" config --get commit.gpgSign
```

`true`: say « Les commits du repo <repo> sont signés (commit.gpgSign) : la signature doit fonctionner sans saisie (agent déjà déverrouillé), sinon les commits du fixer échoueront. »

Toolbox, for the checks of the changed files (`references/fix.md`, section 9):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
bash "$S/cqa-toolbox.sh" doctor
```

`docker` false or `docker_version` null: the checks run with the host's binaries, else are declared not run; say so. `check` never downloads an image (`--pull never`): a missing image counts as unavailable. With Docker running and `images.agence` null, ask before any pass « Télécharger l'image de la boîte à outils ? » with « Télécharger » and « Continuer sans ». « Télécharger »: run the download below with Bash `run_in_background` and wait for its end before `open` (step 3); exit 1 (an image failed, usually the registry login, `references/diagnostic.md`, section 6): run `doctor` again and say what is still missing. « Continuer sans »: the syntax checks fall back to the host's binaries, else are declared not run, and stylelint is not run; the merge requests say so. The same download brings the `gitleaks` image: without it (`images.gitleaks` null), `guard` runs the home rules alone and says so in its `note`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
bash "$S/cqa-toolbox.sh" pull
```

Third-party plugins, when a WordPress repo of the run (`stack` `wordpress`) has no `third_party_paths` in `context.json`: the guard refuses a commit in `third_party_paths` (`references/fix.md`, section 7), so they must be known before any fixer runs. List the plugin folders:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" wp-plugins "$RUN"
```

No plugin found: no question. Otherwise ask « Plugins maison à analyser ? » with the rules of `references/context.md`, section 6.1.1, as in section 10, step 6, write the plugins left unchecked into `repos[].third_party_paths` of their repo in `context.json`, then check the context again (errors: show them and stop). This belongs to the interactive part, in autonomous mode too: always before `open`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

Then list the candidates:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" candidates "$RUN"
```

It writes `fix/candidates.json` and prints `{"keys": [{"key", "repo", "category", "slug", "push_ready", "rows"}], "left": [{"ref", "title", "category", "reason", "url"}], "flights", "notes", "estimate_min"}`. `keys` come SÉCURITÉ first, then in the order of the copy. No key: show the rows left (`left`, by reason), say « Aucune ligne à corriger. » and stop.

**3. Presentation and launch questions.** Present, in French, before anything is created: per category then per repo, the rows to fix (ref, title, « Auto » or « Semi-auto » from `semi_auto`, the assigned devs `assignees`, the « À faire » in short from `todo`); the rows left to the devs grouped by `reason`, with the link `url` of the merge request when there is one; the `notes`; the repos that will get local branches only (`push_ready` false) and why; the facts of step 2; the number of merge requests planned (one per key); the estimated time (`estimate_min` minutes). When the project has no fix merge request or branch yet (in `flights`, every repo has `open`, `closed`, `merged` and `branches` empty), add: « Premier essai sur ce projet : je conseille le mode présent et une ou deux catégories seulement (« Modifier la liste »), pour juger la qualité des MR avant une passe complète. »

Ask « Lancer ces correctifs ? » with « Lancer », « Modifier la liste » and « Annuler ». « Modifier la liste »: the lead removes refs, or adds some in « Autre », a « Détection seule » or « Manuel » row they judge fixable included; run `candidates` again with `--without` (rows removed) and `--refs` (rows added), repeating every earlier one (the options are not cumulative between runs), then present again. « Annuler »: stop, nothing was created.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" candidates "$RUN" --refs CQ-40 --without CQ-3,CQ-12
```

Once the list is accepted, one question at a time:

1. « Mode 100 % autonome ? » with « Oui, je m'absente » (`autonomous`: no question and no push before the end of the pass) and « Non, je reste disponible » (`present`: questions asked as they come, each key published as soon as it is reviewed).
2. « Pousser les branches et créer les MR en brouillon ? » with « Pousser et créer les MR en brouillon » (`mr`), « Garder les branches en local » (`local`: the lead pushes them; no merge request, nothing in Notion) and « Annuler » (stop, nothing was created). Not asked with `--dry-run` (`dry_run`), nor when no key is `push_ready` (`local`; say so).
3. « Ajouter le lien des MR dans Notion ? » with « Oui » (`yes`) and « Ne rien écrire » (`no`). Asked only with `mr`; otherwise `no`.

Open the pass with the answers:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" open "$RUN" --mode <autonomous|present> --push <mr|local|dry_run> --notion <yes|no>
```

It writes `fix/state.json` (stage `keys`, every key at step `selected`) and archives a finished earlier pass in `fix/_history/`.

**4. Keep the workstation awake** for the whole pass:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" start "$RUN"
```

Tell the lead « Le poste ne se mettra pas en veille pendant la passe de correction. Gardez l'écran ouvert : sur un Mac, rabattre l'écran le met en veille malgré tout. », and, as in section 6, how to release it by hand: « Si cette session s'arrête avant la fin de la passe, libérez le poste avec : bash "<scripts>/cqa-keepawake.sh" stop "<run_dir>" ». Release it (step 8) on every path that ends the pass.

**5. Keys.** One key at a time, in the order of `keys`, SÉCURITÉ first, then the order of the copy; `<repo>/<slug>` is the key. In autonomous mode, ask the lead nothing until step 6: what needs them is set aside with the kind `fix_question` (the command of 5.c), with the key at the start of `--detail`, in French, and `--refs` only for the rows concerned.

a. Branch and worktree:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" start "$RUN" "<repo>/<slug>"
```

It prints `{"branch", "worktree", "base", "start_sha", "reused", "done_refs"}`: a new branch from `origin/<base>`, or the branch of an open merge request or of an unmerged fix branch taken over (`reused`); `done_refs` are rows already on that branch (`done_before`), never fixed again. Run again on a key that has its branch, it gives back the same branch and worktree.

Sections: the key is a repo, with one branch and one worktree, and its `sections` in `fix/state.json` run one after the other, in their order (SÉCURITÉ first). Start a section before its first fixer pass; steps 5.b to 5.f run inside the section in progress, with its rows only (5.g, the publication, is once per repo). Once each of its rows is settled (fixed and reviewed, skipped, dropped, or a question set aside in autonomous mode), end it, then start the next one:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" section-start "$RUN" "<repo>" --section <alias>
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" section-end "$RUN" "<repo>" --section <alias>
```

A section that cannot reach a valid review (report or review still invalid after a new attempt, a problem of the workstation) is given up instead: its commits leave the branch, its open rows become `dropped` with the reason, and the uncommitted changes of the worktree are first saved in `fix/<repo>/leftover-<N>.patch`, then the worktree is reset and cleaned. It prints `{"section", "state", "next", "dropped", "also_dropped", "retry", "leftover"}`; then start the next section.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" section-end "$RUN" "<repo>" --section <alias> --failed --reason "<raison en français>"
```

When `done_refs` leaves no row `pending` (rows all `done_before`): no fixer dispatch. Run `section-start` for the first section, then `pass-start --pass 1 --section <alias>` (5.b), write `fix/<repo>/<slug>/report-1.json` with `{"key": "<repo>/<slug>", "pass": 1, "rows": []}`, validate it as in 5.b: the key is `reviewed` at once. End that section, then start and end each next one (their rows are all settled). Then 5.d to 5.g.

b. Fixer pass `N`: the key's `pass` in `fix/state.json` plus 1 (1 for a new key). Right before each dispatch, record the base of the pass and keep the printed `base`: it is the `Base` of the review of pass N.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" pass-start "$RUN" "<repo>/<slug>" --pass <N> --section <alias>
```

Dispatch `gm:cqa-fixer` with the line, exactly:

```
Run dir: <run_dir>. Fix: <repo>/<slug>. Worktree: <path>. Pass: <N>. Rows: CQ-3, CQ-12. Notes: <path|none>. Follow your instructions.
```

`Worktree` is the absolute `worktree` that `start` printed. Pass 1: `Rows` are the rows of the key still `pending` in `fix/state.json`, and `Notes: none`. A later pass: the rows it answers (5.c) or corrects (5.f), and `Notes: fix/<repo>/<slug>/notes-<N>.json`, written before the dispatch with Write, in the format of `references/fix.md`, section 1. Then validate the report again (the fixer validated it too):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" validate "$RUN" "<repo>/<slug>" report --pass <N>
```

It prints `{"ok", "errors", "rows", "dev_checkout_changed"}`, `rows` giving the refs per outcome (`fixed`, `skipped`, `question`).

- Exit 6, or a fixer that returned without a valid report: `pass-start` again (it keeps the base it recorded), dispatch the fixer once more, the dispatch line followed by the `errors`, and validate again. Still invalid: the key is left (below). Exit 2 (worktree or state not as expected): show the message; the key is left.
- Rows skipped with « commit refusé par git : <cause> » (commit signing, git identity, a lock): the cause does not depend on the rows, and the next keys may fail the same way. Present mode: tell the lead at once; autonomous mode: set it aside. The rows stay skipped with that reason; a later fix pass takes them again once the cause is fixed.
- A fixer that stopped on uncommitted changes in the worktree (its one-line return): never clean it. Show them to the lead in present mode, then ask « Le worktree de <repo>/<slug> a des modifications non commitées (ci-dessus). » with « C'est réglé, reprendre » (dispatch again) and « Laisser cette clé » (the key is left); in autonomous mode, set it aside and go on with the next key.

```bash
git -C "<worktree>" -c core.hooksPath=/dev/null -c core.fsmonitor=false status --short
```

- `dev_checkout_changed` other than `null` (`{"paths", "head", "branch", "stash"}`): the dev's own checkout changed during the pass, not through the skill. Present mode: tell the lead at once, in French, the paths and whether HEAD, the branch or the stash moved, and that the pass changed nothing there. Autonomous mode: set it aside with that detail. Never undo anything; go on.

c. Questions: the rows of `rows.question`. Each `question` of `report-<N>.json` has a `text`, 2 to 4 `options` and the `default` the fixer would apply.

- Present mode: ask them now, one at a time, with the fixer's options, plus « Laisser aux devs » while there are fewer than 4, saying which option is the default. A row the lead leaves to the devs (« Laisser aux devs », or a free answer that says so) gets no fixer pass: drop it with the command below (a row without commits is only marked `dropped`; the branch does not change). Write the other answers in `notes-<N+1>.json` (`answers`; `problems` empty) and run one fixer pass on those rows (5.b with N+1) before 5.d. An answered row is never asked again.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" drop "$RUN" "<repo>/<slug>" --refs CQ-12 --reason "laissée aux devs par le lead"
```

- Autonomous mode: set each question aside, then go on without the row (it waits for step 6):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" defer "$RUN" --kind fix_question --detail "<repo>/<slug> : <texte de la question>" --refs CQ-12
```

d. Checks of the changed files, then the guard:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" check "$RUN" "<repo>/<slug>"
```

It runs the syntax checks and stylelint on the files changed in `<start_sha>..HEAD`, writes `checks.json` and prints `{"checks", "not_run", "failed_refs"}`: `failed_refs` are the rows whose commits touch a failed file.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" guard "$RUN" "<repo>/<slug>"
```

It prints `{"ok", "problems", "note"}`, one problem per commit and path (`{"sha", "ref", "path", "line", "label", "reason", "kind", "waivable"}`, `kind` being `form` for a problem of the message, `suspect` for a heuristic secret alert (`credential_assignment`, `credential_define`) and `content` for the others), and exits 5 when there is one. The secrets come from the strict rules of the audit, the values the session knows and gitleaks of the toolbox, run once per call on the same content (label `gitleaks:<RuleID>`, always `content`, never waived). `note` is null, or « gitleaks indisponible : contrôle maison seul » when Docker or the `gitleaks` image is missing: the home rules ran alone, tell the lead. A secret is reported per commit, path, line and label: `line` is the line of the file where it starts (a `define(` or a key written over several lines is reported when one of its lines is added), and one problem stands for every credential of that label on that line. A `suspect` with `waivable` true may be a false positive (a password read from a request, for instance): show the lead its path, line and label, never the line itself, and ask « Cette ligne contient-elle un vrai secret ? »: the lead judges every credential on that whole line. A real secret is a `content` problem. Otherwise, with the lead's answer:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" waive-secret "$RUN" "<repo>/<slug>" --sha <sha> --path <path> --line <line> --label <label>
```

One waiver covers that line of that file for that label, as long as the content of the file is the one the lead judged (the waiver is keyed on the blob of the path in that commit, or on the blob of the index that `guard --staged` prints as its `sha`): a `drop` that replays the commit without changing the file keeps it, any change of the file voids it and the alert comes back. The guard no longer reports it. Any other secret label is never waived.

e. Review: for each fixer pass not reviewed yet (absent from `reviews` of the key) that has fixed rows (`outcome` `fixed` and `pass` N in `fix/state.json`, as `rows.fixed` of its `validate` listed them), in pass order, first read the scope of its diff:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" review-scope "$RUN" "<repo>/<slug>" --pass <N>
```

It prints `{"light", "lines", "files"}` for the diff `bases["<N>"]..HEAD`: `light` is true when every file is documentation (`*.md`, `*.rst`, `*.adoc`) and the added plus removed lines are 40 at most. When `light` is true, write `review-<N>.json` yourself, by the rules of `gm:cqa-fix-reviewer`; otherwise dispatch `gm:cqa-fix-reviewer` with the line, exactly:

```
Run dir: <run_dir>. Review: <repo>/<slug>. Worktree: <path>. Base: <sha>. Pass: <N>. Follow your instructions.
```

`Base` is the `base` that `pass-start` printed for pass N (`bases["<N>"]` of the key in `fix/state.json`). A pass without fixed rows needs no review. Then:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" validate "$RUN" "<repo>/<slug>" review --pass <N>
```

It prints `{"ok", "errors", "blocking"}`. Exit 6: dispatch the reviewer once more with the `errors`, and validate again; still invalid: the key is left (below).

f. Problems, once the reviews of e are recorded. Read them fresh each time, for the rows of the key: a row has a problem when it is in `failed_refs` of the last `check`, when a problem of the last `guard` names its commit (the problem's `ref`, or a `sha` among the row's `commits` in `fix/state.json`), when the review of its current pass (its `pass` in `fix/state.json`) lists it in `blocking`, or when the fixer gave it up (its `attention` in `fix/state.json` starts with « Abandon : »). Each row gets at most one correction pass: each row enters the `problems` of a notes file once. Repeat until no row of the key has a problem:

1. Rows with a problem that the fixer did not give up and that no `notes-<M>.json` of the key lists in `problems` yet: one correction pass on them. Write `notes-<N>.json` with their `problems` (`source` `guard`, `check` or `review`, `detail` in French as `references/fix.md`, section 1 says), then 5.b, 5.d and 5.e for that pass (its review has its own `Base`), and repeat.
2. Otherwise every row with a problem had its correction pass, or was given up: it leaves the branch, one call per reason, in French, 300 characters at most (the fixer's « Abandon : » text, the reviewer's notes, the failed check or the guard reason), then `check` and `guard` again (the drop rewrites the commits that follow it, so the checked files change), and repeat.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" drop "$RUN" "<repo>/<slug>" --refs CQ-12 --reason "<raison en français>"
```

`drop` prints `{"dropped", "also_dropped", "retry"}`: `also_dropped` are rows whose fix depended on a removed commit; keep them for the end message. Those of `retry` are back to `pending` (`retry` true, the reason in `reason`) for one more fixer pass in their section; the others, or a row taken along a second time, are dropped for good.

Not a problem of a row, and never sent to the fixer nor to `drop` (which refuses `done_before` rows and refs outside the key, exit 5): a guard problem on a commit that no row of the key lists in its `commits` (a commit of a taken-over branch, at or before its head), or a `failed_refs` row that is `done_before`. It goes to the lead: present mode, tell them at once; autonomous mode, set it aside. A guard problem left there keeps the key from publication (`publish` exits 5, below).

The key is now at `reviewed` (no row `pending`, every pass with fixed rows reviewed without a blocking row); otherwise it is left (below).

g. Publication. In autonomous mode the key stops at `reviewed`, with its branch and its worktree: nothing is pushed and no merge request is created during the pass (Claude Code may ask the lead to confirm a push); step 6 publishes every key, and `publish` refuses it (exit 5) until step 6 moves the pass to the stage `questions`. In present mode, publish the key now:

- `push` `local` or `dry_run`, or a key whose `push_ready` is false: no publication, only `finish` (below), which keeps the branch in the dev's repo.
- Otherwise `mr-body`, `publish`, then `finish`.

Before `mr-body`, no pass in progress, group the unpushed commits by section: `reorder` prints `{"reordered", "conflicts", "rewritten"}`, and a conflict leaves the order as it was (the summary says so). Then rewrite the message of each commit that a guard problem of `kind` `form` names, keeping the ref of its row: `reword` prints `{"reworded", "commit", "rewritten", "note"}`, and the new message goes through the guard first (exit 5 with its `problems`, nothing changed). Run `guard` again after `reorder` and after each `reword`: `rewritten` maps the old SHAs to the new ones, and the SHAs of the later commits change.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" reorder "$RUN" "<repo>"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" reword "$RUN" "<repo>" --commit <sha> --message-file <file>
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" mr-body "$RUN" "<repo>/<slug>"
```

It needs every section of the key settled or failed (exit 2 names the one that is not: `section-end` first). It writes `mr-title.txt` and `mr.md` for a new merge request, or `mr-addition.md` for a merge request already open (a section « Ajouts du <date> » and « Hors-scope » updated; the rest of its description, which the reviewer may have edited, is kept), its rows grouped by section in the order of the commits, and prints `{"title", "file", "kind", "sections", "fixed", "partial", "covered", "waiting", "out_of_scope"}`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" publish "$RUN" "<repo>/<slug>"
```

It checks everything again (guard, every new commit reported, the merge request still open on GitLab, the remote branch not diverged), pushes the branch as a fast-forward, then creates the draft merge request assigned to the lead, or adds to its description, and prints `{"branch", "head", "pushed", "mr", "created", "updated", "links"}`. By exit code:

- 0: `finish`. With the `note` « Aucun commit sur la branche : ni push ni MR. », nothing left the workstation: `finish` too (it deletes the empty branch; the summary names one it had to keep).
- 2 with « Description de MR périmée : relancez mr-body. », or with a merge request already open for the branch: `mr-body` again, then `publish` once more.
- 5 with « La branche distante a divergé : intégrez ses commits à la main, rien n'a été poussé. », or with « La MR !<iid> est fusionnée » or « fermée » (« relancez une passe fix »): no retry can pass, so no question: tell the lead on the spot, then `finish --keep-local` (the branch stays in the dev's repo; the lead integrates the remote commits by hand, or a later fix pass starts a new branch).
- Any other 2 (a push or `glab` failure), 5 (refused by a guard: guard problem, commit not reported) or 7 (a secret in the title or the description: give the labels, never a value; the same for an exit 7 of `mr-body`): show the message and ask the lead on the spot « La publication de <repo>/<slug> a échoué : <message> » with « Relancer la publication » (once the lead fixed the cause: `mr-body` again, then `publish`) and « Garder la branche en local ». Never force anything.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" finish "$RUN" "<repo>/<slug>"
```

It removes the worktree (exit 5 while it has uncommitted changes: show them to the lead, never clean them), deletes the local branch only when this pass created it and left it without any commit (never a taken-over branch; its head is still the start, no checkout has it, origin does not have it) and prints `{"removed", "branch", "kept_local", "branch_deleted", "branch_kept", "unpushed"}` (`branch_kept`: why the branch stays). After « Garder la branche en local »:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" finish "$RUN" "<repo>/<slug>" --keep-local
```

A key is left when it cannot reach `reviewed` (a report or a review still invalid, uncommitted changes the lead leaves): its branch and worktree stay as they are, nothing is published, and the end message names them (step 8). Go on with the next key.

**6. Deferred questions and publication at the end.** Once every key went through step 5:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" stage "$RUN" questions
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" deferred "$RUN" --fix
```

It prints `{"items"}`, the open entries of the fix mode (`id`, `kind`, `detail`, `refs`). The lead is back: go through them one at a time, in French, with AskUserQuestion. A row question: ask it with the options of the row (its `question` in the last report of the key that holds it), plus « Laisser aux devs » while there are fewer than 4; a row left to the devs (« Laisser aux devs », or a free answer that says so) is dropped as in 5.c (`--reason "laissée aux devs par le lead"`), without a fixer pass. Any other entry (a changed checkout, rows refused by git, uncommitted changes, a problem left to the lead): say it and ask what it needs; for uncommitted changes, « C'est réglé, reprendre » (the key goes on from its `step`, as on a resume, step 1) or « Laisser cette clé ». A row left without answer (the lead skips its question, or gives no answer the fixer can apply) is dropped the same way, without a fixer pass, so that it goes to « Hors-scope » of its merge request:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" drop "$RUN" "<repo>/<slug>" --refs CQ-12 --reason "sans réponse du lead"
```

Then, for each key with other answers, in the order of `keys`: `start` (5.a, it gives the key back its worktree), write `notes-<N>.json` with those `answers`, and run 5.b to 5.f for the answered rows, one pass per section: `pass-start --section` names the section of the rows, which stays settled (at this stage `pass-start` accepts a settled section that still holds a question). Close each entry once settled (a question once its row is dropped or the pass of its key is reviewed):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" deferred "$RUN" --resolve <id> --fix
```

In autonomous mode, before any publication, when at least one key at `reviewed` would be pushed (`push` `mr`, key `push_ready`), ask « Publier les branches maintenant ? » with « Publier » and « Garder les branches en local ». « Garder les branches en local »: `finish --keep-local` for every key at `reviewed` (nothing is pushed, the branches stay in the dev's repo), then step 7. After « Publier », and in present mode: publish every key still at `reviewed`, SÉCURITÉ first, the lead present, as in 5.g (`mr-body`, `publish` and `finish`, or `finish` alone for a local branch). In autonomous mode this is the first push of the pass: Claude Code may ask the lead to confirm each one, and a publication failure is asked on the spot.

**7. Notion.** Move the pass to this stage in every case:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" stage "$RUN" notion
```

Then, only when the pass has `notion` true (the consent of step 3), build the comments: one per row put in a merge request, the skill's comment with its « MR : » block (or a comment reduced to the block when the row has none), never a status:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" mr-links "$RUN"
```

It writes `fix/notion-plan.json` and prints `{"comments", "refs"}`. With `comments` 0, skip the write. Otherwise:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" apply "$RUN" --plan fix/notion-plan.json
```

Exit codes as in section 8, step 1: 0, go on; 7, show each blocked operation (ref, kind, reason, never its text) and go on; 3, once the cause is fixed run it once more (it resumes where it stopped), still 3: say « Écriture Notion interrompue : la passe reste reprenable, relancez /gm:checklist-quality-audit fix plus tard. », release the workstation (step 8) and stop; 8 or 2: show the message and go on, nothing was written. Keep the refs of `replace_failed` (a skill comment that could not be replaced, so a second one was created).

**8. Summary.**

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" stage "$RUN" summary
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_fix.py" summary "$RUN" --replace-failed CQ-3,CQ-12
```

Pass `--replace-failed` only with the refs kept at step 7. It writes `fix/synthese.md`, closes the pass and its open `fix_question` entries, and prints `{"text", "team_message"}`. Exit 7: a secret in the text: nothing was written, and the pass stays pending (`fix_pending`, its `fix_question` entries open) until a `summary` passes. Give the labels, never a value, and tell the lead which text holds it: most often the `reason` of a row in `fix/state.json`, which comes from the fixer's `report-<N>.json` (a `reason`, or the text of a `question`) or from a `drop`; else a title or a reason of `fix/candidates.json`. Once the lead agrees, replace the value by « [secret retiré] » in that text, in `fix/state.json` and in the file it comes from, then run `summary` again (same options). Then release the workstation:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
bash "$S/cqa-keepawake.sh" stop "$RUN"
```

End, in French: show `text`, then the keys left (step 5) with their branch and worktree, the rows `also_dropped` with the row they depended on, and the message for the team, ready to paste (`team_message`).

## Exit codes

| Script | Codes |
|---|---|
| `cqa_notion.py` | 0 success; 2 environment or usage; 3 Notion API or network (token refused, page not shared with the integration); 6 schema of the copy not v2; 7 at least one write blocked; 8 write refused on the template |
| `cqa_plan.py` | 0 success; 2 usage or missing file (the message names the command to run first); 6 invalid `context.json` or findings; 7 secret found by `scan` |
| `cqa_fix.py` | 0 success; 2 usage or environment (also a failed push or `glab` call, a stale merge request description, a worktree missing: the message says what to run); 5 refused by a guard (the message says why: guard problem, remote branch diverged, merge request merged or closed, commit not reported, local or dry-run pass, an autonomous pass still in its key loop, uncommitted changes); 6 invalid `context.json`, report, review or state; 7 secret found in a merge request text or the summary |
| `cqa_ssh_ro.py` | 0 success; 1 remote failure (command or file missing on the server); 2 usage; 4 refused, to be confirmed by the lead |
| `cqa-keepawake.sh` | 0 success, also without any inhibitor on the workstation (warning only); 2 usage |
| `cqa-toolbox.sh` | 0 success; 1 at least one tool failed (the manifest is written); 2 usage or environment; `run` returns the code of the tool (3: target unreachable or 401) |
