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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from cqa.checklist import STATUS_REVIEW, parse_refs, ref_label, validate_schema
from cqa import fix_notion, fix_state, report
from cqa.comments import (
    MAX_COMMENT_LENGTH,
    format_comment,
    format_fix_comment,
    format_fix_page_comment,
    has_mr_block,
    parse_verdict,
    without_mr_block,
)
from cqa.findings import apply_guardrails
from cqa.notion_api import NotionClient, NotionError, extract_id
from cqa.notion_read import (
    assemble_rows,
    assignee_options,
    fix_comment_info,
    human_comments,
    load_rows,
    load_skill_comments,
    resolve,
    status_options,
    write_fiches,
    write_json,
)
from cqa.notion_write import (
    ReferenceWriteRefused,
    apply_plan,
    skill_comments,
    text_digest,
    validate_plan,
)
from cqa.plan import filter_plan, operation_key, plan_digest
from cqa.rerun import (
    comment_dates,
    latest_findings,
    list_of,
    read_grounded_findings,
    recette_candidates,
    rerun_candidates,
    skill_last_of,
)
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
    sub = commands.add_parser(
        "fix-plan",
        help="prépare l'écriture d'une passe fix : commentaires de correctif, statut « En "
        "review », commentaire de page (fix/notion-plan.json)",
    )
    sub.add_argument("run_dir", type=Path, help="dossier du run")
    sub.add_argument(
        "--closed",
        nargs="+",
        default=[],
        metavar="CQ-n",
        help="Réf dont la MR est fermée sans fusion : statut d'avant la passe remis",
    )
    sub.add_argument(
        "--closed-only",
        action="store_true",
        help="seulement les retours des MR fermées (--closed), sans passe ni commentaire de page",
    )
    sub.set_defaults(handler=_cmd_fix_plan)
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
    links = _mr_links(run_dir)
    known = known_values_from_env()
    chosen = {item["ref"]: item for item in recette_candidates(rows, plan, applied)}
    context = _read_json(run_dir / "context.json")
    candidates = rerun_candidates(
        run_dir,
        extra_refs=[],
        git_run=args.git_run,
        read=lambda lot, name: read_grounded_findings(run_dir, context, lot, name),
    )
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
        thread = client.comments(item["page_id"])
        texts = human_comments(thread, notion["bot_id"])
        item["comments"] = [redact(text, known) for text in texts]
        item["fix"] = _fix_info(thread, notion["bot_id"], links.get(ref), known)
        result.append(item)
    (run_dir / "notion").mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "notion" / "recette.json", {"rows": result})
    _emit({**counts, "recette": [item["ref"] for item in result], "code_modifie": sorted(changed)})
    return EXIT_OK


def _mr_links(run_dir: Path) -> dict:
    """Return {ref: [url]} of fix/mr-links.json, {} when absent; ValueError when unreadable."""
    return fix_state.read_mr_links(run_dir)


def _fix_info(thread: list, bot_id: str, urls, known: list) -> dict:
    """Return the « fix » of a recette row: {"mrs", "remaining"} read in its latest fix comment;
    without one, the merge requests that fix/mr-links.json of the run links and no remaining."""
    found = fix_comment_info(thread, bot_id)
    if found is None:
        return {"mrs": list(urls or []), "remaining": None}
    if found["remaining"]:
        found["remaining"] = redact(found["remaining"], known)
    return found


