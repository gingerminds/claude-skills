import unittest

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


def kept_refs(result):
    return [item["ref"] for key in result["keys"] for item in key["rows"]]


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
        first, second = result["keys"][0]["rows"]
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
        forced = [item for key in result["keys"] for item in key["rows"] if item["ref"] == 40]
        self.assertTrue(forced[0]["lead"])

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
        self.assertEqual([key["key"] for key in result["keys"]], ["front/securite"])

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
        by_key = {key["key"]: [r["ref"] for r in key["rows"]] for key in result["keys"]}
        self.assertEqual(by_key, {"back/securite": [2, 3], "front/securite": [1, 2, 3]})
        flags = {r["ref"]: r["every_repo"] for key in result["keys"] for r in key["rows"]}
        self.assertEqual(flags, {1: False, 2: True, 3: False})
        single = select([row(2)], {2: finding(2, repo=None)})
        self.assertFalse(single["keys"][0]["rows"][0]["every_repo"])
        self.assertEqual(single["keys"][0]["key"], "front/securite")

    def test_keys_order_security_first_then_copy_order(self):
        rows = [row(1, category="SEO / GEO"), row(2, category="QUALITÉ"),
                row(3, category="SÉCURITÉ"), row(4, category="SEO / GEO")]
        result = select(rows, {n: finding(n) for n in (1, 2, 3, 4)})
        self.assertEqual([key["key"] for key in result["keys"]],
                         ["front/securite", "front/seo-geo", "front/qualite"])
        key = result["keys"][1]
        self.assertEqual((key["repo"], key["category"], key["slug"]),
                         ("front", "SEO / GEO", "seo-geo"))
        self.assertEqual([r["ref"] for r in key["rows"]], [1, 4])

    def test_lead_can_add_and_remove_refs(self):
        rows = [row(1), row(2, automation="Détection seule")]
        findings = {1: finding(1), 2: finding(2)}
        result = select(rows, findings, refs=(2, 999))
        added = result["keys"][0]["rows"][1]
        self.assertEqual((added["ref"], added["lead"]), (2, True))
        self.assertIn("CQ-999 ne figure pas dans la copie : ignorée.", result["notes"])
        result = select(rows, findings, without=(1,))
        self.assertEqual(kept_refs(result), [])
        self.assertNotIn(1, left_reasons(result))

    def test_repo_access_shapes_the_keys(self):
        for status, ready in (("no_push", False), ("unauthenticated", False),
                              ("glab_missing", False), ("ok", True)):
            result = select([row(1)], {1: finding(1)}, access=[{"repo": "front", "status": status}])
            self.assertEqual(result["keys"][0]["push_ready"], ready, status)
        result = select([row(1)], {1: finding(1)},
                        access=[{"repo": "front", "status": "not_gitlab"}])
        self.assertEqual(result["keys"], [])
        self.assertEqual(left_reasons(result), {1: "repo hors GitLab"})

    def test_estimate(self):
        rows = [row(1), row(2), row(3, category="SEO / GEO")]
        result = select(rows, {n: finding(n) for n in (1, 2, 3)})
        self.assertEqual(result["estimate_min"], 3 * 4 + 2 * 10)

    def test_fiche_path_only_when_the_file_exists(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "fiches").mkdir()
            (Path(tmp) / "fiches" / "CQ-001.md").write_text("x", encoding="utf-8")
            result = select([row(1), row(2)], {1: finding(1), 2: finding(2)}, run_dir=Path(tmp))
        paths = [r["fiche_path"] for r in result["keys"][0]["rows"]]
        self.assertEqual(paths, ["fiches/CQ-001.md", None])


if __name__ == "__main__":
    unittest.main()
