"""Writing a plan to Notion: the only code that changes the copy, behind its safety gates."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from cqa.checklist import ref_label
from cqa.comments import parse_verdict, to_rich_text
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

    The report is {"done", "skipped", "blocked", "errors", "replace_failed"}: done and skipped
    are counts, blocked lists {"key", "labels"}, errors {"key", "message"} and replace_failed
    {"key", "ref", "reason"}, the skill comments that Notion refused to edit or delete.

    A row comment replaces the skill's own comment of the row (written by bot_id, with a
    "Verdict : ..." line): the latest is edited in place, the others are deleted, human comments
    are never touched. When Notion refuses (403) the edit, all skill comments are deleted and a
    new one is created; when it refuses the deletion too, the comment is created anyway.

    A plan whose copy is the reference data source, or with an operation on reference_page_id
    (the page of the template), is refused with ReferenceWriteRefused, and a malformed plan or
    journal with ValueError, all before any request. When the plan assigns,
    the schema of the copy is then read: its NotionError is not caught, nothing has been
    written. An operation is blocked, without any request, when its page is not a row of the
    copy (a page_comment may also target copy_page_id), when its text or target value holds a
    secret, or when an assignment names an option that the plan does not list or that the copy
    no longer has (Notion would create it). Operations go by ref, then status, assign and
    comment, the page_comment ones last.

    notion/applied.json in run_dir lists what was written and is rewritten after each success
    (entries accumulate across applies; a "status" entry carries "value", the status written,
    and a "comment" entry "comment_id" and "mode"; older journals have neither):
    its keys are skipped on a rerun. A comment is also skipped when the page already shows a
    comment with the same text (from bot_id, or from anyone when it is None), as Notion may
    have stored one whose answer was lost. A network error, an authentication failure, a 429
    or a 5xx stops the plan; any other Notion error only fails its operation and the later
    ones of the same page. Only written, blocked and failed operations are logged: those
    already present (journal or comment on the page) are counted in skipped, silently.
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
    result = {"done": 0, "skipped": 0, "blocked": [], "errors": [], "replace_failed": []}
    failed_pages = set()
    for item in sorted(ops, key=_order):
        key, label = item["key"], _describe(item)
        if key in recorded:
            result["skipped"] += 1
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
            sent = _send(client, item, bot_id)
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
        entries.append(_entry(item, sent))
        write_json(journal, {"applied": entries})
        for reason in sent.get("failed", []):
            result["replace_failed"].append({"key": key, "ref": item.get("ref"), "reason": reason})
            log(f"{label} : ancien commentaire conservé ({reason})")
        if not sent.get("skipped"):
            result["done"] += 1
            log(f"{label} : écrit")
        else:
            result["skipped"] += 1
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


def _send(client, item: dict, bot_id: Optional[str]) -> dict:
    """Send one operation and return what it did.

    A comment gives {"comment_id", "mode", "failed"}, or {"skipped": True, "failed"} when the
    text was already on the page; the other operations give {}.
    """
    kind, page_id = item["kind"], item["page_id"]
    if kind == "status":
        client.update_page(page_id, {STATUS_PROPERTY: {"status": {"name": item["after"]}}})
    elif kind == "assign":
        names = [{"name": name} for name in item["after"]]
        client.update_page(page_id, {ASSIGNEE_PROPERTY: {"multi_select": names}})
    else:
        existing = client.comments(page_id)
        own = _skill_comments(existing, bot_id) if kind == "comment" else []
        if own and _plain_text(own[-1]).strip() == item["text"].strip():
            return {"skipped": True, "failed": _delete_all(client, own[:-1])}
        if not own and _shown_in(existing, item["text"], bot_id):
            return {"skipped": True, "failed": []}
        return _write_comment(client, page_id, item["text"], own)
    return {}


def _write_comment(client, page_id: str, text: str, own: list) -> dict:
    """Write a comment, replacing the skill's own ones (oldest first in own) when there are."""
    rich_text = to_rich_text(text)
    if own and _try_update(client, own[-1]["id"], rich_text):
        failed = _delete_all(client, own[:-1])
        return {"comment_id": own[-1]["id"], "mode": "update", "failed": failed}
    failed = _delete_all(client, own)
    created = client.create_comment(page_id, rich_text)
    mode = "recreate" if own and not failed else "create"
    return {"comment_id": created.get("id"), "mode": mode, "failed": failed}


def _try_update(client, comment_id: str, rich_text: list) -> bool:
    """Edit a comment. False when Notion refuses (403) or no longer finds it (404)."""
    try:
        client.update_comment(comment_id, rich_text)
    except NotionError as error:
        if error.status not in (403, 404):
            raise
        return False
    return True


def _delete_all(client, comments: list) -> list:
    """Delete comments and return the reason of each deletion refused (403).

    A 404 means the comment is already gone and counts as deleted.
    """
    failed = []
    for comment in comments:
        try:
            client.delete_comment(comment["id"])
        except NotionError as error:
            if error.status == 403:
                failed.append(str(error))
            elif error.status != 404:
                raise
    return failed


def _skill_comments(comments: list, bot_id: Optional[str]) -> list:
    """Return the comments the integration wrote with a verdict, the oldest first."""
    if bot_id is None:
        return []
    own = [
        comment
        for comment in comments
        if (comment.get("created_by") or {}).get("id") == bot_id
        and parse_verdict(_plain_text(comment)) is not None
    ]
    return sorted(own, key=lambda comment: comment.get("created_time") or "")


def _plain_text(comment: dict) -> str:
    return "".join(part.get("plain_text", "") for part in comment.get("rich_text") or [])


def _shown_in(comments: list, text: str, bot_id: Optional[str]) -> bool:
    wanted = text.strip()
    return any(
        _plain_text(comment).strip() == wanted
        for comment in comments
        if bot_id is None or (comment.get("created_by") or {}).get("id") == bot_id
    )


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


def _entry(item: dict, sent: dict) -> dict:
    entry = {
        "key": item["key"],
        "kind": item["kind"],
        "ref": item.get("ref"),
        "verdict": item.get("verdict"),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if item["kind"] == "status":
        entry["value"] = item["after"]
    if "comment_id" in sent:
        entry["comment_id"] = sent["comment_id"]
        entry["mode"] = sent["mode"]
    return entry


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
