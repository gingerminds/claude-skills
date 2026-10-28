"""Read-only SSH guard: validate commands and SQL queries, run them over SSH, mask the output.

Commands are checked against an allowlist (default deny). Every remote command string is
built here with shlex.quote; SQL queries travel on stdin, never in the command line.
"""
import posixpath
import re
import shlex
import subprocess
from typing import NamedTuple, Optional

from cqa.secrets import known_values_from_env, redact

TIMEOUT = 120
ALLOWED = "commande de lecture autorisée"
SQL_ALLOWED = "requête de lecture autorisée"
SQL_ENGINES = ("drush", "wp", "artisan")
READ_ONLY = "SET SESSION TRANSACTION READ ONLY;\n"
ENV_PUBLIC_KEYS = (
    "APP_ENV", "APP_DEBUG", "APP_URL", "WP_DEBUG", "WP_ENVIRONMENT_TYPE", "LOG_LEVEL",
    "SESSION_SECURE_COOKIE",
)
PHP_INI_KEYS = (
    "expose_php", "display_errors", "memory_limit", "upload_max_filesize", "post_max_size",
    "max_execution_time", "session.cookie_secure", "session.cookie_httponly",
    "session.cookie_samesite", "opcache.enable",
)


class Refused(ValueError):
    """A command, query or path the guard refuses; the message is the French reason."""


# --- Tokens and paths -------------------------------------------------------------------

_ALIAS = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_METACHARACTERS = (";", "|", "&", ">", "<", "`")
_COMPONENT_SEPARATORS = re.compile(r"[/=:, ]+")
_GLOB = re.compile(r"[*?\[{]")
_PSEUDO_FS = re.compile(r"/+(?:proc|sys|dev)(?:/|$)")
_SECRET_NAME = re.compile(
    r"^\.env|\.env$|\.env\.|wp-config|^settings.*\.php|htpasswd|auth\.json"
    r"|\.(?:key|pem|p12|pfx)(?:\.|$)|^id_|_history$"
)
_SECRET_NAMES = frozenset(
    {"shadow", "gshadow", ".my.cnf", "debian.cnf", ".pgpass", ".netrc", ".git-credentials"}
)
_SECRET_PAIRS = frozenset(
    {(".git", "config"), (".docker", "config.json"), (".aws", "credentials")}
)


def validate_alias(alias: str) -> None:
    """Raise ValueError unless alias is a plain SSH alias (no option, no space)."""
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
        raise ValueError(f"alias SSH invalide : « {alias} »")


def _check_characters(text: str) -> None:
    for char in text:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            raise Refused("caractère de contrôle refusé (saut de ligne, tabulation...)")
        if code > 0x7F:
            raise Refused(
                f"caractère non ASCII refusé (U+{code:04X}) : risque de caractère trompeur"
            )


def _check_token(token, *, secrets: bool = True) -> None:
    """Refuse a token with a hidden or shell character, a ".." or a secret-bearing file."""
    if not isinstance(token, str):
        raise Refused("argument non textuel refusé")
    _check_characters(token)
    for char in _METACHARACTERS:
        if char in token:
            raise Refused(f"métacaractère « {char} » refusé")
    if "$" in token:
        raise Refused("caractère « $ » refusé : expansion par le shell distant")
    components = [part for part in _COMPONENT_SEPARATORS.split(token) if part]
    lowered = [part.lower() for part in components]
    if ".." in lowered:
        raise Refused("chemin avec « .. » refusé")
    if "environ" in lowered:
        raise Refused("lecture de l'environnement d'un processus refusée (/proc/<x>/environ)")
    if not secrets:
        return
    for index, part in enumerate(lowered):
        pair = tuple(lowered[index:index + 2])
        if _SECRET_NAME.search(part) or part in _SECRET_NAMES or pair in _SECRET_PAIRS:
            raise Refused(
                f"fichier porteur de secrets refusé (« {components[index]} ») : "
                "lire les clés utiles avec --env-keys"
            )


def _check_path(path: str, *, content: bool = False) -> None:
    """Refuse a path the remote side would not take literally, or a pseudo-file to read."""
    if not path:
        raise Refused("chemin vide refusé")
    if path.startswith("-"):
        raise Refused(f"chemin commençant par « - » refusé : « {path} »")
    if path.startswith("~") or _GLOB.search(path):
        raise Refused(
            f"jokers et « ~ » refusés dans un chemin, le garde-fou ne les développe pas : "
            f"« {path} »"
        )
    if content and _PSEUDO_FS.match(posixpath.normpath(path)):
        raise Refused(
            f"lecture d'un pseudo-fichier système refusée (/proc, /sys, /dev) : « {path} »"
        )


# --- Option parsing ---------------------------------------------------------------------

class _Options(NamedTuple):
    """Allowed options of a command. A value pattern of None accepts any value."""

    flags: str = ""
    valued: dict = {}
    long_flags: frozenset = frozenset()
    long_valued: dict = {}
    forbidden: dict = {}


