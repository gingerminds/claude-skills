import json
import os
import tempfile
import unittest
from pathlib import Path

from cqa.version import parse, plugin_version

PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


class ParseTest(unittest.TestCase):
    def test_parse_reads_dotted_integers(self):
        self.assertEqual(parse("0.18.1"), (0, 18, 1))
        self.assertEqual(parse("0.9.0"), (0, 9, 0))
        self.assertEqual(parse("1.0"), (1, 0))
        self.assertEqual(parse(plugin_version()), tuple(int(part) for part in plugin_version().split(".")))

    def test_versions_compare_as_integers_not_as_text(self):
        self.assertLess(parse("0.9.0"), parse("0.18.1"))
        self.assertLess(parse("0.18.0"), parse("0.18.1"))
        self.assertLess(parse("0.18"), parse("0.18.1"))
        self.assertGreater(parse("0.19.0"), parse("0.18.1"))

    def test_parse_refuses_what_is_not_a_version(self):
        for value in (None, "", "v0.18.1", "0.18.1-rc1", " 0.18.1", "0..1", "0.18.", 18, ["0"]):
            with self.subTest(value=value):
                self.assertIsNone(parse(value))


class VersionTest(unittest.TestCase):
    def test_matches_plugin_json(self):
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertEqual(plugin_version(), data["version"])

    def test_is_semver(self):
        self.assertRegex(plugin_version(), r"^\d+\.\d+\.\d+$")

    def test_independent_of_working_directory(self):
        expected = plugin_version()
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                self.assertEqual(plugin_version(), expected)
            finally:
                os.chdir(previous)
