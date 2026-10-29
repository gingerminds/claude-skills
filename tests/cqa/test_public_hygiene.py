"""Hygiene of the public repository: no IP, Notion id or secret leaks, aligned versions, Python 3.9."""
import ast
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from cqa.secrets import scan_tree

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
EXPECTED_VERSION = "0.14.0"
NEW_SKILL = "/gm:checklist-quality-audit"

SCOPE = [
    "skills/checklist-quality-audit",
    "skills/cqa",
    "agents",
    "scripts/cqa",
    "scripts/cqa_fix.py",
    "scripts/cqa_notion.py",
    "scripts/cqa_plan.py",
    "scripts/cqa_ssh_ro.py",
    "scripts/cqa-toolbox.sh",
    "scripts/cqa-keepawake.sh",
    "tests/cqa",
    "README.md",
]

# R9: content files only. Tests (deliberately fake secrets) and code (type annotations such
# as "known_secrets: list[str]") would only produce false positives.
CONTENT_SCOPE = [
    "skills/checklist-quality-audit",
    "skills/cqa",
    "agents/cqa-auditor.md",
    "agents/cqa-browser.md",
    "agents/cqa-fixer.md",
    "agents/cqa-fix-reviewer.md",
    "README.md",
]

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
NOTION_ID = re.compile(rf"(?<![0-9A-Za-z])(?:[0-9a-fA-F]{{32}}|{UUID})(?![0-9A-Za-z])")

ALLOWED_IPS = {
    "127.0.0.1",  # loopback, local test servers
    "127.0.0.2",  # loopback range check in test_cli_prepare
}

# R15: obviously fake ids used by the tests and fixtures, compared lowercase without dashes.
ALLOWED_NOTION_IDS = {
    "0123456789abcdef0123456789abcdef",  # ID / DASHED_ID in test_notion_api, copy page and data source elsewhere
    "fedcba9876543210fedcba9876543210",  # VIEW_ID in test_notion_api, reference data source in test_cli_prepare
    "00112233445566778899aabbccddeeff",  # bot_id in test_cli_prepare
}


def scope_files(entries):
    """Yield every file of the given repository paths, ignoring bytecode caches."""
    for entry in entries:
        path = ROOT / entry
        if path.is_file():
            yield path
            continue
        for directory, names, files in os.walk(path, onerror=_raise):
            names[:] = [name for name in names if name != "__pycache__"]
            for name in sorted(files):
                if not name.endswith(".pyc"):
                    yield Path(directory, name)


def read(path):
    return path.read_bytes().decode("utf-8", errors="replace")


def find_ips(text):
    return sorted(set(IPV4.findall(text)) - ALLOWED_IPS)


def find_notion_ids(text):
    found = {match.group(0).replace("-", "").lower() for match in NOTION_ID.finditer(text)}
    return sorted(found - ALLOWED_NOTION_IDS)


def _raise(error):
    raise error


class DetectorsTest(unittest.TestCase):
    def test_ipv4_is_found_and_loopback_is_allowed(self):
        address = ".".join(["10", "0", "0", "1"])
        self.assertEqual(find_ips(f"ssh {address} and 127.0.0.1"), [address])

    def test_notion_ids_are_found_in_both_forms(self):
        flat = "a1" * 16
        dashed = "-".join(["abcdef01", "2345", "6789", "abcd", "ef0123456789"])
        text = f"https://www.notion.so/Titre-{flat} et {dashed}"
        self.assertEqual(find_notion_ids(text), [flat, dashed.replace("-", "")])

    def test_notion_id_comparison_ignores_dashes_and_case(self):
        dashed = "01234567-89AB-CDEF-0123-456789ABCDEF"
        self.assertEqual(find_notion_ids(dashed), [])

    def test_longer_hex_runs_are_not_notion_ids(self):
        self.assertEqual(find_notion_ids("commit " + "e" * 40), [])

    def test_python39_check_rejects_newer_syntax(self):
        with self.assertRaises(SyntaxError):
            ast.parse("match x:\n    case 1:\n        pass\n", feature_version=(3, 9))


class PublicHygieneTest(unittest.TestCase):
    def test_scope_is_not_empty(self):
        names = {path.relative_to(ROOT).as_posix() for path in scope_files(SCOPE)}
        for expected in (
            "skills/checklist-quality-audit/SKILL.md",
            "skills/cqa/SKILL.md",
            "agents/cqa-auditor.md",
            "scripts/cqa/secrets.py",
            "scripts/cqa_notion.py",
            "scripts/cqa-toolbox.sh",
            "tests/cqa/test_public_hygiene.py",
            "README.md",
        ):
            self.assertIn(expected, names)

    def test_content_scope_covers_every_agent(self):
        covered = {path.relative_to(ROOT).as_posix() for path in scope_files(CONTENT_SCOPE)}
        for path in sorted((ROOT / "agents").glob("*.md")):
            with self.subTest(agent=path.name):
                self.assertIn(path.relative_to(ROOT).as_posix(), covered)

    def test_no_ipv4(self):
        leaks = [
            (path.relative_to(ROOT).as_posix(), address)
            for path in scope_files(SCOPE)
            for address in find_ips(read(path))
        ]
        self.assertEqual(leaks, [])

    def test_no_notion_ids(self):
        leaks = [
            (path.relative_to(ROOT).as_posix(), identifier)
            for path in scope_files(SCOPE)
            for identifier in find_notion_ids(read(path))
        ]
        self.assertEqual(leaks, [])

    def test_no_screaming_frog_licence_left(self):
        needles = ("GM_" + "SF_", "sf_" + "licence", "Scream" + "ing")
        leaks = [
            path.relative_to(ROOT).as_posix()
            for path in scope_files(SCOPE)
            if any(needle in read(path) for needle in needles)
        ]
        self.assertEqual(leaks, [])

    def test_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in scope_files(CONTENT_SCOPE):
                target = Path(tmp, path.relative_to(ROOT))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            self.assertEqual(scan_tree(Path(tmp), []), [])

    def test_versions_aligned(self):
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], EXPECTED_VERSION)
        self.assertEqual(marketplace["plugins"][0]["version"], EXPECTED_VERSION)

    def test_readme_lists_every_script(self):
        readme = read(ROOT / "README.md")
        for path in sorted(ROOT.glob("scripts/*")):
            if path.is_file():
                with self.subTest(script=path.name):
                    self.assertIn(path.name, readme)

    def test_readme_lists_every_agent(self):
        readme = read(ROOT / "README.md")
        for path in sorted(ROOT.glob("agents/*.md")):
            with self.subTest(agent=path.name):
                self.assertIn(path.name, readme)

    def test_marketplace_lists_the_skill(self):
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
        self.assertIn(NEW_SKILL, marketplace["plugins"][0]["description"])

    def test_python39_syntax(self):
        sources = sorted(ROOT.glob("scripts/cqa_*.py")) + sorted(ROOT.glob("scripts/cqa/*.py"))
        self.assertGreaterEqual(len(sources), 10)
        for path in sources:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                ast.parse(read(path), filename=str(path), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