def _parse(command: str, args: list, spec: _Options) -> tuple:
    """Split args like GNU getopt into ([(option, value)], operands); refuse unknown options."""
    options, operands = [], []
    index = 0
    while index < len(args):
        token = args[index]
        index += 1
        if not token.startswith("-"):
            operands.append(token)
            continue
        if token.startswith("--"):
            name, has_value, value = token.partition("=")
            _forbid(name, spec)
            if name in spec.long_flags and not has_value:
                options.append((name, None))
                continue
            if name not in spec.long_valued:
                raise Refused(f"{command} : option non autorisée « {name} »")
            if not has_value:
                value, index = _next_value(command, name, args, index)
            _check_value(command, name, value, spec.long_valued[name])
            options.append((name, value))
            continue
        cluster = token[1:]
        if not cluster:
            raise Refused(f"{command} : option non autorisée « - »")
        for position, char in enumerate(cluster):
            name = "-" + char
            _forbid(name, spec)
            if char in spec.flags:
                options.append((name, None))
                continue
            if char not in spec.valued:
                raise Refused(f"{command} : option non autorisée « {name} »")
            value = cluster[position + 1:]
            if not value:
                value, index = _next_value(command, name, args, index)
            _check_value(command, name, value, spec.valued[char])
            options.append((name, value))
            break
    return options, operands


def _forbid(name: str, spec: _Options) -> None:
    if name in spec.forbidden:
        raise Refused(spec.forbidden[name])


def _next_value(command: str, name: str, args: list, index: int) -> tuple:
    if index >= len(args):
        raise Refused(f"{command} : valeur manquante pour {name}")
    return args[index], index + 1


def _check_value(command: str, name: str, value: str, pattern) -> None:
    if pattern is not None and not pattern.fullmatch(value):
        raise Refused(f"{command} : valeur non autorisée pour {name} : « {value} »")


def _split_words(args: list) -> tuple:
    words = [token for token in args if not token.startswith("-")]
    options = [token for token in args if token.startswith("-")]
    return words, options


def _check_app_options(command: str, options: list, valued: dict, flags: frozenset) -> None:
    """Check "--name" or "--name=value" options of an application CLI (no separate values)."""
    for option in options:
        if option in flags:
            continue
        name, has_value, value = option.partition("=")
        if not has_value or name not in valued:
            raise Refused(f"{command} : option non autorisée « {name} »")
        if valued[name] == "path":
            _check_path(value)
        else:
            _check_value(command, name, value, valued[name])


# --- Commands ---------------------------------------------------------------------------

_INT = re.compile(r"\d+")
_SIGNED_INT = re.compile(r"\+?\d+")
_LIST = re.compile(r"[A-Za-z0-9_.,-]+")
_PS_FIELDS = re.compile(r"[A-Za-z0-9%,=:_+-]+")
_BSD_PS = re.compile(r"[auxwfjl]+")
_UNIT = re.compile(r"[A-Za-z0-9@._:*-]+")
_PRIORITY = re.compile(r"[0-7a-z.]+")
_JOURNAL_OUTPUT = re.compile(
    r"short|short-iso|short-iso-precise|short-precise|short-full|short-monotonic|short-unix"
    r"|cat|with-unit"
)
_URI = re.compile(r"[A-Za-z0-9:/._-]+")
_FORMAT = re.compile(r"table|json|yaml|csv|tsv|list|count")
_FIELDS = re.compile(r"[a-z_,-]+")
_WORD = re.compile(r"[a-z-]+")
_OPTION_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_WP_SECRET_OPTION = re.compile(
    r"pass|pwd|secret|token|key|salt|auth|nonce|licen[cs]e|credential|private|api", re.I
)

_FOLLOW = "suivi continu refusé (-f, --follow) : la commande ne se terminerait pas"
_RECURSIVE = "grep récursif refusé : il lirait aussi les fichiers porteurs de secrets"
_DENIED = {
    "sudo": "élévation de privilèges",
    "su": "élévation de privilèges",
    "sh": "interpréteur de commandes",
    "bash": "interpréteur de commandes",
    "env": "affiche les variables d'environnement",
    "printenv": "affiche les variables d'environnement",
    "set": "affiche les variables d'environnement",
}

