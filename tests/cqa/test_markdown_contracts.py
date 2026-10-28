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
ARGUMENT_HINT = '"[diagnostic | assign | rerun] [--phase \\"MEL J0\\"] [--dry-run]"'
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
    def test_waves_recheck_target_and_ssh(self):
        text = _text(SKILL)
        start = text.index("## 6. Audit")
        end = text.index("## 7. ")
        section = text[start:end]
        for needle in ("Before each new wave of lots", "steps 4, 5, 6 and 8",
                       "cqa_plan.py probe-target", "ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> true",
                       "context.json", "« Reprendre »", "« Continuer quand même »",
                       "remarques de la synthèse",
                       "without credentials, and compare its `access` with the one in `context.json`",
                       "once more with `--auth-env`: any answer other than `open`",
                       "« La préprod refuse maintenant les identifiants Basic Auth »"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_browser_lots_recheck_chrome(self):
        for path in (SKILL, REFERENCES / "browser.md", REFERENCES / "lots.md"):
            with self.subTest(path=path.name):
                self.assertIn("tabs_context_mcp", _text(path))
        for path in (SKILL, REFERENCES / "browser.md"):
            with self.subTest(path=path.name):
                self.assertIn("Before each browser lot", _text(path))
                self.assertIn("« Continuer sans navigateur »", _text(path))
        self.assertIn("reconnect Claude in Chrome", _text(SKILL))
        browser = _text(REFERENCES / "browser.md")
        self.assertNotIn("The lead must reconnect Claude in Chrome before the lot starts", browser)
        self.assertIn("« Continuer sans navigateur » holds for the rest of the run, unless the lead says otherwise", browser)
        self.assertIn(
            "« Continuer quand même » and « Continuer sans navigateur » hold for the rest of the run, "
            "unless the lead says otherwise",
            _section(_text(SKILL), "6. Audit"),
        )

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

    def test_diagnostic_tests_the_database_with_select_1(self):
        section = _section(_text(REFERENCES / "diagnostic.md"), "8. Application CLI")
        self.assertIn("--sql <drush|wp|artisan> --app-dir <dossier>", section)
        self.assertIn('"SELECT 1"', section)
        self.assertIn("self-signed certificate", section)
        self.assertIn("--skip-ssl", section)
        self.assertIn("ssh.sql_skip_ssl: true", section)
        server = _text(REFERENCES / "server.md")
        self.assertRegex(server, r"ssh\.sql_skip_ssl.{0,40}true")
        self.assertIn("--skip-ssl", server)
        for text in (section, server):
            self.assertIn("without TLS", text)
            self.assertIn("travel in clear", text)
            self.assertNotIn("skips the TLS check", text)
        self.assertIn("the folder inside the container", section)
        self.assertIn("--compose-file <dossier sur l'hôte>/docker-compose.yml", section)

    def test_auditor_passes_skip_ssl_and_compose_file(self):
        auditor = _text(AUDITOR)
        self.assertRegex(auditor, r"ssh\.sql_skip_ssl: true.{0,60}--skip-ssl")
        self.assertIn("--compose-file", auditor)
        self.assertIn("--compose-file", _text(REFERENCES / "server.md"))
        self.assertIn("--compose-file", _text(REFERENCES / "diagnostic.md"))

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

    def test_audit_keeps_the_workstation_awake(self):
        skill = _text(SKILL)
        start = 'cqa-keepawake.sh" start "$RUN"'
        stop = 'cqa-keepawake.sh" stop "$RUN"'
        audit = _section(skill, "6. Audit")
        self.assertIn(start, audit)
        self.assertLess(audit.index(start), audit.index("1. Prepare"))
        self.assertIn(
            "Le poste ne se mettra pas en veille pendant l'audit. Gardez l'écran ouvert : "
            "sur un Mac, rabattre l'écran le met en veille malgré tout.",
            audit,
        )
        self.assertIn(
            "« Si cette session s'arrête avant la fin de l'audit, libérez le poste avec : "
            'bash "<scripts>/cqa-keepawake.sh" stop "<run_dir>" »',
            audit,
        )
        resume = _section(skill, "3. Resume")
        self.assertIn(start, resume)
        self.assertNotIn("is gone with it", resume)
        self.assertIn("may have left its inhibitor running", resume)
        self.assertIn("`start` reuses a live one", resume)
        writing = _section(skill, "8. Writing and synthesis")
        self.assertIn(stop, writing)
        self.assertLess(writing.index("**6. Completion.**"), writing.index(stop))
        plan = _section(skill, "7. Plan and confirmation")
        option = plan.index("   - « Sans toucher aux statuts posés par un humain »:")
        rebuild = plan.index('plan "$RUN" --skip-human-overrides')
        abandon = plan.index("If the lead abandons the audit")
        self.assertLess(option, rebuild)
        self.assertNotIn("   - «", plan[option + 1:rebuild])
        self.assertLess(rebuild, abandon)
        self.assertLess(abandon, plan.index(stop))
        self.assertIn("every other stop of the audit", skill)
        self.assertGreaterEqual(len(re.findall(r"cqa-keepawake\.sh\"? stop \"\$RUN\"", skill)), 5)

    def test_rerun_argument_and_section(self):
        text = _text(SKILL)
        self.assertIn("- `rerun`:", _section(text, "2. Arguments"))
        section = _section(text, "10. Rerun")
        for needle in (
            'cqa_plan.py" rerun-start "$RUN"',
            'cqa_plan.py" rerun-close "$RUN" <NN>',
            "--rows CQ-3,CQ-12",
            'cqa_plan.py" rerun-candidates "$RUN"',
            "--refs",
            'cqa_notion.py" rows "$RUN"',
            'cqa-toolbox.sh" run "$RUN" <tool>',
            'cqa_plan.py" validate "$RUN" <lot_id> --rerun <NN> --rows',
            'cqa_plan.py" plan "$RUN"',
            "Run dir: <run_dir>. Lot: <lot_id>. Follow-up: no. Rerun: <NN>. Rows: CQ-3, CQ-12. Follow your instructions.",
            "section 7",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_rerun_presents_the_candidates_before_any_dispatch(self):
        section = _section(_text(SKILL), "10. Rerun")
        candidates = section.index("rerun-candidates")
        presentation = section.index("« Lancer cette relance ? »")
        snapshot = section.index("cqa_plan.py\" rerun-start")
        toolbox = section.index("cqa-toolbox.sh")
        dispatch = section.index("Rerun: <NN>")
        self.assertLess(snapshot, candidates)
        self.assertLess(candidates, presentation)
        self.assertLess(presentation, toolbox)
        self.assertLess(presentation, dispatch)
        validate = section.index("--rerun <NN> --rows")
        close = section.index("rerun-close")
        plan = section.index('cqa_plan.py" plan')
        self.assertLess(dispatch, validate)
        self.assertLess(validate, close)
        self.assertLess(close, plan)
        self.assertNotIn("highest number", section)
        self.assertIn("which files were discarded", section)

    def test_anything_else_hint_matches_the_argument_hint(self):
        self.assertIn(
            "[diagnostic | assign | rerun] [--phase \"MEL J0\"] [--dry-run]` and stop.",
            _text(SKILL),
        )

    def test_rerun_dispatch_is_documented_in_lots_reference(self):
        text = _text(REFERENCES / "lots.md")
        self.assertIn("Rerun: <NN>. Rows: CQ-3, CQ-12.", text)
        self.assertIn("-rerun-<NN>.json", text)

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

    def test_agents_never_request_php_files(self):
        server = _text(SKILL_DIR / "references" / "server.md")
        for path in (AUDITOR, BROWSER):
            text = _text(path)
            with self.subTest(agent=path.name):
                self.assertIn("Never request a URL whose path contains `.php`", text)
                for needle in ("GET, HEAD or navigation", "/index.php", "wp-login.php", "wp-admin/admin-ajax.php", "never by a request"):
                    self.assertIn(needle, text)
                rule = next(line for line in text.splitlines() if "Never request a URL whose path contains `.php`" in line)
                self.assertIn("Never ask for such a URL in `needs_probes` either", rule)
                self.assertIn("the toolbox sends those requests", rule)
                self.assertNotIn("for the lead", rule)
        for needle in ("CQ-97", "CQ-144", "location ~ \\.php$", "php_flag engine off", ".htaccess", "Never request a `.php` file"):
            with self.subTest(server=needle):
                self.assertIn(needle, server)

    def test_agents_date_the_tool_evidence(self):
        for path in (AUDITOR, BROWSER):
            text = _text(path)
            with self.subTest(agent=path.name):
                self.assertIn("tool_dates", text)
                self.assertIn("`started_at` of its `result.json`", text)
                self.assertIn("keep their `tool_dates`", text)
                self.assertIn("add `http-followup` to `tool_dates`", text)

    def test_agents_evaluate_only_the_rows_of_a_rerun(self):
        for path in (AUDITOR, BROWSER):
            text = _text(path)
            with self.subTest(agent=path.name):
                self.assertIn("Rerun: <NN>. Rows: CQ-3, CQ-12.", text)
                section = _section(text, "Rerun")
                self.assertIn("only those rows", section)
                self.assertIn("findings/<lot>-rerun-<NN>.json", section)
                self.assertIn("Never modify `findings/<lot>.json`", section)

    def test_agents_describe_the_php_entry_points_under_a_sub_path(self):
        for path in (AUDITOR, BROWSER):
            text = _text(path)
            with self.subTest(agent=path.name):
                for needle in (
                    "<path>/index.php",
                    "<path>/wp-login.php",
                    "<path>/wp-admin/admin-ajax.php",
                    "<path>/index.php/<route>",
                    "root `/index.php` is refused under a sub-path",
                ):
                    self.assertIn(needle, text)
                self.assertNotIn("only the three entry points above pass `validate`", text)

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

    def test_auditor_reads_the_crawl_outputs_with_their_limits(self):
        text = _text(AUDITOR)
        self.assertNotIn("`tools/crawl/result.json` has the findings", text)
        self.assertIn("`tools/crawl/summary.json` has the findings", text)
        for needle in ("crawl tronqué", "a missing finding is not proof", "`counts.pages`", "`adopted_origin`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

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
