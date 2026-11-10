import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cqa import deferred, fix_state, fix_summary
from tests.cqa.test_cli_fix import FixCase
from tests.cqa.test_fix_mr import AWS, document_point, write_report_documents

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
            json.dumps({"name": "Nom du contexte"}), encoding="utf-8")
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
        self.assertIn("reprend d'elle-même les lignes « En review »", section(text, "Suite"))
        self.assertNotIn("passer les lignes en « Mis en place »", section(text, "Suite"))
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

    def test_commands_already_broken_before_the_pass_are_listed_per_repo(self):
        broken = "erreur déjà présente avant le correctif (lint is red)"
        (self.run_dir / "fix" / "front" / "checks.json").write_text(json.dumps({
            "checks": [
                {"name": "npm run lint", "status": "not_run", "files": [], "detail": broken,
                 "kind": "lint", "already_broken": True},
                {"name": "npm test", "status": "passed", "files": [], "detail": "", "kind": "test"},
                {"name": "npm run build", "status": "not_run", "files": [], "kind": "build",
                 "detail": "délai dépassé"},
            ], "not_run": []}), encoding="utf-8")
        text = json.loads(self.summary()[1])["text"]
        found = section(text, "Erreurs déjà présentes avant les correctifs")
        self.assertIn("à signaler aux devs", found)
        self.assertIn(f"- front : npm run lint : {broken}", found)
        self.assertNotIn("npm test", found)
        self.assertNotIn("npm run build", found)
        checks = section(text, "Vérifications à faire par le relecteur")
        self.assertNotIn("npm run lint", checks)
        self.assertIn("- npm run build", checks)
        self.assertNotIn(LONG_DASH, text)

    def test_the_detail_of_a_broken_command_stays_on_its_bullet(self):
        detail = "erreur déjà présente avant le correctif (```js\nfailed `x`\n\n  at y)"
        (self.run_dir / "fix" / "front" / "checks.json").write_text(json.dumps({
            "checks": [{"name": "npm test", "status": "not_run", "files": [], "detail": detail,
                        "kind": "test", "already_broken": True}], "not_run": []}),
            encoding="utf-8")
        text = json.loads(self.summary()[1])["text"]
        found = section(text, "Erreurs déjà présentes avant les correctifs")
        self.assertIn("- front : npm test : erreur déjà présente avant le correctif ('''js ; "
                      "failed 'x' ; at y)", found)
        self.assertNotIn("`", found)
        self.assertIn("dans le conteneur", found)
        self.assertIn("cause possible : l'environnement", found)

    def test_without_a_broken_command_the_section_is_left_out(self):
        text = json.loads(self.summary()[1])["text"]
        self.assertNotIn("Erreurs déjà présentes", text)

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

    def test_team_message_starts_with_the_project_name(self):
        """The project name recorded by open heads the synthesis and the team message, never
        the run id nor the « name » of context.json."""
        result = json.loads(self.summary()[1])
        self.assertEqual(result["team_message"].split("\n")[0], "Boutique Exemple")
        self.assertEqual(result["text"].split("\n")[0],
                         "# Boutique Exemple : synthèse de la passe de correction")
        for absent in ("Nom du contexte", self.run_dir.name):
            self.assertNotIn(absent, result["team_message"])
        state = fix_state.load(self.run_dir)
        state["project_name"] = None
        state["finished_at"] = None
        fix_state.save(self.run_dir, state)
        (self.run_dir / "context.json").write_text(json.dumps({
            "name": "Nom du contexte", "project": {"name": "Acme - Boutique"},
            "repos": [{"name": "front", "path": "/tmp/Acme - Autre - WordPress"}]}),
            encoding="utf-8")
        result = json.loads(self.summary()[1])
        self.assertEqual(result["team_message"].split("\n")[0], "Acme - Boutique")

    def test_summary_marks_partial_rows_failed_sections_and_reorder_conflicts(self):
        state = fix_state.load(self.run_dir)
        front, back = state["keys"]["front"], state["keys"]["back"]
        front["rows"]["3"]["remaining"] = "Traduire la page anglaise."
        front["rows"]["12"].update(remaining="Reste du rapport.",
                                   corrections={"remaining": "Reste relu."})
        front["sections"].append({
            "category": "QUALITÉ", "alias": "qualite", "refs": [16], "state": "failed",
            "reason": "Revue illisible.",
            "leftover": {"patch": "fix/front/leftover-1.patch", "base": "b" * 40,
                         "untracked": ["notes.txt"]}})
        front["rows"]["16"] = row("dropped", "Revue illisible.", section="qualite")
        back["reorder_conflicts"] = ["fix(seo CQ-20): add the canonical link"]
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary("--in-review", "CQ-3,CQ-12")
        self.assertEqual(code, 0, err)
        text = json.loads(out)["text"]
        mr = section(text, "MR")
        self.assertIn("Lignes mises en MR : 2, dont 2 partielles.", mr)
        self.assertIn("- CQ-3 : Trois (partielle, reste à faire : Traduire la page anglaise.)", mr)
        self.assertIn("- CQ-12 : Douze (Semi-auto) (partielle, reste à faire : Reste relu.)", mr)
        self.assertNotIn("Reste du rapport.", text)
        self.assertEqual(section(text, "Points d'attention").strip().split("\n"), [
            "- front : section QUALITÉ non aboutie : Revue illisible. Modifications non "
            "commitées sauvées dans fix/front/leftover-1.patch.",
            "- back : Commits non rangés par section : conflit de rejeu "
            "(`fix(seo CQ-20): add the canonical link`).",
        ])
        self.assertIn("Revue illisible. :\n- CQ-16 : sans titre", section(text, "Laissées aux devs"))
        self.assertIn("Lignes passées « En review » : 2 (CQ-3, CQ-12).", section(text, "Notion"))
        self.assertNotIn(LONG_DASH, text)

    def test_summary_lists_the_keys_left_to_the_lead(self):
        """Ruling P19: a key left (key["left"] of the state) is named with its reason, its
        branch and its worktree, and its branch is not counted as a prepared fix."""
        state = fix_state.load(self.run_dir)
        back = state["keys"]["back"]
        back.update(step="reviewed", worktree="/srv/run/fix/back/wt",
                    left={"reason": "commit de fusion dans la branche", "at": "2026-10-29T10:00:00Z"})
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        text = result["text"]
        self.assertEqual(section(text, "Clés laissées au lead").strip(), (
            "- back : commit de fusion dans la branche (branche feature/checklist-qa-fix-2, "
            "worktree /srv/run/fix/back/wt).\n"
            "  - CQ-20 : Vingt : corrigée sur la branche"))
        self.assertLess(text.index("## Clés laissées au lead"), text.index("## Laissées aux devs"))
        self.assertEqual(result["left_keys"], [{
            "key": "back", "reason": "commit de fusion dans la branche", "at": "2026-10-29T10:00:00Z",
            "branch": "feature/checklist-qa-fix-2", "worktree": "/srv/run/fix/back/wt",
            "rows": [{"ref": 20, "title": "Vingt", "state": "corrigée sur la branche"}]}])
        self.assertNotIn("## Branches restées en local", text)
        self.assertNotIn(LONG_DASH, text)
        state = fix_state.load(self.run_dir)
        state["finished_at"] = None
        state["keys"]["back"]["left"] = None
        fix_state.save(self.run_dir, state)
        result = json.loads(self.summary()[1])
        self.assertNotIn("## Clés laissées au lead", result["text"])
        self.assertEqual(result["left_keys"], [])
        self.assertIn("- back : feature/checklist-qa-fix-2",
                      section(result["text"], "Branches restées en local"))

    def test_a_left_key_lists_its_rows_and_hides_its_merge_request(self):
        """Final wave L2 and F1: a left key that took over an MR gets no MR block and no URL
        in the team message, which counts the keys left; its rows are listed under it, each
        with its state, and nowhere else."""
        state = fix_state.load(self.run_dir)
        back = state["keys"]["back"]
        back.update(step="reviewed", worktree="/srv/run/fix/back/wt", reused="mr", kept_local=False,
                    mr={"iid": 9, "url": "https://gitlab.example.org/acme/api/-/merge_requests/9"},
                    left={"reason": "commit de fusion dans la branche", "at": "2026-10-29T10:00:00Z"})
        back["rows"]["21"] = row("skipped", "Trop risqué.", section="seo")
        back["rows"]["22"] = row("dropped", "Revue bloquante.", section="seo")
        back["rows"]["23"] = row("question", "Quel contact ?", section="seo")
        back["rows"]["24"] = row("pending", section="seo")
        back["rows"]["25"] = row("done_before", section="seo")
        back["sections"][0]["refs"] = [20, 21, 22, 23, 24, 25]
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        text = result["text"]
        self.assertNotIn("merge_requests/9", text)
        self.assertNotIn("### back", section(text, "MR"))
        self.assertEqual(section(text, "Clés laissées au lead").strip().split("\n"), [
            "- back : commit de fusion dans la branche (branche feature/checklist-qa-fix-2, "
            "worktree /srv/run/fix/back/wt).",
            "  - CQ-20 : Vingt : corrigée sur la branche",
            "  - CQ-21 : sans titre : écartée : Trop risqué.",
            "  - CQ-22 : sans titre : retirée : Revue bloquante.",
            "  - CQ-23 : sans titre : question sans réponse",
            "  - CQ-24 : sans titre : non traitée",
            "  - CQ-25 : sans titre : déjà sur la branche",
        ])
        left = section(text, "Laissées aux devs")
        for absent in ("CQ-21", "CQ-22", "CQ-23", "Trop risqué."):
            self.assertNotIn(absent, left)
        self.assertEqual(result["team_message"], (
            "Boutique Exemple\n"
            "Correctifs automatiques de la checklist PRE-MEL : 1 MR en brouillon à relire.\n"
            "https://gitlab.example.org/acme/shop/-/merge_requests/7\n"
            "1 repo laissé au lead : back."
        ))
        self.assertNotIn(LONG_DASH, text)

    def test_a_key_left_before_its_start_is_listed_with_its_rows(self):
        """Final wave F1: a key whose start failed (left at « selected », no branch) does not
        vanish from the synthesis: its rows are listed, and the team message counts it."""
        self.write_state(push="local", front_mr=False)
        state = fix_state.load(self.run_dir)
        front = state["keys"]["front"]
        front.update(step="selected", branch=None, worktree=None, start_sha=None,
                     left={"reason": "la branche feature/checklist-qa-fix est extraite dans "
                                     "votre dossier", "at": "2026-10-29T10:00:00Z"})
        for item in front["rows"].values():
            item.update(outcome="pending", reason=None)
        state["keys"]["back"].update(
            left={"reason": "origin/develop est introuvable", "at": "2026-10-29T10:01:00Z"})
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        text = result["text"]
        self.assertEqual(section(text, "Clés laissées au lead").strip().split("\n"), [
            "- front : la branche feature/checklist-qa-fix est extraite dans votre dossier "
            "(branche aucune, worktree aucun).",
            "  - CQ-3 : Trois : non traitée",
            "  - CQ-12 : Douze : non traitée",
            "  - CQ-13 : Treize : non traitée",
            "  - CQ-14 : Quatorze : non traitée",
            "  - CQ-15 : Quinze : non traitée",
            "- back : origin/develop est introuvable (branche feature/checklist-qa-fix-2, "
            "worktree aucun).",
            "  - CQ-20 : Vingt : corrigée sur la branche",
        ])
        self.assertNotIn("## Branches restées en local", text)
        self.assertEqual(section(text, "MR").strip(), "Aucune MR créée.")
        self.assertEqual(result["team_message"], (
            "Boutique Exemple\nAucun correctif préparé.\n2 repos laissés au lead : front, back."))
        self.assertEqual([item["key"] for item in result["left_keys"]], ["front", "back"])
        self.assertEqual(result["left_keys"][0]["rows"][0],
                         {"ref": 3, "title": "Trois", "state": "non traitée"})

    def test_summary_gives_a_rescue_command_for_the_dropped_commits(self):
        """Ruling P11: the SHAs a drop removed (dropped_commits) are listed with the git
        command that keeps them; a leftover patch saved at abandon or open is named too."""
        state = fix_state.load(self.run_dir)
        front = state["keys"]["front"]
        front["rows"]["13"].update(outcome="dropped", dropped_commits=["a" * 40])
        front["rows"]["15"].update(outcome="dropped", reason="Revue bloquante.",
                                   dropped_commits=["b" * 40, "c" * 40])
        front["rows"]["3"]["dropped_commits"] = ["d" * 40]
        front["leftover"] = {"patch": "fix/front/leftover-2.patch", "base": "e" * 40,
                             "untracked": ["draft.txt"]}
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        text = json.loads(out)["text"]
        self.assertEqual(section(text, "Commits retirés de la branche").strip().split("\n"), [
            f"- front : CQ-13 : Treize : `git branch rescue/cq-13 {'a' * 40}`",
            f"- front : CQ-15 : Quinze : `git branch rescue/cq-15 {'b' * 40}`",
            f"- front : CQ-15 : Quinze : `git branch rescue/cq-15-2 {'c' * 40}`",
        ])
        self.assertNotIn("d" * 40, text)
        self.assertLess(text.index("## Points d'attention"), text.index("## Commits retirés"))
        self.assertIn("- front : modifications non commitées du worktree sauvées dans "
                      "fix/front/leftover-2.patch.", section(text, "Points d'attention"))
        self.assertNotIn(LONG_DASH, text)
        state = fix_state.load(self.run_dir)
        state["finished_at"] = None
        for row in state["keys"]["front"]["rows"].values():
            row.pop("dropped_commits", None)
        fix_state.save(self.run_dir, state)
        self.assertNotIn("## Commits retirés", json.loads(self.summary()[1])["text"])

    def test_review_status_count_needs_the_option(self):
        """The count of rows moved to « En review » is printed only when --in-review gives it."""
        self.assertEqual(self.summary("--in-review", "oops")[0], 2)
        state = fix_state.load(self.run_dir)
        self.assertTrue(state["notion"])
        text = json.loads(self.summary()[1])["text"]
        self.assertNotIn("Lignes passées « En review »", text)
        state["finished_at"] = None
        fix_state.save(self.run_dir, state)
        text = json.loads(self.summary("--in-review", "")[1])["text"]
        self.assertIn("Lignes passées « En review » : 0.", section(text, "Notion"))

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


