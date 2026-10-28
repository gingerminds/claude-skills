import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from cqa import checklist
from cqa.comments import format_page_summary, parse_verdict
from cqa.plan import (
    STATUS_FOR_VERDICT,
    build_plan,
    filter_plan,
    is_human_set,
    load_history,
    operation_key,
)

EM_DASH = chr(0x2014)
RUN_DATE = date(2026, 10, 20)
LEAD = "Côme D"
COPY_DS = "0123456789abcdef0123456789abcdef"
OTHER_DS = f"{2:032x}"
SHA = "3f2a1c9" + "e" * 33
OP_KEYS = {
    "key",
    "kind",
    "page_id",
    "ref",
    "verdict",
    "before",
    "after",
    "text",
    "human_override",
}


def make_row(ref=79, **overrides):
    number = ref or 0
    row = {
        "page_id": f"{number:032x}",
        "ref": ref,
        "title": f"Task {ref}",
        "status": checklist.STATUS_TODO,
        "assignees": [],
        "category": "QUALITÉ",
        "subcategory": "Général",
        "blocking": False,
        "sections": ["Back"],
        "technos": [],
        "phases": ["Build"],
        "tools": ["Code"],
        "automation": None,
        "tooling_hint": "",
        "last_edited_by_bot": False,
        "reference_page_id": f"{number + 1000:032x}",
        "fiche_path": f"fiches/CQ-{number:03d}.md",
    }
    row.update(overrides)
    return row


def make_finding(ref=79, verdict="ko", **overrides):
    finding = {
        "ref": ref,
        "verdict": verdict,
        "confidence": "high",
        "summary": "3 éléments ont un tabindex positif.",
        "evidence": [{"type": "file", "repo": "nuxt", "path": "components/AppHeader.vue"}],
        "todo": "Supprimer les tabindex positifs." if verdict in ("ko", "partial") else None,
        "human_check": None,
        "divergence": None,
        "needs_probes": [],
    }
    finding.update(overrides)
    return finding


def make_context(lead=LEAD, copy_page_id=COPY_DS):
    return {
        "schema": 1,
        "run_id": "2026-10-20-1430",
        "plugin_version": "0.9.0",
        "lead": {"name": "Côme Dorval", "option": lead},
        "repos": [
            {
                "name": "nuxt",
                "web_url": "https://gitlab.example.org/acme/front",
                "audited_sha": SHA,
            }
        ],
        "notion": {
            "copy_page_id": copy_page_id,
            "copy_data_source_id": COPY_DS,
            "assignee_options": ["Inès", LEAD],
        },
    }


def make_excluded(row, reason, verdict, detail):
    return {
        "ref": row["ref"],
        "page_id": row["page_id"],
        "reason": reason,
        "verdict": verdict,
        "detail": detail,
        "tools": row.get("tools", []),
    }


def plan_for(
    rows,
    findings=None,
    excluded=None,
    visible=None,
    history=None,
    followup=None,
    missing=None,
    context=None,
):
    return build_plan(
        rows=rows,
        findings={item["ref"]: item for item in findings or []},
        excluded=excluded or [],
        context=context or make_context(),
        visible_verdicts=visible or {},
        history=history or {},
        run_date=RUN_DATE,
        plugin_version="0.9.0",
        followup_done=followup or set(),
        missing_in_copy=missing,
    )


def ops_of(plan, kind=None):
    return [op for op in plan["ops"] if kind is None or op["kind"] == kind]


def sole_op(testcase, plan, kind):
    found = ops_of(plan, kind)
    testcase.assertEqual(len(found), 1, f"{kind}: {plan['ops']}")
    return found[0]


class IsHumanSetTest(unittest.TestCase):
    def test_todo_is_never_human_set(self):
        self.assertFalse(is_human_set({"status": "À traiter", "last_edited_by_bot": False}))

    def test_other_status_edited_by_a_person(self):
        self.assertTrue(is_human_set({"status": "Mis en place", "last_edited_by_bot": False}))

    def test_other_status_edited_by_the_integration(self):
        self.assertFalse(is_human_set({"status": "Mis en place", "last_edited_by_bot": True}))

    def test_missing_values_count_as_human(self):
        self.assertTrue(is_human_set({"status": "Mis en place"}))
        self.assertTrue(is_human_set({"last_edited_by_bot": False}))
        self.assertTrue(is_human_set({"status": "Mis en place", "last_edited_by_bot": None}))
        self.assertTrue(is_human_set({}))


