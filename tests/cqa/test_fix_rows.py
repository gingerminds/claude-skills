import tempfile
import unittest
from pathlib import Path

from cqa import fix_rows

TODO, PARTIAL = "À traiter", "Mise en place partielle"
AUTO, SEMI = "Auto : détection + MR", "Semi-auto : MR à valider"


def row(ref, *, status=TODO, automation=AUTO, category="SÉCURITÉ"):
    return {"ref": ref, "page_id": f"p{ref}", "title": f"Titre {ref}", "status": status,
            "assignees": ["Camille"], "category": category, "automation": automation}


def finding(ref, verdict="ko", repo="front", **extra):
    base = {"ref": ref, "verdict": verdict, "confidence": "high", "summary": f"Résumé {ref}",
            "evidence": [{"type": "file", "repo": repo, "path": "a.php"}] if repo else [],
            "todo": "Corriger", "human_check": None}
    base.update(extra)
    return base


def flight(**parts):
    base = {"open": [], "closed": [], "merged": [], "branches": [], "notes": []}
    base.update(parts)
    return base


REPOS = [{"name": "front"}]
OK = [{"repo": "front", "status": "ok"}]


def select(rows, findings, excluded=(), repos=REPOS, access=OK, flights=None, **kwargs):
    return fix_rows.select(rows, findings, list(excluded), repos, access,
                           flights if flights is not None else {"front": flight()}, **kwargs)


def left_reasons(result):
    return {entry["ref"]: entry["reason"] for entry in result["left"]}


def key_rows(key):
    return [item for section in key["sections"] for item in section["rows"]]


def kept_refs(result):
    return [item["ref"] for key in result["keys"] for item in key_rows(key)]


def sections(result):
    return {key["key"]: [(section["alias"], [item["ref"] for item in section["rows"]])
                         for section in key["sections"]] for key in result["keys"]}


