import random
import tempfile
import unicodedata
import unittest
from pathlib import Path

from cqa import checklist
from cqa.lots import (
    MOBILE_TERMS,
    TOOL_ACTIONS,
    TOOLS,
    build_lots,
    coverage,
    how_to_test,
    lot_document_refs,
    lot_needs_session,
    select_rows,
)
from cqa.probes import _lot_entries

PROJECT = ["Laravel", "Nuxt"]
CODE_TOOLS = ["Code", "CLI local", "HTTP"]
HUMAN = "Intervention humaine"


def make_row(ref=1, **overrides):
    row = {
        "page_id": f"{ref:032x}",
        "ref": ref,
        "title": f"Task {ref}",
        "status": checklist.STATUS_TODO,
        "assignees": [],
        "category": "QUALITÉ",
        "subcategory": "Général",
        "blocking": False,
        "sections": ["Front"],
        "technos": [],
        "phases": ["Build"],
        "tools": ["Code"],
        "automation": None,
        "tooling_hint": "",
        "last_edited_by_bot": False,
        "reference_page_id": f"{ref + 1000:032x}",
        "fiche_path": f"fiches/CQ-{ref:03d}.md",
    }
    row.update(overrides)
    return row


def select(
    rows,
    technos=PROJECT,
    available=CODE_TOOLS,
    phase_filter=None,
    category_filter=None,
    document_refs=frozenset(),
):
    return select_rows(
        rows, technos, available, phase_filter, category_filter, document_refs=document_refs
    )


def lot_ids(built):
    return [lot["id"] for lot in built]


def lot_by_id(built, lot_id):
    return next(lot for lot in built if lot["id"] == lot_id)


class ConstantsTest(unittest.TestCase):
    def test_tools_match_checklist_options_in_order(self):
        self.assertEqual(TOOLS, list(checklist._TOOLS))
        self.assertEqual(len(TOOLS), 11)

    def test_every_tool_but_human_has_an_action(self):
        self.assertEqual(set(TOOL_ACTIONS), set(TOOLS) - {HUMAN})

    def test_actions_text(self):
        self.assertEqual(TOOL_ACTIONS["Audit web"], "docker login + pull de la boîte à outils")
        self.assertEqual(TOOL_ACTIONS["Crawler"], "boîte à outils 1.1 ou plus (docker pull)")
        self.assertEqual(TOOL_ACTIONS["SSH serveur"], "alias SSH vers la préprod")
        self.assertEqual(TOOL_ACTIONS["BDD"], "CLI de l'application accessible en SSH")
        self.assertEqual(TOOL_ACTIONS["Navigateur"], "Claude in Chrome connecté")
        self.assertEqual(
            TOOL_ACTIONS["HTTP"], "accès à la préprod (réseau de l'agence ou identifiants)"
        )
        self.assertEqual(
            TOOL_ACTIONS["CLI local"], "runner du projet démarré, ou boîte à outils 1.2 (docker pull)"
        )
        self.assertEqual(TOOL_ACTIONS["Comptes de test"], "comptes de test")
        self.assertEqual(TOOL_ACTIONS["Service externe"], "préprod publique (à J0)")
        self.assertEqual(TOOL_ACTIONS["Code"], "repos présents et à jour")