class StatusTest(unittest.TestCase):
    def test_status_per_verdict_table(self):
        self.assertEqual(
            STATUS_FOR_VERDICT,
            {
                "ok": "Ok / Recetté",
                "ok_human": "Mis en place",
                "partial": "Mise en place partielle",
                "ko": "À traiter",
                "na": "Non applicable",
                "unknown": None,
            },
        )

    def test_status_per_verdict(self):
        expected = {
            "ok": "Ok / Recetté",
            "ok_human": "Mis en place",
            "partial": "Mise en place partielle",
            "ko": "À traiter",
            "na": "Non applicable",
        }
        for verdict, target in expected.items():
            with self.subTest(verdict=verdict):
                current = "En standby" if target == "À traiter" else "À traiter"
                row = make_row(status=current, last_edited_by_bot=True)
                plan = plan_for([row], [make_finding(verdict=verdict, todo=None)])
                op = sole_op(self, plan, "status")
                self.assertEqual((op["before"], op["after"]), (current, target))
                self.assertEqual(op["verdict"], verdict)
                self.assertEqual(op["page_id"], row["page_id"])
                self.assertEqual(op["ref"], 79)

    def test_unknown_has_no_status_op(self):
        plan = plan_for([make_row()], [make_finding(verdict="unknown")])
        self.assertEqual(ops_of(plan, "status"), [])

    def test_unchanged_status_has_no_op(self):
        row = make_row(status="Mise en place partielle", last_edited_by_bot=True)
        plan = plan_for([row], [make_finding(verdict="partial")])
        self.assertEqual(ops_of(plan, "status"), [])

    def test_guardrails_run_before_the_status(self):
        finding = make_finding(verdict="ok", evidence=[], todo=None)
        plan = plan_for([make_row()], [finding])
        op = sole_op(self, plan, "status")
        self.assertEqual(op["after"], "Mis en place")
        self.assertEqual(op["verdict"], "ok_human")
        self.assertEqual(parse_verdict(sole_op(self, plan, "comment")["text"]), "ok_human")

    def test_followup_flag_is_per_ref(self):
        probe = [{"method": "GET", "url": "https://preprod.example.org/x"}]
        ok_pending = make_finding(verdict="ok", todo=None, needs_probes=probe)
        before = plan_for([make_row()], [ok_pending])
        self.assertEqual(sole_op(self, before, "status")["after"], "Ok / Recetté")
        after = plan_for([make_row()], [ok_pending], followup={79})
        self.assertEqual(ops_of(after, "status"), [])
        self.assertEqual(sole_op(self, after, "comment")["verdict"], "unknown")
        other = plan_for([make_row()], [ok_pending], followup={80})
        self.assertEqual(sole_op(self, other, "status")["after"], "Ok / Recetté")

    def test_human_override_flag(self):
        row = make_row(status="Mis en place", last_edited_by_bot=False)
        plan = plan_for([row], [make_finding(verdict="ko")])
        op = sole_op(self, plan, "status")
        self.assertTrue(op["human_override"])
        self.assertEqual((op["before"], op["after"]), ("Mis en place", "À traiter"))

    def test_no_human_override_when_set_by_the_integration_or_todo(self):
        by_bot = make_row(status="Mis en place", last_edited_by_bot=True)
        todo = make_row(ref=80)
        plan = plan_for(
            [by_bot, todo],
            [make_finding(79, "partial"), make_finding(80, "partial")],
        )
        self.assertEqual(
            [op["human_override"] for op in ops_of(plan, "status")], [False, False]
        )

    def test_row_without_ref_is_never_written(self):
        orphan = make_row(ref=None, page_id=f"{500:032x}", status="Mis en place")
        plan = plan_for([orphan, make_row()], [make_finding()])
        self.assertEqual({op["page_id"] for op in plan["ops"]}, {f"{79:032x}"})
        self.assertNotIn("CQ-None", json.dumps(plan, ensure_ascii=False))


class AssignTest(unittest.TestCase):
    def ok_human(self):
        return make_finding(verdict="ok_human", todo=None, human_check="Valider le rendu.")

    def test_assign_lead_to_ok_human(self):
        row = make_row(assignees=["Inès"])
        plan = plan_for([row], [self.ok_human()])
        op = sole_op(self, plan, "assign")
        self.assertEqual(op["before"], ["Inès"])
        self.assertEqual(op["after"], ["Inès", LEAD])
        self.assertEqual(op["text"], "")
        self.assertFalse(op["human_override"])

    def test_assign_lead_once(self):
        row = make_row(assignees=[LEAD])
        plan = plan_for([row], [self.ok_human()])
        self.assertEqual(ops_of(plan, "assign"), [])

    def test_assign_keeps_current_assignees_untouched(self):
        row = make_row(assignees=["Inès"])
        plan = plan_for([row], [self.ok_human()])
        self.assertEqual(row["assignees"], ["Inès"])
        sole_op(self, plan, "assign")["after"].append("x")
        self.assertEqual(row["assignees"], ["Inès"])

    def test_assign_only_for_ok_human(self):
        for verdict in ("ok", "partial", "ko", "unknown", "na"):
            with self.subTest(verdict=verdict):
                plan = plan_for([make_row()], [make_finding(verdict=verdict, todo=None)])
                self.assertEqual(ops_of(plan, "assign"), [])

    def test_assign_follows_the_lowered_verdict(self):
        finding = make_finding(verdict="ok", evidence=[], todo=None)
        plan = plan_for([make_row()], [finding])
        self.assertEqual(sole_op(self, plan, "assign")["after"], [LEAD])

    def test_no_assign_without_lead_option(self):
        plan = plan_for([make_row()], [self.ok_human()], context=make_context(lead=None))
        self.assertEqual(ops_of(plan, "assign"), [])


