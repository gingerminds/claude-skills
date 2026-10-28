#!/usr/bin/env python3
"""Preparation, diagnostic and audit commands of the checklist audit.

Results go to stdout (JSON, except repos-tsv and toolbox-env which print lines for bash),
errors to stderr.

Exit codes: 0 success, 2 environment or usage, 6 invalid context or findings, 7 secret found.
"""
import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from cqa import deferred, probes, report
from cqa.checklist import STATUS_IMPLEMENTED, parse_refs, ref_label
from cqa.findings import apply_guardrails, validate_document
from cqa.git_tools import (
    alignment,
    deployed_from_gitlab,
    remote_web_url,
    repo_state,
    same_gitlab_group,
)
from cqa.lots import TOOLS, build_lots, coverage, select_rows
from cqa.names import match_option, normalize, propose_assignment
from cqa.notion_read import write_json, write_text
from cqa.plan import build_plan, filter_plan, load_history, operation_key, plan_digest
from cqa.rerun import latest_findings, rerun_candidates
from cqa.project import parse_project_md, project_slug, sibling_repos
from cqa.secrets import known_values_from_env, scan_tree
from cqa.target import build_opener, fetch_page, probe_target, validate_url
from cqa.version import plugin_version

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONTEXT = 6
EXIT_SECRET = 7

MAX_KEY_PAGES = 10
MAX_SITES = 10
MAX_KEY_PAGE_REQUESTS = 50
KEY_PAGES_DEADLINE = 80
TOOLBOX_RATE = "2"

