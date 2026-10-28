# Server: SSH and database, read-only (spec § 8)

Every server command of the skill goes through the guard `cqa_ssh_ro.py`, with the alias the lead chose in section 4. The only exception is the connection test of the diagnostic, `ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> true`, which runs nothing. The `server` lot always goes to `gm:cqa-auditor`, which uses the same guard. When "SSH serveur" or "BDD" is unavailable, its dispatch says so (« SSH serveur indisponible. », « BDD indisponible. ») and it gives `unknown` for the parts that need them, with the command to run in `human_check`. This page also serves the orchestrator in the diagnostic (deployed commit, application CLI).

## Agency rule

- Read commands (files, logs, status, monitoring) run without confirmation, through the guard.
- Any write, change, deletion, restart, or command of uncertain impact: stop and ask the lead, stating the impact. This holds even when the lead asks to go faster.
- Autonomous mode (`mode` is `autonome`) changes nothing here: only read commands run without confirmation, and a command that needs the lead's confirmation is never run, even in autonomous mode. It is set aside (see exit 4 below) and put to the lead at the end of the audit.
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
- `--sql drush|wp|artisan --app-dir <dossier>`, plus `--container <conteneur>` or `--compose-service <service>` (with `--compose-file <absolute path of the compose file on the server>`, needed because the SSH command does not run in the project folder) for an application in a container: one read-only query, sent on standard input through the CLI of the application, which already knows the credentials. In a container, `--app-dir` is the folder inside the container: with an `ssh.app_dirs` entry that has `host` and `container`, use `container` for `--app-dir` of the SQL queries in a container, `host` otherwise.
- `--skip-ssl` (with `--sql drush|wp`, not artisan): the database client connects without TLS (encryption off, not only the certificate check), for a database with a self-signed certificate: the query and its answer travel in clear between the server and the database, often like the connection of the application itself. When `ssh.sql_skip_ssl` is `true` in `context.json`, add `--skip-ssl` to every `--sql` query.
- `--env-keys <fichier> KEY...`: reads a `.env` or `wp-config.php`. Only `APP_ENV`, `APP_DEBUG`, `APP_URL`, `WP_DEBUG`, `WP_ENVIRONMENT_TYPE`, `LOG_LEVEL` and `SESSION_SECURE_COOKIE` show their value; any other key shows only its presence and length.
- `--php-ini KEY...`: reads `expose_php`, `display_errors`, `memory_limit`, `upload_max_filesize`, `post_max_size`, `max_execution_time`, `session.cookie_secure`, `session.cookie_httponly`, `session.cookie_samesite`, `opcache.enable`. It reads the configuration of the PHP command line, which can differ from PHP-FPM: say so when it matters.

## Allowlist (default deny)

