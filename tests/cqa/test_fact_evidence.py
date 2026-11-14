"""R-04: a fact noted from the server (facts/server.json) is the evidence of an na, never of an ok."""
import copy
import json
import unittest
from datetime import date
from pathlib import Path

from cqa import rerun
from cqa.comments import format_comment
from cqa.findings import REFUSED_KEY, apply_guardrails, validate_document
from cqa.grounding import Grounding

from tests.cqa.test_verified_verdicts import FILE, PERF, VerifiedCase

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "agents" / "cqa-auditor.md"
VERDICTS_MD = ROOT / "skills" / "checklist-quality-audit" / "references" / "verdicts.md"
SERVER_MD = ROOT / "skills" / "checklist-quality-audit" / "references" / "server.md"
EM_DASH = chr(0x2014)

TOPIC = "Base de données du serveur"
FACT = {"type": "fact", "topic": TOPIC}
COMMAND = "--sql drush SELECT COUNT(*) FROM watchdog LIMIT 1"
RESULT = "aucune base de données sur ce serveur (jeton-factice-4f9a)"
GOOD = {"refs": [101], "topic": TOPIC, "command": COMMAND, "exit": 0, "result": RESULT}


def fact(**changes):
    return {**GOOD, **changes}


def without(key):
    return {name: value for name, value in GOOD.items() if name != key}


_ROW = {"ref": 79, "tools": ["Code"]}


def _finding(verdict, evidence, **changes):
    item = {
        "ref": 79,
        "verdict": verdict,
        "confidence": "high",
        "summary": "Constat.",
        "evidence": copy.deepcopy(evidence),
        "todo": None,
        "human_check": None,
        "divergence": None,
        "needs_probes": [],
    }
    item.update(changes)
    return item


class FactCase(VerifiedCase):
    def write_facts(self, run_dir, *facts):
        self.write(run_dir, "facts/server.json", {"facts": list(facts)})

    def judge_na(self, run_dir, evidence=None, ref=101):
        self.judge(run_dir, ref, "na", [FACT] if evidence is None else evidence, summary="Pas de base.")

    def grounded(self, run_dir, ref=101, name=f"{PERF}.json"):
        """Return (finding as the reader gives it, finding after the guardrails)."""
        context = self.read(run_dir, "context.json")
        lot = self.read(run_dir, "lots.json")["lots"][0]
        found = rerun.read_grounded_findings(run_dir, context, lot, name)[0]
        (item,) = [entry for entry in found if entry["ref"] == ref]
        return item, apply_guardrails(item, {"ref": ref, "tools": ["Code"]}, followup_done=False)