class CommentTest(unittest.TestCase):
    def row(self):
        return make_row(status="À traiter", last_edited_by_bot=True)

    def test_status_change_comments(self):
        row = make_row(status="Mis en place", last_edited_by_bot=True)
        plan = plan_for([row], [make_finding()], visible={79: ["ko"]}, history={79: "ko"})
        op = sole_op(self, plan, "comment")
        self.assertEqual(op["verdict"], "ko")
        self.assertEqual(parse_verdict(op["text"]), "ko")
        self.assertIn("Audit PRE-MEL · 20/10/2026 · lancé par Côme D", op["text"])
        self.assertIn("Réf CQ-79 · develop@3f2a1c9", op["text"])

    def test_first_run_comments(self):
        plan = plan_for([self.row()], [make_finding()])
        self.assertEqual(len(ops_of(plan, "comment")), 1)
        self.assertEqual(ops_of(plan, "status"), [])

    def test_visible_same_verdict_no_comment(self):
        plan = plan_for([self.row()], [make_finding()], visible={79: ["ko"]})
        self.assertEqual(ops_of(plan, "comment"), [])

    def test_visible_other_verdict_comments(self):
        plan = plan_for([self.row()], [make_finding()], visible={79: ["partial"]})
        self.assertEqual(len(ops_of(plan, "comment")), 1)

    def test_visible_any_of_several_verdicts_is_enough(self):
        plan = plan_for([self.row()], [make_finding()], visible={79: ["ok", "ko"]})
        self.assertEqual(ops_of(plan, "comment"), [])

    def test_history_same_verdict_no_comment(self):
        plan = plan_for([self.row()], [make_finding()], history={79: "ko"})
        self.assertEqual(ops_of(plan, "comment"), [])

    def test_history_other_verdict_comments(self):
        plan = plan_for([self.row()], [make_finding()], history={79: "partial"})
        self.assertEqual(len(ops_of(plan, "comment")), 1)

    def test_other_refs_visibility_does_not_count(self):
        plan = plan_for(
            [self.row()], [make_finding()], visible={80: ["ko"]}, history={80: "ko"}
        )
        self.assertEqual(len(ops_of(plan, "comment")), 1)

    def test_visible_and_history_must_both_miss(self):
        plan = plan_for(
            [self.row()], [make_finding()], visible={79: ["partial"]}, history={79: "ko"}
        )
        self.assertEqual(ops_of(plan, "comment"), [])

    def test_rule_uses_the_lowered_verdict(self):
        finding = make_finding(verdict="ok", evidence=[], todo=None)
        row = make_row(status="Mis en place", last_edited_by_bot=True)
        again = plan_for([row], [finding], visible={79: ["ok_human"]})
        self.assertEqual(ops_of(again, "comment"), [])
        raw = plan_for([row], [finding], visible={79: ["ok"]})
        self.assertEqual(len(ops_of(raw, "comment")), 1)

    def test_unknown_comments_without_status_change(self):
        plan = plan_for([self.row()], [make_finding(verdict="unknown")])
        op = sole_op(self, plan, "comment")
        self.assertEqual(parse_verdict(op["text"]), "unknown")
        silent = plan_for(
            [self.row()], [make_finding(verdict="unknown")], visible={79: ["unknown"]}
        )
        self.assertEqual(ops_of(silent, "comment"), [])

    def test_comment_operation_shape(self):
        op = sole_op(self, plan_for([self.row()], [make_finding()]), "comment")
        self.assertEqual(set(op), OP_KEYS)
        self.assertIsNone(op["before"])
        self.assertIsNone(op["after"])
        self.assertFalse(op["human_override"])


