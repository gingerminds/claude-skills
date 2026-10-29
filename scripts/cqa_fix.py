#!/usr/bin/env python3
"""Fix mode of the checklist audit: GitLab diagnostic, then the commands built on it.

Results go to stdout (JSON), errors to stderr.

Exit codes: 0 success, 2 environment or usage, 5 refused by a guard, 6 invalid context,
report, review or state, 7 secret found.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

from cqa import fix_checks, fix_git, fix_gitlab, fix_guard, fix_names, fix_rows, fix_state
from cqa.checklist import parse_refs, ref_label
from cqa.notion_read import write_json
from cqa.rerun import latest_findings, read_grounded_findings
from cqa.secrets import known_values_from_env

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 5
EXIT_CONTEXT = 6
EXIT_SECRET = 7

UNFINISHED = "Une passe fix est inachevée : reprenez-la ou abandonnez-la (cqa_fix.py abandon)."
_REVIEW_FILE = re.compile(r"review-([0-9]+)\.json")
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
    except fix_git.Refused as error:
        print(error, file=sys.stderr)
        return EXIT_REFUSED
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
    sub = _command(commands, "validate", "contrôle un rapport ou une revue", _cmd_validate)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help="clé <repo>/<slug> de la passe")
    sub.add_argument("kind", choices=("report", "review"), help="report-<N> ou review-<N>.json")
    sub.add_argument("--pass", dest="number", type=int, required=True, help="numéro de la passe")
    sub = _command(commands, "guard", "garde-fou sur les commits d'une clé", _cmd_guard)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help="clé <repo>/<slug> de la passe")
    sub = _command(commands, "drop", "retire des lignes de la branche d'une clé", _cmd_drop)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help="clé <repo>/<slug> de la passe")
    sub.add_argument("--refs", required=True, help="Réf à retirer, séparées par des virgules")
    sub.add_argument("--reason", required=True, help="raison, en français, donnée dans la MR")
    sub = _command(commands, "check", "contrôles de syntaxe et stylelint des fichiers corrigés", _cmd_check)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help="clé <repo>/<slug> de la passe")
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
    key = _key(state, args.key)
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


def _cmd_validate(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    if args.number < 1:
        raise UsageError("--pass : un numéro de passe à partir de 1 est attendu.")
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    document, errors = _read_key_file(run_dir, key, f"{args.kind}-{args.number}.json")
    if args.kind == "review":
        return _validate_review(run_dir, state, key, args, document, errors)
    worktree = _key_worktree(run_dir, key, services.run)
    changed = _dev_checkout_changed(run_dir, key, services.run)
    entries = []
    if document is not None:
        commits = fix_git.commits_in_range(worktree, services.run, key["start_sha"])
        entries, errors = fix_guard.validate_report(
            document, key=args.key, number=args.number, rows=key["rows"], commits=commits
        )
    summary = {outcome: [] for outcome in fix_guard.REPORT_OUTCOMES}
    if not errors:
        for entry in entries:
            row = key["rows"][str(entry["ref"])]
            commits = []
            if entry["outcome"] == "fixed":
                previous = row.get("commits") or []
                commits = previous + [sha for sha in entry["commits"] if sha not in previous]
            row.update(
                outcome=entry["outcome"], reason=entry["reason"], commits=commits,
                attention=entry["attention"], **{"pass": args.number},
            )
            summary[entry["outcome"]].append(entry["ref"])
        key["step"] = "fixed"
        key["pass"] = max(key.get("pass") or 0, args.number)
        fix_state.save(run_dir, state)
    _emit({
        "ok": not errors, "errors": errors,
        "rows": {outcome: sorted(refs) for outcome, refs in summary.items()},
        "dev_checkout_changed": changed,
    })
    return EXIT_CONTEXT if errors else EXIT_OK


def _validate_review(run_dir: Path, state: dict, key: dict, args, document, errors: list) -> int:
    """Check review-<N>.json; a review without blocking verdict moves the key to reviewed."""
    blocking = []
    if document is not None:
        blocking, errors = fix_guard.validate_review(
            document, key=args.key, number=args.number, rows=key["rows"]
        )
    if not errors and not blocking:
        key["step"] = "reviewed"
        fix_state.save(run_dir, state)
    _emit({"ok": not errors, "errors": errors, "blocking": blocking})
    return EXIT_CONTEXT if errors else EXIT_OK


def _cmd_guard(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    worktree = _key_worktree(run_dir, key, services.run)
    repo = _repo(run_dir, key["repo"])
    _third_party(repo)
    problems = fix_guard.check_commits(
        worktree, key["start_sha"], {int(ref) for ref in key["rows"]}, repo,
        known_values_from_env(), services.run,
    )
    _emit({"ok": not problems, "problems": problems})
    if problems:
        print(f"Garde-fou : {len(problems)} problème(s) sur la branche {key['branch']}, "
              "détail dans « problems ».", file=sys.stderr)
        return EXIT_REFUSED
    return EXIT_OK


def _cmd_check(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    worktree = _key_worktree(run_dir, key, services.run)
    third_party = _third_party(_repo(run_dir, key["repo"]))
    folder = run_dir / "fix" / key["repo"] / key["slug"]
    check_dir = folder / "check"
    try:
        files = fix_checks.changed_files(worktree, key["start_sha"], services.run)
        added = fix_checks.added_lines(worktree, key["start_sha"], services.run)
    except ValueError as error:
        raise UsageError(str(error))
    shutil.rmtree(check_dir, ignore_errors=True)
    check_dir.mkdir(parents=True)
    checks = []
    if files:
        checks = fix_checks.run_checks(
            services.run, Path(__file__).resolve().parent / "cqa-toolbox.sh", check_dir, worktree,
            key["repo"], files, added, third_party,
        )
    refs = fix_checks.failed_refs(worktree, key["start_sha"], checks, services.run)
    not_run = [{**item, "why": fix_checks.NOT_RUN_WHY} for item in fix_checks.project_commands(worktree)]
    write_json(folder / "checks.json", {"checks": checks, "not_run": not_run})
    if fix_state.STEPS.index(key.get("step", "started")) < fix_state.STEPS.index("checked"):
        key["step"] = "checked"
        fix_state.save(run_dir, state)
    _emit({"checks": checks, "not_run": not_run, "failed_refs": refs})
    return EXIT_OK


def _third_party(repo: dict) -> list:
    """Return the repo's third_party_paths; raises ContextError when it is not a list of paths."""
    third_party = repo.get("third_party_paths", [])
    if not isinstance(third_party, list) or not all(isinstance(item, str) for item in third_party):
        raise ContextError(
            f"context.json : « third_party_paths » du repo « {repo['name']} » doit être une "
            "liste de chemins."
        )
    return third_party


