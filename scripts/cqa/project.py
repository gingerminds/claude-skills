"""Project facts: PROJECT.md parsing, project slug and sibling repos."""
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

from cqa.names import normalize

_SEPARATOR = " - "
_BULLET = re.compile(r"^\s*(?:[-*+]\s+)?(.*)$")
_BOLD_LABEL = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")
_PLAIN_LABEL = re.compile(r"^([^:]+?)\s*:\s*(.*)$")
_LEADING_COLON = re.compile(r"^:\s*")
_DEV = re.compile(r"^(.*?)\s*\(([^()]*)\)$")

_LABELS = {
    "lead technique": "lead",
    "devs principaux": "devs",
    "url production": "prod_url",
    "url preprod staging": "preprod_url",
    "acces proteges": "protection",
    "url du repo": "repo_url",
    "branche prod": "prod_branch",
    "branche preprod": "preprod_branch",
    "collection vaultwarden": "vaultwarden",
}
_ROLES = {
    "fullstack": "fullstack",
    "front": "front",
    "frontend": "front",
    "back": "back",
    "backend": "back",
}


def parse_project_md(text: str) -> dict:
    """Return the facts of a PROJECT.md; a missing, empty or "N/A" value is None.

    Lines look like "- **Label** : value", with or without the colon, or "- Label : value"
    for nested bullets. A label is matched without case, accents or punctuation, wherever it
    sits; the first occurrence wins. Devs are "Name (role)" entries split on commas, with the
    role full-stack, front or back (None when absent or unknown).
    """
    found = {}
    for line in text.splitlines():
        label, value = _split_line(line)
        key = _LABELS.get(_label_key(label))
        if key and key not in found:
            found[key] = _clean(value)
    return {
        "lead": found.get("lead"),
        "devs": _parse_devs(found.get("devs")),
        "prod_url": found.get("prod_url"),
        "preprod_url": found.get("preprod_url"),
        "protection": found.get("protection"),
        "repo_url": found.get("repo_url"),
        "branches": {"prod": found.get("prod_branch"), "preprod": found.get("preprod_branch")},
        "vaultwarden": found.get("vaultwarden"),
    }


def project_slug(dir_names: list[str]) -> str:
    """Return the slug of the project the directories belong to.

    "Acme - Shop - Laravel" and "Acme - Shop - Nuxt 3" give acme-shop. Names are split on
    " - " and lose their last part (the techno) when they have two or more; the leading parts
    shared by all names are kept, as ASCII lowercase with every other character replaced by "-".
    """
    names = [_project_parts(name) for name in dir_names]
    if not names:
        raise ValueError("no directory name to build a project slug from")
    common = []
    for parts in zip(*names):
        if any(part != parts[0] for part in parts):
            break
        common.append(parts[0])
    slug = _slugify(" ".join(common))
    if not slug:
        raise ValueError("directory names share no leading part: " + ", ".join(dir_names))
    return slug


def sibling_repos(repo: Path) -> list[Path]:
    """Return the git repos beside repo with the same "Client - Projet" prefix, sorted by name."""
    repo = Path(os.path.abspath(repo))
    prefix = _SEPARATOR.join(_project_parts(repo.name)) + _SEPARATOR
    return sorted(
        (
            path
            for path in repo.parent.iterdir()
            if path.name != repo.name
            and path.name.startswith(prefix)
            and path.is_dir()
            and (path / ".git").exists()
        ),
        key=lambda path: path.name,
    )


def _split_line(line: str) -> tuple[str, str]:
    content = _BULLET.match(line).group(1).strip()
    bold = _BOLD_LABEL.match(content)
    if bold:
        return bold.group(1), _LEADING_COLON.sub("", bold.group(2))
    plain = _PLAIN_LABEL.match(content)
    return (plain.group(1), plain.group(2)) if plain else ("", "")


def _label_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize(label)).strip()


def _clean(value: str) -> Optional[str]:
    cleaned = value.replace("`", "").strip()
    return None if not cleaned or cleaned.casefold() == "n/a" else cleaned


def _parse_devs(value: Optional[str]) -> list[dict]:
    devs = []
    for entry in _split_top_level(value or ""):
        match = _DEV.match(entry)
        name, role = (match.group(1), _role(match.group(2))) if match else (entry, None)
        if name:
            devs.append({"name": name, "role": role})
    return devs


def _split_top_level(value: str) -> list[str]:
    entries, current, depth = [], [], 0
    for char in value:
        if char in ",;" and depth == 0:
            entries.append("".join(current))
            current = []
            continue
        depth = max(0, depth + (char == "(") - (char == ")"))
        current.append(char)
    entries.append("".join(current))
    return [entry.strip() for entry in entries if entry.strip()]


def _role(text: str) -> Optional[str]:
    return _ROLES.get(re.sub(r"[^a-z]", "", normalize(text)))


def _project_parts(dir_name: str) -> list[str]:
    parts = [part.strip() for part in dir_name.split(_SEPARATOR)]
    return parts[:-1] if len(parts) >= 2 else parts


def _slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
