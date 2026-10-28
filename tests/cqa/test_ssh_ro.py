import contextlib
import io
import json
import os
import shlex
import subprocess
import unittest
from unittest import mock

import cqa_ssh_ro
from cqa.ssh_ro import build_sql_remote, check_command, check_sql, run

ALIAS = "web-preprod"
FULLWIDTH_CAT = chr(0xFF43) + chr(0xFF41) + chr(0xFF54)
CYRILLIC_CAT = chr(0x0441) + "at"

ALLOWED = [
    # Brief table.
    ["cat", "/var/www/app/storage/logs/laravel.log"],
    ["ls", "-la", "/var/www"],
    ["tail", "-n", "200", "/var/log/nginx/error.log"],
    ["crontab", "-l"],
    ["journalctl", "-u", "php8.3-fpm", "-n", "200", "--no-pager"],
    ["git", "-C", "/var/www/app", "rev-parse", "HEAD"],
    ["docker", "exec", "app", "php", "-v"],
    # Files.
    ["cat", "/var/www/app/REVISION"],
    ["cat", "-n", "/etc/nginx/sites-enabled/example.org.conf"],
    ["head", "-n", "50", "/var/log/syslog"],
    ["head", "-n50", "/var/log/syslog"],
    ["tail", "--lines=100", "/var/log/nginx/access.log"],
    ["tail", "-n", "+10", "/var/log/nginx/access.log"],
    ["grep", "-n", "PHP Fatal", "/var/log/php8.3-fpm.log"],
    ["grep", "-ci", "-e", "error", "-e", "warning", "/var/log/syslog"],
    ["grep", "-m", "20", "server_tokens", "/etc/nginx/nginx.conf"],
    ["ls", "/var/www"],
    ["ls"],
    ["stat", "/var/www/app"],
    ["stat", "-c", "%U %a", "/var/www/app/storage"],
    ["find", "/var/www", "-name", "*.log", "-mtime", "-7"],
    ["find", "-L", "/var/www/app", "-maxdepth", "2", "-type", "f", "-perm", "-o+w"],
    ["find", "/var/www", "(", "-name", "*.sql", "-o", "-name", "*.zip", ")", "-print"],
    ["find", "/var/www", "!", "-user", "www-data", "-printf", "%u %p\\n"],
    # System.
    ["ps", "aux"],
    ["ps", "-eo", "pid,user,%cpu,args"],
    ["df", "-h"],
    ["du", "-sh", "/var/www"],
    ["du", "--max-depth=1", "/var/www"],
    ["free", "-m"],
    ["uptime"],
    ["systemctl", "status", "nginx"],
    ["systemctl", "status", "--no-pager", "php8.3-fpm.service"],
    ["systemctl", "is-active", "mariadb"],
    ["systemctl", "list-timers", "--all"],
    ["journalctl", "--no-pager", "-n500", "-p", "err", "--since=today"],
    ["journalctl", "--no-pager", "--lines=0", "-u", "nginx", "-o", "short-iso"],
    # PHP, servers, git.
    ["php", "-v"],
    ["php", "-m"],
    ["php", "--ini"],
    ["nginx", "-v"],
    ["apachectl", "-v"],
    ["git", "-C", "/var/www/app", "log", "-1", "--format=%H %ci"],
    # Applications.
    ["drush", "status"],
    ["drush", "--version"],
    ["drush", "pm:list", "--status=enabled", "--type=module", "--format=json"],
    ["/var/www/app/vendor/bin/drush", "--root=/var/www/app/web", "pm:security"],
    ["vendor/bin/drush", "status"],
    ["./vendor/bin/drush", "--version"],
    ["/srv/shop/vendor/bin/drush", "--version"],
    ["wp", "core", "version", "--path=/var/www/html"],
    ["wp", "plugin", "list", "--format=json", "--fields=name,status,version,update"],
    ["wp", "theme", "list"],
    ["wp", "cron", "event", "list"],
    ["wp", "option", "get", "blog_public"],
    ["wp", "--version"],
    ["php", "artisan", "about"],
    ["php", "artisan", "route:list", "--json"],
    ["php", "artisan", "schedule:list"],
    ["php", "/var/www/app/artisan", "--version"],
    ["php", "/srv/shop/artisan", "about"],
    # Containers.
    ["docker", "ps"],
    ["docker", "ps", "-a", "--format", "{{.Names}} {{.Image}}"],
    ["docker", "compose", "ps"],
    ["docker", "compose", "--project-directory", "/srv/app", "ps", "-a"],
    ["docker", "compose", "-f", "/srv/app/docker-compose.yml", "exec", "-T", "app",
     "php", "artisan", "about"],
    ["docker", "compose", "exec", "-T", "-w", "/var/www/html", "php", "wp", "core", "version"],
    ["docker", "exec", "-w", "/var/www/html", "app", "vendor/bin/drush", "status"],
    ["docker", "exec", "-T", "app", "php", "-v"],
    ["docker", "exec", "-w", "/var/www", "-T", "app", "./vendor/bin/drush", "status"],
    ["docker", "compose", "-p", "shop", "exec", "-T", "app", "php", "-v"],
    # Option forms: clusters, attached values, long options with a separate value.
    ["tail", "-c+10", "/var/log/syslog"],
    ["head", "--lines", "10", "/var/log/syslog"],
    ["grep", "--regexp=error", "/var/log/syslog"],
    ["journalctl", "-qn", "200", "--no-pager"],
    ["journalctl", "-u", "nginx", "--lines", "200", "--no-pager"],
    ["find", "-H", "/var/www", "-executable"],
]

