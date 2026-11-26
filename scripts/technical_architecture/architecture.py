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

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Pattern, Tuple

TOOL_VERSION = "1.0.0"
CONFIG_NAME = "architecture.json"
ROLES = ("hebergeur", "agence", "client", "tiers")
PDF_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.pdf$")
PREFIX_RE = re.compile(r"^[A-Z]$")


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
