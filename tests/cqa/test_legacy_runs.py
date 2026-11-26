"""R-01: a run started before 0.18.1 keeps, once read by 0.18.1, the findings 0.18.0 validated."""
import json
import tempfile
import unittest
from pathlib import Path

from cqa import contract, probes, rerun
from cqa.findings import LEGACY_KEY

from tests.cqa.test_cli_audit import AuditCase

PERF = "code-performance"
SECU = "code-securite"
OUTSIDE = "https://evil.example.net/x"
KERNEL = {"type": "file", "repo": "laravel", "path": "app/Http/Kernel.php", "line": 1}
HAND_MADE = {
    "type": "http",
    "url": "https://preprod.example.org/telescope",
    "detail": "200 : Telescope exposé",
}
TOOL = {"type": "tool", "tool": "http", "ref": "summary.json#root"}


def finding(ref, verdict, evidence, **changes):
    item = {
        "ref": ref,
        "verdict": verdict,
        "confidence": "high",
        "summary": "Constat de l'audit.",
        "evidence": evidence,
        "todo": None if verdict in ("ok", "na") else "À corriger.",
        "human_check": None,
        "divergence": None,
        "needs_probes": [],
    }
    item.update(changes)
    return item


def report_ok(ref):
    """A conforming verdict of a third-party report, as 0.18.0 wrote it in findings/context-docs.json."""
    return finding(
        ref,
        "ok",
        [{"type": "document", "doc": "d1", "ref": "critères 7.3"}],
        source="context_doc",
        summary="Rapport « Audit » (2026-10-01) : conforme.",
    )


class LegacyCase(AuditCase):
    def make_legacy_run(self, version="0.18.0"):
        """The fixture run as 0.18.0 left it: a blocking ko citing a request the agent made by hand
        next to a valid file, an na without evidence, and a conforming report already taken over."""
        run_dir = self.make_run()
        self.write(run_dir, "comments.json", {})
        self.set_version(run_dir, version)
        self.edit_finding(run_dir, PERF, 101, verdict="ko", evidence=[KERNEL, HAND_MADE])
        self.write(run_dir, f"findings/{SECU}.json", {"lot": SECU, "findings": [finding(104, "na", [])]})

        def judged_from_a_report(document):
            for lot in document["lots"]:
                if lot["id"] == SECU:
                    lot["document_refs"] = [103]

        self.edit(run_dir, "lots.json", judged_from_a_report)
        self.write(run_dir, "findings/context-docs.json", {"lot": "context-docs", "findings": [report_ok(103)]})
        self.write(run_dir, "context-docs/d1/items.json", {"document": {"id": "d1"}, "items": []})
        return run_dir

    def set_version(self, run_dir, version):
        def change(context):
            if version is None:
                context.pop("plugin_version", None)
            else:
                context["plugin_version"] = version

        self.edit(run_dir, "context.json", change)

    def plan(self, run_dir, *flags):
        code, out, err = self.run_main(["plan", run_dir, *flags])
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def stored_plan(self, run_dir):
        return self.read(run_dir, "notion/plan.json")

    def ops_by_ref(self, run_dir):
        found = {}
        for op in self.stored_plan(run_dir)["ops"]:
            found.setdefault(op["ref"], []).append((op["kind"], op["verdict"], op["after"]))
        return found

    def reasons(self, summary):
        return {item["ref"]: item["reason"] for item in summary["discarded"]}


