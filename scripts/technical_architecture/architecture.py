#!/usr/bin/env python3
"""Tooling for the technical architecture documentation of a project.

The documentation lives in one folder (docs/architecture/ by default) holding
README.md, views/*.drawio.svg and tools/ (this script, dossier.css and
architecture.json). Each view is an SVG image that embeds its draw.io diagram.
This script extracts that diagram, re-renders the images with draw.io headless
(Docker), checks that views and flow matrix stay consistent and builds an
on-demand PDF dossier in build/ (never versioned).

Usage: python3 architecture.py [--racine <folder>] extract <view.drawio.svg> <out.drawio>
       python3 architecture.py [--racine <folder>] render <source.drawio> <view.drawio.svg> [--png <preview.png>]
       python3 architecture.py [--racine <folder>] refresh
       python3 architecture.py [--racine <folder>] check
       python3 architecture.py [--racine <folder>] pdf [--sans-points-attention] [--sortie <file.pdf>]
       python3 architecture.py version

--racine defaults to the folder above tools/, where this script is installed.
"""

import base64
import binascii
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Pattern, Tuple

TOOL_VERSION = "1.0.0"
CONFIG_NAME = "architecture.json"
ROLES = ("hebergeur", "agence", "client", "tiers")
PDF_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.pdf$")
PREFIX_RE = re.compile(r"^[A-Z]$")

CARTOUCHE_RE = re.compile(r"État au \d{2}/\d{2}/\d{4}")
STATUS_RE = re.compile(
    r"^(constaté \(\d{2}/\d{2}/\d{4}\)|décidé|à confirmer|indiqué par .+ \(\d{2}/\d{2}/\d{4}\))$"
)
SECRET_RE = re.compile(
    r"(?i)(mot de passe|password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
)
EM_DASH = "\u2014"
MATRIX_HEADING = "## Matrice des flux"
# An <image> whose href is not a data: URI breaks once the SVG leaves draw.io.
EXTERNAL_IMAGE_RE = re.compile(r'<image\b[^>]*?\bhref="(?!data:)([^"]*)"')
# Looks like a flow ID, whatever the configured prefixes.
ID_LIKE_RE = re.compile(r"^[A-Z]\d+$")
UNREADABLE_VIEW = (
    "aucun diagramme embarqué ou diagramme illisible (enregistrer en .drawio.svg avec une copie du diagramme)"
)
VIEW_ERRORS = (ValueError, ET.ParseError, zlib.error, binascii.Error, UnicodeDecodeError)


class ConfigError(Exception):
    """Invalid or missing project configuration; the message is shown to the user."""


@dataclass(frozen=True)
class Config:
    """Project settings read from tools/architecture.json."""

    root: Path
    title: str
    pdf_name: str
    flow_prefixes: Tuple[Tuple[str, str], ...]
    owners: Dict[str, str]
    known_issues: bool
    repositories: Tuple[str, ...]
    tool_version: str

    @property
    def views_dir(self) -> Path:
        return self.root / "views"

    @property
    def readme(self) -> Path:
        return self.root / "README.md"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def css(self) -> Path:
        return self.root / "tools" / "dossier.css"

    @property
    def flow_id_re(self) -> Pattern[str]:
        letters = "".join(letter for letter, _ in self.flow_prefixes)
        return re.compile(r"\b[" + letters + r"]\d{2}\b")

    def pdf_path(self, with_known_issues: bool) -> Path:
        """Where the dossier is written: always under build/."""
        if with_known_issues:
            return self.build_dir / self.pdf_name
        return self.build_dir / f"{Path(self.pdf_name).stem}-sans-points-attention.pdf"


def _required_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{CONFIG_NAME} : « {key} » manquant")
    return value.strip()


