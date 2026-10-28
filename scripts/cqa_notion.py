#!/usr/bin/env python3
"""Notion side of the checklist audit. Results go to stdout as JSON, errors to stderr.

Exit codes: 0 success, 2 environment or usage, 3 Notion API error, 6 invalid schema,
7 write operation blocked by a safety gate, 8 write refused on the reference template.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from cqa.checklist import parse_refs, validate_schema
from cqa.comments import MAX_COMMENT_LENGTH
from cqa.notion_api import NotionClient, NotionError, extract_id
from cqa.notion_read import (
    assemble_rows,
    assignee_options,
    human_comments,
    load_rows,
    load_skill_comments,
    resolve,
    write_fiches,
    write_json,
)
from cqa.notion_write import ReferenceWriteRefused, apply_plan, validate_plan
from cqa.plan import filter_plan, operation_key
from cqa.rerun import list_of, recette_candidates, rerun_candidates, skill_last_of
from cqa.secrets import known_values_from_env, redact

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_API = 3
EXIT_SCHEMA = 6
EXIT_BLOCKED = 7
EXIT_REFUSED = 8


class UsageError(Exception):
    """A problem with the environment or the arguments, worded for the lead."""


def main(argv, *, client=None, git_run=subprocess.run) -> int:
    """Run one command and return its exit code. client is built from NOTION_TOKEN when absent.

    git_run is called like subprocess.run, for the rows of a recette whose code changed.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    args.git_run = git_run
    try:
        if client is None:
            client = NotionClient(_token())
        return args.handler(args, client)
    except NotionError as error:
        print(error, file=sys.stderr)
        return EXIT_API
    except ReferenceWriteRefused as error:
        print(error, file=sys.stderr)
        return EXIT_REFUSED
    except (UsageError, ValueError) as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cqa_notion.py", description="Lecture et écriture de la checklist qualité dans Notion."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commande")
    commands.add_parser("whoami", help="affiche l'intégration Notion").set_defaults(
        handler=_cmd_whoami
    )
    sub = commands.add_parser("resolve", help="trouve la data source d'une page ou d'une base")
    sub.add_argument("url", help="URL ou identifiant de la page ou de la base")
    sub.set_defaults(handler=_cmd_resolve)
    sub = commands.add_parser("schema", help="valide le schéma d'une data source")
    sub.add_argument("data_source_id", help="identifiant de la data source")
    sub.set_defaults(handler=_cmd_schema)
    for name, help_text, handler in (
        ("rows", "lit la copie et la rapproche du modèle (rows.json)", _cmd_rows),
        ("fiches", "met en cache les fiches du modèle (fiches/)", _cmd_fiches),
        ("comments", "lit les verdicts des commentaires de l'intégration", _cmd_comments),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("run_dir", type=Path, help="dossier du run")
        sub.set_defaults(handler=handler)
    sub = commands.add_parser(
        "recette", help="relit la copie et liste les lignes à recetter (recette.json)"
    )
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("--without", default="", help="Réf à retirer de la liste (CQ-3,CQ-12)")
    sub.add_argument("--refs", default="", help="Réf à ajouter, même non candidates (CQ-40)")
    sub.set_defaults(handler=_cmd_recette)
    sub = commands.add_parser("apply", help="écrit un plan dans la copie (reprise possible)")
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument(
        "--plan",
        type=Path,
        default=Path("notion/plan.json"),
        help="plan à écrire, relatif au dossier du run (défaut : notion/plan.json)",
    )
    sub.add_argument(
        "--skip-human-overrides",
        action="store_true",
        help="ne touche pas aux pages dont le statut a été posé par un humain",
    )
    sub.set_defaults(handler=_cmd_apply)
    sub = commands.add_parser("comment-page", help="écrit un commentaire sur la page de la copie")
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument("text_file", type=Path, help="fichier texte du commentaire")
    sub.set_defaults(handler=_cmd_comment_page)
    return parser


def _token() -> str:
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        raise UsageError("NOTION_TOKEN est absent de l'environnement (jeton de l'intégration).")
    return token


def _cmd_whoami(args, client) -> int:
    me = client.me()
    _emit({"bot_id": me["id"], "name": me.get("name") or ""})
    return EXIT_OK


def _cmd_resolve(args, client) -> int:
    _emit(resolve(client, args.url))
    return EXIT_OK


def _cmd_schema(args, client) -> int:
    data_source = client.data_source(extract_id(args.data_source_id))
    problems = validate_schema(data_source)
    options = assignee_options(data_source)
    _emit({"ok": not problems, "problems": problems, "assignee_options": options})
    return EXIT_SCHEMA if problems else EXIT_OK


def _cmd_rows(args, client) -> int:
    _emit(_read_copy(args.run_dir, client))
    return EXIT_OK


def _read_copy(run_dir: Path, client) -> dict:
    """Read the copy and the template, write rows.json and return the counts."""
    notion = _notion_context(run_dir, "copy_data_source_id", "bot_id")
    reference = os.environ.get("GM_CQA_REFERENCE", "").strip()
    if not reference:
        raise UsageError("GM_CQA_REFERENCE est absent de l'environnement : URL du modèle.")
    copy_ds = extract_id(notion["copy_data_source_id"])
    reference_ds = extract_id(resolve(client, reference)["data_source_id"])
    if reference_ds == copy_ds:
        raise UsageError("Le modèle et la copie désignent la même data source.")
    copy_rows = load_rows(client, copy_ds, notion["bot_id"])
    reference_rows = load_rows(client, reference_ds, notion["bot_id"])
    rows, missing = assemble_rows(copy_rows, reference_rows)
    write_json(
        run_dir / "rows.json",
        {"copy_data_source_id": copy_ds, "rows": rows, "missing_in_copy": missing},
    )
    matched = sum(1 for item in rows if item["reference_page_id"] is not None)
    return {
        "rows": len(rows),
        "matched": matched,
        "unmatched": len(rows) - matched,
        "missing_in_copy": len(missing),
    }


def _cmd_fiches(args, client) -> int:
    pairs = [
        (item, {"page_id": item["reference_page_id"], "ref": item["ref"]})
        for item in _read_rows(args.run_dir)
        if item.get("reference_page_id")
    ]
    index = write_fiches(client, args.run_dir, pairs)
    truncated = [int(ref) for ref, entry in index.items() if entry["truncated"]]
    _emit({"fiches": len(index), "truncated": truncated})
    return EXIT_OK


def _cmd_comments(args, client) -> int:
    notion = _notion_context(args.run_dir, "bot_id")
    verdicts = load_skill_comments(client, _read_rows(args.run_dir), notion["bot_id"])
    write_json(
        args.run_dir / "comments.json", {str(ref): found for ref, found in sorted(verdicts.items())}
    )
    _emit({"refs_with_verdicts": len(verdicts)})
    return EXIT_OK


def _cmd_recette(args, client) -> int:
    run_dir = args.run_dir
    notion = _notion_context(run_dir, "bot_id")
    counts = _read_copy(run_dir, client)
    rows = _read_rows(run_dir)
    plan = _optional_json(
        run_dir / "notion" / "plan.json",
        "les valeurs du journal donnent encore le dernier statut écrit par le skill",
    )
    applied = _optional_json(
        run_dir / "notion" / "applied.json",
        "le dernier statut écrit par le skill se déduit du plan (statut d'avant l'audit), "
        "ce qui peut proposer à tort des lignes",
    )
    known = known_values_from_env()
    chosen = {item["ref"]: item for item in recette_candidates(rows, plan, applied)}
    candidates = rerun_candidates(run_dir, extra_refs=[], git_run=args.git_run)
    for note in candidates["notes"]:
        _log(note)
    changed = [entry["ref"] for entry in candidates["candidates"] if "code_modifie" in entry["reasons"]]
    by_ref = {item["ref"]: item for item in rows if isinstance(item, dict)}
    last_of = skill_last_of(plan, applied)
    for ref in changed:
        row = by_ref.get(ref)
        if row is not None and ref not in chosen:
            chosen[ref] = {
                "ref": ref,
                "page_id": row["page_id"],
                "status": row["status"],
                "skill_last": last_of(ref),
            }
    for ref in _parse_refs(args.refs):
        row = by_ref.get(ref)
        if row is None:
            _log(f"CQ-{ref} ne figure pas dans la copie : ignorée.")
        elif ref not in chosen:
            chosen[ref] = {
                "ref": ref,
                "page_id": row["page_id"],
                "status": row["status"],
                "skill_last": last_of(ref),
            }
    for ref in _parse_refs(args.without):
        chosen.pop(ref, None)
    result = []
    for ref in sorted(chosen):
        item = chosen[ref]
        texts = human_comments(client.comments(item["page_id"]), notion["bot_id"])
        item["comments"] = [redact(text, known) for text in texts]
        result.append(item)
    (run_dir / "notion").mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "notion" / "recette.json", {"rows": result})
    _emit({**counts, "recette": [item["ref"] for item in result], "code_modifie": sorted(changed)})
    return EXIT_OK


