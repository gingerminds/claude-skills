"""The evidence check of the findings, frozen when a findings file is validated.

findings/<lot>.checked.json holds, for each findings file of the lot that went through
validate (or rerun-close), what the check of its evidence found at that moment:

    {"lot": "<lot id>",
     "files": {"<findings file>": {"sha256": "<digest of the file>",
                                   "findings": [{"ref", "checked_at", "files": [{"repo", "path"}],
                                                 "tools": ["<tool>"], "problems": ["<text>"]}]}}}

A reader of the findings that decides something trusts the problems recorded for a finding when
the digest of its file is still the one recorded, instead of checking again the cited files and
tools against a workstation that moved since (a fix merged, a tool relaunched).
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cqa.notion_read import write_json

_FOLDER = "findings"


def record_path(run_dir: Path, lot_id: str) -> Path:
    return Path(run_dir) / _FOLDER / f"{lot_id}.checked.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry(ref: int, evidence: list, problems: list, checked_at: str) -> dict:
    """Return what was checked for one finding: the cited files and tools, and the problems."""
    files = [
        {"repo": item["repo"], "path": item["path"]} for item in evidence if item["type"] == "file"
    ]
    tools = [item["tool"] for item in evidence if item["type"] == "tool"]
    return {
        "ref": ref,
        "checked_at": checked_at,
        "files": files,
        "tools": list(dict.fromkeys(tools)),
        "problems": list(problems),
    }


def load(run_dir: Path, lot_id: str, name: str, file_digest: str) -> Optional[dict]:
    """Return {ref: [problems]} recorded for findings/<name>, None when no record covers the file.

    No record covers it when there is none, when it is damaged, or when it was made for another
    content of the file.
    """
    block = _blocks(run_dir, lot_id).get(name)
    if not isinstance(block, dict) or block.get("sha256") != file_digest:
        return None
    entries = block.get("findings")
    if not isinstance(entries, list):
        return None
    found = {}
    for item in entries:
        ref = item.get("ref") if isinstance(item, dict) else None
        problems = item.get("problems") if isinstance(item, dict) else None
        if (
            not isinstance(ref, int)
            or isinstance(ref, bool)
            or not isinstance(problems, list)
            or not all(isinstance(problem, str) for problem in problems)
        ):
            return None
        found[ref] = problems
    return found


def freeze(run_dir: Path, lot_id: str, name: str, file_digest: str, entries: list) -> None:
    """Record the entries of findings/<name>, replacing the block of that file only."""
    blocks = _blocks(run_dir, lot_id)
    blocks[name] = {"sha256": file_digest, "findings": entries}
    write_json(record_path(run_dir, lot_id), {"lot": lot_id, "files": dict(sorted(blocks.items()))})


def forget(run_dir: Path, lot_id: str, name: str) -> None:
    """Drop the block of findings/<name> (a file that left findings/); the record goes with its last block."""
    blocks = _blocks(run_dir, lot_id)
    if name not in blocks:
        return
    del blocks[name]
    path = record_path(run_dir, lot_id)
    if blocks:
        write_json(path, {"lot": lot_id, "files": blocks})
    else:
        path.unlink()


def _blocks(run_dir: Path, lot_id: str) -> dict:
    try:
        document = json.loads(record_path(run_dir, lot_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    blocks = document.get("files") if isinstance(document, dict) else None
    return blocks if isinstance(blocks, dict) else {}
