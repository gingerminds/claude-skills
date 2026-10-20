"""Checklist data: schema validation, row normalization and matching with the reference."""
import re
from typing import Optional

STATUS_TODO = "À traiter"
STATUS_STANDBY = "En standby"
STATUS_PARTIAL = "Mise en place partielle"
STATUS_IMPLEMENTED = "Mis en place"
STATUS_OK = "Ok / Recetté"
STATUS_NA = "Non applicable"

REQUIRED_PROPERTIES = {
    "Tâche": "title",
    "Réf": "unique_id",
    "Réf modèle": "rich_text",
    "Statut": "status",
    "Assignée à": "multi_select",
    "Catégorie": "select",
    "Sous-catégorie": "select",
    "Mise en ligne": "select",
    "Section": "multi_select",
    "Technos concernées": "multi_select",
    "Phase": "multi_select",
    "Outils requis": "multi_select",
    "Automatisation skill": "select",
    "Outillage skill": "rich_text",
}

_STATUSES = (
    STATUS_TODO,
    STATUS_STANDBY,
    STATUS_PARTIAL,
    STATUS_IMPLEMENTED,
    STATUS_OK,
    STATUS_NA,
)
_TECHNOS = ("Laravel", "Drupal", "Nuxt", "Wordpress")
_TOOLS = (
    "Code",
    "CLI local",
    "HTTP",
    "Navigateur",
    "Audit web",
    "Crawler",
    "SSH serveur",
    "BDD",
    "Comptes de test",
    "Service externe",
    "Intervention humaine",
)
_REQUIRED_OPTIONS = (
    ("Statut", "statut manquant", _STATUSES),
    ("Section", "option de Section manquante", ("Front", "Back")),
    ("Technos concernées", "techno manquante", _TECHNOS),
    ("Outils requis", "outil manquant", _TOOLS),
)

_BLOCKING = "Bloquant"
_REF_PREFIX = "CQ"
_REF = re.compile(rf"{_REF_PREFIX}-([0-9]+)", re.IGNORECASE)


def validate_schema(data_source: dict) -> list[str]:
    """Return, in French, what keeps a data source from being audited. Empty when it is valid.

    Properties come first (missing or of the wrong type), then the options the skill relies on.
    Extra properties and options are accepted.
    """
    properties = data_source.get("properties") or {}
    problems = []
    for name, expected in REQUIRED_PROPERTIES.items():
        prop = properties.get(name)
        if not prop:
            problems.append(f"propriété manquante : {name}")
        elif prop.get("type") != expected:
            found = prop.get("type")
            problems.append(
                f"type de propriété incorrect : {name} (attendu : {expected}, trouvé : {found})"
            )
    for name, message, expected in _REQUIRED_OPTIONS:
        prop = properties.get(name)
        if not prop or prop.get("type") != REQUIRED_PROPERTIES[name]:
            continue
        options = (_value(prop) or {}).get("options") or []
        present = {option.get("name") for option in options}
        problems.extend(f"{message} : {option}" for option in expected if option not in present)
    return problems


def normalize_row(page: dict, bot_id: str) -> dict:
    """Return the row of a Notion page, without reference_page_id and fiche_path.

    ref is read from "Réf modèle" (None when empty or malformed), never from the auto "Réf",
    which a duplication renumbers. last_edited_by_bot is False when the editor is unknown, so
    that such a row counts as set by a human.
    """
    props = page.get("properties") or {}
    editor = page.get("last_edited_by") or {}
    return {
        "page_id": page["id"],
        "ref": _parse_ref(_text(props.get("Réf modèle"))),
        "title": _text(props.get("Tâche")),
        "status": _name(props.get("Statut")),
        "assignees": _names(props.get("Assignée à")),
        "category": _name(props.get("Catégorie")),
        "subcategory": _name(props.get("Sous-catégorie")),
        "blocking": _name(props.get("Mise en ligne")) == _BLOCKING,
        "sections": _names(props.get("Section")),
        "technos": _names(props.get("Technos concernées")),
        "phases": _names(props.get("Phase")),
        "tools": _names(props.get("Outils requis")),
        "automation": _name(props.get("Automatisation skill")),
        "tooling_hint": _text(props.get("Outillage skill")),
        "last_edited_by_bot": bool(bot_id) and editor.get("id") == bot_id,
    }


def match_reference(
    copy_rows: list[dict], ref_rows: list[dict]
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair each row of the copy with its row of the reference.

    Three passes, each pairing a row at most once: same ref and same title, then same title
    alone (only when one unpaired reference row has it), then same ref alone. Titles are
    compared after strip and casefold. A row without ref, or with an empty title, never pairs
    on that criterion.

    Return the (copy row, reference row) pairs in copy order, then the copy rows and the
    reference rows left unpaired, each list in the order of its source.
    """
    matched = {}
    taken = set()

    def pair_on(key, unambiguous=False):
        candidates = {}
        for index, ref_row in enumerate(ref_rows):
            value = key(ref_row)
            if index not in taken and value is not None:
                candidates.setdefault(value, []).append(index)
        for index, copy_row in enumerate(copy_rows):
            options = candidates.get(key(copy_row))
            if index in matched or not options or (unambiguous and len(options) > 1):
                continue
            matched[index] = options.pop(0)
            taken.add(matched[index])

    pair_on(_key_ref_and_title)
    pair_on(_key_title, unambiguous=True)
    pair_on(_key_ref)
    pairs = [(copy_rows[index], ref_rows[found]) for index, found in sorted(matched.items())]
    unpaired_copy = [row for index, row in enumerate(copy_rows) if index not in matched]
    unpaired_ref = [row for index, row in enumerate(ref_rows) if index not in taken]
    return pairs, unpaired_copy, unpaired_ref


def ref_label(ref: int) -> str:
    """Return the label of a ref as the team cites it (CQ-79)."""
    return f"{_REF_PREFIX}-{ref}"


def fiche_filename(ref: int) -> str:
    """Return the file name of a cached fiche (CQ-079.md)."""
    return f"{_REF_PREFIX}-{ref:03d}.md"


def _parse_ref(text: str) -> Optional[int]:
    found = _REF.fullmatch(text.strip())
    return int(found.group(1)) if found else None


def _value(prop: Optional[dict]):
    return prop.get(prop.get("type")) if prop else None


def _name(prop: Optional[dict]) -> Optional[str]:
    value = _value(prop)
    return value["name"] if value else None


def _names(prop: Optional[dict]) -> list[str]:
    return [option["name"] for option in _value(prop) or []]


def _text(prop: Optional[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in _value(prop) or [])


def _title_key(row: dict) -> str:
    return (row.get("title") or "").strip().casefold()


def _key_ref_and_title(row: dict) -> Optional[tuple]:
    ref = row.get("ref")
    return None if ref is None else (ref, _title_key(row))


def _key_title(row: dict) -> Optional[str]:
    return _title_key(row) or None


def _key_ref(row: dict) -> Optional[int]:
    return row.get("ref")
