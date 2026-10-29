"""Queue of the decisions an unattended audit sets aside for the lead (deferred.json)."""
import json
from datetime import datetime, timezone
from pathlib import Path

from cqa.checklist import parse_refs
from cqa.notion_read import write_json

AUDIT_KINDS = ("login", "ssh_refused", "gap", "question")
FIX_KINDS = ("fix_question",)
KINDS = AUDIT_KINDS + FIX_KINDS
FILE = "deferred.json"


def _read(run_dir: Path) -> list:
    try:
        document = json.loads((run_dir / FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise ValueError(f"{FILE} est illisible : {error}")
    items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"{FILE} est illisible : « items » doit être une liste.")
    for item in items:
        valid = (
            isinstance(item, dict)
            and isinstance(item.get("id"), int)
            and not isinstance(item.get("id"), bool)
        )
        if not valid:
            raise ValueError(f"{FILE} est illisible : chaque entrée doit être un objet avec un « id » entier.")
    return items


def add(run_dir: Path, kind: str, detail: str, refs: str = "") -> dict:
    """Append an open entry and return it."""
    if kind not in KINDS:
        raise ValueError(f"« {kind} » n'est pas un type connu (attendu : {', '.join(KINDS)}).")
    if not isinstance(detail, str) or not detail.strip():
        raise ValueError("Le détail d'une décision mise de côté ne peut pas être vide.")
    parsed = parse_refs(refs)
    items = _read(run_dir)
    entry = {
        "id": max([item.get("id", 0) for item in items] + [0]) + 1,
        "kind": kind,
        "detail": detail.strip(),
        "refs": parsed,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resolved": False,
    }
    write_json(run_dir / FILE, {"items": items + [entry]})
    return entry


def all_items(run_dir: Path) -> list:
    return _read(run_dir)


def open_items(run_dir: Path, *, fix: bool = False) -> list:
    """Return the open entries: those of the audit, or with fix those of the fix mode (fix_*)."""
    return [
        item
        for item in _read(run_dir)
        if not item.get("resolved") and str(item.get("kind")).startswith("fix_") == fix
    ]


def resolve(run_dir: Path, item_id: int) -> None:
    """Close an entry; raise ValueError when no entry has that id."""
    items = _read(run_dir)
    for item in items:
        if item.get("id") == item_id:
            item["resolved"] = True
            write_json(run_dir / FILE, {"items": items})
            return
    raise ValueError(f"Aucune décision mise de côté n'a le numéro {item_id}.")