REFUSED = [
    # Brief table.
    ["cat", "/var/www/app/.env"],
    ["grep", "-r", "APP_DEBUG", "/var/www/app/.env"],
    ["find", "/var/www", "-name", "*.log", "-delete"],
    ["systemctl", "restart", "nginx"],
    ["docker", "exec", "app", "env"],
    ["docker", "inspect", "app"],
    ["php", "-i"],
    ["cat", "/proc/1/environ"],
    ["sh", "-c", "ls"],
    ["ls", "/tmp;rm"],
    ["sudo", "ls"],
    ["git", "pull"],
    ["crontab", "-e"],
    ["cat", "/var/www/html/wp-config.php"],
    # Refused first words and unknown commands.
    ["su", "-"],
    ["bash", "-c", "id"],
    ["env"],
    ["printenv", "DB_PASSWORD"],
    ["set"],
    ["rm", "-rf", "/tmp/x"],
    ["/bin/cat", "/etc/hosts"],
    ["xargs", "cat"],
    [],
    # Metacharacters, expansions, control characters.
    ["ls", "a|b"],
    ["ls", "a&b"],
    ["ls", ">x"],
    ["ls", "<x"],
    ["ls", "`id`"],
    ["ls", "$(id)"],
    ["ls", "${HOME}"],
    ["ls", "$IFS"],
    ["ls", "/tmp\nrm -rf /var/www"],
    ["ls", "/tmp\rx"],
    ["ls", "/tmp\tx"],
    ["ls", "/tmp\x00x"],
    # Unicode lookalikes and invisible characters.
    [FULLWIDTH_CAT, "/etc/hosts"],
    [CYRILLIC_CAT, "/etc/hosts"],
    ["cat", "/var/www/app/." + chr(0x200B) + "env"],
    ["ls", "/var/www/" + chr(0x202E) + "gol.txt"],
    ["ls", "/var/www" + chr(0xA0)],
    # Secret-bearing files, whatever the command.
    ["cat", "/var/www/app/.env.production"],
    ["cat", "/srv/app/docker/app.env"],
    ["cat", "/var/www/app/public/../.env"],
    ["cat", "/var/www/html/wp-config.php.bak"],
    ["cat", "/var/www/web/sites/default/settings.local.php"],
    ["cat", "/etc/nginx/.htpasswd"],
    ["cat", "/home/deploy/.composer/auth.json"],
    ["cat", "/etc/ssl/private/example.org.key"],
    ["cat", "/etc/letsencrypt/live/example.org/privkey.pem"],
    ["cat", "/home/deploy/.ssh/id_ed25519"],
    ["cat", "/home/deploy/.my.cnf"],
    ["cat", "/etc/mysql/debian.cnf"],
    ["cat", "/etc/shadow"],
    ["cat", "/home/deploy/.bash_history"],
    ["cat", "/var/www/app/.git/config"],
    ["cat", "/home/deploy/.docker/config.json"],
    ["ls", "-la", "/var/www/app/.env"],
    ["stat", "/var/www/app/.ENV"],
    ["head", "/var/www/app/.env"],
    ["grep", "-f", "/var/www/app/.env", "/var/log/syslog"],
    ["find", "/var/www", "-newer", "/var/www/app/.env"],
    ["git", "-C", "/var/www/app/.env", "rev-parse", "HEAD"],
    ["docker", "exec", "app", "cat", "/var/www/html/.env"],
    ["docker", "compose", "-f", "/srv/app/.env", "ps"],
    ["wp", "core", "version", "--path=/var/www/html/wp-config.php"],
    # Process environment and system pseudo-files.
    ["cat", "/proc/self/environ"],
    ["cat", "/proc/1/task/1/environ"],
    ["cat", "/proc/1/cmdline"],
    ["cat", "/./proc/1/cmdline"],
    ["cat", "//proc/1/cmdline"],
    ["head", "-c", "100", "/dev/urandom"],
    ["tail", "-n", "5", "/sys/kernel/debug/x"],
    ["docker", "exec", "app", "cat", "/proc/1/environ"],
    # Paths: "..", globs, tilde.
    ["ls", ".."],
    ["cat", "/var/www/app/storage/../../../etc/hosts"],
    ["cat", "/var/www/app/.e*"],
    ["cat", "/var/www/app/storage/logs/*.log"],
    ["ls", "/var/www/app/[a-z]*"],
    ["cat", "~/notes.txt"],
    ["cat", "/var/www/{a,b}.txt"],
    # Files: options.
    ["cat"],
    ["cat", "-", "/etc/hosts"],
    ["cat", "--", "/etc/hosts"],
    ["tail", "-f", "/var/log/nginx/error.log"],
    ["tail", "-F", "/var/log/nginx/error.log"],
    ["tail", "-fn200", "/var/log/nginx/error.log"],
    ["tail", "-n200", "-f", "/var/log/nginx/error.log"],
    ["tail", "--follow", "/var/log/nginx/error.log"],
    ["tail", "--follow=name", "/var/log/nginx/error.log"],
    ["tail", "--foll", "/var/log/nginx/error.log"],
    ["tail", "-n", "abc", "/var/log/syslog"],
    ["head", "-n"],
    ["grep", "-rn", "DB_", "/var/www/app"],
    ["grep", "-R", "DB_", "/var/www/app"],
    ["grep", "--recursive", "DB_", "/var/www/app"],
    ["grep", "--dereference-recursive", "DB_", "/var/www/app"],
    ["grep", "-d", "recurse", "DB_", "/var/www/app"],
    ["grep", "--directories=recurse", "DB_", "/var/www/app"],
    ["grep", "pattern"],
    ["grep", "-e", "pattern"],
    ["ls", "--color=always", "/var/www"],
    ["du", "--files0-from=/var/www/app/list.txt"],
    ["du", "--exclude-from=/var/www/app/list.txt", "/var/www"],
    ["stat", "--printf=%n", "/var/www"],
    ["df", "--output=source", "/"],
    # find.
    ["find", "/var/www", "-exec", "cat", "{}", ";"],
    ["find", "/var/www", "-exec", "cat", "{}", "+"],
    ["find", "/var/www", "-execdir", "rm", "{}", "+"],
    ["find", "/var/www", "-ok", "rm", "{}", "+"],
    ["find", "/var/www", "-okdir", "rm", "{}", "+"],
    ["find", "/var/www", "-fprint", "/tmp/x"],
    ["find", "/var/www", "-fprint0", "/tmp/x"],
    ["find", "/var/www", "-fprintf", "/tmp/x", "%p"],
    ["find", "/var/www", "-fls", "/tmp/x"],
    ["find", "-D", "exec", "/var/www"],
    ["find", "/var/www", "-name"],
    ["find", "/var/www", "-files0-from", "/tmp/list"],
    ["find", "/var/www", "-name", "*.log", "stray"],
    ["find", "/var/www", "-newer", "/var/www/*.log"],
    ["find", "-", "-name", "x"],
    # System.
    ["ps", "auxe"],
    ["ps", "eww"],
    ["ps", "auxv"],
    ["ps", "-o", "pid", "e"],
    ["ps", "--forest", "-o", "pid;id"],
    ["free", "/tmp"],
    ["uptime", "-x"],
    ["systemctl", "stop", "nginx"],
    ["systemctl", "daemon-reload"],
    ["systemctl", "--host=web2", "status"],
    ["systemctl", "status", "nginx;reboot"],
    ["systemctl"],
    ["journalctl", "-n", "200"],
    ["journalctl", "--no-pager"],
    ["journalctl", "-n", "1000", "--no-pager"],
    ["journalctl", "-n", "+5", "--no-pager"],
    ["journalctl", "-n", "200", "-n", "9999", "--no-pager"],
    ["journalctl", "-nf", "--no-pager"],
    ["journalctl", "-n", "all", "--no-pager"],
    ["journalctl", "-f", "-n", "10", "--no-pager"],
    ["journalctl", "--follow", "-n", "10", "--no-pager"],
    ["journalctl", "-e", "--no-pager"],
    ["journalctl", "-n", "10", "--no-pager", "-o", "json"],
    ["journalctl", "-n", "10", "--no-pager", "--output=verbose"],
    ["journalctl", "-n", "10", "--no-pager", "--vacuum-size=1M"],
    ["journalctl", "-n", "10", "--no-pager", "-D", "/var/log/journal"],
    ["journalctl", "-n", "10", "--no-pager", "_PID=1"],
    ["crontab", "-r"],
    ["crontab", "-l", "-u", "root"],
    ["crontab", "/tmp/cron"],
    ["crontab"],
    # PHP, servers, git.
    ["php", "-r", "echo 1"],
    ["php", "-d", "display_errors=1", "-v"],
    ["php", "-v", "-m"],
    ["php", "/var/www/app/script.php"],
    ["php"],
    ["nginx", "-s", "reload"],
    ["nginx", "-T"],
    ["nginx", "-V"],
    ["apachectl", "graceful"],
    ["git", "-C", "/var/www/app", "log", "-p"],
    ["git", "-C", "/var/www/app", "log", "-1", "--format=%H", "--output=/tmp/x"],
    ["git", "-C", "/var/www/app", "-c", "core.pager=less", "log", "-1", "--format=%H"],
    ["git", "-C", "/var/www/app", "rev-parse", "--all"],
    ["git", "--git-dir=/var/www/app/.git", "rev-parse", "HEAD"],
    ["git", "rev-parse", "HEAD"],
    ["git", "-C", "/var/www/app", "log", "-1", "--pretty=%H"],
    ["git", "-C", "/var/www/app", "status"],
    # Applications.
    ["drush", "sql:query", "SELECT 1"],
    ["drush", "sql:dump"],
    ["drush", "cr"],
    ["drush", "php:eval", "echo 1"],
    ["drush", "status", "--fields=db-password"],
    ["drush", "status", "--field=db-password"],
    ["drush", "status", "db-password"],
    ["drush", "status", "--show-passwords"],
    ["drush", "status", "--root", "/var/www/app"],
    ["drush", "status", "pm:list"],
    ["drush", "--version", "status"],
    ["/var/www/app/vendor/bin/drush.php", "status"],
    ["wp", "option", "get", "auth_key"],
    ["wp", "option", "get", "mailserver_pass"],
    ["wp", "option", "get", "stripe_secret_key"],
    ["wp", "option", "get", "my_api_token"],
    ["wp", "option", "get"],
    ["wp", "option", "update", "blog_public", "0"],
    ["wp", "config", "get", "DB_PASSWORD"],
    ["wp", "eval", "echo 1"],
    ["wp", "core", "version", "--exec=echo 1"],
    ["wp", "core", "version", "--require=/tmp/x.php"],
    ["wp", "--ssh=web2", "core", "version"],
    ["wp", "plugin", "list", "--format=json", "--fields=name;id"],
    ["wp", "plugin", "install", "hello"],
    ["php", "artisan", "tinker"],
    ["php", "artisan", "migrate"],
    ["php", "artisan", "env"],
    ["php", "artisan", "config:show", "database"],
    ["php", "artisan", "about", "--env=production"],
    ["php", "artisan"],
    ["php", "/var/www/app/artisan.php", "about"],
    # Application binaries outside the allowed forms (a file planted on the server).
    ["/tmp/x/drush", "status"],
    ["./drush", "status"],
    ["drush/drush", "status"],
    ["bin/drush", "status"],
    ["/usr/local/bin/drush", "status"],
    ["/tmp/vendor/bin/drush", "status"],
    ["/var/tmp/app/vendor/bin/drush", "status"],
    ["/dev/shm/vendor/bin/drush", "status"],
    ["/home/web/app/vendor/bin/drush", "status"],
    ["/root/app/vendor/bin/drush", "status"],
    ["/run/user/1000/vendor/bin/drush", "status"],
    ["//tmp/x/vendor/bin/drush", "status"],
    ["/./tmp/x/vendor/bin/drush", "status"],
    ["/var/www/app/./vendor/bin/drush", "status"],
    ["x/vendor/bin/drush", "status"],
    ["/tmp/x/wp", "core", "version"],
    ["./wp", "core", "version"],
    ["/usr/local/bin/wp", "--version"],
    ["php", "/tmp/x/artisan", "about"],
    ["php", "./artisan", "about"],
    ["php", "app/artisan", "about"],
    ["php", "/var/tmp/artisan", "--version"],
    ["php", "/dev/shm/artisan", "about"],
    ["php", "/home/web/app/artisan", "about"],
    ["php", "/root/app/artisan", "about"],
    ["php", "/run/user/1000/artisan", "about"],
    ["php", "//tmp/artisan", "about"],
    ["php", "/var/www/./app/artisan", "about"],
    ["docker", "exec", "app", "./drush", "status"],
    ["docker", "compose", "exec", "-T", "app", "php", "/tmp/x/artisan", "about"],
    # Containers.
    ["docker", "container", "inspect", "app"],
    ["docker", "compose", "config"],
    ["docker", "compose", "logs"],
    ["docker", "logs", "app"],
    ["docker", "run", "--rm", "alpine", "ls"],
    ["docker", "--context", "prod", "ps"],
    ["docker", "ps", "--format", "{{json .}}", "extra"],
    ["docker", "exec", "app"],
    ["docker", "exec", "-it", "app", "php", "-v"],
    ["docker", "exec", "-i", "app", "php", "-v"],
    ["docker", "exec", "-e", "X=1", "app", "php", "-v"],
    ["docker", "exec", "-u", "root", "app", "php", "-v"],
    ["docker", "exec", "--privileged", "app", "php", "-v"],
    ["docker", "exec", "-w", "/var/www/..", "app", "php", "-v"],
    ["docker", "exec", "-w"],
    ["docker", "exec", "app", "printenv"],
    ["docker", "exec", "app", "sh", "-c", "env"],
    ["docker", "exec", "app", "php", "-i"],
    ["docker", "exec", "app", "docker", "inspect", "db"],
    ["docker", "compose", "exec", "-T", "app", "env"],
    ["docker", "compose", "exec", "-T", "app"],
    ["docker", "compose", "exec", "-T", "-e", "X=1", "app", "php", "-v"],
    ["docker", "compose", "--env-file", "/srv/app/prod.cfg", "ps"],
    ["docker", "compose", "exec", "-T", "app", "cat", "/proc/self/environ"],
    ["docker", "compose", "exec", "-T", "app", "-w", "/var/www", "ls"],
    ["docker", "compose", "-p", "-x", "ps"],
]