class SelectRowsTest(unittest.TestCase):
    def test_all_kept_when_nothing_excludes(self):
        rows = [make_row(2), make_row(1)]
        kept, excluded = select(rows)
        self.assertEqual(kept, rows)
        self.assertEqual(excluded, [])

    def test_standby_skipped(self):
        kept, excluded = select([make_row(5, status=checklist.STATUS_STANDBY)])
        self.assertEqual(kept, [])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["ref"], 5)
        self.assertEqual(excluded[0]["page_id"], f"{5:032x}")
        self.assertEqual(excluded[0]["reason"], "standby")
        self.assertEqual(excluded[0]["verdict"], "skip")

    def test_excluded_entry_keys(self):
        _, excluded = select([make_row(5, status=checklist.STATUS_STANDBY, tools=["Code", HUMAN])])
        self.assertEqual(
            sorted(excluded[0]), ["detail", "page_id", "reason", "ref", "tools", "verdict"]
        )
        self.assertEqual(excluded[0]["tools"], ["Code", HUMAN])

    def test_no_fiche_skipped(self):
        row = make_row(7)
        row["ref"] = None
        row.pop("fiche_path")
        kept, excluded = select([row])
        self.assertEqual(kept, [])
        self.assertEqual((excluded[0]["reason"], excluded[0]["verdict"]), ("no_fiche", "skip"))
        self.assertIsNone(excluded[0]["ref"])
        self.assertEqual(excluded[0]["page_id"], f"{7:032x}")

    def test_empty_fiche_path_counts_as_missing(self):
        _, excluded = select([make_row(7, fiche_path=None)])
        self.assertEqual(excluded[0]["reason"], "no_fiche")

    def test_techno_na_detail(self):
        kept, excluded = select([make_row(3, technos=["Wordpress"])])
        self.assertEqual(kept, [])
        self.assertEqual((excluded[0]["reason"], excluded[0]["verdict"]), ("techno", "na"))
        self.assertEqual(
            excluded[0]["detail"], "tâche propre à Wordpress, projet Laravel + Nuxt"
        )

    def test_techno_detail_lists_every_techno(self):
        _, excluded = select([make_row(3, technos=["Drupal", "Wordpress"])], technos=["Laravel"])
        self.assertEqual(
            excluded[0]["detail"], "tâche propre à Drupal + Wordpress, projet Laravel"
        )

    def test_techno_detail_without_project_technos(self):
        _, excluded = select([make_row(3, technos=["Wordpress"])], technos=[])
        self.assertEqual(excluded[0]["reason"], "techno")
        self.assertEqual(
            excluded[0]["detail"], "tâche propre à Wordpress, projet sans techno renseignée"
        )

    def test_techno_intersection_keeps_row(self):
        kept, excluded = select([make_row(3, technos=["Wordpress", "Nuxt"])])
        self.assertEqual(len(kept), 1)
        self.assertEqual(excluded, [])

    def test_row_without_techno_applies_to_every_project(self):
        kept, _ = select([make_row(3, technos=[])], technos=["Drupal"])
        self.assertEqual(len(kept), 1)

    def test_phase_deferred(self):
        kept, excluded = select(
            [
                make_row(1, phases=["MEL J0"]),
                make_row(2, phases=["Build", "MEL J0"]),
                make_row(3, phases=["MEL J0", "Post-MEL J+7"]),
                make_row(4, phases=[]),
                make_row(5, phases=["Post-MEL J+7"]),
            ]
        )
        self.assertEqual([row["ref"] for row in kept], [2, 4])
        self.assertEqual([entry["ref"] for entry in excluded], [1, 3, 5])
        self.assertEqual({entry["reason"] for entry in excluded}, {"phase"})
        self.assertEqual({entry["verdict"] for entry in excluded}, {"skip"})
        self.assertEqual(excluded[0]["detail"], "tâche reportée à MEL J0")
        self.assertEqual(excluded[1]["detail"], "tâche reportée à MEL J0 + Post-MEL J+7")

    def test_phase_filter(self):
        rows = [
            make_row(1, phases=["MEL J0"]),
            make_row(2, phases=["Build", "MEL J0"]),
            make_row(3, phases=["Build"]),
            make_row(4, phases=[]),
        ]
        kept, excluded = select(rows, phase_filter="MEL J0")
        self.assertEqual([row["ref"] for row in kept], [1, 2])
        self.assertEqual([entry["ref"] for entry in excluded], [3, 4])
        self.assertEqual({entry["reason"] for entry in excluded}, {"phase"})
        self.assertEqual({entry["verdict"] for entry in excluded}, {"skip"})
        self.assertEqual(excluded[0]["detail"], "tâche hors de la phase demandée (MEL J0)")

    def test_phase_filter_build_drops_deferred_only_rows(self):
        kept, excluded = select(
            [make_row(1, phases=["Build"]), make_row(2, phases=["MEL J0"])],
            phase_filter="Build",
        )
        self.assertEqual([row["ref"] for row in kept], [1])
        self.assertEqual([entry["ref"] for entry in excluded], [2])

    def test_no_tools_unknown(self):
        kept, excluded = select(
            [make_row(4, tools=["SSH serveur", "BDD"])], available=["Code"]
        )
        self.assertEqual(kept, [])
        self.assertEqual((excluded[0]["reason"], excluded[0]["verdict"]), ("no_tools", "unknown"))
        self.assertEqual(excluded[0]["detail"], "outils indisponibles : SSH serveur, BDD")
        self.assertEqual(excluded[0]["tools"], ["SSH serveur", "BDD"])

    def test_document_row_is_never_excluded_for_missing_tools(self):
        row = make_row(4, tools=["Navigateur"])
        kept, excluded = select([row], available=["Code"])
        self.assertEqual(kept, [])
        self.assertEqual(excluded[0]["reason"], "no_tools")
        kept, excluded = select([row], available=["Code"], document_refs=frozenset({4}))
        self.assertEqual(kept, [row])
        self.assertEqual(excluded, [])

    def test_a_document_verdict_only_spares_the_rows_that_have_one(self):
        rows = [make_row(4, tools=["Navigateur"]), make_row(5, tools=["Navigateur"])]
        kept, excluded = select(rows, available=["Code"], document_refs=frozenset({4}))
        self.assertEqual([row["ref"] for row in kept], [4])
        self.assertEqual([(entry["ref"], entry["reason"]) for entry in excluded], [(5, "no_tools")])

    def test_document_rule_comes_after_standby_techno_and_phase(self):
        refs = frozenset({1})
        cases = (
            ("standby", {"status": checklist.STATUS_STANDBY}),
            ("no_fiche", {"fiche_path": None}),
            ("techno", {"technos": ["Wordpress"]}),
            ("phase", {"phases": ["MEL J0"]}),
            ("category", {"category": "QUALITÉ"}),
        )
        for reason, changes in cases:
            with self.subTest(reason=reason):
                row = make_row(1, tools=["Navigateur"], **changes)
                kept, excluded = select(
                    [row],
                    available=["Code"],
                    document_refs=refs,
                    category_filter=["SÉCURITÉ"] if reason == "category" else None,
                )
                self.assertEqual(kept, [])
                self.assertEqual(excluded[0]["reason"], reason)

    def test_document_rule_keeps_the_row_through_a_phase_filter_it_passes(self):
        row = make_row(1, tools=["Navigateur"], phases=["Build"])
        kept, _ = select([row], available=["Code"], phase_filter="Build", document_refs=frozenset({1}))
        self.assertEqual(kept, [row])

    def test_some_available_tool_keeps_row(self):
        kept, excluded = select([make_row(4, tools=["Code", "BDD"])], available=["Code"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(excluded, [])

    def test_human_is_not_a_tool_for_availability(self):
        _, excluded = select([make_row(4, tools=["BDD", HUMAN])], available=[HUMAN])
        self.assertEqual(excluded[0]["reason"], "no_tools")
        self.assertEqual(excluded[0]["detail"], "outils indisponibles : BDD")

    def test_human_only_row_is_kept(self):
        kept, excluded = select([make_row(4, tools=[HUMAN])], available=[])
        self.assertEqual(len(kept), 1)
        self.assertEqual(excluded, [])

    def test_rule_order(self):
        base = {
            "fiche_path": None,
            "technos": ["Wordpress"],
            "phases": ["MEL J0"],
            "tools": ["BDD"],
        }
        row = make_row(1, status=checklist.STATUS_STANDBY, **base)
        self.assertEqual(select([row])[1][0]["reason"], "standby")
        row = make_row(1, **base)
        self.assertEqual(select([row])[1][0]["reason"], "no_fiche")
        base["fiche_path"] = "fiches/CQ-001.md"
        self.assertEqual(select([make_row(1, **base)])[1][0]["reason"], "techno")
        base["technos"] = []
        self.assertEqual(select([make_row(1, **base)])[1][0]["reason"], "phase")
        base["phases"] = ["Build"]
        self.assertEqual(select([make_row(1, **base)])[1][0]["reason"], "no_tools")

    def test_category_filter_is_the_first_rule(self):
        row = make_row(1, category="QUALITÉ", status=checklist.STATUS_STANDBY)
        kept, excluded = select([row], category_filter=["SÉCURITÉ"])
        self.assertEqual(kept, [])
        self.assertEqual(
            (excluded[0]["reason"], excluded[0]["verdict"]), ("category", "skip")
        )
        self.assertEqual(excluded[0]["detail"], "catégorie hors du périmètre (SÉCURITÉ)")
        self.assertEqual(excluded[0]["ref"], 1)
        self.assertEqual(excluded[0]["page_id"], row["page_id"])

    def test_category_detail_lists_every_name_of_the_filter(self):
        _, excluded = select([make_row(1)], category_filter=["SÉCURITÉ", "SEO / GEO"])
        self.assertEqual(excluded[0]["detail"], "catégorie hors du périmètre (SÉCURITÉ, SEO / GEO)")

    def test_category_filter_keeps_the_rows_of_its_categories(self):
        rows = [
            make_row(1, category="SÉCURITÉ"),
            make_row(2, category="QUALITÉ"),
            make_row(3, category="SEO / GEO"),
            make_row(4, category=None),
        ]
        kept, excluded = select(rows, category_filter=["SÉCURITÉ", "SEO / GEO"])
        self.assertEqual([row["ref"] for row in kept], [1, 3])
        self.assertEqual([entry["ref"] for entry in excluded], [2, 4])
        self.assertEqual({entry["reason"] for entry in excluded}, {"category"})

    def test_no_filter_and_an_empty_filter_keep_every_category(self):
        rows = [make_row(1, category="SÉCURITÉ"), make_row(2, category=None)]
        for category_filter in (None, []):
            with self.subTest(category_filter=category_filter):
                kept, excluded = select(rows, category_filter=category_filter)
                self.assertEqual(len(kept), 2)
                self.assertEqual(excluded, [])

    def test_a_row_out_of_scope_is_silent_whatever_else_applies(self):
        row = make_row(1, category="QUALITÉ", technos=["Wordpress"], tools=["BDD"], fiche_path=None)
        _, excluded = select([row], category_filter=["SÉCURITÉ"])
        self.assertEqual(excluded[0]["reason"], "category")

    def test_category_and_phase_filters_intersect(self):
        rows = [
            make_row(1, category="ACCESSIBILITÉ", phases=["Build"]),
            make_row(2, category="ACCESSIBILITÉ", phases=["MEL J0"]),
            make_row(3, category="QUALITÉ", phases=["Build"]),
        ]
        kept, excluded = select(
            rows, phase_filter="Build", category_filter=["ACCESSIBILITÉ"]
        )
        self.assertEqual([row["ref"] for row in kept], [1])
        self.assertEqual(
            [(entry["ref"], entry["reason"]) for entry in excluded],
            [(2, "phase"), (3, "category")],
        )

    def test_order_is_preserved_in_both_lists(self):
        rows = [
            make_row(9),
            make_row(3, status=checklist.STATUS_STANDBY),
            make_row(5),
            make_row(1, status=checklist.STATUS_STANDBY),
        ]
        kept, excluded = select(rows)
        self.assertEqual([row["ref"] for row in kept], [9, 5])
        self.assertEqual([entry["ref"] for entry in excluded], [3, 1])

    def test_inputs_are_not_modified(self):
        rows = [make_row(1), make_row(2, status=checklist.STATUS_STANDBY)]
        before = [dict(row) for row in rows]
        select(rows)
        self.assertEqual(rows, before)


class CoverageTest(unittest.TestCase):
    def run_coverage(self, rows, available=CODE_TOOLS, technos=PROJECT, phase_filter=None):
        kept, excluded = select(rows, technos, available, phase_filter)
        return coverage(kept, excluded, available)

    def test_coverage_and_gains(self):
        rows = [make_row(ref, tools=["Code", "Audit web"]) for ref in (1, 2, 3)]
        rows.append(make_row(4, tools=["Code"]))
        result = self.run_coverage(rows)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["evaluable"], 1)
        self.assertEqual(result["partial"], 3)
        self.assertEqual(
            result["gains"][0],
            {"tool": "Audit web", "action": TOOL_ACTIONS["Audit web"], "rows": 3},
        )

    def test_result_keys(self):
        result = self.run_coverage([])
        self.assertEqual(
            sorted(result),
            sorted(
                [
                    "total",
                    "evaluable",
                    "evaluable_human",
                    "partial",
                    "not_evaluable",
                    "deferred",
                    "na",
                    "standby",
                    "out_of_scope",
                    "gains",
                ]
            ),
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["gains"], [])

    def test_coverage_counts_out_of_scope_apart(self):
        rows = [
            make_row(1, category="SÉCURITÉ"),
            make_row(2, category="SÉCURITÉ", tools=["BDD"]),
            make_row(3, category="QUALITÉ"),
            make_row(4, category="QUALITÉ", status=checklist.STATUS_STANDBY),
        ]
        kept, excluded = select(rows, category_filter=["SÉCURITÉ"])
        result = coverage(kept, excluded, CODE_TOOLS)
        self.assertEqual((result["total"], result["out_of_scope"]), (2, 2))
        self.assertEqual((result["evaluable"], result["not_evaluable"], result["standby"]), (1, 1, 0))
        parts = ("evaluable", "partial", "not_evaluable", "deferred", "na", "standby")
        self.assertEqual(sum(result[key] for key in parts), result["total"])

    def test_out_of_scope_rows_bring_no_gain(self):
        rows = [
            make_row(1, category="SÉCURITÉ", tools=["Code", "BDD"]),
            make_row(2, category="QUALITÉ", tools=["BDD"]),
            make_row(3, category="QUALITÉ", tools=["Code", "Audit web"]),
        ]
        kept, excluded = select(rows, category_filter=["SÉCURITÉ"])
        gains = coverage(kept, excluded, CODE_TOOLS)["gains"]
        self.assertEqual(gains, [{"tool": "BDD", "action": TOOL_ACTIONS["BDD"], "rows": 1}])

    def test_coverage_without_filter_has_no_out_of_scope_row(self):
        result = self.run_coverage([make_row(1), make_row(2)])
        self.assertEqual((result["total"], result["out_of_scope"]), (2, 0))

    def test_each_bucket(self):
        rows = [
            make_row(1, tools=["Code"]),
            make_row(2, tools=["Code", HUMAN]),
            make_row(3, tools=["Code", "BDD"]),
            make_row(4, tools=["BDD"]),
            make_row(5, technos=["Wordpress"]),
            make_row(6, phases=["MEL J0"]),
            make_row(7, status=checklist.STATUS_STANDBY),
            make_row(8, tools=["HTTP"]),
        ]
        result = self.run_coverage(rows)
        self.assertEqual(result["total"], 8)
        self.assertEqual(result["evaluable"], 3)
        self.assertEqual(result["evaluable_human"], 1)
        self.assertEqual(result["partial"], 1)
        self.assertEqual(result["not_evaluable"], 1)
        self.assertEqual(result["na"], 1)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(result["standby"], 1)

    def test_evaluable_human_is_a_subset_of_evaluable(self):
        rows = [make_row(1, tools=["Code", HUMAN]), make_row(2, tools=["Code"])]
        result = self.run_coverage(rows)
        self.assertEqual((result["evaluable"], result["evaluable_human"]), (2, 1))

    def test_human_only_row_is_evaluable_human(self):
        result = self.run_coverage([make_row(1, tools=[HUMAN])], available=[])
        self.assertEqual((result["evaluable"], result["evaluable_human"]), (1, 1))
        self.assertEqual(result["not_evaluable"], 0)
        self.assertEqual(result["gains"], [])

    def test_human_row_with_missing_tool_is_partial_not_human(self):
        rows = [make_row(1, tools=["Code", "BDD", HUMAN])]
        result = self.run_coverage(rows)
        self.assertEqual(result["partial"], 1)
        self.assertEqual(result["evaluable_human"], 0)

    def test_buckets_add_up_to_total(self):
        rows = [
            make_row(1),
            make_row(2, tools=["Code", "BDD"]),
            make_row(3, tools=["BDD"]),
            make_row(4, technos=["Wordpress"]),
            make_row(5, phases=["MEL J0"]),
            make_row(6, status=checklist.STATUS_STANDBY),
        ]
        result = self.run_coverage(rows)
        parts = ("evaluable", "partial", "not_evaluable", "deferred", "na", "standby")
        self.assertEqual(sum(result[key] for key in parts), result["total"])

    def test_row_without_fiche_counts_in_total_only(self):
        rows = [make_row(1), make_row(2, fiche_path=None)]
        result = self.run_coverage(rows)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["evaluable"], 1)
        self.assertEqual(
            sum(result[key] for key in ("partial", "not_evaluable", "deferred", "na", "standby")), 0
        )

    def test_gains_count_kept_and_no_tools_rows_only(self):
        rows = [
            make_row(1, tools=["Code", "BDD"]),
            make_row(2, tools=["BDD"]),
            make_row(3, tools=["BDD"], technos=["Wordpress"]),
            make_row(4, tools=["BDD"], status=checklist.STATUS_STANDBY),
            make_row(5, tools=["BDD"], phases=["MEL J0"]),
        ]
        gains = self.run_coverage(rows)["gains"]
        self.assertEqual(gains, [{"tool": "BDD", "action": TOOL_ACTIONS["BDD"], "rows": 2}])

    def test_gains_are_unavailable_tools_sorted_by_rows(self):
        rows = [
            make_row(1, tools=["Code", "Crawler"]),
            make_row(2, tools=["Code", "BDD", "Crawler"]),
            make_row(3, tools=["Code", "Audit web", "Crawler", HUMAN]),
            make_row(4, tools=["Code", "Audit web"]),
            make_row(5, tools=["Code", "SSH serveur"]),
            make_row(6, tools=["Code", "HTTP"]),
        ]
        gains = self.run_coverage(rows)["gains"]
        self.assertEqual(
            [(gain["tool"], gain["rows"]) for gain in gains],
            [("Crawler", 3), ("Audit web", 2), ("SSH serveur", 1), ("BDD", 1)],
        )
        self.assertEqual(gains[0]["action"], TOOL_ACTIONS["Crawler"])

    def run_documents(self, rows, document_refs, available=CODE_TOOLS):
        kept, excluded = select(rows, PROJECT, available, None, None, document_refs)
        return coverage(kept, excluded, available, document_refs)

    def test_documents_count_the_kept_rows_judged_from_a_document(self):
        rows = [make_row(1), make_row(2), make_row(3, tools=["Navigateur"])]
        result = self.run_documents(rows, frozenset({1, 3, 99}))
        self.assertEqual(result["documents"], 2)

    def test_documents_is_absent_without_a_document_row(self):
        for refs in (frozenset(), frozenset({99})):
            with self.subTest(refs=refs):
                self.assertNotIn("documents", self.run_documents([make_row(1)], refs))
        self.assertNotIn("documents", self.run_coverage([make_row(1)]))

    def test_a_document_row_lacks_no_tool(self):
        rows = [
            make_row(1, tools=["Code", "BDD"]),
            make_row(2, tools=["Navigateur"]),
            make_row(3, tools=["Code", "BDD"]),
        ]
        result = self.run_documents(rows, frozenset({1, 2}))
        self.assertEqual((result["evaluable"], result["partial"]), (2, 1))
        self.assertEqual(result["gains"], [{"tool": "BDD", "action": TOOL_ACTIONS["BDD"], "rows": 1}])
        self.assertEqual(result["total"], 3)

    def test_the_buckets_still_add_up_with_document_rows(self):
        rows = [
            make_row(1),
            make_row(2, tools=["BDD"]),
            make_row(3, tools=["Navigateur"]),
            make_row(4, technos=["Wordpress"]),
            make_row(6, status=checklist.STATUS_STANDBY),
        ]
        result = self.run_documents(rows, frozenset({2, 3, 4, 6}))
        parts = ("evaluable", "partial", "not_evaluable", "deferred", "na", "standby")
        self.assertEqual(sum(result[key] for key in parts), result["total"])
        self.assertEqual(result["documents"], 2)

    def test_a_document_row_with_a_human_check_is_evaluable_with_a_human(self):
        result = self.run_documents([make_row(1, tools=["Navigateur", HUMAN])], frozenset({1}))
        self.assertEqual((result["evaluable"], result["evaluable_human"]), (1, 1))

    def test_gains_skip_available_tools_and_human(self):
        rows = [make_row(1, tools=["Code", "BDD", HUMAN])]
        gains = self.run_coverage(rows, available=["Code", "BDD"])["gains"]
        self.assertEqual(gains, [])

    def test_gains_do_not_double_count_a_repeated_tool(self):
        rows = [make_row(1, tools=["Code", "BDD", "BDD"])]
        self.assertEqual(self.run_coverage(rows)["gains"][0]["rows"], 1)

    def test_coverage_tolerates_excluded_entries_without_tools(self):
        excluded = [
            {"ref": 1, "page_id": "x", "reason": "no_tools", "verdict": "unknown", "detail": ""}
        ]
        result = coverage([], excluded, [])
        self.assertEqual(result["not_evaluable"], 1)
        self.assertEqual(result["gains"], [])


