"""Context documents (third-party audit reports, pentests): model, merge and check.

A document is untrusted data. The importer writes a layout and one part of points per sheet;
merge cleans every text, assigns the ids, computes the coverage from the rows of the source
and writes items.json. Nothing of a document is ever executed or followed as an instruction:
unknown keys are dropped, texts are cleaned, and validate_items refuses any field over its cap.
"""
import json
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

from cqa import docs_read
from cqa.notion_read import write_json
from cqa.secrets import scan_text

KINDS = ("grille", "constats")
OWNERS = ("dev", "contenu", "tiers", "infra", "inconnu")
POINT_VERDICTS = ("ko", "info")
ROLES = ("page", "transverse", "info", "skip")
LIMITS = {
    "label": 80,
    "framework": 40,
    "category": 80,
    "source": 120,
    "criterion": 20,
    "scope": 120,
    "priority": 40,
    "element": 200,
    "summary": 300,
    "fix_hint": 500,
}
MAX_POINTS = 500
MAX_CRITERIA = 20
MAX_SCOPES = 30
MAX_ELEMENTS = 20
MAX_CATEGORIES = 20
MAX_COVERAGE = 1000

FOLDER = "context-docs"
ITEMS_FILE = "items.json"
LAYOUT_FILE = "layout.json"
INDEX_FILE = "index.json"

_DOCUMENT_KEYS = ("id", "label", "date", "kind", "categories", "framework")
_POINT_KEYS = (
    "id",
    "source",
    "criteria",
    "scopes",
    "verdict",
    "priority",
    "summary",
    "fix_hint",
    "owner",
    "elements",
)
_COUNTS = ("ok", "na", "ko", "undetermined")
_COVERAGE_KEYS = _COUNTS + ("points",)
_DEFAULT_SCOPES = {"transverse": "transverse", "info": "global"}
_SHOWN = 30
_UNCLEAN = "n'est pas nettoyé (espaces, caractères de contrôle ou tiret long)"

_DOC_ID = re.compile(r"[A-Za-z0-9_]{1,20}")
_POINT_ID = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_CRITERION = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SPACES = re.compile(r"\s+")
_INVISIBLE = re.compile(
    r"[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f"
    r"​-‏‪-‮⁠-⁤⁦-⁯﻿]"
)
_EM_DASH = chr(0x2014)
_ABSENT = object()


class ContextDocsError(Exception):
    """A context document that cannot be merged. The message is in French."""


def clean_text(value) -> str:
    """Return the text on one line: spaces reduced, control and invisible characters removed.

    The em dash becomes a hyphen. A value that is not a text gives an empty text.
    """
    if not isinstance(value, str):
        return ""
    text = _INVISIBLE.sub("", value.replace(_EM_DASH, "-"))
    return _SPACES.sub(" ", text).strip()


def validate_items(document) -> list:
    """Return the errors, in French, of an items.json document (an empty list when valid).

    Every problem is reported in one pass so that the importer fixes its parts once. A value in
    an error is cleaned and cut, since it comes from the document.
    """
    if not isinstance(document, dict):
        return ['le document doit être un objet JSON {"document": ..., "items": [...]}']
    errors = _unknown_keys(document, ("document", "items", "coverage"), "")
    kind = None
    if "document" not in document:
        errors.append("champ « document » manquant")
    elif not isinstance(document["document"], dict):
        errors.append("« document » doit être un objet JSON")
    else:
        errors.extend(_document_errors(document["document"]))
        kind = document["document"].get("kind")
    ko_ids: set = set()
    all_ids: set = set()
    if "items" not in document:
        errors.append("champ « items » manquant")
    elif not isinstance(document["items"], list):
        errors.append("« items » doit être une liste")
    else:
        errors.extend(_items_errors(document["items"], all_ids, ko_ids))
    present = "coverage" in document
    if kind == "grille":
        if present:
            errors.extend(_coverage_errors(document["coverage"], all_ids, ko_ids))
        else:
            errors.append("champ « coverage » manquant (obligatoire pour une grille)")
    elif kind == "constats" and present:
        errors.append("« coverage » interdite pour un document de constats")
    return errors