REASONS = [
    (["cat", "/var/www/app/.env"], "porteur de secrets"),
    (["cat", "/proc/1/environ"], "environnement"),
    (["sudo", "ls"], "« sudo »"),
    (["env"], "variables d'environnement"),
    (["ls", "/tmp;rm"], "« ; »"),
    (["ls", "$IFS"], "« $ »"),
    (["ls", "/tmp\nrm"], "contrôle"),
    ([CYRILLIC_CAT, "/etc/hosts"], "non ASCII"),
    (["cat", "/var/www/app/public/../x"], "« .. »"),
    (["cat", "/var/www/app/*.log"], "jokers"),
    (["tail", "-f", "/var/log/syslog"], "suivi continu"),
    (["tail", "--follow=name", "/var/log/syslog"], "suivi continu"),
    (["journalctl", "-f", "-n", "10", "--no-pager"], "suivi continu"),
    (["journalctl", "-n", "1000", "--no-pager"], "500"),
    (["journalctl", "-n", "100"], "--no-pager"),
    (["grep", "-rn", "x", "/var/www"], "récursi"),
    (["find", "/var/www", "-delete"], "-delete"),
    (["find", "/var/www", "-fprintf", "/tmp/x", "%p"], "-fprintf"),
    (["find", "/var/www", "-exec", "cat", "{}", "+"], "find : -exec"),
    (["find", "/var/www", "-okdir", "rm", "{}", "+"], "find : -okdir"),
    (["php", "-r", "echo 1"], "php : seuls"),
    (["drush", "php:eval", "echo 1"], "drush : seuls"),
    (["wp", "core", "version", "--exec=echo 1"], "« --exec »"),
    (["drush", "status", "--fields=db-password"], "« --fields »"),
    (["docker", "exec", "-it", "app", "php", "-v"], "« -it »"),
    (["ps", "auxe"], "environnement"),
    (["docker", "inspect", "app"], "docker inspect"),
    (["docker", "exec", "app", "env"], "conteneur"),
    (["php", "-i"], "--php-ini"),
    (["wp", "option", "get", "auth_key"], "secret"),
    (["/tmp/x/drush", "status"], "drush : chemin refusé"),
    (["./wp", "core", "version"], "wp : chemin refusé"),
    (["php", "/tmp/x/artisan", "about"], "artisan : chemin refusé"),
    (["systemctl", "restart", "nginx"], "systemctl"),
    (["rm", "-rf", "/tmp/x"], "liste blanche"),
    (["cat", "/dev/urandom"], "/dev"),
]