class SelectTest(unittest.TestCase):
    def test_selects_open_fixable_rows_with_a_ko_or_partial_finding(self):
        rows = [
            row(1), row(2, status=PARTIAL, automation=SEMI),
            row(3, status="Mis en place"), row(4, status="Ok / Recetté"),
            row(5, status="Non applicable"), row(6, status="En standby"),
            row(7, automation="Détection seule"), row(8, automation="Manuel"),
            row(9, automation=None), row(10), row(11), row(12), row(13), row(14),
            row(15), row(16),
        ]
        findings = {n: finding(n) for n in (1, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15)}
        findings[2] = finding(2, "partial")
        findings[10] = finding(10, "unknown")
        findings[13] = finding(13, "ok")
        findings[14] = finding(14, "ok_human")
        findings[15] = finding(15, "na")
        excluded = [{"ref": 11, "reason": "no_tools"}, {"ref": 12, "reason": "no_fiche"}]
        result = select(rows, findings, excluded)
        self.assertEqual(kept_refs(result), [1, 2])
        first, second = key_rows(result["keys"][0])
        self.assertEqual((first["semi_auto"], second["semi_auto"]), (False, True))
        self.assertEqual(first["verdict"], "ko")
        self.assertEqual(first["summary"], "Résumé 1")
        self.assertEqual(first["todo"], "Corriger")
        self.assertEqual(first["assignees"], ["Camille"])
        self.assertFalse(first["lead"])
        unevaluated = "non évaluée par l'audit : lancer d'abord une relance"
        self.assertEqual(left_reasons(result), {
            7: "Détection seule", 8: "Manuel", 9: "Automatisation non renseignée",
            10: unevaluated, 11: unevaluated, 12: unevaluated, 16: unevaluated,
            13: "rien à corriger selon l'audit", 14: "rien à corriger selon l'audit",
            15: "rien à corriger selon l'audit",
        })
        self.assertTrue(all(entry["url"] is None for entry in result["left"]))

    def test_phase_run_lists_only_its_phase(self):
        rows = [row(1), row(2), row(3), row(4)]
        excluded = [{"ref": 2, "reason": "phase"}, {"ref": 3, "reason": "standby"},
                    {"ref": 4, "reason": "techno"}]
        result = select(rows, {1: finding(1)}, excluded)
        self.assertEqual(kept_refs(result), [1])
        self.assertEqual(result["left"], [])

    def test_candidates_skip_rows_in_open_mrs(self):
        url = "https://gitlab.example.org/acme/shop/-/merge_requests/12"
        flights = {"front": flight(
            open=[{"iid": 12, "url": url, "branch": "feature/checklist-qa-fix-securite",
                   "refs": [107]}],
            closed=[{"iid": 9, "url": "u9", "branch": "feature/checklist-qa-fix-seo", "refs": [40]}],
            branches=[{"branch": "feature/checklist-qa-fix-securite", "where": "local",
                       "refs": [3]}],
        )}
        rows = [row(107), row(40), row(3), row(5)]
        findings = {n: finding(n) for n in (107, 40, 3, 5)}
        result = select(rows, findings, flights=flights)
        self.assertEqual(kept_refs(result), [5])
        self.assertEqual(left_reasons(result), {
            107: "déjà dans la MR !12", 40: "MR !9 fermée sans fusion",
            3: "déjà sur la branche feature/checklist-qa-fix-securite",
        })
        urls = {entry["ref"]: entry["url"] for entry in result["left"]}
        self.assertEqual((urls[107], urls[40], urls[3]), (url, "u9", None))
        result = select(rows, findings, flights=flights, refs=(40,))
        self.assertIn(40, kept_refs(result))
        forced = [item for key in result["keys"] for item in key_rows(key) if item["ref"] == 40]
        self.assertTrue(forced[0]["lead"])

    def test_lead_refs_never_bypass_open_merged_or_branch_checks(self):
        branch = "feature/checklist-qa-fix-securite"
        cases = {
            "open": (flight(open=[{"iid": 12, "url": "u", "branch": branch, "refs": [107]}]),
                     "déjà dans la MR !12"),
            "r3": (flight(open=[{"iid": 14, "url": "u", "branch": branch, "refs": None}]),
                   "MR !14 ouverte, Réf illisibles"),
            "merged": (flight(merged=[{"iid": 7, "url": "u", "branch": branch, "refs": [107]}]),
                       "MR !7 déjà fusionnée"),
            "branch": (flight(branches=[{"branch": branch, "where": "local", "refs": [107]}]),
                       f"déjà sur la branche {branch}"),
        }
        for name, (case, reason) in cases.items():
            result = select([row(107)], {107: finding(107)}, flights={"front": case}, refs=(107,))
            self.assertEqual(kept_refs(result), [], name)
            self.assertEqual(left_reasons(result), {107: reason}, name)

    def test_lead_ref_excluded_from_the_run_is_noted(self):
        result = select([row(1)], {1: finding(1)}, [{"ref": 1, "reason": "phase"}], refs=(1,))
        self.assertEqual(kept_refs(result), [])
        self.assertIn("CQ-1 exclue de ce run (phase) : ignorée.", result["notes"])

    def test_open_mr_with_unreadable_refs_blocks_its_category(self):
        flights = {"front": flight(
            open=[{"iid": 14, "url": "u14", "branch": "feature/checklist-qa-fix-securite-2",
                   "refs": None}],
            notes=["Commits de la MR !14 illisibles : ses Réf sont inconnues."],
        )}
        rows = [row(1), row(2, category="SEO / GEO")]
        result = select(rows, {1: finding(1), 2: finding(2)}, flights=flights)
        self.assertEqual(kept_refs(result), [2])
        self.assertEqual(left_reasons(result), {1: "MR !14 ouverte, Réf illisibles"})
        self.assertEqual(result["left"][0]["url"], "u14")
        self.assertIn("Commits de la MR !14 illisibles : ses Réf sont inconnues.", result["notes"])
        self.assertEqual(result["flights"], flights)

    def test_flight_reasons_apply_only_to_their_repo(self):
        repos = [{"name": "back"}, {"name": "front"}]
        access = [{"repo": "back", "status": "ok"}, {"repo": "front", "status": "ok"}]
        flights = {"back": flight(open=[{"iid": 1, "url": "u", "refs": [1],
                                         "branch": "feature/checklist-qa-fix-securite"}]),
                   "front": flight()}
        result = select([row(1)], {1: finding(1, repo=None)}, repos=repos, access=access,
                        flights=flights)
        self.assertEqual([key["key"] for key in result["keys"]], ["front"])

    def test_rows_without_file_evidence_go_to_every_repo(self):
        repos = [{"name": "back"}, {"name": "front"}]
        access = [{"repo": "back", "status": "ok"}, {"repo": "front", "status": "ok"}]
        flights = {"back": flight(), "front": flight()}
        evidence = [{"type": "file", "repo": "front", "path": "a"},
                    {"type": "file", "repo": "back", "path": "b"}]
        findings = {1: finding(1, repo="front"), 2: finding(2, repo=None),
                    3: finding(3, evidence=evidence)}
        result = select([row(1), row(2), row(3)], findings, repos=repos, access=access,
                        flights=flights)
        self.assertEqual(sections(result), {"back": [("securite", [2, 3])],
                                            "front": [("securite", [1, 2, 3])]})
        flags = {r["ref"]: r["every_repo"] for key in result["keys"] for r in key_rows(key)}
        self.assertEqual(flags, {1: False, 2: True, 3: False})
        single = select([row(2)], {2: finding(2, repo=None)})
        self.assertFalse(key_rows(single["keys"][0])[0]["every_repo"])
        self.assertEqual(single["keys"][0]["key"], "front")

    def test_one_key_per_repo_with_ordered_sections(self):
        repos = [{"name": "back"}, {"name": "front"}]
        access = [{"repo": "back", "status": "no_push"}, {"repo": "front", "status": "ok"}]
        rows = [row(1, category="SEO / GEO"), row(2, category="QUALITÉ"),
                row(3, category="SÉCURITÉ"), row(4, category="SEO / GEO"),
                row(5, category="QUALITÉ"), row(6, category="RGPD & LÉGAL")]
        findings = {1: finding(1), 2: finding(2, repo=None), 3: finding(3), 4: finding(4),
                    5: finding(5, repo="back"), 6: finding(6, repo="back")}
        result = select(rows, findings, repos=repos, access=access,
                        flights={"back": flight(), "front": flight()})
        self.assertEqual(sections(result), {
            "back": [("qualite", [2, 5]), ("rgpd", [6])],
            "front": [("securite", [3]), ("seo", [1, 4]), ("qualite", [2])],
        })
        back, front = result["keys"]
        self.assertEqual({name: front[name] for name in ("key", "repo", "push_ready")},
                         {"key": "front", "repo": "front", "push_ready": True})
        self.assertEqual(set(front), {"key", "repo", "push_ready", "sections"})
        self.assertFalse(back["push_ready"])
        self.assertEqual(
            [(section["category"], section["alias"]) for section in front["sections"]],
            [("SÉCURITÉ", "securite"), ("SEO / GEO", "seo"), ("QUALITÉ", "qualite")],
        )
        self.assertEqual(set(front["sections"][0]), {"category", "alias", "rows"})
        self.assertEqual(front["sections"][1]["rows"][0]["category"], "SEO / GEO")

    def test_rows_outside_the_repo_are_left(self):
        rows = [row(1), row(2), row(3), row(4, automation="Manuel"), row(5), row(6)]
        findings = {1: finding(1, in_repo=False), 2: finding(2, in_repo=True), 3: finding(3),
                    4: finding(4, in_repo=False), 5: finding(5, "ok", in_repo=False),
                    6: finding(6, in_repo=False, todo=None)}
        result = select(rows, findings)
        self.assertEqual(kept_refs(result), [2, 3])
        self.assertEqual(left_reasons(result), {
            1: "hors du dépôt : Corriger", 4: "Manuel", 5: "rien à corriger selon l'audit",
            6: "hors du dépôt",
        })
        self.assertIsNone(result["left"][0]["url"])
        result = select(rows, findings, refs=(1,))
        self.assertEqual(kept_refs(result), [1, 2, 3])

    def test_left_rows_name_the_repos_of_their_evidence_and_rows_keep_their_status(self):
        """The MR of a repo lists in « Hors-scope » only the left rows that may concern it;
        each candidate row keeps the status of the copy, read at launch (status_before)."""
        repos = [{"name": "front"}, {"name": "back"}]
        access = [{"repo": "front", "status": "ok"}, {"repo": "back", "status": "not_gitlab"}]
        rows = [row(1, automation="Manuel"), row(2, automation="Manuel"), row(3), row(4),
                row(5, status=PARTIAL)]
        findings = {1: finding(1, repo="back"), 3: finding(3, repo="back"),
                    4: finding(4, repo="front"), 5: finding(5, repo="front")}
        result = select(rows, findings, repos=repos, access=access,
                        flights={"front": flight(), "back": flight()})
        self.assertEqual({entry["ref"]: (entry["reason"], entry["repos"]) for entry in result["left"]}, {
            1: ("Manuel", ["back"]), 2: ("Manuel", []), 3: ("repo hors GitLab", ["back"]),
        })
        self.assertEqual({item["ref"]: item["status"] for item in fix_rows.key_rows(result["keys"][0])},
                         {4: TODO, 5: PARTIAL})

    def test_candidates_skip_rows_of_a_v2_mr(self):
        url = "https://gitlab.example.org/acme/shop/-/merge_requests/318"
        flights = {"front": flight(
            open=[{"iid": 318, "url": url, "branch": "feature/checklist-qa-fix-securite",
                   "refs": [107]},
                  {"iid": 320, "url": "u320", "branch": "feature/checklist-qa-fix-seo-geo",
                   "refs": None}],
            branches=[{"branch": "feature/checklist-qa-fix-qualite", "where": "local",
                       "refs": [109]}],
        )}
        rows = [row(107), row(108), row(109, category="QUALITÉ"), row(110, category="QUALITÉ"),
                row(111, category="SEO / GEO")]
        findings = {n: finding(n) for n in (107, 108, 109, 110, 111)}
        result = select(rows, findings, flights=flights)
        self.assertEqual(sections(result), {"front": [("securite", [108]), ("qualite", [110])]})
        self.assertEqual(left_reasons(result), {
            107: "déjà dans la MR !318", 109: "déjà sur la branche feature/checklist-qa-fix-qualite",
            111: "MR !320 ouverte, Réf illisibles",
        })
        self.assertEqual(result["left"][0]["url"], url)

    def test_open_pass_mr_with_unreadable_refs_blocks_every_section_of_its_repo(self):
        repos = [{"name": "back"}, {"name": "front"}]
        access = [{"repo": "back", "status": "ok"}, {"repo": "front", "status": "ok"}]
        flights = {"back": flight(),
                   "front": flight(open=[{"iid": 21, "url": "u21", "refs": None,
                                          "branch": "feature/checklist-qa-fix-2"}])}
        rows = [row(1), row(2, category="QUALITÉ")]
        result = select(rows, {1: finding(1, repo=None), 2: finding(2)}, repos=repos,
                        access=access, flights=flights)
        self.assertEqual(sections(result), {"back": [("securite", [1])]})
        self.assertEqual(left_reasons(result), {2: "MR !21 ouverte, Réf illisibles"})

    def test_lead_can_add_and_remove_refs(self):
        rows = [row(1), row(2, automation="Détection seule")]
        findings = {1: finding(1), 2: finding(2)}
        result = select(rows, findings, refs=(2, 999))
        added = key_rows(result["keys"][0])[1]
        self.assertEqual((added["ref"], added["lead"]), (2, True))
        self.assertIn("CQ-999 ne figure pas dans la copie : ignorée.", result["notes"])
        result = select(rows, findings, without=(1,))
        self.assertEqual(kept_refs(result), [])
        self.assertNotIn(1, left_reasons(result))

    def test_repo_access_shapes_the_keys(self):
        for status, ready in (("no_push", False), ("unauthenticated", False),
                              ("unreachable", False), ("glab_missing", False), ("ok", True)):
            result = select([row(1)], {1: finding(1)}, access=[{"repo": "front", "status": status}])
            self.assertEqual(result["keys"][0]["push_ready"], ready, status)
        result = select([row(1)], {1: finding(1)},
                        access=[{"repo": "front", "status": "not_gitlab"}])
        self.assertEqual(result["keys"], [])
        self.assertEqual(left_reasons(result), {1: "repo hors GitLab"})

    def test_estimate_per_section(self):
        repos = [{"name": "back"}, {"name": "front"}]
        access = [{"repo": "back", "status": "ok"}, {"repo": "front", "status": "ok"}]
        rows = [row(1), row(2), row(3, category="SEO / GEO"), row(4, category="QUALITÉ")]
        findings = {1: finding(1), 2: finding(2), 3: finding(3), 4: finding(4, repo="back")}
        result = select(rows, findings, repos=repos, access=access,
                        flights={"back": flight(), "front": flight()})
        self.assertEqual(sections(result), {"back": [("qualite", [4])],
                                            "front": [("securite", [1, 2]), ("seo", [3])]})
        self.assertEqual(result["estimate_min"], 4 * 4 + 3 * 10)

    def test_fiche_path_only_when_the_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "fiches").mkdir()
            (Path(tmp) / "fiches" / "CQ-001.md").write_text("x", encoding="utf-8")
            result = select([row(1), row(2)], {1: finding(1), 2: finding(2)}, run_dir=Path(tmp))
        paths = [r["fiche_path"] for r in key_rows(result["keys"][0])]
        self.assertEqual(paths, ["fiches/CQ-001.md", None])


if __name__ == "__main__":
    unittest.main()
