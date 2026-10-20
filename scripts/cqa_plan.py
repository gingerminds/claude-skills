#!/usr/bin/env python3
"""Preparation, diagnostic and audit commands of the checklist audit.

Results go to stdout (JSON, except repos-tsv and toolbox-env which print lines for bash),
errors to stderr.

Exit codes: 0 success, 2 environment or usage, 6 invalid context.
"""
import argparse
import functools
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import urlsplit

from cqa.git_tools import (
    alignment,
    deployed_from_gitlab,
    remote_web_url,
    repo_state,
    same_gitlab_group,
)
from cqa.lots import TOOLS
from cqa.names import match_option, normalize
from cqa.project import parse_project_md, project_slug, sibling_repos
from cqa.target import probe_target, validate_url
from cqa.version import plugin_version

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONTEXT = 6

MAX_KEY_PAGES = 10
TOOLBOX_RATE = "2"

_SEPARATOR = " - "
_SHA = re.compile(r"[0-9a-f]{7,64}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_RUN_ID = re.compile(r"\d{4}-\d{2}-\d{2}-\d{4}")
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_UNPROTECTED = frozenset({"", "non", "aucun", "aucune", "n/a"})
_ACCESS_VALUES = ("open", "basic_auth", "ip_filtered", "unreachable")
_PROD_PHASES = ("J0", "J+7")
_RUN_FILES = {
    "context": "context.json",
    "lots": "lots.json",
    "plan": "notion/plan.json",
    "applied": "notion/applied.json",
    "synthese": "synthese.md",
}


class UsageError(Exception):
    """A problem with the environment or the arguments, worded for the lead."""


class ContextError(Exception):
    """A context.json that is missing, unreadable or unfit for the command."""


class Services(NamedTuple):
    """What the commands need from the outside, replaceable in tests."""

    run: Callable
    opener: Optional[object]


def main(argv, *, run=subprocess.run, opener=None) -> int:
    """Run one command and return its exit code.

    run executes git and glab; opener is the urllib opener of probe-target (default when None).
    """
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    try:
        return args.handler(args, Services(run, opener))
    except ContextError as error:
        print(error, file=sys.stderr)
        return EXIT_CONTEXT
    except (UsageError, ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cqa_plan.py",
        description="Préparation, diagnostic et audit de la checklist qualité.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commande")
    _register_preparation(commands)
    return parser


def _command(commands, name: str, help_text: str, handler) -> argparse.ArgumentParser:
    sub = commands.add_parser(name, help=help_text)
    sub.set_defaults(handler=handler)
    return sub


def _register_preparation(commands) -> None:
    sub = _command(commands, "project", "repos du projet, stack et PROJECT.md", _cmd_project)
    sub.add_argument("repo_path", help="dossier du repo de départ")
    sub = _command(commands, "team", "lead et équipe de PROJECT.md avec leurs options", _cmd_team)
    sub.add_argument("repo_path", help="dossier du repo contenant PROJECT.md")
    sub.add_argument("options_file", type=Path, help="JSON : options de « Assignée à »")
    sub = _command(commands, "repo-state", "état du repo face à origin/<base>", _cmd_repo_state)
    sub.add_argument("repo_path", help="dossier du repo")
    sub.add_argument("--base", default="develop", help="branche de référence (défaut : develop)")
    sub = _command(commands, "deployed", "commit déployé en préprod (GitLab)", _cmd_deployed)
    sub.add_argument("repo_path", help="dossier du repo")
    sub.add_argument("--branch", default=None, help="branche de préprod (repli sur les pipelines)")
    sub = _command(commands, "align", "la préprod contient-elle origin/develop ?", _cmd_align)
    sub.add_argument("repo_path", help="dossier du repo")
    sub.add_argument("sha", help="commit déployé en préprod")
    sub = _command(commands, "probe-target", "accès à la préprod (HEAD)", _cmd_probe_target)
    sub.add_argument("url", help="URL de la préprod")
    sub.add_argument(
        "--auth-env",
        action="store_true",
        help="envoie GM_AUDIT_AUTH_USER et GM_AUDIT_AUTH_PASS en Basic Auth",
    )
    sub = _command(commands, "context", "contrôle de context.json", _cmd_context)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--check", action="store_true", required=True, help="valide context.json")
    sub = _command(commands, "runs", "runs existants d'un projet et leur état", _cmd_runs)
    sub.add_argument("slug", help="identifiant du projet (slug)")
    sub = _command(commands, "repos-tsv", "nom<TAB>chemin de chaque repo", _cmd_repos_tsv)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub = _command(commands, "toolbox-env", "variables non secrètes (outillage)", _cmd_toolbox_env)
    sub.add_argument("run_dir", type=Path, help="dossier du run")


def _cmd_project(args, services) -> int:
    root = _repo_root(Path(args.repo_path), services.run)
    repo = _repo_entry(root, services.run)
    candidates = []
    for sibling in sibling_repos(root):
        entry = _repo_entry(sibling, services.run)
        remotes = (repo["remote"], entry["remote"])
        if all(remotes) and same_gitlab_group(*remotes):
            candidates.append(entry)
    names = [root.name] + [Path(entry["path"]).name for entry in candidates]
    try:
        slug = project_slug(names)
    except ValueError:
        raise UsageError(
            f"Le nom du dossier « {root.name} » ne permet pas de déduire le projet "
            "(forme attendue : « Client - Projet - Techno »)."
        )
    name = _project_name(root.name)
    _emit({"slug": slug, "name": name, "repos": [repo], "candidates": candidates})
    return EXIT_OK


def _cmd_team(args, services) -> int:
    project_md = _project_md(Path(args.repo_path))
    if project_md is None:
        raise UsageError(f"PROJECT.md est introuvable dans {args.repo_path}.")
    options = _read_options(args.options_file)
    lead_name = project_md["lead"]
    lead = {"name": lead_name, "option": match_option(lead_name, options) if lead_name else None}
    team = [
        {"name": dev["name"], "role": dev["role"], "option": match_option(dev["name"], options)}
        for dev in project_md["devs"]
    ]
    unmatched = []
    for person in [lead, *team]:
        if person["name"] and person["option"] is None and person["name"] not in unmatched:
            unmatched.append(person["name"])
    _emit({"lead": lead, "team": team, "unmatched": unmatched})
    return EXIT_OK


def _cmd_repo_state(args, services) -> int:
    _emit(_repo_state(Path(args.repo_path), args.base, services.run))
    return EXIT_OK


def _cmd_deployed(args, services) -> int:
    root = _repo_root(Path(args.repo_path), services.run)
    web_url = _web_url(_remote(root, services.run))
    if web_url is None:
        _emit({"sha": None, "source": None, "reason": "remote_inconnu"})
        return EXIT_OK
    parts = urlsplit(web_url)
    env = dict(os.environ)
    env["GITLAB_HOST"] = parts.netloc
    run = functools.partial(services.run, cwd=str(root), env=env)
    _emit(deployed_from_gitlab(parts.path.strip("/"), args.branch, run))
    return EXIT_OK


def _cmd_align(args, services) -> int:
    if not _SHA.fullmatch(args.sha):
        _emit({"aligned": None, "missing": [], "reason": "commit_inconnu"})
        return EXIT_OK
    path = Path(args.repo_path)
    state = _repo_state(path, "develop", services.run)
    result = alignment(path, args.sha, "origin/develop", services.run)
    if result["aligned"] is True and not state["fetched"]:
        result = {"aligned": None, "missing": [], "reason": "base_non_a_jour"}
    _emit(result)
    return EXIT_OK


def _cmd_probe_target(args, services) -> int:
    auth = _auth_from_env() if args.auth_env else None
    _emit(probe_target(args.url, auth=auth, opener=services.opener))
    return EXIT_OK


def _cmd_context(args, services) -> int:
    try:
        errors = validate_context(_load_context(args.run_dir))
    except ContextError as error:
        errors = [str(error)]
    _emit({"ok": not errors, "errors": errors})
    return EXIT_CONTEXT if errors else EXIT_OK


def _cmd_runs(args, services) -> int:
    if not _SLUG.fullmatch(args.slug):
        raise UsageError(
            f"« {args.slug} » n'est pas un identifiant de projet (minuscules, chiffres, tirets)."
        )
    base = Path.home() / ".cache" / "gm-audit" / args.slug
    runs = []
    if base.is_dir():
        for entry in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
            if entry.is_dir() and _RUN_ID.fullmatch(entry.name):
                runs.append(_run_state(entry))
    _emit({"slug": args.slug, "runs": runs})
    return EXIT_OK


def _cmd_repos_tsv(args, services) -> int:
    repos = _load_context(args.run_dir).get("repos")
    if not isinstance(repos, list) or not repos:
        raise ContextError("context.json : « repos » est absent ou vide.")
    lines = []
    for index, repo in enumerate(repos):
        label = f"repos[{index}]"
        if not isinstance(repo, dict):
            raise ContextError(f"context.json : « {label} » doit être un objet.")
        for key, check in (("name", _repo_name), ("path", _path)):
            problem = check(repo.get(key))
            if problem:
                raise ContextError(f"context.json : « {label}.{key} » {problem}.")
        lines.append(f"{repo['name']}\t{repo['path']}")
    _emit_lines(lines)
    return EXIT_OK


def _cmd_toolbox_env(args, services) -> int:
    context = _load_context(args.run_dir)
    target = context.get("target")
    if not isinstance(target, dict):
        raise ContextError("context.json : « target » est absent.")
    if _is_prod_phase(context.get("phase_filter")) and target.get("prod_live") is True:
        url, robots = _target_url(target, "prod_url"), "respect"
    else:
        url, robots = _target_url(target, "preprod_url"), "ignore"
    _emit_lines(
        [
            f"GM_AUDIT_TARGET={url}",
            f"GM_AUDIT_ROBOTS={robots}",
            f"GM_AUDIT_RATE={TOOLBOX_RATE}",
            f"GM_AUDIT_USER_AGENT=GingermindsAudit/{plugin_version()}",
        ]
    )
    return EXIT_OK


def _repo_root(path: Path, run) -> Path:
    result = _git(run, path, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout.strip():
        raise UsageError(f"« {path} » n'est pas dans un dépôt git.")
    return Path(result.stdout.strip())


def _remote(path: Path, run) -> Optional[str]:
    result = _git(run, path, "remote", "get-url", "origin")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git(run, path: Path, *args: str):
    return run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _repo_state(path: Path, base: str, run) -> dict:
    try:
        return repo_state(path, base, run)
    except RuntimeError as error:
        raise UsageError(str(error))


def _repo_entry(path: Path, run) -> dict:
    remote = _remote(path, run)
    project_md = _project_md(path)
    stack, techno = _detect_stack(path)
    return {
        "path": str(path),
        "name": _repo_label(path.name),
        "stack": stack,
        "techno": techno,
        "remote": _public_remote(remote) if remote else None,
        "web_url": _web_url(remote),
        "project_md": project_md,
        "protected": _is_protected(project_md),
    }


def _project_md(repo: Path) -> Optional[dict]:
    file = repo / "PROJECT.md"
    if not file.is_file():
        return None
    return parse_project_md(file.read_text(encoding="utf-8", errors="replace"))


def _is_protected(project_md: Optional[dict]) -> bool:
    value = (project_md or {}).get("protection") or ""
    return normalize(value).strip(" .!") not in _UNPROTECTED


def _detect_stack(repo: Path) -> tuple:
    """Return (stack, Notion techno) read from composer.json, package.json and wp-content/."""
    composer = _dependencies(repo / "composer.json", ("require", "require-dev"))
    if any(name.startswith("drupal/core") for name in composer):
        return "drupal", "Drupal"
    roots = any(name.startswith("roots/wordpress") for name in composer)
    if roots or (repo / "wp-content").is_dir():
        return "wordpress", "Wordpress"
    if "laravel/framework" in composer:
        return "laravel", "Laravel"
    package = _dependencies(repo / "package.json", ("dependencies", "devDependencies"))
    if "nuxt" in package:
        return "vue", "Nuxt"
    if "vue" in package:
        return "vue", None
    return None, None


def _dependencies(file: Path, sections: tuple) -> set:
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names = set()
    for section in sections if isinstance(document, dict) else ():
        found = document.get(section)
        if isinstance(found, dict):
            names.update(found)
    return names


def _web_url(remote: Optional[str]) -> Optional[str]:
    try:
        return remote_web_url(remote) if remote else None
    except ValueError:
        return None


def _public_remote(remote: str) -> str:
    """Return the remote without the password, and without any user for an http remote."""
    scheme, separator, rest = remote.partition("://")
    if not separator:
        return remote
    netloc, slash, tail = rest.partition("/")
    userinfo, at, host = netloc.rpartition("@")
    if at:
        user = "" if scheme.lower() in ("http", "https") else userinfo.partition(":")[0]
        netloc = f"{user}@{host}" if user else host
    return f"{scheme}://{netloc}{slash}{tail}"


def _project_name(dir_name: str) -> str:
    parts = [part.strip() for part in dir_name.split(_SEPARATOR)]
    return _SEPARATOR.join(parts[:-1]) if len(parts) >= 2 else dir_name


def _repo_label(dir_name: str) -> str:
    """Return the short name of a repo: the techno part of "Client - Projet - Techno", as a slug."""
    last = dir_name.split(_SEPARATOR)[-1]
    ascii_text = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "repo"


def _read_options(path: Path) -> list:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{path} est introuvable.")
    except (OSError, ValueError) as error:
        raise UsageError(f"{path} est illisible : {error}")
    options = document.get("assignee_options") if isinstance(document, dict) else document
    if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
        raise UsageError(
            f"{path} : un tableau de textes (ou un objet avec « assignee_options ») est attendu."
        )
    return options


def _auth_from_env() -> tuple:
    user = os.environ.get("GM_AUDIT_AUTH_USER", "")
    password = os.environ.get("GM_AUDIT_AUTH_PASS", "")
    if not user or not password:
        raise UsageError(
            "GM_AUDIT_AUTH_USER et GM_AUDIT_AUTH_PASS doivent être définis dans l'environnement "
            "pour --auth-env."
        )
    return user, password


def _run_state(run_dir: Path) -> dict:
    state = {"run_id": run_dir.name, "path": str(run_dir)}
    for key, relative in _RUN_FILES.items():
        state[key] = (run_dir / relative).is_file()
    findings = run_dir / "findings"
    state["findings"] = findings.is_dir() and any(findings.glob("*.json"))
    return state


def _is_prod_phase(phase_filter) -> bool:
    return isinstance(phase_filter, str) and any(tag in phase_filter for tag in _PROD_PHASES)


def _target_url(target: dict, key: str) -> str:
    url = target.get(key)
    if not isinstance(url, str) or not url:
        raise ContextError(f"context.json : « target.{key} » est absent ou vide.")
    try:
        validate_url(url)
    except ValueError as error:
        raise ContextError(f"context.json : « target.{key} » : {error}")
    if "?" in url or "#" in url:
        raise UsageError(
            f"context.json : « target.{key} » ne doit contenir ni « ? » ni « # » : "
            "une requête pourrait porter un jeton."
        )
    return url


def _load_context(run_dir: Path) -> dict:
    path = run_dir / "context.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContextError(f"context.json est introuvable dans {run_dir}.")
    except (OSError, ValueError) as error:
        raise ContextError(f"context.json est illisible : {error}")
    if not isinstance(document, dict):
        raise ContextError("context.json : un objet JSON est attendu.")
    return document


def validate_context(context: dict) -> list:
    """Return the problems of a context.json document, in French; an empty list means valid."""
    errors = []
    _check_fields(errors, "", context, _TOP_FIELDS)
    for name, fields in _SECTIONS.items():
        _check_object(errors, name, context, fields)
    _check_list(errors, "team", context, _TEAM_FIELDS)
    _check_list(errors, "repos", context, _REPO_FIELDS, minimum=1)
    _check_list(errors, "key_pages", context, _PAGE_FIELDS, maximum=MAX_KEY_PAGES)
    _check_repo_names(errors, context.get("repos"))
    target = context.get("target")
    if isinstance(target, dict) and target.get("prod_live") is True and not target.get("prod_url"):
        errors.append(_invalid("target.prod_url", "est vide alors que target.prod_live est vrai"))
    return errors


def _check_object(errors: list, name: str, parent: dict, fields: dict) -> None:
    if name not in parent:
        errors.append(_absent(name))
    elif not isinstance(parent[name], dict):
        errors.append(_invalid(name, "doit être un objet"))
    else:
        _check_fields(errors, name, parent[name], fields)


def _check_list(
    errors: list, name: str, parent: dict, fields: dict, *, minimum: int = 0, maximum=None
) -> None:
    if name not in parent:
        errors.append(_absent(name))
        return
    items = parent[name]
    if not isinstance(items, list):
        errors.append(_invalid(name, "doit être une liste"))
    elif len(items) < minimum:
        errors.append(_invalid(name, f"doit contenir au moins {minimum} élément"))
    elif maximum is not None and len(items) > maximum:
        errors.append(_invalid(name, f"doit contenir {maximum} éléments au plus ({len(items)})"))
    else:
        for index, item in enumerate(items):
            label = f"{name}[{index}]"
            if isinstance(item, dict):
                _check_fields(errors, label, item, fields)
            else:
                errors.append(_invalid(label, "doit être un objet"))


def _check_fields(errors: list, label: str, node: dict, fields: dict) -> None:
    for key, check in fields.items():
        path = f"{label}.{key}" if label else key
        if key not in node:
            errors.append(_absent(path))
            continue
        problem = check(node[key])
        if problem:
            errors.append(_invalid(path, problem))


def _check_repo_names(errors: list, repos) -> None:
    names = [
        repo["name"]
        for repo in (repos if isinstance(repos, list) else [])
        if isinstance(repo, dict) and isinstance(repo.get("name"), str)
    ]
    for name in sorted({name for name in names if names.count(name) > 1}):
        errors.append(_invalid("repos", f"contient deux fois le nom « {name} »"))


def _absent(path: str) -> str:
    return f"context.json : « {path} » est absent."


def _invalid(path: str, problem: str) -> str:
    return f"context.json : « {path} » {problem}."


def _text(value) -> Optional[str]:
    ok = isinstance(value, str) and value.strip()
    return None if ok else "doit être un texte non vide"


def _text_or_null(value) -> Optional[str]:
    return None if value is None or isinstance(value, str) else "doit être un texte ou null"


def _boolean(value) -> Optional[str]:
    return None if isinstance(value, bool) else "doit être un booléen"


def _boolean_or_null(value) -> Optional[str]:
    return None if value is None or isinstance(value, bool) else "doit être un booléen ou null"


def _object(value) -> Optional[str]:
    return None if isinstance(value, dict) else "doit être un objet"


def _texts(value) -> Optional[str]:
    ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
    return None if ok else "doit être une liste de textes"


def _schema(value) -> Optional[str]:
    return None if value == 1 and not isinstance(value, bool) else "doit valoir 1"


def _access(value) -> Optional[str]:
    if value is None or value in _ACCESS_VALUES:
        return None
    return "doit valoir " + ", ".join(_ACCESS_VALUES) + " ou null"


def _tools(value) -> Optional[str]:
    problem = _texts(value)
    if problem:
        return problem
    unknown = [tool for tool in value if tool not in TOOLS]
    return f"contient des outils inconnus : {', '.join(unknown)}" if unknown else None


def _repo_name(value) -> Optional[str]:
    if isinstance(value, str) and _REPO_NAME.fullmatch(value):
        return None
    return "doit être un nom simple (lettres, chiffres, point, tiret ou souligné)"


def _path(value) -> Optional[str]:
    if _text(value) is None and not any(char in value for char in "\t\r\n"):
        return None
    return "doit être un chemin non vide, sans tabulation ni retour à la ligne"


_TOP_FIELDS = {
    "schema": _schema,
    "run_id": _text,
    "plugin_version": _text,
    "technos": _texts,
    "available_tools": _tools,
    "phase_filter": _text_or_null,
    "dry_run": _boolean,
}
_SECTIONS = {
    "project": {"slug": _text, "name": _text, "protection": _text_or_null},
    "lead": {"name": _text_or_null, "option": _text_or_null},
    "target": {
        "preprod_url": _text_or_null,
        "prod_url": _text_or_null,
        "prod_live": _boolean,
        "access": _access,
    },
    "ssh": {"alias": _text_or_null, "app_dirs": _object},
    "test_accounts": {"front": _boolean, "backoffice": _boolean},
    "notion": {
        "copy_page_id": _text_or_null,
        "copy_data_source_id": _text,
        "reference_data_source_id": _text,
        "bot_id": _text,
        "assignee_options": _texts,
    },
}
_TEAM_FIELDS = {"name": _text, "role": _text_or_null, "option": _text_or_null}
_REPO_FIELDS = {
    "name": _repo_name,
    "path": _path,
    "remote": _text_or_null,
    "web_url": _text_or_null,
    "stack": _text_or_null,
    "techno": _text_or_null,
    "audited_sha": _text_or_null,
    "preprod_branch": _text_or_null,
    "deployed_sha": _text_or_null,
    "aligned": _boolean_or_null,
}
_PAGE_FIELDS = {"template": _text, "url": _text}


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _emit_lines(lines: list) -> None:
    print("".join(f"{line}\n" for line in lines), end="")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