SQL_OK = [
    "SELECT nid FROM node_field_data LIMIT 5",
    "select 1;",
    "SHOW TABLES",
    "EXPLAIN SELECT 1",
    "  SELECT COUNT(*) FROM node_field_data WHERE status = 1 ;  ",
    "DESCRIBE users",
    "DESC users",
    "desc users",
    "SHOW CREATE TABLE users",
    "SHOW VARIABLES LIKE 'max_connections'",
    "SELECT name, REPLACE(name, 'a', 'b') FROM taxonomy_term_field_data",
    "SELECT option_name FROM wp_options WHERE autoload = 'yes' LIMIT 20",
    # Sensitive tables: counts only; their schema stays readable.
    "SELECT COUNT(*) FROM users",
    "SELECT COUNT(*) AS total FROM wp_users",
    "select count(*) total, count(uid) from users_field_data where status = 1",
    "SELECT COUNT(*) FROM sessions LIMIT 1",
    "SELECT COUNT(*) FROM `users`",
    "SELECT COUNT(*) FROM node_field_data WHERE type = 'users'",
    "SHOW COLUMNS FROM users",
    "DESCRIBE wp_users",
    "SELECT users_count FROM stats",
]

SQL_REFUSED = [
    # Brief table.
    "DELETE FROM users",
    "SELECT 1; DROP TABLE x",
    "SELECT * INTO OUTFILE '/tmp/x' FROM users",
    "\\! ls",
    "SELECT SLEEP(10)",
    # One statement starting with a read keyword.
    "",
    "   ",
    ";",
    "SELECT 1;;",
    "SELECT ';'",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "(SELECT 1)",
    "SELECTX 1",
    "UPDATE users SET name = 'x'",
    "INSERT INTO users VALUES (1)",
    # Refused words and functions.
    "SELECT id INTO @x FROM users",
    "SELECT 1 into dumpfile '/tmp/x'",
    "SELECT LOAD_FILE('/etc/passwd')",
    "select load_file('/etc/passwd')",
    "SELECT sleep (10)",
    "SELECT BENCHMARK(1000000, MD5('x'))",
    "SELECT GET_LOCK('x', 10)",
    "SELECT RELEASE_LOCK('x')",
    "SELECT name FROM users WHERE name = 'a\\'b'",
    "EXPLAIN ANALYZE DELETE FROM users",
    "EXPLAIN DELETE FROM users",
    "DESCRIBE UPDATE users SET name = 'x'",
    "SELECT * FROM users FOR UPDATE",
    "SELECT * FROM users LOCK IN SHARE MODE",
    "SELECT * FROM users FOR SHARE",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT nextval('users_id_seq')",
    "SELECT query_to_xml('SEL' || 'ECT 1', true, true, '')",
    # Comments, newlines, non-ASCII.
    "SELECT 1 -- comment",
    "SELECT 1 # comment",
    "SELECT 1 /*! INTO OUTFILE '/tmp/x' */",
    "SELECT 1 /* x */",
    "SELECT 1\nsystem ls",
    "SELECT 1\rFROM x",
    "SELECT 1\tFROM x",
    "SELECT " + chr(0x2019) + "x" + chr(0x2019),
    # Credentials.
    "SELECT user_pass FROM wp_users",
    "SELECT pass FROM users_field_data",
    "SELECT authentication_string FROM mysql.user",
    "SELECT option_value FROM wp_options WHERE option_name = 'stripe_secret_key'",
    "SELECT remember_token FROM users",
    # Star in a select list.
    "SELECT * FROM node",
    "select * from node limit 1",
    "SELECT n.* FROM node n",
    "SELECT nid, * FROM node",
    "SELECT COUNT(*), * FROM node",
    "EXPLAIN SELECT * FROM node",
    "SELECT (SELECT * FROM node LIMIT 1)",
    "SELECT nid FROM node WHERE nid IN (SELECT * FROM node)",
    "SELECT nid*2 FROM node",
    # Sensitive tables without COUNT(...) only.
    "SELECT id FROM users LIMIT 5",
    "SELECT name, REPLACE(name, 'a', 'b') FROM users",
    "SELECT mail FROM users_field_data",
    "SELECT user_login FROM wp_users",
    "SELECT id FROM sessions",
    "SELECT payload FROM sessions",
    "SELECT email FROM password_reset_tokens",
    "SELECT name FROM personal_access_tokens",
    "SELECT COUNT(id) FROM personal_access_tokens",
    "SELECT uid FROM `users`",
    'SELECT uid FROM "users"',
    "SELECT name FROM appdb.users",
    "SELECT name FROM `appdb`.`users`",
    "SELECT n.title FROM node n JOIN users u ON u.uid = n.uid",
    "SELECT title FROM node, users",
    "SELECT title FROM (node, users)",
    "SELECT title FROM node WHERE uid IN (SELECT uid FROM users)",
    "SELECT COUNT(*), mail FROM users_field_data",
    "SELECT COUNT(*) FROM users UNION SELECT mail FROM users_field_data",
    "SELECT COUNT(*) + 1 FROM users",
    "SELECT id FROM USERS",
    "SELECT ID FROM Sessions",
    "EXPLAIN SELECT id FROM users",
    # Quotes left open.
    "SELECT 'abc FROM node",
    "SELECT `abc FROM node",
    "SHOW CREATE USER root",
]


