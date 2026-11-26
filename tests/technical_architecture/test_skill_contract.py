import hashlib
import re
import unittest
from pathlib import Path

from technical_architecture.architecture import ANNEX_SECTIONS, MATRIX_HEADING

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "technical-architecture"
SKILL = SKILL_DIR / "SKILL.md"
TOOL_DIR = ROOT / "scripts" / "technical_architecture"
MATRIX_HEADER = "| # | Source | Destination | Port / protocole | Chiffré | Authentification | Finalité | Géré par | Statut |"
STATUS_LINE = "Statuts : `constaté (JJ/MM/AAAA)`, `décidé`, `indiqué par <qui> (JJ/MM/AAAA)`, `à confirmer`."


def frontmatter(text):
    block = text.split("---", 2)[1]
    fields = {}
    for line in block.strip().splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def owned_files():
    for folder in (SKILL_DIR, TOOL_DIR):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path


# SHA-256 of the client and host names that must never appear in this public repository, lowercase,
# words joined by one space or glued: the names themselves stay out of the repository.
FORBIDDEN_DIGESTS = frozenset({
    "11c5e21c22bc90470f85a8a27e8bbac0c1703056debf206cbb1d3e9dfd4989ac",
    "f2bd90f93b888da02c54980f11ae4796dfcc98f447cd3fe326f5a3a964c939bf",
    "38de90475bb334fb3dea5d54f250500aba60fe2c6158115d342b06bcb46e39bf",
})


def forbidden_names(text, digests=FORBIDDEN_DIGESTS):
    """Words and pairs of words of text whose SHA-256 is in digests."""
    words = re.findall(r"[^\W_]+", text.lower())
    candidates = set(words)
    candidates.update(f"{first} {second}" for first, second in zip(words, words[1:]))
    candidates.update(first + second for first, second in zip(words, words[1:]))
    return sorted(c for c in candidates if hashlib.sha256(c.encode("utf-8")).hexdigest() in digests)


class SkillContractTest(unittest.TestCase):
    def test_frontmatter(self):
        fields = frontmatter(SKILL.read_text(encoding="utf-8"))
        self.assertEqual(fields["name"], "technical-architecture")
        self.assertIn("draw.io", fields["description"])
        self.assertIn("/gm:technical-architecture", fields["description"])
        self.assertIn("mise-a-jour", fields["argument-hint"])
        self.assertIn("pdf", fields["argument-hint"])

    def test_every_reference_and_asset_is_linked(self):
        text = SKILL.read_text(encoding="utf-8")
        for path in sorted((SKILL_DIR / "references").iterdir()) + sorted((SKILL_DIR / "assets").iterdir()):
            if path.is_file():
                with self.subTest(path=path.name):
                    self.assertIn(path.relative_to(SKILL_DIR).as_posix(), text)
        self.assertIn("assets/example/", text)
        for named in sorted(set(re.findall(r"(?:references|assets)/[\w./-]+", text))):
            with self.subTest(named=named):
                self.assertTrue((SKILL_DIR / named.rstrip("/.")).exists(), named)

    def test_readme_template_matches_tool(self):
        text = (SKILL_DIR / "assets" / "README.template.md").read_text(encoding="utf-8")
        self.assertIn(MATRIX_HEADING + "\n", text)
        for title in ANNEX_SECTIONS:
            self.assertIn(f"\n## {title}\n", text)
        self.assertIn(STATUS_LINE, text)
        self.assertIn(MATRIX_HEADER, text)
        self.assertIn("\n## Vues\n", text)
        self.assertIn("\n## Modifier un schéma\n", text)
        self.assertIn("make architecture-pdf", text)
        self.assertNotIn(".pdf)", text)

    def test_claude_md_block_markers(self):
        text = (SKILL_DIR / "assets" / "claude-md-block.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- gm:technical-architecture -->"))
        self.assertTrue(text.rstrip().endswith("<!-- /gm:technical-architecture -->"))
        self.assertIn("make diagrams", text)
        self.assertNotIn("architecture-pdf", text)
        self.assertIn("signaler, puis demander", text)

    def test_makefile_snippet(self):
        text = (SKILL_DIR / "assets" / "makefile-snippet.mk").read_text(encoding="utf-8")
        self.assertIn("ARCHITECTURE_TOOL := python3 docs/architecture/tools/architecture.py", text)
        self.assertRegex(text, r"(?m)^diagrams:.*##")
        self.assertRegex(text, r"(?m)^architecture-pdf:.*##")
        self.assertIn("$(if $(SANS_POINTS_ATTENTION),--sans-points-attention)", text)
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if re.match(r"^(diagrams|architecture-pdf):", line):
                recipe = lines[index + 1]
                self.assertTrue(recipe.startswith("\t"), repr(recipe))

    def test_collection_forbids(self):
        text = (SKILL_DIR / "references" / "collection.md").read_text(encoding="utf-8")
        for needle in ("docker compose config", "docker inspect", ".env", "sudo", "ss -tlnp", "sshd -T"):
            self.assertIn(needle, text)

    def test_question_catalogue(self):
        text = (SKILL_DIR / "references" / "questions.md").read_text(encoding="utf-8")
        for question in (
            "Qui utilise la plateforme, et avec quels systèmes échange-t-elle ?",
            "Comment la production est-elle hébergée, et comment les flux y circulent-ils ?",
            "En quoi la préproduction diffère-t-elle de la production ?",
            "Qui peut agir sur la plateforme, et par où ?",
            "Quelles données entrent, quand, et où vont-elles ?",
        ):
            self.assertIn(question, text)

    def test_style_guide_names_the_kit(self):
        text = (SKILL_DIR / "references" / "drawio-style.md").read_text(encoding="utf-8")
        for needle in ("ROLES", "NATURES", "ICONS", "overlaps()", "render", "--png", "extract"):
            self.assertIn(needle, text)

    def test_update_checks_the_tool_version(self):
        text = (SKILL_DIR / "references" / "update.md").read_text(encoding="utf-8")
        self.assertIn("architecture.py version", text)
        self.assertIn("git log --since", text)
        self.assertIn("jamais réattribué", text)

    def test_forbidden_names_detector(self):
        digests = {hashlib.sha256(name.encode("utf-8")).hexdigest() for name in ("acme corp", "acmecorp", "globex")}
        text = "Projet ACME Corp chez Globex, acme-corp et AcmeCorp."
        self.assertEqual(forbidden_names(text, digests), ["acme corp", "acmecorp", "globex"])
        self.assertEqual(forbidden_names("Acme seul, corporate, globexien", digests), [])

    def test_no_em_dash_no_client_names(self):
        for path in owned_files():
            text = path.read_bytes().decode("utf-8", errors="replace")
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertNotIn("—", text)
                self.assertEqual(forbidden_names(text), [])

    def test_no_other_skill_mentioned(self):
        for path in owned_files():
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertNotIn("archi-c4", path.read_bytes().decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