def merge(run_dir, doc_id: str) -> dict:
    """Merge the layout and the parts of a document into context-docs/<id>/items.json.

    Reads context-docs/<id>/layout.json and parts/<NN>.json (NN is the 1-based rank of the sheet
    in the layout on two digits, 00 for a document without sheets). A sheet whose role is not
    "skip" needs its part. Texts are cleaned, ids are assigned (<id>-<NNN>, by sheet then by
    point), unknown keys are dropped, and for a "grille" the coverage is counted by the code on
    the rows of the source. The caps are not applied here: validate_items refuses a field over
    its cap. Returns {"id", "kind", "points_ko", "points_info", "criteria"}.
    """
    folder = _folder(run_dir, doc_id)
    layout = _read_object(folder / LAYOUT_FILE, LAYOUT_FILE)
    document = _layout_document(layout, doc_id)
    sheets = _layout_sheets(layout, document["kind"])
    items = []
    for rank, sheet in _targets(sheets):
        for raw in _read_part(folder, rank, sheet):
            items.append(_point(raw, sheet, f"{doc_id}-{len(items) + 1:03d}"))
    result = {"document": document, "items": items}
    if document["kind"] == "grille":
        result["coverage"] = _coverage(folder, layout, sheets, items)
    write_json(folder / ITEMS_FILE, result)
    return {
        "id": doc_id,
        "kind": document["kind"],
        "points_ko": sum(1 for item in items if item["verdict"] == "ko"),
        "points_info": sum(1 for item in items if item["verdict"] == "info"),
        "criteria": len(result.get("coverage", {})),
    }


def scan_document(document, known_values: list) -> list:
    """Return the labels of the secrets found in a document (never a value), each once.

    Both the JSON of the document and its texts, one per line, are scanned: a value with a quote
    or a backslash is escaped in the JSON but not in the text.
    """
    texts = "\n".join(_strings(document))
    dumped = json.dumps(document, ensure_ascii=False, default=str)
    labels: list = []
    for text in (dumped, texts):
        labels.extend(label for label in scan_text(text, known_values) if label not in labels)
    return labels


def read_index(run_dir) -> dict:
    """Return context-docs/index.json, or {"docs": []} when it is absent or unreadable."""
    index = _load(Path(run_dir) / FOLDER / INDEX_FILE)
    docs = index.get("docs") if isinstance(index, dict) else None
    if not isinstance(docs, list):
        return {"docs": []}
    return {**index, "docs": [entry for entry in docs if isinstance(entry, dict)]}


def read_items(run_dir, doc_id: str) -> Optional[dict]:
    """Return the items.json of a document, or None when it is absent or not a document.

    The content is not validated: call validate_items before relying on it.
    """
    if not isinstance(doc_id, str) or not _DOC_ID.fullmatch(doc_id):
        return None
    document = _load(Path(run_dir) / FOLDER / doc_id / ITEMS_FILE)
    if (
        isinstance(document, dict)
        and isinstance(document.get("document"), dict)
        and isinstance(document.get("items"), list)
    ):
        return document
    return None


def _show(value) -> str:
    text = value if isinstance(value, str) else str(value)
    text = clean_text(text)
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "…"


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _unknown_keys(value: dict, allowed: tuple, prefix: str) -> list:
    return [f"{prefix}clé inconnue : {_show(key)}" for key in value if key not in allowed]


def _text_errors(value, name: str, label: str, limit: int, required: bool) -> list:
    if not isinstance(value, str) or (required and not value.strip()):
        return [f"{label} : « {name} » doit être un texte" + (" non vide" if required else "")]
    problems = []
    if len(value) > limit:
        problems.append(f"{label} : « {name} » dépasse {limit} caractères ({len(value)})")
    if clean_text(value) != value:
        problems.append(f"{label} : « {name} » {_UNCLEAN}")
    return problems


def _list_errors(value, name: str, label: str, limit: int, maximum: int) -> list:
    if not isinstance(value, list) or not all(isinstance(text, str) for text in value):
        return [f"{label} : « {name} » doit être une liste de textes"]
    problems = []
    if len(value) > maximum:
        problems.append(f"{label} : « {name} » dépasse {maximum} entrées ({len(value)})")
    longest = max((len(text) for text in value), default=0)
    if longest > limit:
        problems.append(f"{label} : « {name} » : un texte dépasse {limit} caractères ({longest})")
    if any(not text for text in value):
        problems.append(f"{label} : « {name} » : un texte est vide")
    if any(text and clean_text(text) != text for text in value):
        problems.append(f"{label} : « {name} » : un texte {_UNCLEAN}")
    return problems


def _enum_errors(value, name: str, label: str, allowed: tuple) -> list:
    if value in allowed:
        return []
    return [f"{label} : « {name} » inconnu : {_show(value)} (attendu : {', '.join(allowed)})"]


def _fields(value: dict, keys: tuple, label: str, check) -> list:
    """Run check(key) on each key present in value, report the others as missing."""
    errors = []
    for key in keys:
        if key in value:
            errors.extend(check(key, value[key]))
        else:
            errors.append(f"{label} : « {key} » manquant")
    return errors