class FakeRunner:
    """Stands in for subprocess.run: records each call and returns a scripted result."""

    def __init__(self, stdout="", returncode=0, error=None):
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, stdout=self.stdout)

    @property
    def remote(self):
        return self.calls[-1][0][-1]

    @property
    def options(self):
        return self.calls[-1][1]


def clean_env(extra=None):
    """Patch os.environ without the secret variables of the developer's shell."""
    kept = {
        key: value
        for key, value in os.environ.items()
        if key not in ("NOTION_TOKEN", "GM_AUDIT_AUTH_PASS")
    }
    kept.update(extra or {})
    return mock.patch.dict(os.environ, kept, clear=True)


class CheckCommandTest(unittest.TestCase):
    def test_allowed(self):
        for argv in ALLOWED:
            with self.subTest(argv=argv):
                allowed, reason = check_command(argv)
                self.assertTrue(allowed, reason)
                self.assertTrue(reason)

    def test_refused(self):
        for argv in REFUSED:
            with self.subTest(argv=argv):
                allowed, reason = check_command(argv)
                self.assertFalse(allowed)
                self.assertTrue(reason)

    def test_reasons_are_specific_and_french(self):
        for argv, expected in REASONS:
            with self.subTest(argv=argv):
                allowed, reason = check_command(argv)
                self.assertFalse(allowed)
                self.assertIn(expected, reason)
                self.assertNotIn(chr(0x2014), reason)

    def test_non_text_argument_refused(self):
        self.assertFalse(check_command(["ls", 3])[0])
        self.assertFalse(check_command(["ls", None])[0])
        self.assertFalse(check_command("ls -la")[0])

    def test_input_is_not_modified(self):
        argv = ["docker", "exec", "app", "php", "-v"]
        check_command(argv)
        self.assertEqual(argv, ["docker", "exec", "app", "php", "-v"])


class CheckSqlTest(unittest.TestCase):
    def test_allowed(self):
        for query in SQL_OK:
            with self.subTest(query=query):
                allowed, reason = check_sql(query)
                self.assertTrue(allowed, reason)

    def test_refused(self):
        for query in SQL_REFUSED:
            with self.subTest(query=query):
                allowed, reason = check_sql(query)
                self.assertFalse(allowed)
                self.assertTrue(reason)
                self.assertNotIn(chr(0x2014), reason)

    def test_reasons(self):
        self.assertIn("une seule instruction", check_sql("SELECT 1; DROP TABLE x")[1])
        self.assertIn("SELECT", check_sql("DELETE FROM users")[1])
        self.assertIn("INTO", check_sql("SELECT 1 INTO @x")[1])
        self.assertIn("SLEEP", check_sql("SELECT SLEEP(1)")[1])
        self.assertIn("« * »", check_sql("SELECT * FROM node")[1])
        self.assertIn("COUNT(*)", check_sql("SELECT * FROM node")[1])
        reason = check_sql("SELECT mail FROM users_field_data")[1]
        self.assertIn("« users_field_data »", reason)
        self.assertIn("COUNT", reason)
        self.assertIn("« sessions »", check_sql("SELECT id FROM sessions")[1])
        self.assertIn("non fermé", check_sql("SELECT 'abc FROM node")[1])

    def test_non_text_refused(self):
        self.assertFalse(check_sql(None)[0])


