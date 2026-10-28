# Server: SSH and database, read-only (spec § 8)

Every server command of the skill goes through the guard `cqa_ssh_ro.py`, with the alias the lead chose in section 4. The only exception is the connection test of the diagnostic, `ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> true`, which runs nothing. The `server` lot always goes to `gm:cqa-auditor`, which uses the same guard. When "SSH serveur" or "BDD" is unavailable, its dispatch says so (« SSH serveur indisponible. », « BDD indisponible. ») and it gives `unknown` for the parts that need them, with the command to run in `human_check`. This page also serves the orchestrator in the diagnostic (deployed commit, application CLI).

## Agency rule

- Read commands (files, logs, status, monitoring) run without confirmation, through the guard.
- Any write, change, deletion, restart, or command of uncertain impact: stop and ask the lead, stating the impact. This holds even when the lead asks to go faster.
- v1 is read-only: the skill never runs a write on a server, even confirmed, and never a command outside the guard (the connection test aside). Give the command to the lead, who runs it if they want.

## Modes of the guard

Every Bash call starts with the `S=` line of SKILL.md (here unexpanded).

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> -- <commande...>
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" --check -- <commande...>
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> --sql drush --app-dir <dossier> "SELECT COUNT(*) FROM users LIMIT 1"
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> --env-keys <dossier>/.env APP_ENV APP_DEBUG
```

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> --php-ini display_errors expose_php
```

- `--`: one command of the allowlist, run over SSH; the output comes back masked.
- `--check --`: says whether a command would pass, without SSH (`{"allowed", "reason"}`).
- `--sql drush|wp|artisan --app-dir <dossier>`, plus `--container <conteneur>` or `--compose-service <service>` for an application in a container: one read-only query, sent on standard input through the CLI of the application, which already knows the credentials.
- `--skip-ssl` (with `--sql drush|wp`, not artisan): skips the TLS check of the database client, for a self-signed certificate. When `ssh.sql_skip_ssl` is `true` in `context.json`, add `--skip-ssl` to every `--sql` query.
- `--env-keys <fichier> KEY...`: reads a `.env` or `wp-config.php`. Only `APP_ENV`, `APP_DEBUG`, `APP_URL`, `WP_DEBUG`, `WP_ENVIRONMENT_TYPE`, `LOG_LEVEL` and `SESSION_SECURE_COOKIE` show their value; any other key shows only its presence and length.
- `--php-ini KEY...`: reads `expose_php`, `display_errors`, `memory_limit`, `upload_max_filesize`, `post_max_size`, `max_execution_time`, `session.cookie_secure`, `session.cookie_httponly`, `session.cookie_samesite`, `opcache.enable`. It reads the configuration of the PHP command line, which can differ from PHP-FPM: say so when it matters.

## Allowlist (default deny)

- Files: `cat`, `head`, `tail` (never `-f`), `grep` (never recursive), `ls`, `stat`, `find` (never `-exec`, `-ok`, `-delete`, `-fprint`, `-fls`).
- System: `ps` (no `e` modifier), `df`, `du`, `free`, `uptime`, `systemctl status`, `is-active` and `list-timers`, `journalctl -n <500 au plus> --no-pager`, `crontab -l`.
- PHP: `php -v`, `php -m`, `php --ini`. `php -i` is refused (it prints the environment): use `--php-ini`.
- Git, to read the deployed commit: `git -C <dossier> rev-parse HEAD` and `git -C <dossier> log -1 --format=<format>`.
- Web servers: `nginx -v`, `apachectl -v`.
- Applications: `drush status`, `pm:list`, `pm:security`, `--version`; `wp core version`, `plugin list`, `theme list`, `cron event list`, `option get <nom>` (names that look like a secret refused), `--version`; `php artisan about`, `route:list`, `schedule:list`, `--version`. Binary forms: `drush`, `vendor/bin/drush` or an absolute `<dossier>/vendor/bin/drush`; `wp` alone; `php artisan` or `php <dossier absolu>/artisan`. An absolute path under `/tmp`, `/var/tmp`, `/dev/shm`, `/home`, `/root` or `/run/user` is refused (a file planted there would run).
- Containers: `docker ps`, `docker compose ps`, and `docker exec` or `docker compose exec` of an allowed command. `-T` goes only with `docker compose exec`, never with `docker exec`. `docker inspect`, `env`, `printenv`, `set` and `/proc/<x>/environ` are refused: they show secrets.
- Always refused: `sudo`, `su`, `sh`, `bash`, any shell metacharacter (`;`, `|`, `&`, `>`, `<`, backquote), `$`, globs, `~`, `..`, non-ASCII characters, reads in `/proc`, `/sys`, `/dev`.
- Files that carry secrets (`.env*`, `wp-config.php`, `settings*.php`, `.htpasswd`, `auth.json`, keys and certificates, `.git/config`, shell histories): raw read refused, use `--env-keys`.

