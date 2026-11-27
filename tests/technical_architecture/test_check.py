import copy
import tempfile
import unittest
from pathlib import Path

from technical_architecture.architecture import check, extract_diagram, load_config, matrix_rows, view_text
from tests.technical_architecture.helpers import CONFIG, flow_row, mxfile, readme, write_project, write_view

VALID_LABELS = ("Légende", "Vue d'ensemble\nÉtat au 26/11/2026", "P01 · 443/tcp")
UNREADABLE = (
    "01-vue.drawio.svg : aucun diagramme embarqué ou diagramme illisible "
    "(enregistrer en .drawio.svg avec une copie du diagramme)"
)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def project(self, labels=VALID_LABELS, rows=(flow_row("P01"),), config=None, compressed=False):
        root = write_project(self.tmp, config, readme(rows))
        write_view(root / "views" / "01-vue.drawio.svg", mxfile(*labels), compressed)
        return load_config(root)

    def test_valid_project_passes(self):
        self.assertEqual(check(self.project()), [])

    def test_tool_version_mismatch_is_reported(self):
        data = copy.deepcopy(CONFIG)
        data["tool_version"] = "0.9.0"
        self.assertEqual(
            check(self.project(config=data)),
            ["architecture.json : tool_version 0.9.0 ne correspond pas à l'outil 1.0.0"],
        )

    def test_no_view_is_reported(self):
        config = load_config(write_project(self.tmp, None, readme([])))
        self.assertEqual(check(config), ["views : aucune vue (*.drawio.svg)"])

    def test_unreadable_view_is_reported(self):
        svgs = {
            "invalid content": '<svg xmlns="http://www.w3.org/2000/svg" content="%%%"/>',
            "no content": '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
            "broken page": '<svg xmlns="http://www.w3.org/2000/svg" content="&lt;mxfile&gt;&lt;diagram&gt;!!!&lt;/diagram&gt;&lt;/mxfile&gt;"/>',
            "not xml": "pas du XML",
        }
        for name, svg in svgs.items():
            with self.subTest(name):
                config = self.project()
                (config.views_dir / "01-vue.drawio.svg").write_text(svg, encoding="utf-8")
                self.assertEqual(check(config), [UNREADABLE, "P01 : dans la matrice des flux mais sur aucune vue"])

    def test_duplicate_flow_id_is_reported(self):
        config = self.project(rows=(flow_row("P01"), flow_row("P01")))
        self.assertEqual(check(config), ["P01 : identifiant en double dans la matrice des flux"])

    def test_malformed_flow_id_is_reported(self):
        config = self.project(rows=(flow_row("P01"), flow_row("P1"), flow_row("P001")))
        expected = [
            f"{flow_id} : identifiant invalide (une lettre parmi P, Q, X puis deux chiffres)"
            for flow_id in ("P1", "P001")
        ]
        self.assertEqual(check(config), expected)

    def test_unknown_prefix_is_reported(self):
        config = self.project(rows=(flow_row("P01"), flow_row("R01")))
        self.assertEqual(
            check(config), ["R01 : identifiant invalide (une lettre parmi P, Q, X puis deux chiffres)"]
        )

    def test_compressed_view_is_read(self):
        config = self.project(compressed=True)
        view = config.views_dir / "01-vue.drawio.svg"
        self.assertNotIn("mxGraphModel", view.read_text(encoding="utf-8"))
        self.assertEqual(view_text(extract_diagram(view)), view_text(mxfile(*VALID_LABELS)))
        self.assertEqual(check(config), [])

    def test_missing_cartouche_and_legend(self):
        config = self.project(labels=("P01 · 443/tcp",))
        self.assertEqual(
            check(config),
            ["01-vue.drawio.svg : cartouche sans « État au JJ/MM/AAAA »", "01-vue.drawio.svg : « Légende » absente"],
        )

    def test_external_image_is_reported(self):
        config = self.project()
        view = config.views_dir / "01-vue.drawio.svg"
        view.write_text(
            view.read_text(encoding="utf-8").replace("<rect ", '<image href="img/lib/icon.svg" /><rect '),
            encoding="utf-8",
        )
        self.assertEqual(
            check(config),
            ["01-vue.drawio.svg : image non embarquée (img/lib/icon.svg), réenregistrer en incorporant les images"],
        )

    def test_invalid_status_is_reported(self):
        config = self.project(rows=(flow_row("P01", "vu"),))
        errors = check(config)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("P01 : statut « vu » invalide"))

    def test_valid_statuses_pass(self):
        for status in ("décidé", "à confirmer", "indiqué par l'hébergeur (20/11/2026)", "constaté (01/02/2026)"):
            with self.subTest(status):
                self.assertEqual(check(self.project(rows=(flow_row("P01", status),))), [])

    def test_secret_and_em_dash_are_reported(self):
        labels = VALID_LABELS + ("password: hunter2", "Proxy \u2014 cache")
        root = write_project(self.tmp, None, readme([flow_row("P01")]) + "\ntoken = abc123 \u2014 fin\n")
        write_view(root / "views" / "01-vue.drawio.svg", mxfile(*labels))
        self.assertEqual(
            check(load_config(root)),
            [
                "01-vue.drawio.svg : secret possible (mot de passe, jeton ou clé), à retirer",
                "01-vue.drawio.svg : tiret long interdit, le remplacer",
                "README.md : secret possible (mot de passe, jeton ou clé), à retirer",
                "README.md : tiret long interdit, le remplacer",
            ],
        )

    def test_json_secret_is_reported(self):
        labels = VALID_LABELS + ('{"password": "hunter2"}',)
        root = write_project(self.tmp, None, readme([flow_row("P01")]) + "\n`\"token\": \"abc\"`\n")
        write_view(root / "views" / "01-vue.drawio.svg", mxfile(*labels))
        self.assertEqual(
            check(load_config(root)),
            [
                "01-vue.drawio.svg : secret possible (mot de passe, jeton ou clé), à retirer",
                "README.md : secret possible (mot de passe, jeton ou clé), à retirer",
            ],
        )

    def test_flow_on_view_missing_from_matrix(self):
        config = self.project(labels=VALID_LABELS + ("Q02 · 22/tcp",))
        self.assertEqual(check(config), ["Q02 : sur la vue 01-vue.drawio.svg mais absent de la matrice des flux"])

    def test_flow_in_matrix_missing_from_views(self):
        config = self.project(rows=(flow_row("P01"), flow_row("X03")))
        self.assertEqual(check(config), ["X03 : dans la matrice des flux mais sur aucune vue"])

    def test_missing_readme(self):
        root = write_project(self.tmp)
        write_view(root / "views" / "01-vue.drawio.svg", mxfile(*VALID_LABELS))
        self.assertEqual(check(load_config(root)), ["README.md : introuvable"])

    def test_multi_page_view_is_reported(self):
        config = self.project()
        two_pages = mxfile(*VALID_LABELS).replace(
            "</mxfile>", mxfile("P02 · 22/tcp").split(">", 1)[1].replace("</mxfile>", "") + "</mxfile>"
        )
        write_view(config.views_dir / "01-vue.drawio.svg", two_pages)
        self.assertEqual(two_pages.count("<diagram "), 2)
        self.assertEqual(check(config), ["01-vue.drawio.svg : plusieurs pages (une vue = une page)"])

    def test_formatted_flow_ids_are_reported(self):
        cells = ("`P02`", "**P03**", "p04", "P 05")
        config = self.project(rows=(flow_row("P01"),) + tuple(flow_row(cell) for cell in cells))
        self.assertEqual(
            check(config),
            [f"{cell} : identifiant invalide (une lettre parmi P, Q, X puis deux chiffres)" for cell in cells],
        )

    def test_html_labels_are_stripped(self):
        xml = (
            '<mxfile><diagram id="d" name="Vue"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="a" value="&lt;b&gt;P01&lt;/b&gt;&amp;nbsp;&amp;middot; 443/tcp&lt;br&gt;proxy" '
            'style="rounded=1;html=1;" vertex="1" parent="1"/>'
            '<UserObject id="b" label="&lt;i&gt;Légende&lt;/i&gt;"><mxCell style="text;html=1;" vertex="1" parent="1"/>'
            '</UserObject>'
            '<mxCell id="c" value="Front &amp; API &lt;v2&gt;" style="html=0;" vertex="1" parent="1"/>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        self.assertEqual(view_text(xml), "P01 · 443/tcp proxy\nLégende\nFront & API <v2>")

    def test_matrix_rows_follow_prefixes(self):
        config = self.project(rows=(flow_row("P01"), flow_row("Q03"), flow_row("R01")))
        rows = matrix_rows(config.readme.read_text(encoding="utf-8"), config.flow_id_re)
        self.assertEqual([row["#"] for row in rows], ["P01", "Q03"])
        self.assertEqual(rows[0]["Port / protocole"], "443/tcp")


if __name__ == "__main__":
    unittest.main()