class LegacyRunEndToEndTest(LegacyCase):
    # What 0.18.0 plans for this run (replayed with the plugin of 8581285, see task-F1-report.md).
    BASE_OPS = {
        101: [("comment", "ko", None)],
        102: [("status", "ok", "Ok / Recetté"), ("comment", "ok", None)],
        103: [("status", "ok", "Ok / Recetté"), ("comment", "ok", None)],
        104: [("status", "na", "Non applicable"), ("comment", "na", None)],
        105: [("status", "na", "Non applicable"), ("comment", "na", None)],
        106: [("comment", "unknown", None)],
    }

    def test_run_of_0_18_0_read_by_0_18_1_keeps_its_findings(self):
        run_dir = self.make_legacy_run()
        summary, err = self.plan(run_dir)
        self.assertEqual(summary["discarded"], [])
        self.assertEqual(summary["discarded_count"], 0)
        self.assertNotIn("Constats écartés", err)
        self.assertEqual(self.ops_by_ref(run_dir), self.BASE_OPS)
        self.assertEqual(summary["unknown"], [106])
        self.assertEqual(summary["blocking_non_conform"], [101])
        comment = next(
            op["text"] for op in self.stored_plan(run_dir)["ops"] if op["ref"] == 101 and op["kind"] == "comment"
        )
        self.assertIn("/telescope", comment)
        self.assertNotIn("écartée", comment)
        plan_id = self.stored_plan(run_dir)["plan_id"]
        (run_dir / "probes.json").unlink()
        self.write(run_dir, "tools/http/result.json", {"tool": "http", "status": "failed", "reason": "x"})
        (run_dir / "context-docs" / "d1" / "items.json").unlink()
        self.plan(run_dir)
        self.assertEqual(self.stored_plan(run_dir)["plan_id"], plan_id)

    def test_the_same_content_in_a_run_of_0_18_1_follows_the_new_rules(self):
        run_dir = self.make_legacy_run(version="0.18.1")
        summary, err = self.plan(run_dir)
        ops = self.ops_by_ref(run_dir)
        self.assertEqual(ops[101], [("comment", "ko", None)])
        self.assertIn(101, summary["blocking_non_conform"])
        self.assertEqual(list(self.reasons(summary)), [101])
        self.assertIn("preuve n° 2 (http) écartée", self.reasons(summary)[101])
        self.assertIn("Constats écartés : 1.", err)
        self.assertEqual(ops[104], [("comment", "unknown", None)])
        self.assertEqual([kind for kind, _, _ in ops[103]], ["status", "assign", "comment"])
        self.assertEqual(ops[103][0], ("status", "ok_human", "Mis en place"))
        self.assertEqual(summary["unknown"], [104, 106])

    def test_a_written_run_of_0_18_0_is_planned_again_without_any_operation(self):
        """runs/L1 of the review: the plan written by 0.18.0, the copy read again, then plan."""
        run_dir = self.make_legacy_run()
        self.plan(run_dir)
        plan = self.stored_plan(run_dir)
        after = {state["ref"]: state["after"] for state in plan["rows"]}
        visible = {}
        for op in plan["ops"]:
            if op["kind"] == "comment":
                visible.setdefault(str(op["ref"]), []).append(op["verdict"])

        def written(document):
            for row in document["rows"]:
                row["status"] = after.get(row["ref"], row["status"])
                row["last_edited_by_bot"] = True

        self.edit(run_dir, "rows.json", written)
        self.write(run_dir, "comments.json", visible)
        summary, _ = self.plan(run_dir)
        self.assertEqual(self.stored_plan(run_dir)["ops"], [])
        self.assertEqual(summary["discarded"], [])
        self.assertEqual((summary["unknown"], summary["blocking_non_conform"]), ([106], [101]))


