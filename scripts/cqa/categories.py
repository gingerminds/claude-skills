"""Category scope of a run: the categories of the copy, their names, and the scope test."""
import re
import unicodedata
from typing import Optional

from cqa import fix_names

_SEPARATORS = re.compile(r"[^a-z0-9]+")


def fold(text) -> str:
    """Return text without accent, lowercase, its separators reduced to one space.

    A name typed by the lead and a name of Notion compare through this form ("SEO / GEO" and
    "seo-geo" are the same).
    """
    decomposed = unicodedata.normalize("NFKD", text if isinstance(text, str) else "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SEPARATORS.sub(" ", plain.lower()).strip()


def available(rows: list) -> list:
    """Return the categories of the rows as [{"name", "alias", "rows"}], first appearance first.

    alias is the section alias of the fix mode (fix_names.section_alias), rows the number of
    rows of the category. A row without category is not listed.
    """
    counts: dict = {}
    for row in rows:
        name = row.get("category") if isinstance(row, dict) else None
        if isinstance(name, str) and name.strip():
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": name, "alias": _alias(name), "rows": count} for name, count in counts.items()
    ]


def resolve(requested: list, rows: list) -> tuple:
    """Return (Notion names in the order of the rows, unknown names in the order asked).

    A name matches the Notion name of a category or, when none does, its alias, whatever the
    accents, the case and the separators. An alias shared by several categories names all of
    them.
    """
    listed = available(rows)
    chosen, unknown = set(), []
    for text in requested:
        key = fold(text)
        found = [entry["name"] for entry in listed if key and fold(entry["name"]) == key]
        found = found or [entry["name"] for entry in listed if key and fold(entry["alias"]) == key]
        if not found:
            if text not in unknown:
                unknown.append(text)
            continue
        chosen.update(found)
    return [entry["name"] for entry in listed if entry["name"] in chosen], unknown


def unknown_message(unknown: list, rows: list) -> str:
    """Return the message that names the unknown categories and lists those of the copy."""
    listed = ", ".join(entry["name"] for entry in available(rows))
    return f"Catégorie inconnue : {', '.join(unknown)}. Catégories de la copie : {listed}."


def in_scope(row: dict, category_filter: Optional[list]) -> bool:
    """Return True when the row belongs to the scope: always without a filter (None or empty)."""
    if not category_filter:
        return True
    return row.get("category") in category_filter


def _alias(name: str) -> str:
    try:
        return fix_names.section_alias(name)
    except ValueError:
        return fold(name).replace(" ", "-")
