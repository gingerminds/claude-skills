"""Queue of the decisions an unattended audit sets aside for the lead (deferred.json)."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from cqa.notion_read import write_json

KINDS = ("login", "ssh_refused", "gap", "question")
FILE = "deferred.json"
_REF = re.compile(r"(?:CQ-)?([0-9]+)", re.IGNORECASE)


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
    return items


def parse_refs(text: str) -> list:
    """Return the sorted unique refs of "CQ-5, 12"; raise ValueError on anything else."""
    refs = set()
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        found = _REF.fullmatch(part)
        if not found:
            raise ValueError(f"« {part} » n'est pas une Réf (attendu : CQ-12 ou 12).")
        refs.add(int(found.group(1)))
    return sorted(refs)


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


def open_items(run_dir: Path) -> list:
    return [item for item in _read(run_dir) if not item.get("resolved")]


def resolve(run_dir: Path, item_id: int) -> None:
    """Close an entry; raise ValueError when no entry has that id."""
    items = _read(run_dir)
    for item in items:
        if item.get("id") == item_id:
            item["resolved"] = True
            write_json(run_dir / FILE, {"items": items})
            return
    raise ValueError(f"Aucune décision mise de côté n'a le numéro {item_id}.")
