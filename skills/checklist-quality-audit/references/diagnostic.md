# Diagnostic (section 5)

Goal: know what the workstation lets the audit do, check that the preprod runs the audited code, then show the coverage before the lead decides. Every Bash call starts with the `S=` line of SKILL.md (here unexpanded) and `RUN="<run_dir>"`. Nothing here writes to a repo, a server or Notion.

## 1. Repos (tool "Code")

For each repo of `context.json`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" repo-state "<chemin du repo>"
```

- It fetches `origin`, then gives `{"branch", "clean", "head", "ahead", "behind", "fetched"}`, compared with `origin/develop`. Exit 2: not a usable git repo.
- Expected: `branch` is `develop`, `clean` true, `ahead` and `behind` 0, `fetched` true.
- Otherwise, say what is off, in French, and let the lead fix it: never checkout, stash or pull yourself.
  - Behind on a clean `develop`: suggest `git -C "<chemin>" pull --ff-only`.
  - Other branch, local changes, local commits not pushed: describe them only.
  - `fetched` false: « Impossible de joindre origin : la comparaison repose sur le dernier fetch. »
- Ask « Le repo <name> n'est pas sur develop à jour. » with « C'est corrigé, revérifier » and « Continuer avec l'état actuel ». Run `repo-state` again after a fix.
- `audited_sha` = `head`. "Code" is available when every repo answered.

## 2. Deployed commit and alignment (launch condition)

For each repo, take the first source that answers.

**a. Server**, only with `ssh.alias`, after the SSH check of step 7. The folder of the application: ask « Dossier de l'application <name> sur le serveur (ou saisir le chemin dans « Autre ») ? », with the candidates (3 at most) and « Je ne sais pas » as options, plus « Arrêter l'audit » when there is no candidate; « Je ne sais pas » skips the server source. The candidates come from the guard (marker per stack in `references/server.md`; `artisan` here):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- find /var/www /srv -maxdepth 3 -name artisan
```

Record it in `ssh.app_dirs` (`{"<repo name>": "<folder>"}`), then:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- git -C <dossier> rev-parse HEAD
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- cat <dossier>/REVISION
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- docker ps
```

The agency CI tags images with the short SHA: in `docker ps`, the image tag of the application gives it. Exit 1: no git folder, no `REVISION`, no Docker; try the next one. Exit 4: `references/server.md`.

**b. GitLab** (glab authenticated on the host of the remote):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" deployed "<chemin du repo>" --branch <preprod_branch>
```

- `{"sha", "source"}`. `source` `environment`: last successful deployment of the preprod environment. `pipeline`: only the last successful pipeline of the branch, a weaker clue: confirm with « D'après GitLab, le dernier pipeline réussi de <branche> est <sha court>. Est-ce bien ce qui tourne en préprod ? » (« Oui », « Je ne sais pas »).
- `{"sha": null}`: nothing found (or glab missing, not logged in).

**c. The lead**: « Quel commit tourne en préprod pour le repo <name> (saisir le SHA, court ou complet, dans « Autre ») ? » with « Je ne sais pas » and « Arrêter l'audit ».

Then:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" align "<chemin du repo>" <sha>
```

It gives `{"aligned", "missing", "reason"}`.

- `aligned` true: `deployed_sha` = the sha, `aligned` true.
- `aligned` false: list `missing` (`sha` and `title`, newest first) to the lead, then « La préprod n'a pas les <n> derniers commits de develop pour <name>. » with « Déployer puis relancer (recommandé) » and « Lancer quand même ».
  - Deploy: stop the skill; the lead deploys and relaunches. The skill never deploys.
  - Go on: `aligned` false. The sub-agents fill `divergence` row by row, which caps the verdict at « Mise en place partielle », and the synthesis reports the gap.
- `aligned` null (`reason` `commit_inconnu`, `base_inconnue` or `base_non_a_jour`), or no sha at all: « Commit déployé introuvable pour <name>. Confirmez-vous que la préprod tourne sur develop à jour ? » with « Je confirme » and « Arrêter ». Confirmed: `aligned` stays `null`, `deployed_sha` the sha or `null`, and section 8 adds the remark to `synthese.md`. Otherwise stop.

With `diagnostic`, report the state only: no question about deploying.

## 3. Runner (tool "CLI local")

Follow `shared/runner.md` of the plugin (make, then docker compose, then lando), from each repo, in read-only mode:

```bash
grep -E '^[A-Za-z0-9_-]+:' "<chemin du repo>/Makefile"
```

```bash
docker compose --project-directory "<chemin du repo>" ps --status running --services
```

- "CLI local" is available when the runner of each repo is up, or, failing that, when the host has `composer` (repo with `composer.lock`) and `npm` (repo with `package-lock.json`) for the read-only audits.
- Runner down: « Le runner du repo <name> n'est pas démarré. » with « Je le démarre, revérifier » and « Continuer sans ». Never start it yourself.

## 4. Preprod reachable (tool "HTTP")

From section 4: "HTTP" is available when `target.access` is `open`, or `basic_auth` with credentials accepted and the toolbox ready (step 6). `ip_filtered` and `unreachable`: unavailable.

## 5. Claude in Chrome (tool "Navigateur")

Open a tab on the preprod, as `references/browser.md` says (diagnostic), then close it. Available when the tab opens. A Basic Auth dialog in the tab is expected: the lead logs in during the audit.

## 6. Toolbox (tools "Audit web" and "Crawler")

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
bash "$S/cqa-toolbox.sh" doctor
```