def _document_errors(document: dict) -> list:
    label = "document"
    errors = _unknown_keys(document, _DOCUMENT_KEYS, f"{label} : ")

    def check(key, value):
        if key == "id":
            if isinstance(value, str) and _DOC_ID.fullmatch(value):
                return []
            return [f"{label} : « id » invalide : {_show(value)}"]
        if key == "label":
            return _text_errors(value, key, label, LIMITS["label"], True)
        if key == "framework":
            return _text_errors(value, key, label, LIMITS["framework"], False)
        if key == "kind":
            return _enum_errors(value, key, label, KINDS)
        if key == "categories":
            return _list_errors(value, key, label, LIMITS["category"], MAX_CATEGORIES)
        return _date_errors(value, label)

    return errors + _fields(document, _DOCUMENT_KEYS, label, check)


def _date_errors(value, label: str) -> list:
    problem = [f"{label} : « date » doit être au format AAAA-MM-JJ : {_show(value)}"]
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return problem
    try:
        date.fromisoformat(value)
    except ValueError:
        return problem
    return []


def _items_errors(items: list, all_ids: set, ko_ids: set) -> list:
    errors = []
    if len(items) > MAX_POINTS:
        errors.append(f"trop de points : {len(items)} ({MAX_POINTS} au plus)")
    counts: Counter = Counter()
    for index, item in enumerate(items[:MAX_POINTS], 1):
        if not isinstance(item, dict):
            errors.append(f"point n° {index} : doit être un objet JSON")
            continue
        identifier = item.get("id")
        valid = isinstance(identifier, str) and _POINT_ID.fullmatch(identifier)
        label = f"point {identifier}" if valid else f"point n° {index}"
        if valid:
            counts[identifier] += 1
            all_ids.add(identifier)
            if item.get("verdict") == "ko":
                ko_ids.add(identifier)
        errors.extend(_point_errors(item, label))
    errors.extend(
        f"identifiant en double : {identifier} ({count} points)"
        for identifier, count in counts.items()
        if count > 1
    )
    return errors


def _point_errors(item: dict, label: str) -> list:
    def check(key, value):
        if key == "id":
            if value == "":
                return [f"{label} : « id » manquant"]
            if isinstance(value, str) and _POINT_ID.fullmatch(value):
                return []
            return [f"{label} : « id » invalide : {_show(value)}"]
        if key in ("source", "summary"):
            return _text_errors(value, key, label, LIMITS[key], True)
        if key in ("priority", "fix_hint"):
            return _text_errors(value, key, label, LIMITS[key], False)
        if key == "verdict":
            return _enum_errors(value, key, label, POINT_VERDICTS)
        if key == "owner":
            return _enum_errors(value, key, label, OWNERS)
        if key == "criteria":
            return _list_errors(value, key, label, LIMITS["criterion"], MAX_CRITERIA)
        if key == "scopes":
            return _list_errors(value, key, label, LIMITS["scope"], MAX_SCOPES)
        return _list_errors(value, key, label, LIMITS["element"], MAX_ELEMENTS)

    return _unknown_keys(item, _POINT_KEYS, f"{label} : ") + _fields(
        item, _POINT_KEYS, label, check
    )


def _coverage_errors(coverage, all_ids: set, ko_ids: set) -> list:
    if not isinstance(coverage, dict):
        return ["« coverage » doit être un objet JSON"]
    errors = []
    if len(coverage) > MAX_COVERAGE:
        errors.append(f"trop de critères dans « coverage » : {len(coverage)} ({MAX_COVERAGE} au plus)")
    for criterion, entry in list(coverage.items())[:MAX_COVERAGE]:
        if not criterion:
            errors.append("coverage : un critère est vide")
        elif len(criterion) > LIMITS["criterion"]:
            errors.append(
                f"coverage : un critère dépasse {LIMITS['criterion']} caractères ({len(criterion)})"
            )
        label = f"coverage « {_show(criterion)} »"
        if not isinstance(entry, dict):
            errors.append(f"{label} : doit être un objet JSON")
            continue
        errors.extend(_unknown_keys(entry, _COVERAGE_KEYS, f"{label} : "))
        errors.extend(_entry_errors(entry, label, all_ids, ko_ids))
    return errors