- Files: `cat`, `head`, `tail` (never `-f`), `grep` (never recursive), `ls` (`--time-style=<format>` allowed), `stat`, `find` (never `-exec`, `-ok`, `-delete`, `-fprint`, `-fls`).
- System: `date` (to print the time only: never `-s`, `--set`, `-f` or a date as argument, which would set the clock), `ps` (no `e` modifier), `df`, `du`, `free`, `uptime`, `systemctl status`, `is-active` and `list-timers`, `journalctl -n <500 au plus> --no-pager`, `crontab -l`.
- PHP: `php -v`, `php -m`, `php --ini`. `php -i` is refused (it prints the environment): use `--php-ini`.
- Git, to read the deployed commit: `git -C <dossier> rev-parse HEAD` and `git -C <dossier> log -1 --format=<format>`.
- Web servers: `nginx -v`, `apachectl -v`.
- Applications: `drush status`, `pm:list`, `pm:security`, `--version`; `wp core version`, `plugin list`, `theme list`, `cron event list`, `site list --field=url` (no other form), `option get <nom>` (only the options without secret listed below), `--version`, never `--fields` with `args` (cron arguments) or `update_package` (package URL with a license key); `php artisan about`, `route:list`, `schedule:list`, `--version`. Binary forms: `drush`, `vendor/bin/drush` or an absolute `<dossier>/vendor/bin/drush`; `wp` alone; `php artisan` or `php <dossier absolu>/artisan`. An absolute path under `/tmp`, `/var/tmp`, `/dev/shm`, `/home`, `/root` or `/run/user` is refused (a file planted there would run).
- Containers: `docker ps`, `docker compose ps`, and `docker exec` or `docker compose exec` of an allowed command. `-T` goes only with `docker compose exec`, never with `docker exec`. `docker inspect`, `env`, `printenv`, `set` and `/proc/<x>/environ` are refused: they show secrets.
- Always refused: `sudo`, `su`, `sh`, `bash`, any shell metacharacter (`;`, `|`, `&`, `>`, `<`, backquote), `$`, globs, `~`, `..`, non-ASCII characters, reads in `/proc`, `/sys`, `/dev`. So `grep -E 'a|b'` is refused, in `docker exec` too: run one `grep` per pattern.
- WordPress options readable with `wp option get`: `siteurl`, `home`, `blogname`, `blogdescription`, `blog_public`, `WPLANG`, `template`, `stylesheet`, `active_plugins`, `permalink_structure`, `users_can_register`, `default_role`, `timezone_string`, `date_format`, `time_format`, `show_on_front`, `page_on_front`, `page_for_posts`, `posts_per_page`, `default_comment_status`, `comment_registration`, `comment_moderation`, `close_comments_for_old_posts`, `uploads_use_yearmonth_folders`, `wp_page_for_privacy_policy`, `db_version`, `auto_update_plugins`, `auto_update_themes`. Any other option is refused: the settings of the plugins hold API keys and passwords in their arrays.
- Files that carry secrets (`.env*`, `wp-config.php`, `settings*.php`, `.htpasswd`, `auth.json`, keys and certificates, `.git/config`, shell histories): raw read refused, use `--env-keys`.

Folder of an application (diagnostic): ask the lead, with the candidates of `ls /var/www` and of `find /var/www /srv /app /opt -maxdepth 3 -name <repère>`, the marker being `artisan` (Laravel), `wp-content` (WordPress) or `drush` with `-maxdepth 4` (Drupal, in `vendor/bin`). A root missing on the server makes `find` exit 1: keep the paths it printed (the lines starting with `find:` are the missing roots). For an application in a container, run the same `find` through `docker exec <conteneur>` to get the folder inside the container, and record both (`ssh.app_dirs`, `references/context.md` section 4).

## Database

