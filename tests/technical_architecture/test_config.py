import copy
import tempfile
import unittest
from pathlib import Path

from technical_architecture.architecture import ConfigError, load_config
from tests.technical_architecture.helpers import CONFIG, write_project


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, **changes):
        data = copy.deepcopy(CONFIG)
        data.update(changes)
        return data

    def test_valid_config_is_loaded(self):
        config = load_config(write_project(self.tmp))
        self.assertEqual(config.title, "Architecture de la plateforme Exemple")
        self.assertEqual(config.pdf_name, "architecture-exemple.pdf")
        self.assertEqual(
            config.flow_prefixes,
            (("P", "Production"), ("Q", "Préproduction"), ("X", "Hors environnement")),
        )
        self.assertIs(config.known_issues, True)
        self.assertEqual(config.owners["agence"], "Gingerminds")
        self.assertEqual(config.tool_version, "1.0.0")

    def test_defaults(self):
        data = {key: CONFIG[key] for key in ("title", "pdf", "flow_prefixes", "tool_version")}
        config = load_config(write_project(self.tmp, data))
        self.assertEqual(config.owners, {})
        self.assertIs(config.known_issues, True)
        self.assertEqual(config.repositories, ())

    def test_flow_id_re_follows_prefixes(self):
        data = self.config(flow_prefixes={"P": "Production", "X": "Hors environnement"})
        flow_id_re = load_config(write_project(self.tmp, data)).flow_id_re
        self.assertTrue(flow_id_re.fullmatch("P01"))
        self.assertTrue(flow_id_re.search("flux X12 · 443/tcp"))
        self.assertEqual(flow_id_re.findall("consultation (P01, X02) ; voir P01."), ["P01", "X02", "P01"])
        for rejected in ("Q01", "P1", "P001", "srv-P01", "P01-backup", "web.P01"):
            with self.subTest(rejected=rejected):
                self.assertIsNone(flow_id_re.search(rejected))

    def test_paths(self):
        root = write_project(self.tmp)
        config = load_config(root)
        self.assertEqual(config.root, root)
        self.assertEqual(config.views_dir, root / "views")
        self.assertEqual(config.readme, root / "README.md")
        self.assertEqual(config.css, root / "tools" / "dossier.css")

    def test_pdf_paths(self):
        root = write_project(self.tmp)
        config = load_config(root)
        self.assertEqual(config.pdf_path(True), root / "build" / "architecture-exemple.pdf")
        self.assertEqual(
            config.pdf_path(False), root / "build" / "architecture-exemple-sans-points-attention.pdf"
        )

    def test_errors(self):
        cases = [
            ("title", self.config(title=" "), "architecture.json : « title » manquant"),
            ("tool_version", {k: v for k, v in CONFIG.items() if k != "tool_version"},
             "architecture.json : « tool_version » manquant"),
            ("pdf parent", self.config(pdf="../x.pdf"), "architecture.json : « pdf » doit être un nom de fichier en .pdf"),
            ("pdf suffix", self.config(pdf="x.txt"), "architecture.json : « pdf » doit être un nom de fichier en .pdf"),
            ("prefixes empty", self.config(flow_prefixes={}),
             "architecture.json : « flow_prefixes » attend des lettres majuscules uniques"),
            ("prefix PP", self.config(flow_prefixes={"PP": "Production"}),
             "architecture.json : « flow_prefixes » attend des lettres majuscules uniques"),
            ("prefix p", self.config(flow_prefixes={"p": "Production"}),
             "architecture.json : « flow_prefixes » attend des lettres majuscules uniques"),
            ("owner role", self.config(owners={"hoster": "Hébergeur"}),
             "architecture.json : rôle inconnu « hoster » dans « owners »"),
        ]
        for name, data, message in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ConfigError) as raised:
                    load_config(write_project(Path(tmp), data))
                self.assertEqual(str(raised.exception), message)

    def test_missing_file(self):
        root = self.tmp / "docs" / "architecture"
        with self.assertRaises(ConfigError) as raised:
            load_config(root)
        self.assertEqual(str(raised.exception), f"architecture.json introuvable dans {root / 'tools'}")

    def test_invalid_json(self):
        root = write_project(self.tmp)
        (root / "tools" / "architecture.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError) as raised:
            load_config(root)
        self.assertTrue(str(raised.exception).startswith("architecture.json : JSON invalide"))


if __name__ == "__main__":
    unittest.main()