It prints `{"docker", "docker_version", "registry_login", "images": {"<image>": "<version>" or null}, "sf_licence", "arch"}`.

| Fact | Meaning | What the lead does |
|---|---|---|
| `docker` false | Docker missing | install Docker Desktop (macOS) or Docker Engine (Linux) |
| `docker_version` null | Docker not started | start Docker |
| `registry_login` false and images null | no access to the registry | `docker login registry.gitlab.gingerminds.fr` with a GitLab token that can read the registry |
| `registry_login` true, an image null | images not pulled | accept the download (below) |
| `sf_licence` false | Screaming Frog licence absent | export `GM_SF_LICENCE_USER` and `GM_SF_LICENCE_KEY` (Vaultwarden) |
| `arch` `arm64` | Apple Silicon | nothing; an image without arm64 runs emulated, more slowly |

Download, only after « Télécharger les images de la boîte à outils (plus de 2 Go) ? » (« Oui », « Non »), with Bash `run_in_background`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
bash "$S/cqa-toolbox.sh" pull
```

Exit 1: an image failed, usually the registry login. Then run `doctor` again.

- The toolbox is **ready** when `docker_version` is set and `images.agence` is not null.
- "Audit web": toolbox ready, and the target reachable from the workstation (`access` `open`, or `basic_auth` with accepted credentials).
- "Crawler": "Audit web", `images.screamingfrog` not null and `sf_licence` true.

## 7. SSH (tool "SSH serveur")

Only with `ssh.alias`. The connection test, outside the guard because it runs nothing:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> true
```

Exit 0: available. Otherwise: « Connexion SSH impossible avec <alias> (clé non chargée, VPN ?). » Never show the SSH configuration.

## 8. Application CLI (tool "BDD")

Only with "SSH serveur", for each repo with a database CLI, through the guard (folder of step 2):

| Stack | Command after `<alias> --` |
|---|---|
| drupal | `<dossier>/vendor/bin/drush --version` |
| laravel | `php <dossier>/artisan --version` |
| wordpress | `wp core version --path=<dossier>` |
| in a container | `docker compose -f <dossier>/docker-compose.yml exec -T <service> <commande ci-dessus>` or `docker exec <conteneur> <commande ci-dessus>` |

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- php <dossier>/artisan --version
```

Exit 0 for each repo concerned: "BDD" available. Exit 1: CLI missing (shared hosting): unavailable.

## 9. Other tools

- "Comptes de test": `test_accounts.front` or `test_accounts.backoffice` true.
- "Service externe": every repo has `protected` false (output of `project`) and `target.access` is `open`. Otherwise the rows that need an outside service wait for J0.
- "Intervention humaine" is never a tool to provide: leave it out of `available_tools`.

## 10. Record, read the copy, coverage

Write in `context.json`: `available_tools` (exact names: `Code`, `CLI local`, `HTTP`, `Navigateur`, `Audit web`, `Crawler`, `SSH serveur`, `BDD`, `Comptes de test`, `Service externe`), and for each repo `audited_sha`, `deployed_sha`, `aligned`, and `ssh.app_dirs`. Write the table of checks and their results in `diagnostic.json` (no secret). Then:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" rows "$RUN"
```

- It writes `rows.json` and prints `{"rows", "matched", "unmatched", "missing_in_copy"}`.
- `unmatched` rows have no fiche in the template: they are not evaluated (« fiche introuvable »). `missing_in_copy`: rows of the template absent from the copy, listed in the synthesis, never created.
- Exit 2: `GM_CQA_REFERENCE` absent, or the copy is the template. Exit 3: Notion API.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" coverage "$RUN"
```

It prints `{"total", "evaluable", "evaluable_human", "partial", "not_evaluable", "deferred", "na", "standby", "gains": [{"tool", "action", "rows"}]}`.

- `evaluable_human` is a part of `evaluable`: never add them.
- Rows without fiche = `total` minus `evaluable`, `partial`, `not_evaluable`, `deferred`, `na` and `standby`.

## 11. What the lead sees

1. The table of checks, in French: « Contrôle | Résultat | Outil couvert ».
2. The coverage: « <evaluable>/<total> lignes évaluables, dont <evaluable_human> avec validation humaine ; <partial> partielles (un outil manque) ; <not_evaluable> non évaluables ; <deferred> reportées à J0 / J+7 ; <na> non applicables (techno) ; <standby> en standby. » Add « <n> sans fiche dans le modèle » when there are some.
3. The gains, one line each: « <action> : +<rows> lignes (<tool>) ».
4. The gaps found: repo not on `develop` up to date, preprod behind, `PROJECT.md` that cites another repo.

With `diagnostic`, stop here: « Diagnostic terminé, rien n'a été écrit dans Notion. Dossier du run : <run_dir>. » Otherwise ask « Que faire ? » with « Lancer l'audit » and « Compléter mon poste d'abord ». The second stops the skill: a later launch the same day offers to resume this run, and redoes the diagnostic.