_SEPARATOR = " - "
_SHA = re.compile(r"[0-9a-f]{7,64}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_RUN_ID = re.compile(r"\d{4}-\d{2}-\d{2}-\d{4}")
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_UNPROTECTED = frozenset({"", "non", "aucun", "aucune", "n/a"})
_ACCESS_VALUES = ("open", "basic_auth", "ip_filtered", "unreachable")
_TOOL_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_EVIDENCE_TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REF_KEY = re.compile(r"[0-9]+")
_SPEC_NAME = re.compile(r"(?:openapi|swagger).*\.(?:ya?ml|json)")
_SPEC_SKIPPED_DIRS = frozenset({"vendor", "node_modules", ".git"})
_EXCLUDED_KEYS = ("ref", "page_id", "reason", "verdict", "detail")
_MISSING_ROW = "constat manquant : "
_RERUN_FILE = re.compile(r"(.+)-rerun-([0-9]{2})\.json")
_MAX_COMMENT = 1500
_EM_DASH = chr(0x2014)
_RUN_FILES = {
    "context": "context.json",
    "lots": "lots.json",
    "plan": "notion/plan.json",
    "applied": "notion/applied.json",
    "synthese": "synthese.md",
    "done": "done.json",
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
    _register_audit(commands)
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
    sub = _command(commands, "key-pages-check", "pages clés et liens de l'accueil", _cmd_key_pages)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument(
        "--auth-env",
        action="store_true",
        help="envoie GM_AUDIT_AUTH_USER et GM_AUDIT_AUTH_PASS en Basic Auth",
    )
    sub = _command(commands, "reuse-context", "reprend les réponses d'un run précédent", _cmd_reuse)
    sub.add_argument("new_run_dir", type=Path, help="dossier du nouveau run")
    sub.add_argument("old_run_dir", type=Path, help="dossier du run à réutiliser")
    sub = _command(commands, "context", "contrôle de context.json", _cmd_context)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--check", action="store_true", required=True, help="valide context.json")
    sub = _command(commands, "runs", "runs existants d'un projet et leur état", _cmd_runs)
    sub.add_argument("slug", help="identifiant du projet (slug)")
    sub = _command(commands, "repos-tsv", "nom<TAB>chemin de chaque repo", _cmd_repos_tsv)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub = _command(commands, "toolbox-env", "variables non secrètes (outillage)", _cmd_toolbox_env)
    sub.add_argument("run_dir", type=Path, help="dossier du run")


def _run_command(commands, name: str, help_text: str, handler) -> argparse.ArgumentParser:
    sub = _command(commands, name, help_text, handler)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    return sub


def _register_audit(commands) -> None:
    _run_command(commands, "coverage", "couverture des lignes selon les outils", _cmd_coverage)
    _run_command(commands, "lots", "répartit les lignes en lots (lots.json)", _cmd_lots)
    sub = _run_command(commands, "probes", "sondes HTTP de la boîte à outils", _cmd_probes)
    sub.add_argument(
        "--followup",
        action="store_true",
        help="regroupe les sondes demandées par les constats (probes-followup.json)",
    )
    sub = _run_command(commands, "validate", "valide les constats d'un lot", _cmd_validate)
    sub.add_argument("lot_id", help="identifiant du lot (lots.json)")
    sub.add_argument(
        "--followup", action="store_true", help="valide findings/<lot>-followup.json"
    )
    sub.add_argument(
        "--rerun",
        type=_rerun_number,
        metavar="NN",
        help="valide findings/<lot>-rerun-NN.json sur les seules Réf qu'il contient",
    )
    sub.add_argument(
        "--rows",
        default=None,
        help="avec --rerun : Réf relancées (CQ-3,CQ-12), toute autre Réf écrite est une erreur",
    )
    _run_command(
        commands, "snapshot", "copie findings/ et notion/ dans history/", _cmd_snapshot
    )
    _run_command(
        commands,
        "rerun-start",
        "ouvre une relance : écarte les relances non closes, sauvegarde, donne NN",
        _cmd_rerun_start,
    )
    sub = _run_command(
        commands, "rerun-close", "clôt une relance : écarte les fichiers invalides", _cmd_rerun_close
    )
    sub.add_argument("number", type=_rerun_number, help="numéro de la relance (NN)")
    sub = _run_command(commands, "plan", "plan d'écriture Notion (notion/plan.json)", _cmd_plan)
    sub.add_argument(
        "--skip-human-overrides",
        action="store_true",
        help="retire les pages dont le statut a été posé par un humain",
    )
    sub.add_argument(
        "--recette",
        type=_rerun_number,
        metavar="NN",
        nargs="?",
        const=0,
        default=None,
        help="plan d'une recette : NN est le numéro de la relance qui a jugé les lignes de "
        "notion/recette.json ; celles qui sont « Mis en place » et jugées peuvent changer "
        "ce statut posé par un dev",
    )
    _run_command(commands, "manifest", "résultats des outils (tools/manifest.json)", _cmd_manifest)
    _run_command(commands, "synthesis", "synthese.md et commentaire de la page", _cmd_synthesis)
    _run_command(commands, "scan", "contrôle anti-secret du dossier du run", _cmd_scan)
    _run_command(commands, "openapi-specs", "specs OpenAPI des repos", _cmd_openapi_specs)
    sub = _run_command(commands, "skip", "marque un outil comme ignoré", _cmd_skip)
    sub.add_argument("tool", help="nom de l'outil")
    sub.add_argument("reason", help="raison, affichée dans la synthèse")
    sub = _run_command(commands, "assign", "propose une répartition des lignes", _cmd_assign)
    sub.add_argument(
        "--include-unknown", action="store_true", help="ajoute les lignes non évaluées"
    )
    sub = _run_command(
        commands, "rerun-candidates", "lignes à réévaluer lors d'une relance ciblée", _cmd_rerun
    )
    sub.add_argument(
        "--refs", default="", help="Réf à ajouter, séparées par des virgules (CQ-12,CQ-40)"
    )
    _run_command(commands, "assign-plan", "plan d'assignation (assign-plan.json)", _cmd_assign_plan)
    sub = _run_command(
        commands, "defer", "met une décision de côté pour le lead (deferred.json)", _cmd_defer
    )
    sub.add_argument(
        "--kind", required=True, help="type de décision : " + ", ".join(deferred.KINDS)
    )
    sub.add_argument("--detail", required=True, help="texte pour le lead, en français")
    sub.add_argument("--refs", default="", help="Réf concernées, séparées par des virgules")
    sub = _run_command(
        commands, "deferred", "décisions mises de côté encore ouvertes", _cmd_deferred
    )
    sub.add_argument("--resolve", type=int, metavar="ID", help="ferme la décision ID")


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


def _cmd_key_pages(args, services) -> int:
    context = _load_context(args.run_dir)
    target = context.get("target")
    if not isinstance(target, dict):
        raise ContextError("context.json : « target » est absent.")
    home = _target_url(target, "prod_url" if probes.is_prod_run(context) else "preprod_url")
    if not urlsplit(home).path:
        home += "/"
    entries = _key_page_entries(context.get("key_pages"))
    delay = 1 / _request_rate()
    auth = _auth_from_env() if args.auth_env else None
    opener = services.opener
    if opener is None:
        opener = build_opener(authenticated=auth is not None)
    origins = {_origin(home)}
    checker = _PageChecker(opener, auth, delay, time.monotonic() + KEY_PAGES_DEADLINE)
    first = checker.get(home, with_body=True)
    origins.add(_origin(first["url"]))
    links = _home_links(first["url"], first["body"], _origin(first["url"]))
    templates = {home: None}
    for template, url in entries:
        if templates.get(url) is None:
            templates[url] = template
    pages = []
    for url, template in templates.items():
        if url == home:
            status, checked = first["status"], True
        elif any(_same_origin(url, origin) for origin in origins):
            result = checker.get(url)
            status, checked = result["status"], result["checked"]
        else:
            status, checked = None, False
        pages.append(_page_entry(url, template, status, checked, key=True))
    dead = []
    for url in links:
        if time.monotonic() >= checker.deadline:
            break
        if url in templates or len(checker.requests) >= MAX_KEY_PAGE_REQUESTS:
            continue
        result = checker.get(url)
        entry = _page_entry(url, None, result["status"], result["checked"], key=False)
        pages.append(entry)
        if entry["status"] == 404:
            dead.append(url)
    _emit({"pages": pages, "home_links_404": dead})
    return EXIT_OK


def _key_page_entries(key_pages) -> list:
    """Return (template, url) of each key page; a URL that fails validation is never echoed."""
    if not isinstance(key_pages, list):
        raise ContextError("context.json : « key_pages » est absent.")
    entries = []
    for index, page in enumerate(key_pages):
        template = page.get("template") if isinstance(page, dict) else None
        url = page.get("url") if isinstance(page, dict) else None
        label = template if isinstance(template, str) and template else f"n° {index + 1}"
        try:
            validate_url(url if isinstance(url, str) else "")
        except ValueError:
            raise ContextError(
                f"context.json : l'URL de la page clé « {label} » est invalide "
                "(http ou https, sans identifiants, sans espace)."
            )
        entries.append((template, url))
    return entries


def _page_entry(url: str, template, status, checked: bool, *, key: bool) -> dict:
    """One page of the report; "gap" is true for a checked page that should have answered."""
    if not checked:
        gap = False
    elif key:
        gap = not (isinstance(status, int) and status < 400)
        gap = gap and not (template == "404" and status == 404)
    else:
        gap = status == 404
    return {"url": url, "template": template, "status": status, "checked": checked, "gap": gap}


class _PageChecker:
    """GET pages one after the other at the toolbox rate, until the deadline."""

    def __init__(self, opener, auth, delay: float, deadline: float):
        self.opener, self.auth, self.delay, self.deadline = opener, auth, delay, deadline
        self.requests = []

    def get(self, url: str, *, with_body: bool = False) -> dict:
        if self.requests:
            if time.monotonic() >= self.deadline:
                return {"status": None, "url": url, "body": "", "checked": False}
            time.sleep(self.delay)
        self.requests.append(url)
        try:
            result = fetch_page(url, auth=self.auth, opener=self.opener, with_body=with_body)
        except ValueError:
            if len(self.requests) == 1:
                raise
            result = {"status": None, "url": url, "body": ""}
        return {**result, "checked": True}


def _request_rate() -> float:
    raw = os.environ.get("GM_AUDIT_RATE") or TOOLBOX_RATE
    try:
        rate = float(raw)
    except ValueError:
        rate = 0.0
    if not rate > 0 or rate == float("inf"):
        raise UsageError("GM_AUDIT_RATE doit être un nombre de requêtes par seconde supérieur à 0.")
    return rate


def _origin(url: str) -> tuple:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or (
        443 if parts.scheme.lower() == "https" else 80
    )


def _same_origin(url: str, origin: tuple) -> bool:
    try:
        return _origin(url) == origin
    except ValueError:
        return False


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href.strip())


def _home_links(base: str, body: str, origin: tuple) -> list:
    """Return the internal links of an HTML page: absolute, without fragment, once each."""
    parser = _LinkParser()
    parser.feed(body)
    links = []
    for href in parser.hrefs:
        try:
            url = urldefrag(urljoin(base, href))[0]
            if urlsplit(url).scheme in ("http", "https") and _same_origin(url, origin):
                validate_url(url)
            else:
                continue
        except ValueError:
            continue
        if url not in links and url != urldefrag(base)[0]:
            links.append(url)
    return links


_REUSED_SECTIONS = ("lead", "team", "ssh", "test_accounts", "key_pages", "sites")
_REUSED_TARGET_FIELDS = ("preprod_url", "prod_url")
_RECOMPUTED_REPO_FIELDS = ("audited_sha", "deployed_sha", "aligned")


def _cmd_reuse(args, services) -> int:
    if not args.new_run_dir.is_dir():
        raise UsageError(f"{args.new_run_dir} n'est pas un dossier de run.")
    old = _load_context(args.old_run_dir)
    path = args.new_run_dir / "context.json"
    new = _load_context(args.new_run_dir) if path.exists() else {}
    copied = []
    if _reuse_repos(old, new):
        copied.append("repos")
    if isinstance(old.get("notion"), dict):
        previous = new.get("notion") if isinstance(new.get("notion"), dict) else {}
        kept = {key: value for key, value in old["notion"].items() if key != "bot_id"}
        new["notion"] = {**kept, **({"bot_id": previous["bot_id"]} if "bot_id" in previous else {})}
        copied.append("notion")
    target = old.get("target")
    if isinstance(target, dict):
        previous = new.get("target") if isinstance(new.get("target"), dict) else {}
        new["target"] = {
            **{field: target[field] for field in _REUSED_TARGET_FIELDS if field in target},
            "prod_live": previous.get("prod_live", False),
            "access": previous.get("access"),
        }
        copied.append("target")
    for key in _REUSED_SECTIONS:
        if key in old:
            new[key] = old[key]
            copied.append(key)
    write_json(path, new)
    _emit({"copied": copied})
    return EXIT_OK


def _reuse_repos(old: dict, new: dict) -> bool:
    """Keep the repos of the new run (fresh from project) and take only their preprod branch."""
    old_repos = [repo for repo in old.get("repos") or [] if isinstance(repo, dict)]
    if not old_repos:
        return False
    if not isinstance(new.get("repos"), list):
        new["repos"] = [
            {**repo, **{field: None for field in _RECOMPUTED_REPO_FIELDS}} for repo in old_repos
        ]
        return True
    branches = {repo.get("name"): repo.get("preprod_branch") for repo in old_repos}
    for repo in new["repos"]:
        if isinstance(repo, dict) and branches.get(repo.get("name")):
            repo["preprod_branch"] = branches[repo["name"]]
    return True


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
    _emit_lines([f"{name}\t{path}" for name, path in _context_repos(args.run_dir)])
    return EXIT_OK


def _context_repos(run_dir: Path) -> list:
    """Return the (name, path) of each repo of context.json, checked for a tab-separated line."""
    repos = _load_context(run_dir).get("repos")
    if not isinstance(repos, list) or not repos:
        raise ContextError("context.json : « repos » est absent ou vide.")
    found = []
    for index, repo in enumerate(repos):
        label = f"repos[{index}]"
        if not isinstance(repo, dict):
            raise ContextError(f"context.json : « {label} » doit être un objet.")
        for key, check in (("name", _repo_name), ("path", _path)):
            problem = check(repo.get(key))
            if problem:
                raise ContextError(f"context.json : « {label}.{key} » {problem}.")
        found.append((repo["name"], repo["path"]))
    return found


def _prod_origin(url) -> Optional[str]:
    """Return scheme://host of a prod URL, or None when it is absent or unusable."""
    if not isinstance(url, str) or not url:
        return None
    try:
        validate_url(url)
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.username or parts.password:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _cmd_toolbox_env(args, services) -> int:
    context = _load_context(args.run_dir)
    target = context.get("target")
    if not isinstance(target, dict):
        raise ContextError("context.json : « target » est absent.")
    if probes.is_prod_run(context):
        url, robots = _target_url(target, "prod_url"), "respect"
    else:
        url, robots = _target_url(target, "preprod_url"), "ignore"
    lines = [
        f"GM_AUDIT_TARGET={url}",
        f"GM_AUDIT_ROBOTS={robots}",
        f"GM_AUDIT_RATE={TOOLBOX_RATE}",
        f"GM_AUDIT_USER_AGENT=GingermindsAudit/{plugin_version()}",
    ]
    if robots == "ignore":
        origin = _prod_origin(target.get("prod_url"))
        if origin:
            lines.append(f"GM_AUDIT_PROD_ORIGIN={origin}")
    _emit_lines(lines)
    return EXIT_OK


def _cmd_coverage(args, services) -> int:
    context, kept, excluded = _selection(args.run_dir)
    _emit(coverage(kept, excluded, context["available_tools"]))
    return EXIT_OK


def _cmd_lots(args, services) -> int:
    context, kept, excluded = _selection(args.run_dir)
    lots = build_lots(kept)
    counts = coverage(kept, excluded, context["available_tools"])
    write_json(args.run_dir / "lots.json", {"lots": lots, "excluded": excluded, "coverage": counts})
    _emit(
        {
            "lots": [
                {**{key: lot[key] for key in lot if key != "refs"}, "rows": len(lot["refs"])}
                for lot in lots
            ],
            "excluded": dict(Counter(entry["reason"] for entry in excluded)),
            "coverage": counts,
        }
    )
    return EXIT_OK


def _cmd_probes(args, services) -> int:
    run_dir = args.run_dir
    if args.followup:
        _checked_context(run_dir)
        result = probes.followup_probes(run_dir)
        document = {"base_url": result["base_url"], "probes": result["probes"]}
        write_json(run_dir / "probes-followup.json", document)
        _emit(
            {
                "file": "probes-followup.json",
                "base_url": document["base_url"],
                "count": len(document["probes"]),
                "lots": result["lots"],
                "discarded": result["discarded"],
            }
        )
        return EXIT_OK
    context, kept, _ = _selection(run_dir)
    result = probes.build_probes(
        base_url=probes.target_url(context),
        technos=context["technos"],
        rows=kept,
        fiches_dir=run_dir / "fiches",
        key_pages=context["key_pages"],
    )
    document = {"base_url": result["base_url"], "probes": result["probes"]}
    write_json(run_dir / "probes.json", document)
    from_fiches = sum(1 for probe in document["probes"] if probe["id"].startswith("fiche-"))
    _emit(
        {
            "file": "probes.json",
            "base_url": document["base_url"],
            "count": len(document["probes"]),
            "from_fiches": from_fiches,
            "discarded": result["discarded"],
        }
    )
    return EXIT_OK


def _rerun_number(text: str) -> int:
    if not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
        raise argparse.ArgumentTypeError(f"« {text} » n'est pas un numéro de relance (1, 2, ...).")
    return int(text)


def _new_history_dir(run_dir: Path, suffix: str = "") -> Path:
    """Create history/<stamp><suffix>, adding -2, -3... when the name is taken."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S") + suffix
    (run_dir / "history").mkdir(exist_ok=True)
    for index in range(1, 1000):
        target = run_dir / "history" / (stamp if index == 1 else f"{stamp}-{index}")
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return target
    raise UsageError("history/ : impossible de créer un dossier de sauvegarde.")


def _copy_state(run_dir: Path, target: Path) -> None:
    for name in ("findings", "notion"):
        if (run_dir / name).is_dir():
            shutil.copytree(run_dir / name, target / name)


def _require_findings(run_dir: Path) -> None:
    if not (run_dir / "findings").is_dir():
        raise UsageError("findings/ est introuvable : il n'y a rien à sauvegarder.")


def _cmd_snapshot(args, services) -> int:
    run_dir = args.run_dir.resolve()
    _require_findings(run_dir)
    target = _new_history_dir(run_dir)
    _copy_state(run_dir, target)
    print(target)
    return EXIT_OK


def _rerun_files(run_dir: Path, number: Optional[str] = None) -> list:
    """Return the findings/<lot>-rerun-NN.json files, sorted, of one rerun or of all."""
    folder = run_dir / "findings"
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        match = _RERUN_FILE.fullmatch(path.name)
        if match and (number is None or match.group(2) == number):
            found.append(path)
    return found


def _closed_reruns(run_dir: Path) -> set:
    document = _read_json_object(run_dir / "reruns.json")
    closed = document.get("closed")
    return {item for item in closed if isinstance(item, str)} if isinstance(closed, list) else set()


def _read_json_object(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _highest_rerun(run_dir: Path) -> int:
    numbers = [int(_RERUN_FILE.fullmatch(path.name).group(2)) for path in _rerun_files(run_dir)]
    numbers += [int(item) for item in _closed_reruns(run_dir) if item.isdigit()]
    history = run_dir / "history"
    if history.is_dir():
        for path in history.rglob("*-rerun-*.json"):
            match = _RERUN_FILE.fullmatch(path.name)
            if match:
                numbers.append(int(match.group(2)))
    return max(numbers, default=0)


def _cmd_defer(args, services) -> int:
    _emit(deferred.add(args.run_dir.resolve(), args.kind, args.detail, args.refs))
    return EXIT_OK


def _cmd_deferred(args, services) -> int:
    run_dir = args.run_dir.resolve()
    if args.resolve is not None:
        deferred.resolve(run_dir, args.resolve)
    _emit({"items": deferred.open_items(run_dir)})
    return EXIT_OK


def _cmd_rerun_start(args, services) -> int:
    run_dir = args.run_dir.resolve()
    _require_findings(run_dir)
    number = _highest_rerun(run_dir) + 1
    if number > 99:
        raise UsageError("Il y a déjà 99 relances dans ce run : lancer un nouveau run.")
    target = _new_history_dir(run_dir)
    closed = _closed_reruns(run_dir)
    abandoned = []
    for path in _rerun_files(run_dir):
        if _RERUN_FILE.fullmatch(path.name).group(2) in closed:
            continue
        (target / "abandoned").mkdir(exist_ok=True)
        shutil.move(str(path), str(target / "abandoned" / path.name))
        abandoned.append(path.name)
    _copy_state(run_dir, target)
    _emit({"snapshot": str(target), "rerun": f"{number:02d}", "abandoned": abandoned})
    return EXIT_OK


def _cmd_rerun_close(args, services) -> int:
    run_dir = args.run_dir.resolve()
    number = f"{args.number:02d}"
    files = _rerun_files(run_dir, number)
    if not files:
        raise UsageError(
            f"Aucun fichier findings/<lot>-rerun-{number}.json à clore : "
            "lancer d'abord rerun-start."
        )
    context = _checked_context(run_dir)
    lots = {item["id"]: item for item in _read_lots(run_dir)["lots"]}
    discarded = []
    for path in files:
        lot = lots.get(_RERUN_FILE.fullmatch(path.name).group(1))
        if lot is None or _rerun_errors(run_dir, context, lot, path.name, None):
            folder = _new_history_dir(run_dir, f"-rerun-{number}-invalid")
            shutil.move(str(path), str(folder / path.name))
            discarded.append(path.name)
    closed = sorted(_closed_reruns(run_dir) | {number})
    write_json(run_dir / "reruns.json", {"closed": closed})
    _emit({"closed": number, "discarded": discarded})
    return EXIT_OK


def _parse_rows(text: str) -> set:
    return set(parse_refs(text))


def _rerun_errors(run_dir, context, lot, name, rows) -> list:
    """Return the errors of a rerun file: only the rows it holds count, none is required."""
    found = _read_findings_file(run_dir, context, lot, name)
    if found is None:
        return [f"findings/{name} est introuvable : le sous-agent n'a pas écrit son fichier."]
    errors = [error for error in found[1] if not error.startswith(_MISSING_ROW)]
    if rows is not None:
        document = _read_json_object(run_dir / "findings" / name)
        items = document.get("findings")
        for item in items if isinstance(items, list) else []:
            ref = item.get("ref") if isinstance(item, dict) else None
            if isinstance(ref, int) and not isinstance(ref, bool) and ref not in rows:
                errors.append(
                    f"constat {ref_label(ref)} : Réf absente de la liste des lignes relancées"
                )
    if not found[0] and not errors:
        errors = [f"findings/{name} ne contient aucun constat."]
    return errors


def _cmd_validate(args, services) -> int:
    if args.rerun is not None and args.followup:
        raise UsageError("--rerun et --followup ne se combinent pas.")
    if args.rows is not None and args.rerun is None:
        raise UsageError("--rows ne s'utilise qu'avec --rerun.")
    context = _checked_context(args.run_dir)
    lots = _read_lots(args.run_dir)["lots"]
    lot = next((item for item in lots if item["id"] == args.lot_id), None)
    if lot is None:
        known = ", ".join(item["id"] for item in lots) or "aucun"
        raise UsageError(f"Le lot « {args.lot_id} » est absent de lots.json (lots : {known}).")
    if args.rerun is not None:
        name = f"{lot['id']}-rerun-{args.rerun:02d}.json"
        rows = _parse_rows(args.rows) if args.rows is not None else None
        errors = _rerun_errors(args.run_dir, context, lot, name, rows)
    else:
        name = _findings_name(lot["id"], args.followup)
        found = _read_findings_file(args.run_dir, context, lot, name)
        if found is None:
            errors = [f"findings/{name} est introuvable : le sous-agent n'a pas écrit son fichier."]
        else:
            errors = found[1]
    _emit({"ok": not errors, "errors": errors})
    return EXIT_CONTEXT if errors else EXIT_OK


def _cmd_plan(args, services) -> int:
    run_dir = args.run_dir.resolve()
    context = _plan_context(run_dir)
    rows, missing = _read_rows(run_dir)
    lots = _read_lots(run_dir)
    visible = _read_visible_verdicts(run_dir)
    findings, followup_done = _collect_findings(run_dir, context, lots["lots"])
    copy_ds_id = context["notion"]["copy_data_source_id"]
    history = load_history(run_dir.parent.parent, run_dir.parent.name, copy_ds_id, run_dir.name)
    recette = {"refs": frozenset(), "judged": [], "unjudged": []}
    if args.recette == 0:
        raise UsageError(
            "--recette demande le numéro de la relance qui a jugé les lignes (par exemple "
            "--recette 01) : celle que rerun-close vient de clore."
        )
    if args.recette is not None:
        _check_last_closed(run_dir, args.recette)
        recette = _recette_scope(run_dir, context, lots["lots"], rows, args.recette)
    plan = build_plan(
        rows=rows,
        findings=findings,
        excluded=lots["excluded"],
        context=context,
        visible_verdicts=visible,
        history=history,
        run_date=_run_start(context).date(),
        plugin_version=context["plugin_version"],
        followup_done=followup_done,
        missing_in_copy=missing,
        recette_refs=recette["refs"],
    )
    if args.recette is not None:
        plan["recette"] = {**recette, "rerun": f"{args.recette:02d}"}
        plan["recette"]["refs"] = sorted(recette["refs"])
    built = plan
    plan = filter_plan(plan, skip_human_overrides=args.skip_human_overrides)
    if args.recette is not None:
        plan["recette"]["kept"] = _kept_human_statuses(built, plan)
    plan["plan_id"] = plan_digest(plan)
    (run_dir / "notion").mkdir(exist_ok=True)
    write_json(run_dir / "notion" / "plan.json", plan)
    _emit(plan["summary"])
    return EXIT_OK


def _check_last_closed(run_dir: Path, number: int) -> None:
    closed = sorted(int(item) for item in _closed_reruns(run_dir) if item.isdigit())
    if not closed or closed[-1] != number:
        last = f"{closed[-1]:02d}" if closed else "aucune"
        raise UsageError(
            f"--recette {number:02d} : la recette doit porter sur la dernière relance close "
            f"({last} dans reruns.json). Clore la relance avec rerun-close d'abord."
        )


def _kept_human_statuses(built: dict, filtered: dict) -> list:
    """Return the judged rows of the recette whose human status the filter kept, with the verdict."""
    recette = built["recette"]
    judged = set(recette["judged"]) - set(recette["refs"])
    written = {op["key"] for op in filtered["ops"]}
    return [
        {"ref": op["ref"], "verdict": op["verdict"], "status": op["before"]}
        for op in built["ops"]
        if op["kind"] == "status"
        and op["human_override"]
        and op["ref"] in judged
        and op["key"] not in written
    ]


def _recette_scope(run_dir: Path, context: dict, lots: list, rows: list, number: int) -> dict:
    """Return the refs of the recette by what the plan may do with them.

    refs: "Mis en place" in the copy and judged again by the rerun NN (a finding in a kept
    findings/<lot>-rerun-NN.json): they may replace the status a dev set. judged: every ref
    of the recette with such a finding. unjudged: the others, left as they are.
    """
    document = _read_run_json(run_dir, "notion/recette.json", "cqa_notion.py recette")
    listed = document.get("rows") if isinstance(document, dict) else None
    if not isinstance(listed, list):
        raise UsageError("notion/recette.json : la liste « rows » est absente.")
    wanted = {row["ref"] for row in listed if isinstance(row, dict) and _is_ref(row.get("ref"))}
    judged = set()
    for lot in lots:
        found = _read_findings_file(run_dir, context, lot, f"{lot['id']}-rerun-{number:02d}.json")
        judged.update(item["ref"] for item in (found[0] if found else []))
    status = {row["ref"]: row.get("status") for row in rows if row.get("ref") is not None}
    exempt = {ref for ref in wanted & judged if status.get(ref) == STATUS_IMPLEMENTED}
    return {
        "refs": frozenset(exempt),
        "judged": sorted(wanted & judged),
        "unjudged": sorted(wanted - judged),
    }


def _cmd_rerun(args, services) -> int:
    refs = parse_refs(args.refs)
    run_dir = args.run_dir.resolve()
    context = _checked_context(run_dir)
    _emit(
        rerun_candidates(
            run_dir,
            extra_refs=refs,
            git_run=services.run,
            read=lambda lot, name: _read_findings_file(run_dir, context, lot, name),
        )
    )
    return EXIT_OK


def _cmd_manifest(args, services) -> int:
    document = report.manifest(args.run_dir)
    (args.run_dir / "tools").mkdir(exist_ok=True)
    write_json(args.run_dir / "tools" / "manifest.json", document)
    _emit(document)
    return EXIT_OK


def _cmd_synthesis(args, services) -> int:
    run_dir = args.run_dir
    synthese, comment = report.synthesis(run_dir)
    (run_dir / "notion").mkdir(exist_ok=True)
    write_text(run_dir / "synthese.md", synthese)
    write_text(run_dir / "notion" / "page-comment.txt", comment)
    _emit(
        {
            "synthese": "synthese.md",
            "page_comment": "notion/page-comment.txt",
            "page_comment_length": len(comment),
        }
    )
    return EXIT_OK


def _cmd_scan(args, services) -> int:
    found = [
        {"path": path, "label": label}
        for path, label in scan_tree(args.run_dir, known_values_from_env(), exclude=("fiches",))
    ]
    _emit({"ok": not found, "found": found})
    return EXIT_SECRET if found else EXIT_OK


def _cmd_openapi_specs(args, services) -> int:
    lines = []
    for name, path in _context_repos(args.run_dir):
        root = Path(path)
        if not root.is_dir():
            print(f"Le repo « {name} » est introuvable : {path}", file=sys.stderr)
            continue
        lines.extend(f"{name}\t{relative}" for relative in _openapi_files(root))
    _emit_lines(lines)
    return EXIT_OK


def _cmd_skip(args, services) -> int:
    if not _TOOL_NAME.fullmatch(args.tool):
        raise UsageError(
            f"« {args.tool} » n'est pas un nom d'outil "
            "(minuscules, chiffres et tirets, 40 caractères au plus)."
        )
    reason = " ".join(args.reason.split())
    if not reason:
        raise UsageError("La raison est vide : elle sert à expliquer le saut dans la synthèse.")
    if not args.run_dir.is_dir():
        raise UsageError(f"Le dossier du run {args.run_dir} est introuvable.")
    result = {
        "tool": args.tool,
        "toolbox_version": None,
        "tool_version": None,
        "status": "skipped",
        "reason": reason,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": 0,
        "targets": [],
        "files": [],
        "counts": {},
    }
    folder = args.run_dir / "tools" / args.tool
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "result.json", result)
    _emit(result)
    return EXIT_OK


def _cmd_assign(args, services) -> int:
    run_dir = args.run_dir
    context = _checked_context(run_dir)
    lead = _lead_option(context)
    rows, _ = _read_rows(run_dir)
    verdicts = _verdicts(run_dir, context, rows) if args.include_unknown else {}
    annotated = [{**row, "verdict": verdicts.get(row.get("ref"))} for row in rows]
    team = [member for member in context["team"] if member.get("option")]
    without_option = [member["name"] for member in context["team"] if not member.get("option")]
    document = {
        "lead": lead,
        "include_unknown": args.include_unknown,
        "proposal": propose_assignment(annotated, team, lead, include_unknown=args.include_unknown),
        "sans_option": list(dict.fromkeys(without_option)),
    }
    (run_dir / "notion").mkdir(exist_ok=True)
    write_json(run_dir / "notion" / "assign-proposal.json", document)
    _emit(document)
    return EXIT_OK


def _cmd_assign_plan(args, services) -> int:
    run_dir = args.run_dir
    context = _checked_context(run_dir)
    notion = context["notion"]
    document = _read_run_json(run_dir, "notion/assign-proposal.json", "cqa_plan.py assign")
    entries = document.get("proposal") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise UsageError("notion/assign-proposal.json : la liste « proposal » est absente.")
    rows, _ = _read_rows(run_dir)
    ops, unchanged = _assign_operations(entries, rows, notion["assignee_options"])
    comment = None
    page_id = notion["copy_page_id"]
    if ops and isinstance(page_id, str) and page_id.strip():
        comment = _assignment_comment(ops, context, _lead_option(context))
        ops.append(_page_comment_operation(page_id, comment))
    plan = {
        "run_id": context["run_id"],
        "copy_data_source_id": notion["copy_data_source_id"],
        "copy_page_id": notion["copy_page_id"],
        "assignee_options": list(notion["assignee_options"]),
        "ops": ops,
    }
    (run_dir / "notion").mkdir(exist_ok=True)
    write_json(run_dir / "notion" / "assign-plan.json", plan)
    _emit(
        {
            "file": "notion/assign-plan.json",
            "assigned": sum(1 for op in ops if op["kind"] == "assign"),
            "unchanged": unchanged,
            "page_comment": comment is not None,
        }
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


_WORDPRESS_PACKAGES = ("roots/wordpress", "johnpbloch/wordpress", "wpackagist-plugin/", "wpackagist-theme/")


def _detect_stack(repo: Path) -> tuple:
    """Return (stack, Notion techno) read from composer.json, package.json and wp-content/."""
    composer = _dependencies(repo / "composer.json", ("require", "require-dev"))
    if any(name.startswith("drupal/core") for name in composer):
        return "drupal", "Drupal"
    wordpress = any(name.startswith(_WORDPRESS_PACKAGES) for name in composer)
    if wordpress or (repo / "wp-content").is_dir():
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
    _check_fields(
        errors, "", context, {key: c for key, c in _OPTIONAL_TOP_FIELDS.items() if key in context}
    )
    for name, fields in _SECTIONS.items():
        _check_object(errors, name, context, fields)
    for name, fields in _OPTIONAL_SECTION_FIELDS.items():
        section = context.get(name)
        if isinstance(section, dict):
            present = {key: check for key, check in fields.items() if key in section}
            _check_fields(errors, name, section, present)
    _check_list(errors, "team", context, _TEAM_FIELDS)
    _check_list(errors, "repos", context, _REPO_FIELDS, minimum=1)
    _check_list(errors, "key_pages", context, _PAGE_FIELDS, maximum=MAX_KEY_PAGES)
    _check_repo_names(errors, context.get("repos"))
    _check_app_dirs(errors, context.get("ssh"))
    if "sites" in context:
        _check_list(errors, "sites", context, _SITE_FIELDS, maximum=MAX_SITES)
        _check_sites(errors, context["sites"])
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


_APP_DIR_KEYS = ("host", "container")


def _check_app_dirs(errors: list, ssh) -> None:
    """Check each folder of ssh.app_dirs: a host path, or {"host": ..., "container": ...}."""
    app_dirs = ssh.get("app_dirs") if isinstance(ssh, dict) else None
    if not isinstance(app_dirs, dict):
        return
    for name, value in app_dirs.items():
        label = f"ssh.app_dirs.{name}"
        if isinstance(value, str):
            problem = _absolute_path(value)
            if problem:
                errors.append(_invalid(label, problem))
        elif isinstance(value, dict):
            unknown = sorted(str(key) for key in value if key not in _APP_DIR_KEYS)
            if unknown:
                errors.append(_invalid(label, (
                    "contient une clé inconnue : "
                    + ", ".join(f"« {key} »" for key in unknown)
                    + " (seules « host » et « container » sont permises)"
                )))
            _check_fields(errors, label, value, {key: _absolute_path for key in _APP_DIR_KEYS})
        else:
            errors.append(_invalid(label, (
                'doit être un chemin absolu sur le serveur, ou un objet {"host": "<chemin>", '
                '"container": "<chemin>"} pour une application en conteneur'
            )))


def _absolute_path(value) -> Optional[str]:
    if isinstance(value, str) and value.startswith("/") and _path(value) is None:
        return None
    return "doit être un chemin absolu (commençant par « / »), sans tabulation ni retour à la ligne"


def _check_sites(errors: list, sites) -> None:
    if not isinstance(sites, list):
        return
    domains = []
    for index, site in enumerate(sites):
        if not isinstance(site, dict):
            continue
        domain, home = site.get("domain"), site.get("home")
        if _hostname(domain) is not None:
            continue
        domains.append(domain.lower())
        if _url(home) is None and urlsplit(home).hostname != domain.lower():
            errors.append(_invalid(f"sites[{index}].home", f"doit être une URL du domaine {domain}"))
    for domain in sorted({name for name in domains if domains.count(name) > 1}):
        errors.append(_invalid("sites", f"contient deux fois le domaine « {domain} »"))


def _absent(path: str) -> str:
    return f"context.json : « {path} » est absent."


def _invalid(path: str, problem: str) -> str:
    return f"context.json : « {path} » {problem}."


def _text(value) -> Optional[str]:
    ok = isinstance(value, str) and value.strip()
    return None if ok else "doit être un texte non vide"


def _text_or_null(value) -> Optional[str]:
    return None if value is None or isinstance(value, str) else "doit être un texte ou null"


def _filled_text_or_null(value) -> Optional[str]:
    if value is None or _text(value) is None:
        return None
    return "doit être un texte non vide ou null"


def _choice(*allowed):
    def check(value) -> Optional[str]:
        if isinstance(value, str) and value in allowed:
            return None
        return "doit valoir " + ", ".join(f"« {item} »" for item in allowed)

    return check


def _choice_or_null(*allowed):
    inner = _choice(*allowed)

    def check(value) -> Optional[str]:
        return None if value is None else inner(value)

    return check


def _boolean(value) -> Optional[str]:
    return None if isinstance(value, bool) else "doit être un booléen"


def _boolean_or_null(value) -> Optional[str]:
    return None if value is None or isinstance(value, bool) else "doit être un booléen ou null"


def _hostname(value) -> Optional[str]:
    if isinstance(value, str) and _HOSTNAME.fullmatch(value):
        return None
    return "doit être un nom d'hôte, sans schéma ni chemin"


def _url(value) -> Optional[str]:
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        if parts and parts.scheme in ("http", "https") and parts.hostname:
            return None
    except ValueError:
        pass
    return "doit être une URL http ou https"


def _key_page_url(value) -> Optional[str]:
    if not isinstance(value, str):
        return "doit être un texte"
    try:
        validate_url(value)
    except ValueError as error:
        return str(error)[0].lower() + str(error)[1:].rstrip(".")
    return None


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
_OPTIONAL_TOP_FIELDS = {
    "mode": _choice("autonome", "interactif"),
    "notion_write": _choice_or_null("all", "keep_human", "none"),
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
_OPTIONAL_SECTION_FIELDS = {
    "notion": {"reference_page_id": _filled_text_or_null},
    "ssh": {"sql_skip_ssl": _boolean},
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
_PAGE_FIELDS = {"template": _text, "url": _key_page_url}
_SITE_FIELDS = {
    "domain": _hostname,
    "backoffice": _choice("commun", "propre"),
    "sso": _boolean,
    "home": _url,
}
_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*")


def _read_run_json(run_dir: Path, relative: str, hint: str):
    try:
        return json.loads((run_dir / relative).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{relative} est introuvable dans {run_dir} : lancer d'abord {hint}.")
    except (OSError, ValueError) as error:
        raise UsageError(f"{relative} est illisible : {error}")


def _checked_context(run_dir: Path) -> dict:
    context = _load_context(run_dir)
    errors = validate_context(context)
    if errors:
        raise ContextError("\n".join(errors))
    return context


def _lead_option(context: dict) -> str:
    option = context["lead"]["option"]
    if not isinstance(option, str) or not option.strip():
        raise ContextError(
            "context.json : « lead.option » est vide : "
            "le lead n'a pas d'option dans « Assignée à »."
        )
    return option


def _plan_context(run_dir: Path) -> dict:
    """Return the checked context, with what the comments of the plan quote."""
    context = _checked_context(run_dir)
    _lead_option(context)
    _run_start(context)
    for repo in context["repos"]:
        sha = repo["audited_sha"]
        if not isinstance(sha, str) or not sha.strip():
            raise ContextError(
                f"context.json : « audited_sha » du repo « {repo['name']} » est vide : "
                "les commentaires citent le commit audité."
            )
    return context


def _run_start(context: dict) -> datetime:
    try:
        return report.run_start(context["run_id"])
    except ValueError as error:
        raise ContextError(str(error)) from None


def _is_ref(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _row_label(row: dict) -> str:
    return ref_label(row["ref"]) if _is_ref(row.get("ref")) else row["page_id"]


def _read_rows(run_dir: Path) -> tuple:
    """Return (rows, missing_in_copy) of rows.json."""
    document = _read_run_json(run_dir, "rows.json", "cqa_notion.py rows")
    rows = document.get("rows") if isinstance(document, dict) else None
    sound = isinstance(rows, list) and all(
        isinstance(row, dict) and isinstance(row.get("page_id"), str) for row in rows
    )
    if not sound:
        raise UsageError("rows.json : la liste « rows » est absente ou invalide.")
    missing = document.get("missing_in_copy", [])
    if not isinstance(missing, list):
        raise UsageError("rows.json : « missing_in_copy » doit être une liste.")
    return rows, missing


def _read_lots(run_dir: Path) -> dict:
    document = _read_run_json(run_dir, "lots.json", "cqa_plan.py lots")
    lots = document.get("lots") if isinstance(document, dict) else None
    excluded = document.get("excluded") if isinstance(document, dict) else None
    sound = (
        isinstance(lots, list)
        and all(
            isinstance(lot, dict)
            and isinstance(lot.get("id"), str)
            and isinstance(lot.get("refs"), list)
            for lot in lots
        )
        and isinstance(excluded, list)
        and all(
            isinstance(entry, dict) and all(key in entry for key in _EXCLUDED_KEYS)
            for entry in excluded
        )
    )
    if not sound:
        raise UsageError("lots.json : « lots » ou « excluded » est absent ou invalide.")
    return document


def _selection(run_dir: Path) -> tuple:
    """Return (context, kept rows, excluded entries) for the rows of the run."""
    context = _checked_context(run_dir)
    rows, _ = _read_rows(run_dir)
    kept, excluded = select_rows(
        rows, context["technos"], context["available_tools"], context["phase_filter"]
    )
    for row in kept:
        if not _is_ref(row.get("ref")):
            raise UsageError(
                f"rows.json : la ligne « {row.get('title')} » n'a pas de ref, "
                "relancer cqa_notion.py rows."
            )
    return context, kept, excluded


def _read_visible_verdicts(run_dir: Path) -> dict:
    """Return comments.json as {ref: [verdict]}, its keys being text in the file."""
    document = _read_run_json(run_dir, "comments.json", "cqa_notion.py comments")
    if not isinstance(document, dict):
        raise UsageError("comments.json : un objet {Réf: [verdicts]} est attendu.")
    verdicts = {}
    for key, found in document.items():
        if not _REF_KEY.fullmatch(key) or not isinstance(found, list):
            raise UsageError(
                "comments.json : chaque clé doit être une Réf numérique et valoir une liste."
            )
        verdicts[int(key)] = [item for item in found if isinstance(item, str)]
    return verdicts


def _findings_name(lot_id: str, followup: bool) -> str:
    return f"{lot_id}-followup.json" if followup else f"{lot_id}.json"


def _read_findings(run_dir: Path, context: dict, lot: dict, *, followup: bool) -> Optional[tuple]:
    """Return (valid findings, errors) of a lot file, None when the file does not exist."""
    return _read_findings_file(run_dir, context, lot, _findings_name(lot["id"], followup))


def _read_findings_file(run_dir: Path, context: dict, lot: dict, name: str) -> Optional[tuple]:
    """Return (valid findings, errors) of a findings file, None when it does not exist.

    The valid findings are those of validate_document whose evidence also holds against the run.
    """
    try:
        document = json.loads((run_dir / "findings" / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return [], [f"findings/{name} est illisible : {error}"]
    valid, errors = validate_document(
        document, set(lot["refs"]), app_path=probes.app_path(context)
    )
    roots = {repo["name"]: Path(repo["path"]) for repo in context["repos"]}
    grounded = []
    for item in valid:
        problems = _evidence_problems(item["evidence"], roots, run_dir)
        errors.extend(f"constat {ref_label(item['ref'])} : {problem}" for problem in problems)
        if not problems:
            grounded.append(item)
    return grounded, errors


def _collect_findings(run_dir: Path, context: dict, lots: list) -> tuple:
    """Return ({ref: finding}, refs of the lots that went through the follow-up of the probes).

    The latest finding of a row prevails: first round, follow-up, then the reruns in order (see
    latest_findings); a ref a later file does not give (or gives invalid) keeps its previous
    finding. A lot went through the follow-up when it has a follow-up file, or when
    probes-followup.json exists and the lot still asked for probes: if every URL was discarded
    no relaunch has any use, but what the lot waited for will not come either.
    """
    findings = latest_findings(
        run_dir, lots, lambda lot, name: _read_findings_file(run_dir, context, lot, name)
    )
    followup_done = set()
    requested = set()
    if (run_dir / "probes-followup.json").is_file():
        requested = set(probes.pending_lots(run_dir, lots, probes.app_path(context)))
    for lot in lots:
        if (run_dir / "findings" / _findings_name(lot["id"], True)).exists() or lot[
            "id"
        ] in requested:
            followup_done.update(lot["refs"])
    return findings, followup_done


def _verdicts(run_dir: Path, context: dict, rows: list) -> dict:
    """Return {ref: verdict} of the audit: the exclusions, then the findings after guardrails."""
    if not (run_dir / "lots.json").is_file():
        raise UsageError(
            "lots.json est introuvable : les lignes non évaluées se déduisent des verdicts "
            "de l'audit, lancer d'abord cqa_plan.py lots."
        )
    lots = _read_lots(run_dir)
    findings, followup_done = _collect_findings(run_dir, context, lots["lots"])
    by_ref = {row["ref"]: row for row in rows if _is_ref(row.get("ref"))}
    verdicts = {
        entry["ref"]: entry["verdict"] for entry in lots["excluded"] if _is_ref(entry["ref"])
    }
    for lot in lots["lots"]:
        for ref in lot["refs"]:
            finding = findings.get(ref)
            if finding is None:
                verdicts[ref] = "unknown"
            else:
                row = by_ref.get(ref, {})
                verdicts[ref] = apply_guardrails(finding, row, followup_done=ref in followup_done)[
                    "verdict"
                ]
    return verdicts


def _evidence_problems(evidence: list, roots: dict, run_dir: Path) -> list:
    """Return what the evidence of a valid finding claims that the run does not hold."""
    problems = []
    for number, item in enumerate(evidence, start=1):
        kind = item["type"]
        if kind == "file":
            problem = _file_problem(item, roots)
        elif kind == "tool":
            problem = _tool_problem(item, run_dir)
        else:
            problem = None
        if problem:
            problems.append(f"preuve n° {number} ({kind}) : {problem}")
    return problems


def _file_problem(item: dict, roots: dict) -> Optional[str]:
    repo, path = item["repo"], item["path"]
    if repo not in roots:
        return f"repo {_quoted(repo)} absent de context.json (repos : {', '.join(roots)})"
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return f"chemin {_quoted(path)} hors du repo {_quoted(repo)}"
    if relative.parts == ():
        return f"chemin {_quoted(path)} : la racine du repo {_quoted(repo)} n'est pas une preuve"
    candidate = roots[repo] / relative
    if not candidate.exists():
        return f"chemin {_quoted(path)} introuvable dans le repo {_quoted(repo)}"
    if not candidate.resolve().is_relative_to(roots[repo].resolve()):
        return f"chemin {_quoted(path)} hors du repo {_quoted(repo)}"
    if not candidate.is_file():
        return f"chemin {_quoted(path)} : pas un fichier dans le repo {_quoted(repo)} (dossier ?)"
    return None


def _tool_problem(item: dict, run_dir: Path) -> Optional[str]:
    tool = item["tool"]
    if not _EVIDENCE_TOOL.fullmatch(tool):
        return f"nom d'outil {_quoted(tool)} invalide"
    if not (run_dir / "tools" / tool / "summary.json").is_file():
        return f"outil {_quoted(tool)} sans résumé (tools/{tool}/summary.json est introuvable)"
    return None


def _quoted(value) -> str:
    text = str(value)
    return f"« {text if len(text) <= 60 else text[:59] + '…'} »"


def _openapi_files(root: Path) -> list:
    """Return the relative paths of the OpenAPI and Swagger specs of a repo, sorted."""
    found = []
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in _SPEC_SKIPPED_DIRS]
        for name in names:
            path = Path(directory, name)
            if not _SPEC_NAME.fullmatch(name.lower()) or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if not any(char in relative for char in "\t\r\n"):
                found.append(relative)
    return sorted(found)


def _assign_operations(entries: list, rows: list, options: list) -> tuple:
    """Return (assign operations, number of rows already holding their assignee).

    An entry without assignee is left out. An entry that names a page outside rows.json, a row
    twice, or an assignee that is not an existing option is a UsageError: nothing is written.
    """
    by_page = {row["page_id"]: row for row in rows}
    ops, seen, unchanged = [], set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("page_id"), str):
            raise UsageError(
                "notion/assign-proposal.json : une entrée de « proposal » est invalide."
            )
        row = by_page.get(entry["page_id"])
        if row is None:
            raise UsageError(
                f"notion/assign-proposal.json : la page « {entry['page_id']} » "
                "n'est pas une ligne de rows.json."
            )
        if entry["page_id"] in seen:
            raise UsageError(
                f"notion/assign-proposal.json : la ligne {_row_label(row)} apparaît deux fois."
            )
        seen.add(entry["page_id"])
        assignee = entry.get("assignee")
        if assignee in (None, ""):
            continue
        if assignee not in options:
            raise UsageError(
                f"« {assignee} » n'est pas une option de « Assignée à » : "
                "aucune option n'est créée, choisir une option existante."
            )
        before = list(row.get("assignees") or [])
        if assignee in before:
            unchanged += 1
        else:
            ops.append(_assign_operation(row, before, before + [assignee]))
    return ops, unchanged


def _assign_operation(row: dict, before: list, after: list) -> dict:
    return {
        "key": operation_key("assign", row["page_id"], after, ""),
        "kind": "assign",
        "page_id": row["page_id"],
        "ref": row.get("ref"),
        "verdict": None,
        "before": before,
        "after": after,
        "text": "",
        "human_override": False,
    }


def _page_comment_operation(page_id: str, text: str) -> dict:
    return {
        "key": operation_key("page_comment", page_id, None, text),
        "kind": "page_comment",
        "page_id": page_id,
        "ref": None,
        "verdict": None,
        "before": None,
        "after": None,
        "text": text,
        "human_override": False,
    }


def _assignment_comment(ops: list, context: dict, lead: str) -> str:
    """Return the comment for the copy page: how many rows each person was given."""
    counts = Counter(op["after"][-1] for op in ops)
    order = [name for name in context["notion"]["assignee_options"] if name in counts]
    order += [name for name in counts if name not in order]
    total = sum(counts.values())
    plural = "s" if total > 1 else ""
    start = _run_start(context)
    lines = [
        f"Répartition PRE-MEL · {start:%d/%m/%Y} · lancée par {lead}",
        f"{total} ligne{plural} assignée{plural} :",
        *(f"- {name} : {counts[name]}" for name in order),
    ]
    text = "\n".join(lines).replace(_EM_DASH, "-")
    return text if len(text) <= _MAX_COMMENT else text[: _MAX_COMMENT - 1].rstrip() + "…"


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _emit_lines(lines: list) -> None:
    print("".join(f"{line}\n" for line in lines), end="")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
