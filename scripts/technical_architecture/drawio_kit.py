"""Drawing kit for the technical architecture views: builds draw.io XML.

Used by throwaway per-view generators (`build() -> Diagram`, then `write`).
Labels are plain text (html=0): HTML labels make draw.io embed a raster
fallback image per label in the SVG. Standalone: never imports the tool.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

# Fill and stroke colour per role (who runs the component).
ROLES: Dict[str, Tuple[str, str]] = {
    "hebergeur": ("#dae8fc", "#6c8ebf"),
    "agence": ("#d5e8d4", "#82b366"),
    "client": ("#ffe6cc", "#d79b00"),
    "tiers": ("#e1d5e7", "#9673a6"),
    "internet": ("#ffffff", "#666666"),
    "confirm": ("#f5f5f5", "#999999"),
    "perso": ("#f8cecc", "#b85450"),
}
ZONE_FILL: Dict[str, str] = {
    "hebergeur": "#f4f8fd",
    "agence": "#f4faf3",
    "client": "#fff8f0",
    "tiers": "#f8f4fa",
    "internet": "#fafafa",
}
# Colour and width per flow nature.
NATURES: Dict[str, Tuple[str, float]] = {
    "visiteur": ("#333333", 2),
    "donnees": ("#1f6fb2", 1.5),
    "admin": ("#b85450", 1.5),
    "confirm": ("#999999", 1.5),
}
# draw.io built-in images: path, width, height.
ICONS: Dict[str, Tuple[str, int, int]] = {
    "docker": ("img/lib/mscae/Docker.svg", 28, 23),
    "vm": ("img/lib/azure2/compute/Virtual_Machine.svg", 19, 18),
    "ssh": ("img/lib/azure2/other/SSH_Keys.svg", 18, 16),
    "users": ("img/lib/azure2/identity/Users.svg", 24, 24),
    "internet": ("img/lib/azure2/general/Globe.svg", 19, 19),
    "network": ("img/lib/azure2/networking/Virtual_Networks.svg", 20, 15),
    "database": ("img/lib/mscae/Azure_Database_for_MySQL_servers.svg", 20, 26),
    "api": ("img/lib/azure2/devops/API_Connections.svg", 24, 18),
    "building": ("img/lib/allied_telesis/buildings/Medium_Business_Building.svg", 22, 24),
    "monitoring": ("img/lib/azure2/management_governance/Monitor.svg", 24, 24),
    "workflow": ("img/lib/azure2/general/Workflow.svg", 24, 24),
    "servers": ("img/lib/azure2/general/Server_Farm.svg", 19, 20),
    "folder": ("img/lib/azure2/general/Folder_Blank.svg", 24, 20),
    "registry": ("img/lib/azure2/containers/Container_Registries.svg", 24, 22),
    "gitlab": ("images/gitlab-logo.svg", 26, 24),
    "waf": ("img/lib/azure2/networking/Web_Application_Firewall_Policies_WAF.svg", 24, 24),
    "nat": ("img/lib/azure2/networking/NAT.svg", 24, 24),
    "firewall": ("img/lib/azure2/networking/Firewalls.svg", 20, 19),
    "backup": ("img/lib/azure2/management_governance/Recovery_Services_Vaults.svg", 26, 22),
    "identity": ("img/lib/azure2/identity/Azure_Active_Directory.svg", 24, 24),
    "scheduler": ("img/lib/azure2/general/Scheduler.svg", 24, 24),
    "logs": ("img/lib/azure2/analytics/Log_Analytics_Workspaces.svg", 24, 24),
    "files": ("img/lib/azure2/general/Files.svg", 22, 24),
    "dashboard": ("img/lib/azure2/general/Dashboard.svg", 24, 22),
    "branch": ("img/lib/azure2/general/Branch.svg", 24, 24),
    "health": ("img/lib/azure2/general/Service_Health.svg", 24, 22),
    "builds": ("img/lib/azure2/general/Builds.svg", 20, 20),
}
LEGEND_ROLES = (("hebergeur", " (hébergeur)"), ("agence", " (agence)"), ("client", " (client)"), ("tiers", ""))

Point = Tuple[float, float]


def _role(role: str) -> Tuple[str, str]:
    if role not in ROLES:
        raise ValueError(f"rôle inconnu : {role}")
    return ROLES[role]


def _nature(nature: str) -> Tuple[str, float]:
    if nature not in NATURES:
        raise ValueError(f"nature inconnue : {nature}")
    return NATURES[nature]


def _icon_style(icon: Optional[str], vertical: str) -> Tuple[str, int]:
    """Style prefix for a built-in image at the left of the label, and the image width."""
    if icon is None:
        return "", 0
    if icon not in ICONS:
        raise ValueError(f"pictogramme inconnu : {icon}")
    path, width, height = ICONS[icon]
    style = (f"shape=label;image={path};imageWidth={width};imageHeight={height};"
             f"imageAlign=left;imageVerticalAlign={vertical};")
    return style, width


class Diagram:
    """A single-page draw.io diagram with absolute geometry tracking."""

    def __init__(self, diagram_id: str, name: str, width: int = 1800, height: int = 1100) -> None:
        self.mxfile = ET.Element("mxfile", host="drawio")
        diagram = ET.SubElement(self.mxfile, "diagram", id=diagram_id, name=name)
        model = ET.SubElement(diagram, "mxGraphModel", dx=str(width), dy=str(height), grid="1", gridSize="10",
                              guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1",
                              pageScale="1", pageWidth=str(width), pageHeight=str(height),
                              math="0", shadow="0")
        self.root = ET.SubElement(model, "root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self.rects: Dict[str, Tuple[float, float, float, float]] = {"1": (0, 0, width, height)}
        self.ids = {"0", "1"}
        # Vertices in creation order: id, parent, relative rectangle.
        self.vertices: List[Tuple[str, str, Tuple[float, float, float, float]]] = []

    @staticmethod
    def label(text: str) -> str:
        """Plain-text label; ** markers are dropped."""
        return text.replace("**", "")

    def _claim(self, cell_id: str) -> None:
        if cell_id in self.ids:
            raise ValueError(f"identifiant en double : {cell_id}")
        self.ids.add(cell_id)

    def _vertex(self, cell_id, value, style, x, y, w, h, parent) -> str:
        if parent not in self.rects:
            raise ValueError(f"cellule inconnue : {parent}")
        self._claim(cell_id)
        cell = ET.SubElement(self.root, "mxCell", id=cell_id, value=value, style=style, vertex="1", parent=parent)
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
        px, py, _, _ = self.rects[parent]
        self.rects[cell_id] = (px + x, py + y, w, h)
        self.vertices.append((cell_id, parent, (x, y, w, h)))
        return cell_id

    def zone(self, cell_id: str, text: str, x: float, y: float, w: float, h: float, role: str = "internet",
             parent: str = "1", dashed: bool = False, font: int = 13, icon: Optional[str] = None) -> str:
        """A container zone (network, machine, organisation) with its bold title at the top left."""
        _, stroke = _role(role)
        image, width = _icon_style(icon, "top")
        style = (image + "rounded=1;arcSize=2;html=0;container=1;collapsible=0;recursiveResize=0;"
                 f"verticalAlign=top;align=left;spacingLeft={width + 20 if icon else 10};spacingTop=4;"
                 f"fontSize={font};fontStyle=1;"
                 f"fillColor={ZONE_FILL.get(role, '#ffffff')};strokeColor={stroke};strokeWidth=1.5;"
                 + ("dashed=1;" if dashed else ""))
        return self._vertex(cell_id, self.label(text), style, x, y, w, h, parent)

    def box(self, cell_id: str, text: str, x: float, y: float, w: float, h: float, role: str,
            parent: str = "1", confirm: bool = False, font: int = 11, bold: bool = False,
            icon: Optional[str] = None) -> str:
        """A component box coloured by role; confirm=True marks it as unconfirmed."""
        fill, stroke = ROLES["confirm"] if confirm else _role(role)
        image, width = _icon_style(icon, "middle")
        style = (image + f"rounded=1;arcSize=8;html=0;fontSize={font};align=center;spacing=4;"
                 + (f"spacingLeft={width + 12};" if icon else "")
                 + f"fillColor={fill};strokeColor={stroke};"
                 + ("fontStyle=1;" if bold else "")
                 + ("dashed=1;fontColor=#666666;" if confirm else "fontColor=#1a1a1a;"))
        return self._vertex(cell_id, self.label(text), style, x, y, w, h, parent)

    def text(self, cell_id: str, text: str, x: float, y: float, w: float, h: float, parent: str = "1",
             font: int = 10, align: str = "left", color: str = "#333333", bold: bool = False) -> str:
        """A free text block."""
        style = (f"text;html=0;align={align};verticalAlign=top;fontSize={font};fontColor={color};spacing=2;"
                 + ("fontStyle=1;" if bold else ""))
        return self._vertex(cell_id, self.label(text), style, x, y, w, h, parent)

    def _anchor(self, cell_id: str, rel: Point) -> Point:
        x, y, w, h = self.rects[cell_id]
        return (x + rel[0] * w, y + rel[1] * h)

    def edge(self, cell_id: str, text: str, source: str, target: str, nature: str, exit: Point, entry: Point,
             clear: bool = False, points: Sequence[Point] = (), label_seg: Optional[int] = None,
             label_frac: float = 0.5, both: bool = False, font: int = 10) -> str:
        """An orthogonal flow arrow through absolute waypoints.

        The label sits on segment label_seg (default: the longest one), at
        label_frac of that segment. clear=True draws it dashed (unencrypted).
        """
        color, width = _nature(nature)
        for end in (source, target):
            if end not in self.rects or end == "1":
                raise ValueError(f"cellule inconnue : {end}")
        self._claim(cell_id)
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;endArrow=block;endFill=1;"
                 f"jumpStyle=arc;jumpSize=8;"
                 f"strokeColor={color};strokeWidth={width};fontSize={font};fontColor={color};"
                 f"labelBackgroundColor=#ffffff;"
                 f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
                 f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
                 + ("dashed=1;" if clear else "")
                 + ("startArrow=block;startFill=1;" if both else ""))
        cell = ET.SubElement(self.root, "mxCell", id=cell_id, value=self.label(text), style=style, edge="1",
                             parent="1", source=source, target=target)
        geometry = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        path = [self._anchor(source, exit), *points, self._anchor(target, entry)]
        lengths = [math.dist(a, b) for a, b in zip(path, path[1:])]
        total = sum(lengths) or 1
        seg = lengths.index(max(lengths)) if label_seg is None else label_seg
        position = (sum(lengths[:seg]) + lengths[seg] * label_frac) / total
        geometry.set("x", f"{2 * position - 1:.3f}")
        if points:
            array = ET.SubElement(geometry, "Array", **{"as": "points"})
            for px, py in points:
                ET.SubElement(array, "mxPoint", x=str(px), y=str(py))
        return cell_id

    def sample_edge(self, cell_id: str, text: str, x: float, y: float, length: float, nature: str,
                    clear: bool = False, parent: str = "1") -> None:
        """A legend sample arrow between two free points, with its text on the right."""
        color, width = _nature(nature)
        self._claim(cell_id)
        style = (f"html=0;endArrow=block;endFill=1;strokeColor={color};strokeWidth={width};"
                 + ("dashed=1;" if clear else ""))
        cell = ET.SubElement(self.root, "mxCell", id=cell_id, value="", style=style, edge="1", parent=parent)
        geometry = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        ET.SubElement(geometry, "mxPoint", x=str(x), y=str(y), **{"as": "sourcePoint"})
        ET.SubElement(geometry, "mxPoint", x=str(x + length), y=str(y), **{"as": "targetPoint"})
        self.text(cell_id + "-txt", text, x + length + 8, y - 8, 230, 20, parent=parent, font=10)

    def legend(self, x: float, y: float, owners: Mapping[str, str], w: float = 930, h: float = 190,
               extra: Optional[Tuple[str, str]] = None) -> str:
        """Standard legend: one box per role named in owners, unconfirmed, flow natures, encryption.

        extra is an optional (label, role) box, for instance ("Données personnelles", "perso").
        """
        items = [(role, owners[role] + suffix) for role, suffix in LEGEND_ROLES if role in owners]
        boxes = [(f"legend-{role}", text, role, False) for role, text in items]
        boxes.append(("legend-confirm", "À confirmer", "internet", True))
        if extra:
            boxes.append(("legend-extra", extra[0], extra[1], False))
        widths = [max(140, round(len(text) * 6.2) + 20) for _, text, _, _ in boxes]
        w = max(w, 15 + sum(width + 10 for width in widths) + 5)
        zone = self.zone("legend", "Légende", x, y, w, h, role="internet", font=12)
        left = 15
        for (cell_id, text, role, confirm), width in zip(boxes, widths):
            self.box(cell_id, text, left, 32, width, 32, role, parent=zone, confirm=confirm, font=10)
            left += width + 10
        self.text("legend-zone", "Cadre = zone réseau ou machine ; bloc = composant, coloré selon qui le gère.",
                  15, 72, 900, 20, parent=zone, font=10)
        self.sample_edge("legend-visiteur", "Trafic des visiteurs", 20, 112, 60, "visiteur", parent=zone)
        self.sample_edge("legend-donnees", "Flux de données", 330, 112, 60, "donnees", parent=zone)
        self.sample_edge("legend-admin", "Administration, déploiement, supervision", 620, 112, 60, "admin",
                         parent=zone)
        self.sample_edge("legend-chiffre", "Trait plein : chiffré (TLS, SSH)", 20, 145, 60, "donnees", parent=zone)
        self.sample_edge("legend-clair", "Pointillés : non chiffré « (clair) »", 330, 145, 60, "donnees",
                         clear=True, parent=zone)
        self.sample_edge("legend-confirm-edge", "Gris : flux à confirmer", 620, 145, 60, "confirm", parent=zone)
        self.text("legend-ids", "« Pnn · port/tcp » : identifiant de la matrice des flux, port et protocole.",
                  15, 162, 900, 20, parent=zone, font=10)
        return zone

    def cartouche(self, x: float, y: float, w: float, h: float, title: str, environment: str, state: str,
                  sources: str) -> str:
        """Standard title block: title, environment and « État au » date, sources."""
        zone = self.zone("cartouche", title, x, y, w, h, role="internet", font=16)
        self.text("cartouche-env", f"{environment}\nÉtat au {state}", 15, 32, w - 30, 40, parent=zone, font=12,
                  bold=True)
        self.text("cartouche-sources", sources, 15, 80, w - 30, h - 90, parent=zone, font=11)
        return zone

    def overlaps(self) -> List[Tuple[str, str]]:
        """Pairs of sibling vertices whose rectangles intersect with a positive area."""
        found = []
        for index, (first, parent, (x1, y1, w1, h1)) in enumerate(self.vertices):
            for second, other_parent, (x2, y2, w2, h2) in self.vertices[index + 1:]:
                if parent != other_parent:
                    continue
                if min(x1 + w1, x2 + w2) - max(x1, x2) > 0 and min(y1 + h1, y2 + h2) - max(y1, y2) > 0:
                    found.append((first, second))
        return found

    def to_xml(self) -> str:
        """The uncompressed mxfile XML."""
        return ET.tostring(self.mxfile, encoding="unicode")

    def write(self, path: Path) -> None:
        """Write the uncompressed .drawio file."""
        ET.indent(self.mxfile)
        ET.ElementTree(self.mxfile).write(path, encoding="unicode")
