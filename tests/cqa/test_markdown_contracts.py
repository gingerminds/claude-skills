"""Contracts of the Markdown instructions of the audit: skill, references, sub-agents."""
import argparse
import json
import re
import unittest
from pathlib import Path

import cqa_notion
import cqa_plan
import cqa_ssh_ro
from cqa.findings import VERDICTS, apply_guardrails, validate_document
from cqa.plan import STATUS_FOR_VERDICT

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "agents" / "cqa-auditor.md"
BROWSER = ROOT / "agents" / "cqa-browser.md"
SKILL_DIR = ROOT / "skills" / "checklist-quality-audit"
SKILL = SKILL_DIR / "SKILL.md"
ALIAS_DIR = ROOT / "skills" / "cqa"
ALIAS = ALIAS_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"
VERDICTS_MD = REFERENCES / "verdicts.md"
TOOLBOX = ROOT / "scripts" / "cqa-toolbox.sh"
SCHEMA = "skills/checklist-quality-audit/schemas/finding.schema.json"
EXPECTED_REFERENCES = {
    "context.md",
    "diagnostic.md",
    "lots.md",
    "verdicts.md",
    "browser.md",
    "server.md",
    "assign.md",
}
ARGUMENT_HINT = '"[diagnostic | assign] [--phase \\"MEL J0\\"] [--dry-run]"'
MAX_DESCRIPTION = 1536
SCRIPTS_LINE = 'S="${CLAUDE_SKILL_DIR}/../../scripts"'

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
_CLI_CALL = re.compile(
    r"\b(cqa_notion\.py|cqa_plan\.py|cqa-toolbox\.sh)[\"']?[ \t]+([a-z][a-z-]*)([^\n`]*)"
)
_GUARD_CALL = re.compile(r"\bcqa_ssh_ro\.py[\"']?([^\n`]*)")
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
_SHELL_END = re.compile(r" -- |&&|\|\||;|\s[|>]\s")
_REFERENCE = re.compile(r"references/([A-Za-z0-9_-]+\.md)")
_ALIAS_TARGET = re.compile(r"Read `\$\{CLAUDE_SKILL_DIR\}/([^`]+)` and follow it exactly")
_BASH_BLOCK = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)
_TOOLBOX_MAIN = re.compile(r"^main\(\) \{\n(.*?)^\}", re.DOTALL | re.MULTILINE)
_TOOLBOX_USAGE = re.compile(r"^usage\(\) \{\n(.*?)^\}", re.DOTALL | re.MULTILINE)
_CASE_LABEL = re.compile(r"^\s+([a-z]+)\)", re.MULTILINE)
_SCRIPT_NAME = re.compile(r"cqa_notion\.py|cqa_plan\.py|cqa-toolbox\.sh|cqa_ssh_ro\.py")
_NOTION_WRITE = re.compile(r"cqa_notion\.py\" (?:apply|comment-page)\b")
_CHAINING = re.compile(r"&&|\|\||;|\|")
_PLAN_QUESTION = "« Écrire le plan dans la copie Notion ? »"
_ASSIGN_QUESTION = "« Écrire ces <n> assignations dans Notion ? »"
_ASSIGN_APPLY = 'apply "$RUN" --plan notion/assign-plan.json'
_SESSION_CREDENTIALS = "when the session holds the Basic Auth credentials"


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


def _skill_files() -> list:
    return [SKILL, *sorted(REFERENCES.glob("*.md"))]


def _python_commands(module) -> dict:
    """Return {subcommand: option strings} of the argparse parser of a CLI module."""
    parser = module._build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {name: set(sub._option_string_actions) for name, sub in action.choices.items()}
    raise AssertionError(f"{module.__name__} : aucune sous-commande")


def _toolbox_commands() -> dict:
    """Return {subcommand: flags} of cqa-toolbox.sh: its main() cases, the flags of its usage."""
    script = _text(TOOLBOX)
    commands = _CASE_LABEL.findall(_TOOLBOX_MAIN.search(script).group(1))
    flags = set(_FLAG.findall(_TOOLBOX_USAGE.search(script).group(1)))
    return {name: flags for name in commands}


