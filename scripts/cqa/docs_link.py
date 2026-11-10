"""Link the points of the imported context documents to the rows of the checklist.

A point is linked by criterion (the criteria of the document cited by the fiche of a row), or by
a proposal the orchestrator makes once for the whole document. Only the rows of the categories
of the document, and of the category scope of the run, are candidates. The content of a
document is data: a text is cleaned before it is written to links.md, and links.md is read back
line by line, every line being checked.
"""
import copy
import json
import re
from pathlib import Path
from typing import Optional

from cqa import checklist, context_docs
from cqa.categories import in_scope, resolve
from cqa.notion_read import write_json, write_text

LINKS_FILE = "links.json"
MARKDOWN_FILE = "links.md"
MAX_PROPOSED = 5
HOWS = ("criterion", "proposed")

_FICHES = "fiches"
_POINT_ID = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_HEADING = re.compile(r"(#{1,6})\s")
_HEADER_LINE = re.compile(r"Catégories\s+(\S+)\s*:(.*)")
_MODES = {
    "critère": "criterion",
    "critere": "criterion",
    "criterion": "criterion",
    "proposé": "proposed",
    "propose": "proposed",
    "proposed": "proposed",
    "sans ligne": None,
    "": None,
}
_MODE_LABELS = {"criterion": "critère", "proposed": "proposé", None: "sans ligne"}
_MARKDOWN_HEAD = (
    "# Rattachement des points du document aux lignes de la checklist",
    "# Une ligne par point : - <point> | CQ-12, CQ-40 | <critère ou proposé> | <résumé>",
    "# Videz la colonne des Réf pour détacher un point. Les Réf doivent être des lignes des catégories du document.",
    "# Le résumé est donné pour s'y retrouver : il n'est pas relu.",
)


def candidate_rows(rows: list, categories: list, category_filter: Optional[list]) -> list:
    """Return the rows with a ref and a fiche, of a category of the list and of the run scope."""
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and _is_ref(row.get("ref"))
        and row.get("fiche_path")
        and row.get("category") in categories
        and in_scope(row, category_filter)
    ]


def row_criteria(run_dir: Path, row: dict, framework: str) -> list:
    """Return the criteria of the framework that the fiche of a row cites, in order, once each.

    They come from the section "### Critères <first word of the framework> ..." of the fiche,
    whose items read "- [7.3](url) : title". A fiche that is absent, has no such section or
    cites another framework gives none.
    """
    words = framework.split() if isinstance(framework, str) else []
    text = _fiche_text(run_dir, row)
    if not words or text is None:
        return []
    start = re.compile(
        rf"(#{{2,6}})\s*Crit[èeé]res\s+{re.escape(words[0])}(?=\s|$)", re.IGNORECASE
    )
    found: list = []
    level = 0
    for line in text.splitlines():
        if level:
            heading = _HEADING.match(line)
            if heading and len(heading.group(1)) <= level:
                level = 0
            else:
                criterion = _criterion_of(line)
                if criterion and criterion not in found:
                    found.append(criterion)
                continue
        opening = start.match(line.strip())
        if opening:
            level = len(opening.group(1))
    return found


def criteria_index(run_dir: Path, rows: list, framework: str) -> dict:
    """Return {criterion: [refs of the rows whose fiche cites it, sorted]}."""
    index: dict = {}
    for row in rows:
        if not isinstance(row, dict) or not _is_ref(row.get("ref")):
            continue
        for criterion in row_criteria(run_dir, row, framework):
            index.setdefault(criterion, set()).add(row["ref"])
    return {criterion: sorted(refs) for criterion, refs in index.items()}


