#!/usr/bin/env python3
"""Fix mode of the checklist audit: GitLab diagnostic, then the commands built on it.

Results go to stdout (JSON), errors to stderr.

Exit codes: 0 success, 2 environment or usage, 5 refused by a guard, 6 invalid context,
report, review or state, 7 secret found.
"""
import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import quote

from cqa import deferred, git_tools, fix_checks, fix_git, fix_gitlab, fix_gitleaks, fix_guard, fix_mr, fix_names, fix_rows, fix_stack, fix_state, fix_summary
from cqa.checklist import STATUS_REVIEW, parse_refs, ref_label
from cqa.notion_read import write_json, write_text
from cqa.project import project_name
from cqa.report import run_start
from cqa.rerun import latest_findings, read_grounded_findings
from cqa.secrets import known_values_from_env, scan_text

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 5
EXIT_CONTEXT = 6
EXIT_SECRET = 7
# The launcher of the Docker toolbox, next to this script (check, secrets).
LAUNCHER = Path(__file__).resolve().parent / "cqa-toolbox.sh"

UNFINISHED = "Une passe fix est inachevée : reprenez-la ou abandonnez-la (cqa_fix.py abandon)."
UNFINISHED_V2 = fix_state.UNFINISHED_V2
_REVIEW_FILE = re.compile(r"review-([0-9]+)\.json")
NO_PASS = "Aucune passe fix en cours : lancez d'abord cqa_fix.py open."
NEED_BODY = "Lancez d'abord cqa_fix.py mr-body."
DIVERGED = fix_git.DIVERGED
NOTHING_TO_PUBLISH = "Aucun commit sur la branche : ni push ni MR."
AUTONOMOUS_KEYS = ("Mode autonome : publication seulement à la fin de la passe (stage questions), "
                   "rien n'a été poussé.")
STALE_BODY = "Description de MR périmée : relancez mr-body."
NOTHING_PUSHED = "Rien n'a été poussé."
UNREPORTED = "commit non rapporté par le fixer : {sha} {subject}"
LEFT_KEY = ("La clé {name} est laissée au lead ({reason}) : {command} ne la touche pas. Une fois le "
            "problème réglé : cqa_fix.py leave <run_dir> {name} --clear. Rien n'a changé.")
LINKS_FILE = "fix/mr-links.json"
STACK_CORE_FILE = "fix/stack-core.json"
KEY_HELP = "clé de la passe : le nom du repo (fix/candidates.json)"
SECTION_HELP = "alias de la section (securite, qualite...)"
# Push settings of the dev's config that could send more than the branch, trigger GitLab
# push options (ci.skip, merge_request.*) or prompt. An empty pushOption resets the list.
_PUSH_CONFIG = ("-c", "push.followTags=false", "-c", "push.recurseSubmodules=no",
                "-c", "push.gpgSign=false", "-c", "push.pushOption=")


class UsageError(Exception):
    """A problem with the environment or the arguments, worded for the lead."""


class ContextError(Exception):
    """A context.json that is missing, unreadable or unfit for the command."""


class SecretFound(Exception):
    """A text bound for GitLab that holds a secret (exit 7); the message names labels only."""


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
    except SecretFound as error:
        print(error, file=sys.stderr)
        return EXIT_SECRET
    except fix_stack.StackRefused as error:
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
    sub.add_argument("key", help=KEY_HELP)
    for name, help_text, handler in (
        ("section-start", "commence la section suivante d'une clé", _cmd_section_start),
        ("section-end", "termine la section en cours d'une clé, toutes ses lignes réglées",
         _cmd_section_end),
    ):
        sub = _command(commands, name, help_text, handler)
        sub.add_argument("run_dir", type=Path, help="dossier du run")
        sub.add_argument("key", help=KEY_HELP)
        sub.add_argument("--section", required=True, help=SECTION_HELP)
        if name == "section-end":
            sub.add_argument("--failed", action="store_true", help=(
                "la section n'aboutit pas : ses commits sont retirés, ses lignes aussi"))
            sub.add_argument("--reason",
                             help="avec --failed : raison, en français, donnée dans la MR")
    sub = _command(commands, "pass-start", "note la tête de la branche avant une passe du fixer",
                   _cmd_pass_start)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--pass", dest="number", type=int, required=True,
                     help="numéro de la passe du fixer qui commence")
    sub.add_argument("--section", required=True, help=SECTION_HELP)
    sub = _command(commands, "validate", "contrôle un rapport ou une revue", _cmd_validate)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("kind", choices=("report", "review"), help="report-<N> ou review-<N>.json")
    sub.add_argument("--pass", dest="number", type=int, required=True, help="numéro de la passe")
    sub = _command(commands, "guard", "garde-fou sur les commits d'une clé", _cmd_guard)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--staged", action="store_true",
                     help="contrôle l'index du worktree et le message prévu, avant le commit")
    sub.add_argument("--message-file", type=Path,
                     help="fichier du message du commit prévu (avec --staged)")
    sub = _command(commands, "waive-secret",
                   "lève une alerte de secret heuristique sur une ligne, avec l'accord du lead",
                   _cmd_waive_secret)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--sha", required=True,
                     help="SHA du commit (ou blob indexé, avec guard --staged) du problème")
    sub.add_argument("--path", required=True, help="chemin du fichier du problème")
    sub.add_argument("--line", type=int, required=True, help="ligne du problème")
    sub.add_argument("--label", required=True,
                     help="label du problème (credential_assignment, credential_define)")
    sub = _command(commands, "review-scope", "diff d'une passe : relecture allégée ou non",
                   _cmd_review_scope)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--pass", dest="number", type=int, required=True, help="numéro de la passe")
    sub = _command(commands, "drop", "retire des lignes de la branche d'une clé", _cmd_drop)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--refs", required=True, help="Réf à retirer, séparées par des virgules")
    sub.add_argument("--reason", required=True, help="raison, en français, donnée dans la MR")
    sub = _command(commands, "reorder", "range les commits non poussés d'une clé par section",
                   _cmd_reorder)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub = _command(commands, "reword", "réécrit le message d'un commit non poussé d'une clé",
                   _cmd_reword)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--commit", required=True, help="SHA du commit dont le message change")
    sub.add_argument("--message-file", type=Path, required=True,
                     help="fichier du nouveau message (sujet, puis corps éventuel)")
    sub = _command(commands, "leave",
                   "laisse une clé au lead : ni rangement, ni publication, ni finish, jusqu'à --clear",
                   _cmd_leave)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    choice = sub.add_mutually_exclusive_group(required=True)
    choice.add_argument("--reason", help="raison, en français, donnée dans la synthèse")
    choice.add_argument("--clear", action="store_true",
                        help="reprend la clé : le lead a réglé le problème de la branche")
    sub = _command(commands, "check", "contrôles de syntaxe et stylelint des fichiers corrigés", _cmd_check)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub = _command(commands, "mr-body", "titre et description de la MR d'une clé", _cmd_mr_body)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub = _command(commands, "publish", "pousse la branche, crée ou complète la MR en brouillon",
                   _cmd_publish)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub = _command(commands, "finish",
                   "retire le worktree d'une clé publiée, et sa branche locale restée sans commit",
                   _cmd_finish)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("key", help=KEY_HELP)
    sub.add_argument("--keep-local", action="store_true",
                     help="garde la branche en local après un push refusé ou en échec")
    sub = _command(commands, "summary", "synthèse de la passe et message pour l'équipe", _cmd_summary)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--replace-failed", default="",
                     help="Réf dont le commentaire du skill n'a pas pu être remplacé")
    sub.add_argument("--in-review",
                     help="Réf passées « En review » dans Notion, séparées par des virgules "
                          "(\"\" pour aucune) ; sans l'option, la synthèse n'en parle pas")
    sub = _command(commands, "abandon", "abandonne la passe en cours", _cmd_abandon)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub = _command(commands, "merged", "lignes « En review » dont la MR est fusionnée et déployée",
                   _cmd_merged)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub = _command(commands, "stack-core",
                   "cœur WordPress ou Drupal en lecture seule pour les sous-agents", _cmd_stack_core)
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--repo", help="repo à traiter (par défaut : le premier WordPress ou Drupal)")
    choice = sub.add_mutually_exclusive_group()
    choice.add_argument("--download", action="store_true",
                        help="télécharge la version officielle dans le cache partagé (WordPress)")
    choice.add_argument("--path", type=Path, help="dossier du cœur indiqué par le lead")
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


def _cmd_merged(args, services: Services) -> int:
    run_dir = Path(args.run_dir)
    context = _load_context(run_dir)
    repos = {repo.get("name"): repo for repo in context.get("repos") or []
             if isinstance(repo, dict)}
    document = _read_json_file(run_dir / "rows.json", "lancer d'abord cqa_notion.py rows")
    rows = document.get("rows") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ContextError("rows.json : la liste « rows » est absente.")
    refs = sorted({row["ref"] for row in rows if isinstance(row, dict)
                   and row.get("status") == STATUS_REVIEW and isinstance(row.get("ref"), int)})
    notes = []
    links = _newest_links(run_dir, refs, notes)
    checker = _MergeChecker(services.run, repos, notes)
    result = {"merged_deployed": [], "merged_waiting": [], "closed": [], "open": []}
    for ref in refs:
        if ref not in links:
            notes.append(f"{ref_label(ref)} : « {STATUS_REVIEW} » sans MR connue dans les runs du "
                         "projet : non vérifiée.")
            continue
        verdicts = [checker.verdict(entry) for entry in links[ref]]
        states = [state for state, _ in verdicts]
        if "closed" in states:
            result["closed"].append(ref)
        elif "open" in states:
            result["open"].append(ref)
        elif "waiting" in states:
            why = " ; ".join(why for state, why in verdicts if state == "waiting")
            result["merged_waiting"].append({"ref": ref, "why": why})
        else:
            result["merged_deployed"].append(ref)
    result["notes"] = notes
    _emit(result)
    return EXIT_OK