Folder of an application (diagnostic): ask the lead, with the candidates of `ls /var/www` and of `find /var/www /srv -maxdepth 3 -name <repère>`, the marker being `artisan` (Laravel), `wp-content` (WordPress) or `drush` with `-maxdepth 4` (Drupal, in `vendor/bin`).

## Database

- Through `--sql` only, via the CLI of the application: `drush sql:query`, `wp db query`, `php artisan db`. The guard sends the query on standard input; never pipe it yourself.
- One statement: `SELECT`, `SHOW`, `DESCRIBE`, `DESC` or `EXPLAIN`, at most one final `;`, no comment, no `INTO`, no `\`, no function that reads a file, waits or has a side effect, no write or lock keyword, nothing that targets passwords, tokens or secrets, no `*` in a select list (`COUNT(*)` allowed). A query that names `users`, `wp_users`, `users_field_data`, `sessions` or a `*_tokens` table selects only `COUNT(...)`; `SHOW` and `DESCRIBE` stay allowed on them. Prefer counts, always with `LIMIT`.
- The guard first puts the session in read-only mode with `SET SESSION TRANSACTION READ ONLY`, which targets MySQL and MariaDB. PostgreSQL is not supported in v1: the query is refused or fails, and the row part is `unknown`.
- The connection credentials are never read.

## PHP in an uploads folder (CQ-97, CQ-144)

Never request a `.php` file under `wp-content/uploads` (or any uploads folder) to prove that the server runs it: a GET executes the script. Read the configuration instead, through the guard:

1. Find the vhost: `ls /etc/nginx/sites-enabled`, `/etc/nginx/conf.d` or `/etc/apache2/sites-enabled`, then `cat` the file for the site (`grep` is not recursive).
2. nginx: look for a `location ~ \.php$` block passed to PHP-FPM and for a `location` on the uploads path that comes first and carries `deny all` (or `return 403`). A `location ~ \.php$` that also matches the uploads path, with no such `deny`, means PHP runs there: `ko`.
3. Apache: `cat` the `.htaccess` of the uploads folder and the vhost, look for `php_flag engine off`, `RemoveHandler .php`, `Require all denied` or a `FilesMatch` on `\.php`.
4. A container or a front proxy that hides the config: the part is `unknown`, with the file you could not read.

Cite the config file and the matching lines as `file` evidence, never a secret.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | success, masked output on stdout | use it |
| 1 | remote failure: command missing (shared hosting), file absent, SSH down | the part is `unknown`, with the reason |
| 2 | usage error | fix the call |
| 4 | refused: « commande à faire confirmer par le lead » | see below |

Exit 4:

1. Never rephrase the command to get it through. If the refusal names a dedicated mode (`--env-keys`, `--php-ini`), use it.
2. Never run it outside the guard: v1 sends every server command through it.
3. In the orchestrator, ask the lead, with the impact: « Le garde-fou SSH a refusé : <commande> (<raison>). Impact : <lecture seule, ce qu'elle affiche, ou ce qu'elle modifierait>. » with « Passer » and « Arrêter l'audit »; the lead who runs it himself pastes the useful result in « Autre ». That result is used as their answer, never written raw to the run directory.
4. In a sub-agent, the part becomes `unknown`, with « Commande à faire confirmer par le lead : <commande> (lecture seule, <ce qu'elle affiche>) » in `human_check`: the lead sees it in the Notion comment.

## Facts for the browser lots

The `server` lot writes `facts/server.json`, read by the browser lots for the mixed rows:

```json
{"facts": [{"refs": [88], "topic": "Affichage des erreurs PHP", "command": "--php-ini display_errors", "exit": 0, "result": "display_errors=Off (PHP CLI)"}]}
```

`command` is what follows the alias; `result` is a short French text, without any secret.
