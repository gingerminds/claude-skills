#!/usr/bin/env python3
"""Read-only SSH guard of the checklist audit: the only way the skill runs server commands.

Remote output goes to stdout, masked; messages go to stderr.

Exit codes: 0 success, 1 remote failure, 2 usage, 4 refused (to be confirmed by the lead).
"""
import json
import shlex
import subprocess
import sys

from cqa.ssh_ro import (
    SQL_ENGINES,
    Refused,
    build_sql_remote,
    check_command,
    check_sql,
    read_env_keys,
    read_php_ini,
    run,
    validate_alias,
)

EXIT_OK = 0
EXIT_REMOTE = 1
EXIT_USAGE = 2
EXIT_REFUSED = 4

USAGE = """\
Garde-fou SSH en lecture seule.

  cqa_ssh_ro.py <alias> -- <commande...>
  cqa_ssh_ro.py --check -- <commande...>
  cqa_ssh_ro.py <alias> --sql <drush|wp|artisan> --app-dir <dossier>
                [--container <conteneur> | --compose-service <service>]
                [--skip-ssl] "<requête>"
  cqa_ssh_ro.py <alias> --env-keys <fichier> CLÉ...
  cqa_ssh_ro.py <alias> --php-ini CLÉ...

Codes de sortie : 0 succès, 1 échec distant, 2 usage, 4 refusé (à confirmer par le lead)."""

_SQL_OPTIONS = ("--app-dir", "--container", "--compose-service")


class UsageError(Exception):
    """Arguments the CLI cannot use, worded for the lead."""


def main(argv, *, runner=subprocess.run) -> int:
    """Run one guard command and return its exit code."""
    argv = list(argv)
    try:
        if argv[:1] in (["-h"], ["--help"]):
            print(USAGE)
            return EXIT_OK
        if argv[:1] == ["--check"]:
            return _check(argv[1:])
        if not argv:
            raise UsageError("alias SSH manquant.")
        validate_alias(argv[0])
        alias, mode, rest = argv[0], argv[1:2], argv[2:]
        if mode == ["--"]:
            return _command(alias, rest, runner)
        if mode == ["--sql"]:
            return _sql(alias, rest, runner)
        if mode == ["--env-keys"]:
            return _env_keys(alias, rest, runner)
        if mode == ["--php-ini"]:
            code, output = read_php_ini(alias, rest, runner=runner)
            return _finish(code, output)
        raise UsageError("mode attendu après l'alias : --, --sql, --env-keys ou --php-ini.")
    except Refused as refusal:
        return _refuse(str(refusal))
    except (UsageError, ValueError) as error:
        _log(f"{error}\n\n{USAGE}")
        return EXIT_USAGE


def _check(args: list) -> int:
    if args[:1] != ["--"] or len(args) < 2:
        raise UsageError("--check attend « -- <commande...> ».")
    allowed, reason = check_command(args[1:])
    print(json.dumps({"allowed": allowed, "reason": reason}, ensure_ascii=False))
    return EXIT_OK if allowed else EXIT_REFUSED


def _command(alias: str, words: list, runner) -> int:
    if not words:
        raise UsageError("commande manquante après « -- ».")
    allowed, reason = check_command(words)
    if not allowed:
        return _refuse(reason)
    code, output = run(alias, shlex.join(words), runner=runner)
    return _finish(code, output)


def _sql(alias: str, args: list, runner) -> int:
    if not args or args[0] not in SQL_ENGINES:
        raise UsageError("--sql attend drush, wp ou artisan.")
    engine, values, queries, skip_ssl = args[0], {}, [], False
    index = 1
    while index < len(args):
        token = args[index]
        index += 1
        if token in _SQL_OPTIONS:
            if token in values or index >= len(args):
                raise UsageError(f"{token} : valeur manquante ou option répétée.")
            values[token] = args[index]
            index += 1
        elif token == "--skip-ssl":
            if skip_ssl:
                raise UsageError("--skip-ssl : option répétée.")
            skip_ssl = True
        elif token.startswith("--"):
            raise UsageError(f"option inconnue « {token} ».")
        else:
            queries.append(token)
    if "--app-dir" not in values:
        raise UsageError("--app-dir est obligatoire avec --sql.")
    if len(queries) != 1:
        raise UsageError("--sql attend une seule requête, entre guillemets.")
    if "--container" in values and "--compose-service" in values:
        raise UsageError("--container et --compose-service sont exclusifs.")
    allowed, reason = check_sql(queries[0])
    if not allowed:
        return _refuse(reason)
    remote, stdin = build_sql_remote(
        engine,
        values["--app-dir"],
        queries[0],
        container=values.get("--container"),
        compose_service=values.get("--compose-service"),
        skip_ssl=skip_ssl,
    )
    code, output = run(alias, remote, stdin=stdin, runner=runner)
    return _finish(code, output)


def _env_keys(alias: str, args: list, runner) -> int:
    if len(args) < 2:
        raise UsageError("--env-keys attend un fichier puis au moins une clé.")
    code, output = read_env_keys(alias, args[0], args[1:], runner=runner)
    if code != 0:
        _log(f"Lecture du fichier impossible (code de sortie {code}) : contenu non affiché.")
        return EXIT_REMOTE
    sys.stdout.write(output)
    return EXIT_OK


def _finish(code: int, output: str) -> int:
    sys.stdout.write(output)
    if code != 0:
        _log(f"Échec distant : code de sortie {code}.")
        return EXIT_REMOTE
    return EXIT_OK


def _refuse(reason: str) -> int:
    _log(f"Refusé, à confirmer par le lead : {reason}")
    return EXIT_REFUSED


def _log(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
