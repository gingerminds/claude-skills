import json
import os
import tempfile
import unittest
from pathlib import Path

from cqa.version import plugin_version

PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


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