def link(
    run_dir: Path, rows: list, category_filter: Optional[list], proposed: Optional[dict] = None
) -> dict:
    """Link the points of the imported documents to the candidate rows.

    Return {"validated": False, "categories": {doc: [Notion names]}, "links": {point: {"refs",
    "how"}}, "to_propose": [points], "unlinked": [points]}. A point with criteria takes the
    candidate rows whose fiche cites one of them (how "criterion"). Any other point is
    proposed once the orchestrator has proposals: without proposals (proposed is None) it goes
    to to_propose when the document has candidate rows, otherwise, or when the proposals give
    it no candidate row, it is unlinked. A proposal keeps the candidate rows only, five at
    most. Documents that are not imported, or whose items cannot be read, are left out.
    """
    result: dict = {
        "validated": False,
        "categories": {},
        "links": {},
        "to_propose": [],
        "unlinked": [],
    }
    proposals = proposed if isinstance(proposed, dict) else {}
    indexes: dict = {}
    for doc_id, document in _imported(run_dir):
        meta = document["document"]
        names = [name for name in _list(meta.get("categories")) if isinstance(name, str)]
        resolved, _unknown = resolve(names, rows)
        result["categories"][doc_id] = resolved
        candidates = candidate_rows(rows, resolved, category_filter)
        refs = {row["ref"] for row in candidates}
        framework = meta.get("framework") if isinstance(meta.get("framework"), str) else ""
        key = (framework.split()[0] if framework.split() else "", tuple(resolved))
        if key not in indexes:
            indexes[key] = {
                _fold(criterion): found
                for criterion, found in criteria_index(run_dir, candidates, framework).items()
            }
        for point in _points(document):
            point_id = point["id"]
            matched = sorted(
                {
                    ref
                    for criterion in _list(point.get("criteria"))
                    if isinstance(criterion, str)
                    for ref in indexes[key].get(_fold(criterion), ())
                }
            )
            chosen = _proposal(proposals.get(point_id), refs) if not matched else []
            if matched:
                result["links"][point_id] = {"refs": matched, "how": "criterion"}
            elif chosen:
                result["links"][point_id] = {"refs": chosen, "how": "proposed"}
            elif proposed is None and candidates:
                result["to_propose"].append(point_id)
            else:
                result["unlinked"].append(point_id)
    return result


def counts(links: dict) -> dict:
    """Return the figures of the validation question: points, rows, proposed, unlinked, categories."""
    entries = links.get("links") or {}
    refs = {ref for entry in entries.values() for ref in entry.get("refs", ())}
    names: list = []
    for listed in (links.get("categories") or {}).values():
        names.extend(name for name in listed if name not in names)
    return {
        "points": len(entries),
        "rows": len(refs),
        "proposed": sum(1 for entry in entries.values() if entry.get("how") == "proposed"),
        "unlinked": len(links.get("unlinked") or ()),
        "categories": names,
    }


def render_markdown(links: dict, items_by_doc: dict) -> str:
    """Return links.md: the categories of each document, then one line per point.

    items_by_doc is {doc: [points]}. The summary is cleaned to one line and cannot hold the
    separator, so that a text of the document never adds a line or a column.
    """
    lines = list(_MARKDOWN_HEAD)
    entries = links.get("links") or {}
    for doc_id, points in items_by_doc.items():
        names = (links.get("categories") or {}).get(doc_id, [])
        lines += ["", f"Catégories {doc_id} : {', '.join(names)}"]
        for point in points:
            if not isinstance(point, dict) or not _safe_id(point.get("id")):
                continue
            entry = entries.get(point["id"]) or {}
            refs = ", ".join(checklist.ref_label(ref) for ref in entry.get("refs", ()))
            how = _MODE_LABELS.get(entry.get("how") if entry.get("refs") else None, "sans ligne")
            summary = context_docs.clean_text(point.get("summary")).replace("|", "/")
            lines.append(f"- {point['id']} | {refs} | {how} | {summary}")
    return "\n".join(lines) + "\n"


def markdown_categories(text: str) -> dict:
    """Return {doc: [names]} of the "Catégories d1 : A, B" lines of links.md, as written."""
    return {doc_id: names for _number, doc_id, names in _headers(text)}