class ContextSummaryTest(SummaryCase):
    """The « Rapports de contexte » section: the documents, the points no row holds and the
    disagreements between a report and the code."""

    POINTS = [document_point(1, ["7.3"]), document_point(2, ["12.9"]),
              document_point(3, ["5.1"]), document_point(4, ["7.3"], owner="contenu"),
              document_point(5, [], owner="tiers", source="Annexe, ligne 2")]

    def write_documents(self, points=None):
        write_report_documents(self.run_dir, points or self.POINTS, unlinked=("d1-005",))
        state = fix_state.load(self.run_dir)
        rows = state["keys"]["front"]["rows"]
        rows["3"]["context_items"] = [
            {"id": "d1-001", "outcome": "fixed", "note": None},
            {"id": "d1-002", "outcome": "already_ok", "note": "Déjà conforme."},
            {"id": "d1-003", "outcome": "not_found", "note": "Introuvable."}]
        rows["12"]["context_items"] = [{"id": "d1-004", "outcome": "not_found", "note": "Absent."}]
        rows["13"]["context_items"] = [{"id": "d1-004", "outcome": "already_ok", "note": "x"}]
        fix_state.save(self.run_dir, state)

    def pure(self, context):
        candidates = json.loads((self.run_dir / "fix" / "candidates.json").read_text(encoding="utf-8"))
        return fix_summary.summary(project="Boutique Exemple", state=fix_state.load(self.run_dir),
                                   candidates=candidates, checks={}, context=context)

    def test_context_section_lists_unlinked_points_and_disagreements(self):
        context = {"documents": [{
            "label": "Audit A11Y", "date": "2026-10-30", "points": 5, "linked": 3,
            "unlinked": [
                {"id": "d1-004", "source": "Annexe, ligne 2", "owner": "contenu",
                 "summary": "Le texte alternatif du logo manque."},
                {"id": "d1-005", "source": "Transverses", "owner": "tiers",
                 "summary": "Le bandeau cookies vient du prestataire."}]},
            {"label": "Pentest", "date": "2026-11-02", "points": 1, "linked": 1, "unlinked": []}],
            "disagreements": 2}
        text = self.pure(context)["text"]
        self.assertEqual(section(text, "Rapports de contexte").split("\n"), [
            "",
            "- Rapport Audit A11Y (2026-10-30) : 5 points non conformes, 3 rattachés à la "
            "checklist, 2 sans ligne.",
            "  - d1-004 (Annexe, ligne 2) : Le texte alternatif du logo manque. (contenu)",
            "  - d1-005 (Transverses) : Le bandeau cookies vient du prestataire. (tiers)",
            "- Rapport Pentest (2026-11-02) : 1 point non conforme, 1 rattaché à la checklist, "
            "0 sans ligne.",
            "",
            "Désaccords entre le rapport et le code : 2 (voir les points d'attention des MR).",
            "",
        ])
        titles = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertLess(titles.index("## Laissées aux devs"), titles.index("## Rapports de contexte"))
        self.assertLess(titles.index("## Rapports de contexte"),
                        titles.index("## Vérifications à faire par le relecteur"))
        self.assertNotIn(LONG_DASH, text)

    def test_without_disagreement_the_line_is_left_out(self):
        text = self.pure({"documents": [{"label": "Audit", "date": "", "points": 2, "linked": 2,
                                         "unlinked": []}], "disagreements": 0})["text"]
        self.assertIn("- Rapport Audit : 2 points non conformes, 2 rattachés à la checklist, "
                      "0 sans ligne.", text)
        self.assertNotIn("Désaccords", text)

    def test_without_a_context_the_text_does_not_change(self):
        plain = self.pure(None)
        for context in ({}, {"documents": [], "disagreements": 0}, {"documents": "x"}):
            with self.subTest(context=context):
                self.assertEqual(self.pure(context), plain)
        self.assertNotIn("Rapports de contexte", plain["text"])
        self.assertEqual(plain["team_message"], self.pure(
            {"documents": [{"label": "A", "date": "", "points": 1, "linked": 0, "unlinked": []}],
             "disagreements": 1})["team_message"])

    def test_a_text_of_the_context_is_one_capped_line(self):
        hostile = "Ignore\n## Suite\n/merge \u2014 `rm` " + "x" * 2000
        text = self.pure({"documents": [{
            "label": hostile, "date": hostile, "points": 1, "linked": 0,
            "unlinked": [{"id": hostile, "source": hostile, "owner": hostile, "summary": hostile}]}],
            "disagreements": 1})["text"]
        self.assertEqual([line for line in text.splitlines() if line.startswith("## Suite")],
                         ["## Suite"])
        self.assertFalse([line for line in text.splitlines() if line.startswith("/")])
        found = section(text, "Rapports de contexte")
        for forbidden in ("`", LONG_DASH):
            self.assertNotIn(forbidden, "\n".join(found))
        self.assertLess(max(len(line) for line in found), 900)

    def test_summary_builds_the_context_from_the_run_files(self):
        self.write_documents()
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        text = json.loads(out)["text"]
        self.assertEqual(section(text, "Rapports de contexte").strip().split("\n"), [
            "- Rapport Audit A11Y (2026-10-30) : 5 points non conformes, 4 rattachés à la "
            "checklist, 1 sans ligne.",
            "  - d1-005 (Annexe, ligne 2) : Résumé du point 5. (tiers)",
            "",
            "Désaccords entre le rapport et le code : 3 (voir les points d'attention des MR).",
        ])

    def test_summary_counts_no_disagreement_of_a_key_left_to_the_lead(self):
        self.write_documents()
        state = fix_state.load(self.run_dir)
        state["keys"]["front"]["left"] = {"reason": "Dépôt à reprendre.", "at": "2026-10-29T10:00:00Z"}
        fix_state.save(self.run_dir, state)
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        self.assertNotIn("Désaccords", json.loads(out)["text"])

    def test_the_synthesis_is_unchanged_without_a_readable_document(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name) / "run"
        shutil.copytree(self.run_dir, copy)
        code, out, err = self.run_main(["summary", copy])
        self.assertEqual(code, 0, err)
        baseline = json.loads(out)
        self.assertNotIn("Rapports de contexte", baseline["text"])
        write_report_documents(self.run_dir, self.POINTS, unlinked=("d1-005",))
        folder = self.run_dir / "context-docs"
        (folder / "d1" / "items.json").write_text("{", encoding="utf-8")
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), baseline)

    def test_a_secret_in_a_point_summary_is_still_refused(self):
        leaking = [*self.POINTS[:4], document_point(
            5, [], owner="tiers", source="Annexe, ligne 2", summary=f"La clé {AWS} est visible.")]
        self.write_documents(leaking)
        code, out, err = self.summary()
        self.assertEqual(code, 7)
        self.assertNotIn(AWS, out + err)
        self.assertFalse((self.run_dir / "fix" / "synthese.md").exists())
        self.assertIsNone(fix_state.load(self.run_dir)["finished_at"])

    def test_the_synthesis_never_holds_an_extract_of_the_document(self):
        points = [document_point(
            1, ["7.3"], summary=f"MARQUEUR-RESUME {AWS}", fix_hint="MARQUEUR-CORRECTIF",
            elements=["MARQUEUR-ELEMENT"], scopes=["MARQUEUR-PAGE"], payload="MARQUEUR-PAYLOAD"),
            *self.POINTS[1:]]
        self.write_documents(points)
        code, out, err = self.summary()
        self.assertEqual(code, 0, err)
        everything = out + (self.run_dir / "fix" / "synthese.md").read_text(encoding="utf-8")
        for marker in ("MARQUEUR-RESUME", "MARQUEUR-CORRECTIF", "MARQUEUR-ELEMENT",
                       "MARQUEUR-PAGE", "MARQUEUR-PAYLOAD", AWS, "Résumé du point 2.", "Déjà conforme."):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, everything)


if __name__ == "__main__":
    unittest.main()