def _stack_records(run_dir: Path) -> dict:
    """Return the stack core chosen per repo (fix/stack-core.json), {} when none."""
    try:
        document = json.loads((run_dir / STACK_CORE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _cmd_stack_core(args, services: Services) -> int:
    run_dir = Path(args.run_dir)
    repos = [repo for repo in _load_context(run_dir).get("repos") or []
             if isinstance(repo, dict) and repo.get("stack") in fix_stack.MARKERS
             and isinstance(repo.get("path"), str) and repo["path"]
             and (args.repo is None or repo.get("name") == args.repo)]
    if not repos:
        raise UsageError("Aucun repo WordPress ou Drupal dans context.json"
                         + (f" pour « {args.repo} »." if args.repo else "."))
    stack, repo_path = repos[0]["stack"], Path(repos[0]["path"])
    core, source, note = None, None, None
    if args.path:
        core, source = fix_stack.check_path(stack, args.path), "path"
    else:
        core = fix_stack.find_core(stack, repo_path)
        source = "repo" if core else None
    version = fix_stack.read_version(stack, repo_path, core)
    cache = None
    if args.download:
        if stack != "wordpress":
            raise UsageError("Le téléchargement n'existe que pour WordPress.")
        if not fix_stack.valid_version(version):
            raise UsageError(
                "Version WordPress inconnue ou invalide : indiquez un dossier avec --path.")
        cache = fix_stack.download(version, fix_stack.cache_root())
        core, source = cache, "cache"
    elif core is None and version:
        cache = fix_stack.cached(stack, version)
        if cache:
            core, source = cache, "cache"
    if core is None:
        note = ("Cœur introuvable dans le repo."
                + (" Pas de téléchargement pour Drupal : indiquez un dossier (--path)."
                   if stack != "wordpress" else ""))
    result = {"stack": stack, "version": version, "found": core is not None,
              "path": str(core) if core else None, "source": source,
              "cache": str(cache) if cache else None, "note": note}
    if core:
        record = {"stack": stack, "version": version, "path": result["path"], "source": source}
        name = repos[0].get("name")
        (run_dir / "fix").mkdir(exist_ok=True)
        records = _stack_records(run_dir)
        records[name] = record
        write_json(run_dir / STACK_CORE_FILE, records)
        state = _read_state(run_dir, required=False)
        if state is not None and not state.get("finished_at") and not fix_state.is_v2(state):
            state.setdefault("stack_core", {})[name] = record
            fix_state.save(run_dir, state)
    _emit(result)
    return EXIT_OK


def _read_json_file(path: Path, hint: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContextError(f"{path.name} est introuvable dans {path.parent} : {hint}.")
    except (OSError, ValueError) as error:
        raise ContextError(f"{path.name} est illisible : {error}")


def _newest_links(run_dir: Path, refs: list, notes: list) -> dict:
    """Return {ref: [entry, ...]}, one entry per repo (the newest), read in the newest run of
    the project (run_dir and its siblings, newest first) whose fix/mr-links.json links the ref."""
    project = run_dir.resolve().parent
    folders = sorted((path for path in project.iterdir() if path.is_dir()),
                     key=lambda path: path.name, reverse=True)
    read = {}
    found = {}
    for folder in folders:
        try:
            read[folder.name] = fix_state.read_mr_link_entries(folder)
        except ValueError as error:
            read[folder.name] = {}
            notes.append(f"Run {folder.name} : {error} Fichier ignoré.")
    for ref in refs:
        for folder in folders:
            entries = read[folder.name].get(ref)
            if entries:
                per_repo = {}
                for entry in entries:
                    per_repo[entry.get("repo") or entry["url"]] = entry
                found[ref] = list(per_repo.values())
                break
    return found


class _MergeChecker:
    """State and preprod deployment of merge requests, repo by repo (read-only)."""

    def __init__(self, run, repos: dict, notes: list):
        self.run, self.repos, self.notes = run, repos, notes
        self._deployed = {}
        self._glab_down = False

    def verdict(self, entry: dict) -> tuple:
        """Return (state, why): state is open, closed, waiting (why set) or deployed."""
        repo = self.repos.get(entry.get("repo")) or self._repo_of_url(entry["url"])
        name = entry.get("repo") or (repo or {}).get("name") or "repo inconnu"
        iid = entry.get("iid") if isinstance(entry.get("iid"), int) else _iid_of(entry["url"])
        if iid is None:
            self._note(f"{name} : numéro de MR illisible, MR non vérifiée.")
            return "waiting", f"{name} : MR non vérifiable"
        try:
            host, project = (fix_gitlab.host_and_path(repo.get("remote") or "") if repo
                             else _host_and_project_of_url(entry["url"]))
        except ValueError:
            self._note(f"{name} : chemin du projet GitLab inconnu (remote illisible).")
            return "waiting", f"{name} : projet GitLab inconnu"
        try:
            info = fix_gitlab.merge_request(self.run, host, project, iid)
        except OSError:
            self._glab_down = True
            self._note("glab est indisponible : aucune MR n'a pu être lue.")
            return "waiting", f"{name} : glab indisponible"
        if info is None:
            return "waiting", f"{name} : MR !{iid} illisible sur GitLab"
        if info["state"] != "merged":
            return ("closed" if info["state"] == "closed" else "open"), ""
        if not info["sha"]:
            return "waiting", f"{name} : commit de fusion de la MR !{iid} inconnu"
        if repo is None or not isinstance(repo.get("path"), str):
            self._note(f"{name} : repo absent de context.json, déploiement non vérifié.")
            return "waiting", f"{name} : repo local inconnu"
        return self._deployment(name, repo, host, project, info["sha"])

    def _repo_of_url(self, url: str) -> Optional[dict]:
        """Return the repo of the context whose remote has the host and the exact project
        path of a merge request URL (an entry without « repo », v2 file), else None."""
        try:
            host, project = _host_and_project_of_url(url)
        except ValueError:
            return None
        for repo in self.repos.values():
            try:
                remote_host, remote_path = fix_gitlab.host_and_path(repo.get("remote") or "")
            except ValueError:
                continue
            if (remote_host.lower(), remote_path.strip("/")) == (host.lower(), project.strip("/")):
                return repo
        return None

    def _deployment(self, name: str, repo: dict, host: str, project: str, sha: str) -> tuple:
        path = repo["path"]
        if name not in self._deployed:
            if not fix_git.fetch(path, self.run):
                self._note(f"{name} : git fetch origin a échoué : déploiement jugé sur les refs "
                           "locales.")
            env = dict(os.environ)
            env["GITLAB_HOST"] = host
            run = functools.partial(self.run, cwd=path, env=env)
            found = git_tools.deployed_from_gitlab(project, repo.get("preprod_branch"), run)
            self._deployed[name] = found["sha"]
        deployed = self._deployed[name]
        if not deployed:
            return "waiting", f"{name} : commit déployé en préprod inconnu"
        if not fix_git.rev(path, self.run, sha):
            return "waiting", f"{name} : commit de fusion {sha[:8]} absent du repo local"
        if not fix_git.rev(path, self.run, deployed):
            return "waiting", f"{name} : commit déployé {deployed[:8]} absent du repo local"
        if fix_git.is_ancestor(path, self.run, sha, deployed):
            return "deployed", ""
        return "waiting", f"{name} : fusionnée, pas encore déployée en préprod"

    def _note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _host_and_project_of_url(url: str) -> tuple:
    found = re.fullmatch(r"https?://([^/]+)/(.+?)/-/merge_requests/[0-9]+.*", url.strip())
    if not found:
        raise ValueError(f"URL de MR non reconnue : {url!r}")
    return found.group(1), found.group(2)


def _iid_of(url: str) -> Optional[int]:
    found = re.search(r"/merge_requests/([0-9]+)", url)
    return int(found.group(1)) if found else None


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
        raise UsageError(UNFINISHED_V2 if fix_state.is_v2(old) else UNFINISHED)
    if not (run_dir / "fix" / "candidates.json").is_file():
        raise UsageError("Lancez d'abord cqa_fix.py candidates.")
    candidates = _read_run_json(run_dir, "fix/candidates.json", "cqa_fix.py candidates")
    context = _load_context(run_dir)
    try:
        state = fix_state.new_state(
            candidates, mode=args.mode, push=args.push, notion=args.notion == "yes",
            project_name=_project_name(context),
        )
    except (KeyError, TypeError, AttributeError, ValueError):
        raise UsageError("fix/candidates.json : clés invalides, relancez cqa_fix.py candidates.")
    records, notes = _present_stack_records(run_dir)
    if records:
        state["stack_core"] = records
    removed, archived, leftovers = (_archive(run_dir, old, services.run) if old is not None
                                    else ([], None, []))
    fix_state.save(run_dir, state)
    _emit({
        "started_at": state["started_at"], "stage": state["stage"], "mode": state["mode"],
        "push": state["push"], "notion": state["notion"], "project_name": state["project_name"],
        "keys": list(state["keys"]), "archived": archived, "removed": removed,
        "leftovers": leftovers, "notes": notes,
    })
    return EXIT_OK


def _present_stack_records(run_dir: Path) -> tuple:
    """Return (the stack core records whose folder still exists, a note for each other one):
    a core gone since stack-core ran is never given to the sub-agents."""
    records, notes = {}, []
    for name, record in _stack_records(run_dir).items():
        path = record.get("path") if isinstance(record, dict) else None
        if isinstance(path, str) and path and Path(path).is_dir():
            records[name] = record
        else:
            stack = record.get("stack") if isinstance(record, dict) else None
            notes.append(f"Cœur {stack or 'de la stack'} du repo {name} introuvable ({path}) : "
                         "non repris, relancez cqa_fix.py stack-core.")
    return records, notes


def _project_name(context: dict) -> Optional[str]:
    """Return the name of the project as cqa_plan.py project gives it: « project.name » of
    context.json, else the folder name of the first repo without its techno part."""
    project = context.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    for repo in context.get("repos") or []:
        if isinstance(repo, dict) and isinstance(repo.get("path"), str) and repo["path"].strip():
            return project_name(Path(repo["path"]).name) or None
    return None


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
    _not_left(key, args.key, "start")
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
            path, services.run, flight=flight, push=state["push"], base=base, worktree=worktree,
        )
        key.update(opened, base=base, worktree=str(worktree), dev_fingerprint=fingerprint)
    else:
        if key.get("branch_deleted"):
            _restore_branch(path, services.run, key)
            key.pop("branch_deleted")
        fix_git.reopen(path, services.run, branch=key["branch"], push=state["push"], worktree=worktree)
    done_refs = fix_git.refs_between(
        path, services.run, f"refs/remotes/origin/{key['base']}",
        key.get("taken_head") or key["start_sha"],
    )
    for ref in done_refs:
        row = key["rows"].get(str(ref))
        if isinstance(row, dict) and row.get("outcome") == "pending":
            row["outcome"] = "done_before"
    if key.get("step") in ("selected", "done"):
        key["step"] = "started"
        key.pop("empty_branch", None)
    fix_state.save(run_dir, state)
    _emit({
        "branch": key["branch"], "worktree": str(worktree), "base": key["base"],
        "start_sha": key["start_sha"], "reused": key["reused"], "done_refs": done_refs,
    })
    return EXIT_OK


def _restore_branch(path, run, key: dict) -> None:
    """Create again, at start_sha, the branch without commit that finish deleted
    (_delete_empty_branch). A branch of that name found again is taken only at start_sha:
    on another commit it is someone else's, and start exits 2 without touching it."""
    branch = key["branch"]
    head = fix_git.rev(path, run, f"refs/heads/{branch}")
    if head == key["start_sha"]:
        return
    if head is not None:
        raise UsageError(
            f"La branche {branch}, supprimée à la fin de la passe parce qu'elle était vide, "
            f"existe de nouveau sur un autre commit ({head[:12]}) : elle n'est pas reprise. "
            "Renommez-la, ou abandonnez la passe (cqa_fix.py abandon), puis relancez."
        )
    created = fix_names.git(path, run, "branch", "--no-track", branch, key["start_sha"])
    if created.returncode != 0:
        raise fix_git.GitRefused(
            f"La branche {branch} n'a pas pu être recréée : {created.stderr.strip()}")


def _cmd_pass_start(args, services: Services) -> int:
    """Record the head of the key's branch just before fixer pass N: the Base of review N.

    Pass N belongs to the section in progress (--section, recorded in pass_sections): its
    report covers the rows of that section only. At the questions stage, a pass that answers
    the questions set aside in a settled section belongs to that section, which stays settled
    (_answers_questions). N must not be lower than a pass already started, and no report of
    pass N or later may be validated yet. Starting the same pass again before its report keeps
    its first base, so that the commits of an interrupted run stay in bases[N]..HEAD; the base
    moves to HEAD only when it is no longer in the branch. A reviewed key goes back to fixed:
    it is never reviewed in the middle of a pass (see _every_pass_reviewed).
    """
    run_dir = args.run_dir.resolve()
    if args.number < 1:
        raise UsageError("--pass : un numéro de passe à partir de 1 est attendu.")
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    section = _section(key, args.key, args.section)
    if section["state"] != "active" and not _answers_questions(state, key, section):
        raise UsageError(
            f"La section {args.section} est à l'état « {section['state']} » : pass-start "
            "attend la section en cours (cqa_fix.py section-start)."
        )
    owners = _recorded(key, "pass_sections")
    owner = owners.get(str(args.number))
    if owner is not None and owner != args.section:
        raise UsageError(f"--pass {args.number} : la passe {args.number} appartient à la "
                         f"section {owner}.")
    bases = _recorded(key, "bases")
    started = max((int(number) for number in bases), default=0)
    reported = key.get("pass") or 0
    if args.number < started:
        raise UsageError(
            f"--pass {args.number} : la passe {started} de la clé {args.key} est déjà commencée."
        )
    if args.number <= reported:
        raise UsageError(
            f"--pass {args.number} : le rapport de la passe {reported} de la clé {args.key} est "
            f"déjà validé ; la passe suivante est la passe {reported + 1}."
        )
    worktree = _key_worktree(run_dir, key, services.run)
    base = bases.get(str(args.number))
    if not (fix_git.is_sha(base) and fix_git.is_ancestor(worktree, services.run, base, "HEAD")):
        base = fix_names.git_out(worktree, services.run, "rev-parse", "--verify", "--quiet", "HEAD")
        if not fix_git.is_sha(base):
            raise fix_git.GitRefused(f"git rev-parse HEAD a échoué dans {worktree}.")
        bases[str(args.number)] = base
        key["bases"] = bases
    owners[str(args.number)] = args.section
    key["pass_sections"] = owners
    if key.get("step") == "reviewed":
        key["step"] = "fixed"
    fix_state.save(run_dir, state)
    _emit({"pass": args.number, "base": base})
    return EXIT_OK


def _answers_questions(state: dict, key: dict, section: dict) -> bool:
    """True at the questions stage for a settled section that still holds a question row, or a
    row back to pending for its retry (_settle_drops): the pass answers the questions set aside
    in autonomous mode (SKILL.md section 12, step 6), or retries that row."""
    if state["stage"] != "questions" or section["state"] != "settled":
        return False
    for ref in section["refs"]:
        row = key["rows"].get(str(ref)) or {}
        if row.get("outcome") == "question" or (row.get("outcome") == "pending"
                                                and row.get("retry")):
            return True
    return False


def _pass_in_progress(key: dict) -> Optional[int]:
    """Return the pass started (pass-start) whose report is not validated yet, or None (R16)."""
    started = max((int(number) for number in _recorded(key, "bases")), default=0)
    return started if started > (key.get("pass") or 0) else None


def _cmd_section_start(args, services: Services) -> int:
    """Make the next section of the key the one in progress; starting it again resumes it.

    Sections run in their order: only the first pending one may start, and only while no
    other section is in progress.
    """
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    section = _section(key, args.key, args.section)
    if section["state"] != "active":
        active = next((item for item in key["sections"] if item["state"] == "active"), None)
        if active is not None:
            raise UsageError(
                f"La section {active['alias']} est en cours : terminez-la (cqa_fix.py "
                f"section-end) avant la section {args.section}."
            )
        if section["state"] != "pending":
            raise UsageError(
                f"La section {args.section} est à l'état « {section['state']} » : elle ne "
                "recommence pas."
            )
        first = next(item for item in key["sections"] if item["state"] == "pending")
        if first is not section:
            raise UsageError(
                f"La section {first['alias']} passe avant la section {args.section} : "
                "commencez-la d'abord."
            )
        section["state"] = "active"
        fix_state.save(run_dir, state)
    _emit({"section": section["alias"], "category": section.get("category"),
           "refs": section["refs"], "state": section["state"]})
    return EXIT_OK


def _cmd_section_end(args, services: Services) -> int:
    """Settle the section in progress once each of its rows is settled, or with --failed give
    it up (_fail_section).

    A row is settled when it is skipped, dropped or already on the branch, fixed with a review
    of its pass that does not block it, or a question set aside (autonomous mode only). No
    pass of the section may be in progress (started, its report not validated).
    """
    if args.failed != (args.reason is not None):
        raise UsageError("--failed et --reason vont ensemble : la section qui n'aboutit pas et "
                         "la raison, en français, du retrait de ses lignes.")
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    section = _section(key, args.key, args.section)
    if section["state"] != "active":
        raise UsageError(
            f"La section {args.section} est à l'état « {section['state']} » : section-end "
            "attend la section en cours."
        )
    if args.failed:
        return _fail_section(run_dir, state, key, section, _reason(args.reason), services.run)
    started = _pass_in_progress(key)
    if started is not None:
        raise UsageError(
            f"La passe {started} est commencée sans rapport validé : validez-le avant de "
            f"terminer la section {args.section}."
        )
    reviews = _recorded(key, "reviews")
    unsettled = []
    for ref in section["refs"]:
        why = _unsettled(ref, key["rows"].get(str(ref)) or {}, reviews, state.get("mode"))
        if why:
            unsettled.append(f"{ref_label(ref)} {why}")
    if unsettled:
        raise UsageError(f"La section {args.section} n'est pas réglée : {', '.join(unsettled)}.")
    section["state"] = "settled"
    fix_state.save(run_dir, state)
    _emit({"section": section["alias"], "state": "settled", "next": _next_section(key)})
    return EXIT_OK


def _next_section(key: dict) -> Optional[str]:
    return next((item["alias"] for item in key["sections"] if item["state"] == "pending"), None)


def _fail_section(run_dir: Path, state: dict, key: dict, section: dict, reason: str,
                  run) -> int:
    """Give up the section in progress (section-end --failed): it could not reach a valid
    review, or the workstation failed.

    Its rows still open (pending, fixed, question) leave with reason, and so does a skipped
    row covered by the fix of one of them; other skipped, dropped and done_before rows keep
    theirs. Their commits leave the branch with drop_commits, with every commit made since
    the first pass of the section (reported or not). Nothing changes when one of them is
    already on a remote. The uncommitted changes of the worktree are first saved in
    fix/<repo>/leftover-<N>.patch, then the worktree is reset and cleaned
    (fix_git.save_leftovers); the section records them at once in « leftover », which a
    later run prints when it saves no new patch. The passes of the section whose report is
    not validated are closed: their base and section are forgotten, so that the next
    section may use their number, and their report, review and notes files are renamed
    <name>-<N>.failed.json, as is a review that was never recorded.
    """
    worktree = _key_worktree(run_dir, key, run)
    rows, alias = key["rows"], section["alias"]
    refs = _failing_refs(section, rows)
    allowed, linked = _drop_scope(rows)
    passes = sorted(int(number) for number, owner in _recorded(key, "pass_sections").items()
                    if owner == alias)
    extra = frozenset(_commits_since_pass(worktree, run, key, passes))
    scope = {"branch": key["branch"], "start_sha": key["start_sha"], "refs": set(refs),
             "linked": linked, "extra": extra}
    fix_git.check_drop(worktree, run, **scope)
    leftover = fix_git.save_leftovers(worktree, run, _key_folder(run_dir, key))
    saved = None
    if leftover is not None:
        saved = {"patch": leftover["path"].relative_to(run_dir).as_posix(),
                 "base": leftover["base"], "untracked": leftover["untracked"]}
        section["leftover"] = saved
        fix_state.save(run_dir, state)
    before = _range_shas(worktree, run, key)
    result = fix_git.drop_commits(worktree, run, allowed=allowed, **scope)
    also, retry = _settle_drops(
        state, key, refs, result["dropped"], reason,
        f"retirée avec la section {alias} : son correctif dépend d'un commit retiré",
    )
    _remap(worktree, run, key, before, result["rewritten"])
    _close_failed_passes(run_dir, key, passes)
    section.update(state="failed", reason=reason)
    if _every_pass_reviewed(key):
        _raise_step(key, "reviewed")
    fix_state.save(run_dir, state)
    if result["unattached"]:
        raise fix_git.GitRefused(result["unattached"])
    _emit({"section": alias, "state": "failed", "next": _next_section(key), "dropped": refs,
           "also_dropped": also, "retry": retry,
           "leftover": saved or section.get("leftover")})
    return EXIT_OK


def _failing_refs(section: dict, rows: dict) -> list:
    """Return the rows a failed section drops, in its order: those still open, and the skipped
    ones covered by the fix of one of those (fix_mr.covered_refs)."""
    opened = {ref for ref in section["refs"]
              if (rows.get(str(ref)) or {}).get("outcome") in ("pending", "fixed", "question")}
    covered = fix_mr.covered_refs(rows)
    return [ref for ref in section["refs"] if ref in opened or covered.get(ref) in opened]


def _commits_since_pass(worktree: Path, run, key: dict, passes: list) -> set:
    """Return the SHAs of bases[N]..HEAD for the first of passes whose base is in the branch."""
    bases = _recorded(key, "bases")
    for number in passes:
        base = bases.get(str(number))
        if fix_git.is_sha(base) and fix_git.is_ancestor(worktree, run, base, "HEAD"):
            return {item["sha"] for item in fix_git.commits_in_range(worktree, run, base)}
    return set()


def _close_failed_passes(run_dir: Path, key: dict, passes: list) -> None:
    """Close the passes of a failed section (see _fail_section)."""
    bases, owners = _recorded(key, "bases"), _recorded(key, "pass_sections")
    reviews = _recorded(key, "reviews")
    folder = _key_folder(run_dir, key)
    for number in passes:
        names = ()
        if number > (key.get("pass") or 0):
            bases.pop(str(number), None)
            owners.pop(str(number), None)
            names = ("report", "review", "notes")
        elif str(number) not in reviews:
            names = ("review",)
        for name in names:
            path = folder / f"{name}-{number}.json"
            if path.exists():
                os.replace(path, folder / f"{name}-{number}.failed.json")
    key["bases"], key["pass_sections"] = bases, owners


def _unsettled(ref: int, row: dict, reviews: dict, mode) -> Optional[str]:
    """Return why a row of a section is not settled yet, or None."""
    outcome = row.get("outcome")
    if outcome in ("skipped", "dropped", "done_before"):
        return None
    if outcome == "question":
        return None if mode == "autonomous" else "question sans réponse"
    if outcome == "fixed":
        number = row.get("pass")
        review = reviews.get(str(number))
        if not isinstance(review, dict):
            return f"sans revue de la passe {number}"
        if ref in (review.get("blocking") or []):
            return f"bloquante à la revue de la passe {number}"
        return None
    return "en attente"


def _section(key: dict, name: str, alias: str) -> dict:
    """Return the section of the key with this alias; exit 2 when there is none."""
    for section in key["sections"]:
        if section["alias"] == alias:
            return section
    known = ", ".join(section["alias"] for section in key["sections"]) or "aucune"
    raise UsageError(f"Section inconnue : {alias} (sections de la clé {name} : {known}).")


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
    closed, base = _report_pass_errors(key, args.number, worktree, services.run)
    entries = []
    if document is not None:
        commits = fix_git.commits_in_range(worktree, services.run, key["start_sha"])
        pass_commits = None
        if base is not None:
            pass_commits = {item["sha"] for item in fix_git.commits_in_range(
                worktree, services.run, base)}
        entries, errors = fix_guard.validate_report(
            document, key=args.key, number=args.number, rows=_pass_rows(key, args.number),
            commits=commits, pass_commits=pass_commits,
            reviewed={int(number) for number in _recorded(key, "reviews")},
            sections={ref: row.get("section") for ref, row in key["rows"].items()},
        )
    errors = closed + errors
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
                attention=entry["attention"], remaining=entry["remaining"],
                **{"pass": args.number},
            )
            # The texts of a new report replace those an earlier review corrected.
            row.pop("corrections", None)
            summary[entry["outcome"]].append(entry["ref"])
        key["step"] = "fixed"
        key["pass"] = max(key.get("pass") or 0, args.number)
        key["reports"] = sorted(set(_reports(key)) | {args.number})
        if _every_pass_reviewed(key):
            _raise_step(key, "reviewed")
    if changed is not None or not errors:
        fix_state.save(run_dir, state)
    _emit({
        "ok": not errors, "errors": errors,
        "rows": {outcome: sorted(refs) for outcome, refs in summary.items()},
        "dev_checkout_changed": changed,
    })
    return EXIT_CONTEXT if errors else EXIT_OK


def _pass_rows(key: dict, number: int) -> dict:
    """Return the rows of the section of pass number (pass-start records it), else every row."""
    alias = _recorded(key, "pass_sections").get(str(number))
    if alias is None:
        return key["rows"]
    return {ref: row for ref, row in key["rows"].items() if row.get("section") == alias}


def _validate_review(run_dir: Path, state: dict, key: dict, args, document, errors: list) -> int:
    """Check review-<N>.json and record its blocking rows (see _every_pass_reviewed) and the
    text corrections of its ok verdicts, in rows[ref]["corrections"]."""
    blocking = []
    if document is not None:
        blocking, errors = fix_guard.validate_review(
            document, key=args.key, number=args.number, rows=key["rows"]
        )
    errors = _review_pass_errors(key, args.number) + errors
    if not errors:
        reviews = _recorded(key, "reviews")
        reviews[str(args.number)] = {"blocking": blocking}
        key["reviews"] = reviews
        corrections = fix_guard.review_corrections(document)
        for item in document["rows"]:
            row = key["rows"][str(item["ref"])]
            if item["ref"] in corrections:
                row["corrections"] = corrections[item["ref"]]
            else:
                row.pop("corrections", None)
        if _every_pass_reviewed(key):
            _raise_step(key, "reviewed")
        fix_state.save(run_dir, state)
    _emit({"ok": not errors, "errors": errors, "blocking": blocking})
    return EXIT_CONTEXT if errors else EXIT_OK


def _cmd_guard(args, services: Services) -> int:
    """Run the guard on the commits of the key, or with --staged on the index of its worktree
    and the message of the commit to come (--message-file); nothing is committed."""
    if args.staged != (args.message_file is not None):
        raise UsageError("--staged et --message-file vont ensemble : l'index du worktree et le "
                         "fichier du message du commit prévu.")
    message = None
    if args.staged:
        try:
            message = _commit_message(args.message_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            raise UsageError(f"--message-file : {args.message_file} est illisible ({error}).")
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    worktree = _key_worktree(run_dir, key, services.run)
    scanner = fix_gitleaks.Gitleaks(services.run, LAUNCHER)
    if message is None:
        problems = _guard_problems(run_dir, key, worktree, services.run, gitleaks=scanner)
        where = f"sur la branche {key['branch']}"
    else:
        problems = _unwaived(key, worktree, services.run,
                             _staged_problems(run_dir, key, worktree, message, services.run,
                                              gitleaks=scanner),
                             staged=True)
        where = "dans l'index et le message prévu, ne commitez pas en l'état"
    _note(scanner)
    _emit({"ok": not problems, "problems": problems, "note": scanner.note})
    if problems:
        print(f"Garde-fou : {len(problems)} problème(s) {where}, détail dans « problems ».",
              file=sys.stderr)
        return EXIT_REFUSED
    return EXIT_OK


def _commit_message(text: str) -> str:
    """Return the message git commit -F would record: trailing spaces and blank lines at
    either end removed (comment lines are kept, as git does without an editor)."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def _staged_problems(run_dir: Path, key: dict, worktree: Path, message: str, run,
                     gitleaks=None) -> list:
    repo = _repo(run_dir, key["repo"])
    _third_party(repo)
    return fix_guard.check_staged(worktree, message, _row_sections(key), repo,
                                  known_values_from_env(), run, gitleaks=gitleaks)


def _note(scanner) -> None:
    """Tell the lead, on stderr, when gitleaks did not run (the home rules ran alone)."""
    if scanner.note:
        print(f"{scanner.note} ({scanner.reason})", file=sys.stderr)


def _cmd_waive_secret(args, services: Services) -> int:
    """Record the lead's waiver of one heuristic secret alert that the guard reports now.

    The alert is named by the sha of its commit in <start_sha>..HEAD (or the blob of its path
    in the index, as guard --staged prints it), its path, line and label. Only a label of
    fix_guard.HEURISTIC_SECRET_LABELS may be waived. The waiver is keyed on the blob of the
    path (its content): it holds while the file is unchanged, in whatever commit a drop
    replays it, and ends with any change of the file. It keeps no text of the line.
    """
    heuristic = sorted(fix_guard.HEURISTIC_SECRET_LABELS)
    if args.label not in fix_guard.HEURISTIC_SECRET_LABELS:
        raise UsageError(f"--label {args.label} : seule une alerte heuristique "
                         f"({', '.join(heuristic)}) peut être levée ; un autre secret ne "
                         "l'est jamais, son correctif est retiré.")
    if args.line < 1:
        raise UsageError("--line : un numéro de ligne à partir de 1 est attendu.")
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    worktree = _key_worktree(run_dir, key, services.run)
    commits = {item["sha"] for item in fix_git.commits_in_range(
        worktree, services.run, key["start_sha"])}
    if args.sha in commits:
        problems = _guard_problems(run_dir, key, worktree, services.run, waivers=False)
        blob = _blob_of(worktree, services.run, args.sha, args.path)
    elif fix_git.is_sha(args.sha) and args.sha == fix_names.git_out(
            worktree, services.run, "rev-parse", "--verify", "--quiet", f":{args.path}"):
        problems = _staged_problems(run_dir, key, worktree, "", services.run)
        blob = args.sha
    else:
        raise UsageError(f"--sha {args.sha} : ni un commit de {key['start_sha'][:12]}..HEAD, ni "
                         f"le blob indexé de {args.path}. Rien n'a été levé.")
    wanted = {"sha": args.sha, "path": args.path, "line": args.line, "label": args.label}
    if blob is None or not any(
        item["waivable"] and all(item[name] == value for name, value in wanted.items())
        for item in problems
    ):
        raise UsageError(f"La garde ne signale pas {args.label} à la ligne {args.line} de "
                         f"{args.path} ({args.sha[:12]}) : rien n'a été levé.")
    wanted = {"blob": blob, "path": args.path, "line": args.line, "label": args.label}
    waivers = _secret_waivers(key)
    found = next((item for item in waivers
                  if all(item.get(name) == value for name, value in wanted.items())), None)
    if found is None:
        found = {**wanted, "at": fix_state.now()}
        waivers.append(found)
        key["secret_waivers"] = waivers
        fix_state.save(run_dir, state)
    _emit({"waiver": found, "waivers": len(waivers)})
    return EXIT_OK


def _secret_waivers(key: dict) -> list:
    """Return the secret waivers of the key ([] when absent; fix_state.load checked them)."""
    return list(key.get("secret_waivers") or [])


def _blob_of(worktree: Path, run, sha, path) -> Optional[str]:
    """Return the blob of path in commit sha, None when unknown."""
    if not fix_git.is_sha(sha) or not path:
        return None
    return fix_names.git_out(worktree, run, "rev-parse", "--verify", "--quiet", f"{sha}:{path}")


def _unwaived(key: dict, worktree: Path, run, problems: list, *, staged: bool = False) -> list:
    """Return the problems that no secret waiver of the key covers (fix_guard.unwaived): the
    blob of a problem is that of its path in its commit, or its sha itself when staged (the
    blob of the index, as check_staged sets it)."""
    waivers = _secret_waivers(key)
    if not waivers:
        return problems
    if staged:
        return fix_guard.unwaived(problems, waivers, lambda sha, path: sha)
    return fix_guard.unwaived(problems, waivers, lambda sha, path: _blob_of(worktree, run, sha, path))


def _row_sections(key: dict) -> dict:
    """Return {ref: alias of its section} for every row of the key."""
    return {int(ref): row.get("section") for ref, row in key["rows"].items()}


def _cmd_review_scope(args, services: Services) -> int:
    """Print whether review N may be light: the diff of bases[N]..HEAD is documentation only
    and fix_guard.LIGHT_REVIEW_LINES lines at most (see fix_guard.review_scope)."""
    run_dir = args.run_dir.resolve()
    if args.number < 1:
        raise UsageError("--pass : un numéro de passe à partir de 1 est attendu.")
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    base = _recorded(key, "bases").get(str(args.number))
    if not fix_git.is_sha(base):
        raise UsageError(f"Passe {args.number} non commencée : lancez d'abord cqa_fix.py "
                         f"pass-start --pass {args.number}.")
    worktree = _key_worktree(run_dir, key, services.run)
    if not fix_git.is_ancestor(worktree, services.run, base, "HEAD"):
        raise UsageError(f"La base de la passe {args.number} ({base[:12]}) n'est plus dans la "
                         f"branche : relancez cqa_fix.py pass-start --pass {args.number}.")
    _emit(fix_guard.review_scope(worktree, services.run, base))
    return EXIT_OK


def _cmd_check(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    worktree = _key_worktree(run_dir, key, services.run)
    third_party = _third_party(_repo(run_dir, key["repo"]))
    folder = _key_folder(run_dir, key)
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
        try:
            checks = fix_checks.run_checks(
                services.run, LAUNCHER, check_dir, worktree, key["repo"], files, added, third_party,
                start_sha=key["start_sha"],
            )
        except ValueError as error:
            raise UsageError(str(error))
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


def _reason(text: str) -> str:
    """Return the --reason of a drop, stripped; exit 2 when empty or too long."""
    reason = text.strip()
    if not reason or len(reason) > fix_guard.MAX_REASON:
        raise UsageError(
            f"--reason : une raison en français de {fix_guard.MAX_REASON} caractères au plus "
            "est attendue."
        )
    return reason


def _cmd_drop(args, services: Services) -> int:
    """Remove rows from the key's branch (fix_git.drop_commits) and record why.

    A row taken along by the drop (also_dropped) gets one retry (_settle_drops).
    """
    run_dir = args.run_dir.resolve()
    refs = parse_refs(args.refs)
    if not refs:
        raise UsageError("--refs : au moins une Réf est attendue (CQ-3).")
    reason = _reason(args.reason)
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    _not_left(key, args.key, "drop")
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
    allowed, linked = _drop_scope(rows)
    before = _range_shas(worktree, services.run, key)
    result = fix_git.drop_commits(
        worktree, services.run, branch=key["branch"], start_sha=key["start_sha"], refs=set(refs),
        linked=linked, allowed=allowed,
    )
    also, retry = _settle_drops(
        state, key, refs, result["dropped"], reason,
        f"retirée avec {_labels(refs)} : son correctif dépend d'un commit retiré",
    )
    _remap(worktree, services.run, key, before, result["rewritten"])
    if _every_pass_reviewed(key):
        _raise_step(key, "reviewed")
    fix_state.save(run_dir, state)
    if result["unattached"]:
        raise fix_git.GitRefused(result["unattached"])
    _emit({"dropped": refs, "also_dropped": also, "retry": retry})
    return EXIT_OK


def _drop_scope(rows: dict) -> tuple:
    """Return (allowed, linked) for drop_commits: the refs a drop may take (all but done_before)
    and their recorded commits."""
    allowed = {int(ref) for ref, row in rows.items() if row.get("outcome") != "done_before"}
    linked = {int(ref): rows[ref].get("commits") or [] for ref in rows if int(ref) in allowed}
    return allowed, linked


def _settle_drops(state: dict, key: dict, refs: list, dropped: set, reason: str,
                  ricochet: str) -> tuple:
    """Record a drop in the rows and return (also_dropped, retry), sorted.

    The rows of refs leave the branch with reason. A row taken along (its fix depended on a
    removed commit) goes back to pending once, with ricochet as its reason and retry true,
    for one more pass of the fixer in its section (_can_retry); it leaves for good with that
    reason when it already had its retry, or when its section can no longer run a pass. The
    SHAs removed from every row stay on it (dropped_commits, ruling P11): the summary gives
    the command that keeps them.
    """
    also, retry = sorted(set(dropped) - set(refs)), []
    for ref in sorted(dropped):
        row = key["rows"][str(ref)]
        removed = [sha for sha in row.get("commits") or [] if isinstance(sha, str)]
        if removed:
            kept = [sha for sha in row.get("dropped_commits") or [] if isinstance(sha, str)]
            row["dropped_commits"] = kept + [sha for sha in removed if sha not in kept]
        if ref in refs:
            row.update(outcome="dropped", commits=[], reason=reason)
        elif not row.get("retry") and _can_retry(state, key, row):
            row.update(outcome="pending", commits=[], reason=ricochet, retry=True, attention=None)
            row.pop("remaining", None)
            row.pop("corrections", None)
            retry.append(ref)
        else:
            row.update(outcome="dropped", commits=[], reason=ricochet)
    return also, retry


def _can_retry(state: dict, key: dict, row: dict) -> bool:
    """True when the section of row may still run a pass of the fixer: not started yet, in
    progress, or settled at the questions stage (_answers_questions)."""
    section = next((item for item in key["sections"] if item["alias"] == row.get("section")),
                   None)
    if section is None:
        return False
    if section["state"] in ("pending", "active"):
        return True
    return section["state"] == "settled" and state["stage"] == "questions"


def _range_shas(worktree: Path, run, key: dict) -> list:
    """Return the SHAs of <start_sha>..HEAD, oldest first (before a rewrite, for _remap)."""
    return [item["sha"] for item in fix_git.commits_in_range(worktree, run, key["start_sha"])]


def _remap(worktree: Path, run, key: dict, before: list, rewritten: dict) -> None:
    """Point the SHAs that the key records at its rewritten branch: the commits of the rows,
    the bases of the passes and the head of a taken-over branch (_remapped).

    before lists <start_sha>..HEAD before the rewrite. start_sha never moves; reviews hold
    refs only, and secret waivers are keyed on blobs: neither changes. The stamp of the MR
    description (mr_body) keeps the old head, so that publish asks for a new one.
    """
    for row in key["rows"].values():
        if isinstance(row, dict):
            row["commits"] = [rewritten.get(sha, sha) for sha in row.get("commits") or []]
    bases, taken = _recorded(key, "bases"), key.get("taken_head")
    if not bases and not fix_git.is_sha(taken):
        return
    after = _branch_commits(worktree, run, key["start_sha"], key["branch"])
    if bases:
        key["bases"] = {
            number: _remapped(base, before, after, rewritten, key["start_sha"])
            for number, base in bases.items()
        }
    if fix_git.is_sha(taken):
        key["taken_head"] = _remapped(taken, before, after, rewritten, key["start_sha"])


def _cmd_reorder(args, services: Services) -> int:
    """Group the commits of the key by section, in the order of its sections, before the
    publication (fix_git.reorder_commits): the commits of a section keep their order, and
    pushed commits or those of a taken-over branch never move.

    A conflict changes nothing in git: the subjects of the commits that did not replay go to
    reorder_conflicts in the state, for the summary; a reorder that goes through, or finds
    nothing to move, clears it. rewritten maps the old SHAs to the new ones. Never while a
    pass is in progress.
    """
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    _not_left(key, args.key, "reorder")
    _no_pass_in_progress(key, "ranger les commits")
    worktree = _key_worktree(run_dir, key, services.run)
    commits = fix_git.commits_in_range(worktree, services.run, key["start_sha"])
    result = fix_git.reorder_commits(
        worktree, services.run, branch=key["branch"], start_sha=key["start_sha"],
        keep=key.get("taken_head"), rank=_section_rank(key),
    )
    if result["conflicts"]:
        subjects = {item["sha"]: item["message"].split("\n", 1)[0] for item in commits}
        key["reorder_conflicts"] = [subjects.get(sha, sha) for sha in result["conflicts"]]
    else:
        key.pop("reorder_conflicts", None)
        _remap(worktree, services.run, key, [item["sha"] for item in commits],
               result["rewritten"])
    fix_state.save(run_dir, state)
    if result["unattached"]:
        raise fix_git.GitRefused(result["unattached"])
    _emit({"reordered": result["reordered"], "conflicts": result["conflicts"],
           "rewritten": result["rewritten"]})
    return EXIT_OK


def _no_pass_in_progress(key: dict, what: str) -> None:
    """Exit 2 while a pass is started without a validated report (_pass_in_progress)."""
    started = _pass_in_progress(key)
    if started is not None:
        raise UsageError(f"La passe {started} est commencée sans rapport validé : validez-le "
                         f"avant de {what}. Rien n'a changé.")


def _section_rank(key: dict):
    """Return rank(commit) for reorder_commits: the place in the key of the section of the row
    that its subject names, else of the first row that lists it in its commits, else of the
    section alias of its subject; None when none is a section of the key."""
    ranks = {}
    for index, section in enumerate(key["sections"]):
        ranks.setdefault(section["alias"], index)
    owners = {}
    for section in key["sections"]:
        for ref in section["refs"]:
            for sha in (key["rows"].get(str(ref)) or {}).get("commits") or []:
                owners.setdefault(sha, ranks[section["alias"]])

    def rank(commit: dict) -> Optional[int]:
        row = key["rows"].get(str(commit["ref"])) if commit["ref"] is not None else None
        if isinstance(row, dict) and row.get("section") in ranks:
            return ranks[row["section"]]
        if commit["sha"] in owners:
            return owners[commit["sha"]]
        parsed = fix_names.parse_subject(commit["message"].split("\n", 1)[0])
        return ranks.get(parsed[0]) if parsed else None

    return rank


def _cmd_reword(args, services: Services) -> int:
    """Rewrite the message of one unpushed commit of the key (fix_git.reword_commit), the
    content of every commit unchanged, to settle a form problem of the guard before the push.

    The new message (--message-file, read as git commit -F would) goes through the guard's
    checks of a message first (fix_guard.check_message: convention, section, length,
    attribution, secrets, gitleaks): a problem exits 5 with {"ok", "problems", "note"} and
    nothing changes. The subject keeps the ref of its row: only a subject without a ref of
    the key (a form problem) may take another one. A commit of a taken-over branch (up to
    taken_head) is never reworded, and nothing is while a pass is in progress. rewritten maps
    the old SHAs to the new ones.
    """
    try:
        message = _commit_message(args.message_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise UsageError(f"--message-file : {args.message_file} est illisible ({error}).")
    if not message:
        raise UsageError("--message-file : le message est vide.")
    if not re.fullmatch(r"[0-9a-fA-F]{4,64}", args.commit):
        raise UsageError(f"--commit {args.commit} : un SHA de commit est attendu.")
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    _not_left(key, args.key, "reword")
    _no_pass_in_progress(key, "réécrire un message")
    run = services.run
    worktree = _key_worktree(run_dir, key, run)
    commits = fix_git.commits_in_range(worktree, run, key["start_sha"])
    sha = fix_names.git_out(worktree, run, "rev-parse", "--verify", "--quiet",
                            f"{args.commit}^{{commit}}")
    old = next((item for item in commits if item["sha"] == sha), None)
    if old is None:
        raise UsageError(f"--commit {args.commit} : ce n'est pas un commit de "
                         f"{key['start_sha'][:12]}..HEAD de la branche {key['branch']}. "
                         "Rien n'a changé.")
    new_ref = fix_names.ref_of_subject(message.split("\n", 1)[0])
    if str(old["ref"]) in key["rows"] and new_ref != old["ref"]:
        raise fix_git.Refused(
            f"Le nouveau sujet porte {ref_label(new_ref) if new_ref else 'aucune Réf'} au lieu "
            f"de {ref_label(old['ref'])} : le message réécrit garde la Réf de sa ligne (seule une "
            "Réf hors de la clé peut changer). Rien n'a changé."
        )
    scanner = fix_gitleaks.Gitleaks(run, LAUNCHER)
    problems = fix_guard.check_message(message, _row_sections(key), known_values_from_env(),
                                       gitleaks=scanner)
    _note(scanner)
    if problems:
        _emit({"ok": False, "problems": problems, "note": scanner.note})
        print(f"Garde-fou : {len(problems)} problème(s) dans le nouveau message, détail dans "
              "« problems ». Rien n'a été réécrit.", file=sys.stderr)
        return EXIT_REFUSED
    result = fix_git.reword_commit(worktree, run, branch=key["branch"],
                                   start_sha=key["start_sha"], sha=sha, message=message,
                                   keep=key.get("taken_head"))
    _remap(worktree, run, key, [item["sha"] for item in commits], result["rewritten"])
    fix_state.save(run_dir, state)
    if result["unattached"]:
        raise fix_git.GitRefused(result["unattached"])
    _emit({"reworded": bool(result["rewritten"]), "commit": result["commit"],
           "rewritten": result["rewritten"], "note": scanner.note})
    return EXIT_OK


def _branch_commits(worktree: Path, run, start_sha: str, branch: str) -> set:
    """Return the SHAs of <start_sha>..<branch>, read on the branch itself."""
    listing = fix_names.git(worktree, run, "rev-list", f"{start_sha}..refs/heads/{branch}")
    if listing.returncode != 0:
        raise fix_git.GitRefused(f"git rev-list a échoué dans {worktree} : {listing.stderr.strip()}")
    return set(listing.stdout.split())


def _remapped(base: str, before: list, after: set, rewritten: dict, start_sha: str) -> str:
    """Return where a pass base (or a taken-over head) lands after a rewrite of the branch.

    before lists <start_sha>..HEAD before the rewrite, oldest first (linear: every rewrite
    refuses merges); after holds the commits of the rebuilt branch. A kept commit maps to its new SHA (or stays);
    a dropped one to the new SHA of its nearest kept first-parent ancestor, else start_sha. A
    base outside the range (start_sha or older) does not move.
    """
    position = {sha: index for index, sha in enumerate(before)}
    index = position.get(base)
    if index is None:
        return rewritten.get(base, base)
    while index >= 0:
        sha = before[index]
        if sha in rewritten:
            return rewritten[sha]
        if sha in after:
            return sha
        index -= 1
    return start_sha


def _report_pass_errors(key: dict, number: int, worktree: Path, run) -> tuple:
    """Return (errors, base) for report <number>: the pass must be started (pass-start), its
    base still in the branch, and the pass still open (no later report, no review of it).
    base is None when the pass cannot bound the commits of the report.
    """
    errors = []
    base = _recorded(key, "bases").get(str(number))
    if not fix_git.is_sha(base):
        errors.append(f"passe {number} non commencée : lancez d'abord cqa_fix.py pass-start "
                      f"--pass {number}")
        base = None
    elif not fix_git.is_ancestor(worktree, run, base, "HEAD"):
        errors.append(f"la base de la passe {number} ({base[:12]}) n'est plus dans la branche : "
                      f"relancez cqa_fix.py pass-start --pass {number}")
        base = None
    reported = key.get("pass") or 0
    if number < reported:
        errors.append(f"la passe {number} est close : le rapport de la passe {reported} est "
                      "déjà validé")
    if str(number) in _recorded(key, "reviews"):
        errors.append(f"la passe {number} est close : sa revue est enregistrée")
    return errors, base


def _review_pass_errors(key: dict, number: int) -> list:
    """Return why review <number> may not be recorded: its pass started, its report validated."""
    errors = []
    if not fix_git.is_sha(_recorded(key, "bases").get(str(number))):
        errors.append(f"passe {number} non commencée : lancez d'abord cqa_fix.py pass-start "
                      f"--pass {number}")
    reported = number in _reports(key) or any(
        isinstance(row, dict) and row.get("pass") == number for row in key["rows"].values()
    )
    if number > (key.get("pass") or 0) or not reported:
        errors.append(f"le rapport de la passe {number} n'est pas validé : validez-le avant la "
                      "revue")
    return errors


def _reports(key: dict) -> list:
    """Return the passes whose report is validated ("reports" of the key, [] when absent)."""
    value = key.get("reports")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        raise fix_state.StateError(
            f"{fix_state.STATE_FILE} : « reports » doit être une liste de numéros de passe."
        )
    return list(value)


def _every_pass_reviewed(key: dict) -> bool:
    """True when the key may be reviewed (R14): no pass is in progress, no row is pending and,
    for every pass p with rows fixed in it, review p is recorded and blocks none of them.

    A pass is in progress from pass-start until its report is validated (R16): a base recorded
    for a pass later than every validated report. A row fixed again in a later pass belongs to
    that pass: an earlier blocking verdict on it no longer counts. A dropped row is no longer
    fixed.
    """
    if _pass_in_progress(key) is not None:
        return False
    rows = [row for row in key["rows"].values() if isinstance(row, dict)]
    if any(row.get("outcome") == "pending" for row in rows):
        return False
    reviews = _recorded(key, "reviews")
    fixed = {}
    for ref, row in key["rows"].items():
        if isinstance(row, dict) and row.get("outcome") == "fixed":
            fixed.setdefault(str(row.get("pass") or 0), set()).add(int(ref))
    for number, refs in fixed.items():
        review = reviews.get(number)
        if not isinstance(review, dict) or refs & set(review.get("blocking") or []):
            return False
    return True


def _raise_step(key: dict, step: str) -> None:
    """Move the key forward to step, never back."""
    steps = fix_state.STEPS
    if steps.index(key.get("step", "started")) < steps.index(step):
        key["step"] = step


def _recorded(key: dict, name: str) -> dict:
    """Return the "bases" or "reviews" of a key ({} when absent), checked to be keyed by pass."""
    value = key.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(str(number).isdigit() for number in value):
        raise fix_state.StateError(
            f"{fix_state.STATE_FILE} : « {name} » doit être un objet indexé par numéro de passe."
        )
    return dict(value)


def _cmd_mr_body(args, services: Services) -> int:
    """Write mr-title.txt and mr.md, or mr-addition.md when the key already has an MR.

    One MR per pass and per repo: every section of the key must be settled or failed. The
    description groups its rows by section, in the order of the commits (fix_mr.material).
    """
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    _not_left(key, args.key, "mr-body")
    _need_reviewed(key, args.key, "mr-body")
    _need_settled(key, args.key, "mr-body")
    context = _load_context(run_dir)
    run_id = _run_id(run_dir, context)
    worktree = _key_worktree(run_dir, key, services.run)
    candidates = _read_run_json(run_dir, "fix/candidates.json", "cqa_fix.py candidates")
    try:
        changed = fix_checks.changed_files(worktree, key["start_sha"], services.run)
    except ValueError as error:
        raise UsageError(str(error))
    branch_refs = fix_git.refs_between(
        worktree, services.run, f"refs/remotes/origin/{key['base']}", "HEAD"
    )
    since = (key.get("published_pass") or 0) if key.get("mr") else 0
    order = [item["sha"] for item in fix_git.commits_in_range(worktree, services.run,
                                                              key["start_sha"])]
    found = fix_mr.material(
        key, candidates, _notion_rows(run_dir), _numbered(run_dir, key, "report"),
        _numbered(run_dir, key, "review"), _optional_key_json(run_dir, key, "checks.json"),
        changed, set(branch_refs), since=since, order=order,
    )
    if key.get("mr"):
        text = fix_mr.addition(**found)
        name, stale, kind = "mr-addition.md", "mr.md", "update"
    else:
        notion = context.get("notion") if isinstance(context.get("notion"), dict) else {}
        text = fix_mr.body(
            run_id=run_id, copy_url=fix_mr.notion_url(notion.get("copy_page_id")), **found,
        )
        name, stale, kind = "mr.md", "mr-addition.md", "create"
    title = fix_mr.title(*fix_mr.title_counts(found, earlier=kind == "create"))
    _refuse_secrets(f"{title}\n{text}", "rien n'a été écrit")
    folder = _key_folder(run_dir, key)
    write_text(folder / "mr-title.txt", title + "\n")
    write_text(folder / name, text)
    if (folder / stale).exists():
        (folder / stale).unlink()
    key["mr_body"] = _body_stamp(run_dir, key, worktree, services.run)
    fix_state.save(run_dir, state)
    _emit({
        "title": title, "file": (folder / name).relative_to(run_dir).as_posix(), "kind": kind,
        "sections": [section["alias"] for section in found["sections"]],
        "fixed": [row["ref"] for row in found["fixed"]],
        "partial": [row["ref"] for row in found["fixed"] if row["remaining"]],
        "covered": [row["ref"] for row in found["covered"]],
        "waiting": [row["ref"] for row in found["waiting"]],
        "out_of_scope": [row["ref"] for row in found["left"]],
    })
    return EXIT_OK


def _cmd_publish(args, services: Services) -> int:
    """Push the key's branch, then create its draft MR or add to the description of its MR.

    Nothing leaves the machine before every check passed: local pass, autonomous pass still
    in its key loop, step, branch name, state of the MR on GitLab, fresh description files,
    secrets, guard, every new commit reported by the fixer, and a remote branch that HEAD
    contains. The push is a fast-forward of that one branch, never forced. Rows covered by
    the fix of another row ship in the MR, so they get its link too. Every section of the key
    must be settled or failed (one MR per pass and per repo). Each link of fix/mr-links.json
    says whether its row is partial, and its status at launch (fix_mr.link_details).
    """
    run = services.run
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    _not_left(key, args.key, "publish")
    branch = key["branch"]
    _refuse_local(state, key)
    if state.get("mode") == "autonomous" and state["stage"] == "keys":
        raise fix_git.Refused(AUTONOMOUS_KEYS)
    _need_reviewed(key, args.key, "publish")
    _need_settled(key, args.key, "publish")
    if not fix_names.is_pass_branch(branch, fix_names.BRANCH_BASE):
        raise fix_git.Refused(
            f"La branche {branch} n'est pas une branche de correctif "
            f"({fix_names.BRANCH_BASE}) : rien n'a été poussé."
        )
    worktree = _key_worktree(run_dir, key, run)
    links = _read_links(run_dir)
    candidates = _read_run_json(run_dir, "fix/candidates.json", "cqa_fix.py candidates")
    if key.get("mr") is None and _ahead_of_base(worktree, run, key["base"]) == 0:
        _published(run_dir, state, key, empty=True)
        _emit({"branch": branch, "head": fix_git.rev(worktree, run, "HEAD"), "pushed": False,
               "mr": None, "created": False, "updated": False, "links": [],
               "note": NOTHING_TO_PUBLISH})
        return EXIT_OK
    host, project, user = _gitlab_of(run_dir, key["repo"])
    encoded = quote(project, safe="")
    project_ref = f"{host}/{project}"
    folder = _key_folder(run_dir, key)
    current, adopted = None, False
    if key.get("mr") is not None:
        current = _open_mr(run, host, encoded, key["mr"], branch)
    else:
        found = _find_open_mr(run, host, encoded, branch)
        if found is not None:
            key["mr"] = {"iid": found["iid"], "url": found["web_url"]}
            ours = _read_text(folder / "mr.md")
            adopted = ours is not None and fix_mr.same_description(
                found.get("description") or "", ours
            )
            if not adopted:
                fix_state.save(run_dir, state)
                raise UsageError(
                    f"La MR !{found['iid']} est déjà ouverte pour la branche {branch} : relancez "
                    "cqa_fix.py mr-body (ajout à sa description), puis cqa_fix.py publish. "
                    "Rien n'a été poussé."
                )
    title = None
    if current is not None:
        text = _read_text(folder / "mr-addition.md")
        if text is None:
            raise UsageError(NEED_BODY)
    else:
        text, title = _read_text(folder / "mr.md"), _read_text(folder / "mr-title.txt")
        if text is None or not (title or "").strip():
            raise UsageError(NEED_BODY)
        title = title.strip()
    if key.get("mr_body") != _body_stamp(run_dir, key, worktree, run):
        raise UsageError(STALE_BODY)
    _refuse_secrets(text if title is None else f"{title}\n{text}", "rien n'a été poussé")
    scanner = fix_gitleaks.Gitleaks(run, LAUNCHER)
    problems = _guard_problems(run_dir, key, worktree, run, gitleaks=scanner)
    _note(scanner)
    if problems:
        _emit({"ok": False, "problems": problems, "note": scanner.note})
        print(f"Garde-fou : {len(problems)} problème(s) sur la branche {branch}, rien n'a été "
              "poussé : détail dans « problems ».", file=sys.stderr)
        return EXIT_REFUSED
    unreported = _unreported(worktree, key, run)
    if unreported:
        lines = [UNREPORTED.format(sha=item["sha"][:12], subject=item["message"].split("\n", 1)[0])
                 for item in unreported]
        raise fix_git.Refused("\n".join(lines + [NOTHING_PUSHED]))
    head = fix_git.rev(worktree, run, "HEAD")
    remote = _remote_head(worktree, run, branch)
    if remote is not None and not fix_git.is_ancestor(worktree, run, remote, head):
        raise fix_git.Refused(DIVERGED)
    pushed = remote != head
    if pushed:
        _push(worktree, run, branch)
    created = updated = False
    if key.get("mr") is None:
        # Recorded before the MR exists: a branch pushed without MR is not a local branch.
        # A taken-over MR needs no such record: the branch is already on GitLab with it.
        key["pushed"] = True
        fix_state.save(run_dir, state)
        # The state is written once, with the MR and the step: after a failure from here,
        # a rerun finds this MR on GitLab and adopts it instead of describing it twice.
        key["mr"] = _create_mr(run, host, encoded, worktree, project_ref, branch=branch,
                               base=key["base"], title=title, text=text, user=user)
        created = True
    elif current is not None:
        updated = _add_to_mr(run, host, worktree, project_ref, key["mr"]["iid"], current, text)
    refs = sorted({int(ref) for ref, row in key["rows"].items()
                   if row.get("outcome") in ("fixed", "done_before")}
                  | set(fix_mr.covered_refs(key["rows"])))
    details = fix_mr.link_details(key, candidates, _notion_rows(run_dir), refs)
    write_json(run_dir / LINKS_FILE,
               fix_mr.merge_links(links, key["repo"], key["mr"], refs, details))
    _published(run_dir, state, key)
    _emit({"branch": branch, "head": head, "pushed": pushed, "mr": key["mr"], "created": created,
           "updated": updated, "links": refs, "assignee": user if created else None})
    return EXIT_OK


def _cmd_finish(args, services: Services) -> int:
    """Remove the worktree of a published key, or of a reviewed one whose branch stays local.

    --keep-local lets a reviewed key of a pushing pass end without publication (push refused,
    glab failure, lead's choice): its branch stays in the dev's repo, kept_local in the state.
    A branch the pass created and left without any commit is then deleted
    (_delete_empty_branch): branch_deleted in the output and in the state; otherwise
    branch_kept says why the branch stays.
    """
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _started_key(state, args.key)
    _not_left(key, args.key, "finish --keep-local" if args.keep_local else "finish")
    step = key.get("step")
    if args.keep_local and step == "published":
        raise UsageError(f"La clé {args.key} est déjà publiée : lancez finish sans --keep-local.")
    kept_local = (state["push"] in ("local", "dry_run") or not key.get("push_ready")
                  or args.keep_local)
    if step != "published" and not (step == "reviewed" and kept_local):
        raise UsageError(
            f"La clé {args.key} est à l'étape « {step} » : finish attend « published », ou "
            "« reviewed » quand la branche reste en local (--keep-local après un push refusé)."
        )
    path = _repo_path(run_dir, key["repo"])
    worktree = _worktree_path(run_dir, key)
    removed = None
    if worktree.exists():
        _key_worktree(run_dir, key, services.run)
        status = fix_names.git(worktree, services.run, "status", "--porcelain=v1", "-uall")
        if status.returncode != 0:
            raise fix_git.GitRefused(f"git status a échoué dans {worktree} : {status.stderr.strip()}")
        if status.stdout.strip():
            raise fix_git.Refused(
                f"Le worktree {worktree} a des modifications non commitées : commitez-les ou "
                "retirez-les, puis relancez. Rien n'a été retiré."
            )
        fix_git.remove_worktree(path, services.run, worktree)
        removed = str(worktree)
    kept = _delete_empty_branch(path, services.run, key)
    key["step"] = "done"
    key["kept_local"] = kept_local
    if kept is None:
        key["branch_deleted"] = True
    fix_state.save(run_dir, state)
    _emit({"removed": removed, "branch": key["branch"], "kept_local": kept_local,
           "branch_deleted": kept is None, "branch_kept": kept,
           "unpushed": fix_git.unpushed(path, services.run, key["branch"])})
    return EXIT_OK


def _delete_empty_branch(path, run, key: dict) -> Optional[str]:
    """Delete the key's local branch when this pass created it and left it without any
    commit; return None once deleted, else why it stays (in French).

    Only a pass branch that the pass created (never a taken-over one: reused or taken_head,
    whatever its head), whose head is still start_sha (the origin/<base> it started from),
    whose reflog shows no move since its creation (a pass that committed, then dropped
    everything, keeps its branch: the dropped commits stay reachable through its reflog,
    ruling P11) and whose commits are all on origin, checked out in no worktree (the dev's
    own checkout included) and absent from origin goes. git update-ref deletes it only while
    it points at start_sha, so a branch with a commit is never deleted. When origin cannot be
    asked, the branch stays.
    """
    branch, start = key["branch"], key["start_sha"]
    if key.get("reused") or key.get("taken_head"):
        return "branche reprise d'une passe précédente : jamais supprimée"
    if not any(fix_names.is_pass_branch(branch, base)
               for base in (fix_names.BRANCH_BASE, fix_names.DRY_RUN_BASE)):
        return "ce n'est pas une branche de passe"
    head = fix_git.rev(path, run, f"refs/heads/{branch}")
    if head is None:
        return "branche introuvable dans le repo"
    if head != start:
        return "la branche porte des commits de la passe"
    moved = _reflog_moved(path, run, branch)
    if moved is None:
        return "git reflog show a échoué : branche gardée"
    if moved:
        return "la branche a porté des commits retirés (reflog) : gardée, voir la synthèse"
    if fix_git.unpushed(path, run, branch):
        return "la branche porte des commits absents d'origin"
    try:
        checked_out = any(entry["branch"] == branch for entry in fix_git.worktrees(path, run))
    except fix_git.GitRefused:
        return "git worktree list a échoué : branche gardée"
    if checked_out:
        return "branche extraite dans un worktree ou dans votre repo"
    try:
        on_origin = fix_git.remote_has(path, run, branch)
    except fix_git.GitRefused:
        return "origin injoignable : branche gardée"
    if on_origin:
        return "branche présente sur origin"
    deleted = fix_names.git(path, run, "update-ref", "-d", f"refs/heads/{branch}", start)
    if deleted.returncode != 0:
        return f"git update-ref a échoué : {deleted.stderr.strip()}"
    return None


def _reflog_moved(path, run, branch: str) -> Optional[bool]:
    """True when the reflog of the branch holds an entry after its creation: the branch
    pointed at a commit of the pass once (a drop put it back), and that commit is reachable
    only through this reflog. A branch without reflog counts as never moved; None when git
    could not read it (the caller keeps the branch)."""
    listing = fix_names.git(path, run, "reflog", "show", "--format=%H", f"refs/heads/{branch}")
    if listing.returncode != 0:
        return None
    return len(listing.stdout.split()) > 1


def _cmd_leave(args, services: Services) -> int:
    """Leave the key to the lead (--reason), or take it back (--clear).

    A left key (a merge commit or a rewritten history in its branch, a failed section that
    could not be rebuilt, a start that failed) keeps its branch and its worktree as they are:
    start, reorder, reword, mr-body, publish and finish refuse it (exit 2) until --clear, and
    the summary names it with its reason and its rows. A key at « selected » (its start
    failed: no branch to protect) is accepted. The record is key["left"] = {"reason", "at"}
    in fix/state.json, so it lives and dies with the pass. Both forms are idempotent: changed
    says whether the state moved; the same reason again keeps the first record.
    """
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    key = _key(state, args.key)
    if key.get("branch"):
        _started_key(state, args.key)
    current = key.get("left") if fix_state.is_left(key) else None
    if args.clear:
        changed = current is not None
        key["left"] = None
    else:
        reason = _reason(args.reason)
        if key.get("step") == "done":
            raise UsageError(f"La clé {args.key} est terminée (étape « done ») : plus rien à "
                             "laisser au lead.")
        changed = current is None or current.get("reason") != reason
        if changed:
            key["left"] = {"reason": reason, "at": fix_state.now()}
    if changed:
        fix_state.save(run_dir, state)
    _emit({"key": args.key, "left": key.get("left"), "changed": changed,
           "branch": key.get("branch"), "worktree": key.get("worktree")})
    return EXIT_OK


def _not_left(key: dict, name: str, command: str) -> None:
    """Exit 2 for a key left to the lead (cqa_fix.py leave): nothing of it moves until --clear."""
    if fix_state.is_left(key):
        raise UsageError(LEFT_KEY.format(name=name, reason=key["left"]["reason"], command=command))


def _need_reviewed(key: dict, name: str, command: str) -> None:
    if key.get("step") != "reviewed":
        raise UsageError(
            f"La clé {name} est à l'étape « {key.get('step')} » : {command} attend « reviewed » "
            "(revue sans ligne bloquante)."
        )


def _need_settled(key: dict, name: str, command: str) -> None:
    """Exit 2 while a section of the key is pending or in progress: one MR per pass and per
    repo carries every section, each settled or failed (section-end)."""
    for section in key["sections"]:
        if section["state"] not in ("settled", "failed"):
            raise UsageError(
                f"La section {section['alias']} est à l'état « {section['state']} » : {command} "
                f"attend chaque section de la clé {name} réglée ou en échec (cqa_fix.py "
                "section-end)."
            )


def _refuse_local(state: dict, key: dict) -> None:
    """Refuse a push the lead did not agree to: local or dry-run pass, repo not ready."""
    kept = f"rien n'est poussé, la branche {key['branch']} reste dans votre repo."
    if state["push"] == "local":
        raise fix_git.Refused(f"Passe en branches locales : {kept}")
    if state["push"] == "dry_run":
        raise fix_git.Refused(f"Passe --dry-run : {kept}")
    if not key.get("push_ready"):
        raise fix_git.Refused(
            f"Le repo {key['repo']} n'est pas prêt pour GitLab (voir cqa_fix.py doctor) : {kept}"
        )


def _run_id(run_dir: Path, context: dict) -> str:
    """Return the run id (AAAA-MM-JJ-HHMM): the run folder name, else context.json run_id."""
    for value in (run_dir.name, context.get("run_id")):
        try:
            run_start(value)
        except ValueError:
            continue
        return value
    raise ContextError(
        f"Le dossier du run {run_dir.name} ne porte pas de run_id AAAA-MM-JJ-HHMM : "
        "la date de l'audit est inconnue."
    )


def _key_folder(run_dir: Path, key: dict) -> Path:
    """Return fix/<repo>, or fix/<repo>/<slug> for a key of a v2 pass."""
    folder = run_dir / "fix" / key["repo"]
    return folder / key["slug"] if "slug" in key else folder


def _numbered(run_dir: Path, key: dict, kind: str) -> list:
    """Return [(N, document)] of the report-<N>.json or review-<N>.json files of a key."""
    pattern = re.compile(rf"{kind}-([0-9]+)\.json")
    found = []
    for path in _key_folder(run_dir, key).glob(f"{kind}-*.json"):
        match = pattern.fullmatch(path.name)
        if match:
            document, errors = _read_key_file(run_dir, key, path.name)
            if errors:
                raise ContextError(errors[0])
            found.append((int(match.group(1)), document))
    return sorted(found, key=lambda item: item[0])


def _optional_key_json(run_dir: Path, key: dict, name: str):
    """Return a JSON file of the key folder, or None when it does not exist."""
    if not (_key_folder(run_dir, key) / name).exists():
        return None
    document, errors = _read_key_file(run_dir, key, name)
    if errors:
        raise ContextError(errors[0])
    return document


def _notion_rows(run_dir: Path) -> list:
    """Return the rows of rows.json, used only for Notion links; [] when unreadable."""
    try:
        rows = json.loads((run_dir / "rows.json").read_text(encoding="utf-8")).get("rows")
    except (OSError, ValueError, AttributeError):
        return []
    return rows if isinstance(rows, list) else []


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _refuse_secrets(text: str, consequence: str) -> None:
    labels = scan_text(text, known_values_from_env())
    if labels:
        raise SecretFound(
            f"Secret détecté dans le titre ou la description de la MR ({', '.join(labels)}) : "
            f"{consequence}. Retirez-le du rapport ou du constat, puis relancez cqa_fix.py mr-body."
        )


def _read_links(run_dir: Path) -> dict:
    """Return fix/mr-links.json ({"links": {}} when absent); exit 6 when it is unreadable."""
    try:
        document = json.loads((run_dir / LINKS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"links": {}}
    except (OSError, ValueError) as error:
        raise ContextError(f"{LINKS_FILE} est illisible : {error}")
    links = document.get("links") if isinstance(document, dict) else None
    if not isinstance(links, dict) or not all(
        isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries)
        for entries in links.values()
    ):
        raise ContextError(f'{LINKS_FILE} : {{"links": {{"<Réf>": [{{"repo", "url", "iid"}}]}}}} attendu.')
    return document


def _gitlab_of(run_dir: Path, name: str) -> tuple:
    """Return (host, project, user) of a repo from fix/doctor.json."""
    doctor = _read_run_json(run_dir, "fix/doctor.json", "cqa_fix.py doctor")
    for entry in doctor.get("repos") or []:
        if isinstance(entry, dict) and entry.get("repo") == name:
            if not entry.get("host") or not entry.get("project"):
                raise fix_git.Refused(f"Le repo {name} n'est pas sur GitLab : rien n'a été poussé.")
            return entry["host"], entry["project"], entry.get("user")
    raise UsageError(f"fix/doctor.json : le repo « {name} » est absent, relancez cqa_fix.py doctor.")


def _open_mr(run, host: str, encoded: str, mr, branch: str) -> dict:
    """Return the MR of the state as GitLab has it now; exit 5 when it is merged or closed."""
    iid = mr.get("iid") if isinstance(mr, dict) else None
    if not isinstance(iid, int) or isinstance(iid, bool) or not isinstance(mr.get("url"), str):
        raise fix_state.StateError(f"{fix_state.STATE_FILE} : « mr » de la clé est invalide.")
    info = fix_gitlab.glab_json(run, host, f"projects/{encoded}/merge_requests/{iid}")
    if not isinstance(info, dict):
        raise UsageError(f"GitLab illisible : l'état de la MR !{iid} est inconnu. Rien n'a été poussé.")
    if info.get("state") in ("merged", "closed"):
        word = "fusionnée" if info["state"] == "merged" else "fermée"
        raise fix_git.Refused(
            f"La MR !{iid} est {word} : relancez une passe fix pour repartir d'une nouvelle branche."
        )
    if info.get("state") != "opened":
        raise UsageError(
            f"La MR !{iid} est dans l'état « {info.get('state')} » : rien n'a été poussé, "
            "relancez plus tard."
        )
    if info.get("source_branch") != branch:
        raise fix_git.Refused(f"La MR !{iid} ne part pas de la branche {branch} : rien n'a été poussé.")
    return info


def _find_open_mr(run, host: str, encoded: str, branch: str) -> Optional[dict]:
    """Return the open MR whose source branch is branch, or None."""
    query = f"merge_requests?source_branch={quote(branch, safe='')}&state=opened"
    listing = fix_gitlab.glab_json(run, host, f"projects/{encoded}/{query}")
    if not isinstance(listing, list):
        raise UsageError(
            f"GitLab illisible : impossible de savoir si une MR est ouverte pour la branche "
            f"{branch}. Rien n'a été poussé."
        )
    for item in listing:
        if (isinstance(item, dict) and item.get("source_branch") == branch
                and isinstance(item.get("iid"), int) and isinstance(item.get("web_url"), str)):
            return item
    return None


def _guard_problems(run_dir: Path, key: dict, worktree: Path, run, *,
                    waivers: bool = True, gitleaks=None) -> list:
    """Run the guard on <start_sha>..HEAD of the key, less the problems the lead waived
    (waive-secret) unless waivers is False; gitleaks is the fix_gitleaks.Gitleaks of the
    call, None to run the home rules alone (waive-secret: no gitleaks finding is waivable).

    The commits a taken-over branch carried between start_sha and its head (never pushed) are
    judged like the others, except that a ref outside the key is not held against them.
    """
    repo = _repo(run_dir, key["repo"])
    _third_party(repo)
    problems = fix_guard.check_commits(
        worktree, key["start_sha"], _row_sections(key), repo, known_values_from_env(), run,
        gitleaks=gitleaks,
    )
    inherited = _inherited_commits(worktree, key, run)
    problems = [problem for problem in problems
                if not (problem["reason"] == fix_guard.OUT_OF_KEY and problem["sha"] in inherited)]
    return _unwaived(key, worktree, run, problems) if waivers else problems


def _inherited_commits(worktree: Path, key: dict, run) -> set:
    """Return the SHAs of <start_sha>..<taken_head>: what a taken-over branch carried."""
    taken = key.get("taken_head")
    if not taken or taken == key["start_sha"]:
        return set()
    listing = fix_names.git(worktree, run, "rev-list", f"{key['start_sha']}..{taken}")
    if listing.returncode != 0:
        raise fix_git.GitRefused(f"git rev-list a échoué dans {worktree} : {listing.stderr.strip()}")
    return set(listing.stdout.split())


def _unreported(worktree: Path, key: dict, run) -> list:
    """Return the commits of <taken_head or start_sha>..HEAD that no fixed row of the state lists."""
    reported = {sha for row in key["rows"].values() if row.get("outcome") == "fixed"
                for sha in row.get("commits") or []}
    commits = fix_git.commits_in_range(worktree, run, key.get("taken_head") or key["start_sha"])
    return [commit for commit in commits if commit["sha"] not in reported]


def _body_stamp(run_dir: Path, key: dict, worktree: Path, run) -> dict:
    """Return what a description is built from: pass, last review, head and row outcomes."""
    reviews = [int(found.group(1)) for found in (
        _REVIEW_FILE.fullmatch(path.name) for path in _key_folder(run_dir, key).glob("review-*.json")
    ) if found]
    return {
        "pass": key.get("pass") or 0,
        "review": max(reviews, default=0),
        "head": fix_git.rev(worktree, run, "HEAD"),
        "outcomes": {ref: row.get("outcome") for ref, row in key["rows"].items()},
    }


def _ahead_of_base(worktree: Path, run, base: str) -> int:
    count = fix_names.git(worktree, run, "rev-list", "--count", f"refs/remotes/origin/{base}..HEAD")
    if count.returncode != 0:
        raise fix_git.GitRefused(f"git rev-list a échoué dans {worktree} : {count.stderr.strip()}")
    return int(count.stdout.strip() or 0)


def _remote_head(worktree: Path, run, branch: str) -> Optional[str]:
    """Fetch origin/<branch> when GitLab has it and return its head, else None."""
    try:
        present = fix_git.remote_has(worktree, run, branch)
    except fix_git.GitRefused as error:
        raise fix_git.GitRefused(f"{error}. {NOTHING_PUSHED}")
    if not present:
        return None
    fetched = fix_names.git(worktree, run, "fetch", "--quiet", "origin",
                            f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    if fetched.returncode != 0:
        raise fix_git.GitRefused(
            f"git fetch origin {branch} a échoué : {fetched.stderr.strip()}. {NOTHING_PUSHED}"
        )
    return fix_git.rev(worktree, run, f"refs/remotes/origin/{branch}")


def _push(worktree: Path, run, branch: str) -> None:
    pushed = fix_names.git(
        worktree, run, *_PUSH_CONFIG, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"
    )
    if pushed.returncode != 0:
        raise fix_git.GitRefused(
            f"Le push de {branch} a échoué : {pushed.stderr.strip()}. Rien n'a changé sur GitLab, "
            "la branche reste en local."
        )


def _create_mr(run, host: str, encoded: str, worktree: Path, project_ref: str, *, branch: str,
               base: str, title: str, text: str, user) -> dict:
    """Create the draft MR, assigned to the lead, and return {"iid", "url"}.

    -R names the project that the reads checked, whatever remote glab would pick in the
    worktree.
    """
    args = ["mr", "create", "-R", project_ref, "--source-branch", branch, "--target-branch", base,
            "--title", title, f"--description={text}", "--draft"]
    if user:
        args += ["--assignee", user]
    created = fix_gitlab.glab(run, host, *args, "--yes", cwd=str(worktree))
    if created.returncode != 0:
        raise fix_git.GitRefused(
            f"glab mr create a échoué : {(created.stderr or created.stdout).strip()}. La branche "
            f"{branch} est poussée, sans MR : relancez cqa_fix.py publish."
        )
    mr = fix_mr.mr_from_output(f"{created.stdout}\n{created.stderr}")
    if mr is None:
        found = _find_open_mr(run, host, encoded, branch)
        mr = {"iid": found["iid"], "url": found["web_url"]} if found else None
    if mr is None:
        raise fix_git.GitRefused(
            f"La MR de la branche {branch} est créée, mais son lien est illisible : relancez "
            "cqa_fix.py publish."
        )
    return mr


def _add_to_mr(run, host: str, worktree: Path, project_ref: str, iid: int, current: dict,
               text: str) -> bool:
    """Add mr-addition.md to the description GitLab has; return True when it changed."""
    old = current.get("description") or ""
    addition, hors_scope = fix_mr.split_addition(text)
    if addition and addition in old:
        addition = ""
    merged = fix_mr.merge_description(old, addition, hors_scope, date.today())
    if merged == old:
        return False
    updated = fix_gitlab.glab(run, host, "mr", "update", str(iid), "-R", project_ref,
                              f"--description={merged}", cwd=str(worktree))
    if updated.returncode != 0:
        raise fix_git.GitRefused(
            f"glab mr update !{iid} a échoué : {(updated.stderr or updated.stdout).strip()}. "
            "La description de la MR n'a pas changé : relancez cqa_fix.py publish."
        )
    return True


def _published(run_dir: Path, state: dict, key: dict, *, empty: bool = False) -> None:
    """Record the publication: an addition later only describes the passes after this one.

    empty marks a branch left without any commit in the dev's repo; the summary names it.
    """
    key["step"] = "published"
    key["published_pass"] = key.get("pass") or 0
    key["empty_branch"] = empty
    fix_state.save(run_dir, state)


def _cmd_abandon(args, services: Services) -> int:
    run_dir = args.run_dir.resolve()
    state = _read_state(run_dir, required=True)
    if state.get("finished_at"):
        raise UsageError("Aucune passe fix inachevée : rien à abandonner.")
    removed, leftovers = _remove_worktrees(run_dir, state, services.run)
    unpushed = []
    for key in state["keys"].values():
        if key.get("branch"):
            path = _repo_path(run_dir, key["repo"])
            count = fix_git.unpushed(path, services.run, key["branch"])
            if count:
                unpushed.append({"branch": key["branch"], "commits": count})
    _close_fix_questions(run_dir)
    state["finished_at"] = fix_state.now()
    fix_state.save(run_dir, state)
    _emit({"removed": removed, "unpushed": unpushed, "leftovers": leftovers})
    return EXIT_OK


def _close_fix_questions(run_dir: Path) -> None:
    for item in deferred.open_items(run_dir, fix=True):
        deferred.resolve(run_dir, item["id"])


def _cmd_summary(args, services: Services) -> int:
    """Write fix/synthese.md, close the pass and its fix questions; exit 7 on a secret.

    The project name recorded by open heads the synthesis and the team message (see
    _summary_project). --in-review gives the rows the Notion stage moved to « En review ».
    """
    run_dir = args.run_dir.resolve()
    state = _pass_state(run_dir)
    failed = parse_refs(args.replace_failed)
    in_review = None if args.in_review is None else parse_refs(args.in_review)
    context = _load_context(run_dir)
    candidates = _read_run_json(run_dir, "fix/candidates.json", "cqa_fix.py candidates")
    checks = {name: _optional_key_json(run_dir, key, "checks.json")
              for name, key in state["keys"].items() if key.get("mr")}
    found = fix_summary.summary(
        project=_summary_project(state, context), state=state, candidates=candidates,
        checks=checks, replace_failed=failed, in_review=in_review,
    )
    labels = scan_text(f"{found['text']}\n{found['team_message']}", known_values_from_env())
    if labels:
        raise SecretFound(
            f"Secret détecté dans la synthèse ({', '.join(labels)}) : rien n'a été écrit. "
            "Retirez-le du rapport ou de la raison concernée, puis relancez cqa_fix.py summary."
        )
    write_text(run_dir / "fix" / "synthese.md", found["text"])
    _close_fix_questions(run_dir)
    state["finished_at"] = fix_state.now()
    fix_state.save(run_dir, state)
    _emit(found)
    return EXIT_OK


def _summary_project(state: dict, context: dict) -> str:
    """Return the project name of the pass: the one open recorded, else the one of
    cqa_plan.py project (_project_name), else the first repo; never the run id."""
    name = state.get("project_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    found = _project_name(context)
    if found:
        return found
    return next((key["repo"] for key in state["keys"].values()), "Projet")


def _read_state(run_dir: Path, *, required: bool):
    try:
        return fix_state.load(run_dir)
    except FileNotFoundError:
        if required:
            raise UsageError(NO_PASS)
        return None


def _pass_state(run_dir: Path) -> dict:
    """Return the state of the pass in progress; exit 2 when there is none, or when it is a
    v2 pass, which only abandon closes."""
    state = _read_state(run_dir, required=True)
    if state.get("finished_at"):
        raise UsageError(NO_PASS)
    if fix_state.is_v2(state):
        raise UsageError(UNFINISHED_V2)
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
    relative = (_key_folder(run_dir, key) / name).relative_to(run_dir).as_posix()
    try:
        return json.loads((run_dir / relative).read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [f"{relative} est introuvable : le sous-agent n'a pas écrit son fichier."]
    except (OSError, ValueError) as error:
        return None, [f"{relative} est illisible : {error}"]


def _dev_checkout_changed(run_dir: Path, key: dict, run):
    """Return the changes of the dev's checkout since the last alert (fix_state.dev_checkout_alert)."""
    if not isinstance(key.get("dev_fingerprint"), dict):
        return None
    return fix_state.dev_checkout_alert(key, _repo_path(run_dir, key["repo"]), run)


def _labels(refs) -> str:
    return ", ".join(ref_label(ref) for ref in refs)


def _worktree_path(run_dir: Path, key: dict) -> Path:
    return _key_folder(run_dir, key) / "wt"


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


def _remove_worktrees(run_dir: Path, state: dict, run) -> tuple:
    """Remove the worktrees the keys of the pass still have, each after its uncommitted
    changes were saved in fix/<repo>/leftover-<N>.patch (fix_git.save_leftovers, recorded on
    the key as « leftover »: nothing is ever destroyed unsaved); return (the paths removed,
    [{"key", "patch"}] of the patches written)."""
    removed, leftovers = [], []
    for name, key in state["keys"].items():
        worktree = _worktree_path(run_dir, key)
        if not worktree.exists():
            continue
        try:
            saved = _save_leftovers(run_dir, key, worktree, run)
            if saved is not None:
                key["leftover"] = saved
                leftovers.append({"key": name, "patch": saved["patch"]})
            if fix_git.remove_worktree(_repo_path(run_dir, key["repo"]), run, worktree):
                removed.append(str(worktree))
        except (fix_git.GitRefused, fix_git.Refused) as error:
            # The records of the earlier keys stay: saved in the state, named to the lead.
            fix_state.save(run_dir, state)
            written = ", ".join(item["patch"] for item in leftovers)
            raise type(error)(f"{error}" + (
                f" Patch déjà écrits et enregistrés dans l'état : {written}." if written else ""))
    return removed, leftovers


def _save_leftovers(run_dir: Path, key: dict, worktree: Path, run):
    """Save the uncommitted changes of a worktree about to be removed; return the record
    {"patch" (relative to the run), "base", "untracked"}, None when it had none."""
    try:
        found = fix_git.save_leftovers(worktree, run, _key_folder(run_dir, key))
    except fix_git.GitRefused as error:
        raise fix_git.GitRefused(
            f"{error} Le worktree {worktree} n'a pas été retiré : ses modifications non "
            "commitées, s'il en a, seraient perdues. Sauvez-les ou retirez-le vous-même "
            "(git worktree remove --force), puis relancez."
        )
    if found is None:
        return None
    return {"patch": found["path"].relative_to(run_dir).as_posix(), "base": found["base"],
            "untracked": found["untracked"]}


def _archive(run_dir: Path, old: dict, run) -> tuple:
    """Move a finished pass, state and key folders, to fix/_history/<stamp>/; return (the
    worktrees removed, the archive folder, the leftover patches saved, named where they now
    are, as the archived state names them)."""
    removed, leftovers = _remove_worktrees(run_dir, old, run)
    target = _new_history_dir(run_dir)
    remapped = False
    for name, key in old["keys"].items():
        folder = _key_folder(run_dir, key)
        if folder.is_dir():
            destination = target / folder.relative_to(run_dir / "fix")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(destination))
            if folder.parent != run_dir / "fix" and not any(folder.parent.iterdir()):
                folder.parent.rmdir()
            saved = key.get("leftover")
            if isinstance(saved, dict) and isinstance(saved.get("patch"), str):
                inside = Path(saved["patch"])
                if inside.is_relative_to(folder.relative_to(run_dir)):
                    saved["patch"] = (destination / inside.relative_to(
                        folder.relative_to(run_dir))).relative_to(run_dir).as_posix()
                    remapped = True
    for item in leftovers:
        item["patch"] = old["keys"][item["key"]]["leftover"]["patch"]
    if remapped:
        fix_state.save(run_dir, old)
    os.replace(run_dir / fix_state.STATE_FILE, target / "state.json")
    return removed, target.relative_to(run_dir).as_posix(), leftovers


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
