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
- `ssh.alias` is the choice, or `null`. `ssh.app_dirs` (`{"<repo name>": "<folder on the server>"}`) is filled during the diagnostic. `ssh.sql_skip_ssl` (optional boolean, default `false`) is set to `true` by the diagnostic when SQL queries only work with `--skip-ssl`.

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
- 404: `<preprod_url>/cqa-page-inexistante-<4 chiffres>`.
- Other `access`: routes of the code only; the lead completes the URLs.

Show the list (template, URL), then « Valider ces pages clés ? » with « Valider » and « Annuler l'audit »; corrections in « Autre », in one answer. Absolute URLs on the preprod host, or on the back-office host. On a prod run, the same pages on the host of `prod_url`.

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
  "notion": {"copy_page_id": "<page_id de la copie ou null>", "copy_data_source_id": "<data_source_id de la copie>",
             "reference_data_source_id": "<data_source_id du modèle>", "reference_page_id": "<page_id du modèle ou null>",
             "bot_id": "<bot_id de whoami>",
             "assignee_options": ["Camille B", "Inès"]},
  "available_tools": [],
  "phase_filter": null,
  "dry_run": false
}
```

- `run_id`: the name of the run folder. `plugin_version`: the `version` of `.claude-plugin/plugin.json`, two folders above this skill.
- `project`: `slug` and `name` from `project`; `protection`: `project_md.protection`, else `null`.
- `repos[]`: `name` from `project` (letters, digits, `.`, `-`, `_`; unique), `path` absolute (no tab, no line break), `remote`, `web_url`, `stack`, `techno` from `project`. `preprod_branch`: `project_md.branches.preprod`, else `release`. `audited_sha`, `deployed_sha` and `aligned` stay `null` until the diagnostic.
- `target.access`: `open`, `basic_auth`, `ip_filtered` or `unreachable`.
- `notion`: `copy_page_id` from the resolve of the copy (`null` for a database URL), `copy_data_source_id`, `reference_data_source_id` and `reference_page_id` (`null` for a database URL) from the resolve of `GM_CQA_REFERENCE`: the writes refuse this page and this data source (exit 8), `bot_id` from `whoami`, `assignee_options` from `schema.json`.
- `available_tools`: `[]` until the diagnostic. `phase_filter`: the value of `--phase`, else `null`. `dry_run`: true with `--dry-run`.
- Never a secret: no credential, no token.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" context "$RUN" --check
```

It prints `{"ok", "errors"}`. Exit 6: fix every error listed, write the file again and check again, until `ok` is true.