def _cmd_fix_plan(args, client) -> int:
    """Write fix/notion-plan.json, what a fix pass writes to Notion, and print what it holds.

    For each row of the pass (fix_notion.pass_rows): its fix comment; the « En review » status
    of a row put in a published merge request, with its status before the pass as expected
    (fix/mr-links.json), when the copy has the option; its audit comment without the « MR : »
    block of the v2 fix mode, when it still has one. Then the page comment of the pass.
    --closed names rows whose merge request was closed unmerged: their status goes back to the
    one before the pass (expected « En review »), read in the newest fix/mr-links.json of the
    project's runs that links them, and their fix comment says so. --closed-only plans those
    alone, without a pass. Prints {"ops", "in_review", "reverted", "notes"}: the count of each
    kind of operation, the refs set to « En review », the refs whose status goes back, and what
    the plan leaves out.
    """
    run_dir = args.run_dir
    notion = _notion_context(run_dir, "copy_data_source_id", "bot_id")
    context = _read_json(run_dir / "context.json")
    closed = sorted({ref for text in args.closed for ref in _parse_refs(text)})
    if args.closed_only and not closed:
        raise UsageError("--closed-only attend les Réf des MR fermées sans fusion (--closed CQ-12).")
    if args.closed_only:
        _refuse_when_none(run_dir)
        if isinstance(context, dict) and context.get("dry_run") is True:
            raise UsageError("context.json : dry_run vaut true (--dry-run) : rien n'est préparé "
                             "pour Notion.")
    common = {
        "lead_option": _lead_option(context),
        "repos": context.get("repos") or [],
        "plugin_version": context.get("plugin_version") or "",
    }
    rows = {item["ref"]: item for item in _read_rows(run_dir)
            if isinstance(item, dict) and isinstance(item.get("ref"), int)}
    notes = []
    state, concerned, links = None, {}, {}
    if args.closed_only:
        common["run_date"] = date.today()
    else:
        state = _notion_pass(run_dir)
        common["run_date"] = _fix_date(run_dir)
        candidates = _read_json(run_dir / "fix" / "candidates.json",
                                "lancer d'abord cqa_fix.py candidates")
        reports = {name: _key_reports(run_dir, key) for name, key in state["keys"].items()}
        concerned = fix_notion.pass_rows(
            state, candidates if isinstance(candidates, dict) else {}, reports
        )
        links = fix_state.read_mr_link_entries(run_dir)
    reverts = _closed_targets(run_dir, closed, concerned, notes)
    review = _review_option(client, notion, concerned, notes)
    latest = []

    def findings() -> tuple:
        if not latest:
            sources = {}
            found = _latest_findings(run_dir, context, sources)
            latest.append((found, comment_dates(run_dir, sources)))
        return latest[0]

    ops, in_review, reverted = [], [], []
    for ref in sorted(set(concerned) | set(reverts)):
        row = rows.get(ref)
        if row is None:
            notes.append(f"{ref_label(ref)} ne figure pas dans la copie : rien n'est écrit pour "
                         "cette ligne.")
            continue
        if ref in reverts:
            urls, before = reverts[ref]
            text = format_fix_comment(row=row, **common, mrs=[], done=None, remaining=None,
                                      uat=None, commits=[],
                                      not_done=fix_notion.closed_reason(urls, before))
            if before:
                ops.append(_fix_op("status", row, before=STATUS_REVIEW, after=before,
                                   expected=STATUS_REVIEW))
                reverted.append(ref)
            else:
                notes.append(f"{ref_label(ref)} : statut d'avant la passe inconnu : statut "
                             "laissé tel quel.")
        else:
            item = concerned[ref]
            text = format_fix_comment(
                row=row, **common, mrs=item["mrs"], pushed=item["pushed"],
                branches=item["branches"], done=item["done"], remaining=item["remaining"], uat=item["uat"],
                commits=item["commits"], not_done=item["not_done"],
            )
            if item["in_mr"] and review:
                before = fix_notion.status_before(item, links.get(ref))
                if before is None:
                    notes.append(f"{ref_label(ref)} : statut d'avant la passe inconnu : statut "
                                 f"« {STATUS_REVIEW} » non posé.")
                elif before != STATUS_REVIEW:
                    ops.append(_fix_op("status", row, before=before, after=STATUS_REVIEW,
                                       expected=before))
                    in_review.append(ref)
        audit = _audit_without_mr_block(client, row, notion["bot_id"], findings, context, common)
        if audit is not None:
            rewritten, verdict, read = audit
            ops.append(_fix_op("comment", row, text=rewritten, verdict=verdict,
                               replaces=text_digest(read)))
        ops.append(_fix_op("fix_comment", row, text=text))
    if state is not None and not concerned:
        notes.append("Aucune ligne de la passe à écrire dans Notion : pas de commentaire de page.")
    elif state is not None:
        page_id = notion.get("copy_page_id")
        if isinstance(page_id, str) and page_id.strip():
            text = _fix_page_comment(state, concerned, common, not review, in_review, reverted)
            ops.append(_fix_op("page_comment", {"page_id": page_id, "ref": None}, text=text))
        else:
            notes.append("copy_page_id absent de context.json (copie donnée par l'URL de la "
                         "base) : pas de commentaire de page.")
    plan = {
        "run_id": context.get("run_id"),
        "copy_data_source_id": notion["copy_data_source_id"],
        "copy_page_id": notion.get("copy_page_id"),
        "assignee_options": [],
        "ops": ops,
    }
    plan["plan_id"] = plan_digest(plan)
    # A recette may revert closed merge requests (--closed-only) in a run without a fix pass.
    (run_dir / "fix").mkdir(exist_ok=True)
    write_json(run_dir / "fix" / "notion-plan.json", plan)
    counts = {}
    for kind in ("status", "comment", "fix_comment", "page_comment"):
        count = sum(1 for item in ops if item["kind"] == kind)
        if count:
            counts[kind] = count
    _emit({"ops": counts, "in_review": in_review, "reverted": reverted, "notes": notes})
    return EXIT_OK