def parse_markdown(text: str, links: dict, candidate_refs: set) -> dict:
    """Return the links with the edits of links.md; raise ValueError, with the line, on any problem.

    A line gives the refs of a point: an empty column detaches it, a ref makes it linked (how
    "proposed" for a point that was not linked before, otherwise the mode written on the line).
    A point without line is left as it was. Every line is checked: a known point once, refs
    among candidate_refs, a known mode. The summary column is not read.
    """
    known = set(links.get("links") or {}) | set(links.get("unlinked") or ()) | set(
        links.get("to_propose") or ()
    )
    result = copy.deepcopy(links)
    result.setdefault("categories", {})
    result.setdefault("links", {})
    seen: set = set()
    unlinked = list(result.get("unlinked") or [])
    to_propose = list(result.get("to_propose") or [])
    for number, line in _numbered(text):
        header = _HEADER_LINE.fullmatch(line)
        if header:
            doc_id = header.group(1)
            if doc_id not in links.get("categories", {}):
                raise ValueError(f"ligne {number} : document inconnu : {_show(doc_id)}.")
            result["categories"][doc_id] = _names(header.group(2))
            continue
        point_id, refs, mode = _point_line(number, line)
        if point_id not in known:
            raise ValueError(f"ligne {number} : point inconnu : {_show(point_id)}.")
        if point_id in seen:
            raise ValueError(f"ligne {number} : point en double : {point_id}.")
        seen.add(point_id)
        for ref in refs:
            if ref not in candidate_refs:
                raise ValueError(
                    f"ligne {number} : {checklist.ref_label(ref)} n'est pas une ligne "
                    "candidate (catégories du document et périmètre du run)."
                )
        previous = (result["links"].get(point_id) or {}).get("how")
        to_propose = [item for item in to_propose if item != point_id]
        unlinked = [item for item in unlinked if item != point_id]
        if refs:
            result["links"][point_id] = {"refs": refs, "how": mode or previous or "proposed"}
        else:
            result["links"].pop(point_id, None)
            unlinked.append(point_id)
    result["unlinked"] = unlinked
    result["to_propose"] = to_propose
    return result