# (options, operands): "content" = files to read (one at least), "paths" = paths, "none".
_SIMPLE = {
    "cat": (_Options(flags="nbsAETv"), "content"),
    "head": (
        _Options(
            flags="qv",
            valued={"n": _INT, "c": _INT},
            long_valued={"--lines": _INT, "--bytes": _INT},
        ),
        "content",
    ),
    "tail": (
        _Options(
            flags="qv",
            valued={"n": _SIGNED_INT, "c": _SIGNED_INT},
            long_valued={"--lines": _SIGNED_INT, "--bytes": _SIGNED_INT},
            forbidden={"-f": _FOLLOW, "-F": _FOLLOW, "--follow": _FOLLOW},
        ),
        "content",
    ),
    "ls": (_Options(flags="laAhtrSd1inR"), "paths"),
    "stat": (_Options(flags="Lt", valued={"c": None}, long_valued={"--format": None}), "paths"),
    "df": (_Options(flags="hHTilPk"), "paths"),
    "du": (
        _Options(flags="shcakmxS", valued={"d": _INT}, long_valued={"--max-depth": _INT}),
        "paths",
    ),
    "free": (_Options(flags="hmgkbtw"), "none"),
    "uptime": (_Options(flags="ps"), "none"),
}

_GREP = _Options(
    flags="icnlLvwxEFhHosq",
    valued={"m": _INT, "A": _INT, "B": _INT, "C": _INT, "e": None},
    long_flags=frozenset({
        "--ignore-case", "--count", "--line-number", "--invert-match", "--fixed-strings",
        "--extended-regexp", "--only-matching", "--no-filename", "--with-filename",
        "--files-with-matches", "--word-regexp",
    }),
    long_valued={
        "--max-count": _INT, "--regexp": None, "--context": _INT, "--after-context": _INT,
        "--before-context": _INT,
    },
    forbidden={
        "-r": _RECURSIVE, "-R": _RECURSIVE, "-d": _RECURSIVE, "--recursive": _RECURSIVE,
        "--dereference-recursive": _RECURSIVE, "--directories": _RECURSIVE,
    },
)

_FIND_FLAGS = frozenset({
    "-print", "-print0", "-ls", "-prune", "-quit", "-empty", "-readable", "-writable",
    "-executable", "-xdev", "-mount", "-depth", "-follow", "-nouser", "-nogroup", "-true",
    "-false", "-not", "-a", "-and", "-o", "-or", "!", "(", ")", "-daystart", "-noleaf",
})
_FIND_VALUED = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
    "-regextype", "-lname", "-ilname", "-type", "-xtype", "-maxdepth", "-mindepth", "-mtime",
    "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-newermt", "-size", "-perm", "-user",
    "-group", "-uid", "-gid", "-links", "-inum", "-used", "-fstype", "-printf",
})
_FIND_PATH_VALUED = frozenset({"-newer", "-anewer", "-cnewer", "-samefile"})
_FIND_FORBIDDEN = ("-exec", "-ok", "-delete", "-fprint", "-fls")

_PS = _Options(
    flags="AefFHjlwy",
    valued={"o": _PS_FIELDS, "p": _LIST, "u": _LIST, "C": _LIST},
    long_flags=frozenset({"--no-headers", "--forest"}),
    long_valued={"--sort": _PS_FIELDS},
)

_SYSTEMCTL = _Options(
    flags="laq",
    valued={"n": _INT},
    long_flags=frozenset({"--no-pager", "--full", "--all", "--plain", "--no-legend", "--quiet"}),
    long_valued={"--lines": _INT},
)
_SYSTEMCTL_VERBS = ("status", "is-active", "list-timers")

_JOURNAL_MAX_LINES = 500
_JOURNALCTL = _Options(
    flags="qrk",
    valued={
        "n": _INT, "u": _UNIT, "p": _PRIORITY, "S": None, "U": None, "g": None, "t": _LIST,
        "o": _JOURNAL_OUTPUT,
    },
    long_flags=frozenset({
        "--no-pager", "--no-hostname", "--quiet", "--reverse", "--dmesg", "--utc",
    }),
    long_valued={
        "--lines": _INT, "--unit": _UNIT, "--priority": _PRIORITY, "--since": None,
        "--until": None, "--grep": None, "--identifier": _LIST, "--output": _JOURNAL_OUTPUT,
    },
    forbidden={"-f": _FOLLOW, "--follow": _FOLLOW},
)

_DRUSH_COMMANDS = ("status", "pm:list", "pm:security")
_DRUSH_VALUED = {
    "--root": "path", "--uri": _URI, "--format": _FORMAT,
    "--type": re.compile(r"module|theme"), "--status": re.compile(r"enabled|disabled"),
}
_DRUSH_FLAGS = frozenset({"--no-core"})

_WP_COMMANDS = (("core", "version"), ("plugin", "list"), ("theme", "list"),
                ("cron", "event", "list"))
_WP_VALUED = {
    "--path": "path", "--url": _URI, "--format": _FORMAT, "--fields": _FIELDS,
    "--status": _WORD,
}
_WP_FLAGS = frozenset({"--skip-plugins", "--skip-themes", "--allow-root", "--extra"})

_ARTISAN_COMMANDS = ("about", "route:list", "schedule:list")
_ARTISAN_FLAGS = frozenset({"--json", "--no-ansi"})