class DocumentRefsOfLotsTest(unittest.TestCase):
    def test_lot_carries_its_document_refs(self):
        rows = [make_row(ref, category="SÉCURITÉ") for ref in (1, 2, 3)]
        rows += [make_row(ref, category="PERFORMANCE") for ref in (4, 5)]
        built = build_lots(rows, document_refs=frozenset({2, 3, 5, 99}))
        self.assertEqual(lot_ids(built), ["code-securite", "code-performance"])
        self.assertEqual(lot_by_id(built, "code-securite")["document_refs"], [2, 3])
        self.assertEqual(lot_by_id(built, "code-performance")["document_refs"], [5])
        self.assertEqual(lot_by_id(built, "code-securite")["refs"], [1, 2, 3])

    def test_a_lot_without_a_document_row_has_an_empty_list(self):
        built = build_lots([make_row(1), make_row(2, tools=["Navigateur"])])
        self.assertEqual([lot["document_refs"] for lot in built], [[], []])
        self.assertEqual(
            [lot["document_refs"] for lot in build_lots([make_row(1)], document_refs=frozenset({7}))],
            [[]],
        )

    def test_document_refs_are_sorted_whatever_the_order_of_the_rows(self):
        rows = [make_row(ref) for ref in (9, 3, 6, 1)]
        built = build_lots(rows, document_refs=frozenset({9, 1, 6}))
        self.assertEqual(built[0]["document_refs"], [1, 6, 9])

    def test_the_toolbox_sibling_carries_its_own_document_rows(self):
        rows = [
            make_row(1, tools=["Code"]),
            make_row(2, tools=["Code", "Audit web"]),
            make_row(3, tools=["Code", "Audit web"]),
        ]
        built = build_lots(rows, document_refs=frozenset({1, 3}))
        self.assertEqual(lot_by_id(built, "code-qualite")["document_refs"], [1])
        self.assertEqual(lot_by_id(built, "code-qualite-outils")["document_refs"], [3])

    def test_a_split_category_gives_each_chunk_its_document_rows(self):
        rows = [make_row(ref, subcategory="Général") for ref in range(1, 31)]
        built = build_lots(rows, max_size=20, document_refs=frozenset({1, 30}))
        self.assertEqual(
            [(lot["id"], lot["document_refs"]) for lot in built],
            [("code-qualite-general-1", [1]), ("code-qualite-general-2", [30])],
        )

    def test_browser_and_server_lots_carry_them_too(self):
        rows = [
            make_row(1, tools=["Navigateur"], category="ACCESSIBILITÉ"),
            make_row(2, tools=["SSH serveur"]),
        ]
        built = build_lots(rows, document_refs=frozenset({1, 2}))
        self.assertEqual({lot["id"]: lot["document_refs"] for lot in built},
                         {"server": [2], "browser-accessibilite": [1]})

    def test_lot_document_refs_reads_the_list(self):
        self.assertEqual(lot_document_refs({"id": "x", "refs": [1, 2], "document_refs": [2]}), [2])

    def test_lot_document_refs_tolerates_an_older_lots_json(self):
        for lot in ({"id": "x", "refs": [1]}, {"document_refs": None}, {"document_refs": "2"}, {}):
            with self.subTest(lot=lot):
                self.assertEqual(lot_document_refs(lot), [])

    def test_lot_document_refs_keeps_only_whole_numbers(self):
        self.assertEqual(
            lot_document_refs({"document_refs": [3, True, "4", None, 1.5, 1]}), [1, 3]
        )

    def test_the_inputs_are_not_modified(self):
        rows = [make_row(1), make_row(2)]
        refs = frozenset({1})
        build_lots(rows, document_refs=refs)
        self.assertEqual([row["ref"] for row in rows], [1, 2])
        self.assertEqual(refs, frozenset({1}))


