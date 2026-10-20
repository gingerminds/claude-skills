"""Reading the checklist from Notion: resolving a location, rows, fiches and past verdicts."""
import json
import os
from pathlib import Path
from typing import Optional

from cqa.checklist import STATUS_STANDBY, fiche_filename, match_reference, normalize_row
from cqa.comments import parse_verdict
from cqa.notion_api import NotionClient, NotionError, extract_id

FICHES_DIR = "fiches"
INDEX_NAME = "index.json"


def resolve(client: NotionClient, url_or_id: str) -> dict:
    """Return {"page_id", "database_id", "data_source_id"} for a database or a page holding one.

    A database id is read directly (page_id is None). When Notion says the id is not a database,
    it is a page: its first child_database block is the database. A database with no data source
    or with several is refused with a ValueError, so is a page without a database.
    """
    object_id = extract_id(url_or_id)
    try:
        database = client.database(object_id)
    except NotionError as error:
        if not _is_not_a_database(error):
            raise
        page_id = object_id
        database_id = _first_child_database(client, page_id)
        database = client.database(database_id)
    else:
        page_id = None
        database_id = object_id
    sources = database.get("data_sources") or []
    if not sources:
        raise ValueError("La base de données ne contient aucune data source.")
    if len(sources) > 1:
        raise ValueError(
            "La base de données contient plusieurs data sources : "
            "la copie ne doit en avoir qu'une."
        )
    return {"page_id": page_id, "database_id": database_id, "data_source_id": sources[0]["id"]}


def load_rows(client: NotionClient, ds_id: str, bot_id: str) -> list[dict]:
    """Return every row of a data source, normalized, in the order Notion gives them."""
    return [normalize_row(item, bot_id) for item in client.query_data_source(ds_id)]


def assemble_rows(copy_rows: list[dict], ref_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return the rows.json rows and missing_in_copy for a copy and its reference.

    A paired row takes the ref of its reference row, with its page and fiche path. An unpaired
    row has none of the three, whatever its own "Réf modèle" says: that value could collide
    with the ref of a paired row. A reference row without ref has no fiche file name.
    """
    pairs, _, missing = match_reference(copy_rows, ref_rows)
    reference_of = {copy_row["page_id"]: ref_row for copy_row, ref_row in pairs}
    rows = []
    for copy_row in copy_rows:
        ref_row = reference_of.get(copy_row["page_id"])
        ref = ref_row["ref"] if ref_row else None
        rows.append(
            {
                **copy_row,
                "ref": ref,
                "reference_page_id": ref_row["page_id"] if ref_row else None,
                "fiche_path": None if ref is None else f"{FICHES_DIR}/{fiche_filename(ref)}",
            }
        )
    return rows, [{"ref": item["ref"], "title": item["title"]} for item in missing]


def assignee_options(data_source: dict) -> list[str]:
    """Return the option names of "Assignée à", in schema order (empty when it is absent)."""
    prop = (data_source.get("properties") or {}).get("Assignée à") or {}
    options = (prop.get("multi_select") or {}).get("options") or []
    return [option["name"] for option in options]


def write_fiches(client: NotionClient, run_dir: Path, pairs: list[tuple[dict, dict]]) -> dict:
    """Cache the reference fiche of each pair as fiches/CQ-xxx.md and return fiches/index.json.

    Only the page_id and ref of the reference row (second item of a pair) are read. A pair
    without ref is skipped, and a ref is fetched once. A fiche counts as cached when its file
    and its index entry both exist: the index, written last, tells whether Notion truncated it.
    The index is also written when a fetch fails, so that a rerun resumes where this one stopped.
    """
    fiches_dir = Path(run_dir) / FICHES_DIR
    fiches_dir.mkdir(parents=True, exist_ok=True)
    index_path = fiches_dir / INDEX_NAME
    cached = _read_index(index_path)
    index = {}
    try:
        for _, ref_row in pairs:
            ref = ref_row["ref"]
            if ref is None or str(ref) in index:
                continue
            name = fiche_filename(ref)
            truncated = _cached_truncated(cached.get(str(ref)), fiches_dir / name)
            if truncated is None:
                fetched = client.page_markdown(ref_row["page_id"])
                write_text(fiches_dir / name, fetched["markdown"])
                truncated = fetched["truncated"]
            index[str(ref)] = {"path": f"{FICHES_DIR}/{name}", "truncated": truncated}
    finally:
        write_json(index_path, index)
    return index


def load_skill_comments(client: NotionClient, rows: list[dict], bot_id: str) -> dict:
    """Return {ref: [verdict, ...]} read in the comments the integration left on the rows.

    Rows "En standby" and rows without ref are not read. A comment counts only when the
    integration wrote it and it carries a known "Verdict : ..." line. Refs without verdict
    are left out, and verdicts keep the order of the comments.
    """
    verdicts = {}
    for item in rows:
        if item["ref"] is None or item["status"] == STATUS_STANDBY:
            continue
        found = []
        for entry in client.comments(item["page_id"]):
            if (entry.get("created_by") or {}).get("id") != bot_id:
                continue
            text = "".join(part.get("plain_text", "") for part in entry.get("rich_text") or [])
            verdict = parse_verdict(text)
            if verdict:
                found.append(verdict)
        if found:
            verdicts.setdefault(item["ref"], []).extend(found)
    return verdicts


def write_text(path: Path, text: str) -> None:
    """Write a UTF-8 file atomically: a temporary file, then a rename."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path: Path, value) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _is_not_a_database(error: NotionError) -> bool:
    """Whether Notion refused a database read because the id is something else (a page)."""
    return error.status == 404 or (error.status == 400 and error.code == "validation_error")


def _first_child_database(client: NotionClient, page_id: str) -> str:
    for block in client.block_children(page_id):
        if block.get("type") == "child_database":
            return block["id"]
    raise ValueError("La page ne contient aucune base de données.")


def _cached_truncated(entry, path: Path) -> Optional[bool]:
    """Return the truncated flag of a cached fiche, or None when it must be fetched again."""
    if isinstance(entry, dict) and isinstance(entry.get("truncated"), bool) and path.is_file():
        return entry["truncated"]
    return None


def _read_index(path: Path) -> dict:
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return index if isinstance(index, dict) else {}
