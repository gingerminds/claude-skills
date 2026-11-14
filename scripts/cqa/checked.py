"""The evidence check of the findings, frozen when a findings file is validated.

findings/<lot>.checked.json holds, for each findings file of the lot that went through
validate (or rerun-close), what the check of its evidence found at that moment:

    {"lot": "<lot id>",
     "files": {"<findings file>": {"sha256": "<digest of its JSON content>",
                                   "findings": [{"ref", "checked_at", "files": [{"repo", "path"}],
                                                 "tools": ["<tool>"], "problems": ["<text>"],
                                                 "refused": [{"evidence", "problem", "label"}],
                                                 "retry": [{"evidence", "problem"}]}]}}}

"problems" set the finding aside; "refused" names the pieces of evidence dropped from it (their
number, counting from 1, and the French texts, see cqa.grounding.Problem); "retry" names, among
both, the problems of a tool or of a probe, which a reader checks again (a tool that had not
finished). A record written before "refused" or "retry" existed has none. The digest is the one of
the JSON content of the file (digest), so a file written again with the same content, keys in
another order or another indentation, keeps its record.

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


def digest(document) -> str:
    """Return the sha256 of the canonical JSON of a parsed findings file (sorted keys, no spaces,
    ASCII escapes: a lone surrogate, which JSON allows, has no UTF-8 form)."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry(
    ref: int, evidence: list, problems: list, checked_at: str, refused: tuple = (), retry: tuple = ()
) -> dict:
    """Return what was checked for one finding: the cited files and tools, and the problems.

    refused holds the pieces of evidence dropped from the finding, as
    {"evidence": <number>, "problem": <text>, "label": <short reason>}; retry the problems of a tool
    or of a probe, as {"evidence": <number>, "problem": <the text in problems or refused>}.
    """
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
        "refused": [dict(item) for item in refused],
        "retry": [dict(item) for item in retry],
    }


def load(run_dir: Path, lot_id: str, name: str, file_digest: str) -> Optional[dict]:
    """Return {ref: {"problems", "refused", "retry"}} recorded for findings/<name>, None when no
    record covers the file.

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
        refused = item.get("refused", []) if isinstance(item, dict) else None
        retry = item.get("retry", []) if isinstance(item, dict) else None
        if (
            not isinstance(ref, int)
            or isinstance(ref, bool)
            or not _texts(problems)
            or not isinstance(refused, list)
            or not all(_is_refusal(refusal) for refusal in refused)
            or not isinstance(retry, list)
            or not all(_is_numbered(again) for again in retry)
        ):
            return None
        found[ref] = {"problems": problems, "refused": refused, "retry": retry}
    return found


def has_block(run_dir: Path, lot_id: str, name: str) -> bool:
    """Whether the record of the lot holds a block for findings/<name>, whatever its content."""
    return name in _blocks(run_dir, lot_id)


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


def _texts(value) -> bool:
    return isinstance(value, list) and all(isinstance(text, str) for text in value)


def _is_refusal(value) -> bool:
    return _is_numbered(value) and isinstance(value.get("label"), str)


def _is_numbered(value) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("evidence"), int)
        and not isinstance(value.get("evidence"), bool)
        and value["evidence"] >= 1
        and isinstance(value.get("problem"), str)
    )


def _blocks(run_dir: Path, lot_id: str) -> dict:
    try:
        document = json.loads(record_path(run_dir, lot_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    blocks = document.get("files") if isinstance(document, dict) else None
    return blocks if isinstance(blocks, dict) else {}