class ExcludedTest(unittest.TestCase):
    DETAIL = "tâche propre à Wordpress, projet Laravel + Nuxt"

    def techno(self, row):
        return make_excluded(row, "techno", "na", self.DETAIL)

    def test_techno_exclusion(self):
        row = make_row(technos=["Wordpress"])
        plan = plan_for([row], excluded=[self.techno(row)])
        status = sole_op(self, plan, "status")
        self.assertEqual((status["before"], status["after"]), ("À traiter", "Non applicable"))
        self.assertEqual(status["verdict"], "na")
        comment = sole_op(self, plan, "comment")
        self.assertIn("tâche propre à", comment["text"])
        self.assertEqual(parse_verdict(comment["text"]), "na")
        self.assertIn("Réf CQ-79", comment["text"])

    def test_techno_status_carries_human_override(self):
        human = make_row(technos=["Wordpress"], status="Mis en place", last_edited_by_bot=False)
        bot = make_row(
            ref=80, technos=["Wordpress"], status="Mis en place", last_edited_by_bot=True
        )
        todo = make_row(ref=81, technos=["Wordpress"])
        plan = plan_for(
            [human, bot, todo], excluded=[self.techno(row) for row in (human, bot, todo)]
        )
        flags = {op["ref"]: op["human_override"] for op in ops_of(plan, "status")}
        self.assertEqual(flags, {79: True, 80: False, 81: False})

    def test_techno_already_not_applicable(self):
        row = make_row(technos=["Wordpress"], status="Non applicable")
        plan = plan_for([row], excluded=[self.techno(row)])
        self.assertEqual(ops_of(plan, "status"), [])
        self.assertEqual(len(ops_of(plan, "comment")), 1)

    def test_techno_comment_follows_the_rules(self):
        row = make_row(technos=["Wordpress"], status="Non applicable")
        excluded = [self.techno(row)]
        by_visible = plan_for([row], excluded=excluded, visible={79: ["na"]})
        self.assertEqual(ops_of(by_visible, "comment"), [])
        by_history = plan_for([row], excluded=excluded, history={79: "na"})
        self.assertEqual(ops_of(by_history, "comment"), [])
        changed = make_row(technos=["Wordpress"])
        plan = plan_for([changed], excluded=[self.techno(changed)], visible={79: ["na"]})
        self.assertEqual(len(ops_of(plan, "comment")), 1)

    def test_no_tools_comments_unknown(self):
        row = make_row(tools=["SSH serveur"])
        excluded = make_excluded(row, "no_tools", "unknown", "outils indisponibles : SSH serveur")
        plan = plan_for([row], excluded=[excluded])
        self.assertEqual(ops_of(plan, "status"), [])
        comment = sole_op(self, plan, "comment")
        self.assertEqual(parse_verdict(comment["text"]), "unknown")
        self.assertIn("outils indisponibles : SSH serveur", comment["text"])
        quiet = plan_for([row], excluded=[excluded], visible={79: ["unknown"]})
        self.assertEqual(quiet["ops"], [])
        quiet = plan_for([row], excluded=[excluded], history={79: "unknown"})
        self.assertEqual(quiet["ops"], [])

    def test_standby_phase_and_no_fiche_have_no_operation(self):
        rows = [
            make_row(ref=1, status="En standby"),
            make_row(ref=2, phases=["MEL J0"]),
            make_row(ref=3, fiche_path=None),
        ]
        excluded = [
            make_excluded(rows[0], "standby", "skip", "ligne en standby"),
            make_excluded(rows[1], "phase", "skip", "tâche reportée à MEL J0"),
            make_excluded(rows[2], "no_fiche", "skip", "fiche introuvable dans le modèle"),
        ]
        self.assertEqual(plan_for(rows, excluded=excluded)["ops"], [])

    def test_unmatched_row_exclusion_is_ignored(self):
        orphan = make_row(ref=None, page_id=f"{500:032x}", fiche_path=None)
        excluded = {
            "ref": None,
            "page_id": orphan["page_id"],
            "reason": "no_fiche",
            "verdict": "skip",
            "detail": "fiche introuvable dans le modèle",
            "tools": [],
        }
        self.assertEqual(plan_for([orphan], excluded=[excluded])["ops"], [])

    def test_excluded_row_ignores_a_stray_finding(self):
        row = make_row(technos=["Wordpress"], status="Non applicable")
        plan = plan_for([row], [make_finding()], excluded=[self.techno(row)], visible={79: ["na"]})
        self.assertEqual(plan["ops"], [])

    def test_excluded_entry_without_row_is_ignored(self):
        ghost = make_row(ref=90, technos=["Wordpress"])
        plan = plan_for([make_row()], [make_finding()], excluded=[self.techno(ghost)])
        self.assertEqual({op["ref"] for op in plan["ops"]}, {79})


class MissingFindingTest(unittest.TestCase):
    def test_missing_finding_is_unknown(self):
        plan = plan_for([make_row()])
        self.assertEqual(ops_of(plan, "status"), [])
        comment = sole_op(self, plan, "comment")
        self.assertEqual(comment["verdict"], "unknown")
        self.assertEqual(parse_verdict(comment["text"]), "unknown")
        self.assertIn("constat invalide ou absent", comment["text"])
        self.assertEqual(plan["summary"]["unknown"], [79])

    def test_missing_finding_follows_the_comment_rules(self):
        plan = plan_for([make_row()], visible={79: ["unknown"]})
        self.assertEqual(plan["ops"], [])

    def test_finding_of_another_row_is_not_used(self):
        plan = plan_for([make_row(79), make_row(80)], [make_finding(79)])
        by_ref = {op["ref"]: op for op in ops_of(plan, "comment")}
        self.assertEqual(by_ref[80]["verdict"], "unknown")
        self.assertEqual(by_ref[79]["verdict"], "ko")


