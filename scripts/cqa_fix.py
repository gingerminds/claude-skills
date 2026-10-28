#!/usr/bin/env python3
"""Fix mode of the checklist audit: GitLab diagnostic, then the commands built on it.

Results go to stdout (JSON), errors to stderr.

Exit codes: 0 success, 2 environment or usage, 5 refused by a guard, 6 invalid context,
report, review or state, 7 secret found.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

from cqa import fix_gitlab, fix_names, fix_rows
from cqa.checklist import parse_refs
from cqa.notion_read import write_json
from cqa.rerun import latest_findings, read_grounded_findings

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 5
EXIT_CONTEXT = 6
EXIT_SECRET = 7


class UsageError(Exception):
    """A problem with the environment or the arguments, worded for the lead."""


class ContextError(Exception):
    """A context.json that is missing, unreadable or unfit for the command."""


class Services(NamedTuple):
    """What the commands need from the outside, replaceable in tests."""

    run: Callable


def main(argv, *, run=subprocess.run) -> int:
    """Run one command and return its exit code. run executes git and glab."""
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    try:
        return args.handler(args, Services(run))
    except ContextError as error:
        print(error, file=sys.stderr)
        return EXIT_CONTEXT
    except (UsageError, ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cqa_fix.py", description="Mode correction de la checklist qualité."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commande")
    sub = _command(commands, "doctor", "accès GitLab et état des repos", _cmd_doctor)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub = _command(commands, "candidates", "lignes que le mode correction peut traiter", _cmd_candidates)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--refs", default="", help="Réf à ajouter, séparées par des virgules (CQ-12)")
    sub.add_argument("--without", default="", help="Réf à retirer, séparées par des virgules")
    return parser


def _command(commands, name: str, help_text: str, handler) -> argparse.ArgumentParser:
    sub = commands.add_parser(name, help=help_text)
    sub.set_defaults(handler=handler)
    return sub


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


def _cmd_doctor(args, services: Services) -> int:
    context = _load_context(args.run_dir)
    repos = [repo for repo in context.get("repos") or [] if isinstance(repo, dict)]
    if not repos:
        raise ContextError("context.json : aucun repo à diagnostiquer.")
    for repo in repos:
        if not isinstance(repo.get("path"), str) or not repo["path"]:
            raise ContextError(
                f"context.json : le repo « {repo.get('name')} » n'a pas de chemin (« path »)."
            )
    report = {"repos": [_diagnose(repo, services.run) for repo in repos]}
    (args.run_dir / "fix").mkdir(exist_ok=True)
    write_json(args.run_dir / "fix" / "doctor.json", report)
    _emit(report)
    return EXIT_OK


def _diagnose(repo: dict, run) -> dict:
    path = repo.get("path")
    entry = fix_gitlab.repo_access(run, repo)
    fetched = fix_names.git(path, run, "fetch", "origin", "--quiet").returncode == 0
    entry["identity"] = all(
        fix_names.git(path, run, "config", key).stdout.strip() for key in ("user.name", "user.email")
    )
    entry["fetched"] = fetched
    entry["ahead"] = _ahead(path, run, repo.get("audited_sha"), entry["base"] or "develop")
    return entry


def _ahead(path, run, sha, base: str):
    if not sha:
        return None
    if fix_names.git(path, run, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return None
    count = fix_names.git(path, run, "rev-list", "--count", f"{sha}..origin/{base}")
    if count.returncode != 0:
        return None
    try:
        return int(count.stdout.strip())
    except ValueError:
        return None


def _read_run_json(run_dir: Path, name: str, hint: str) -> dict:
    try:
        document = json.loads((run_dir / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{name} est introuvable dans {run_dir} : lancer d'abord {hint}.")
    except (OSError, ValueError) as error:
        raise UsageError(f"{name} est illisible : {error}")
    if not isinstance(document, dict):
        raise UsageError(f"{name} : un objet JSON est attendu.")
    return document


def _cmd_candidates(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    refs, without = parse_refs(args.refs), parse_refs(args.without)
    if not (run_dir / "fix" / "doctor.json").is_file():
        raise UsageError("Lancez d'abord cqa_fix.py doctor.")
    doctor = _read_run_json(run_dir, "fix/doctor.json", "cqa_fix.py doctor")
    context = _load_context(run_dir)
    repos = [repo for repo in context.get("repos") or [] if isinstance(repo, dict)]
    rows = _read_run_json(run_dir, "rows.json", "cqa_notion.py rows").get("rows")
    lots = _read_run_json(run_dir, "lots.json", "cqa_plan.py lots")
    if not isinstance(rows, list) or not isinstance(lots.get("lots"), list):
        raise UsageError("rows.json ou lots.json : listes « rows » et « lots » attendues.")
    excluded = [entry for entry in lots.get("excluded") or [] if isinstance(entry, dict)]
    findings = latest_findings(
        run_dir, lots["lots"], lambda lot, name: read_grounded_findings(run_dir, context, lot, name)
    )
    access = [entry for entry in doctor.get("repos") or [] if isinstance(entry, dict)]
    flights = _flights(repos, access, services.run)
    try:
        result = fix_rows.select(
            rows, findings, excluded, repos, access, flights,
            refs=refs, without=without, run_dir=run_dir,
        )
    except ValueError as error:
        raise UsageError(str(error))
    write_json(run_dir / "fix" / "candidates.json", result)
    _emit(result)
    return EXIT_OK


def _flights(repos: list, access: list, run) -> dict:
    by_name = {entry.get("repo"): entry for entry in access}
    flights = {}
    for repo in repos:
        entry = by_name.get(repo.get("name"))
        if not entry or entry.get("status") == "not_gitlab":
            continue
        flights[repo["name"]] = fix_gitlab.in_flight(
            run, repo, entry["host"], entry["project"], entry.get("base") or "develop",
            api=entry["status"] in ("ok", "no_push"),
        )
    return flights


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