class BrowserLotsTest(unittest.TestCase):
    def test_browser_lots(self):
        rows = [
            make_row(ref, category="ACCESSIBILITÉ", tools=["Navigateur"])
            for ref in range(1, 26)
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["browser-accessibilite"])
        lot = built[0]
        self.assertEqual(lot["kind"], "browser")
        self.assertEqual(lot["refs"], list(range(1, 26)))
        self.assertFalse(lot["needs_toolbox"])
        self.assertFalse(lot["needs_server_facts"])

    def test_lot_keys(self):
        built = build_lots([make_row(1, tools=["Navigateur"])])
        self.assertEqual(
            sorted(built[0]),
            [
                "document_refs",
                "id",
                "kind",
                "needs_server_facts",
                "needs_session",
                "needs_toolbox",
                "refs",
            ],
        )

    def test_other_categories_share_one_lot_without_size_limit(self):
        categories = ["SÉCURITÉ", "SEO / GEO", "QUALITÉ"]
        rows = [
            make_row(ref, category=categories[ref % 3], tools=["Navigateur", "HTTP"])
            for ref in range(1, 61)
        ]
        built = build_lots(rows, max_size=20)
        self.assertEqual(lot_ids(built), ["browser-autres"])
        self.assertEqual(len(built[0]["refs"]), 60)

    def test_accessibility_split_from_other_browser_rows(self):
        rows = [
            make_row(1, category="ACCESSIBILITÉ", tools=["Navigateur"]),
            make_row(2, category="SEO / GEO", tools=["Navigateur"]),
            make_row(3, category="ACCESSIBILITÉ", tools=["Navigateur"]),
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["browser-accessibilite", "browser-autres"])
        self.assertEqual(built[0]["refs"], [1, 3])
        self.assertEqual(built[1]["refs"], [2])

    def test_accessibility_matches_decomposed_accent(self):
        decomposed = unicodedata.normalize("NFD", "ACCESSIBILITÉ")
        built = build_lots([make_row(1, category=decomposed, tools=["Navigateur"])])
        self.assertEqual(lot_ids(built), ["browser-accessibilite"])

    def test_needs_server_facts(self):
        rows = [
            make_row(1, category="SÉCURITÉ", tools=["Navigateur"]),
            make_row(2, category="SÉCURITÉ", tools=["Navigateur", "SSH serveur"]),
            make_row(3, category="ACCESSIBILITÉ", tools=["Navigateur"]),
            make_row(4, category="QUALITÉ", tools=["Navigateur", "BDD"]),
        ]
        built = build_lots(rows)
        self.assertTrue(lot_by_id(built, "browser-autres")["needs_server_facts"])
        self.assertFalse(lot_by_id(built, "browser-accessibilite")["needs_server_facts"])

    def test_needs_server_facts_false_without_server_rows(self):
        rows = [make_row(1, tools=["Navigateur", "HTTP"])]
        self.assertFalse(build_lots(rows)[0]["needs_server_facts"])

    def test_browser_wins_over_server(self):
        rows = [make_row(1, tools=["SSH serveur", "Navigateur"])]
        built = build_lots(rows)
        self.assertEqual((built[0]["id"], built[0]["kind"]), ("browser-autres", "browser"))


