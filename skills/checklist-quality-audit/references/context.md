# Context (section 4)

Goal: the `context.json` of the run, accepted by `cqa_plan.py context --check`. Ask one question at a time, with AskUserQuestion, in French, and only what is missing or must be confirmed. Every Bash call starts with the `S=` line of SKILL.md (here unexpanded) and `RUN="<run_dir>"`.

Values to type (URL, identifier, password, path): AskUserQuestion needs 2 to 4 options, so each question below gives real options, and the question text says « (saisir la valeur dans « Autre ») ». When the free-text answer is not available, ask the same question as a plain message and wait for the reply. Never repeat the password back.

## 1. Project and repos

Output of `cqa_plan.py project "$PWD"` (section 3 ran it):

- `{"slug", "name", "repos": [<repo of the current folder>], "candidates": [<repos beside it, same "Client - Projet" prefix, same GitLab group>]}`.
- Each repo: `{"path", "name", "stack", "techno", "remote", "web_url", "project_md", "protected"}`.
- `project_md` is `null` without `PROJECT.md`, else `{"lead", "devs", "prod_url", "preprod_url", "protection", "repo_url", "branches": {"prod", "preprod"}, "vaultwarden"}`.
- `protected` is false when "Accès protégés ?" is empty, « Non », « Aucun » or « N/A ».

Exit 2: the current folder is not in a git repo, or its name is not « Client - Projet - Techno ». Show the message and stop: the lead launches the skill from a repo of the project.

Question « Repos » : « Repos du projet : <name> (<path>), <name> (<path>). Est-ce la bonne liste ? »
- Options: « Oui », « Seulement <name du repo courant> »; the path of a missing repo in « Autre ».
- A repo added by path: run `cqa_plan.py project "<path>"` and take its `repos[0]`.

Then:
- `PROJECT.md` cites a `repo_url` other than the `web_url` of its repo: say « PROJECT.md cite un autre repo (<repo_url>) : le remote git fait foi. », and keep the remote.
- Repo `name`: keep the one `project` gives (last part of the folder name, as a slug: `laravel`, `nuxt-3`). Two repos with the same name: add `-2` to the second.
- Technos: the `techno` of each repo (`Drupal`, `Wordpress`, `Laravel`, `Nuxt`), each once.
- A repo whose `techno` is `null` (Vue without Nuxt, unknown stack): « Quelle techno de la checklist concerne le repo <name> ? » with « Nuxt », « Aucune »; another techno of the checklist in « Autre ».

## 2. Notion

Presence of the variables, never their value:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
[ -n "${NOTION_TOKEN:-}" ] && echo "NOTION_TOKEN présent" || echo "NOTION_TOKEN absent"
[ -n "${GM_CQA_REFERENCE:-}" ] && echo "GM_CQA_REFERENCE présent" || echo "GM_CQA_REFERENCE absent"
python3 "$S/cqa_notion.py" whoami
```

- A variable absent: stop with « <variable> est absent de l'environnement : l'exporter dans le profil du shell (valeur dans Vaultwarden), puis relancer Claude Code. »
- `whoami` gives `{"bot_id", "name"}`. Exit 3: token refused or network down.

Question « Copie Notion » : « URL de la copie Notion de la checklist du projet, page ou base (saisir la valeur dans « Autre ») ? » with « Le modèle n'est pas encore dupliqué » and « Annuler l'audit ». The first: stop with « Dupliquez le modèle dans la page Notion du projet, puis relancez. »

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_notion.py" resolve "<URL de la copie>"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_notion.py" resolve "$GM_CQA_REFERENCE"
```

