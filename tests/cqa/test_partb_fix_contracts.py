"""Contracts of the final fixes of the toolbox 1.2 support (lot C, part B)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = (ROOT / "agents" / "cqa-auditor.md").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "checklist-quality-audit" / "SKILL.md").read_text(encoding="utf-8")
REFS = ROOT / "skills" / "checklist-quality-audit" / "references"

STALE = (
    "`tools/stylelint` and `tools/deps` describe the repos at `repos[].audited_sha`. "
    "In a rerun or a recette, when `git -C <repo> rev-parse HEAD` differs from `audited_sha`, "
    "do not judge CQ-40, CQ-66 or CQ-113 from them"
)


def section(text, title):
    start = text.index(title)
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


class StaleToolsContract(unittest.TestCase):
    def test_rerun_stylelint_and_deps_sections_pin_the_sentence(self):
        for title in ("## Rerun", "### Stylelint", "### Dependencies"):
            body = section(AUDITOR, title)
            self.assertIn(STALE, body, title)
            self.assertIn("run the check yourself on the current files (runner, then host)", body)
            self.assertIn("else `unknown` with that reason", body)

    def test_skill_never_relaunches_stylelint_or_deps_in_a_rerun(self):
        sec10 = section(SKILL, "## 10.")
        sec11 = section(SKILL, "## 11.")
        for body in (sec10, sec11):
            self.assertRegex(body, r"(?i)never (offer )?stylelint or deps")
            self.assertIn("audited_sha", body)

    def test_rerun_candidates_tools_are_already_relaunched(self):
        sec10 = section(SKILL, "## 10.")
        self.assertIn("lists the tools already relaunched since the findings", sec10)
        self.assertIn("not the tools to relaunch", sec10)
        self.assertIn("which tools to relaunch", sec10)
        self.assertNotIn("relaunch the tools of `tools` that the lead kept", sec10)






class FirstContextContract(unittest.TestCase):
    def test_wp_plugins_needs_a_first_context_written_before_step_8(self):
        step6 = SKILL[SKILL.index("6. Key pages:"):SKILL.index("7. Mode and Notion consent")]
        self.assertIn("first write a minimal `$RUN/context.json` holding `repos` (from `project`), `target` and `sites`", step6)
        self.assertIn("step 8, which completes and checks it", step6)
        ref = (REFS / "context.md").read_text(encoding="utf-8")
        body = section(ref, "## 6.1.1")
        self.assertIn("write a first `context.json` holding `repos` (from `project`), `target` and `sites`", body)
        self.assertIn("step 8 of the skill completes and checks it", body)


class RepoHostDomainContract(unittest.TestCase):
    def test_wp_plugins_doc_names_the_repo_host_among_project_domains(self):
        body = section((REFS / "context.md").read_text(encoding="utf-8"), "## 6.1.1")
        self.assertIn("the host of the repo", body)


if __name__ == "__main__":
    unittest.main()
