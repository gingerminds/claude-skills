#!/usr/bin/env python3
"""Notion side of the checklist audit. Results go to stdout as JSON, errors to stderr.

Exit codes: 0 success, 2 environment or usage, 3 Notion API error, 6 invalid schema.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from cqa.checklist import validate_schema
from cqa.notion_api import NotionClient, NotionError, extract_id
from cqa.notion_read import (
    assemble_rows,
    assignee_options,
    load_rows,
    load_skill_comments,
    resolve,
    write_fiches,
    write_json,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_API = 3
EXIT_SCHEMA = 6


class UsageError(Exception):
    """A problem with the environment or the arguments, worded for the lead."""


def main(argv, *, client=None) -> int:
    """Run one command and return its exit code. client is built from NOTION_TOKEN when absent."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    try:
        if client is None:
            client = NotionClient(_token())
        return args.handler(args, client)
    except NotionError as error:
        print(error, file=sys.stderr)
        return EXIT_API
    except (UsageError, ValueError) as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cqa_notion.py", description="Lecture de la checklist qualité dans Notion."
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
    notion = _notion_context(args.run_dir, "copy_data_source_id", "bot_id")
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
        args.run_dir / "rows.json",
        {"copy_data_source_id": copy_ds, "rows": rows, "missing_in_copy": missing},
    )
    matched = sum(1 for item in rows if item["reference_page_id"] is not None)
    _emit(
        {
            "rows": len(rows),
            "matched": matched,
            "unmatched": len(rows) - matched,
            "missing_in_copy": len(missing),
        }
    )
    return EXIT_OK


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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