- Each gives `{"page_id", "database_id", "data_source_id"}`. `page_id` is `null` for the URL of a database: no comment on the page then.
- Exit 3, not found: the page is not shared with the integration. « La page n'est pas connectée à l'intégration Notion : l'ajouter dans ses connexions, puis répondre. »
- Exit 2: invalid URL, or a database with several data sources.
- Same `data_source_id` for the copy and the template: « Cette URL est celle du modèle de référence : donner l'URL de la copie du projet. » Ask again; never go on with the template.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" schema "<data_source_id de la copie>" > "$RUN/schema.json"
```

The output goes to `schema.json`, the errors still to stderr. By exit code:

- 0: `schema.json` holds `{"ok", "problems", "assignee_options"}`. Go on.
- 6: read `problems` in `schema.json` (Read), show them and stop: « La copie n'a pas le schéma v2 attendu : <problèmes>. Dupliquer à nouveau le modèle. »
- 3: Notion API error (message on stderr; `schema.json` is empty). Show it and stop, before `team`.
- 2: show the message and stop.

Lead and team, from the repo that holds `PROJECT.md`:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" team "<chemin du repo>" "$RUN/schema.json"
```

- It gives `{"lead": {"name", "option"}, "team": [{"name", "role", "option"}], "unmatched": [<names>]}`.
- Exit 2, no `PROJECT.md`: ask « Qui fait partie de l'équipe : prénom, nom et rôle front, back ou full-stack (saisir la liste dans « Autre ») ? » with « Je suis seul sur le projet » and « Annuler l'audit », then match each name with `assignee_options` yourself: accents and case aside, first name alone, or first name and initial of the surname.
- Confirm the lead: « Vous êtes <lead.name>, option « <lead.option> » de « Assignée à » ? » with « Oui » and up to 3 other options of « Assignée à »; another option in « Autre ». `lead.option` must be an existing option: the plan assigns the lead and signs the comments with it.
- Each name of `unmatched`: « Quelle option de « Assignée à » correspond à <nom> ? » with up to 3 close options and « Aucune ».
- Never create an option. A person without option stays in `team` with `option: null` and gets no row.

## 3. Target