class NaWithAFactTest(FactCase):
    def test_na_with_a_matching_fact_stays_na(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.judge_na(run_dir)
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        item, judged = self.grounded(run_dir)
        self.assertEqual(item["evidence"], [FACT])
        self.assertNotIn(REFUSED_KEY, item)
        self.assertEqual(judged["verdict"], "na")
        self.assertIsNone(judged["guardrail"])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertNotIn(101, summary["blocking_non_conform"])
        self.assertEqual(self.status_after(run_dir, 101), "Non applicable")
        self.assertEqual(self.comment_verdict(run_dir, 101), "na")

    def test_the_fact_may_cover_several_rows_and_sit_among_other_facts(self):
        run_dir = self.make_run()
        other = fact(refs=[7], topic="Autre sujet")
        self.write_facts(run_dir, other, fact(refs=[88, 101, 102]))
        self.judge_na(run_dir)
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        self.assertEqual(self.grounded(run_dir)[1]["verdict"], "na")

    def test_one_valid_fact_is_enough_when_the_topic_is_noted_twice(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, fact(refs=[102]), fact(exit=1), GOOD)
        self.judge_na(run_dir)
        self.assertEqual(self.validate_errors(run_dir, PERF), [])

    def test_na_with_a_missing_fact_becomes_unknown(self):
        cases = {
            "absent": None,
            "unreadable": "{",
            "not an object": "[]",
            "no list": '{"facts": 3}',
            "unknown topic": {"facts": [fact(topic="Autre sujet")]},
            "no fact object": {"facts": ["x", 3, None]},
        }
        for label, content in cases.items():
            with self.subTest(case=label):
                run_dir = self.make_run()
                if isinstance(content, str):
                    (run_dir / "facts").mkdir()
                    (run_dir / "facts" / "server.json").write_text(content, encoding="utf-8")
                elif content is not None:
                    self.write(run_dir, "facts/server.json", content)
                self.judge_na(run_dir)
                errors = self.errors_of(run_dir, 101)
                self.assertEqual(len(errors), 1)
                self.assertIn("preuve n° 1 (fact) écartée", errors[0])
                item, judged = self.grounded(run_dir)
                self.assertEqual(item["evidence"], [])
                self.assertEqual(item[REFUSED_KEY][0]["evidence"], FACT)
                self.assertEqual(judged["verdict"], "unknown")
                self.assertIn("aucune preuve : na abaissé à unknown", judged["guardrail"])
                summary, _ = self.plan(run_dir)
                self.assertIn(101, summary["unknown"])
                self.assertIn("preuve n° 1 (fact) écartée", self.reasons(summary)[101])
                self.assertNotEqual(self.status_after(run_dir, 101), "Non applicable")

    def test_each_refusal_says_what_is_missing_in_french(self):
        run_dir = self.make_run()
        self.judge_na(run_dir)
        path = run_dir / "facts" / "server.json"
        cases = (
            (None, ("facts/server.json", "introuvable")),
            ("{", ("facts/server.json", "illisible")),
            ("[]", ("facts/server.json", "liste « facts »")),
            ({"facts": []}, ("aucun fait", TOPIC)),
        )
        for index, (content, needles) in enumerate(cases):
            with self.subTest(index=index):
                path.unlink(missing_ok=True)
                if isinstance(content, str):
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                elif content is not None:
                    self.write(run_dir, "facts/server.json", content)
                (error,) = self.errors_of(run_dir, 101)
                for needle in needles:
                    self.assertIn(needle, error)

    def test_fact_must_cover_the_row(self):
        cases = {
            "other row": fact(refs=[102]),
            "empty refs": fact(refs=[]),
            "no refs": without("refs"),
            "refs not a list": fact(refs=101),
            "ref as text": fact(refs=["101"]),
            "ref as float": fact(refs=[101.0]),
            "ref as boolean": fact(refs=[True]),
        }
        for label, entry in cases.items():
            with self.subTest(case=label):
                run_dir = self.make_run()
                self.write_facts(run_dir, entry)
                self.judge_na(run_dir)
                (error,) = self.errors_of(run_dir, 101)
                self.assertIn("preuve n° 1 (fact) écartée", error)
                self.assertIn("CQ-101", error)
                self.assertEqual(self.grounded(run_dir)[1]["verdict"], "unknown")

    def test_a_fact_of_another_topic_does_not_cover_the_row(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, fact(topic="Base de données du serveur "), fact(topic="base de données du serveur"))
        self.judge_na(run_dir)
        (error,) = self.errors_of(run_dir, 101)
        self.assertIn("aucun fait", error)

    def test_fact_with_a_failed_command_is_dropped(self):
        for value in (1, 2, -1, False, True, "0", 0.0, None, [0]):
            with self.subTest(exit=value):
                run_dir = self.make_run()
                self.write_facts(run_dir, fact(exit=value))
                self.judge_na(run_dir)
                (error,) = self.errors_of(run_dir, 101)
                self.assertIn("preuve n° 1 (fact) écartée", error)
                self.assertIn("exit", error)
                self.assertEqual(self.grounded(run_dir)[1]["verdict"], "unknown")
        run_dir = self.make_run()
        entry = dict(GOOD)
        del entry["exit"]
        self.write_facts(run_dir, entry)
        self.judge_na(run_dir)
        self.assertEqual(len(self.errors_of(run_dir, 101)), 1)

    def test_fact_without_command_or_result_is_dropped(self):
        cases = {
            "empty result": fact(result=""),
            "blank result": fact(result="  \n"),
            "no result": without("result"),
            "result not text": fact(result=3),
            "empty command": fact(command=""),
            "blank command": fact(command=" "),
            "no command": without("command"),
            "command not text": fact(command=["--php-ini"]),
        }
        for label, entry in cases.items():
            with self.subTest(case=label):
                run_dir = self.make_run()
                self.write_facts(run_dir, entry)
                self.judge_na(run_dir)
                (error,) = self.errors_of(run_dir, 101)
                self.assertIn("preuve n° 1 (fact) écartée", error)
                self.assertTrue("résultat" in error or "commande" in error, error)
                self.assertEqual(self.grounded(run_dir)[1]["verdict"], "unknown")

    def test_a_refusal_never_quotes_the_command_nor_the_result(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, fact(exit=1), fact(refs=[102], exit=1))
        self.judge_na(run_dir)
        (error,) = self.errors_of(run_dir, 101)
        self.assertNotIn("jeton-factice", error)
        self.assertNotIn("watchdog", error)
        summary, err = self.plan(run_dir)
        self.assertNotIn("jeton-factice", err)
        self.assertNotIn("jeton-factice", json.dumps(summary))
        self.assertNotIn("jeton-factice", json.dumps(self.read(run_dir, "notion/plan.json")))

    def test_a_refused_fact_never_sets_the_finding_aside(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "ko", [FILE, FACT], summary="Base absente mais requise.")
        (error,) = self.errors_of(run_dir, 101)
        self.assertIn("preuve n° 2 (fact) écartée", error)
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertIn(101, summary["blocking_non_conform"])
        self.assertEqual(self.comment_verdict(run_dir, 101), "ko")

    def test_the_facts_written_after_validate_are_taken_by_the_plan(self):
        run_dir = self.make_run()
        self.judge_na(run_dir)
        self.assertEqual(len(self.errors_of(run_dir, 101)), 1)
        self.write_facts(run_dir, GOOD)
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertEqual(self.status_after(run_dir, 101), "Non applicable")

    def test_a_fact_that_still_does_not_cover_the_row_is_refused_again_by_the_plan(self):
        run_dir = self.make_run()
        self.judge_na(run_dir)
        self.assertEqual(len(self.errors_of(run_dir, 101)), 1)
        self.write_facts(run_dir, fact(refs=[102]))
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])

    def test_an_accepted_fact_is_frozen_at_validation(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.judge_na(run_dir)
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        (run_dir / "facts" / "server.json").unlink()
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        record = self.read(run_dir, f"findings/{PERF}.checked.json")["files"][f"{PERF}.json"]["findings"]
        (entry,) = [item for item in record if item["ref"] == 101]
        self.assertEqual((entry["problems"], entry["refused"], entry["retry"]), ([], [], []))

    def test_every_row_of_a_file_is_checked_against_its_own_line(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.judge_na(run_dir, ref=101)
        self.judge_na(run_dir, ref=102)
        errors = self.validate_errors(run_dir, PERF)
        self.assertEqual(len(errors), 1)
        self.assertIn("CQ-102", errors[0])
        self.assertNotIn("CQ-101 ", errors[0])


class FactInARerunTest(FactCase):
    def start_rerun(self, run_dir):
        self.run_json(["rerun-start", run_dir])
        document = self.read(run_dir, f"findings/{PERF}.json")
        (first,) = [item for item in document["findings"] if item["ref"] == 101]
        first.update(verdict="na", confidence="high", todo=None, evidence=[FACT], needs_probes=[])
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", {"lot": PERF, "findings": [first]})

    def test_rerun_close_keeps_a_rerun_file_whose_only_problem_is_a_refused_fact(self):
        run_dir = self.make_run()
        self.start_rerun(run_dir)
        self.assertEqual(self.run_json(["rerun-close", run_dir, "1", "--rows", "101"])["discarded"], [])
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertIn("rerun-01", self.reasons(summary)[101])
        self.assertIn("preuve n° 1 (fact) écartée", self.reasons(summary)[101])

    def test_a_rerun_na_with_a_fact_of_the_row_stays_na(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.start_rerun(run_dir)
        self.assertEqual(self.run_json(["rerun-close", run_dir, "1", "--rows", "101"])["discarded"], [])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertEqual(self.status_after(run_dir, 101), "Non applicable")


class FactAndOkTest(FactCase):
    def test_ok_with_only_a_fact_stays_ok_human(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.judge(run_dir, 101, "ok", [FACT], summary="Base absente.")
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        item, judged = self.grounded(run_dir)
        self.assertEqual(item["evidence"], [FACT])
        self.assertEqual(judged["verdict"], "ok_human")
        self.assertEqual(
            judged["guardrail"],
            "aucune preuve contrôlable (un fait relevé par SSH ne suffit pas pour un ok) : "
            "ok abaissé à ok_human",
        )
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertEqual(self.comment_verdict(run_dir, 101), "ok_human")
        self.assertNotEqual(self.status_after(run_dir, 101), "Ok / Recetté")

    def test_ok_with_a_fact_and_a_valid_file_stays_ok(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.judge(run_dir, 101, "ok", [FACT, FILE], summary="Base absente.")
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        _, judged = self.grounded(run_dir)
        self.assertEqual(judged["verdict"], "ok")
        self.assertIsNone(judged["guardrail"])

    def test_the_guardrail_without_any_evidence_keeps_its_own_reason(self):
        judged = apply_guardrails(_finding("ok", []), _ROW, followup_done=False)
        self.assertEqual(judged["verdict"], "ok_human")
        self.assertEqual(judged["guardrail"], "aucune preuve : ok abaissé à ok_human")

    def test_a_fact_never_lifts_the_other_rules_of_an_ok(self):
        row_human = {"ref": 79, "tools": ["Intervention humaine"]}
        cases = (
            (_finding("ok", [FACT, FILE], confidence="medium"), _ROW, "confiance non haute"),
            (_finding("ok", [FACT, FILE]), row_human, "intervention humaine"),
            (_finding("ok", [FACT, FILE], divergence="Le code diffère."), _ROW, "partial"),
            (_finding("ok", [FACT, FILE], todo="À faire."), _ROW, "à faire ou à valider"),
            (_finding("ok", [FACT, FILE], source="context_doc"), _ROW, "rapport tiers"),
        )
        for finding, row, needle in cases:
            with self.subTest(needle=needle):
                judged = apply_guardrails(finding, row, followup_done=True)
                self.assertNotEqual(judged["verdict"], "ok")
                self.assertIn(needle, judged["guardrail"])

    def test_a_fact_never_lifts_the_rules_of_an_na(self):
        probes = [{"url": "https://preprod.example.org/x", "method": "GET"}]
        cases = (
            (_finding("na", [FACT], confidence="medium"), False, "confiance non haute : na abaissé à unknown"),
            (_finding("na", [FACT], needs_probes=probes), True, "sondes ou scénarios en attente"),
        )
        for finding, followup, needle in cases:
            with self.subTest(needle=needle):
                judged = apply_guardrails(finding, _ROW, followup_done=followup)
                self.assertEqual(judged["verdict"], "unknown")
                self.assertIn(needle, judged["guardrail"])

    def test_a_ko_or_a_partial_keeps_its_fact(self):
        for verdict in ("ko", "partial", "unknown"):
            with self.subTest(verdict=verdict):
                judged = apply_guardrails(_finding(verdict, [FACT]), _ROW, followup_done=False)
                self.assertEqual(judged["verdict"], verdict)
                self.assertIsNone(judged["guardrail"])


class FactShapeTest(unittest.TestCase):
    def validate(self, evidence, **options):
        return validate_document({"lot": "server", "findings": [_finding("na", evidence)]}, {79}, **options)

    def test_fact_evidence_shape(self):
        valid, errors = self.validate([FACT])
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 1)
        for evidence in ({"type": "fact"}, {"type": "fact", "topic": ""}, {"type": "fact", "topic": " \n"}):
            with self.subTest(evidence=evidence):
                valid, errors = self.validate([evidence])
                self.assertEqual(valid, [])
                self.assertEqual(len(errors), 1)
                self.assertIn("preuve n° 1 (fact)", errors[0])
                self.assertIn("« topic »", errors[0])
        valid, errors = self.validate([{"type": "fact", "topic": 3}])
        self.assertEqual(valid, [])
        self.assertIn("« topic » doit être un texte non vide", errors[0])

    def test_the_unknown_type_message_lists_fact(self):
        _, errors = self.validate([{"type": "log"}])
        self.assertIn("fact", errors[0])
        self.assertTrue(errors[0].endswith("(file, page, tool, http, document, fact)"), errors[0])

    def test_agent_file_may_carry_a_fact(self):
        valid, errors = self.validate([FACT], agent_file=True)
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 1)
        valid, errors = self.validate([{"type": "document", "doc": "d1", "ref": "critères 7.3"}], agent_file=True)
        self.assertEqual(valid, [])
        self.assertIn("document", errors[0])


class FactGroundingTest(FactCase):
    def grounding(self, run_dir, *, strict=True):
        return Grounding(run_dir, self.read(run_dir, "context.json"), {}, strict=strict)

    def test_legacy_run_does_not_check_facts(self):
        run_dir = self.make_run()
        self.judge_na(run_dir)
        self.assertEqual(self.grounding(run_dir, strict=False).check([FACT], ref=101), [])
        self.assertEqual(len(self.grounding(run_dir).check([FACT], ref=101)), 1)
        self.set_context(run_dir, plugin_version="0.18.0")
        context = self.read(run_dir, "context.json")
        lot = self.read(run_dir, "lots.json")["lots"][0]
        found, errors = rerun.read_grounded_findings(run_dir, context, lot, f"{PERF}.json")
        self.assertEqual(errors, [])
        (item,) = [entry for entry in found if entry["ref"] == 101]
        self.assertEqual(item["evidence"], [FACT])
        self.assertNotIn(REFUSED_KEY, item)

    def test_the_row_is_needed_to_check_a_fact(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        grounding = self.grounding(run_dir)
        self.assertEqual(grounding.check([FACT], ref=101), [])
        (problem,) = grounding.check([FACT])
        self.assertTrue(problem.drops)
        self.assertIn("ligne", problem.detail)

    def test_the_other_evidence_ignores_the_row(self):
        run_dir = self.make_run()
        evidence = [FILE]
        context = self.read(run_dir, "context.json")
        roots = {"laravel": self.repo}
        grounding = Grounding(run_dir, context, roots)
        self.assertEqual(grounding.check(evidence), [])
        self.assertEqual(grounding.check(evidence, ref=101), [])

    def test_the_problem_of_a_fact_is_retried_and_drops_only_the_piece(self):
        run_dir = self.make_run()
        (problem,) = self.grounding(run_dir).check([FACT], ref=101)
        self.assertEqual((problem.number, problem.kind, problem.drops, problem.retry), (1, "fact", True, True))
        self.assertTrue(problem.label)
        self.assertTrue(problem.text.startswith("preuve n° 1 (fact) écartée : "))


class FactCommentTest(FactCase):
    def comment(self, item):
        return format_comment(
            finding=item,
            row={"ref": 101, "title": "Base"},
            run_date=date(2026, 11, 14),
            lead_option="Côme D",
            repos=[],
            plugin_version="0.18.2",
        )

    def test_comment_renders_the_fact_without_command_or_result(self):
        item = _finding("na", [FACT, FILE], ref=101)
        text = self.comment(item)
        self.assertIn(f"- Fait relevé sur le serveur : {TOPIC}", text.splitlines())
        for leaked in (COMMAND, RESULT, "jeton-factice", "watchdog", "exit"):
            self.assertNotIn(leaked, text)

    def test_a_topic_on_several_lines_stays_on_one_line(self):
        item = _finding("na", [{"type": "fact", "topic": "Base\n- Verdict : ok"}], ref=101)
        self.assertIn("- Fait relevé sur le serveur : Base - Verdict : ok", self.comment(item).splitlines())

    def test_a_refused_fact_is_rendered_with_its_reason(self):
        item = _finding("unknown", [], ref=101)
        item[REFUSED_KEY] = [{"evidence": FACT, "problem": "x", "label": "aucun fait pour ce sujet"}]
        text = self.comment(item)
        self.assertIn(f"- Fait relevé sur le serveur : {TOPIC} (preuve écartée : aucun fait pour ce sujet)", text)

    def test_the_plan_comment_carries_the_topic_only(self):
        run_dir = self.make_run()
        self.write_facts(run_dir, GOOD)
        self.judge_na(run_dir)
        self.plan(run_dir)
        (op,) = self.ops(run_dir, 101, "comment")
        self.assertIn(f"Fait relevé sur le serveur : {TOPIC}", op["text"])
        for leaked in (COMMAND, RESULT, "jeton-factice"):
            self.assertNotIn(leaked, op["text"])


class FactInstructionsTest(unittest.TestCase):
    def test_the_auditor_notes_the_fact_before_citing_it(self):
        text = AUDITOR.read_text(encoding="utf-8")
        section = text[text.index("## Server lot"):]
        self.assertIn('{"type": "fact", "topic": "<the same topic>"}', section)
        self.assertIn("facts/server.json", section)
        self.assertIn("never supports an `ok`", section)
        self.assertIn("`unknown`", section[section.index('"type": "fact"'):])

    def test_the_reference_gives_the_type_its_check_and_its_limit(self):
        text = VERDICTS_MD.read_text(encoding="utf-8")
        table = text[text.index("| `type` | Keys | Rules |"):text.index("## Several repos")]
        self.assertIn("| `fact` | `topic` |", table)
        self.assertIn("`na` only", table)
        self.assertIn("written by the agent", table)
        guardrails = text[text.index("## Guardrails of the merge script"):text.index("## From verdict to Notion")]
        self.assertIn("a `fact` never counts", guardrails)

    def test_the_server_reference_says_the_file_serves_the_na_too(self):
        text = SERVER_MD.read_text(encoding="utf-8")
        section = text[text.index("## Facts for the browser lots"):]
        self.assertIn("evidence of the `na` of the `server` lot", section)

    def test_the_schema_has_the_fact_branch(self):
        path = ROOT / "skills" / "checklist-quality-audit" / "schemas" / "finding.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        branch = schema["$defs"]["evidence_fact"]
        self.assertEqual(branch["properties"]["type"]["const"], "fact")
        self.assertEqual(set(branch["required"]), {"type", "topic"})
        self.assertNotIn(EM_DASH, json.dumps(schema, ensure_ascii=False))

    def test_the_new_lines_carry_no_long_dash(self):
        for path in (AUDITOR, VERDICTS_MD, SERVER_MD):
            self.assertNotIn(EM_DASH, path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