def _shell_segment(text: str) -> str:
    """Return the arguments of one command: up to the remote command or a shell operator."""
    return _SHELL_END.split(text, maxsplit=1)[0]


def _section(text: str, heading: str) -> str:
    """Return the body of a "## " section of a Markdown text, up to the next one."""
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


class SkillTest(unittest.TestCase):
    def test_skill_frontmatter(self):
        fields = _frontmatter(SKILL)
        self.assertEqual(fields.get("name"), "checklist-quality-audit")
        self.assertEqual(fields.get("disable-model-invocation"), "true")
        self.assertEqual(fields.get("argument-hint"), ARGUMENT_HINT)
        description = fields.get("description") or ""
        self.assertTrue(description)
        self.assertLessEqual(len(description), MAX_DESCRIPTION)
        self.assertIn("/gm:checklist-quality-audit", description)
        self.assertIn("/gm:cqa", description)

    def test_skill_commands_exist(self):
        known = {
            "cqa_notion.py": _python_commands(cqa_notion),
            "cqa_plan.py": _python_commands(cqa_plan),
            "cqa-toolbox.sh": _toolbox_commands(),
        }
        self.assertEqual(set(known["cqa-toolbox.sh"]), {"doctor", "pull", "run", "all"})
        found = set()
        for path in _skill_files():
            for cli, command, rest in _CLI_CALL.findall(_text(path)):
                with self.subTest(file=path.name, cli=cli, command=command):
                    self.assertIn(command, known[cli])
                    flags = set(_FLAG.findall(_shell_segment(rest)))
                    self.assertLessEqual(flags, known[cli][command])
                    found.add((cli, command))
        self.assertIn(("cqa_notion.py", "apply"), found)
        self.assertIn(("cqa-toolbox.sh", "all"), found)

    def test_guard_modes_exist(self):
        modes = set(_FLAG.findall(cqa_ssh_ro.USAGE))
        calls = 0
        for path in _skill_files():
            for rest in _GUARD_CALL.findall(_text(path)):
                flags = set(_FLAG.findall(_shell_segment(rest)))
                calls += 1
                with self.subTest(file=path.name, call=rest.strip()):
                    self.assertLessEqual(flags, modes)
        self.assertTrue(calls)

    def test_bash_blocks_set_their_variables(self):
        for path in _skill_files():
            for block in _BASH_BLOCK.findall(_text(path)):
                with self.subTest(file=path.name, block=block.splitlines()[0]):
                    if '"$S/' in block:
                        self.assertIn(SCRIPTS_LINE, block)
                    if "$RUN" in block:
                        self.assertRegex(block, r'(?m)^RUN="[^"]+"')

    def test_references_exist(self):
        cited = set(_REFERENCE.findall(_text(SKILL)))
        for name in sorted(cited):
            with self.subTest(reference=name):
                self.assertTrue((REFERENCES / name).is_file())
        self.assertEqual(cited, EXPECTED_REFERENCES)
        self.assertEqual({path.name for path in REFERENCES.glob("*.md")}, EXPECTED_REFERENCES)

    def test_skill_sets_the_scripts_dir(self):
        self.assertIn(SCRIPTS_LINE, _text(SKILL))

    def test_one_script_command_per_block(self):
        for path in _skill_files():
            for block in _BASH_BLOCK.findall(_text(path)):
                with self.subTest(file=path.name, block=block.splitlines()[-1]):
                    self.assertLessEqual(len(_SCRIPT_NAME.findall(block)), 1)

    def test_notion_writes_stand_alone(self):
        writes = 0
        for path in _skill_files():
            for block in _BASH_BLOCK.findall(_text(path)):
                if not _NOTION_WRITE.search(block):
                    continue
                writes += 1
                lines = [
                    line
                    for line in block.splitlines()
                    if line.strip() and not line.startswith(("S=", "RUN="))
                ]
                with self.subTest(file=path.name, block=lines[0]):
                    self.assertEqual(len(lines), 1)
                    self.assertNotRegex(lines[0], _CHAINING)
        self.assertTrue(writes)

    def test_commands_rule(self):
        rules = _section(_text(SKILL), "1. Imperative rules")
        for needle in (
            "One Bash call per command",
            "never chain a Notion write with anything else",
            "Never repeat a password",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)

    def test_notion_writes_follow_the_confirmation(self):
        skill = _text(SKILL)
        self.assertLess(skill.index(_PLAN_QUESTION), skill.index('cqa_notion.py" apply "$RUN"'))
        for path in (SKILL, REFERENCES / "assign.md"):
            with self.subTest(file=path.name):
                text = _text(path)
                self.assertLess(text.index(_ASSIGN_QUESTION), text.index(_ASSIGN_APPLY))

    def test_run_is_complete_after_the_final_scan(self):
        skill = _text(SKILL)
        resume = _section(skill, "3. Resume")
        self.assertNotIn("test -f", resume)
        self.assertIn("`runs` reports it as `done`", resume)
        self.assertIn("whose `done` is false is incomplete", resume)
        writing = _section(skill, "8. Writing and synthesis")
        self.assertLess(writing.index('cqa_plan.py" scan'), writing.index("Write `done.json`"))

    def test_toolbox_credentials_come_from_the_session(self):
        for path in _skill_files():
            text = _text(path)
            blocks = [
                block
                for block in _BASH_BLOCK.findall(text)
                if "GM_AUDIT_AUTH_USER=" in block and "cqa-toolbox.sh" in block
            ]
            if blocks:
                with self.subTest(file=path.name):
                    self.assertIn(_SESSION_CREDENTIALS, text)
                    self.assertNotIn("when `target.access` is `basic_auth`", text)

    def test_no_em_dash_in_skill(self):
        for path in [*_skill_files(), ALIAS]:
            with self.subTest(file=path.parent.name + "/" + path.name):
                self.assertNotIn(EM_DASH, _text(path))


