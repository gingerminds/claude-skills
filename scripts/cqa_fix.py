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

from cqa import fix_gitlab, fix_names
from cqa.notion_read import write_json

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


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