def _entry_errors(entry: dict, label: str, all_ids: set, ko_ids: set) -> list:
    errors = []
    for name in _COUNTS:
        if name not in entry:
            errors.append(f"{label} : « {name} » manquant")
        elif not _is_count(entry[name]):
            errors.append(f"{label} : « {name} » doit être un entier positif ou nul")
    if "points" not in entry:
        errors.append(f"{label} : « points » manquant")
    elif not isinstance(entry["points"], list) or not all(
        isinstance(point, str) for point in entry["points"]
    ):
        errors.append(f"{label} : « points » doit être une liste d'identifiants")
    else:
        for point in entry["points"]:
            if point not in all_ids:
                errors.append(f"{label} : le point {_show(point)} n'existe pas")
            elif point not in ko_ids:
                errors.append(f"{label} : le point {_show(point)} n'est pas un point ko")
    return errors


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _load(path: Path):
    """Return the JSON of a file, or None when it is absent, unreadable or not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _read_object(path: Path, name: str) -> dict:
    if not path.is_file():
        raise ContextDocsError(f"Fichier manquant : {name}")
    value = _load(path)
    if not isinstance(value, dict):
        raise ContextDocsError(f"Fichier illisible ou qui n'est pas un objet JSON : {name}")
    return value


def _folder(run_dir, doc_id) -> Path:
    if not isinstance(doc_id, str) or not _DOC_ID.fullmatch(doc_id):
        raise ContextDocsError(f"Identifiant de document invalide : {_show(doc_id)}")
    folder = Path(run_dir) / FOLDER / doc_id
    if not folder.is_dir():
        raise ContextDocsError(f"Document inconnu : {doc_id}")
    return folder


def _text_of(value) -> str:
    """Return the clean text of a text or a number, and an empty text for anything else."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    return clean_text(value)


def _listed(value) -> list:
    """Return the clean texts of a list (or of a lone text), without empty ones nor repeats."""
    if isinstance(value, str):
        value = [value]
    texts: list = []
    for entry in value if isinstance(value, list) else []:
        text = _text_of(entry)
        if text and text not in texts:
            texts.append(text)
    return texts


def _layout_document(layout: dict, doc_id: str) -> dict:
    raw = layout.get("document")
    if not isinstance(raw, dict):
        raise ContextDocsError("Layout invalide : « document » doit être un objet JSON")
    kind = _text_of(raw.get("kind"))
    if kind not in KINDS:
        raise ContextDocsError(
            f"Layout invalide : « kind » inconnu : {_show(kind)} (attendu : {', '.join(KINDS)})"
        )
    return {
        "id": doc_id,
        "label": _text_of(raw.get("label")),
        "date": _text_of(raw.get("date")),
        "kind": kind,
        "categories": _listed(raw.get("categories")),
        "framework": _text_of(raw.get("framework")),
    }


def _layout_sheets(layout: dict, kind: str) -> list:
    raw = layout.get("sheets", [])
    if not isinstance(raw, list):
        raise ContextDocsError("Layout invalide : « sheets » doit être une liste")
    sheets = []
    for index, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ContextDocsError(f"Layout invalide : la feuille n° {index} doit être un objet JSON")
        role = _text_of(entry.get("role"))
        if role not in ROLES:
            raise ContextDocsError(
                f"Layout invalide : feuille n° {index} : « role » inconnu : {_show(role)} "
                f"(attendu : {', '.join(ROLES)})"
            )
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            raise ContextDocsError(f"Layout invalide : feuille n° {index} : « name » doit être un texte")
        shown = clean_text(name)
        sheet = {
            "role": role,
            "name": name or None,
            "shown": shown,
            "scope": clean_text(entry.get("scope")) or _DEFAULT_SCOPES.get(role, shown),
        }
        if kind == "grille" and role in ("page", "transverse"):
            sheet["header_row"] = _header_row(entry.get("header_row", 0), index)
            sheet["criterion_col"] = _column(entry.get("criterion_col"), "criterion_col", index)
            sheet["status_col"] = _column(entry.get("status_col"), "status_col", index)
        sheets.append(sheet)
    return sheets


