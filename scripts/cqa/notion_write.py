"""Writing a plan to Notion: the only code that changes the copy, behind its safety gates."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from cqa.checklist import ref_label
from cqa.comments import to_rich_text
from cqa.notion_api import NotionError
from cqa.notion_read import assignee_options, write_json
from cqa.secrets import scan_text

STATUS_PROPERTY = "Statut"
ASSIGNEE_PROPERTY = "Assignée à"
REFUSED_MESSAGE = "écriture refusée : data source du modèle"
REFUSED_PAGE_MESSAGE = "écriture refusée : page du modèle"

_KIND_ORDER = {"status": 0, "assign": 1, "comment": 2, "page_comment": 3}
_STOPPING_STATUSES = (0, 401, 429)


class ReferenceWriteRefused(ValueError):
    """The plan targets the data source of the reference template."""


def apply_plan(
    client,
    plan: dict,
    *,
    run_dir: Path,
    reference_ds_id: str,
    row_page_ids: set[str],
    copy_page_id: Optional[str],
    known_secrets: list[str],
    bot_id: Optional[str] = None,
    reference_page_id: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Write the operations of a plan to Notion and return its report.

    The report is {"done", "skipped", "blocked", "errors"}: done and skipped are counts,
    blocked lists {"key", "labels"} and errors {"key", "message"}.

    A plan whose copy is the reference data source, or with an operation on reference_page_id
    (the page of the template), is refused with ReferenceWriteRefused, and a malformed plan or
    journal with ValueError, all before any request. When the plan assigns,
    the schema of the copy is then read: its NotionError is not caught, nothing has been
    written. An operation is blocked, without any request, when its page is not a row of the
    copy (a page_comment may also target copy_page_id), when its text or target value holds a
    secret, or when an assignment names an option that the plan does not list or that the copy
    no longer has (Notion would create it). Operations go by ref, then status, assign and
    comment, the page_comment ones last.

    notion/applied.json in run_dir lists what was written and is rewritten after each success:
    its keys are skipped on a rerun. A comment is also skipped when the page already shows a
    comment with the same text (from bot_id, or from anyone when it is None), as Notion may
    have stored one whose answer was lost. A network error, an authentication failure, a 429
    or a 5xx stops the plan; any other Notion error only fails its operation and the later
    ones of the same page.
    """
    _refuse_reference(plan, reference_ds_id)
    ops = validate_plan(plan)
    if reference_page_id is not None:
        reference_page = _normalize(reference_page_id)
        if any(_normalize(item["page_id"]) == reference_page for item in ops):
            raise ReferenceWriteRefused(REFUSED_PAGE_MESSAGE)
    allowed = {_normalize(page_id) for page_id in row_page_ids}
    journal = Path(run_dir) / "notion" / "applied.json"
    entries = _load_journal(journal)
    options = _live_options(client, plan, ops)
    journal.parent.mkdir(parents=True, exist_ok=True)
    recorded = {entry["key"] for entry in entries}
    result = {"done": 0, "skipped": 0, "blocked": [], "errors": []}
    failed_pages = set()
    for item in sorted(ops, key=_order):
        key, label = item["key"], _describe(item)
        if key in recorded:
            result["skipped"] += 1
            log(f"{label} : déjà écrit lors d'un passage précédent")
            continue
        labels = _blocking_labels(item, options, allowed, copy_page_id, known_secrets)
        if labels:
            result["blocked"].append({"key": key, "labels": labels})
            log(f"{label} : bloqué sans écriture ({', '.join(labels)})")
            continue
        if item["page_id"] in failed_pages:
            message = "non tentée : une opération précédente de la page a échoué"
            result["errors"].append({"key": key, "message": message})
            log(f"{label} : {message}")
            continue
        try:
            written = _send(client, item, bot_id)
        except NotionError as error:
            stop = error.status in _STOPPING_STATUSES or error.status >= 500
            message = str(error)
            if stop:
                message += " Application interrompue : relancer la commande pour reprendre."
            result["errors"].append({"key": key, "message": message})
            log(f"{label} : échec, {message}")
            if stop:
                break
            failed_pages.add(item["page_id"])
            continue
        recorded.add(key)
        entries.append(_entry(item))
        write_json(journal, {"applied": entries})
        if written:
            result["done"] += 1
            log(f"{label} : écrit")
        else:
            result["skipped"] += 1
            log(f"{label} : commentaire déjà présent sur la page")
    return result


def _refuse_reference(plan: dict, reference_ds_id: str) -> None:
    copy_ds = plan.get("copy_data_source_id")
    if not isinstance(copy_ds, str) or not copy_ds.strip():
        raise ValueError("plan invalide : copy_data_source_id est absent.")
    if not isinstance(reference_ds_id, str) or not reference_ds_id.strip():
        raise ValueError("data source du modèle inconnue : impossible de vérifier la copie.")
    if _normalize(copy_ds) == _normalize(reference_ds_id):
        raise ReferenceWriteRefused(REFUSED_MESSAGE)