- `preprod_url`: `project_md.preprod_url`, confirmed with « URL de la préprod : <url> ? » (« Oui », « Annuler l'audit »; another URL in « Autre »). Missing: « URL de la préprod (saisir la valeur dans « Autre ») ? » with « Continuer sans préprod » and « Annuler l'audit ». Scheme, host and base path only: no `?`, `#` or credentials.
- `prod_url`: `project_md.prod_url`, else `null`.
- `prod_live`: asked only with `--phase "MEL J0"` or `--phase "Post-MEL J+7"`: « La prod (<prod_url>) est-elle en ligne ? » (« Oui », « Non »). Otherwise false. True needs a `prod_url`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_plan.py" probe-target "<preprod_url>"
```

It gives `{"status", "access"}`, plus `location` for a redirect it did not follow.

| `access` | Answer | What to do |
|---|---|---|
| `open` | 2xx or 3xx | nothing |
| `basic_auth` | 401 | ask the credentials (below) |
| `ip_filtered` | 403 | say « HTTP indisponible hors réseau agence » |
| `unreachable` | no answer, or another status | say « La préprod ne répond pas depuis ce poste (<status>) : vérifier l'URL ou le VPN. » |

With `location`, the preprod redirects to another origin: show it and ask « La préprod redirige vers <location>. Quelle adresse auditer ? » with « <location> » and « <preprod_url> ».

Prod run (`prod_live` true with a J0 or J+7 phase): the toolbox and the sub-agents target `prod_url`. Run `probe-target` on `prod_url` as well; `target.access` is then the answer of the prod.

Credentials, for `basic_auth` only:
1. « La préprod demande une authentification Basic Auth. Identifiant (saisir la valeur dans « Autre ») ? » with « Continuer sans identifiants » and « Annuler l'audit ».
2. « Mot de passe Basic Auth (saisir la valeur dans « Autre ») ? » with « Continuer sans identifiants » and « Annuler l'audit ». Never repeat it back, never show it in a recap.
3. Check them. Inside the single quotes, write each `'` of a value as `'\''`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' python3 "$S/cqa_plan.py" probe-target "<preprod_url>" --auth-env
```

- `open`: keep both in the session for section 6 and the secret checks.
- Still `basic_auth`: « Identifiants refusés par la préprod. » Ask once more, then go on without them.
- Exit 2 on an `http://` URL: credentials are never sent in clear; go on without them.
- `target.access` stays the answer without credentials (`basic_auth`): the sub-agents never authenticate, only the toolbox does.

## 4. SSH alias

```bash
grep -iE '^[[:space:]]*(Host|Include)[[:space:]]' ~/.ssh/config
```

- Show only alias names (also from the included files), never `HostName`, user or key.
- Question « Alias SSH » : « Quel alias SSH utiliser pour la préprod (ou saisir l'alias dans « Autre ») ? » with up to 2 aliases whose name contains a word of the project slug, then always « Autre alias » and « Aucun »; another alias in « Autre ». « Autre alias » chosen without a typed value: ask the alias as a plain message.
- `ssh.alias` is the choice, or `null`. `ssh.app_dirs` is filled during the diagnostic: `{"<repo name>": "<folder on the server>"}`, or, for an application in a container, `{"<repo name>": {"host": "<folder on the server>", "container": "<folder inside the container>"}}`. Use `container` for `--app-dir` of the SQL queries in a container, `host` otherwise. In the container form, `host` is optional (or `null`) when the code exists only in the image: the server commands on the folder then run through `docker exec <conteneur>` with `container`. `null` means the folder is not known (« Je ne sais pas »): the entry is skipped. A relative folder left by a run before 0.12 is tolerated: `context --check` lists it in `warnings` and it is asked again. `ssh.sql_skip_ssl` (optional boolean, default `false`) is set to `true` by the diagnostic when SQL queries only work with `--skip-ssl`.

## 5. Test accounts

Question « Comptes » : « Disposez-vous de comptes de test, pour vous connecter vous-même dans Chrome pendant l'audit ? » with « Front et back-office », « Front seulement », « Back-office seulement », « Aucun ».

- `test_accounts` = `{"front": <bool>, "backoffice": <bool>}`. The skill never reads nor types these accounts.
- With a back-office: « URL de connexion du back-office (ou saisir l'URL dans « Autre ») ? » with the default of the stack when there is one (Drupal `<preprod_url>/user/login`, WordPress `<preprod_url>/wp-admin/`), then always « Saisir l'URL » and « Pas de back-office à auditer »; another URL in « Autre ». « Saisir l'URL » chosen without a typed value: ask the URL as a plain message. It becomes the key page « Back-office ».

## 6. Key pages

One page per template, 10 at most: Accueil, Liste, Détail, Formulaire, Recherche, Page légale, 404, Back-office, and so on.

Sources, when `access` is `open` (unauthenticated GET only):

```bash
curl -q -sS --max-time 20 -A "GingermindsAudit/<plugin_version>" "<preprod_url>/sitemap.xml" | head -c 20000
```

```bash
curl -q -sS --max-time 20 -A "GingermindsAudit/<plugin_version>" "<preprod_url>/" | grep -oE 'href="[^"]+"' | sort -u | head -100
```

- Routes of the code: Nuxt `pages/`; Laravel `routes/web.php` (back-office); Drupal content types and views in `config/sync/`; WordPress templates of the theme (`archive-*.php`, `single-*.php`, `page-*.php`).
- WordPress with an SSH alias: the « Formulaire » page, found through the guard (the only `wp post list` form it allows; `url` is a field of this command):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- wp post list --post_type=page --s=gravityform --fields=ID,post_title,post_status,url --path=<dossier>
```

  `<dossier>` is the WordPress folder found in section 6.1, step 1. Keep only the pages whose `post_status` is `publish`, and propose the `url` of one as the key page « Formulaire »; none found or a guard refusal: go on without it, never work around the guard.
- 404: always proposed, counted in the 10, template written exactly `404`: `<preprod_url>/gm-cqa-404-<8 random hex>` (8 random hexadecimal characters, new for each run). On a prod run, the same path on the host of `prod_url`. A `404` page copied from an earlier run keeps its URL.
- Other `access`: routes of the code only; the lead completes the URLs.

Show the list (template, URL), then « Valider ces pages clés ? » with « Valider » and « Annuler l'audit »; corrections in « Autre », in one answer. Absolute URLs on the preprod host, or on the back-office host. On a prod run, the same pages on the host of `prod_url`.

## 6.1 Other sites (multisite)

A project can serve several sites. The back-office is usually shared by all of them, but a site can have one of its own, so the questions are asked here, in the context step, never asked after the audit starts (the lead is present now, and the launch questions of the next step come later). One question at a time, in French.

Detection of the other domains, in this order:

1. WordPress with an SSH alias: first locate the folder through the guard, then list the sites through it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- find /var/www /srv /app /opt -maxdepth 3 -name wp-load.php
```

A root missing on the server makes `find` exit 1: keep the paths it printed (the lines starting with `find:` are the missing roots). Then, with the folder:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- wp site list --field=url --path=<dossier>
```

   The folder is the one holding `wp-load.php` (`wp-config.php` is refused by the guard as a secret carrier) (`ssh.app_dirs`, when already known, is reused). WordPress in a container: run both commands through `docker exec <conteneur>` (`-- docker exec <conteneur> find ...`, `-- docker exec <conteneur> wp site list ...`), with the folder inside the container as `--path`. Several candidates or none: ask « Dossier de WordPress sur le serveur (ou saisir le chemin dans « Autre ») ? » with the candidates and « Je ne sais pas ». `wp site list --field=url` is the only `wp site` command the guard allows, and a guard refusal is never worked around (the guard is not widened). One URL per line. Any failure (no alias, no folder found, not a multisite, wp error) falls through to step 2, keeping the domains already found.
2. Otherwise the domains listed in the `CLAUDE.md` or `PROJECT.md` of the project.
3. Otherwise ask « Autres domaines du projet en préprod ? » with « Aucun, un seul site » and « Saisir les domaines » (hosts in « Autre », separated by commas).

Show the domains found as a proposal for the lead to confirm or correct. No other domain: ask « Connexion par SSO ? » once for the preprod site (its `backoffice` is `commun`), then write `sites` only if it is `true`; otherwise no `sites` key, the project is a single site. With other domains: the preprod site (the host of `target.preprod_url`) is the first entry, always `"backoffice": "commun"`, and gets « Connexion par SSO ? » once, like the others. For each other domain, one question at a time:

- « Back-office commun ou propre à ce site ? » with « Commun » and « Propre à ce site ».
- « Connexion par SSO ? » with « Oui » and « Non ».

`sites` (optional top-level list, 10 at most, unique domains) is `[{"domain": "<host>", "backoffice": "commun" or "propre", "sso": <bool>, "home": "<URL of the home page>"}]`, the preprod site included. `home` is an absolute URL on the host `domain`. More than 10 domains: keep the 10 the lead picks and say so. A `context.json` without `sites` is a single site.

The browser may only visit the hosts of `target.preprod_url`, of `sites` and of `key_pages` (plus `target.prod_url` on a prod run). Each `home` is visited in addition to the 10 key pages, so the sites never use their places.

## 6.1.1 Third-party plugins (WordPress only)

Only themes and custom plugins are analysed: the code of third-party plugins is outside the scope of the code rows and of the toolbox. `wp-plugins` reads `$RUN/context.json`: in a new run, before it, write a first `context.json` holding `repos` (from `project`), `target` and `sites`; step 8 of the skill completes and checks it. For each WordPress repo (`stack` `wordpress`), run `python3 "$S/cqa_plan.py" wp-plugins "$RUN"`. It lists, read-only from the commit of the repo (`git ls-files`, `readme.txt` and the header of the main PHP file, 64 KiB at most each, the plugins' code is never run), each folder `plugins/<name>` and `mu-plugins/<name>` wherever it sits (`wp-content/plugins/`, `web/app/plugins/`, `src/plugins/`), as `{"repo", "path", "third_party", "why"}`. `third_party` is true for a short list of well-known plugins, a `readme.txt` with a `Stable tag:`, or a `Plugin URI` or `Author URI` outside the domains of the project (the preprod, the prod, the sites and the host of the repo, so an agency `Author URI` is not third-party); anything else is proposed as custom. The `unreadable` key, when present, names a repo whose files could not be listed: say so and go on.

No plugin found: no question. Otherwise ask « Plugins maison à analyser ? » as a multi-select question (4 options at most per question: split the list in several questions, one at a time, when it is longer), with the plugins proposed as custom checked and the others listed as third-party with their `why`. The lead corrects the list in « Autre ». Every plugin that stays unchecked goes into `repos[].third_party_paths` of its repo (the `path` as listed, relative to the repo root; 200 at most); with none to exclude, leave the key out. `reuse-context` copies `third_party_paths` per repo, so an earlier answer is proposed again, never asked after the audit starts (the lead is present now, and the launch questions of the next step come later).

## 6.2 Without PROJECT.md

`project_md` is `null` (exit 2 of `team`, no preprod URL): do not ask everything from scratch. Read the `CLAUDE.md` of the repo and the Markdown files of `docs/` (Read, only these), look for the preprod and prod URLs, the other domains and the people of the team (name, role), and propose what you found for the lead to confirm or correct, one question at a time, in the questions of sections 2, 3 and 6.1 (the `team` matching with `assignee_options` stays as in section 2). Nothing found: ask as usual. At the end of step 6 of section 4 of SKILL.md (key pages and multisite, the lead being present), offer « Générer un PROJECT.md à partir de ces réponses ? » with « Oui » and « Non » : on « Oui », write `PROJECT.md` at the root of the repo from the answers (lead, team, URLs, protection, branches) with Write, following the layout of the `PROJECT.md` of the other projects when the lead gives one; never commit it. This is a context question: it is asked before the lead leaves, in autonomous mode too.

## 6.3 Earlier run

`cqa_plan.py reuse-context` (SKILL.md, section 3) pre-fills `context.json` from a previous run. Ask again only: the probe of the target (section 3 above, `probe-target`, and the credentials); `prod_live` (the question of section 3 above, only with `--phase "MEL J0"` or `--phase "Post-MEL J+7"`), and on a prod run the key pages again on the host of `prod_url` (section 6); then `cqa_plan.py key-pages-check "$RUN"` (`--auth-env` with the credentials). Look only at the pages with `checked` true. A page with `gap` true and a `status` of 404: « La page <template> (<url>) répond 404. Quelle URL la remplace ? » with « Retirer cette page » and « Garder cette page » (the new URL in « Autre »). A `checked` page whose `status` is `null`: say « <url> ne répond pas » (not 404) and ask the same question. A page with `checked` false was not requested (another origin, or the time limit): it is neither a gap nor a 404. The page of the template « 404 » is expected to answer 404: it is never asked for a replacement because of it, whatever it answers (a non-404 answer is reported by `soft_404`, never as a gap to replace). Everything else is kept as copied.

## 7. context.json

Write `$RUN/context.json` with Write. Example (values to replace):

```json
{
  "schema": 1,
  "run_id": "2026-10-20-1430",
  "plugin_version": "0.9.0",
  "project": {"slug": "acme-shop", "name": "Acme - Shop", "protection": "Basic Auth sur la préprod"},
  "lead": {"name": "Camille Bernard", "option": "Camille B"},
  "team": [{"name": "Inès Petit", "role": "front", "option": "Inès"}, {"name": "Hugo Moreau", "role": "back", "option": null}],
  "repos": [
    {"name": "laravel", "path": "/home/camille/Projets/Acme - Shop - Laravel", "remote": "git@gitlab.example.org:acme/back.git",
     "web_url": "https://gitlab.example.org/acme/back", "stack": "laravel", "techno": "Laravel",
     "audited_sha": null, "preprod_branch": "release", "deployed_sha": null, "aligned": null}
  ],
  "technos": ["Laravel", "Nuxt"],
  "target": {"preprod_url": "https://preprod.example.org", "prod_url": "https://www.example.org", "prod_live": false, "access": "basic_auth"},
  "ssh": {"alias": "acme-preprod", "app_dirs": {}},
  "test_accounts": {"front": false, "backoffice": true},
  "key_pages": [{"template": "Accueil", "url": "https://preprod.example.org/"}],
  "sites": [{"domain": "preprod.example.org", "backoffice": "commun", "sso": false, "home": "https://preprod.example.org/"}],
  "notion": {"copy_page_id": "<page_id de la copie ou null>", "copy_data_source_id": "<data_source_id de la copie>",
             "reference_data_source_id": "<data_source_id du modèle>", "reference_page_id": "<page_id du modèle ou null>",
             "bot_id": "<bot_id de whoami>",
             "assignee_options": ["Camille B", "Inès"]},
  "available_tools": [],
  "phase_filter": null,
  "dry_run": false,
  "mode": "interactif",
  "notion_write": "all"
}
```

- `run_id`: the name of the run folder. `plugin_version`: the `version` of `.claude-plugin/plugin.json`, two folders above this skill.
- `project`: `slug` and `name` from `project`; `protection`: `project_md.protection`, else `null`.
- `repos[]`: `name` from `project` (letters, digits, `.`, `-`, `_`; unique), `path` absolute (no tab, no line break), `remote`, `web_url`, `stack`, `techno` from `project`. `preprod_branch`: `project_md.branches.preprod`, else `release`. `audited_sha`, `deployed_sha` and `aligned` stay `null` until the diagnostic.
- `sites`: optional, see section 6.1; leave it out for a single site.
- `repos[].third_party_paths`: optional, see section 6.1.1: folders of third-party code (plugins) relative to the repo root, no `..`, no leading `/`, no `\`, no empty segment, 200 at most. Matched by segment; the toolbox leaves them out. Any other value is refused by `context --check`.
- `target.access`: `open`, `basic_auth`, `ip_filtered` or `unreachable`.
- `notion`: `copy_page_id` from the resolve of the copy (`null` for a database URL), `copy_data_source_id`, `reference_data_source_id` and `reference_page_id` (`null` for a database URL) from the resolve of `GM_CQA_REFERENCE`: the writes refuse this page and this data source (exit 8), `bot_id` from `whoami`, `assignee_options` from `schema.json`.
- `available_tools`: `[]` until the diagnostic. `phase_filter`: the value of `--phase`, else `null`. `dry_run`: true with `--dry-run`.
- `mode` (optional, default `interactif`): `"autonome"` or `"interactif"`, the answer to « Mode 100 % autonome ? » (SKILL.md, section 4). `notion_write` (optional): `"all"`, `"keep_human"` or `"none"`, the answer to « Écrire dans la copie Notion à la fin de l'audit ? », `null` while not asked (section 7 then asks it, as before). A run without these keys behaves as an interactive run that asks at section 7. Any other value is refused by `context --check`.
- `crawl_minutes` (optional, integer from 5 to 240, default 30): the maximum duration of the crawl, answered at the diagnostic (`references/diagnostic.md`, section 6, « Durée maximale du crawl ? »; in autonomous mode the proposed duration). It is not reused from an earlier run. `toolbox-env` derives the URL cap from it. Any other value is refused by `context --check`.
- Never a secret: no credential, no token.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

It prints `{"ok", "errors"}`. Exit 6: fix every error listed, write the file again and check again, until `ok` is true.
