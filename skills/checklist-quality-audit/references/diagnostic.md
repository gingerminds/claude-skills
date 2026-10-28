# Diagnostic (section 5)

Goal: know what the workstation lets the audit do, check that the preprod runs the audited code, then show the coverage before the lead decides. Every Bash call starts with the `S=` line of SKILL.md (here unexpanded) and `RUN="<run_dir>"`. Nothing here writes to a repo, a server or Notion.

In autonomous mode (`mode` is `autonome`, SKILL.md section 5) no question of this page is asked: each one says its non-acting default, recorded with `defer --kind gap` for the end of the audit, and the diagnostic goes on. The skill never deploys, never starts a runner, never downloads the toolbox images and never stops the audit on its own.

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
- In autonomous mode, do not ask: record `cqa_plan.py defer "$RUN" --kind gap --detail "Le repo <name> n'est pas sur develop à jour : <écart>."` and go on with the current state (« Continuer avec l'état actuel »); never touch the repo.
- `audited_sha` = `head`. "Code" is available when every repo answered.

## 2. Deployed commit and alignment (launch condition)

For each repo, take the first source that answers.

**a. Server**, only with `ssh.alias`, after the SSH check of step 7. The folder of the application: ask « Dossier de l'application <name> sur le serveur (ou saisir le chemin dans « Autre ») ? », with the candidates (3 at most) and « Je ne sais pas » as options, plus « Arrêter l'audit » when there is no candidate; « Je ne sais pas » skips the server source. In autonomous mode, do not ask: record `null` unless there is exactly one candidate, which is then taken; with `null` (as « Je ne sais pas »: the server source is skipped), also record `cqa_plan.py defer "$RUN" --kind gap --detail "Dossier de l'application <name> sur le serveur à confirmer (<n> candidats)."`; never stop the audit. The candidates come from the guard (marker per stack in `references/server.md`; `artisan` here):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- find /var/www /srv /app /opt -maxdepth 3 -name artisan
```

A root missing on the server makes `find` exit 1: keep the paths it printed (the lines starting with `find:` are the missing roots). For an application in a container (Docker deployments often sit under `/app` or `/opt`), also find the folder inside the container with the same `find` after `-- docker exec <conteneur>` (or `-- docker compose -f <dossier sur l'hôte>/docker-compose.yml exec -T <service>`).

Record it in `ssh.app_dirs`: `{"<repo name>": "<dossier>"}`, or for an application in a container `{"<repo name>": {"host": "<dossier sur l'hôte>", "container": "<dossier dans le conteneur>"}}`. Use `container` for `--app-dir` of the SQL queries in a container, `host` otherwise (the commands below run on the server; without `host`, when the code exists only in the image, run them through `docker exec <conteneur>` with `container`). « Je ne sais pas »: record `null`. Then:

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

- `{"sha", "source"}`. `source` `environment`: last successful deployment of the preprod environment. `pipeline`: only the last successful pipeline of the branch, a weaker clue: confirm with « D'après GitLab, le dernier pipeline réussi de <branche> est <sha court>. Est-ce bien ce qui tourne en préprod ? » (« Oui », « Je ne sais pas »). In autonomous mode, do not ask: the pipeline SHA stays unconfirmed, as « Je ne sais pas » (the case « Commit déployé introuvable » below).
- `{"sha": null}`: nothing found (or glab missing, not logged in).

**c. The lead**: « Quel commit tourne en préprod pour le repo <name> (saisir le SHA, court ou complet, dans « Autre ») ? » with « Je ne sais pas » and « Arrêter l'audit ». In autonomous mode, do not ask: step c is not asked, as « Je ne sais pas » (no sha: the case « Commit déployé introuvable » below).

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
  - In autonomous mode, do not ask: record `cqa_plan.py defer "$RUN" --kind gap --detail "La préprod n'a pas les <n> derniers commits de develop pour <name>."` and go on as with « Lancer quand même » (`aligned` false); never deploy.
- `aligned` null (`reason` `commit_inconnu`, `base_inconnue` or `base_non_a_jour`), or no sha at all: « Commit déployé introuvable pour <name>. Confirmez-vous que la préprod tourne sur develop à jour ? » with « Je confirme » and « Arrêter ». Confirmed: `aligned` stays `null`, `deployed_sha` the sha or `null`, and section 8 adds the remark to `remarques.md`. Otherwise stop. In autonomous mode, do not ask (SKILL.md, section 5): record `cqa_plan.py defer "$RUN" --kind gap --detail "Commit déployé introuvable pour <name> : alignement non confirmé."`, store `aligned` false (never `null`, which means the lead confirmed) and `deployed_sha` the sha or `null`, and go on: the repo is treated as not aligned and the synthesis reports the gap.

With `diagnostic`, report the state only: no question about deploying.

## 3. Runner (tool "CLI local")

Follow `shared/runner.md` of the plugin (make, then docker compose, then lando), from each repo, in read-only mode:

```bash
grep -E '^[A-Za-z0-9_-]+:' "<chemin du repo>/Makefile"
```

```bash
docker compose --project-directory "<chemin du repo>" ps --status running --services
```

- "CLI local" is available when any of these holds:
  - the runner of each repo is up;
  - the host has `composer` (repo with `composer.lock`) and `npm` (repo with `package-lock.json`), for the read-only audits;
  - `agence_1_2` is true (from the `doctor` run of step 6): `stylelint` and `deps` do these checks in the toolbox.
- Runner down: « Le runner du repo <name> n'est pas démarré. » with « Je le démarre, revérifier » and « Continuer sans ». Never start it yourself.
- In autonomous mode, do not ask: record `cqa_plan.py defer "$RUN" --kind gap --detail "Le runner du repo <name> n'est pas démarré."` and go on as with « Continuer sans » ("CLI local" as the fallback above allows); never start it.

## 4. Preprod reachable (tool "HTTP")

From section 4: "HTTP" is available when `target.access` is `open`, or `basic_auth` with credentials accepted and the toolbox ready (step 6). `ip_filtered` and `unreachable`: unavailable.

## 4.1 Key pages and links of the home page (tool "HTTP")

Only when "HTTP" is available. Authenticated with `--auth-env` (credentials of section 4 of SKILL.md) when `target.access` is `basic_auth`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" key-pages-check "$RUN"
```

- It requests every key page and the internal links of the home page (50 requests at most, at the toolbox rate, 100 seconds at most, on the origin of the target only) and prints `{"pages": [{"url", "template", "status", "checked", "gap"}], "home_links_404": [<urls>], "soft_404": [{"url", "status"}]}`. A page with `checked` false was not requested (another origin, or the time limit): it is not a gap.
- Count only the pages with `checked` true. `gap` is true for a key page that does not answer 2xx or 3xx and for a dead link of the home page. A checked page with `status` `null` is « <url> ne répond pas »; a `status` 404 is « <url> répond 404 ». The page of the template « 404 » is never a `gap`: it is expected to answer 404, and a replacement is never asked for it.
- `soft_404` lists the checked pages of the template « 404 » that answer anything but 404 (200, a redirection, 5xx, 401, or `null`; never an unchecked page). Each is the gap « La page d'erreur répond <status> au lieu de 404 (soft 404 ou redirection) : les outils de pages l'auditeront avec un constat unexpected-status. » (« ne répond pas » for `null`), in the table of checks and in `diagnostic.json`, in both modes. The page is kept as it is and audited; never ask about it. In autonomous mode, record `cqa_plan.py defer "$RUN" --kind gap --detail "La page d'erreur <url> répond <status> au lieu de 404."`.
- Interactive mode: each gap goes in the table of checks and in `diagnostic.json`, and a key page with a gap is not audited as it is: ask the lead for a replacement (section 6.3 of `references/context.md`).
- The gaps go in the table of checks and in `diagnostic.json`, in both modes. Dead home links go there as one combined mention at most (« <n> liens de l'accueil répondent 404 »), never one by one.
- Autonomous mode: ask nothing. Record each key page with a gap with `cqa_plan.py defer "$RUN" --kind gap --detail "<url> répond <status>"` (« ne répond pas » when `status` is `null`); dead home links are not deferred; the page stays as the lead validated it.

## 5. Claude in Chrome (tool "Navigateur")

Open a tab on the preprod, as `references/browser.md` says (diagnostic), then close it. Available when the tab opens. A Basic Auth dialog in the tab is expected: the lead logs in during the audit.

## 6. Toolbox (tools "Audit web" and "Crawler")

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
bash "$S/cqa-toolbox.sh" doctor
```

It prints `{"docker", "docker_version", "registry_login", "images": {"<image>": "<version>" or null}, "agence_crawl", "agence_1_2", "arch"}`. `agence_crawl` is true when the `agence` image is version 1.1.0 or later, `agence_1_2` when it is version 1.2.0 or later.

| Fact | Meaning | What the lead does |
|---|---|---|
| `docker` false | Docker missing | install Docker Desktop (macOS) or Docker Engine (Linux) |
| `docker_version` null | Docker not started | start Docker |
| `registry_login` false and images null | no access to the registry | `docker login registry.gitlab.gingerminds.fr` with a GitLab token that can read the registry |
| `registry_login` true, an image null | images not pulled | accept the download (below) |
| `agence_crawl` false | `agence` image older than 1.1.0, crawl unavailable | accept the download again (below) |
| `agence_1_2` false | image `agence` antérieure à 1.2.0 : affichage, Stylelint, dépendances et passages desktop seront ignorés | accepter un nouveau téléchargement |
| `arch` `arm64` | Apple Silicon | nothing; an image without arm64 runs emulated, more slowly |

Download, only after « Télécharger les images de la boîte à outils (plus de 2 Go) ? » (« Oui », « Non »), with Bash `run_in_background`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
bash "$S/cqa-toolbox.sh" pull
```

Exit 1: an image failed, usually the registry login. Then run `doctor` again.

In autonomous mode, do not ask: no download (as « Non »), record `cqa_plan.py defer "$RUN" --kind gap --detail "Images de la boîte à outils à télécharger (plus de 2 Go)."`; the tools that need the missing images stay unavailable ("Audit web" without the `agence` image, "Crawler" without `agence_crawl`; `stylelint`, `deps`, `viewport` and the desktop passes without `agence_1_2`).

- The toolbox is **ready** when `docker_version` is set and `images.agence` is not null.
- "Audit web": toolbox ready, and the target reachable from the workstation (`access` `open`, or `basic_auth` with accepted credentials).
- "Crawler": "Audit web", `agence_crawl` true.

### Crawl duration (when "Crawler" is available)

In a rerun or a recette, skip this section: a relaunched crawl keeps the `crawl_minutes` of the audit. The crawl stops at a maximum duration and a maximum number of URLs, both read from `context.json` by `cqa-toolbox.sh`. Propose them from the size of the site. Authenticated with `--auth-env` (and the credentials set in the same line, as for `key-pages-check`) when the session holds them:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" crawl-estimate "$RUN"
```

- It times at most 9 key pages (the `404` template excluded, half of the time budget at most), then counts the URLs of the sitemaps (`sitemap.xml` and the `Sitemap:` lines of `robots.txt`, indexes followed, prod addresses read on the target), in 100 seconds at most. It prints `{"sitemap_urls", "mean_response_s", "pages_timed", "truncated", "proposed_minutes", "max_urls"}`. `truncated` is true when the time limit, the file limit or the size limit cut the count or dropped a file: say so in the question. `sitemap_urls` is `null` without a sitemap; `null` together with `truncated` true means « sitemap non lu à temps ou trop gros »: name it that way in the question, not « Pas de sitemap ». No credential is printed.
- Ask « Durée maximale du crawl ? » with « <proposed_minutes> min (proposé) » first, then two values among 30, 60 and 120 that differ from the proposal, and any other duration in « Autre » (5 to 240 minutes). The text of the question gives the basis: « <sitemap_urls> URLs dans le sitemap, <mean_response_s> s par page : <proposed_minutes> min, <max_urls> URLs au plus », or « Pas de sitemap : 30 min proposées » when `sitemap_urls` is `null` and `truncated` is false (« Sitemap non lu à temps ou trop gros : 30 min proposées » when `truncated` is true).
- Write the answer in `context.json` as `crawl_minutes` (integer, 5 to 240). The URL cap follows the duration (`toolbox-env`). A slowdown of the preprod during the crawl is not anticipated: the crawl lowers its own rate and stops at its duration.
- If `crawl-estimate` fails (non-zero exit, no output): propose 30 min and ask without a basis (« Pas d'estimation : 30 min proposées »); in autonomous mode use 30.
- Autonomous mode: do not ask. Use `proposed_minutes` and write it in `crawl_minutes`. No `defer`: nothing is left open.
- `reuse-context` never copies `crawl_minutes`: it depends on the response time measured on this run.

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
| in a container | `docker compose -f <dossier sur l'hôte>/docker-compose.yml exec -T <service> <commande ci-dessus>` or `docker exec <conteneur> <commande ci-dessus>`, with the folder inside the container in the command |

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- php <dossier>/artisan --version
```

Exit 0 for each repo concerned: the application CLI is there. That does not prove the database answers (`wp core version` never touches it), so then test "BDD" with a real query. `--app-dir` is the folder of step 2 for an application on the host; for an application in a container (`--container <conteneur>` or `--compose-service <service>`), it is the folder inside the container (the `container` of its `ssh.app_dirs` entry), and `--compose-file` is the absolute path of the compose file on the host:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> --sql <drush|wp|artisan> --app-dir <dossier> [--container <conteneur> | --compose-service <service> --compose-file <dossier sur l'hôte>/docker-compose.yml] "SELECT 1"
```

Exit 0: "BDD" available. On a TLS certificate error in the output (« self-signed certificate », « SSL »), retry the same command with `--skip-ssl` (drush and wp only): the client then connects without TLS, so the queries travel in clear between the server and the database. If it passes, write `ssh.sql_skip_ssl: true` in `context.json` ("BDD" available). Otherwise, or on exit 1 (CLI missing, shared hosting), "BDD" is unavailable.

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

## 10.1 Account for a restricted role

After `rows.json` is read and the coverage is computed, before « Lancer l'audit »: a selected row that needs an account with a restricted role (a non-administrator role, for instance CQ-115) cannot be evaluated with the account of the lead. The skill never reads nor types that account. The row is evaluated at the end of the audit, under the restricted session, after the other session lots, so the decision is always recorded as a deferred `question` (handled at the end in both modes, SKILL.md section 8, step 5):

- Autonomous mode: ask nothing. `cqa_plan.py defer "$RUN" --kind question --detail "La ligne CQ-<n> demande un compte à rôle restreint (<rôle>). Connectez-vous avec ce compte dans Chrome, puis répondez." --refs CQ-<n>`.
- Interactive mode: ask « La ligne CQ-<n> demande un compte à rôle restreint (<rôle>). En avez-vous un pour vous connecter vous-même dans Chrome ? » with « Oui, j'en ai un » and « Non, laisser la ligne non évaluée ». « Oui »: record the same `question` entry as above. « Non »: the row stays `unknown` and its detail says « Compte à rôle restreint manquant. ».

## 11. What the lead sees

1. The table of checks, in French: « Contrôle | Résultat | Outil couvert ».
2. The coverage: « <evaluable>/<total> lignes évaluables, dont <evaluable_human> avec validation humaine ; <partial> partielles (un outil manque) ; <not_evaluable> non évaluables ; <deferred> reportées à J0 / J+7 ; <na> non applicables (techno) ; <standby> en standby. » Add « <n> sans fiche dans le modèle » when there are some.
3. The gains, one line each: « <action> : +<rows> lignes (<tool>) ».
4. The gaps found: repo not on `develop` up to date, preprod behind, `PROJECT.md` that cites another repo.

With `diagnostic`, stop here: « Diagnostic terminé, rien n'a été écrit dans Notion. Dossier du run : <run_dir>. » Otherwise ask « Que faire ? » with « Lancer l'audit » and « Compléter mon poste d'abord ». The second stops the skill: a later launch offers to resume this run (SKILL.md, section 3), and redoes the diagnostic.
