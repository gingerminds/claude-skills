import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from technical_architecture.architecture import check, extract_diagram, load_config, view_text

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "skills" / "technical-architecture" / "assets" / "example"
KIT_DIR = ROOT / "scripts" / "technical_architecture"
GENERATORS = {
    "view01.py": "01-vue-ensemble.drawio.svg",
    "view02.py": "02-infra-prod.drawio.svg",
    "view03.py": "03-infra-preprod.drawio.svg",
}


def load_generator(name):
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location(f"example_{name[:-3]}", EXAMPLE / "generators" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExampleTest(unittest.TestCase):
    def test_example_passes_check(self):
        self.assertEqual(check(load_config(EXAMPLE)), [])

    def test_example_covers_every_prefix(self):
        config = load_config(EXAMPLE)
        ids = {flow_id for flow_id in config.flow_id_re.findall(config.readme.read_text(encoding="utf-8"))}
        self.assertEqual(len([i for i in ids if i.startswith("P")]), 8)
        self.assertEqual(len([i for i in ids if i.startswith("Q")]), 4)
        self.assertEqual(len([i for i in ids if i.startswith("X")]), 3)

    def test_generators_match_committed_views(self):
        env = dict(os.environ, PYTHONPATH=str(KIT_DIR))
        for generator, view in GENERATORS.items():
            with self.subTest(generator), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "vue.drawio"
                subprocess.run(
                    [sys.executable, str(EXAMPLE / "generators" / generator), str(out)], env=env, check=True
                )
                self.assertEqual(
                    view_text(out.read_text(encoding="utf-8")),
                    view_text(extract_diagram(EXAMPLE / "views" / view)),
                )

    def test_example_has_no_overlap(self):
        for generator in GENERATORS:
            with self.subTest(generator):
                self.assertEqual(load_generator(generator).build().overlaps(), [])


if __name__ == "__main__":
    unittest.main()