_DOCKER_PS = _Options(
    flags="aq",
    long_flags=frozenset({"--all", "--quiet", "--no-trunc"}),
    long_valued={"--format": None, "--filter": None},
)
_COMPOSE_PS = _Options(
    flags="aq",
    long_flags=frozenset({"--all", "--quiet", "--services"}),
    long_valued={"--format": None, "--filter": None},
)
_COMPOSE_GLOBAL = {
    "-f": "path", "--file": "path", "--project-directory": "path",
    "-p": "name", "--project-name": "name",
}
_INSPECT = "docker inspect refusé : il affiche les variables d'environnement (secrets)"


def check_command(argv: list[str]) -> tuple[bool, str]:
    """Return (allowed, French reason) for argv, a command split into words."""
    try:
        _check(argv)
    except Refused as refusal:
        return False, str(refusal)
    return True, ALLOWED


def _check(argv) -> None:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise Refused("commande vide ou mal formée")
    for token in argv:
        _check_token(token)
    name, args = argv[0], list(argv[1:])
    if name in _DENIED:
        raise Refused(f"« {name} » refusé : {_DENIED[name]}")
    if _is_binary(name, "drush"):
        _drush(args)
    elif _is_binary(name, "wp"):
        _wp(args)
    elif name in _SIMPLE:
        _simple(name, args)
    elif name in _HANDLERS:
        _HANDLERS[name](args)
    else:
        raise Refused(f"« {name} » est hors de la liste blanche des commandes de lecture")


_BINARY_FORMS = {
    "drush": ("drush", "vendor/bin/drush", "./vendor/bin/drush"),
    "wp": ("wp",),
    "artisan": ("artisan",),
}
_BINARY_SUFFIXES = {"drush": "/vendor/bin/drush", "artisan": "/artisan"}
_BINARY_HINTS = {
    "drush": "drush, vendor/bin/drush ou /<dossier>/vendor/bin/drush",
    "wp": "wp seul",
    "artisan": "artisan (depuis le dossier de l'application) ou /<dossier>/artisan",
}
_UNSAFE_DIRS = ("/tmp/", "/var/tmp/", "/dev/shm/", "/home/", "/root/", "/run/user/")


def _is_binary(token: str, name: str) -> bool:
    """True for an allowed form of an application binary; refuse any other path to it.

    Only fixed forms and absolute, normalized paths outside temporary and home folders are
    allowed, so that a file planted on the server is not run.
    """
    if token in _BINARY_FORMS[name]:
        return True
    if token != name and not token.endswith("/" + name):
        return False
    _check_path(token)
    suffix = _BINARY_SUFFIXES.get(name)
    absolute = (
        suffix is not None
        and token.endswith(suffix)
        and token.startswith("/")
        and not token.startswith("//")
        and posixpath.normpath(token) == token
        and not token.startswith(_UNSAFE_DIRS)
    )
    if not absolute:
        raise Refused(
            f"{name} : chemin refusé « {token} » (fichier peut-être déposé sur le serveur) ; "
            f"formes permises : {_BINARY_HINTS[name]}, hors de /tmp, /var/tmp, /dev/shm, "
            "/home, /root et /run/user"
        )
    return True


def _simple(name: str, args: list) -> None:
    spec, kind = _SIMPLE[name]
    _, operands = _parse(name, args, spec)
    if kind == "none" and operands:
        raise Refused(f"{name} : aucun argument autorisé")
    if kind == "content":
        _content_paths(name, operands)
        return
    for path in operands:
        _check_path(path)


def _content_paths(command: str, paths: list) -> None:
    if not paths:
        raise Refused(f"{command} : indiquer au moins un fichier")
    for path in paths:
        _check_path(path, content=True)


def _grep(args: list) -> None:
    options, operands = _parse("grep", args, _GREP)
    if not any(name in ("-e", "--regexp") for name, _ in options):
        if not operands:
            raise Refused("grep : motif manquant")
        operands = operands[1:]
    _content_paths("grep", operands)


def _find(args: list) -> None:
    index = 0
    while index < len(args) and args[index] in ("-H", "-L", "-P"):
        index += 1
    # Starting points end at the first word beginning with "-", or "(", ")", "!" (GNU find).
    while index < len(args) and not args[index].startswith(("-", "(", ")", "!")):
        _check_path(args[index])
        index += 1
    while index < len(args):
        token = args[index]
        index += 1
        if token in _FIND_FLAGS:
            continue
        if token in _FIND_VALUED or token in _FIND_PATH_VALUED:
            value, index = _next_value("find", token, args, index)
            if token in _FIND_PATH_VALUED:
                _check_path(value)
            continue
        if token.startswith(_FIND_FORBIDDEN):
            raise Refused(f"find : {token} refusé (exécute, supprime ou écrit des fichiers)")
        raise Refused(f"find : prédicat non autorisé « {token} »")


