import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from technical_architecture import drawio_kit
from technical_architecture.architecture import check, load_config, view_text
from technical_architecture.drawio_kit import ICONS, Diagram
from tests.technical_architecture.helpers import CONFIG, flow_row, readme, write_project, write_view


def cells(diagram):
    return list(ET.fromstring(diagram.to_xml()).iter("mxCell"))


def style_of(diagram, cell_id):
    return next(cell.get("style") for cell in cells(diagram) if cell.get("id") == cell_id)


def sample():
    diagram = Diagram("vue", "Vue")
    zone = diagram.zone("prod", "Production", 20, 20, 600, 300, role="hebergeur", icon="vm")
    diagram.box("web", "Proxy", 20, 40, 160, 50, "agence", parent=zone, icon="docker")
    diagram.box("db", "Base", 300, 40, 160, 50, "agence", parent=zone, icon="database")
    diagram.edge("e1", "P01 · 3306/tcp", "web", "db", "donnees", (1, 0.5), (0, 0.5))
    diagram.edge("e2", "P02 · 80/tcp (clair)", "db", "web", "visiteur", (0.5, 1), (0.5, 1),
                 clear=True, points=((400, 140), (100, 140)))
    return diagram


class DrawioKitTest(unittest.TestCase):
    def test_every_style_is_plain_text(self):
        diagram = sample()
        diagram.legend(20, 400, CONFIG["owners"])
        diagram.cartouche(700, 20, 400, 150, "Vue", "Production", "26/11/2026", "Sources : dépôt.")
        styles = [cell.get("style") for cell in cells(diagram) if cell.get("style")]
        self.assertGreater(len(styles), 20)
        for style in styles:
            self.assertIn("html=0", style)

    def test_ids_unique_and_edges_connected(self):
        diagram = sample()
        with self.assertRaises(ValueError):
            diagram.box("web", "Autre", 0, 0, 10, 10, "agence")
        with self.assertRaises(ValueError):
            diagram.edge("e1", "P03", "web", "db", "donnees", (1, 0.5), (0, 0.5))
        with self.assertRaises(ValueError):
            diagram.edge("e9", "P03", "web", "absent", "donnees", (1, 0.5), (0, 0.5))
        all_cells = cells(diagram)
        ids = [cell.get("id") for cell in all_cells]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in all_cells:
            if cell.get("edge") and cell.get("source"):
                self.assertIn(cell.get("source"), ids)
                self.assertIn(cell.get("target"), ids)

    def test_icon_style(self):
        style = style_of(sample(), "web")
        self.assertIn("shape=label;image=img/lib/mscae/Docker.svg;imageWidth=28;imageHeight=23;imageAlign=left;", style)
        self.assertIn("imageVerticalAlign=middle;", style)
        self.assertIn("spacingLeft=40;", style)
        zone_style = style_of(sample(), "prod")
        self.assertIn("image=img/lib/azure2/compute/Virtual_Machine.svg;imageWidth=19;imageHeight=18;", zone_style)
        self.assertIn("imageVerticalAlign=top;", zone_style)
        self.assertEqual(ICONS["ssh"], ("img/lib/azure2/other/SSH_Keys.svg", 18, 16))

    def test_edge_style(self):
        diagram = sample()
        plain, clear = style_of(diagram, "e1"), style_of(diagram, "e2")
        for style in (plain, clear):
            self.assertIn("jumpStyle=arc;jumpSize=8;", style)
            self.assertIn("edgeStyle=orthogonalEdgeStyle;", style)
        self.assertNotIn("dashed=1", plain)
        self.assertIn("dashed=1;", clear)
        self.assertIn("strokeColor=#1f6fb2;", plain)

    def test_unknown_role_nature_icon(self):
        diagram = Diagram("vue", "Vue")
        with self.assertRaises(ValueError) as raised:
            diagram.box("a", "A", 0, 0, 10, 10, "hoster")
        self.assertEqual(str(raised.exception), "rôle inconnu : hoster")
        diagram.box("b", "B", 0, 0, 10, 10, "agence")
        diagram.box("c", "C", 50, 0, 10, 10, "agence")
        with self.assertRaises(ValueError) as raised:
            diagram.edge("e", "", "b", "c", "web", (1, 0.5), (0, 0.5))
        self.assertEqual(str(raised.exception), "nature inconnue : web")
        with self.assertRaises(ValueError) as raised:
            diagram.box("d", "D", 100, 0, 10, 10, "agence", icon="kubernetes")
        self.assertEqual(str(raised.exception), "pictogramme inconnu : kubernetes")

    def test_label_segment_is_checked(self):
        diagram = sample()
        for segment in (1, 5, -1):
            with self.subTest(segment=segment), self.assertRaises(ValueError) as raised:
                diagram.edge(f"seg{segment}", "P09", "web", "db", "donnees", (1, 0.5), (0, 0.5), label_seg=segment)
            self.assertEqual(str(raised.exception), f"segment inconnu : {segment}")
        diagram.edge("seg0", "P09", "web", "db", "donnees", (1, 0.5), (0, 0.5), label_seg=0)

    def test_write_refuses_overlaps(self):
        diagram = sample()
        diagram.box("cache", "Cache", 150, 60, 100, 40, "agence", parent="prod")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vue.drawio"
            with self.assertRaises(ValueError) as raised:
                diagram.write(path)
            self.assertEqual(str(raised.exception), "blocs qui se chevauchent : web / cache")
            self.assertFalse(path.exists())

    def test_overlaps(self):
        diagram = sample()
        self.assertEqual(diagram.overlaps(), [])
        diagram.box("cache", "Cache", 150, 60, 100, 40, "agence", parent="prod")
        self.assertEqual(diagram.overlaps(), [("web", "cache")])
        diagram.box("touching", "Bord", 620, 20, 50, 50, "tiers")
        self.assertEqual(diagram.overlaps(), [("web", "cache")])

    def test_legend_uses_owner_names_and_no_flow_id(self):
        diagram = Diagram("vue", "Vue")
        owners = dict(CONFIG["owners"], tiers="Service tiers")
        diagram.legend(0, 0, owners)
        text = view_text(diagram.to_xml())
        for expected in ("Hébergeur Exemple (hébergeur)", "Gingerminds (agence)", "Client Exemple (client)",
                         "Service tiers", "À confirmer", "Légende",
                         "« Pnn · port/tcp » : identifiant de la matrice des flux, port et protocole."):
            self.assertIn(expected, text)
        self.assertNotIn("(tiers)", text)
        config = load_config(write_project(Path(tempfile.mkdtemp())))
        self.assertEqual(config.flow_id_re.findall(text), [])
        self.assertEqual(diagram.overlaps(), [])

    def test_legend_grows_for_long_names(self):
        diagram = Diagram("vue", "Vue")
        owners = {"hebergeur": "Hébergeur au nom vraiment très long", "agence": "Agence au nom long",
                  "client": "Client au nom vraiment très long", "tiers": "Prestataires tiers"}
        diagram.legend(0, 0, owners)
        self.assertEqual(diagram.overlaps(), [])
        zone_width = float(next(c for c in cells(diagram) if c.get("id") == "legend").find("mxGeometry").get("width"))
        self.assertGreaterEqual(zone_width, 930)

    def test_cartouche(self):
        diagram = Diagram("vue", "Vue")
        diagram.cartouche(0, 0, 400, 150, "Infrastructure de production", "Production", "26/11/2026",
                          "Sources : dépôt, relevé SSH.")
        text = view_text(diagram.to_xml())
        self.assertIn("Infrastructure de production", text)
        self.assertIn("Production\nÉtat au 26/11/2026", text)
        self.assertIn("Sources : dépôt, relevé SSH.", text)

    def test_text_blocks_wrap_to_their_width(self):
        diagram = Diagram("vue", "Vue")
        sources = ("Sources : dépôt (compose, CI), relevé SSH en lecture seule du 26/11/2026, "
                   "indications de l'hébergeur (NAT, pare-feu).")
        diagram.text("t", sources + "\nSeconde ligne.", 0, 0, 300, 100, font=11)
        lines = view_text(diagram.to_xml()).split("\n")
        self.assertGreater(len(lines), 3)
        self.assertTrue(all(len(line) <= 300 / (11 * 0.55) for line in lines))
        self.assertEqual(" ".join(lines), sources + " Seconde ligne.")
        self.assertEqual(lines[-1], "Seconde ligne.")

    def test_wrap_never_starts_a_line_with_french_punctuation(self):
        diagram = Diagram("vue", "Vue")
        note = ("Une seule VM porte toute la pile : les échanges avec les bases restent internes "
                "(réseau Docker), d'où des flèches sans identifiant ; « exemple » ?")
        diagram.text("t", note, 0, 0, 180, 120)
        lines = view_text(diagram.to_xml()).split("\n")
        self.assertEqual(" ".join(lines), note)
        for line in lines:
            self.assertFalse(line[:1] in ":;?!»", line)
            self.assertFalse(line.endswith("«"), line)

    def test_wrap_keeps_host_names_and_dates_whole(self):
        diagram = Diagram("vue", "Vue")
        diagram.text("t", "Hôte srv-web-01.example.org via proxy-interne-02", 0, 0, 140, 60)
        diagram.cartouche(200, 0, 120, 150, "Vue", "Production", "26/11/2026", "Sources : dépôt.")
        lines = view_text(diagram.to_xml()).split("\n")
        for name in ("srv-web-01.example.org", "proxy-interne-02"):
            self.assertTrue(any(name in line for line in lines), lines)
        self.assertFalse(any(line.endswith("-") for line in lines), lines)
        self.assertIn("État au 26/11/2026", lines)

    def test_special_characters_round_trip(self):
        diagram = Diagram("vue", "Vue")
        diagram.box("a", "Front & API <v2>\nété", 0, 0, 200, 60, "client")
        self.assertEqual(view_text(diagram.to_xml()), "Front & API <v2>\nété")

    def test_bold_markers_are_dropped(self):
        diagram = Diagram("vue", "Vue")
        diagram.box("a", "**VM web**\n2 vCPU", 0, 0, 200, 60, "hebergeur")
        self.assertEqual(view_text(diagram.to_xml()), "VM web\n2 vCPU")

    def test_write_produces_a_drawio_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vue.drawio"
            sample().write(path)
            root = ET.parse(path).getroot()
            self.assertEqual(root.tag, "mxfile")
            self.assertIsNotNone(root.find("diagram/mxGraphModel/root"))

    def test_kit_view_passes_check(self):
        diagram = Diagram("vue", "Vue")
        diagram.box("internet", "Internet", 20, 20, 160, 50, "internet", icon="internet")
        diagram.box("proxy", "Proxy", 400, 20, 160, 50, "agence", icon="docker")
        diagram.edge("p01", "P01 · 443/tcp", "internet", "proxy", "visiteur", (1, 0.5), (0, 0.5))
        diagram.legend(20, 200, CONFIG["owners"])
        diagram.cartouche(1000, 200, 400, 150, "Vue", "Production", "26/11/2026", "Sources : dépôt.")
        with tempfile.TemporaryDirectory() as tmp:
            root = write_project(Path(tmp), None, readme([flow_row("P01")]))
            write_view(root / "views" / "01-vue.drawio.svg", diagram.to_xml())
            self.assertEqual(check(load_config(root)), [])

    def test_kit_is_standalone(self):
        source = Path(drawio_kit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import architecture", source)
        self.assertNotIn("from architecture", source)
        self.assertNotIn("technical_architecture", source)


if __name__ == "__main__":
    unittest.main()