class BuildSqlRemoteTest(unittest.TestCase):
    STDIN = "SET SESSION TRANSACTION READ ONLY;\nSELECT 1;\n"

    def test_artisan_sql_uses_stdin(self):
        remote, stdin = build_sql_remote("artisan", "/srv/app", "SELECT 1")
        self.assertEqual(remote, "cd /srv/app && php artisan db")
        self.assertTrue(stdin.startswith("SET SESSION TRANSACTION READ ONLY;"))
        self.assertEqual(stdin, self.STDIN)

    def test_drush(self):
        remote, stdin = build_sql_remote("drush", "/var/www/app", "SELECT 1;")
        self.assertEqual(remote, "cd /var/www/app && vendor/bin/drush sql:query --file=-")
        self.assertEqual(stdin, self.STDIN)

    def test_wp(self):
        remote, stdin = build_sql_remote("wp", "/var/www/html", " SELECT 1 ; ")
        self.assertEqual(remote, "wp db query --path=/var/www/html")
        self.assertEqual(stdin, self.STDIN)

    def test_query_never_in_remote(self):
        for engine in ("drush", "wp", "artisan"):
            with self.subTest(engine=engine):
                remote, stdin = build_sql_remote(engine, "/srv/app", "SHOW TABLES")
                self.assertNotIn("SHOW", remote)
                self.assertIn("SHOW TABLES;\n", stdin)

    def test_container(self):
        remote, stdin = build_sql_remote("artisan", "/var/www/html", "SELECT 1", container="app")
        self.assertEqual(remote, "docker exec -i app sh -c 'cd /var/www/html && php artisan db'")
        self.assertEqual(stdin, self.STDIN)

    def test_compose_service(self):
        remote, _ = build_sql_remote("wp", "/var/www/html", "SELECT 1", compose_service="php")
        self.assertEqual(
            remote, "docker compose exec -T php sh -c 'wp db query --path=/var/www/html'"
        )

    def test_sql_skip_ssl_by_engine(self):
        remote, stdin = build_sql_remote("wp", "/var/www/html", "SELECT 1", skip_ssl=True)
        self.assertEqual(remote, "wp db query --skip-ssl --path=/var/www/html")
        self.assertEqual(stdin, self.STDIN)
        remote, _ = build_sql_remote("drush", "/var/www/app", "SELECT 1", skip_ssl=True)
        self.assertEqual(
            remote,
            "cd /var/www/app && vendor/bin/drush sql:query --extra=--skip-ssl --file=-",
        )
        with self.assertRaises(ValueError) as caught:
            build_sql_remote("artisan", "/srv/app", "SELECT 1", skip_ssl=True)
        self.assertIn("--skip-ssl n'est pas pris en charge avec artisan", str(caught.exception))
        remote, _ = build_sql_remote(
            "wp", "/var/www/html", "SELECT 1", container="app", skip_ssl=True
        )
        self.assertEqual(
            remote, "docker exec -i app sh -c 'wp db query --skip-ssl --path=/var/www/html'"
        )
        remote, _ = build_sql_remote(
            "drush", "/var/www/html", "SELECT 1", compose_service="php", skip_ssl=True
        )
        self.assertEqual(
            shlex.split(remote)[-1],
            "cd /var/www/html && vendor/bin/drush sql:query --extra=--skip-ssl --file=-",
        )
        remote, _ = build_sql_remote("wp", "/var/www/html", "SELECT 1", skip_ssl=False)
        self.assertEqual(remote, "wp db query --path=/var/www/html")

    def test_compose_file(self):
        remote, _ = build_sql_remote(
            "wp", "/var/www/html", "SELECT 1", compose_service="php",
            compose_file="/srv/my app/docker-compose.yml",
        )
        self.assertEqual(
            remote,
            "docker compose -f '/srv/my app/docker-compose.yml' exec -T php "
            "sh -c 'wp db query --path=/var/www/html'",
        )
        for options in (
            {"compose_file": "/srv/docker-compose.yml"},
            {"compose_service": "php", "compose_file": "docker-compose.yml"},
            {"compose_service": "php", "compose_file": "/srv/../x.yml"},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    build_sql_remote("wp", "/var/www/html", "SELECT 1", **options)

    def test_paths_are_quoted(self):
        remote, _ = build_sql_remote("drush", "/srv/my app", "SELECT 1")
        self.assertEqual(remote, "cd '/srv/my app' && vendor/bin/drush sql:query --file=-")
        remote, _ = build_sql_remote("artisan", "/srv/my app", "SELECT 1", container="app")
        self.assertEqual(shlex.split(remote)[-1], "cd '/srv/my app' && php artisan db")

    def test_refusals(self):
        cases = [
            (("artisan", "/srv/app", "DELETE FROM users"), {}),
            (("mysql", "/srv/app", "SELECT 1"), {}),
            (("artisan", "srv/app", "SELECT 1"), {}),
            (("artisan", "/srv/app/../x", "SELECT 1"), {}),
            (("artisan", "/srv/app;id", "SELECT 1"), {}),
            (("artisan", "/srv/app/.env", "SELECT 1"), {}),
            (("artisan", "/srv/app", "SELECT 1"), {"container": "app", "compose_service": "php"}),
            (("artisan", "/srv/app", "SELECT 1"), {"container": "-it"}),
            (("artisan", "/srv/app", "SELECT 1"), {"container": "app;id"}),
            (("artisan", "/srv/app", "SELECT 1"), {"compose_service": ""}),
        ]
        for args, options in cases:
            with self.subTest(args=args, options=options):
                with self.assertRaises(ValueError):
                    build_sql_remote(*args, **options)


class RunTest(unittest.TestCase):
    def test_ssh_command_and_options(self):
        runner = FakeRunner("ok\n")
        with clean_env():
            code, output = run(ALIAS, "ls -la /var/www", runner=runner)
        self.assertEqual((code, output), (0, "ok\n"))
        command, options = runner.calls[0]
        self.assertEqual(
            command,
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ALIAS, "ls -la /var/www"],
        )
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertNotIn("input", options)
        self.assertIs(options["stderr"], subprocess.STDOUT)
        self.assertGreater(options["timeout"], 0)

    def test_stdin_is_sent(self):
        runner = FakeRunner()
        with clean_env():
            run(ALIAS, "cd /srv/app && php artisan db", stdin="SELECT 1;\n", runner=runner)
        self.assertEqual(runner.options["input"], "SELECT 1;\n")
        self.assertNotIn("stdin", runner.options)

    def test_output_redacted(self):
        runner = FakeRunner("DB_PASSWORD=supersecret\n")
        with clean_env():
            code, output = run(ALIAS, "crontab -l", runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(output, "DB_PASSWORD=[secret masqué]\n")

    def test_known_values_redacted(self):
        runner = FakeRunner("user audit, mot de passe s3cret-pass-0123\n")
        with clean_env({"GM_AUDIT_AUTH_PASS": "s3cret-pass-0123"}):
            _, output = run(ALIAS, "crontab -l", runner=runner)
        self.assertNotIn("s3cret-pass-0123", output)
        self.assertIn("[secret masqué]", output)

    def test_remote_failure_code(self):
        runner = FakeRunner("ls: cannot access '/x': No such file or directory\n", returncode=2)
        with clean_env():
            code, output = run(ALIAS, "ls /x", runner=runner)
        self.assertEqual(code, 2)
        self.assertIn("No such file", output)

    def test_timeout(self):
        runner = FakeRunner(error=subprocess.TimeoutExpired(["ssh"], 120))
        with clean_env():
            code, output = run(ALIAS, "ls", runner=runner)
        self.assertNotEqual(code, 0)
        self.assertIn("Délai", output)

    def test_ssh_missing(self):
        runner = FakeRunner(error=FileNotFoundError("ssh"))
        with clean_env():
            code, output = run(ALIAS, "ls", runner=runner)
        self.assertNotEqual(code, 0)
        self.assertIn("ssh", output)

    def test_alias_injection_refused(self):
        for alias in ("-oProxyCommand=id", "", "web preprod", "web;id", "-v"):
            with self.subTest(alias=alias):
                runner = FakeRunner()
                with self.assertRaises(ValueError):
                    run(alias, "ls", runner=runner)
                self.assertEqual(runner.calls, [])


class CliTest(unittest.TestCase):
    def run_main(self, argv, runner=None, env=None):
        """Run cqa_ssh_ro.main; return (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        runner = runner or FakeRunner()
        with clean_env(env):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_ssh_ro.main(argv, runner=runner)
        return code, out.getvalue(), err.getvalue()

    def test_check_allowed(self):
        runner = FakeRunner()
        code, out, _ = self.run_main(["--check", "--", "cat", "/etc/hosts"], runner)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["allowed"], True)
        self.assertEqual(runner.calls, [])

    def test_check_refused(self):
        runner = FakeRunner()
        code, out, _ = self.run_main(["--check", "--", "cat", "/var/www/app/.env"], runner)
        self.assertEqual(code, 4)
        result = json.loads(out)
        self.assertEqual(set(result), {"allowed", "reason"})
        self.assertFalse(result["allowed"])
        self.assertIn("secrets", result["reason"])
        self.assertEqual(runner.calls, [])

    def test_command_runs_quoted(self):
        runner = FakeRunner("/var/www/app/storage/logs/laravel.log\n")
        argv = [ALIAS, "--", "find", "/var/www", "-name", "*.log", "-printf", "%p %s\\n"]
        code, out, _ = self.run_main(argv, runner)
        self.assertEqual(code, 0)
        self.assertEqual(out, "/var/www/app/storage/logs/laravel.log\n")
        self.assertEqual(runner.remote, "find /var/www -name '*.log' -printf '%p %s\\n'")
        self.assertEqual(shlex.split(runner.remote), argv[2:])
        self.assertEqual(runner.calls[0][0][:5], ["ssh", "-o", "BatchMode=yes", "-o",
                                                  "ConnectTimeout=10"])
        self.assertEqual(runner.calls[0][0][5], ALIAS)

    def test_command_output_redacted(self):
        runner = FakeRunner("DB_PASSWORD=supersecret\n")
        code, out, _ = self.run_main([ALIAS, "--", "crontab", "-l"], runner)
        self.assertEqual(code, 0)
        self.assertEqual(out, "DB_PASSWORD=[secret masqué]\n")

    def test_command_refused(self):
        runner = FakeRunner()
        code, out, err = self.run_main([ALIAS, "--", "systemctl", "restart", "nginx"], runner)
        self.assertEqual(code, 4)
        self.assertEqual(out, "")
        self.assertIn("lead", err)
        self.assertIn("systemctl", err)
        self.assertEqual(runner.calls, [])

    def test_remote_failure(self):
        runner = FakeRunner("grep: /x: No such file or directory\n", returncode=2)
        code, out, err = self.run_main([ALIAS, "--", "grep", "x", "/x"], runner)
        self.assertEqual(code, 1)
        self.assertIn("No such file", out)
        self.assertIn("2", err)

    def test_ssh_failure_is_remote_failure(self):
        runner = FakeRunner("ssh: Could not resolve hostname web-preprod\n", returncode=255)
        code, _, _ = self.run_main([ALIAS, "--", "uptime"], runner)
        self.assertEqual(code, 1)

    def test_sql(self):
        runner = FakeRunner("id\n1\n")
        code, out, _ = self.run_main(
            [ALIAS, "--sql", "artisan", "--app-dir", "/srv/app", "SELECT nid FROM node LIMIT 1"],
            runner,
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "id\n1\n")
        self.assertEqual(runner.remote, "cd /srv/app && php artisan db")
        self.assertEqual(
            runner.options["input"],
            "SET SESSION TRANSACTION READ ONLY;\nSELECT nid FROM node LIMIT 1;\n",
        )

    def test_sql_options_any_order(self):
        runner = FakeRunner()
        code, _, _ = self.run_main(
            [ALIAS, "--sql", "drush", "SHOW TABLES", "--container", "app",
             "--app-dir", "/var/www/html"],
            runner,
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            runner.remote,
            "docker exec -i app sh -c 'cd /var/www/html && vendor/bin/drush sql:query --file=-'",
        )

    def test_sql_refused(self):
        runner = FakeRunner()
        code, _, err = self.run_main(
            [ALIAS, "--sql", "wp", "--app-dir", "/var/www/html", "DELETE FROM wp_users"], runner
        )
        self.assertEqual(code, 4)
        self.assertIn("lead", err)
        self.assertEqual(runner.calls, [])

    def test_cli_compose_file(self):
        runner = FakeRunner()
        code, _, _ = self.run_main(
            [ALIAS, "--sql", "wp", "--app-dir", "/app", "--compose-service", "php",
             "--compose-file", "/srv/dc.yml", "SELECT 1"], runner)
        self.assertEqual(code, 0)
        self.assertIn("docker compose -f /srv/dc.yml exec -T php", runner.calls[0][0][-1])
        runner = FakeRunner()
        code, _, err = self.run_main(
            [ALIAS, "--sql", "wp", "--app-dir", "/app", "--compose-file", "/srv/dc.yml",
             "SELECT 1"], runner)
        self.assertEqual(code, 2)
        self.assertTrue(err)
        self.assertEqual(runner.calls, [])

    def test_cli_sql_skip_ssl_flag(self):
        runner = FakeRunner()
        code, _, _ = self.run_main(
            [ALIAS, "--sql", "wp", "--skip-ssl", "--app-dir", "/app", "SELECT 1"], runner
        )
        self.assertEqual(code, 0)
        self.assertIn("wp db query --skip-ssl --path=/app", runner.calls[0][0][-1])
        for argv in (
            [ALIAS, "--sql", "wp", "--skip-ssl", "--skip-ssl", "--app-dir", "/app", "SELECT 1"],
            [ALIAS, "--sql", "wp", "--skip-ssl", "yes", "--app-dir", "/app", "SELECT 1"],
            [ALIAS, "--sql", "artisan", "--skip-ssl", "--app-dir", "/app", "SELECT 1"],
        ):
            with self.subTest(argv=argv):
                runner = FakeRunner()
                code, _, err = self.run_main(argv, runner)
                self.assertEqual(code, 2)
                self.assertTrue(err)
                self.assertEqual(runner.calls, [])

    def test_sql_usage_errors(self):
        cases = [
            [ALIAS, "--sql", "artisan", "SELECT 1"],
            [ALIAS, "--sql", "mysql", "--app-dir", "/srv/app", "SELECT 1"],
            [ALIAS, "--sql", "artisan", "--app-dir", "/srv/app"],
            [ALIAS, "--sql", "artisan", "--app-dir", "/srv/app", "SELECT 1", "SELECT 2"],
            [ALIAS, "--sql", "artisan", "--app-dir", "/srv/app", "--container", "app",
             "--compose-service", "php", "SELECT 1"],
            [ALIAS, "--sql", "artisan", "--app-dir", "/srv/app", "--bogus", "SELECT 1"],
            [ALIAS, "--sql", "artisan", "--app-dir"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                runner = FakeRunner()
                code, _, err = self.run_main(argv, runner)
                self.assertEqual(code, 2)
                self.assertTrue(err)
                self.assertEqual(runner.calls, [])

    def test_sql_bad_app_dir_refused(self):
        runner = FakeRunner()
        code, _, _ = self.run_main(
            [ALIAS, "--sql", "artisan", "--app-dir", "/srv/app/../..", "SELECT 1"], runner
        )
        self.assertEqual(code, 4)
        self.assertEqual(runner.calls, [])

    ENV_FILE = (
        "# Production\n"
        "APP_ENV=production\n"
        "APP_DEBUG=false\n"
        "APP_KEY=base64:c2VjcmV0LWtleS0wMTIzNDU2Nzg5\n"
        "APP_URL='https://www.example.org'\n"
        'DB_PASSWORD="hunter2-secret"\n'
        "export LOG_LEVEL=warning # quiet\n"
        "MAIL_PASSWORD=\n"
        "APP_DEBUG=true\n"
    )

    def test_env_keys(self):
        runner = FakeRunner(self.ENV_FILE)
        argv = [ALIAS, "--env-keys", "/var/www/app/.env", "APP_ENV", "APP_DEBUG", "APP_KEY",
                "APP_URL", "DB_PASSWORD", "LOG_LEVEL", "MAIL_PASSWORD", "MAIL_HOST"]
        code, out, _ = self.run_main(argv, runner)
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            [
                "APP_ENV=production",
                "APP_DEBUG=true",
                "APP_KEY=<présente, 35 caractères>",
                "APP_URL=https://www.example.org",
                "DB_PASSWORD=<présente, 14 caractères>",
                "LOG_LEVEL=warning",
                "MAIL_PASSWORD=<présente, 0 caractère>",
                "MAIL_HOST=<absente>",
            ],
        )
        self.assertNotIn("hunter2", out)
        self.assertNotIn("c2VjcmV0", out)
        self.assertEqual(runner.remote, "cat /var/www/app/.env")

    def test_env_keys_wp_config(self):
        content = (
            "<?php\n"
            "define( 'DB_PASSWORD', 'hunter2-secret' );\n"
            "define('WP_DEBUG', false);\n"
            "define( \"WP_ENVIRONMENT_TYPE\", 'staging' );\n"
        )
        runner = FakeRunner(content)
        argv = [ALIAS, "--env-keys", "/var/www/html/wp-config.php", "WP_DEBUG",
                "WP_ENVIRONMENT_TYPE", "DB_PASSWORD", "AUTH_KEY"]
        code, out, _ = self.run_main(argv, runner)
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            [
                "WP_DEBUG=false",
                "WP_ENVIRONMENT_TYPE=staging",
                "DB_PASSWORD=<présente, 14 caractères>",
                "AUTH_KEY=<absente>",
            ],
        )
        self.assertNotIn("hunter2", out)

    def test_env_keys_one_character(self):
        runner = FakeRunner("PIN=7\n")
        code, out, _ = self.run_main([ALIAS, "--env-keys", "/srv/app/.env", "PIN"], runner)
        self.assertEqual((code, out), (0, "PIN=<présente, 1 caractère>\n"))

    def test_env_keys_allowed_value_redacted(self):
        runner = FakeRunner("APP_URL=https://www.example.org/?token=abcd1234efgh\n")
        code, out, _ = self.run_main([ALIAS, "--env-keys", "/srv/app/.env", "APP_URL"], runner)
        self.assertEqual(code, 0)
        self.assertNotIn("abcd1234efgh", out)

    def test_env_keys_remote_failure_prints_nothing(self):
        runner = FakeRunner("DB_PASSWORD=hunter2-secret\ncat: read error\n", returncode=1)
        code, out, err = self.run_main(
            [ALIAS, "--env-keys", "/srv/app/.env", "DB_PASSWORD"], runner
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertNotIn("hunter2", err)

    def test_env_keys_usage_and_refusals(self):
        cases = [
            ([ALIAS, "--env-keys", "/srv/app/.env"], 2),
            ([ALIAS, "--env-keys"], 2),
            ([ALIAS, "--env-keys", "/srv/app/.env", "DB-PASSWORD"], 2),
            ([ALIAS, "--env-keys", "/srv/app/.env", "APP_ENV=x"], 2),
            ([ALIAS, "--env-keys", "/srv/app/../../etc/x.env", "APP_ENV"], 4),
            ([ALIAS, "--env-keys", "/proc/1/environ", "APP_ENV"], 4),
            ([ALIAS, "--env-keys", "/srv/app/*.env", "APP_ENV"], 4),
            ([ALIAS, "--env-keys", "-n", "APP_ENV"], 4),
            ([ALIAS, "--env-keys", "/srv/app/.env;id", "APP_ENV"], 4),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                runner = FakeRunner()
                code, _, err = self.run_main(argv, runner)
                self.assertEqual(code, expected)
                self.assertTrue(err)
                self.assertEqual(runner.calls, [])

    def test_php_ini(self):
        runner = FakeRunner("expose_php=\nmemory_limit=256M\n")
        code, out, _ = self.run_main([ALIAS, "--php-ini", "expose_php", "memory_limit"], runner)
        self.assertEqual(code, 0)
        self.assertEqual(out, "expose_php=\nmemory_limit=256M\n")
        words = shlex.split(runner.remote)
        self.assertEqual(words[:2], ["php", "-r"])
        self.assertEqual(len(words), 3)
        self.assertIn('"expose_php"', words[2])
        self.assertIn('"memory_limit"', words[2])
        self.assertIn("ini_get", words[2])
        self.assertNotIn("display_errors", words[2])

    def test_php_ini_unknown_key(self):
        for keys in (["disable_functions"], ["expose_php", "sendmail_path"], []):
            with self.subTest(keys=keys):
                runner = FakeRunner()
                code, _, err = self.run_main([ALIAS, "--php-ini"] + keys, runner)
                self.assertEqual(code, 2)
                self.assertTrue(err)
                self.assertEqual(runner.calls, [])

    def test_usage_errors(self):
        cases = [
            [],
            [ALIAS],
            [ALIAS, "--"],
            ["--check"],
            ["--check", "--"],
            ["--check", "cat", "/etc/hosts"],
            [ALIAS, "cat", "/etc/hosts"],
            [ALIAS, "--bogus"],
            ["-oProxyCommand=id", "--", "ls"],
            ["web preprod", "--", "ls"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                runner = FakeRunner()
                code, _, err = self.run_main(argv, runner)
                self.assertEqual(code, 2)
                self.assertTrue(err)
                self.assertEqual(runner.calls, [])

    def test_help(self):
        code, out, _ = self.run_main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("--env-keys", out)


if __name__ == "__main__":
    unittest.main()