class ServerLotTest(unittest.TestCase):
    def test_single_server_list(self):
        rows = [
            make_row(1, category="SÉCURITÉ", tools=["SSH serveur"]),
            make_row(2, category="QUALITÉ", tools=["BDD", "Code"]),
            make_row(3, category="RGPD & LÉGAL", tools=["SSH serveur", "HTTP"]),
        ]
        built = build_lots(rows, max_size=1)
        self.assertEqual(lot_ids(built), ["server"])
        self.assertEqual(built[0]["kind"], "server")
        self.assertEqual(built[0]["refs"], [1, 2, 3])
        self.assertFalse(built[0]["needs_toolbox"])
        self.assertFalse(built[0]["needs_server_facts"])


class CodeLotsTest(unittest.TestCase):
    def test_one_lot_per_category(self):
        rows = [
            make_row(1, category="SÉCURITÉ"),
            make_row(2, category="QUALITÉ"),
            make_row(3, category="SÉCURITÉ"),
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-securite", "code-qualite"])
        self.assertEqual(built[0]["kind"], "code")
        self.assertEqual(built[0]["refs"], [1, 3])

    def test_slugs(self):
        rows = [
            make_row(1, category="ÉCO-CONCEPTION / WEBPERFORMANCE"),
            make_row(2, category="SEO / GEO"),
        ]
        self.assertEqual(
            lot_ids(build_lots(rows)),
            ["code-eco-conception-webperformance", "code-seo-geo"],
        )

    def test_group_of_max_size_is_not_split(self):
        rows = [make_row(ref, subcategory=f"Sub {ref % 3}") for ref in range(1, 21)]
        self.assertEqual(lot_ids(build_lots(rows)), ["code-qualite"])

    def test_code_split_by_subcategory(self):
        rows = [
            make_row(ref, subcategory="Qualité du code" if ref <= 11 else "Tests")
            for ref in range(1, 23)
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-qualite-qualite-du-code", "code-qualite-tests"])
        self.assertEqual(built[0]["refs"], list(range(1, 12)))
        self.assertEqual(built[1]["refs"], list(range(12, 23)))

    def test_subgroup_too_large_is_numbered(self):
        rows = [make_row(ref, subcategory="Tests") for ref in range(1, 46)]
        built = build_lots(rows)
        self.assertEqual(
            lot_ids(built),
            ["code-qualite-tests-1", "code-qualite-tests-2", "code-qualite-tests-3"],
        )
        self.assertEqual([len(lot["refs"]) for lot in built], [15, 15, 15])
        self.assertEqual(
            [ref for lot in built for ref in lot["refs"]], list(range(1, 46))
        )

    def test_numbered_chunks_never_exceed_max_size(self):
        rows = [make_row(ref, subcategory="Tests") for ref in range(1, 45)]
        built = build_lots(rows, max_size=7)
        self.assertEqual(len(built), 7)
        self.assertLessEqual(max(len(lot["refs"]) for lot in built), 7)
        self.assertEqual(sum(len(lot["refs"]) for lot in built), 44)
        self.assertEqual(lot_ids(built)[-1], "code-qualite-tests-7")

    def test_small_subgroups_next_to_a_large_one(self):
        rows = [make_row(ref, subcategory="Tests") for ref in range(1, 26)]
        rows.append(make_row(26, subcategory="Docs"))
        built = build_lots(rows)
        self.assertEqual(
            lot_ids(built),
            ["code-qualite-tests-1", "code-qualite-tests-2", "code-qualite-docs"],
        )

    def test_custom_max_size(self):
        rows = [make_row(ref, subcategory="A" if ref % 2 else "B") for ref in range(1, 7)]
        built = build_lots(rows, max_size=5)
        self.assertEqual(lot_ids(built), ["code-qualite-a", "code-qualite-b"])

    def test_process_rgpd_merged(self):
        rows = [
            make_row(1, category="PROCESS INTERNE"),
            make_row(2, category="RGPD & LÉGAL"),
            make_row(3, category="PROCESS INTERNE"),
            make_row(4, category="SÉCURITÉ"),
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-process-rgpd", "code-securite"])
        self.assertEqual(built[0]["refs"], [1, 2, 3])

    def test_process_rgpd_split_uses_merged_name(self):
        rows = [
            make_row(ref, category="PROCESS INTERNE" if ref % 2 else "RGPD & LÉGAL",
                     subcategory="Alpha" if ref <= 12 else "Beta")
            for ref in range(1, 25)
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-process-rgpd-alpha", "code-process-rgpd-beta"])

    def test_missing_category_and_subcategory(self):
        rows = [make_row(1, category=None)]
        self.assertEqual(lot_ids(build_lots(rows)), ["code-sans-categorie"])
        rows = [make_row(ref, subcategory=None) for ref in range(1, 23)]
        self.assertEqual(
            lot_ids(build_lots(rows)),
            ["code-qualite-sans-sous-categorie-1", "code-qualite-sans-sous-categorie-2"],
        )

    def test_row_order_does_not_change_lots(self):
        rows = [make_row(ref, subcategory="Tests") for ref in range(1, 46)]
        shuffled = list(rows)
        random.Random(7).shuffle(shuffled)
        self.assertEqual(build_lots(shuffled), build_lots(rows))


class ToolboxSiblingTest(unittest.TestCase):
    def test_outils_sibling(self):
        rows = [
            make_row(1, category="SEO / GEO", tools=["Code"]),
            make_row(2, category="SEO / GEO", tools=["Code", "Crawler"]),
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-seo-geo", "code-seo-geo-outils"])
        self.assertEqual(built[0]["refs"], [1])
        self.assertFalse(built[0]["needs_toolbox"])
        self.assertEqual(built[1]["refs"], [2])
        self.assertTrue(built[1]["needs_toolbox"])
        self.assertEqual(built[1]["kind"], "code")

    def test_audit_web_also_moves_and_empty_origin_is_dropped(self):
        rows = [
            make_row(1, category="SEO / GEO", tools=["Audit web"]),
            make_row(2, category="SEO / GEO", tools=["Crawler", "HTTP"]),
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-seo-geo-outils"])
        self.assertEqual(built[0]["refs"], [1, 2])
        self.assertTrue(built[0]["needs_toolbox"])

    def test_no_sibling_without_toolbox_rows(self):
        built = build_lots([make_row(1), make_row(2, category="SÉCURITÉ")])
        self.assertFalse(any(lot["needs_toolbox"] for lot in built))
        self.assertFalse(any(lot["id"].endswith("-outils") for lot in built))

    def test_browser_and_server_siblings(self):
        rows = [
            make_row(1, category="ACCESSIBILITÉ", tools=["Navigateur"]),
            make_row(2, category="ACCESSIBILITÉ", tools=["Navigateur", "Audit web"]),
            make_row(3, category="SÉCURITÉ", tools=["SSH serveur", "Audit web"]),
            make_row(4, category="SÉCURITÉ", tools=["SSH serveur"]),
            make_row(5, category="SÉCURITÉ", tools=["Navigateur", "SSH serveur", "Crawler"]),
        ]
        built = build_lots(rows)
        self.assertEqual(
            lot_ids(built),
            [
                "server",
                "browser-accessibilite",
                "server-outils",
                "browser-accessibilite-outils",
                "browser-autres-outils",
            ],
        )
        self.assertEqual(lot_by_id(built, "server-outils")["kind"], "server")
        self.assertEqual(lot_by_id(built, "server-outils")["refs"], [3])
        self.assertEqual(lot_by_id(built, "browser-accessibilite-outils")["refs"], [2])
        self.assertTrue(lot_by_id(built, "browser-autres-outils")["needs_server_facts"])
        self.assertFalse(lot_by_id(built, "browser-accessibilite-outils")["needs_server_facts"])
        self.assertTrue(lot_by_id(built, "browser-autres-outils")["needs_toolbox"])

    def test_split_counts_the_whole_group_before_toolbox_rows_move(self):
        rows = [make_row(ref, tools=["Code", "Crawler"]) for ref in range(1, 6)]
        rows += [make_row(ref, tools=["Code"]) for ref in range(6, 22)]
        built = build_lots(rows, max_size=20)
        self.assertEqual(
            lot_ids(built),
            ["code-qualite-general-1", "code-qualite-general-2", "code-qualite-general-1-outils"],
        )
        self.assertEqual([len(lot["refs"]) for lot in built], [6, 10, 5])


class LotOrderTest(unittest.TestCase):
    def test_lot_order(self):
        rows = [
            make_row(1, category="ACCESSIBILITÉ", tools=["Navigateur"]),
            make_row(2, category="SEO / GEO", tools=["Code", "Crawler"]),
            make_row(3, category="SÉCURITÉ", tools=["Code"]),
            make_row(4, category="SÉCURITÉ", tools=["SSH serveur"]),
            make_row(5, category="SÉCURITÉ", tools=["Navigateur"]),
            make_row(6, category="PROCESS INTERNE", tools=[HUMAN]),
            make_row(7, category="ACCESSIBILITÉ", tools=["Navigateur", "Audit web"]),
        ]
        built = build_lots(rows)
        self.assertEqual(
            lot_ids(built),
            [
                "server",
                "code-securite",
                "code-process-rgpd",
                "browser-accessibilite",
                "browser-autres",
                "code-seo-geo-outils",
                "browser-accessibilite-outils",
            ],
        )

    def test_empty_input(self):
        self.assertEqual(build_lots([]), [])

    def test_every_ref_lands_in_exactly_one_lot(self):
        categories = ["SÉCURITÉ", "SEO / GEO", "QUALITÉ", "ACCESSIBILITÉ", "RGPD & LÉGAL"]
        tool_sets = [
            ["Code"],
            ["Navigateur"],
            ["BDD"],
            ["Code", "Crawler"],
            ["Navigateur", "Audit web"],
        ]
        rng = random.Random(3)
        rows = [
            make_row(
                ref,
                category=rng.choice(categories),
                subcategory=rng.choice(["A", "B", "C"]),
                tools=rng.choice(tool_sets),
            )
            for ref in range(1, 141)
        ]
        built = build_lots(rows, max_size=8)
        refs = [ref for lot in built for ref in lot["refs"]]
        self.assertEqual(sorted(refs), list(range(1, 141)))
        self.assertEqual(len(set(lot_ids(built))), len(built))
        for lot in built:
            self.assertTrue(lot["refs"])
            if lot["kind"] == "code":
                self.assertLessEqual(len(lot["refs"]), 8)


class SessionLotsTest(unittest.TestCase):
    def test_session_rows_get_their_own_browser_lot(self):
        rows = [
            make_row(1, category="ACCESSIBILITÉ", tools=["Navigateur"], sections=["Front"]),
            make_row(2, category="ACCESSIBILITÉ", tools=["Navigateur"], sections=["Back-office"]),
            make_row(3, category="SEO / GEO", tools=["Navigateur", "Comptes de test"], sections=["Front"]),
            make_row(4, category="SEO / GEO", tools=["Navigateur"], sections=["bo"]),
            make_row(5, category="SEO / GEO", tools=["Navigateur"], sections=["Front", "BACK"]),
            make_row(6, category="SEO / GEO", tools=["Navigateur"], sections=["Backup front"]),
            make_row(7, category="SEO / GEO", tools=["Navigateur"], sections=["Boutique"]),
        ]
        built = build_lots(rows)
        self.assertEqual(
            lot_ids(built),
            [
                "browser-accessibilite",
                "browser-autres",
                "browser-accessibilite-session",
                "browser-autres-session",
            ],
        )
        self.assertEqual(lot_by_id(built, "browser-accessibilite")["refs"], [1])
        self.assertEqual(lot_by_id(built, "browser-accessibilite-session")["refs"], [2])
        self.assertEqual(lot_by_id(built, "browser-autres")["refs"], [7])
        self.assertEqual(lot_by_id(built, "browser-autres-session")["refs"], [3, 4, 5, 6])
        self.assertEqual(
            [lot["needs_session"] for lot in built], [False, False, True, True]
        )

    def test_session_toolbox_row_keeps_needs_session(self):
        rows = [make_row(1, tools=["Navigateur", "Audit web"], sections=["Back"])]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["browser-autres-session-outils"])
        self.assertTrue(built[0]["needs_session"])
        self.assertTrue(built[0]["needs_toolbox"])

    def test_non_browser_lots_never_need_a_session(self):
        rows = [make_row(1, tools=["Code", "Comptes de test"], sections=["Back"])]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-qualite"])
        self.assertFalse(built[0]["needs_session"])

    def test_tooling_hint_moves_rows_to_outils_lots(self):
        rows = [
            make_row(109, category="SÉCURITÉ", tooling_hint="Gitleaks sur le dépôt"),
            make_row(110, category="SÉCURITÉ", tooling_hint="relecture du code"),
            make_row(111, category="SÉCURITÉ", tooling_hint="Yellow Lab + axe-core"),
            make_row(112, category="SÉCURITÉ", tooling_hint=""),
        ]
        built = build_lots(rows)
        self.assertEqual(lot_ids(built), ["code-securite", "code-securite-outils"])
        self.assertEqual(built[0]["refs"], [110, 112])
        self.assertEqual(built[1]["refs"], [109, 111])
        self.assertTrue(built[1]["needs_toolbox"])

    def test_old_lots_json_without_needs_session_is_read(self):
        old = {"lots": [{"id": "browser-autres", "kind": "browser", "needs_toolbox": False, "refs": [1]}]}
        entries = _lot_entries(old)
        self.assertEqual([lot_needs_session(lot) for lot in entries], [False])
        self.assertTrue(lot_needs_session({"needs_session": True}))

    def test_hint_matches_tool_names_not_substrings(self):
        for hint in ("syntaxe PHP", "taxe", "relaxe", "axes de recherche", "menu html"):
            with self.subTest(hint=hint):
                built = build_lots([make_row(1, tooling_hint=hint)])
                self.assertEqual(lot_ids(built), ["code-qualite"])
        for hint in (
            "axe-core", "axe DevTools", "Nu HTML Checker", "Yellow Lab", "yellowlab", "crawler"
        ):
            with self.subTest(hint=hint):
                built = build_lots([make_row(1, tooling_hint=hint)])
                self.assertEqual(lot_ids(built), ["code-qualite-outils"])

    def test_lot_c_rows_go_to_outils_lots(self):
        for hint in (
            "Stylelint (max-nesting-depth)",
            "composer audit, npm audit",
            "détection des débordements horizontaux",
            "zoom 200 %, détection des débordements",
            "Débordement à 320 px",
        ):
            with self.subTest(hint=hint):
                built = build_lots([make_row(1, tooling_hint=hint)])
                self.assertEqual(lot_ids(built), ["code-qualite-outils"])
        for hint in ("Navigateur piloté (onglet Coverage)", "audit", "audit de code", "relecture"):
            with self.subTest(hint=hint):
                built = build_lots([make_row(1, tooling_hint=hint)])
                self.assertEqual(lot_ids(built), ["code-qualite"])


def fiche_text(how, todo="Une exigence.", before=""):
    """A fiche as the Notion export writes it: a title, then the sections."""
    return (
        f"# Titre de la ligne\n\n{before}## Comment tester\n\n{how}\n\n"
        f"## À faire\n\n{todo}\n\n## Par techno\n\nRien.\n"
    )


class FicheCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        (self.run_dir / "fiches").mkdir()

    def write_fiche(self, ref, text):
        path = self.run_dir / "fiches" / f"CQ-{ref:03d}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def routed(self, row, text=None, **kwargs):
        """The ids of the lots when the fiche of the row says text (no fiche when None)."""
        if text is not None:
            self.write_fiche(row["ref"], text)
        return lot_ids(build_lots([row], run_dir=self.run_dir, **kwargs))


class MobileTermsTest(FicheCase):
    def test_mobile_terms_are_the_closed_list(self):
        self.assertEqual(
            MOBILE_TERMS,
            (
                "320",
                "paysage",
                "landscape",
                "échap",
                "escape",
                "burger",
                "menu mobile",
                "tactile",
                "piège",
                "focus trap",
                "inert",
                "aria-expanded",
            ),
        )

    def test_each_mobile_term_in_the_fiche_routes_the_row_to_the_outils_lot(self):
        for term in MOBILE_TERMS:
            for text in (
                f"Ouvrir la page puis vérifier : {term}.",
                f"{term.upper()}",
                f"Vérifier ({term}) sur la page.",
                f"Le test\nde {term}\nrestant à faire.",
            ):
                with self.subTest(term=term, text=text):
                    self.write_fiche(1, fiche_text(text))
                    built = build_lots([make_row(1)], run_dir=self.run_dir)
                    self.assertEqual(lot_ids(built), ["code-qualite-outils"])
                    self.assertTrue(built[0]["needs_toolbox"])

    def test_terms_are_read_without_case_and_without_accent(self):
        for text in (
            "ECHAP", "echap", "Échap", "ÉCHAP", "touche Echap", "PIEGE de focus", "piege",
            "Piège", "Menu  Mobile", "menu\u00a0mobile", "Focus\nTrap", "ARIA-EXPANDED",
            "Aria-Expanded", "en PAYSAGE", "Landscape", "TACTILE", "Burger", "INERT",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    self.routed(make_row(1), fiche_text(text)), ["code-qualite-outils"]
                )

    def test_each_term_has_word_boundaries(self):
        for text in (
            "Tab", "clavier", "focus", "échappement des sorties", "Échappement", "3200",
            "13200 pixels", "1320", "inertie du scroll", "escaped", "tactiles", "paysages",
            "landscaped", "burgers", "x320", "xaria-expanded", "focus traps", "menu mobiles",
            "1,320 euros", "1.320", "320,5", "320.5 px", "pièges",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.routed(make_row(1), fiche_text(text)), ["code-qualite"])

    def test_a_width_written_with_its_unit_still_routes(self):
        for text in ("à 320px de large", "320 px", "320×640", "(320)", "de 320 à 1920", "320."):
            with self.subTest(text=text):
                self.assertEqual(
                    self.routed(make_row(1), fiche_text(text)), ["code-qualite-outils"]
                )

    def test_common_words_do_not_route(self):
        for text in (
            "Parcourir la page à la touche Tab puis au clavier, vérifier le focus visible.",
            "échappement des sorties",
            "3200",
            "Tab, clavier, focus",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.routed(make_row(1), fiche_text(text)), ["code-qualite"])

    def test_only_the_comment_tester_section_counts(self):
        text = (
            "# Titre\n\nIntro sur l'Échap.\n\n## Comment tester\n\nRequêter la page.\n\n"
            "## À faire\n\nGérer Échap, aria-expanded et 320 px.\n\n## Par techno\n\nburger.\n"
        )
        self.assertEqual(self.routed(make_row(1), text), ["code-qualite"])

    def test_a_subsection_of_comment_tester_counts(self):
        text = fiche_text("Intro.\n\n### Mobile\n\nTester à 320 px.")
        self.assertEqual(self.routed(make_row(1), text), ["code-qualite-outils"])

    def test_the_last_section_counts_too(self):
        text = "# Titre\n\n## À faire\n\nx\n\n## Comment tester\n\nEn paysage."
        self.assertEqual(self.routed(make_row(1), text), ["code-qualite-outils"])

    def test_tooling_hint_alone_routes(self):
        row = make_row(1, tooling_hint="Navigateur piloté (parcours Tab, touche Échap)")
        self.assertEqual(lot_ids(build_lots([row])), ["code-qualite-outils"])
        self.assertEqual(self.routed(row), ["code-qualite-outils"])
        row = make_row(2, tooling_hint="Navigateur piloté (parcours Tab)")
        self.assertEqual(lot_ids(build_lots([row])), ["code-qualite"])

    def test_the_hint_and_the_fiche_are_two_signals(self):
        row = make_row(1, tooling_hint="relecture du code")
        self.assertEqual(self.routed(row, fiche_text("En 320 px")), ["code-qualite-outils"])
        row = make_row(2, tooling_hint="Navigateur piloté, Échap")
        self.assertEqual(self.routed(row, fiche_text("Requêter la page")), ["code-qualite-outils"])

    def test_without_a_run_dir_the_fiches_are_not_read(self):
        row = make_row(1)
        self.write_fiche(1, fiche_text("En 320 px"))
        self.assertEqual(lot_ids(build_lots([row])), ["code-qualite"])
        self.assertEqual(lot_ids(build_lots([row], run_dir=None)), ["code-qualite"])
        self.assertEqual(lot_ids(build_lots([row], run_dir=self.run_dir)), ["code-qualite-outils"])

    def test_a_missing_or_broken_fiche_never_raises_and_routes_nothing(self):
        rows = [
            make_row(1),
            make_row(2, fiche_path=None),
            make_row(3, fiche_path=""),
            make_row(4, fiche_path="fiches/CQ-404.md"),
            make_row(5),
            make_row(6),
        ]
        self.write_fiche(1, fiche_text("Requêter la page."))
        self.write_fiche(5, "# Titre\n\nSans section, mais 320 et Échap.\n")
        (self.run_dir / "fiches" / "CQ-006.md").write_bytes(b"\xff\xfe## Comment tester\n320\n")
        built = build_lots(rows, run_dir=self.run_dir)
        self.assertEqual(lot_ids(built), ["code-qualite"])
        self.assertEqual(built[0]["refs"], [1, 2, 3, 4, 5, 6])

    def test_a_row_routed_by_its_fiche_follows_the_rules_of_the_lot_it_leaves(self):
        rows = [
            make_row(1, category="ACCESSIBILITÉ", tools=["Navigateur"]),
            make_row(2, category="ACCESSIBILITÉ", tools=["Navigateur"], sections=["Back"]),
            make_row(3, category="ACCESSIBILITÉ", tools=["Navigateur"]),
            make_row(4, category="SÉCURITÉ", tools=["SSH serveur"]),
            make_row(5, category="SÉCURITÉ", tools=["Code"]),
        ]
        for ref in (1, 2, 4, 5):
            self.write_fiche(ref, fiche_text("Tester à 320 px."))
        self.write_fiche(3, fiche_text("Lire le titre."))
        built = build_lots(rows, run_dir=self.run_dir)
        self.assertEqual(
            lot_ids(built),
            [
                "browser-accessibilite",
                "server-outils",
                "code-securite-outils",
                "browser-accessibilite-outils",
                "browser-accessibilite-session-outils",
            ],
        )
        by_id = {lot["id"]: lot for lot in built}
        self.assertEqual(by_id["browser-accessibilite"]["refs"], [3])
        self.assertFalse(by_id["browser-accessibilite"]["needs_toolbox"])
        self.assertEqual(by_id["browser-accessibilite-outils"]["refs"], [1])
        self.assertTrue(by_id["browser-accessibilite-outils"]["needs_toolbox"])
        self.assertFalse(by_id["browser-accessibilite-outils"]["needs_session"])
        self.assertEqual(by_id["browser-accessibilite-session-outils"]["refs"], [2])
        self.assertTrue(by_id["browser-accessibilite-session-outils"]["needs_session"])
        self.assertEqual(by_id["server-outils"]["refs"], [4])
        self.assertEqual(by_id["server-outils"]["kind"], "server")
        self.assertEqual(by_id["code-securite-outils"]["refs"], [5])

    def test_the_split_of_a_big_group_counts_the_rows_before_they_move(self):
        rows = [make_row(ref) for ref in range(1, 22)]
        for ref in range(1, 6):
            self.write_fiche(ref, fiche_text("En paysage."))
        built = build_lots(rows, max_size=20, run_dir=self.run_dir)
        self.assertEqual(
            lot_ids(built),
            ["code-qualite-general-1", "code-qualite-general-2", "code-qualite-general-1-outils"],
        )
        self.assertEqual([len(lot["refs"]) for lot in built], [6, 10, 5])

    def test_the_document_refs_stay_with_their_rows(self):
        rows = [make_row(1), make_row(2)]
        self.write_fiche(1, fiche_text("En 320 px"))
        built = build_lots(rows, document_refs=frozenset({1}), run_dir=self.run_dir)
        self.assertEqual(lot_ids(built), ["code-qualite", "code-qualite-outils"])
        self.assertEqual(lot_by_id(built, "code-qualite-outils")["document_refs"], [1])
        self.assertEqual(lot_by_id(built, "code-qualite")["document_refs"], [])

    def test_the_inputs_are_not_modified(self):
        rows = [make_row(1, tooling_hint="Échap"), make_row(2)]
        self.write_fiche(2, fiche_text("En 320 px"))
        before = [dict(row) for row in rows]
        build_lots(rows, run_dir=self.run_dir)
        self.assertEqual(rows, before)


class HowToTestTest(FicheCase):
    def test_it_returns_the_section_up_to_the_next_title(self):
        self.write_fiche(1, fiche_text("Requêter `/` puis lire l'en-tête."))
        self.assertEqual(
            how_to_test(self.run_dir, make_row(1)), "Requêter `/` puis lire l'en-tête."
        )

    def test_it_keeps_the_subsections_and_stops_at_a_level_one_title(self):
        text = (
            "# Titre\n\n## Comment tester\n\nA.\n\n### Détail\n\nB.\n\n"
            "# Autre titre\n\nC.\n\n## À faire\n\nD.\n"
        )
        self.write_fiche(1, text)
        self.assertEqual(how_to_test(self.run_dir, make_row(1)), "A.\n\n### Détail\n\nB.")

    def test_the_title_is_read_without_case_and_with_spaces(self):
        for title in ("## comment tester", "##   Comment   Tester  ", "## COMMENT TESTER"):
            with self.subTest(title=title):
                self.write_fiche(1, f"# T\n\n{title}\n\nA.\n\n## B\n\nZ.\n")
                self.assertEqual(how_to_test(self.run_dir, make_row(1)), "A.")

    def test_a_title_in_a_code_block_does_not_end_the_section(self):
        text = "# T\n\n## Comment tester\n\nA.\n\n```\n## not a title\n```\n\nB.\n\n## C\n\nZ.\n"
        self.write_fiche(1, text)
        self.assertEqual(
            how_to_test(self.run_dir, make_row(1)), "A.\n\n```\n## not a title\n```\n\nB."
        )

    def test_a_similar_title_is_not_the_section(self):
        for title in ("## Comment tester ?", "### Comment tester", "## Comment tester plus", "Comment tester"):
            with self.subTest(title=title):
                self.write_fiche(1, f"# T\n\n{title}\n\nA 320.\n")
                self.assertEqual(how_to_test(self.run_dir, make_row(1)), "")

    def test_it_is_cut_at_4000_characters(self):
        self.write_fiche(1, fiche_text("a" * 5000))
        text = how_to_test(self.run_dir, make_row(1))
        self.assertEqual(text, "a" * 4000)
        self.write_fiche(2, fiche_text("b" * 4000))
        self.assertEqual(len(how_to_test(self.run_dir, make_row(2))), 4000)
        self.write_fiche(3, fiche_text("c" * 3999))
        self.assertEqual(len(how_to_test(self.run_dir, make_row(3))), 3999)

    def test_a_term_after_the_cut_is_not_seen(self):
        self.write_fiche(1, fiche_text("a " * 2100 + "en paysage"))
        self.assertEqual(self.routed(make_row(1)), ["code-qualite"])

    def test_how_to_test_never_raises(self):
        self.write_fiche(1, "# Titre\n\nSans section.\n")
        (self.run_dir / "fiches" / "CQ-002.md").write_bytes(b"\xff\xfe\x00## Comment tester\n")
        (self.run_dir / "fiches" / "CQ-003.md").mkdir()
        (self.run_dir / "fiches" / "CQ-005.md").write_text("", encoding="utf-8")
        cases = {
            "no fiche": make_row(4),
            "no section": make_row(1),
            "unreadable": make_row(2),
            "a folder": make_row(3),
            "empty": make_row(5),
            "no key": {"ref": 9},
            "none path": make_row(6, fiche_path=None),
            "empty path": make_row(6, fiche_path=""),
            "number path": make_row(6, fiche_path=12),
            "null byte": make_row(6, fiche_path="fiches/CQ\x00.md"),
            "not a row": None,
            "a text": "CQ-1",
            "a list": [],
        }
        for label, row in cases.items():
            with self.subTest(case=label):
                self.assertEqual(how_to_test(self.run_dir, row), "")
        for label, run_dir in (
            ("missing folder", self.run_dir / "nowhere"),
            ("none", None),
            ("text", "nowhere"),
        ):
            with self.subTest(run_dir=label):
                self.assertEqual(how_to_test(run_dir, make_row(1)), "")
        self.write_fiche(7, fiche_text("x" * 9000))
        self.assertEqual(how_to_test(self.run_dir, make_row(7)), "x" * 4000)

    def test_only_the_file_name_of_the_fiche_path_counts(self):
        self.write_fiche(1, fiche_text("A 320."))
        outside = self.run_dir / "CQ-001.md"
        outside.write_text(fiche_text("Ailleurs, en paysage."), encoding="utf-8")
        for path in ("fiches/CQ-001.md", "../CQ-001.md", "/etc/../CQ-001.md", "x/y/CQ-001.md"):
            with self.subTest(path=path):
                self.assertEqual(
                    how_to_test(self.run_dir, make_row(1, fiche_path=path)), "A 320."
                )

    def test_the_fiche_is_read_from_the_run_not_from_the_row(self):
        self.write_fiche(1, fiche_text("A."))
        self.assertEqual(how_to_test(str(self.run_dir), make_row(1)), "A.")


if __name__ == "__main__":
    unittest.main()