def _cmd_drop(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    refs = parse_refs(args.refs)
    reason = args.reason.strip()
    if not refs:
        raise UsageError("--refs : au moins une Réf est attendue (CQ-3).")
    if not reason or len(reason) > fix_guard.MAX_REASON:
        raise UsageError(
            f"--reason : une raison en français de {fix_guard.MAX_REASON} caractères au plus "
            "est attendue."
        )
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    rows = key["rows"]
    unknown = [ref for ref in refs if str(ref) not in rows]
    if unknown:
        raise UsageError(f"Réf absentes de la clé {args.key} : {_labels(unknown)}.")
    before = [ref for ref in refs if rows[str(ref)].get("outcome") == "done_before"]
    if before:
        raise fix_git.Refused(
            f"{_labels(before)} : déjà sur la branche avant cette passe, rien n'a été retiré."
        )
    worktree = _key_worktree(run_dir, key, services.run)
    allowed = {int(ref) for ref, row in rows.items() if row.get("outcome") != "done_before"}
    linked = {int(ref): rows[ref].get("commits") or [] for ref in rows if int(ref) in allowed}
    result = fix_git.drop_commits(
        worktree, services.run, branch=key["branch"], start_sha=key["start_sha"], refs=set(refs),
        linked=linked, allowed=allowed,
    )
    rewritten = result["rewritten"]
    also = sorted(set(result["dropped"]) - set(refs))
    for ref, row in rows.items():
        if int(ref) in result["dropped"]:
            row.update(outcome="dropped", commits=[], reason=reason if int(ref) in refs else (
                f"retirée avec {_labels(refs)} : son correctif dépend d'un commit retiré"))
        else:
            row["commits"] = [rewritten.get(sha, sha) for sha in row.get("commits") or []]
    if _blocking_rows_dropped(run_dir, key):
        steps = fix_state.STEPS
        if steps.index(key.get("step", "started")) < steps.index("reviewed"):
            key["step"] = "reviewed"
    fix_state.save(run_dir, state)
    if result["unattached"]:
        raise fix_git.GitRefused(result["unattached"])
    _emit({"dropped": refs, "also_dropped": also})
    return EXIT_OK


def _blocking_rows_dropped(run_dir: Path, key: dict) -> bool:
    """True when the latest review of the key has blocking rows and all of them are dropped."""
    folder = run_dir / "fix" / key["repo"] / key["slug"]
    numbers = [
        int(found.group(1)) for found in (_REVIEW_FILE.fullmatch(path.name)
                                          for path in folder.glob("review-*.json")) if found
    ]
    if not numbers:
        return False
    document, errors = _read_key_file(run_dir, key, f"review-{max(numbers)}.json")
    items = document.get("rows") if isinstance(document, dict) else None
    if errors or not isinstance(items, list):
        return False
    blocking = [item.get("ref") for item in items
                if isinstance(item, dict) and item.get("verdict") == "blocking"]
    return bool(blocking) and all(
        (key["rows"].get(str(ref)) or {}).get("outcome") == "dropped" for ref in blocking
    )


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


def _key(state: dict, name: str) -> dict:
    key = state["keys"].get(name)
    if key is None:
        known = ", ".join(state["keys"]) or "aucune"
        raise UsageError(f"Clé inconnue : {name} (clés de la passe : {known}).")
    return key


def _started_key(state: dict, name: str) -> dict:
    """Return a key that has its branch and worktree; exit 2 before its first start."""
    key = _key(state, name)
    if key.get("step") == "selected" or not key.get("branch"):
        raise UsageError(
            f"La clé {name} n'a pas encore de branche ni de worktree : "
            "lancez d'abord cqa_fix.py start."
        )
    if not fix_git.is_sha(key.get("start_sha")):
        raise fix_state.StateError(
            f"{fix_state.STATE_FILE} : « start_sha » de la clé {name} est invalide."
        )
    return key


def _key_worktree(run_dir: Path, key: dict, run) -> Path:
    """Return the worktree of a started key, checked to be a worktree on the key's branch."""
    worktree = _worktree_path(run_dir, key)
    top = None
    if worktree.is_dir():
        top = fix_names.git_out(worktree, run, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != worktree.resolve():
        raise UsageError(f"Le worktree {worktree} est absent : relancez cqa_fix.py start.")
    branch = fix_names.git_out(worktree, run, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != key["branch"]:
        raise UsageError(
            f"Le worktree {worktree} n'est plus sur la branche {key['branch']} : remettez-l'y, "
            "puis relancez."
        )
    return worktree


def _read_key_file(run_dir: Path, key: dict, name: str) -> tuple:
    """Return (document, []) for a file of the key folder, or (None, [error])."""
    relative = f"fix/{key['repo']}/{key['slug']}/{name}"
    try:
        return json.loads((run_dir / relative).read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [f"{relative} est introuvable : le sous-agent n'a pas écrit son fichier."]
    except (OSError, ValueError) as error:
        return None, [f"{relative} est illisible : {error}"]


def _dev_checkout_changed(run_dir: Path, key: dict, run):
    before = key.get("dev_fingerprint")
    if not isinstance(before, dict):
        return None
    after = fix_state.dev_fingerprint(_repo_path(run_dir, key["repo"]), run)
    return fix_state.fingerprint_changes(before, after)


def _labels(refs) -> str:
    return ", ".join(ref_label(ref) for ref in refs)


def _worktree_path(run_dir: Path, key: dict) -> Path:
    return run_dir / "fix" / key["repo"] / key["slug"] / "wt"


def _repo(run_dir: Path, name: str) -> dict:
    for repo in _load_context(run_dir).get("repos") or []:
        if isinstance(repo, dict) and repo.get("name") == name:
            if isinstance(repo.get("path"), str) and repo["path"]:
                return repo
    raise ContextError(f"context.json : le repo « {name} » est absent ou sans chemin (« path »).")


def _repo_path(run_dir: Path, name: str) -> str:
    return _repo(run_dir, name)["path"]


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
    """Move a finished pass, state and key folders, to fix/_history/<stamp>/."""
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
    history = run_dir / fix_state.HISTORY_DIR
    history.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        target = history / (stamp if index == 1 else f"{stamp}-{index}")
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return target
    raise UsageError(f"{fix_state.HISTORY_DIR}/ : impossible de créer un dossier d'archive.")


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