def _parse_refs(text: str) -> list:
    return parse_refs(text)


def _optional_json(path: Path, consequence: str = ""):
    """Read a JSON file, None when it is missing; an unreadable one is reported with its effect."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        if consequence:
            _log(f"{path.name} est illisible ({error}) : {consequence}.")
        return None


def _refuse_when_none(run_dir: Path) -> None:
    """Refuse the audit writes when the lead declined them at launch (notion_write "none")."""
    context = _read_json(run_dir / "context.json")
    if isinstance(context, dict) and context.get("notion_write") == "none":
        raise UsageError(
            "context.json : notion_write vaut « none » (le lead a refusé l'écriture dans "
            "Notion au lancement) : rien n'est écrit."
        )


def _cmd_apply(args, client) -> int:
    if args.plan == Path("notion/plan.json"):
        _refuse_when_none(args.run_dir)
    plan = _read_json(args.run_dir / args.plan, "lancer d'abord cqa_plan.py plan")
    if not isinstance(plan, dict):
        raise UsageError(f"{args.plan.name} : un objet JSON est attendu.")
    validate_plan(plan)
    if args.skip_human_overrides:
        try:
            plan = filter_plan(plan, skip_human_overrides=True)
        except KeyError as error:
            raise UsageError(f"{args.plan.name} : la clé « {error.args[0]} » est absente.")
        except (TypeError, AttributeError):
            raise UsageError(f"{args.plan.name} : « rows » ou « summary » est invalide.")
    notion = _notion_context(args.run_dir, "bot_id")
    row_page_ids = {
        item["page_id"]
        for item in _read_rows(args.run_dir)
        if isinstance(item, dict) and isinstance(item.get("page_id"), str)
    }
    return _write_plan(client, args.run_dir, plan, notion, row_page_ids)


def _cmd_comment_page(args, client) -> int:
    _refuse_when_none(args.run_dir)
    context = _read_json(args.run_dir / "context.json")
    section = context.get("notion") if isinstance(context, dict) else None
    if isinstance(section, dict) and "copy_page_id" in section and section["copy_page_id"] is None:
        print(
            "La copie est donnée par l'URL de la base (copy_page_id absent) : "
            "aucun commentaire n'est écrit sur la page.",
            file=sys.stderr,
        )
        _emit(
            {"done": 0, "skipped": 0, "blocked": [], "errors": [], "reason": "copy_page_id absent"}
        )
        return EXIT_OK
    notion = _notion_context(args.run_dir, "copy_page_id", "copy_data_source_id", "bot_id")
    page_id = notion["copy_page_id"]
    text = _read_comment_text(args.text_file)
    operation = {
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
    plan = {
        "copy_data_source_id": notion["copy_data_source_id"],
        "copy_page_id": page_id,
        "assignee_options": [],
        "ops": [operation],
    }
    return _write_plan(client, args.run_dir, plan, notion, set())


def _write_plan(client, run_dir: Path, plan: dict, notion: dict, row_page_ids: set) -> int:
    """Apply a plan with the safety gates, print its report and return the exit code."""
    reference_ds_id, reference_page_id = _reference(client, notion)
    result = apply_plan(
        client,
        plan,
        run_dir=run_dir,
        reference_ds_id=reference_ds_id,
        row_page_ids=row_page_ids,
        copy_page_id=notion.get("copy_page_id") or None,
        known_secrets=known_values_from_env(),
        bot_id=notion["bot_id"],
        reference_page_id=reference_page_id,
        log=_log,
    )
    _emit(result)
    if result["errors"]:
        return EXIT_API
    return EXIT_BLOCKED if result["blocked"] else EXIT_OK


def _reference(client, notion: dict) -> tuple:
    """Return the reference (data source id, page id or None) to keep the writes away from.

    Both come from the context; when one is missing, GM_CQA_REFERENCE is resolved.
    """
    data_source = notion.get("reference_data_source_id")
    if not isinstance(data_source, str) or not data_source.strip():
        data_source = None
    has_page = "reference_page_id" in notion
    page = notion.get("reference_page_id")
    if page is not None and (not isinstance(page, str) or not page.strip()):
        raise UsageError("context.json : notion.reference_page_id doit être un texte ou null.")
    if data_source and has_page:
        return data_source.strip(), page
    reference = os.environ.get("GM_CQA_REFERENCE", "").strip()
    if not reference:
        raise UsageError(
            "Le modèle est inconnu : notion.reference_data_source_id ou "
            "notion.reference_page_id est absent de context.json et GM_CQA_REFERENCE de "
            "l'environnement. Aucune écriture sans cette vérification."
        )
    resolved = resolve(client, reference)
    return (
        data_source.strip() if data_source else resolved["data_source_id"],
        page if has_page else resolved["page_id"],
    )


def _read_comment_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as error:
        raise UsageError(f"{path} est illisible : {error}")
    if not text:
        raise UsageError(f"{path} est vide : aucun commentaire à écrire.")
    if len(text) > MAX_COMMENT_LENGTH:
        raise UsageError(f"{path} dépasse {MAX_COMMENT_LENGTH} caractères ({len(text)}).")
    return text


def _notion_context(run_dir: Path, *keys: str) -> dict:
    """Return the "notion" section of context.json, checking that each key is a filled string."""
    context = _read_json(run_dir / "context.json")
    notion = context.get("notion") if isinstance(context, dict) else None
    for key in keys:
        value = notion.get(key) if isinstance(notion, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise UsageError(f"context.json : notion.{key} est absent ou vide.")
    return notion


def _read_rows(run_dir: Path) -> list:
    document = _read_json(run_dir / "rows.json", "lancer d'abord la commande rows")
    rows = document.get("rows") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise UsageError("rows.json : la liste « rows » est absente.")
    return rows


def _read_json(path: Path, hint: str = ""):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        suffix = f" : {hint}." if hint else "."
        raise UsageError(f"{path.name} est introuvable dans {path.parent}{suffix}")
    except (OSError, ValueError) as error:
        raise UsageError(f"{path.name} est illisible : {error}")


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _log(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