class LegacyReadingTest(LegacyCase):
    def grounded(self, run_dir, lot_id=PERF, name=None):
        context = self.read(run_dir, "context.json")
        lot = next(lot for lot in self.read(run_dir, "lots.json")["lots"] if lot["id"] == lot_id)
        return rerun.read_grounded_findings(run_dir, context, lot, name or f"{lot_id}.json")

    def test_a_legacy_finding_keeps_the_evidence_the_new_checks_refuse(self):
        run_dir = self.make_legacy_run()
        self.write(run_dir, "tools/lighthouse/summary.json", {"tool": "lighthouse", "findings": []})
        evidence = [
            {"type": "page", "url": OUTSIDE, "detail": "vu"},
            {"type": "http", "url": "https://preprod.example.org/jamais-sondee", "detail": "200"},
            {"type": "tool", "tool": "lighthouse", "ref": "summary.json#home"},
        ]
        self.edit_finding(run_dir, PERF, 102, evidence=evidence)
        valid, errors = self.grounded(run_dir)
        self.assertEqual(errors, [])
        by_ref = {item["ref"]: item for item in valid}
        self.assertEqual(by_ref[102]["evidence"], evidence)
        self.assertTrue(by_ref[102][LEGACY_KEY])
        summary, _ = self.plan(run_dir)
        self.assertEqual(self.ops_by_ref(run_dir)[102][0], ("status", "ok", "Ok / Recetté"))

    def test_the_checks_of_0_18_0_still_hold_in_a_legacy_run(self):
        run_dir = self.make_legacy_run()
        self.edit_finding(run_dir, PERF, 101, evidence=[{"type": "file", "repo": "laravel", "path": "ghost.php"}])
        self.edit_finding(run_dir, PERF, 102, evidence=[{"type": "tool", "tool": "crawl", "ref": "summary.json"}])
        valid, errors = self.grounded(run_dir)
        self.assertEqual(valid, [])
        self.assertEqual(len(errors), 2)
        self.assertIn("introuvable", errors[0])
        self.assertIn("sans résumé", errors[1])

    def test_a_legacy_file_may_hold_what_0_18_1_refuses_in_a_lot_file(self):
        run_dir = self.make_legacy_run()
        document = self.read(run_dir, f"findings/{PERF}.json")
        document["lot"] = "autre-lot"
        document["findings"][1]["foo"] = 1
        self.write(run_dir, f"findings/{PERF}.json", document)
        valid, errors = self.grounded(run_dir)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(item["ref"] for item in valid), [101, 102])

    def test_an_absent_or_unreadable_version_is_a_legacy_run(self):
        for version in (None, "", "0.18.0", "0.9.0", "0.18.1-dev", "abc"):
            with self.subTest(version=version):
                run_dir = self.make_legacy_run(version=version)
                self.assertTrue(contract.legacy_run(self.read(run_dir, "context.json")))
                valid, errors = self.grounded(run_dir)
                self.assertEqual((errors, sorted(item["ref"] for item in valid)), ([], [101, 102]))
        for version in ("0.18.1", "0.19.0", "1.0.0"):
            with self.subTest(version=version):
                self.assertFalse(contract.legacy_run({"plugin_version": version}))
        self.assertTrue(contract.legacy_run(None))

    def test_validate_applies_the_new_rules_and_the_file_then_follows_them(self):
        run_dir = self.make_legacy_run()
        errors = self.validate_errors(run_dir, PERF)
        self.assertEqual(len(errors), 1)
        self.assertIn("CQ-101", errors[0])
        self.assertIn("écartée", errors[0])
        self.assertTrue((run_dir / f"findings/{PERF}.checked.json").is_file())
        valid, _ = self.grounded(run_dir)
        kept = {item["ref"]: item for item in valid}
        self.assertEqual(kept[101]["evidence"], [KERNEL])
        self.assertNotIn(LEGACY_KEY, kept[101])
        summary, _ = self.plan(run_dir)
        self.assertIn(101, self.reasons(summary))
        self.assertEqual(self.ops_by_ref(run_dir)[104], [("status", "na", "Non applicable"), ("comment", "na", None)])

    def test_a_file_validated_since_follows_the_new_rules_even_when_rewritten(self):
        run_dir = self.make_legacy_run()
        self.validate_errors(run_dir, PERF)
        self.edit_finding(run_dir, PERF, 102, summary="Texte corrigé après scan.")
        valid, errors = self.grounded(run_dir)
        self.assertTrue(any("écartée" in error for error in errors))
        self.assertTrue(all(LEGACY_KEY not in item for item in valid))

    def test_a_rerun_file_validated_after_the_update_follows_the_new_rules(self):
        run_dir = self.make_legacy_run()
        self.assertEqual(self.run_json(["rerun-start", run_dir])["rerun"], "01")
        self.write(run_dir, f"findings/{SECU}-rerun-01.json", {"lot": SECU, "findings": [finding(104, "na", [])]})
        code, out, err = self.run_main(["validate", run_dir, SECU, "--rerun", "1", "--rows", "104"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.run_json(["rerun-close", run_dir, "1"])["discarded"], [])
        summary, _ = self.plan(run_dir)
        ops = self.ops_by_ref(run_dir)
        self.assertEqual(ops[104], [("comment", "unknown", None)])
        self.assertEqual(ops[101], [("comment", "ko", None)])
        self.assertNotIn(101, self.reasons(summary))

    def test_rerun_close_reads_a_legacy_rerun_file_with_the_old_rules_and_records_nothing(self):
        run_dir = self.make_legacy_run()
        self.run_json(["rerun-start", run_dir])
        rerun_finding = finding(101, "ko", [KERNEL, HAND_MADE], todo="Fermer Telescope.")
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", {"lot": PERF, "findings": [rerun_finding]})
        self.assertEqual(self.run_json(["rerun-close", run_dir, "1"])["discarded"], [])
        self.assertFalse((run_dir / f"findings/{PERF}.checked.json").exists())
        valid, errors = self.grounded(run_dir, name=f"{PERF}-rerun-01.json")
        self.assertEqual(errors, ["constat manquant : CQ-102"])
        self.assertEqual(valid[0]["evidence"], [KERNEL, HAND_MADE])

    def test_the_followup_probes_of_a_legacy_file_are_still_asked(self):
        run_dir = self.make_run()
        self.set_version(run_dir, "0.18.0")
        document = self.read(run_dir, f"findings/{SECU}.json")
        document["findings"][0]["foo"] = 1
        self.write(run_dir, f"findings/{SECU}.json", document)
        paths = [probe["path"] for probe in probes.followup_probes(run_dir)["probes"]]
        self.assertEqual(paths, ["/telescope", "/status"])
        self.set_version(run_dir, "0.18.1")
        paths = [probe["path"] for probe in probes.followup_probes(run_dir)["probes"]]
        self.assertEqual(paths, ["/telescope"])


class LegacyContractTest(unittest.TestCase):
    def test_a_block_of_the_record_makes_a_file_strict_in_a_legacy_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "findings").mkdir()
            legacy = {"plugin_version": "0.18.0"}
            self.assertFalse(contract.strict_file(run_dir, legacy, PERF, f"{PERF}.json"))
            record = {"lot": PERF, "files": {f"{PERF}.json": {"sha256": "x", "findings": []}}}
            (run_dir / "findings" / f"{PERF}.checked.json").write_text(json.dumps(record), encoding="utf-8")
            self.assertTrue(contract.strict_file(run_dir, legacy, PERF, f"{PERF}.json"))
            self.assertFalse(contract.strict_file(run_dir, legacy, PERF, f"{PERF}-followup.json"))
            self.assertTrue(contract.strict_file(run_dir, {"plugin_version": "0.18.1"}, PERF, "x.json"))


if __name__ == "__main__":
    unittest.main()