def read_links(run_dir: Path, *, validated_only: bool = True) -> dict:
    """Return context-docs/links.json; {} when it is absent, unreadable, unsound or not validated.

    Entries that are not {"refs": [int], "how": criterion|proposed} are dropped.
    """
    try:
        document = json.loads((Path(run_dir) / context_docs.FOLDER / LINKS_FILE).read_text("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(document, dict):
        return {}
    categories = document.get("categories")
    entries = document.get("links")
    unlinked = document.get("unlinked")
    to_propose = document.get("to_propose")
    if not (
        isinstance(categories, dict)
        and isinstance(entries, dict)
        and isinstance(unlinked, list)
        and isinstance(to_propose, list)
    ):
        return {}
    if validated_only and document.get("validated") is not True:
        return {}
    return {
        "validated": document.get("validated") is True,
        "categories": {
            doc_id: [name for name in names if isinstance(name, str)]
            for doc_id, names in categories.items()
            if isinstance(names, list)
        },
        "links": {
            point_id: {"refs": list(entry["refs"]), "how": entry["how"]}
            for point_id, entry in entries.items()
            if _sound_entry(entry)
        },
        "to_propose": [item for item in to_propose if isinstance(item, str)],
        "unlinked": [item for item in unlinked if isinstance(item, str)],
    }


def write_links(run_dir: Path, links: dict) -> None:
    """Write context-docs/links.json."""
    folder = Path(run_dir) / context_docs.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / LINKS_FILE, links)


def write_markdown(run_dir: Path, text: str) -> Path:
    """Write context-docs/links.md and return its path."""
    folder = Path(run_dir) / context_docs.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    write_text(folder / MARKDOWN_FILE, text)
    return folder / MARKDOWN_FILE


def points_by_ref(run_dir: Path, verdicts: tuple = ("ko",)) -> dict:
    """Return {ref: [points]} from the validated links, the points of the given verdicts only.

    Each point is a copy of the point of items.json with "doc", "label" and "date" of its
    document, in the order of the point ids. A document that is no longer imported gives none.
    """
    links = read_links(run_dir)
    found: dict = {}
    if not links:
        return found
    for doc_id, document in _imported(run_dir):
        meta = document["document"]
        for point in sorted(_points(document), key=lambda item: item["id"]):
            entry = links["links"].get(point["id"])
            if not entry or point.get("verdict") not in verdicts:
                continue
            enriched = {
                **copy.deepcopy(point),
                "doc": doc_id,
                "label": context_docs.clean_text(meta.get("label")),
                "date": context_docs.clean_text(meta.get("date")),
            }
            for ref in entry["refs"]:
                found.setdefault(ref, []).append(copy.deepcopy(enriched))
    return {ref: found[ref] for ref in sorted(found)}


def imported_points(run_dir: Path) -> dict:
    """Return {doc: [points]} of the imported documents whose items.json can be read."""
    return {doc_id: _points(document) for doc_id, document in _imported(run_dir)}


def _imported(run_dir: Path):
    """Yield (doc_id, items.json) of the imported documents whose items can be read."""
    seen: set = set()
    for entry in context_docs.read_index(run_dir)["docs"]:
        doc_id = entry.get("id")
        if entry.get("status") != "imported" or not isinstance(doc_id, str) or doc_id in seen:
            continue
        seen.add(doc_id)
        document = context_docs.read_items(run_dir, doc_id)
        if document is not None:
            yield doc_id, document


def _points(document: dict) -> list:
    return [
        item
        for item in document["items"]
        if isinstance(item, dict) and _safe_id(item.get("id"))
    ]


def _safe_id(value) -> bool:
    return isinstance(value, str) and bool(_POINT_ID.fullmatch(value))


def _is_ref(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _fold(text: str) -> str:
    return text.strip().casefold()


def _show(value) -> str:
    return context_docs.clean_text(value)[:40]


def _proposal(value, candidates: set) -> list:
    """Return the candidate refs of a proposal (list or text of refs), five at most, sorted."""
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    refs: list = []
    for item in items:
        try:
            found = [item] if _is_ref(item) else checklist.parse_refs(item) if isinstance(item, str) else []
        except ValueError:
            found = []
        refs.extend(ref for ref in found if ref in candidates and ref not in refs)
    return sorted(refs[:MAX_PROPOSED])


def _sound_entry(entry) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("how") in HOWS
        and isinstance(entry.get("refs"), list)
        and all(_is_ref(ref) for ref in entry["refs"])
    )


def _fiche_text(run_dir: Path, row: dict) -> Optional[str]:
    path = row.get("fiche_path")
    if isinstance(path, str) and path:
        name = Path(path).name
    elif _is_ref(row.get("ref")):
        name = checklist.fiche_filename(row["ref"])
    else:
        return None
    try:
        return (Path(run_dir) / _FICHES / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _criterion_of(line: str) -> Optional[str]:
    """Return the criterion of a list item of a fiche ("- [7.3](url) : title"), or None."""
    item = line.strip()
    if item[:2] not in ("- ", "* ", "+ "):
        return None
    rest = item[2:].strip()
    if rest.startswith("["):
        end = rest.find("]")
        token = rest[1:end] if end > 0 else ""
    else:
        token = re.split(r"[\s:]", rest, maxsplit=1)[0]
        if not any(char.isdigit() for char in token):
            return None
    token = token.strip()
    if not token or len(token) > context_docs.LIMITS["criterion"]:
        return None
    return token


def _numbered(text: str):
    """Yield (number, line) of the lines of links.md that carry something, comments excluded."""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield number, stripped


def _names(text: str) -> list:
    names: list = []
    for name in text.split(","):
        name = context_docs.clean_text(name)
        if name and name not in names:
            names.append(name)
    return names


def _headers(text: str) -> list:
    found = []
    for number, line in _numbered(text):
        header = _HEADER_LINE.fullmatch(line)
        if header:
            found.append((number, header.group(1), _names(header.group(2))))
    return found


def _point_line(number: int, line: str) -> tuple:
    """Return (point, refs, mode) of a line "- point | refs | mode | summary"."""
    parts = [part.strip() for part in line[2:].split("|", 3)] if line.startswith("- ") else []
    if len(parts) != 4:
        raise ValueError(
            f"ligne {number} : forme attendue « - <point> | CQ-12, CQ-40 | <critère ou proposé> | <résumé> »."
        )
    point_id, refs_text, mode_text, _summary = parts
    if mode_text.casefold() not in _MODES:
        raise ValueError(
            f"ligne {number} : « {_show(mode_text)} » n'est pas un mode de rattachement "
            "(critère, proposé ou sans ligne)."
        )
    try:
        refs = checklist.parse_refs(refs_text)
    except ValueError as error:
        raise ValueError(f"ligne {number} : {error}") from None
    return point_id, refs, _MODES[mode_text.casefold()]