def _header_row(value, index: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContextDocsError(
            f"Layout invalide : feuille n° {index} : « header_row » doit être un entier positif ou nul"
        )
    return value


def _column(value, name: str, index: int) -> int:
    try:
        return docs_read.column_number(value)
    except docs_read.DocumentError:
        raise ContextDocsError(
            f"Layout invalide : feuille n° {index} : « {name} » doit être des lettres de colonne "
            "(A, B...)"
        ) from None


def _targets(sheets: list) -> list:
    """Return [(rank, sheet)] of the sheets that need a part; (0, None) without sheets."""
    if not sheets:
        return [(0, None)]
    return [(rank, sheet) for rank, sheet in enumerate(sheets, 1) if sheet["role"] != "skip"]


def _read_part(folder: Path, rank: int, sheet: Optional[dict]) -> list:
    relative = f"parts/{rank:02d}.json"
    shown = sheet["shown"] if sheet else ""
    path = folder / relative
    if not path.is_file():
        raise ContextDocsError(
            f"Partie manquante pour la feuille « {_show(shown)} » : {relative}"
            if sheet
            else f"Partie manquante : {relative}"
        )
    part = _read_object(path, relative)
    if sheet and "sheet" in part and clean_text(part["sheet"]) != shown:
        raise ContextDocsError(
            f"La partie {relative} décrit une autre feuille que celle du layout : "
            f"« {_show(part['sheet'])} » au lieu de « {_show(shown)} »"
        )
    points = part.get("points")
    if not isinstance(points, list):
        raise ContextDocsError(f"« points » doit être une liste dans {relative}")
    if any(not isinstance(point, dict) for point in points):
        raise ContextDocsError(f"Chaque point doit être un objet JSON dans {relative}")
    return points


def _point(raw: dict, sheet: Optional[dict], identifier: str) -> dict:
    shown = sheet["shown"] if sheet else ""
    return {
        "id": identifier,
        "source": _text_of(raw.get("source")) or shown,
        "criteria": _listed(raw.get("criteria")),
        "scopes": _listed(raw.get("scopes")) or ([sheet["scope"]] if sheet and sheet["scope"] else []),
        "verdict": _text_of(raw.get("verdict")).lower(),
        "priority": _text_of(raw.get("priority")),
        "summary": _text_of(raw.get("summary")),
        "fix_hint": _text_of(raw.get("fix_hint")),
        "owner": _text_of(raw.get("owner")).lower() or "inconnu",
        "elements": _listed(raw.get("elements")),
    }


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.lower().split())


def _status_table(values) -> dict:
    """Return {folded text: status}; a text listed under several statuses counts as ko first.

    The statuses are ko, ok, na, undetermined and neutral (optional): neutral is a text that the
    document explains as "not invalidated, decided elsewhere"; it is counted in none of the
    counters of the coverage. A text listed under neutral and another status keeps the other.
    """
    if not isinstance(values, dict):
        raise ContextDocsError("Layout invalide : « status_values » doit être un objet JSON")
    table = {}
    for status in ("neutral", "undetermined", "na", "ok", "ko"):
        texts = values.get(status, [])
        if not isinstance(texts, list):
            raise ContextDocsError(
                f"Layout invalide : « status_values » : « {status} » doit être une liste de textes"
            )
        for text in texts:
            if isinstance(text, str) and _fold(text):
                table[_fold(text)] = status
    return table


def _source(folder: Path) -> Path:
    sources = sorted(path for path in folder.glob("source.*") if path.is_file())
    if not sources:
        raise ContextDocsError("Source du document introuvable (source.<extension>)")
    return sources[0]


def _coverage(folder: Path, layout: dict, sheets: list, items: list) -> dict:
    """Count the statuses of the criteria on the rows of the page and transverse sheets."""
    coverage: dict = {}
    graded = [sheet for sheet in sheets if sheet["role"] in ("page", "transverse")]
    if graded:
        table = _status_table(layout.get("status_values"))
        source = _source(folder)
        try:
            for sheet in graded:
                _count_sheet(source, sheet, table, coverage)
        except docs_read.DocumentError as error:
            raise ContextDocsError(str(error)) from None
    for item in items:
        if item["verdict"] != "ko":
            continue
        for criterion in item["criteria"]:
            entry = coverage.get(criterion)
            if entry is not None and item["id"] not in entry["points"]:
                entry["points"].append(item["id"])
    return coverage


def _count_sheet(source: Path, sheet: dict, table: dict, coverage: dict) -> None:
    criterion_at = sheet["criterion_col"] - 1
    status_at = sheet["status_col"] - 1
    for number, cells in docs_read.iter_rows(source, sheet["name"]):
        if number <= sheet["header_row"] or len(cells) <= criterion_at:
            continue
        criterion = clean_text(cells[criterion_at])
        if len(criterion) > LIMITS["criterion"] or not _CRITERION.fullmatch(criterion):
            continue
        text = cells[status_at] if len(cells) > status_at else ""
        status = table.get(_fold(text), "undetermined")
        if status == "neutral":
            continue
        entry = coverage.setdefault(
            criterion, {"ok": 0, "na": 0, "ko": 0, "undetermined": 0, "points": []}
        )
        entry[status] += 1
