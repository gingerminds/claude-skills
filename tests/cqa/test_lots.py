import random
import unicodedata
import unittest

from cqa import checklist
from cqa.lots import TOOL_ACTIONS, TOOLS, build_lots, coverage, lot_needs_session, select_rows
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


def select(rows, technos=PROJECT, available=CODE_TOOLS, phase_filter=None):
    return select_rows(rows, technos, available, phase_filter)


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
        self.assertEqual(TOOL_ACTIONS["CLI local"], "runner du projet démarré")
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
                    "gains",
                ]
            ),
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["gains"], [])

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
            ["id", "kind", "needs_server_facts", "needs_session", "needs_toolbox", "refs"],
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


if __name__ == "__main__":
    unittest.main()