def _ps(args: list) -> None:
    _, operands = _parse("ps", args, _PS)
    for token in operands:
        if "e" in token:
            raise Refused("ps : le modificateur « e » affiche l'environnement des processus")
        if not _BSD_PS.fullmatch(token):
            raise Refused(f"ps : option non autorisée « {token} »")


def _systemctl(args: list) -> None:
    _, operands = _parse("systemctl", args, _SYSTEMCTL)
    if not operands or operands[0] not in _SYSTEMCTL_VERBS:
        raise Refused("systemctl : seuls status, is-active et list-timers sont autorisés")
    for unit in operands[1:]:
        if not _UNIT.fullmatch(unit):
            raise Refused(f"systemctl : nom d'unité invalide « {unit} »")


def _journalctl(args: list) -> None:
    options, operands = _parse("journalctl", args, _JOURNALCTL)
    if operands:
        raise Refused("journalctl : filtres positionnels refusés, utiliser -u")
    lines = [int(value) for name, value in options if name in ("-n", "--lines")]
    names = {name for name, _ in options}
    if not lines or max(lines) > _JOURNAL_MAX_LINES or "--no-pager" not in names:
        raise Refused(
            f"journalctl : -n N ({_JOURNAL_MAX_LINES} au plus) et --no-pager sont obligatoires"
        )


def _crontab(args: list) -> None:
    if args != ["-l"]:
        raise Refused("crontab : seul -l est autorisé")


def _php(args: list) -> None:
    if args and _is_binary(args[0], "artisan"):
        _artisan(args[1:])
        return
    if "-i" in args:
        raise Refused("php -i affiche les variables d'environnement : utiliser --php-ini")
    if len(args) != 1 or args[0] not in ("-v", "-m", "--ini"):
        raise Refused("php : seuls -v, -m et --ini sont autorisés")


def _artisan(args: list) -> None:
    words, options = _split_words(args)
    if not words and options == ["--version"]:
        return
    if len(words) != 1 or words[0] not in _ARTISAN_COMMANDS:
        raise Refused(
            "php artisan : seuls about, route:list, schedule:list et --version sont autorisés"
        )
    _check_app_options("php artisan", options, {}, _ARTISAN_FLAGS)


def _drush(args: list) -> None:
    words, options = _split_words(args)
    if not words and options == ["--version"]:
        return
    if len(words) != 1 or words[0] not in _DRUSH_COMMANDS:
        raise Refused("drush : seuls status, pm:list, pm:security et --version sont autorisés")
    _check_app_options("drush", options, _DRUSH_VALUED, _DRUSH_FLAGS)


def _wp(args: list) -> None:
    words, options = _split_words(args)
    if not words and options == ["--version"]:
        return
    words = tuple(words)
    if len(words) == 3 and words[:2] == ("option", "get"):
        name = words[2]
        if not _OPTION_NAME.fullmatch(name) or _WP_SECRET_OPTION.search(name):
            raise Refused(f"wp option get : nom d'option refusé (motif de secret) : « {name} »")
    elif words not in _WP_COMMANDS:
        raise Refused(
            "wp : seuls core version, plugin list, theme list, cron event list, option get "
            "et --version sont autorisés"
        )
    _check_app_options("wp", options, _WP_VALUED, _WP_FLAGS)


def _git(args: list) -> None:
    if len(args) >= 3 and args[0] == "-C":
        rest = args[2:]
        is_log = len(rest) == 3 and rest[:2] == ["log", "-1"] and rest[2].startswith("--format=")
        if rest == ["rev-parse", "HEAD"] or is_log:
            _check_path(args[1])
            return
    raise Refused(
        "git : seuls -C <dir> rev-parse HEAD et -C <dir> log -1 --format=<fmt> sont autorisés"
    )


def _server_version(name: str):
    def check(args: list) -> None:
        if args != ["-v"]:
            raise Refused(f"{name} : seul -v est autorisé")
    return check


def _docker(args: list) -> None:
    sub, rest = (args[0], args[1:]) if args else (None, [])
    if sub == "inspect" or (sub == "container" and rest[:1] == ["inspect"]):
        raise Refused(_INSPECT)
    if sub == "ps":
        _, operands = _parse("docker ps", rest, _DOCKER_PS)
        if operands:
            raise Refused("docker ps : aucun argument autorisé")
    elif sub == "exec":
        _exec("docker exec", rest)
    elif sub == "compose":
        _compose(rest)
    else:
        raise Refused("docker : seuls ps, compose ps, exec et compose exec sont autorisés")


def _compose(args: list) -> None:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        name, has_value, value = args[index].partition("=")
        index += 1
        if name not in _COMPOSE_GLOBAL:
            raise Refused(f"docker compose : option non autorisée « {name} »")
        if not has_value:
            value, index = _next_value("docker compose", name, args, index)
        if _COMPOSE_GLOBAL[name] == "path":
            _check_path(value)
        else:
            _check_name(value)
    sub, rest = args[index:index + 1], args[index + 1:]
    if sub == ["ps"]:
        _, services = _parse("docker compose ps", rest, _COMPOSE_PS)
        for service in services:
            _check_name(service)
    elif sub == ["exec"]:
        _exec("docker compose exec", rest)
    else:
        raise Refused("docker compose : seuls ps et exec sont autorisés")