def _lead_option(context) -> str:
    option = ((context.get("lead") or {}) if isinstance(context, dict) else {}).get("option")
    if not isinstance(option, str) or not option.strip():
        raise UsageError("context.json : « lead.option » est vide : le commentaire cite le lead.")
    return option


def _notion_pass(run_dir: Path) -> dict:
    """Return fix/state.json of a pass whose result may go to Notion; exit 2 otherwise."""
    try:
        state = fix_state.load(run_dir)
    except FileNotFoundError:
        raise UsageError("fix/state.json est introuvable : aucune passe de correctifs dans ce run.")
    except fix_state.StateError as error:
        raise UsageError(str(error))
    if state.get("push") == "dry_run":
        raise UsageError("Passe --dry-run : rien n'est écrit dans Notion.")
    if not state.get("notion"):
        raise UsageError(
            "fix/state.json : le lead n'a pas donné son accord pour écrire dans Notion "
            "(« notion » vaut false) : rien n'est préparé."
        )
    return state


def _key_reports(run_dir: Path, key: dict) -> list:
    """Return [(N, report)] of a key: fix/<repo>/report-<N>.json (fix/<repo>/<slug> for v2)."""
    folder = Path(run_dir) / "fix" / key["repo"]
    if "slug" in key:
        folder = folder / key["slug"]
    found = []
    for path in folder.glob("report-*.json"):
        match = re.fullmatch(r"report-([0-9]+)\.json", path.name)
        if match is None:
            continue
        document = _optional_json(path, "les textes de ses lignes manquent aux commentaires")
        if isinstance(document, dict):
            found.append((int(match.group(1)), document))
    return sorted(found, key=lambda item: item[0])


def _closed_targets(run_dir: Path, closed: list, concerned: dict, notes: list) -> dict:
    """Return {ref: (url, status_before)} for the refs of closed merge requests, read in the
    newest fix/mr-links.json of the project's runs (run_dir and its siblings) that links each."""
    targets = {}
    if not closed:
        return targets
    project = Path(run_dir).resolve().parent
    folders = sorted((path for path in project.iterdir() if path.is_dir()),
                     key=lambda path: path.name, reverse=True)
    read = {}
    for ref in closed:
        if ref in concerned:
            notes.append(f"{ref_label(ref)} est une ligne de cette passe : le retour de sa MR "
                         "fermée est ignoré.")
            continue
        for folder in folders:
            if folder.name not in read:
                try:
                    read[folder.name] = fix_state.read_mr_link_entries(folder)
                except ValueError as error:
                    read[folder.name] = {}
                    notes.append(f"Run {folder.name} : {error} Fichier ignoré.")
            entries = read[folder.name].get(ref)
            if entries:
                targets[ref] = fix_notion.closed_target(entries)
                break
        else:
            notes.append(f"{ref_label(ref)} : aucune MR connue dans les runs du projet : statut "
                         "laissé tel quel.")
    return targets


def _review_option(client, notion: dict, concerned: dict, notes: list) -> bool:
    """Return whether the copy has the « En review » status option; read only when a row of
    the pass went into a published merge request."""
    if not any(item["in_mr"] for item in concerned.values()):
        return True
    data_source = client.data_source(notion["copy_data_source_id"])
    if STATUS_REVIEW in status_options(data_source):
        return True
    notes.append(fix_notion.NO_REVIEW_OPTION)
    return False


