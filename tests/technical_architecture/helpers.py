"""Shared fixtures for the technical architecture tool tests."""

import base64
import copy
import json
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Optional, Sequence

from technical_architecture.architecture import MATRIX_HEADING

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


MATRIX_HEADER = (
    "| # | Source | Destination | Port / protocole | Chiffré | Authentification | Finalité | Géré par | Statut |\n"
    "|---|--------|-------------|------------------|---------|------------------|----------|----------|--------|"
)
STATUS_LINE = (
    "Statuts : `constaté (JJ/MM/AAAA)`, `décidé`, `indiqué par <qui> (JJ/MM/AAAA)`, `à confirmer`."
)


def mxfile(*labels: str) -> str:
    """A one-page uncompressed mxfile with one vertex per label."""
    root_file = ET.Element("mxfile", host="tests")
    diagram = ET.SubElement(root_file, "diagram", id="page-1", name="Vue")
    model = ET.SubElement(diagram, "mxGraphModel")
    cells = ET.SubElement(model, "root")
    ET.SubElement(cells, "mxCell", id="0")
    ET.SubElement(cells, "mxCell", id="1", parent="0")
    for index, label in enumerate(labels):
        cell = ET.SubElement(
            cells, "mxCell", id=f"v{index}", value=label, style="html=0;", vertex="1", parent="1"
        )
        ET.SubElement(cell, "mxGeometry", {"x": str(index * 120), "y": "0", "width": "100", "height": "40", "as": "geometry"})
    return ET.tostring(root_file, encoding="unicode")


def compress_diagrams(mxfile_xml: str) -> str:
    """Store every page the way diagrams.net does: base64 of raw deflate of the URL-quoted model."""
    root_file = ET.fromstring(mxfile_xml)
    for diagram in root_file.iter("diagram"):
        model = diagram.find("mxGraphModel")
        quoted = urllib.parse.quote(ET.tostring(model, encoding="unicode"), safe="-_.!~*'()")
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        raw = packer.compress(quoted.encode("utf-8")) + packer.flush()
        diagram.remove(model)
        diagram.text = base64.b64encode(raw).decode("ascii")
    return ET.tostring(root_file, encoding="unicode")


def write_view(path: Path, mxfile_xml: str, compressed: bool = False) -> Path:
    """Write a minimal .drawio.svg whose content attribute holds the diagram."""
    content = compress_diagrams(mxfile_xml) if compressed else mxfile_xml
    svg = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "content": content})
    ET.SubElement(svg, "rect", width="10", height="10")
    path.write_text(ET.tostring(svg, encoding="unicode"), encoding="utf-8")
    return path


def flow_row(flow_id: str, status: str = "constaté (26/11/2026)") -> str:
    """A matrix row for the given flow ID."""
    return f"| {flow_id} | Internet | Proxy | 443/tcp | oui | aucune | Site public | Agence | {status} |"


def readme(rows: Sequence[str], known_issues: bool = True) -> str:
    """A minimal README with the structure the tool reads."""
    parts = [
        "# Architecture de la plateforme Exemple",
        "## Vues",
        "| Vue | Question |\n|-----|----------|\n| [01 · Vue](views/01-vue.drawio.svg) | Question ? |",
        "## Légende",
        "Couleurs par responsable.",
        MATRIX_HEADING,
        STATUS_LINE,
        "### Production",
        MATRIX_HEADER + "".join("\n" + row for row in rows),
    ]
    if known_issues:
        parts += ["## Points d'attention connus", "| Point | Détail |\n|-------|--------|\n| Sauvegardes | Non testées. |"]
    return "\n\n".join(parts) + "\n"