def _exec(command: str, args: list) -> None:
    """Check "[-T] [-w <dir>] <name> <cmd...>", then the inner command itself."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        token = args[index]
        index += 1
        if token == "-w":
            directory, index = _next_value(command, token, args, index)
            _check_path(directory)
        elif token != "-T":
            raise Refused(
                f"{command} : option non autorisée « {token} » (seules -T et -w <dir>)"
            )
    if index >= len(args):
        raise Refused(f"{command} : conteneur manquant")
    _check_name(args[index])
    inner = args[index + 1:]
    if not inner:
        raise Refused(f"{command} : commande à lancer manquante")
    try:
        _check(inner)
    except Refused as refusal:
        raise Refused(f"commande lancée dans le conteneur : {refusal}") from None


def _check_name(name) -> None:
    _check_token(name)
    if not _NAME.fullmatch(name):
        raise Refused(f"nom de conteneur ou de service invalide : « {name} »")


_HANDLERS = {
    "grep": _grep,
    "find": _find,
    "ps": _ps,
    "systemctl": _systemctl,
    "journalctl": _journalctl,
    "crontab": _crontab,
    "php": _php,
    "git": _git,
    "nginx": _server_version("nginx"),
    "apachectl": _server_version("apachectl"),
    "docker": _docker,
}


# --- SQL --------------------------------------------------------------------------------

_SQL_START = re.compile(r"(?:SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.I)
_SQL_COMMENT = re.compile(r"--|#|/\*")
_SQL_INTO = re.compile(r"\bINTO\b", re.I)
_SQL_FUNCTION = re.compile(
    r"\b(LOAD_FILE|SLEEP|BENCHMARK|GET_LOCK|RELEASE_LOCK|pg_\w+|lo_\w+|dblink\w*|nextval"
    r"|setval|set_config|query_to_xml\w*|sys_exec|sys_eval)\s*\(|\b(LOAD_FILE)\b",
    re.I,
)
_SQL_WRITE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE(?!\s*\()|MERGE|UPSERT|CALL|DO|HANDLER|LOAD|LOCK|UNLOCK"
    r"|SHARE|ANALYZE|SET|TRUNCATE|DROP|ALTER|GRANT|REVOKE|RENAME|KILL|SHUTDOWN|FLUSH|RESET"
    r"|PURGE|PREPARE|EXECUTE|COPY|VACUUM)\b",
    re.I,
)
_SQL_CREDENTIAL = re.compile(
    r"pass|pwd|secret|token|salt|authentication_string|api_?key|\bCREATE\s+USER\b", re.I
)
_SQL_TOKEN = re.compile(r"\s+|'(?:[^']|'')*'|\"[^\"]*\"|`[^`]*`|[A-Za-z0-9_$]+|.")
_SQL_SENSITIVE_TABLES = frozenset({"users", "wp_users", "users_field_data", "sessions"})
_SQL_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|`[^`]*`|\"[^\"]*\"")
_SQL_LIST_END = frozenset({
    "FROM", "WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "UNION", "INTERSECT", "EXCEPT",
    "WINDOW", "FOR", "INTO", "LOCK", "OFFSET", "FETCH",
})


def check_sql(query: str) -> tuple[bool, str]:
    """Return (allowed, French reason) for a single read-only SQL statement."""
    try:
        _sql_body(query)
    except Refused as refusal:
        return False, str(refusal)
    return True, SQL_ALLOWED


def _sql_body(query) -> str:
    """Return the statement without its final ";", or raise Refused."""
    if not isinstance(query, str):
        raise Refused("requête non textuelle refusée")
    text = query.strip()
    _check_characters(text)
    if "\\" in text:
        raise Refused("« \\ » refusé : commandes du client SQL")
    if text.count(";") > 1 or (";" in text and not text.endswith(";")):
        raise Refused(
            "une seule instruction est permise : au plus un « ; », en dernière position"
        )
    body = text[:-1].rstrip() if text.endswith(";") else text
    if not body:
        raise Refused("requête vide")
    if not _SQL_START.match(body):
        raise Refused("la requête doit commencer par SELECT, SHOW, DESCRIBE, DESC ou EXPLAIN")
    if _SQL_COMMENT.search(body):
        raise Refused("commentaires refusés dans la requête")
    if _SQL_INTO.search(body):
        raise Refused("INTO refusé : écriture dans un fichier ou une variable")
    function = _SQL_FUNCTION.search(body)
    if function:
        name = function.group(1) or function.group(2)
        raise Refused(
            f"fonction refusée « {name.upper()} » : lecture de fichier, attente ou effet de bord"
        )
    write = _SQL_WRITE.search(body)
    if write:
        raise Refused(f"mot-clé d'écriture ou de verrou refusé : « {write.group(1).upper()} »")
    if _SQL_CREDENTIAL.search(body):
        raise Refused("requête refusée : elle vise des mots de passe, jetons ou secrets")
    _check_select_lists(_sql_tokens(body))
    return body


def _sql_tokens(body: str) -> list:
    """Split a statement into tokens; whitespace dropped, quoted strings and names kept whole."""
    tokens = [token for token in _SQL_TOKEN.findall(body) if not token.isspace()]
    for token in tokens:
        if token in ("'", '"', "`"):
            raise Refused(f"guillemet « {token} » non fermé dans la requête")
    return tokens


def _check_select_lists(tokens: list) -> None:
    """Refuse a "*" in a select list, and any select list but COUNT(...) near a sensitive table.

    A sensitive table counts wherever its name stands (FROM, JOIN, subquery, condition): the
    check stays on the safe side. SHOW and DESCRIBE, without SELECT, keep the schema readable.
    """
    lists = [_select_list(tokens, index) for index, token in enumerate(tokens)
             if token.upper() == "SELECT"]
    for items in lists:
        stars = [index for index, token in enumerate(items) if token == "*"]
        if not all(_counts_rows(items, index) for index in stars):
            raise Refused(
                "« * » refusé dans la liste des colonnes : nommer les colonnes utiles "
                "(COUNT(*) reste permis)"
            )
    sensitive = next((name for name in map(_sql_name, tokens) if _is_sensitive(name)), None)
    if sensitive and not all(_only_counts(items) for items in lists):
        raise Refused(
            f"table sensible « {sensitive} » : seul COUNT(...) est permis dans la liste "
            "des colonnes"
        )


def _select_list(tokens: list, start: int) -> list:
    """Return the tokens of the select list of the SELECT at tokens[start]."""
    depth, items = 0, []
    for token in tokens[start + 1:]:
        if token == ")" and depth == 0:
            break
        if depth == 0 and token.upper() in _SQL_LIST_END:
            break
        depth += {"(": 1, ")": -1}.get(token, 0)
        items.append(token)
    return items


def _counts_rows(items: list, index: int) -> bool:
    """True for the "*" of COUNT(*) at items[index]."""
    before = [token.upper() for token in items[max(0, index - 2):index]]
    return before == ["COUNT", "("] and items[index + 1:index + 2] == [")"]


def _only_counts(items: list) -> bool:
    """True when every item of a select list is COUNT(...), with an optional alias."""
    for item in _split_items(items):
        if [token.upper() for token in item[:2]] != ["COUNT", "("]:
            return False
        close = _closing(item, 1)
        if close is None:
            return False
        rest = item[close + 1:]
        if rest[:1] and rest[0].upper() == "AS":
            rest = rest[1:]
        if rest and (len(rest) > 1 or not _SQL_ALIAS.fullmatch(rest[0])):
            return False
    return True


def _split_items(items: list) -> list:
    parts, current, depth = [], [], 0
    for token in items:
        if token == "," and depth == 0:
            parts.append(current)
            current = []
            continue
        depth += {"(": 1, ")": -1}.get(token, 0)
        current.append(token)
    return parts + [current]


def _closing(tokens: list, opening: int) -> Optional[int]:
    depth = 0
    for index in range(opening, len(tokens)):
        depth += {"(": 1, ")": -1}.get(tokens[index], 0)
        if depth == 0:
            return index
    return None


def _sql_name(token: str) -> str:
    return token[1:-1] if token[:1] in ('"', "`") else token


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SQL_SENSITIVE_TABLES or lowered.endswith("_tokens")


def build_sql_remote(
    engine: str,
    app_dir: str,
    query: str,
    *,
    container: Optional[str] = None,
    compose_service: Optional[str] = None,
    skip_ssl: bool = False,
) -> tuple[str, str]:
    """Return (remote command, stdin text) for a validated query; raise ValueError otherwise.

    Refused (a ValueError) means the guard refuses the query, the folder or the name.
    skip_ssl adds the client option that skips TLS verification (self-signed certificate).
    """
    if engine not in SQL_ENGINES:
        raise ValueError(f"moteur SQL inconnu « {engine} » : drush, wp ou artisan")
    if container is not None and compose_service is not None:
        raise ValueError("--container et --compose-service sont exclusifs")
    if skip_ssl and engine == "artisan":
        raise ValueError("--skip-ssl n'est pas pris en charge avec artisan")
    body = _sql_body(query)
    _check_token(app_dir)
    if not app_dir.startswith("/"):
        raise Refused(f"le dossier de l'application doit être un chemin absolu : « {app_dir} »")
    _check_path(app_dir)
    directory = shlex.quote(app_dir)
    if engine == "drush":
        extra = "--extra=--skip-ssl " if skip_ssl else ""
        remote = f"cd {directory} && vendor/bin/drush sql:query {extra}--file=-"
    elif engine == "wp":
        remote = "wp db query " + ("--skip-ssl " if skip_ssl else "") + shlex.quote("--path=" + app_dir)
    else:
        remote = f"cd {directory} && php artisan db"
    if container is not None:
        _check_name(container)
        remote = f"docker exec -i {container} sh -c {shlex.quote(remote)}"
    elif compose_service is not None:
        _check_name(compose_service)
        remote = f"docker compose exec -T {compose_service} sh -c {shlex.quote(remote)}"
    return remote, f"{READ_ONLY}{body};\n"


# --- Execution --------------------------------------------------------------------------

def run(
    alias: str, remote: str, *, stdin: Optional[str] = None, runner=subprocess.run
) -> tuple[int, str]:
    """Run a guard-built remote command over SSH; return (exit code, masked output).

    remote must come from this module (check_command words joined with shlex.join, or a
    build_* function): run does not validate it. stderr is merged into the output.
    """
    code, output = _ssh(alias, remote, stdin=stdin, runner=runner)
    return code, redact(output, known_values_from_env())


def _ssh(alias: str, remote: str, *, stdin: Optional[str], runner) -> tuple:
    validate_alias(alias)
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, remote]
    options = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "errors": "replace",
        "timeout": TIMEOUT,
    }
    if stdin is None:
        options["stdin"] = subprocess.DEVNULL
    else:
        options["input"] = stdin
    try:
        result = runner(command, **options)
    except subprocess.TimeoutExpired:
        return 124, f"Délai dépassé : la commande a été interrompue après {TIMEOUT} s.\n"
    except OSError as error:
        return 127, f"ssh n'a pas pu être lancé : {error}\n"
    return result.returncode, result.stdout or ""


def read_env_keys(
    alias: str, path: str, keys: list[str], *, runner=subprocess.run
) -> tuple[int, str]:
    """Read a .env or wp-config.php file remotely; return (exit code, text to show).

    Only the keys of ENV_PUBLIC_KEYS show their value; the others show their presence and
    length. On a remote failure the text is empty: the raw output is never shown.
    """
    _check_token(path, secrets=False)
    _check_path(path, content=True)
    if not keys:
        raise ValueError("--env-keys : indiquer au moins une clé")
    for key in keys:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError(f"--env-keys : nom de clé invalide « {key} »")
    code, content = _ssh(alias, "cat " + shlex.quote(path), stdin=None, runner=runner)
    if code != 0:
        return code, ""
    values = _parse_env(content)
    known = known_values_from_env()
    lines = []
    for key in keys:
        if key not in values:
            lines.append(f"{key}=<absente>")
        elif key in ENV_PUBLIC_KEYS:
            lines.append(f"{key}={redact(values[key], known)}")
        else:
            size = len(values[key])
            unit = "caractère" if size <= 1 else "caractères"
            lines.append(f"{key}=<présente, {size} {unit}>")
    return 0, "\n".join(lines) + "\n"


_ENV_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*?)\s*$")
_DEFINE = re.compile(r"""define\s*\(\s*(['"])([A-Za-z_][A-Za-z0-9_]*)\1\s*,\s*(.*?)\s*\)\s*;""")


def _parse_env(content: str) -> dict:
    """Map each key of a .env file or of wp-config.php define() calls to its last value."""
    values = {}
    for line in content.splitlines():
        define = _DEFINE.search(line)
        if define:
            values[define.group(2)] = _unquote(define.group(3), comment=False)
            continue
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(1)] = _unquote(match.group(2), comment=True)
    return values


def _unquote(raw: str, *, comment: bool) -> str:
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    if comment:
        raw = re.split(r"\s#", raw, maxsplit=1)[0]
    return raw.strip()


def build_php_ini_remote(keys: list) -> str:
    """Return the remote command printing "key=value" for each key of PHP_INI_KEYS."""
    if not keys:
        raise ValueError("--php-ini : indiquer au moins une clé")
    unknown = [key for key in keys if key not in PHP_INI_KEYS]
    if unknown:
        raise ValueError(
            f"--php-ini : clé hors de la liste ({', '.join(map(str, unknown))}) ; "
            f"clés permises : {', '.join(PHP_INI_KEYS)}"
        )
    names = ", ".join(f'"{key}"' for key in keys)
    code = (
        f"foreach ([{names}] as $k) {{ $v = ini_get($k); "
        'echo $k, "=", $v === false ? "<inconnue>" : $v, PHP_EOL; }'
    )
    return "php -r " + shlex.quote(code)


def read_php_ini(alias: str, keys: list[str], *, runner=subprocess.run) -> tuple[int, str]:
    """Return (exit code, masked "key=value" lines) for the PHP CLI of the server."""
    return run(alias, build_php_ini_remote(keys), runner=runner)
