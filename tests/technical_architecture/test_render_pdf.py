import contextlib
import copy
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from technical_architecture import architecture
from technical_architecture.architecture import (
    build_dossier_markdown,
    extract_diagram,
    load_config,
    main,
    view_text,
)
from tests.technical_architecture.helpers import CONFIG, flow_row, mxfile, readme, write_project, write_view

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "scripts" / "technical_architecture" / "dossier.css"
LABELS = ("Légende", "Vue\nÉtat au 26/11/2026", "P01 · 443/tcp")
INDEX = (
    "| [01 · Vue](views/01-vue.drawio.svg) | Question ? |\n"
    "| [02 · Production](views/02-prod.drawio.svg) | Comment la production est-elle hébergée ? |"
)


def fake_run(command, **kwargs):
    """Stand-in for docker run: writes the file each tool would produce in the mounted folder."""
    mount = command[command.index("-v") + 1]
    workdir = Path(mount.rsplit(":/data", 1)[0])
    if "--output" in command:
        (workdir / command[command.index("--output") + 1]).write_text("produit", encoding="utf-8")
    if "--print-to-pdf=/data/dossier.pdf" in command:
        (workdir / "dossier.pdf").write_bytes(b"%PDF-1.4")
    return subprocess.CompletedProcess(command, 0)


class RenderPdfTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.run_patch = mock.patch.object(architecture.subprocess, "run", side_effect=fake_run)
        self.run = self.run_patch.start()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self._out = contextlib.redirect_stdout(self.stdout)
        self._err = contextlib.redirect_stderr(self.stderr)
        self._out.__enter__()
        self._err.__enter__()

    def tearDown(self):
        self._err.__exit__(None, None, None)
        self._out.__exit__(None, None, None)
        self.run_patch.stop()
        self._tmp.cleanup()

    def project(self, config=None, known_issues=True):
        text = readme([flow_row("P01")], known_issues).replace(
            "| [01 · Vue](views/01-vue.drawio.svg) | Question ? |", INDEX
        )
        text = text.replace("Couleurs par responsable.", "Couleurs par responsable, voir [exploitation](../EXPLOITATION.md).")
        root = write_project(self.tmp, config, text)
        write_view(root / "views" / "01-vue.drawio.svg", mxfile(*LABELS))
        write_view(root / "views" / "02-prod.drawio.svg", mxfile("Légende", "État au 26/11/2026", "Production"))
        shutil.copyfile(CSS, root / "tools" / "dossier.css")
        return root

    def commands(self):
        return [call.args[0] for call in self.run.call_args_list]

    def test_render_command(self):
        source = self.tmp / "source.drawio"
        source.write_text(mxfile(*LABELS), encoding="utf-8")
        architecture.render(source, self.tmp / "vue.drawio.svg", self.tmp / "vue.png")
        svg, png = self.commands()
        self.assertIn(architecture.DRAWIO_IMAGE, svg)
        self.assertIn("--shm-size=1g", svg)
        for expected in ("--export", "--embed-diagram", "--embed-svg-images"):
            self.assertIn(expected, svg)
        self.assertEqual(svg[svg.index("--format") + 1], "svg")
        self.assertEqual(svg[svg.index("--theme") + 1], "light")
        self.assertEqual(png[png.index("--format") + 1], "png")
        self.assertEqual(png[png.index("--scale") + 1], "2")
        self.assertTrue((self.tmp / "vue.drawio.svg").is_file())
        self.assertTrue((self.tmp / "vue.png").is_file())

    def test_docker_mount_keeps_spaces(self):
        architecture._docker("image", Path("/tmp/a b/c"), ("arg",), "échec")
        self.assertIn("/tmp/a b/c:/data", self.commands()[0])

    def test_docker_failure_exits_in_french(self):
        self.run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(command, 1)
        with self.assertRaises(SystemExit) as raised:
            architecture._docker("image", self.tmp, ("arg",), "l'export draw.io a échoué")
        self.assertEqual(str(raised.exception.code), "ERREUR : l'export draw.io a échoué")

    def test_docker_missing_exits_in_french(self):
        self.run.side_effect = FileNotFoundError(2, "No such file or directory", "docker")
        with self.assertRaises(SystemExit) as raised:
            architecture._docker("image", self.tmp, ("arg",), "l'export draw.io a échoué")
        self.assertEqual(str(raised.exception.code), "ERREUR : docker introuvable : installer Docker et le lancer")

    def test_extract_missing_file(self):
        out = self.tmp / "vue.drawio"
        self.assertEqual(main(["extract", str(self.tmp / "absente.drawio.svg"), str(out)]), 1)
        self.assertIn("ERREUR : absente.drawio.svg : introuvable", self.stderr.getvalue())
        self.assertFalse(out.exists())

    def test_refresh_refuses_before_rendering(self):
        root = self.project()
        (root / "views" / "02-prod.drawio.svg").write_text("<svg/>", encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            architecture.refresh(root / "views")
        self.assertIn("02-prod.drawio.svg", str(raised.exception.code))
        self.run.assert_not_called()

    def test_refresh_renders_every_view(self):
        root = self.project()
        architecture.refresh(root / "views")
        self.assertEqual(len(self.commands()), 2)

    def test_dossier_markdown(self):
        markdown = build_dossier_markdown(load_config(self.project()), True)
        self.assertTrue(markdown.startswith("::: {.titre}\n# Architecture de la plateforme Exemple\n\nDossier généré le "))
        first = markdown.index("## 01 · Vue\n\n*Question ?*\n\n![01 · Vue](views/01-vue.drawio.svg)")
        second = markdown.index("## 02 · Production\n\n*Comment la production est-elle hébergée ?*")
        self.assertLess(first, second)
        self.assertEqual(markdown.count("::: {.vue}"), 2)
        self.assertIn("| P01 | Internet | Proxy | 443/tcp | oui | aucune | Site public | Agence |\n", markdown)
        self.assertNotIn("Statut", markdown)
        self.assertNotIn(".md", markdown)
        self.assertIn("## Points d'attention connus", markdown)

    def test_dossier_without_known_issues(self):
        config = load_config(self.project())
        self.assertNotIn("Points d'attention", build_dossier_markdown(config, False))
        data = copy.deepcopy(CONFIG)
        data["known_issues"] = False
        config = load_config(self.project(data))
        self.assertNotIn("Points d'attention", build_dossier_markdown(config, True))

    def test_pdf_refused_when_check_fails(self):
        root = self.project()
        for view in (root / "views").iterdir():
            view.unlink()
        self.assertEqual(main(["--racine", str(root), "pdf"]), 1)
        self.run.assert_not_called()
        self.assertIn("ERREUR : views : aucune vue (*.drawio.svg)", self.stderr.getvalue())
        self.assertFalse((root / "build").exists())

    def test_pdf_commands_and_default_path(self):
        root = self.project()
        self.assertEqual(main(["--racine", str(root), "pdf"]), 0)
        pandoc, chrome = self.commands()
        self.assertIn(architecture.PANDOC_IMAGE, pandoc)
        self.assertIn("markdown-implicit_figures", pandoc)
        self.assertEqual(pandoc[pandoc.index("--columns") + 1], "1000")
        self.assertIn(architecture.CHROME_IMAGE, chrome)
        self.assertIn("--print-to-pdf=/data/dossier.pdf", chrome)
        full = root / "build" / "architecture-exemple.pdf"
        self.assertEqual(full.read_bytes(), b"%PDF-1.4")
        self.assertIn(f"Dossier PDF : {full}", self.stdout.getvalue())

        self.assertEqual(main(["--racine", str(root), "pdf", "--sans-points-attention"]), 0)
        self.assertTrue((root / "build" / "architecture-exemple-sans-points-attention.pdf").is_file())
        self.assertEqual(sorted(path.name for path in root.iterdir()), ["README.md", "build", "tools", "views"])

    def test_pdf_custom_output(self):
        root = self.project()
        out = self.tmp / "sortie" / "dossier.pdf"
        self.assertEqual(main(["--racine", str(root), "pdf", "--sortie", str(out)]), 0)
        self.assertTrue(out.is_file())
        self.assertFalse((root / "build").exists())

    def test_pdf_without_css(self):
        root = self.project()
        (root / "tools" / "dossier.css").unlink()
        self.assertEqual(main(["--racine", str(root), "pdf"]), 2)
        self.assertIn("ERREUR : dossier.css introuvable", self.stderr.getvalue())
        self.run.assert_not_called()

    def test_check_command(self):
        root = self.project()
        write_view(root / "views" / "02-prod.drawio.svg", mxfile("Légende", "État au 26/11/2026"))
        self.assertEqual(main(["--racine", str(root), "check"]), 0)
        self.assertIn("Schémas vérifiés : 2 vue(s), 1 flux.", self.stdout.getvalue())

    def test_check_command_reports_errors(self):
        root = self.project()
        (root / "README.md").unlink()
        self.assertEqual(main(["--racine", str(root), "check"]), 1)
        self.assertIn("ERREUR : README.md : introuvable", self.stderr.getvalue())

    def test_version_command(self):
        self.assertEqual(main(["version"]), 0)
        self.assertEqual(self.stdout.getvalue().strip(), "1.0.0")

    def test_config_error_exit_code(self):
        self.assertEqual(main(["--racine", str(self.tmp), "check"]), 2)
        self.assertTrue(self.stderr.getvalue().startswith("ERREUR : architecture.json introuvable"))

    def test_extract_command(self):
        root = self.project()
        out = self.tmp / "vue.drawio"
        self.assertEqual(main(["extract", str(root / "views" / "01-vue.drawio.svg"), str(out)]), 0)
        self.assertEqual(view_text(out.read_text(encoding="utf-8")), view_text(mxfile(*LABELS)))
        self.assertEqual(main(["extract", str(root / "README.md"), str(out)]), 1)
        self.assertIn("README.md : aucun diagramme embarqué", self.stderr.getvalue())


@unittest.skipUnless(
    os.environ.get("GM_TA_DOCKER") == "1" and shutil.which("docker"), "GM_TA_DOCKER=1 and docker required"
)
class RealDockerTest(unittest.TestCase):
    def test_real_docker_render(self):
        with tempfile.TemporaryDirectory(prefix="gm ta ") as tmp:
            source = Path(tmp) / "source.drawio"
            source.write_text(mxfile(*LABELS), encoding="utf-8")
            view = Path(tmp) / "vue.drawio.svg"
            with contextlib.redirect_stdout(io.StringIO()):
                architecture.render(source, view, Path(tmp) / "vue.png")
            self.assertEqual(view_text(extract_diagram(view)), view_text(mxfile(*LABELS)))
            self.assertGreater((Path(tmp) / "vue.png").stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