class AliasTest(unittest.TestCase):
    def test_alias_frontmatter(self):
        fields = _frontmatter(ALIAS)
        self.assertEqual(fields.get("name"), "cqa")
        self.assertEqual(fields.get("disable-model-invocation"), "true")
        self.assertEqual(fields.get("argument-hint"), ARGUMENT_HINT)
        self.assertEqual(fields.get("argument-hint"), _frontmatter(SKILL)["argument-hint"])
        description = fields.get("description") or ""
        self.assertLessEqual(len(description), MAX_DESCRIPTION)
        self.assertIn("/gm:checklist-quality-audit", description)

    def test_alias_points_to_main_skill(self):
        body = _text(ALIAS)
        targets = _ALIAS_TARGET.findall(body)
        self.assertEqual(len(targets), 1)
        self.assertEqual((ALIAS_DIR / targets[0]).resolve(), SKILL.resolve())
        self.assertIn("with these arguments: $ARGUMENTS", body)
        self.assertIn("references/", body)
        self.assertIn("Resolve every relative path from that skill's directory", body)

    def test_alias_keeps_the_scripts_dir_valid(self):
        scripts = ALIAS_DIR / SCRIPTS_LINE.split("/", 1)[1].rstrip('"')
        self.assertEqual(scripts.resolve(), (ROOT / "scripts").resolve())
        self.assertTrue((scripts / "cqa_notion.py").is_file())


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

    def test_prod_probes_need_a_prod_run(self):
        rule = next(
            line for line in _text(VERDICTS_MD).splitlines() if "Only ask for URLs" in line
        )
        self.assertIn("target.prod_live", rule)
        self.assertIn("J0", rule)

    def test_explains_the_exit_codes_of_apply(self):
        text = _text(VERDICTS_MD)
        for needle in ("exit 7", "exit 3", "exit 8", "blocked[].labels", "unknown_option",
                       "page_outside_copy", "notion/applied.json"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

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