def load_config(root: Path) -> Config:
    """Read and validate root/tools/architecture.json."""
    path = root / "tools" / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{CONFIG_NAME} introuvable dans {root / 'tools'}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ConfigError(f"{CONFIG_NAME} : JSON invalide ({error})") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_NAME} : JSON invalide (objet attendu)")

    title = _required_text(data, "title")
    tool_version = _required_text(data, "tool_version")
    pdf_name = data.get("pdf")
    if not isinstance(pdf_name, str) or not PDF_NAME_RE.match(pdf_name):
        raise ConfigError(f"{CONFIG_NAME} : « pdf » doit être un nom de fichier en .pdf")

    prefixes = data.get("flow_prefixes")
    if (
        not isinstance(prefixes, dict)
        or not prefixes
        or not all(isinstance(key, str) and PREFIX_RE.match(key) for key in prefixes)
        or not all(isinstance(name, str) and name.strip() for name in prefixes.values())
    ):
        raise ConfigError(f"{CONFIG_NAME} : « flow_prefixes » attend des lettres majuscules uniques")

    owners = data.get("owners") or {}
    if not isinstance(owners, dict):
        raise ConfigError(f"{CONFIG_NAME} : « owners » attend un objet rôle → nom")
    for role, name in owners.items():
        if role not in ROLES:
            raise ConfigError(f"{CONFIG_NAME} : rôle inconnu « {role} » dans « owners »")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{CONFIG_NAME} : nom manquant pour le rôle « {role} » dans « owners »")

    known_issues = data.get("known_issues", True)
    if not isinstance(known_issues, bool):
        raise ConfigError(f"{CONFIG_NAME} : « known_issues » attend true ou false")
    repositories = data.get("repositories") or []
    if not isinstance(repositories, list) or not all(isinstance(item, str) for item in repositories):
        raise ConfigError(f"{CONFIG_NAME} : « repositories » attend une liste de chemins")

    return Config(
        root=root,
        title=title,
        pdf_name=pdf_name,
        flow_prefixes=tuple((letter, name.strip()) for letter, name in prefixes.items()),
        owners={role: name.strip() for role, name in owners.items()},
        known_issues=known_issues,
        repositories=tuple(repositories),
        tool_version=tool_version,
    )


def default_root() -> Path:
    """The documentation folder: the tool is installed in <root>/tools/."""
    return Path(__file__).resolve().parents[1]


def _decode_diagram(payload: str) -> ET.Element:
    """Decode a compressed draw.io diagram (base64, raw deflate, URL encoding)."""
    raw = zlib.decompress(base64.b64decode(payload), -15)
    return ET.fromstring(urllib.parse.unquote(raw.decode("utf-8")))


def extract_diagram(view: Path) -> str:
    """Return the uncompressed <mxfile> XML embedded in a .drawio.svg file.

    Raises ValueError when the SVG carries no embedded diagram.
    """
    content = ET.parse(view).getroot().get("content")
    if not content:
        raise ValueError("aucun diagramme embarqué")
    mxfile = ET.fromstring(content)
    for diagram in mxfile.iter("diagram"):
        if diagram.find("mxGraphModel") is None and (diagram.text or "").strip():
            graph = _decode_diagram(diagram.text.strip())
            diagram.text = None
            diagram.append(graph)
    return ET.tostring(mxfile, encoding="unicode")


def view_text(mxfile_xml: str) -> str:
    """Return the visible text of every cell, HTML stripped, one label per line."""
    labels = []
    for element in ET.fromstring(mxfile_xml).iter():
        for attribute in ("value", "label"):
            value = element.get(attribute)
            if value:
                text = html.unescape(re.sub(r"<[^>]+>", " ", value)).replace("\xa0", " ")
                labels.append(re.sub(r"\s+", " ", text).strip())
    return "\n".join(label for label in labels if label)