def validate_plan(plan: dict) -> list:
    """Return the operations of a plan, or raise ValueError when its structure is wrong."""
    ops = plan.get("ops")
    if not isinstance(ops, list):
        raise ValueError("plan invalide : la liste « ops » est absente.")
    for number, item in enumerate(ops, 1):
        problem = _problem(item)
        if problem:
            raise ValueError(f"plan invalide : opération {number} : {problem}.")
    return ops


def _live_options(client, plan: dict, ops: list) -> list:
    """Return the assignee options of the plan that the copy still has, read now.

    The plan's list is a snapshot: an option removed from the copy since would be recreated
    by Notion on write. Nothing is read when the plan has no assignment.
    """
    if not any(item["kind"] == "assign" for item in ops):
        return []
    live = assignee_options(client.data_source(plan["copy_data_source_id"]))
    return [name for name in plan.get("assignee_options") or [] if name in live]


def _problem(item) -> Optional[str]:
    """Return what is wrong with an operation of the plan, or None."""
    if not isinstance(item, dict):
        return "elle n'est pas un objet"
    for name in ("key", "page_id"):
        if not isinstance(item.get(name), str) or not item[name].strip():
            return f"« {name} » est absent ou vide"
    kind = item.get("kind")
    if kind not in _KIND_ORDER:
        return f"type inconnu ({kind!r})"
    text, after = item.get("text"), item.get("after")
    if text is not None and not isinstance(text, str):
        return "« text » n'est pas un texte"
    if kind == "status" and (not isinstance(after, str) or not after.strip()):
        return "« after » doit être le nom d'un statut"
    if kind == "assign" and not _is_names(after):
        return "« after » doit être la liste des noms assignés"
    if kind in ("comment", "page_comment") and not (text and text.strip()):
        return "« text » est vide"
    return None


def _order(item: dict) -> tuple:
    ref, kind = item.get("ref"), item["kind"]
    return (kind == "page_comment", not _is_ref(ref), ref if _is_ref(ref) else 0, _KIND_ORDER[kind])


def _blocking_labels(item, options, allowed, copy_page_id, known_secrets) -> list:
    labels = []
    page = _normalize(item["page_id"])
    if item["kind"] == "page_comment":
        permitted = copy_page_id is not None and page == _normalize(copy_page_id)
    else:
        permitted = page in allowed
    if not permitted:
        labels.append("page_outside_copy")
    for text in _outgoing_texts(item):
        labels.extend(label for label in scan_text(text, known_secrets) if label not in labels)
    if item["kind"] == "assign" and any(name not in options for name in item["after"]):
        labels.append("unknown_option")
    return labels


def _outgoing_texts(item: dict) -> list:
    after = item.get("after")
    texts = [item.get("text") or ""]
    if isinstance(after, str):
        texts.append(after)
    elif isinstance(after, list):
        texts.extend(name for name in after if isinstance(name, str))
    return texts


def _send(client, item: dict, bot_id: Optional[str]) -> bool:
    """Send one operation. Return False when a comment was already on the page."""
    kind, page_id = item["kind"], item["page_id"]
    if kind == "status":
        client.update_page(page_id, {STATUS_PROPERTY: {"status": {"name": item["after"]}}})
    elif kind == "assign":
        names = [{"name": name} for name in item["after"]]
        client.update_page(page_id, {ASSIGNEE_PROPERTY: {"multi_select": names}})
    else:
        if _already_commented(client, page_id, item["text"], bot_id):
            return False
        client.create_comment(page_id, to_rich_text(item["text"]))
    return True


def _already_commented(client, page_id: str, text: str, bot_id: Optional[str]) -> bool:
    wanted = text.strip()
    for comment in client.comments(page_id):
        if bot_id is not None and (comment.get("created_by") or {}).get("id") != bot_id:
            continue
        shown = "".join(part.get("plain_text", "") for part in comment.get("rich_text") or [])
        if shown.strip() == wanted:
            return True
    return False


def _load_journal(path: Path) -> list:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise ValueError(f"notion/applied.json est illisible : {error}")
    entries = document.get("applied") if isinstance(document, dict) else None
    valid = isinstance(entries, list) and all(
        isinstance(entry, dict) and isinstance(entry.get("key"), str) for entry in entries
    )
    if not valid:
        raise ValueError("notion/applied.json : la liste « applied » est invalide.")
    return entries


def _entry(item: dict) -> dict:
    return {
        "key": item["key"],
        "kind": item["kind"],
        "ref": item.get("ref"),
        "verdict": item.get("verdict"),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _describe(item: dict) -> str:
    ref = item.get("ref")
    where = ref_label(ref) if _is_ref(ref) else "page de la copie"
    return f"{where} ({item['kind']})"


def _is_ref(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_names(value) -> bool:
    return isinstance(value, list) and all(isinstance(name, str) for name in value)


def _normalize(identifier: str) -> str:
    return identifier.replace("-", "").lower()