class OperationShapeTest(unittest.TestCase):
    def build(self, **overrides):
        row = make_row(status="Mis en place", assignees=["Inès"])
        finding = make_finding(verdict="ok_human", todo=None, human_check="Valider.")
        return plan_for([row], [finding], **overrides)

    def test_exact_keys_and_order_of_operations(self):
        rows = [make_row(80), make_row(79, status="Ok / Recetté")]
        findings = [
            make_finding(80, "ok_human", todo=None, human_check="x"),
            make_finding(79, "ko"),
        ]
        plan = plan_for(rows, findings)
        for op in plan["ops"]:
            self.assertEqual(set(op), OP_KEYS)
        self.assertEqual(
            [(op["ref"], op["kind"]) for op in plan["ops"]],
            [(79, "status"), (79, "comment"), (80, "status"), (80, "assign"), (80, "comment")],
        )

    def test_key_format(self):
        plan = self.build()
        for op in plan["ops"]:
            self.assertRegex(op["key"], rf"^{op['kind']}:{op['page_id']}:[0-9a-f]{{12}}$")

    def test_key_is_sha1_of_after_and_text(self):
        plan = plan_for([make_row()], [make_finding()], history={79: "partial"})
        op = sole_op(self, plan, "comment")
        digest = hashlib.sha1(op["text"].encode("utf-8")).hexdigest()[:12]
        self.assertEqual(op["key"], f"comment:{op['page_id']}:{digest}")
        status = plan_for([make_row()], [make_finding(verdict="ok", todo=None)])
        status_op = sole_op(self, status, "status")
        digest = hashlib.sha1("Ok / Recetté".encode("utf-8")).hexdigest()[:12]
        self.assertEqual(status_op["key"], f"status:{status_op['page_id']}:{digest}")

    def test_assign_key_hashes_the_names(self):
        plan = self.build()
        op = sole_op(self, plan, "assign")
        digest = hashlib.sha1(json.dumps(op["after"], ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(op["key"], f"assign:{op['page_id']}:{digest[:12]}")

    def test_keys_are_stable_and_distinct(self):
        first, second = self.build(), self.build()
        self.assertEqual([op["key"] for op in first["ops"]], [op["key"] for op in second["ops"]])
        self.assertEqual(len({op["key"] for op in first["ops"]}), len(first["ops"]))

    def test_key_changes_with_the_operation(self):
        base = operation_key("comment", "p", None, "texte")
        self.assertNotEqual(base, operation_key("comment", "p", None, "autre"))
        self.assertNotEqual(base, operation_key("comment", "q", None, "texte"))
        self.assertNotEqual(base, operation_key("status", "p", None, "texte"))
        self.assertNotEqual(
            operation_key("assign", "p", ["a"], ""), operation_key("assign", "p", ["a", "b"], "")
        )

    def test_plan_top_level(self):
        plan = self.build()
        self.assertEqual(
            set(plan),
            {
                "plan_id",
                "run_id",
                "copy_data_source_id",
                "copy_page_id",
                "assignee_options",
                "ops",
                "summary",
                "rows",
            },
        )
        self.assertEqual(plan["run_id"], "2026-10-20-1430")
        self.assertEqual(plan["copy_data_source_id"], COPY_DS)
        self.assertEqual(plan["copy_page_id"], COPY_DS)
        self.assertEqual(plan["assignee_options"], ["Inès", LEAD])

    def test_plan_is_json_serializable(self):
        plan = self.build(missing=[{"ref": 5, "title": "Absente"}])
        self.assertEqual(json.loads(json.dumps(plan)), plan)

    def test_inputs_are_not_modified(self):
        rows = [make_row(status="Mis en place", assignees=["Inès"])]
        findings = {79: make_finding(verdict="ok", evidence=[], todo=None)}
        snapshot = copy.deepcopy((rows, findings))
        build_plan(
            rows=rows,
            findings=findings,
            excluded=[],
            context=make_context(),
            visible_verdicts={},
            history={},
            run_date=RUN_DATE,
            plugin_version="0.9.0",
            followup_done=set(),
        )
        self.assertEqual((rows, findings), snapshot)

    def test_no_em_dash_in_the_plan(self):
        finding = make_finding(summary=f"Un texte {EM_DASH} avec tiret long")
        plan = plan_for([make_row()], [finding])
        self.assertNotIn(EM_DASH, json.dumps(plan, ensure_ascii=False))


class SummaryTest(unittest.TestCase):
    def scenario(self):
        rows = [
            make_row(79, blocking=True),
            make_row(80, blocking=True, status="Mis en place", last_edited_by_bot=True),
            make_row(81, blocking=False),
            make_row(82, blocking=True),
            make_row(83, status="En standby"),
            make_row(84, phases=["MEL J0"], blocking=True),
            make_row(85, technos=["Wordpress"]),
            make_row(86, tools=["SSH serveur"]),
            make_row(87, blocking=True, status="Mis en place", last_edited_by_bot=False),
            make_row(88, fiche_path=None, status="Ok / Recetté"),
            make_row(None, page_id=f"{500:032x}", status="À traiter", blocking=True),
        ]
        findings = [
            make_finding(79, "ko"),
            make_finding(80, "partial"),
            make_finding(81, "ok", todo=None),
            make_finding(82, "unknown"),
            make_finding(87, "ko"),
        ]
        excluded = [
            make_excluded(rows[4], "standby", "skip", "ligne en standby"),
            make_excluded(rows[5], "phase", "skip", "tâche reportée à MEL J0"),
            make_excluded(rows[6], "techno", "na", "tâche propre à Wordpress, projet Laravel"),
            make_excluded(rows[7], "no_tools", "unknown", "outils indisponibles : SSH serveur"),
            make_excluded(rows[9], "no_fiche", "skip", "fiche introuvable dans le modèle"),
            {
                "ref": None,
                "page_id": rows[10]["page_id"],
                "reason": "no_fiche",
                "verdict": "skip",
                "detail": "fiche introuvable dans le modèle",
                "tools": [],
            },
        ]
        missing = [{"ref": 5, "title": "Absente de la copie"}]
        return rows, findings, excluded, missing

    def test_summary_keys_match_what_the_page_comment_consumes(self):
        rows, findings, excluded, missing = self.scenario()
        summary = plan_for(rows, findings, excluded, missing=missing)["summary"]
        self.assertEqual(
            set(summary),
            {
                "by_status_after",
                "blocking_non_conform",
                "unknown",
                "human_overrides",
                "deferred",
                "missing_in_copy",
            },
        )

    def test_summary(self):
        rows, findings, excluded, missing = self.scenario()
        summary = plan_for(rows, findings, excluded, missing=missing)["summary"]
        self.assertEqual(
            summary["by_status_after"],
            {
                "À traiter": 6,
                "Mise en place partielle": 1,
                "Ok / Recetté": 2,
                "Non applicable": 1,
                "En standby": 1,
            },
        )
        self.assertEqual(summary["blocking_non_conform"], [79, 80, 82, 84, 87])
        self.assertEqual(summary["unknown"], [82, 86])
        self.assertEqual(
            summary["human_overrides"],
            [{"ref": 87, "before": "Mis en place", "after": "À traiter"}],
        )
        self.assertEqual(summary["deferred"], [84])
        self.assertEqual(summary["missing_in_copy"], missing)

    def test_blocking_ko_row_is_listed(self):
        plan = plan_for([make_row(79, blocking=True)], [make_finding(79, "ko")])
        self.assertEqual(plan["summary"]["blocking_non_conform"], [79])

    def test_blocking_lists_partial_but_not_conform_rows(self):
        rows = [make_row(1, blocking=True), make_row(2, blocking=True), make_row(3, blocking=True)]
        findings = [
            make_finding(1, "partial"),
            make_finding(2, "ok", todo=None),
            make_finding(3, "na"),
        ]
        plan = plan_for(rows, findings)
        self.assertEqual(plan["summary"]["blocking_non_conform"], [1])

    def test_non_blocking_ko_is_not_listed(self):
        plan = plan_for([make_row(79, blocking=False)], [make_finding(79, "ko")])
        self.assertEqual(plan["summary"]["blocking_non_conform"], [])

    def test_summary_is_empty_without_rows(self):
        summary = plan_for([])["summary"]
        self.assertEqual(
            summary,
            {
                "by_status_after": {},
                "blocking_non_conform": [],
                "unknown": [],
                "human_overrides": [],
                "deferred": [],
                "missing_in_copy": [],
            },
        )

    def test_status_count_ignores_rows_without_status(self):
        plan = plan_for([make_row(79, status=None)], [make_finding(79, "unknown")])
        self.assertEqual(plan["summary"]["by_status_after"], {})

    def test_summary_feeds_the_page_comment(self):
        rows, findings, excluded, missing = self.scenario()
        plan = plan_for(rows, findings, excluded, missing=missing)
        text = format_page_summary(summary=plan["summary"], run_date=RUN_DATE, lead_option=LEAD)
        self.assertIn("- À traiter : 6", text)
        self.assertIn(
            "Lignes bloquantes non conformes (5) : CQ-79, CQ-80, CQ-82, CQ-84, CQ-87", text
        )
        self.assertIn("Lignes non évaluées : 2", text)
        self.assertIn("Statuts posés par un humain et contredits par l'audit (1) : CQ-87", text)
        self.assertIn("Lignes reportées à J0 / J+7 : 1", text)
        self.assertIn("Lignes du modèle absentes de la copie : 1", text)
        self.assertLessEqual(len(text), 1500)


class FilterPlanTest(unittest.TestCase):
    def scenario(self):
        rows = [
            make_row(79, status="Mis en place", last_edited_by_bot=False, blocking=True),
            make_row(80, blocking=True),
            make_row(81, status="Mise en place partielle", last_edited_by_bot=False, blocking=True),
            make_row(82, status="En standby"),
        ]
        findings = [
            make_finding(79, "ko"),
            make_finding(80, "ko"),
            make_finding(81, "ok", todo=None),
        ]
        excluded = [make_excluded(rows[3], "standby", "skip", "ligne en standby")]
        return plan_for(
            rows,
            findings,
            excluded,
            missing=[{"ref": 5, "title": "Absente"}],
            history={},
        )

    def test_filter_plan_drops_page_ops(self):
        plan = self.scenario()
        filtered = filter_plan(plan, skip_human_overrides=True)
        self.assertEqual({op["ref"] for op in filtered["ops"]}, {80})
        self.assertTrue(all(op["page_id"] == f"{80:032x}" for op in filtered["ops"]))
        self.assertEqual(
            [(op["ref"], op["kind"]) for op in plan["ops"] if op["ref"] != 80],
            [(79, "status"), (79, "comment"), (81, "status"), (81, "comment")],
        )

    def test_filter_plan_keeps_everything_when_not_skipping(self):
        plan = self.scenario()
        self.assertEqual(filter_plan(plan, skip_human_overrides=False), plan)

    def test_filter_plan_does_not_modify_its_input(self):
        plan = self.scenario()
        snapshot = copy.deepcopy(plan)
        filtered = filter_plan(plan, skip_human_overrides=True)
        self.assertEqual(plan, snapshot)
        self.assertIsNot(filtered["ops"], plan["ops"])
        filtered["ops"].clear()
        self.assertEqual(plan, snapshot)
        copied = filter_plan(plan, skip_human_overrides=False)
        copied["ops"].clear()
        copied["summary"]["unknown"].append(1)
        self.assertEqual(plan, snapshot)

    def test_filter_plan_without_human_override_is_a_no_op(self):
        plan = plan_for([make_row(80, blocking=True)], [make_finding(80, "ko")])
        self.assertEqual(filter_plan(plan, skip_human_overrides=True), plan)

    def test_filter_plan_recomputes_the_summary(self):
        plan = self.scenario()
        before = plan["summary"]
        self.assertEqual(
            before["by_status_after"],
            {"À traiter": 2, "Ok / Recetté": 1, "En standby": 1},
        )
        self.assertEqual(before["blocking_non_conform"], [79, 80])
        self.assertEqual(len(before["human_overrides"]), 2)
        after = filter_plan(plan, skip_human_overrides=True)["summary"]
        self.assertEqual(
            after["by_status_after"],
            {"À traiter": 1, "Mise en place partielle": 1, "Mis en place": 1, "En standby": 1},
        )
        self.assertEqual(after["human_overrides"], [])

    def test_filter_plan_brings_a_reverted_blocking_row_back(self):
        plan = self.scenario()
        self.assertNotIn(81, plan["summary"]["blocking_non_conform"])
        after = filter_plan(plan, skip_human_overrides=True)["summary"]
        self.assertEqual(after["blocking_non_conform"], [80, 81])

    def test_filter_plan_carries_the_other_summary_lists(self):
        plan = self.scenario()
        after = filter_plan(plan, skip_human_overrides=True)["summary"]
        for key in ("unknown", "deferred", "missing_in_copy"):
            self.assertEqual(after[key], plan["summary"][key])
        self.assertEqual(after["missing_in_copy"], [{"ref": 5, "title": "Absente"}])

    def test_filtered_summary_feeds_the_page_comment(self):
        filtered = filter_plan(self.scenario(), skip_human_overrides=True)
        text = format_page_summary(summary=filtered["summary"], run_date=RUN_DATE, lead_option=LEAD)
        self.assertIn("- Mis en place : 1", text)
        self.assertIn("Lignes bloquantes non conformes (2) : CQ-80, CQ-81", text)
        self.assertNotIn("Statuts posés par un humain", text)
        self.assertLessEqual(len(text), 1500)

    def test_filtered_plan_is_json_serializable(self):
        filtered = filter_plan(self.scenario(), skip_human_overrides=True)
        self.assertEqual(json.loads(json.dumps(filtered)), filtered)

    def test_filtering_twice_is_stable(self):
        once = filter_plan(self.scenario(), skip_human_overrides=True)
        self.assertEqual(filter_plan(once, skip_human_overrides=True), once)


class RecetteTest(unittest.TestCase):
    def scenario(self, recette):
        rows = [
            make_row(79, status="Mis en place", last_edited_by_bot=False),
            make_row(80, status="Mis en place", last_edited_by_bot=False),
        ]
        findings = [make_finding(79, "ok", todo=None), make_finding(80, "ko")]
        return build_plan(
            rows=rows,
            findings={item["ref"]: item for item in findings},
            excluded=[],
            context=make_context(),
            visible_verdicts={},
            history={},
            run_date=RUN_DATE,
            plugin_version="0.9.0",
            followup_done=set(),
            recette_refs=frozenset(recette),
        )

    def test_recette_refs_may_override_human_status(self):
        plan = self.scenario({79})
        flags = {op["ref"]: op["human_override"] for op in plan["ops"] if op["kind"] == "status"}
        self.assertEqual(flags, {79: False, 80: True})
        filtered = filter_plan(plan, skip_human_overrides=True)
        self.assertEqual({op["ref"] for op in filtered["ops"]}, {79})
        self.assertEqual(
            [state["after"] for state in filtered["rows"]], ["Ok / Recetté", "Mis en place"]
        )
        self.assertEqual(filtered["summary"]["human_overrides"], [])

    def test_without_recette_refs_both_are_protected(self):
        plan = self.scenario(set())
        self.assertTrue(all(op["human_override"] for op in plan["ops"] if op["kind"] == "status"))
        self.assertEqual(filter_plan(plan, skip_human_overrides=True)["ops"], [])


class LoadHistoryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write_run(self, run_id, applied, *, copy_ds=COPY_DS, via="plan", slug="acme-shop"):
        run_dir = self.root / slug / run_id
        (run_dir / "notion").mkdir(parents=True)
        if applied is not None:
            self.write_json(run_dir / "notion" / "applied.json", {"applied": applied})
        if via == "plan":
            self.write_json(run_dir / "notion" / "plan.json", {"copy_data_source_id": copy_ds})
        elif via == "context":
            self.write_json(
                run_dir / "context.json", {"notion": {"copy_data_source_id": copy_ds}}
            )
        return run_dir

    @staticmethod
    def write_json(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def entry(ref, verdict, kind="comment"):
        return {
            "key": f"{kind}:{ref}:abcdefabcdef",
            "kind": kind,
            "ref": ref,
            "verdict": verdict,
            "at": "2026-10-20T14:30:00",
        }

    def load(self, exclude="2026-10-21-0900", copy_ds=COPY_DS, slug="acme-shop"):
        return load_history(self.root, slug, copy_ds, exclude)

    def test_last_comment_verdict_per_ref(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko"), self.entry(80, "ko")])
        self.write_run("2026-10-19-0900", [self.entry(79, "partial")])
        self.assertEqual(self.load(), {79: "partial", 80: "ko"})

    def test_keys_are_int_refs(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")])
        self.assertEqual(list(self.load()), [79])

    def test_later_entry_of_a_run_wins(self):
        self.write_run("2026-10-19-0900", [self.entry(79, "ko"), self.entry(79, "ok")])
        self.assertEqual(self.load(), {79: "ok"})

    def test_run_order_is_by_name_not_by_listing(self):
        self.write_run("2026-10-20-1430", [self.entry(79, "ok")])
        self.write_run("2026-10-20-0900", [self.entry(79, "ko")])
        self.write_run("2026-09-30-1700", [self.entry(79, "partial")])
        self.assertEqual(self.load(), {79: "ok"})

    def test_current_run_is_excluded(self):
        self.write_run("2026-10-19-0900", [self.entry(79, "ko")])
        self.write_run("2026-10-21-0900", [self.entry(79, "ok"), self.entry(80, "ok")])
        self.assertEqual(self.load(exclude="2026-10-21-0900"), {79: "ko"})

    def test_other_copies_are_ignored(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")], copy_ds=OTHER_DS)
        self.write_run("2026-10-19-0900", [self.entry(80, "ko")])
        self.assertEqual(self.load(), {80: "ko"})

    def test_other_projects_are_ignored(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")], slug="other-project")
        self.assertEqual(self.load(), {})

    def test_only_comments_count(self):
        applied = [
            self.entry(79, "ko", kind="status"),
            self.entry(80, "ko", kind="assign"),
            self.entry(None, "ok", kind="page_comment"),
            self.entry(81, "partial"),
        ]
        self.write_run("2026-10-18-0900", applied)
        self.assertEqual(self.load(), {81: "partial"})

    def test_copy_found_through_context_when_plan_is_missing(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")], via="context")
        self.assertEqual(self.load(), {79: "ko"})
        self.assertEqual(self.load(copy_ds=OTHER_DS), {})

    def test_plan_wins_over_context(self):
        run_dir = self.write_run("2026-10-18-0900", [self.entry(79, "ko")], copy_ds=OTHER_DS)
        self.write_json(
            run_dir / "context.json", {"notion": {"copy_data_source_id": COPY_DS}}
        )
        self.assertEqual(self.load(), {})

    def test_run_that_names_no_copy_is_ignored(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")], via=None)
        self.assertEqual(self.load(), {})

    def test_run_without_applied_file_is_ignored(self):
        self.write_run("2026-10-18-0900", None)
        self.assertEqual(self.load(), {})

    def test_unreadable_files_are_ignored(self):
        broken = self.write_run("2026-10-17-0900", [self.entry(79, "ko")])
        (broken / "notion" / "applied.json").write_text("{not json", encoding="utf-8")
        shaped = self.write_run("2026-10-18-0900", [self.entry(80, "ko")])
        self.write_json(shaped / "notion" / "applied.json", ["unexpected"])
        garbled = self.write_run("2026-10-19-0900", [self.entry(81, "ko")])
        self.write_json(
            garbled / "notion" / "applied.json", {"applied": ["x", 3, {"kind": "comment"}]}
        )
        self.write_run("2026-10-19-1000", [self.entry(82, "ko")])
        self.assertEqual(self.load(), {82: "ko"})

    def test_invalid_entries_are_skipped(self):
        applied = [
            {"kind": "comment", "ref": "79", "verdict": "ko"},
            {"kind": "comment", "ref": True, "verdict": "ko"},
            {"kind": "comment", "ref": 80, "verdict": None},
            {"kind": "comment", "ref": 81},
            self.entry(82, "ko"),
        ]
        self.write_run("2026-10-18-0900", applied)
        self.assertEqual(self.load(), {82: "ko"})

    def test_missing_cache_root_or_project(self):
        self.assertEqual(self.load(), {})
        self.assertEqual(load_history(self.root / "absent", "acme-shop", COPY_DS, "x"), {})

    def test_stray_files_next_to_runs_are_ignored(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")])
        (self.root / "acme-shop" / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.load(), {79: "ko"})

    def test_history_feeds_build_plan(self):
        self.write_run("2026-10-18-0900", [self.entry(79, "ko")])
        history = self.load()
        row = make_row(79, status="À traiter", last_edited_by_bot=True)
        plan = plan_for([row], [make_finding(79, "ko")], history=history)
        self.assertEqual(plan["ops"], [])


if __name__ == "__main__":
    unittest.main()