def _audit_without_mr_block(client, row: dict, bot_id: str, findings, context: dict,
                            common: dict):
    """Return (text, verdict, read) for the audit comment of a row rewritten without the
    « MR : » block of the v2 fix mode (read: the text it replaces), None when it has no such
    block (comments.has_mr_block) or no audit comment.

    A comment with a verdict keeps its text; a reduced v2 comment (no verdict) is written again
    from the latest finding of the row, dated as the audit plan dates it (the date of the rerun
    that judged the row, else the start of the run), and only loses its block without one.
    findings() gives ({ref: latest finding}, {ref: date of its rerun}).
    """
    own = skill_comments(client.comments(row["page_id"]), bot_id, "comment")
    if not own:
        return None
    text = "".join(part.get("plain_text", "") for part in own[-1].get("rich_text") or [])
    if not has_mr_block(text):
        return None
    verdict = parse_verdict(text)
    found, dates = findings() if verdict is None else ({}, {})
    finding = found.get(row["ref"])
    if finding is None:
        return without_mr_block(text), verdict, text
    day = dates.get(row["ref"])
    if day is None:
        try:
            day = report.run_start(context.get("run_id")).date()
        except ValueError:
            day = common["run_date"]
    judged = apply_guardrails(finding, row, followup_done=True)
    rebuilt = format_comment(finding=judged, row=row, **{**common, "run_date": day})
    return rebuilt, judged["verdict"], text


def _fix_page_comment(state: dict, concerned: dict, common: dict, review_missing: bool,
                      in_review: list, reverted: list) -> str:
    mrs = []
    for key in state["keys"].values():
        if fix_notion.published(key) and key["mr"]["url"] not in mrs:
            mrs.append(key["mr"]["url"])
    outside = [item for item in concerned.values() if item["fixed"] and not item["in_mr"]]
    pushed, branches = [], []
    for item in outside:
        pushed.extend(name for name in item["pushed"] if name not in pushed)
        branches.extend(name for name in item["branches"] if name not in branches)
    pushed_rows = [ref for ref, item in concerned.items()
                   if item["fixed"] and not item["in_mr"] and item["pushed"]]
    local = [ref for ref, item in concerned.items()
             if item["fixed"] and not item["in_mr"] and not item["pushed"]]
    return format_fix_page_comment(
        run_date=common["run_date"], lead_option=common["lead_option"], mrs=mrs,
        branches=branches, in_mr=[ref for ref, item in concerned.items() if item["in_mr"]],
        local=local, partial=[ref for ref, item in concerned.items() if item["partial"]],
        in_review=in_review, review_missing=review_missing,
        not_done=fix_notion.not_done_groups(concerned), reverted=reverted, pushed=pushed,
        pushed_rows=pushed_rows,
    )


def _fix_op(kind: str, row: dict, *, text: str = "", verdict=None, before=None, after=None,
            expected=None, replaces=None) -> dict:
    """Return an operation of fix/notion-plan.json; expected makes a status conditional,
    replaces an audit comment rewrite (see notion_write.apply_plan)."""
    operation = {
        "key": operation_key(kind, row["page_id"], after, text),
        "kind": kind,
        "page_id": row["page_id"],
        "ref": row["ref"],
        "verdict": verdict,
        "before": before,
        "after": after,
        "text": text,
        "human_override": False,
    }
    if expected is not None:
        operation["expected"] = expected
    if replaces is not None:
        operation["replaces"] = replaces
    return operation


def _latest_findings(run_dir: Path, context: dict, sources: Optional[dict] = None) -> dict:
    """Return {ref: latest valid finding} of the run, {} when lots.json is missing. sources,
    when given, receives {ref: name of the findings file of its latest finding}."""
    try:
        lots = json.loads((run_dir / "lots.json").read_text(encoding="utf-8"))["lots"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(lots, list):
        return {}

    def read(lot, name):
        found = read_grounded_findings(run_dir, context, lot, name)
        if sources is not None:
            for item in found[0] if found else []:
                sources[item["ref"]] = name
        return found

    return latest_findings(run_dir, lots, read)


def _fix_date(run_dir: Path) -> date:
    """Return the local date the fix pass started, from fix/state.json."""
    try:
        state = fix_state.load(run_dir)
    except FileNotFoundError:
        raise UsageError("fix/state.json est introuvable : aucune passe de correctifs dans ce run.")
    except fix_state.StateError as error:
        raise UsageError(str(error))
    try:
        started = datetime.strptime(state["started_at"], "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, TypeError, ValueError):
        raise UsageError("fix/state.json : « started_at » est absent ou invalide.")
    return started.replace(tzinfo=timezone.utc).astimezone().date()


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
