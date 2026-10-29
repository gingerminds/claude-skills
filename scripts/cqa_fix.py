#!/usr/bin/env python3
"""Fix mode of the checklist audit: GitLab diagnostic, then the commands built on it.

Results go to stdout (JSON), errors to stderr.

Exit codes: 0 success, 2 environment or usage, 5 refused by a guard, 6 invalid context,
report, review or state, 7 secret found.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

from cqa import fix_git, fix_gitlab, fix_names, fix_rows, fix_state
from cqa.checklist import parse_refs
from cqa.notion_read import write_json
from cqa.rerun import latest_findings, read_grounded_findings

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 5
EXIT_CONTEXT = 6
EXIT_SECRET = 7

UNFINISHED = "Une passe fix est inachevée : reprenez-la ou abandonnez-la (cqa_fix.py abandon)."
NO_PASS = "Aucune passe fix en cours : lancez d'abord cqa_fix.py open."


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
    except (ContextError, fix_state.StateError) as error:
        print(error, file=sys.stderr)
        return EXIT_CONTEXT
    except (UsageError, fix_git.GitRefused, ValueError, OSError) as error:
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
    sub = _command(commands, "open", "ouvre une passe de correction", _cmd_open)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--mode", required=True, choices=fix_state.MODES)
    sub.add_argument("--push", required=True, choices=fix_state.PUSHES)
    sub.add_argument("--notion", required=True, choices=("yes", "no"))
    sub = _command(commands, "stage", "fait avancer l'étape de la passe", _cmd_stage)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("stage", choices=fix_state.STAGES[1:], help="étape atteinte")
    sub = _command(commands, "start", "branche et worktree d'une clé", _cmd_start)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help="clé <repo>/<slug> de fix/candidates.json")
    sub = _command(commands, "abandon", "abandonne la passe en cours", _cmd_abandon)
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


def _cmd_open(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    old = _read_state(run_dir, required=False)
    if old is not None and not old.get("finished_at"):
        raise UsageError(UNFINISHED)
    if not (run_dir / "fix" / "candidates.json").is_file():
        raise UsageError("Lancez d'abord cqa_fix.py candidates.")
    candidates = _read_run_json(run_dir, "fix/candidates.json", "cqa_fix.py candidates")
    try:
        state = fix_state.new_state(
            candidates, mode=args.mode, push=args.push, notion=args.notion == "yes"
        )
    except (KeyError, TypeError, AttributeError):
        raise UsageError("fix/candidates.json : clés invalides, relancez cqa_fix.py candidates.")
    removed, archived = _archive(run_dir, old, services.run) if old is not None else ([], None)
    fix_state.save(run_dir, state)
    _emit({
        "started_at": state["started_at"], "stage": state["stage"], "mode": state["mode"],
        "push": state["push"], "notion": state["notion"], "keys": list(state["keys"]),
        "archived": archived, "removed": removed,
    })
    return EXIT_OK


def _cmd_stage(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    current = state["stage"]
    if fix_state.STAGES.index(args.stage) < fix_state.STAGES.index(current):
        raise UsageError(
            f"La passe est déjà à l'étape « {current} » : revenir à « {args.stage} » est impossible."
        )
    state["stage"] = args.stage
    fix_state.save(run_dir, state)
    _emit({"stage": args.stage})
    return EXIT_OK


def _cmd_start(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = state["keys"].get(args.key)
    if key is None:
        known = ", ".join(state["keys"]) or "aucune"
        raise UsageError(f"Clé inconnue : {args.key} (clés de la passe : {known}).")
    path = _repo_path(run_dir, key["repo"])
    worktree = _worktree_path(run_dir, key)
    if key.get("branch") is None:
        folder = worktree.parent
        if folder.exists() and any(folder.iterdir()):
            raise UsageError(
                f"Le dossier {folder} n'est pas vide : retirez-le (git worktree remove pour un "
                "worktree), puis relancez."
            )
        candidates = _read_run_json(run_dir, "fix/candidates.json", "cqa_fix.py candidates")
        flight = (candidates.get("flights") or {}).get(key["repo"]) or {}
        base = _base(run_dir, key["repo"])
        fingerprint = fix_state.dev_fingerprint(path, services.run)
        folder.mkdir(parents=True, exist_ok=True)
        opened = fix_git.open_new(
            path, services.run, slug=key["slug"], flight=flight, push=state["push"], base=base,
            worktree=worktree,
        )
        key.update(opened, base=base, worktree=str(worktree), dev_fingerprint=fingerprint)
    else:
        fix_git.reopen(path, services.run, branch=key["branch"], push=state["push"], worktree=worktree)
    done_refs = fix_git.refs_between(
        path, services.run, f"refs/remotes/origin/{key['base']}", key["start_sha"]
    )
    for ref in done_refs:
        row = key["rows"].get(str(ref))
        if isinstance(row, dict) and row.get("outcome") == "pending":
            row["outcome"] = "done_before"
    if key.get("step") in ("selected", "done"):
        key["step"] = "started"
    fix_state.save(run_dir, state)
    _emit({
        "branch": key["branch"], "worktree": str(worktree), "base": key["base"],
        "start_sha": key["start_sha"], "reused": key["reused"], "done_refs": done_refs,
    })
    return EXIT_OK


def _cmd_abandon(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _read_state(run_dir, required=True)
    if state.get("finished_at"):
        raise UsageError("Aucune passe fix inachevée : rien à abandonner.")
    removed = _remove_worktrees(run_dir, state, services.run)
    unpushed = []
    for key in state["keys"].values():
        if key.get("branch"):
            path = _repo_path(run_dir, key["repo"])
            count = fix_git.unpushed(path, services.run, key["branch"])
            if count:
                unpushed.append({"branch": key["branch"], "commits": count})
    state["finished_at"] = fix_state.now()
    fix_state.save(run_dir, state)
    _emit({"removed": removed, "unpushed": unpushed})
    return EXIT_OK


def _read_state(run_dir: Path, *, required: bool):
    try:
        return fix_state.load(run_dir)
    except FileNotFoundError:
        if required:
            raise UsageError(NO_PASS)
        return None


def _pass_state(run_dir: Path) -> dict:
    """Return the state of the pass in progress; exit 2 when there is none."""
    state = _read_state(run_dir, required=True)
    if state.get("finished_at"):
        raise UsageError(NO_PASS)
    return state


def _worktree_path(run_dir: Path, key: dict) -> Path:
    return run_dir / "fix" / key["repo"] / key["slug"] / "wt"


def _repo_path(run_dir: Path, name: str) -> str:
    for repo in _load_context(run_dir).get("repos") or []:
        if isinstance(repo, dict) and repo.get("name") == name:
            if isinstance(repo.get("path"), str) and repo["path"]:
                return repo["path"]
    raise ContextError(f"context.json : le repo « {name} » est absent ou sans chemin (« path »).")


def _base(run_dir: Path, name: str) -> str:
    doctor = _read_run_json(run_dir, "fix/doctor.json", "cqa_fix.py doctor")
    for entry in doctor.get("repos") or []:
        if isinstance(entry, dict) and entry.get("repo") == name and entry.get("base"):
            return entry["base"]
    return "develop"


def _remove_worktrees(run_dir: Path, state: dict, run) -> list:
    """Remove the worktrees the keys of the pass still have; return their paths."""
    removed = []
    for key in state["keys"].values():
        worktree = _worktree_path(run_dir, key)
        if worktree.exists():
            if fix_git.remove_worktree(_repo_path(run_dir, key["repo"]), run, worktree):
                removed.append(str(worktree))
    return removed


def _archive(run_dir: Path, old: dict, run) -> tuple:
    """Move a finished pass, state and key folders, to fix/history/<stamp>/."""
    removed = _remove_worktrees(run_dir, old, run)
    target = _new_history_dir(run_dir)
    for key in old["keys"].values():
        folder = run_dir / "fix" / key["repo"] / key["slug"]
        if folder.is_dir():
            (target / key["repo"]).mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(target / key["repo"] / key["slug"]))
            if not any(folder.parent.iterdir()):
                folder.parent.rmdir()
    os.replace(run_dir / fix_state.STATE_FILE, target / "state.json")
    return removed, target.relative_to(run_dir).as_posix()


def _new_history_dir(run_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    history = run_dir / "fix" / "history"
    history.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        target = history / (stamp if index == 1 else f"{stamp}-{index}")
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return target
    raise UsageError("fix/history/ : impossible de créer un dossier d'archive.")


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