- Through `--sql` only, via the CLI of the application: `drush sql:query`, `wp db query`, `php artisan db`. The guard sends the query on standard input; never pipe it yourself.
- One statement: `SELECT`, `SHOW`, `DESCRIBE`, `DESC` or `EXPLAIN`, at most one final `;`, no comment, no `INTO`, no `\`, no function that reads a file, waits or has a side effect, no write or lock keyword, nothing that targets passwords, tokens or secrets (except the count of hash formats below), no `*` in a select list (`COUNT(*)` allowed). A query that names `users`, `wp_users`, `users_field_data`, `sessions` or a `*_tokens` table selects only `COUNT(...)`; `SHOW` and `DESCRIBE` stay allowed on them. Prefer counts, always with `LIMIT`.
- The guard first puts the session in read-only mode with `SET SESSION TRANSACTION READ ONLY`, which targets MySQL and MariaDB. PostgreSQL is not supported in v1: the query is refused or fails, and the row part is `unknown`.
- The connection credentials are never read.
- Secret columns are never named, in any clause (select list, `WHERE`, `ON`, `GROUP BY`, `HAVING`, `ORDER BY`), not even in a function or compared with `=`: no value and no oracle. They are the columns whose name holds `pass`, `pwd`, `secret`, `token`, `salt`, an API, private, cron or license key, `activation_key` (`user_activation_key`), `recovery_code` or `totp`, plus `consumer_key`; `id`, `sid`, `ssid`, `session`, `payload`, `session_key` and `session_value` of a `sessions` or `*_sessions` table; `meta_value` of a `*usermeta` or `*sitemeta` table; `option_value` of any options table (read an option without secret with `wp option get`); `data` and `value` of the Drupal and Laravel stores (`config`, `key_value*`, `cache*`, `users_data`); `payload` of the job tables. Count rows by another column instead: `SELECT COUNT(*) FROM wp_usermeta WHERE meta_key = 'wp_capabilities' LIMIT 1`. The only exception is the count of password hash formats below.
- The text of the queries of other sessions is refused too, as it can quote a session id or a token: `PROCESSLIST`, `performance_schema`, `sys`, `information_schema.INNODB_*`, `mysql.general_log` and `slow_log`, `SHOW ENGINE`, binlog and relay log events.

Password hash formats (CQ-121): the only query allowed on `user_pass`, `pass` or `password` counts rows by length or by prefix, never a value. Shape: `SELECT COUNT(*) FROM <table> WHERE <condition> [AND <condition>...] LIMIT <n>`, a condition being `CHAR_LENGTH(<colonne>) = <n>` or `LENGTH(...)`, `LEFT(<colonne>, n) = '<préfixe>'` or `SUBSTRING(<colonne>, 1, n) = '<préfixe>'` with `n` from 1 to 4, or `<autre colonne> = <constante>` (for one user: `user_login = 'admin'`). Anything else is refused: `<`, `>`, `LIKE`, `IN`, `BETWEEN`, `GROUP BY`, `ORDER BY`, other functions, nested functions, `SUBSTRING` not starting at 1. Inside the double quotes of the shell, write each `$` of a prefix as `\$`, or the shell expands it:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
python3 "$S/cqa_ssh_ro.py" <alias> --sql wp --app-dir <dossier> "SELECT COUNT(*) FROM wp_users WHERE CHAR_LENGTH(user_pass) = 34 AND LEFT(user_pass, 3) = '\$P\$' LIMIT 1"
```

On an old WordPress MD5 hash (32 hexadecimal characters, unsalted), a prefix filtered on one user reveals part of the hash itself, not an algorithm: detect MD5 with counts over all users only, `CHAR_LENGTH(user_pass) = 32` or `LEFT(user_pass, 1) = '$'`, never with a prefix per user.

Usual formats: WordPress (`wp_users.user_pass`) `$P$` (phpass, 34 characters), `$wp$` (bcrypt of WordPress 6.8 and later), 32 characters without `$` (old MD5); Drupal (`users_field_data.pass`) `$2y$` (bcrypt, 60 characters, Drupal 10.1 and later) or `$S$` (55 characters, earlier); Laravel (`users.password`) `$2y$` (bcrypt, 60 characters).

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
4. In autonomous mode, never ask: the orchestrator records the refusal, and the rows concerned stay `unknown` until the lead decides at the end of the audit:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" defer "$RUN" --kind ssh_refused --detail "Le garde-fou SSH a refusé : <commande> (<raison>). Impact : <lecture seule, ce qu'elle affiche, ou ce qu'elle modifierait>." --refs CQ-3,CQ-12
```

5. In autonomous mode, each refusal a sub-agent reports in its return (the command, its refs, its impact) becomes one `defer --kind ssh_refused --refs <its refs> --detail "<commande> Impact : <impact>"`, as above; in interactive mode, relay it to the lead.
6. In a sub-agent, the part becomes `unknown`, with « Commande à faire confirmer par le lead : <commande> (lecture seule, <ce qu'elle affiche>) » in `human_check`: the lead sees it in the Notion comment.

## Facts for the browser lots

The `server` lot writes `facts/server.json`, read by the browser lots for the mixed rows:

```json
{"facts": [{"refs": [88], "topic": "Affichage des erreurs PHP", "command": "--php-ini display_errors", "exit": 0, "result": "display_errors=Off (PHP CLI)"}]}
```

`command` is what follows the alias; `result` is a short French text, without any secret.
