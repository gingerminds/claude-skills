"""Shared fixtures for the technical architecture tool tests."""

import copy
import json
from pathlib import Path
from typing import Optional

CONFIG = {
    "tool_version": "1.0.0",
    "title": "Architecture de la plateforme Exemple",
    "pdf": "architecture-exemple.pdf",
    "flow_prefixes": {"P": "Production", "Q": "Préproduction", "X": "Hors environnement"},
    "owners": {"hebergeur": "Hébergeur Exemple", "agence": "Gingerminds", "client": "Client Exemple"},
    "known_issues": True,
    "repositories": [],
}


def write_project(tmp: Path, config: Optional[dict] = None, readme: Optional[str] = None) -> Path:
    """Create a docs/architecture tree under tmp and return its root."""
    root = Path(tmp) / "docs" / "architecture"
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / "views").mkdir(exist_ok=True)
    data = copy.deepcopy(CONFIG) if config is None else config
    (root / "tools" / "architecture.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if readme is not None:
        (root / "README.md").write_text(readme, encoding="utf-8")
    return root
