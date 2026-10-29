import json
import tempfile
import unittest
from pathlib import Path

from cqa import deferred, fix_state
from tests.cqa.test_cli_fix import FixCase

LONG_DASH = "\u2014"


def row(outcome, reason=None, section="securite"):
    return {"outcome": outcome, "reason": reason, "commits": [], "pass": 1, "attention": None,
            "section": section}


def key(repo, category, alias, rows, *, branch="feature/checklist-qa-fix", mr=None, step="done",
        kept_local=False, push_ready=True):
    return {
        "repo": repo, "push_ready": push_ready,
        "branch": branch, "base": "develop", "start_sha": "a" * 40,
        "worktree": None, "reused": None, "mr": mr, "step": step, "pass": 1,
        "dev_fingerprint": None, "kept_local": kept_local,
        "sections": [{"category": category, "alias": alias, "refs": [int(ref) for ref in rows],
                      "state": "settled", "reason": None}],
        "pass_sections": {"1": alias}, "rows": rows,
    }


def section(text, heading):
    lines = text.split("\n")
    start = lines.index(f"## {heading}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


class SummaryCase(FixCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / "run"
        (self.run_dir / "fix" / "front").mkdir(parents=True)
        (self.run_dir / "fix" / "back").mkdir(parents=True)
        (self.run_dir / "context.json").write_text(
            json.dumps({"name": "Boutique Exemple"}), encoding="utf-8")
        cand = lambda ref, title, **more: {"ref": ref, "title": title, **more}
        candidates = {
            "keys": [
                {"key": "front", "repo": "front", "sections": [
                    {"category": "SÉCURITÉ", "alias": "securite",
                     "rows": [cand(3, "Trois"), cand(12, "Douze", semi_auto=True),
                              cand(13, "Treize"), cand(14, "Quatorze"), cand(15, "Quinze")]}]},
                {"key": "back", "repo": "back", "sections": [
                    {"category": "SEO", "alias": "seo", "rows": [cand(20, "Vingt")]}]},
            ],
            "left": [{"ref": 40, "title": "Quarante", "category": "SÉCURITÉ", "reason": "Manuel",
                      "url": None}],
        }
        (self.run_dir / "fix" / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
        self.write_state(push="mr")
        (self.run_dir / "fix" / "front" / "checks.json").write_text(json.dumps({
            "checks": [{"name": "php -l", "status": "passed", "files": ["a.php"], "detail": None}],
            "not_run": [{"name": "Makefile (test)", "command": "make test", "why": "x"}],
        }), encoding="utf-8")

    def write_state(self, push="mr", front_mr=True, **front):
        rows = {"3": row("fixed"), "12": row("fixed"), "13": row("skipped", "Trop large."),
                "14": row("question", "Question sans réponse."), "15": row("pending")}
        mr = {"iid": 7, "url": "https://gitlab.example.org/acme/shop/-/merge_requests/7"}
        state = {
            "started_at": "2026-10-29T09:00:00Z", "finished_at": None, "stage": "notion",
            "mode": "present", "push": push, "notion": True,
            "project_name": "Boutique Exemple",
            "keys": {
                "front": key("front", "SÉCURITÉ", "securite", rows,
                             mr=mr if front_mr else None, **front),
                "back": key("back", "SEO", "seo", {"20": row("fixed", section="seo")},
                            branch="feature/checklist-qa-fix-2", mr=None, kept_local=True),
            },
        }
        fix_state.save(self.run_dir, state)

    def summary(self, *extra):
        return self.run_main(["summary", self.run_dir, *extra])


class SummaryTest(SummaryCase):
    def test_summary_sections(self):
        code, out, err = self.summary("--replace-failed", "CQ-3,CQ-12")
        self.assertEqual(code, 0, err)
        text = json.loads(out)["text"]
        self.assertEqual((self.run_dir / "fix" / "synthese.md").read_text(encoding="utf-8"), text)
        mr = section(text, "MR")
        self.assertIn("front", mr)
        self.assertIn("SÉCURITÉ", mr)
        self.assertIn("https://gitlab.example.org/acme/shop/-/merge_requests/7", mr)
        self.assertIn("- CQ-3 : Trois", mr)
        self.assertIn("- CQ-12 : Douze (Semi-auto)", mr)
        self.assertIn("- CQ-15 : Quinze", mr)
        self.assertNotIn("Vingt", mr)
        left = section(text, "Laissées aux devs")
        self.assertIn("Manuel", left)
        self.assertIn("- CQ-40 : Quarante", left)
        self.assertLess(left.index("Trop large."), left.index("Question sans réponse."))
        self.assertIn("- CQ-13 : Treize", left)
        self.assertIn("- CQ-14 : Quatorze", left)
        checks = section(text, "Vérifications à faire par le relecteur")
        self.assertIn("Makefile (test)", checks)
        self.assertIn("make test", checks)
        local = section(text, "Branches restées en local")
        self.assertIn("- back : feature/checklist-qa-fix-2", local)
        self.assertNotIn("front", local)
        self.assertIn("Commentaire du skill non remplacé, un second a été créé : CQ-3, CQ-12",
                      section(text, "Notion"))
        self.assertIn("recette", section(text, "Suite"))
        self.assertNotIn(LONG_DASH, text)

    def test_optional_sections_are_left_out(self):
        state = fix_state.load(self.run_dir)
        state["keys"]["back"]["kept_local"] = False
        fix_state.save(self.run_dir, state)
        text = json.loads(self.summary()[1])["text"]
        self.assertNotIn("## Branches restées en local", text)
        self.assertNotIn("## Notion", text)

    def test_local_pass_lists_every_branch(self):
        self.write_state(push="local", front_mr=False)
        text = json.loads(self.summary()[1])["text"]
        local = section(text, "Branches restées en local")
        self.assertIn("- front : feature/checklist-qa-fix", local)
        self.assertIn("- back : feature/checklist-qa-fix-2", local)

    def test_checks_are_deduplicated_across_keys(self):
        state = fix_state.load(self.run_dir)
        state["keys"]["back"]["mr"] = {"iid": 8, "url": "https://gitlab.example.org/acme/api/-/merge_requests/8"}
        fix_state.save(self.run_dir, state)
        (self.run_dir / "fix" / "back" / "checks.json").write_text(json.dumps({
            "checks": [], "not_run": [{"name": "Makefile (test)", "command": "make test", "why": "x"}],
        }), encoding="utf-8")
        text = json.loads(self.summary()[1])["text"]
        self.assertEqual(section(text, "Vérifications à faire par le relecteur").count("make test"), 1)

    def test_team_message_lists_every_mr(self):
        state = fix_state.load(self.run_dir)
        state["keys"]["back"]["mr"] = {"iid": 8, "url": "https://gitlab.example.org/acme/api/-/merge_requests/8"}
        fix_state.save(self.run_dir, state)
        message = json.loads(self.summary()[1])["team_message"]
        self.assertEqual(message, (
            "Boutique Exemple\n"
            "Correctifs automatiques de la checklist PRE-MEL : 2 MR en brouillon à relire.\n"
            "https://gitlab.example.org/acme/shop/-/merge_requests/7\n"
            "https://gitlab.example.org/acme/api/-/merge_requests/8"
        ))

    def test_team_message_without_mr(self):
        self.write_state(push="local", front_mr=False)
        message = json.loads(self.summary()[1])["team_message"]
        self.assertEqual(message, (
            "Boutique Exemple\nCorrectifs préparés en branches locales : 2 branches."))

    def test_team_message_counts_one_branch_in_the_singular(self):
        self.write_state(push="local", front_mr=False)
        state = fix_state.load(self.run_dir)
        state["keys"]["back"]["branch"] = None
        fix_state.save(self.run_dir, state)
        message = json.loads(self.summary()[1])["team_message"]
        self.assertEqual(message, (
            "Boutique Exemple\nCorrectifs préparés en branches locales : 1 branche."))

    def test_a_row_covered_by_another_fix_ships_in_the_mr(self):
        """A row « couverte par le correctif de CQ-x » is fixed by the MR, not left to the devs."""
        state = fix_state.load(self.run_dir)
        state["keys"]["front"]["rows"]["13"]["reason"] = (
            "corrigé par le commit de CQ-3 d'une passe précédente")
        fix_state.save(self.run_dir, state)
        text = json.loads(self.summary()[1])["text"]
        self.assertNotIn("Treize", section(text, "Laissées aux devs"))
        self.assertIn("- CQ-13 : Treize (couverte par le correctif de CQ-3)", section(text, "MR"))

    def test_summary_closes_the_pass(self):
        deferred.add(self.run_dir, "question", "Question de l'audit", "CQ-3")
        deferred.add(self.run_dir, "fix_question", "Question du fix", "CQ-14")
        deferred.add(self.run_dir, "fix_question", "Autre question", "CQ-15")
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        self.assertTrue(fix_state.load(self.run_dir)["finished_at"])
        self.assertEqual([i["kind"] for i in deferred.open_items(self.run_dir)], ["question"])
        self.assertEqual(deferred.open_items(self.run_dir, fix=True), [])
        self.assertFalse(fix_state.pending(self.run_dir))

    def test_summary_refuses_a_secret(self):
        deferred.add(self.run_dir, "fix_question", "Question du fix", "CQ-14")
        state = fix_state.load(self.run_dir)
        state["keys"]["front"]["rows"]["13"]["reason"] = "clé AKIAIOSFODNN7EXAMPLE trouvée"
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary()
        self.assertEqual(code, 7)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", err)
        self.assertFalse((self.run_dir / "fix" / "synthese.md").exists())
        self.assertIsNone(fix_state.load(self.run_dir)["finished_at"])
        self.assertEqual(len(deferred.open_items(self.run_dir, fix=True)), 1)

    def test_summary_needs_a_pass_and_valid_refs(self):
        self.assertEqual(self.summary("--replace-failed", "oops")[0], 2)
        self.assertEqual(self.summary()[0], 0)
        code, out, err = self.summary()
        self.assertEqual(code, 2)
        self.assertIn("cqa_fix.py open", err)

    def test_abandon_resolves_the_fix_questions(self):
        deferred.add(self.run_dir, "question", "Question de l'audit", "CQ-3")
        deferred.add(self.run_dir, "fix_question", "Question du fix", "CQ-14")
        state = fix_state.load(self.run_dir)
        for item in state["keys"].values():
            item["branch"] = None
        fix_state.save(self.run_dir, state)
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(deferred.open_items(self.run_dir, fix=True), [])
        self.assertEqual(len(deferred.open_items(self.run_dir)), 1)


if __name__ == "__main__":
    unittest.main()
