"""Contracts of the Markdown instructions of the audit: sub-agents and verdict reference."""
import json
import re
import unittest
from pathlib import Path

from cqa.findings import VERDICTS, apply_guardrails, validate_document
from cqa.plan import STATUS_FOR_VERDICT

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "agents" / "cqa-auditor.md"
BROWSER = ROOT / "agents" / "cqa-browser.md"
VERDICTS_MD = ROOT / "skills" / "checklist-quality-audit" / "references" / "verdicts.md"
SCHEMA = "skills/checklist-quality-audit/schemas/finding.schema.json"

CHROME_TOOLS = {
    f"mcp__claude-in-chrome__{name}"
    for name in (
        "tabs_context_mcp",
        "tabs_create_mcp",
        "tabs_close_mcp",
        "navigate",
        "read_page",
        "find",
        "get_page_text",
        "javascript_tool",
        "computer",
        "resize_window",
        "read_console_messages",
    )
}
EM_DASH = chr(0x2014)

_PLUGIN_PATH = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s`\"'<>)]+)")
_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_TOOL_QUERY = re.compile(r"`select:([^`]+)`")
_CURL = re.compile(r"\bcurl [^`\n]*")
_SQL_QUERY = re.compile(r"--sql \S+ [^\n\"]*\"([^\"\n]+)\"")
_PROBES_CAP = "A finding that lists probes is never `ok`, `ok_human` or `na`"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(path: Path) -> dict:
    lines = _text(path).splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise AssertionError(f"{path.name} : frontmatter absent")
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _tools(path: Path) -> list:
    return [tool.strip() for tool in _frontmatter(path)["tools"].split(",")]


def _example_findings() -> list:
    blocks = [json.loads(block) for block in _JSON_BLOCK.findall(_text(VERDICTS_MD))]
    return [block for block in blocks if isinstance(block, dict) and "verdict" in block]


class AgentsTest(unittest.TestCase):
    def test_agents_frontmatter(self):
        for path, name in ((AUDITOR, "cqa-auditor"), (BROWSER, "cqa-browser")):
            with self.subTest(agent=name):
                fields = _frontmatter(path)
                self.assertEqual(fields.get("name"), name)
                self.assertTrue(fields.get("description"))
                self.assertTrue(fields.get("tools"))
        self.assertEqual(_tools(AUDITOR), ["Read", "Grep", "Glob", "Bash", "Write"])
        self.assertEqual(set(_tools(BROWSER)), {"Read", "Write", "ToolSearch"} | CHROME_TOOLS)

    def test_agents_mention_contract(self):
        for path in (AUDITOR, BROWSER):
            text = _text(path)
            for needle in ("findings/", "finding.schema.json", "needs_probes", "divergence"):
                with self.subTest(agent=path.name, needle=needle):
                    self.assertIn(needle, text)

    def test_followup_writes_a_complete_lot_document(self):
        for path in (AUDITOR, BROWSER):
            with self.subTest(agent=path.name):
                self.assertIn("findings/<lot>-followup.json", _text(path))
                self.assertIn("one finding for every row of the lot", _text(path))

    def test_auditor_runs_server_commands_through_the_guard(self):
        text = _text(AUDITOR)
        self.assertIn("scripts/cqa_ssh_ro.py", text)
        self.assertIn("exit code 4", text)

    def test_auditor_never_starts_the_runner(self):
        text = _text(AUDITOR)
        self.assertIn("Never start the project runner", text)
        self.assertIn("read the target in the Makefile", text)

    def test_auditor_curl_ignores_curlrc(self):
        commands = _CURL.findall(_text(AUDITOR))
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(command.startswith("curl -q "))

    def test_auditor_sql_examples_have_a_limit(self):
        queries = _SQL_QUERY.findall(_text(AUDITOR))
        self.assertTrue(queries)
        for query in queries:
            with self.subTest(query=query):
                self.assertRegex(query, r"\bLIMIT \d+$")

    def test_first_round_probes_cap_the_verdict(self):
        for path in (AUDITOR, BROWSER, VERDICTS_MD):
            with self.subTest(file=path.name):
                self.assertIn(_PROBES_CAP, _text(path))

    def test_browser_forbids_forms(self):
        text = _text(BROWSER)
        self.assertIn("Never submit a form", text)
        self.assertIn("Never type a password", text)

    def test_browser_never_changes_state(self):
        text = _text(BROWSER)
        self.assertIn("Never click an element or open a URL that changes state", text)
        self.assertIn("Never interact with a cookie or consent banner", text)
        self.assertIn("Close only the tabs you created", text)

    def test_browser_loads_every_chrome_tool(self):
        queries = _TOOL_QUERY.findall(_text(BROWSER))
        self.assertEqual(len(queries), 1)
        loaded = set(queries[0].split(","))
        self.assertEqual(loaded, {tool for tool in _tools(BROWSER) if tool in CHROME_TOOLS})
        self.assertEqual(loaded, CHROME_TOOLS)

    def test_cited_plugin_paths_exist(self):
        for path in (AUDITOR, BROWSER, VERDICTS_MD):
            for cited in _PLUGIN_PATH.findall(_text(path)):
                with self.subTest(file=path.name, cited=cited):
                    self.assertTrue((ROOT / cited.rstrip(".,:;")).exists())

    def test_no_em_dash(self):
        for path in (AUDITOR, BROWSER, VERDICTS_MD):
            with self.subTest(file=path.name):
                self.assertNotIn(EM_DASH, _text(path))


class VerdictsReferenceTest(unittest.TestCase):
    def test_cites_the_schema(self):
        self.assertIn(SCHEMA, _text(VERDICTS_MD))
        self.assertTrue((ROOT / SCHEMA).is_file())

    def test_maps_every_verdict_to_its_status(self):
        text = _text(VERDICTS_MD)
        for verdict, status in STATUS_FOR_VERDICT.items():
            with self.subTest(verdict=verdict):
                shown = status if status is not None else "unchanged"
                self.assertRegex(text, rf"(?m)^\| `{verdict}` \| {re.escape(shown)} \|")

    def test_one_valid_example_per_verdict(self):
        examples = _example_findings()
        self.assertEqual(sorted(item["verdict"] for item in examples), sorted(VERDICTS))
        for item in examples:
            with self.subTest(verdict=item["verdict"]):
                valid, errors = validate_document({"lot": "x", "findings": [item]}, {item["ref"]})
                self.assertEqual(errors, [])
                self.assertEqual(len(valid), 1)
                kept = apply_guardrails(item, {"tools": ["Code"]}, followup_done=False)
                self.assertEqual(kept["verdict"], item["verdict"])
                self.assertIsNone(kept["guardrail"])


if __name__ == "__main__":
    unittest.main()