def _cells(line: str) -> List[str]:
    """Split a Markdown table line into stripped cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _matrix_table_rows(readme_text: str) -> List[Tuple[List[str], List[str]]]:
    """(header, cells) for every body row of the tables under the flow matrix heading."""
    rows: List[Tuple[List[str], List[str]]] = []
    in_matrix = False
    header: List[str] = []
    lines = readme_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## "):
            in_matrix = line.strip() == MATRIX_HEADING
            header = []
            continue
        if not in_matrix or not line.lstrip().startswith("|"):
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if re.match(r"^\s*\|\s*:?-{3,}", next_line):
            header = _cells(line)
            continue
        cells = _cells(line)
        if header and cells and not re.match(r"^:?-{3,}", cells[0]):
            rows.append((header, cells))
    return rows


def _id_rows(readme_text: str) -> List[Tuple[str, Dict[str, str]]]:
    """(first cell, row) for every matrix row whose first cell looks like a flow ID."""
    return [
        (cells[0], dict(zip(header, cells)))
        for header, cells in _matrix_table_rows(readme_text)
        if ID_LIKE_RE.match(cells[0])
    ]


def matrix_rows(readme_text: str, flow_id_re: Pattern[str]) -> List[Dict[str, str]]:
    """Return the flow rows of the tables under the flow matrix heading."""
    return [row for first, row in _id_rows(readme_text) if flow_id_re.fullmatch(first)]


def _text_errors(name: str, text: str) -> List[str]:
    """Errors shared by views and README: secrets and em dashes."""
    errors = []
    if SECRET_RE.search(text):
        errors.append(f"{name} : secret possible (mot de passe, jeton ou clé), à retirer")
    if EM_DASH in text:
        errors.append(f"{name} : tiret long interdit, le remplacer")
    return errors


def list_views(config: Config) -> List[Path]:
    """The views of the project, in file name order."""
    return sorted(config.views_dir.glob("*.drawio.svg")) if config.views_dir.is_dir() else []


def check(config: Config) -> List[str]:
    """Check views and flow matrix; return French error messages (empty when valid)."""
    errors: List[str] = []
    if config.tool_version != TOOL_VERSION:
        errors.append(
            f"{CONFIG_NAME} : tool_version {config.tool_version} ne correspond pas à l'outil {TOOL_VERSION}"
        )
    flow_id_re = config.flow_id_re
    view_ids: Dict[str, str] = {}
    views = list_views(config)
    if not views:
        errors.append("views : aucune vue (*.drawio.svg)")
    for view in views:
        try:
            text = view_text(extract_diagram(view))
            raw = view.read_text(encoding="utf-8")
        except VIEW_ERRORS:
            errors.append(f"{view.name} : {UNREADABLE_VIEW}")
            continue
        if not CARTOUCHE_RE.search(text):
            errors.append(f"{view.name} : cartouche sans « État au JJ/MM/AAAA »")
        if "Légende" not in text:
            errors.append(f"{view.name} : « Légende » absente")
        for href in sorted(set(EXTERNAL_IMAGE_RE.findall(raw))):
            errors.append(f"{view.name} : image non embarquée ({href}), réenregistrer en incorporant les images")
        errors += _text_errors(view.name, text)
        for flow_id in flow_id_re.findall(text):
            view_ids.setdefault(flow_id, view.name)

    if not config.readme.is_file():
        return errors + [f"{config.readme.name} : introuvable"]
    readme_text = config.readme.read_text(encoding="utf-8")
    errors += _text_errors(config.readme.name, readme_text)
    letters = ", ".join(letter for letter, _ in config.flow_prefixes)
    matrix_ids = set()
    duplicates = set()
    for first, row in _id_rows(readme_text):
        if not flow_id_re.fullmatch(first):
            errors.append(f"{first} : identifiant invalide (une lettre parmi {letters} puis deux chiffres)")
            continue
        if first in matrix_ids:
            if first not in duplicates:
                errors.append(f"{first} : identifiant en double dans la matrice des flux")
                duplicates.add(first)
            continue
        matrix_ids.add(first)
        status = row.get("Statut", "")
        if not STATUS_RE.match(status):
            errors.append(
                f"{first} : statut « {status} » invalide "
                "(constaté (JJ/MM/AAAA), décidé, à confirmer, indiqué par … (JJ/MM/AAAA))"
            )

    for flow_id in sorted(set(view_ids) - matrix_ids):
        errors.append(f"{flow_id} : sur la vue {view_ids[flow_id]} mais absent de la matrice des flux")
    for flow_id in sorted(matrix_ids - set(view_ids)):
        errors.append(f"{flow_id} : dans la matrice des flux mais sur aucune vue")
    return errors
