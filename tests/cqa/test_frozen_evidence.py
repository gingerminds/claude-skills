"""A-02 and D-02: the evidence is frozen at validation, and plan names what it sets aside."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cqa import report

from tests.cqa.test_cli_audit import AuditCase

PERF = "code-performance"
RECORD = f"findings/{PERF}.checked.json"
GHOST = {"type": "file", "repo": "ghost", "path": "a.php"}


class FrozenCase(AuditCase):
    def make_run(self):
        run_dir = super().make_run()
        self.write(run_dir, "comments.json", {})
        return run_dir

    def validated_run(self):
        run_dir = self.make_run()
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        return run_dir

    def plan(self, run_dir, *flags):
        code, out, err = self.run_main(["plan", run_dir, *flags])
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def comment_of(self, run_dir, ref):
        plan = self.read(run_dir, "notion/plan.json")
        return [op for op in plan["ops"] if op["ref"] == ref and op["kind"] == "comment"]

    def reasons(self, summary):
        return {item["ref"]: item["reason"] for item in summary["discarded"]}

    def delete_kernel(self):
        (self.repo / "app" / "Http" / "Kernel.php").unlink()

    def fail_tool(self, run_dir, tool="http"):
        folder = run_dir / "tools" / tool
        (folder / "summary.json").unlink()
        self.write(run_dir, f"tools/{tool}/result.json", {"tool": tool, "status": "failed"})


class ValidateRecordTest(FrozenCase):
    def test_validate_writes_checked_record(self):
        run_dir = self.validated_run()
        document = self.read(run_dir, RECORD)
        self.assertEqual(document["lot"], PERF)
        text = (run_dir / "findings" / f"{PERF}.json").read_bytes()
        block = document["files"][f"{PERF}.json"]
        self.assertEqual(block["sha256"], hashlib.sha256(text).hexdigest())
        by_ref = {entry["ref"]: entry for entry in block["findings"]}
        self.assertEqual(sorted(by_ref), [101, 102])
        self.assertEqual(by_ref[101]["files"], [{"repo": "laravel", "path": "app/Http/Kernel.php"}])
        self.assertEqual(by_ref[101]["tools"], [])
        self.assertEqual(by_ref[102]["tools"], ["http"])
        for entry in by_ref.values():
            self.assertEqual(entry["problems"], [])
            self.assertRegex(entry["checked_at"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$")

    def test_validate_prints_what_it_always_printed(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["validate", run_dir, PERF])
        self.assertEqual((code, json.loads(out)), (0, {"ok": True, "errors": []}))

    def test_validate_still_refuses_a_finding_with_false_evidence(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [GHOST])
        errors = self.validate_errors(run_dir, PERF)
        self.assertEqual(len(errors), 1)
        self.assertIn("CQ-101", errors[0])
        self.assertIn("ghost", errors[0])
        entry = self.read(run_dir, RECORD)["files"][f"{PERF}.json"]["findings"][0]
        self.assertEqual(entry["ref"], 101)
        self.assertIn("ghost", entry["problems"][0])

    def test_validate_again_checks_the_run_again_and_replaces_the_record(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [GHOST])
        self.validate_errors(run_dir, PERF)
        self.set_evidence(run_dir, [{"type": "file", "repo": "laravel", "path": "app/Http/Kernel.php"}])
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        entry = self.read(run_dir, RECORD)["files"][f"{PERF}.json"]["findings"][0]
        self.assertEqual(entry["problems"], [])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])

    def test_the_followup_file_gets_its_own_block(self):
        run_dir = self.validated_run()
        document = self.read(run_dir, f"findings/{PERF}.json")
        self.write(run_dir, f"findings/{PERF}-followup.json", document)
        code, out, err = self.run_main(["validate", run_dir, PERF, "--followup"])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            sorted(self.read(run_dir, RECORD)["files"]), [f"{PERF}-followup.json", f"{PERF}.json"]
        )

    def test_a_lot_covered_by_a_document_leaves_no_record(self):
        run_dir = self.make_run()
        lots = self.read(run_dir, "lots.json")
        lots["lots"][0]["document_refs"] = lots["lots"][0]["refs"]
        self.write(run_dir, "lots.json", lots)
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        self.assertFalse((run_dir / RECORD).exists())


class FrozenPlanTest(FrozenCase):
    def test_plan_does_not_revalidate_checked_findings_after_file_deleted(self):
        run_dir = self.validated_run()
        first, _ = self.plan(run_dir)
        before = self.read(run_dir, "notion/plan.json")
        self.assertEqual([op["kind"] for op in before["ops"] if op["ref"] == 101], ["comment"])
        self.delete_kernel()
        second, err = self.plan(run_dir)
        after = self.read(run_dir, "notion/plan.json")
        self.assertEqual(after["ops"], before["ops"])
        self.assertEqual(after["plan_id"], before["plan_id"])
        self.assertNotIn(101, second["unknown"])
        self.assertEqual(second["unknown"], first["unknown"])
        self.assertEqual(self.comment_of(run_dir, 101)[0]["verdict"], "ko")
        self.assertNotIn("CQ-101", err)

    def test_plan_does_not_revalidate_after_tool_summary_removed(self):
        run_dir = self.validated_run()
        self.plan(run_dir)
        before = self.read(run_dir, "notion/plan.json")
        self.fail_tool(run_dir)
        second, err = self.plan(run_dir)
        after = self.read(run_dir, "notion/plan.json")
        self.assertEqual(after["ops"], before["ops"])
        self.assertEqual(after["plan_id"], before["plan_id"])
        self.assertNotIn(102, second["unknown"])
        self.assertNotIn("CQ-102", err)

    def test_legacy_run_without_checked_record_still_revalidates(self):
        run_dir = self.make_run()
        self.assertFalse((run_dir / RECORD).exists())
        self.delete_kernel()
        summary, err = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertEqual(self.comment_of(run_dir, 101)[0]["verdict"], "unknown")
        self.assertIn("app/Http/Kernel.php", self.reasons(summary)[101])
        self.assertIn("CQ-101", err)

    def test_a_damaged_record_falls_back_on_the_check_against_the_run(self):
        run_dir = self.validated_run()
        (run_dir / RECORD).write_text("{not json", encoding="utf-8")
        self.delete_kernel()
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])

    def test_a_findings_file_changed_after_its_validation_is_checked_again(self):
        run_dir = self.validated_run()
        self.set_evidence(run_dir, [{"type": "file", "repo": "laravel", "path": "app/Other.php"}])
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertIn("app/Other.php", self.reasons(summary)[101])

    def test_a_false_evidence_stays_refused_and_is_named(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [GHOST])
        self.validate_errors(run_dir, PERF)
        summary, err = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertIn("ghost", self.reasons(summary)[101])
        self.assertEqual(self.comment_of(run_dir, 101)[0]["verdict"], "unknown")

    def test_rerun_candidates_use_checked_record(self):
        run_dir = self.validated_run()
        self.delete_kernel()
        result = self.run_json(["rerun-candidates", run_dir])
        self.assertNotIn(101, {item["ref"] for item in result["candidates"]})
        legacy = self.make_run()
        self.delete_kernel()
        result = self.run_json(["rerun-candidates", legacy])
        by_ref = {item["ref"]: item for item in result["candidates"]}
        self.assertEqual(by_ref[101]["reasons"][0], "cause_levee")
        self.assertIn("aucun constat valide", by_ref[101]["details"][0])


class RerunFileFreezeTest(FrozenCase):
    def write_rerun(self, run_dir, evidence=None):
        source = self.read(run_dir, f"findings/{PERF}.json")["findings"][0]
        if evidence is not None:
            source["evidence"] = evidence
        source["needs_probes"] = []
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", {"lot": PERF, "findings": [source]})

    def test_validate_of_a_rerun_file_writes_its_block(self):
        run_dir = self.make_run()
        self.write_rerun(run_dir)
        code, out, err = self.run_main(["validate", run_dir, PERF, "--rerun", "1", "--rows", "101"])
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.read(run_dir, RECORD)["files"]), [f"{PERF}-rerun-01.json"])

    def test_rerun_close_freezes_a_rerun_file_that_was_not_validated(self):
        run_dir = self.make_run()
        self.write_rerun(run_dir)
        self.run_json(["rerun-close", run_dir, "1"])
        self.assertEqual(sorted(self.read(run_dir, RECORD)["files"]), [f"{PERF}-rerun-01.json"])
        self.delete_kernel()
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])

    def test_rerun_close_keeps_the_record_made_at_validation(self):
        run_dir = self.make_run()
        self.write_rerun(run_dir)
        self.run_main(["validate", run_dir, PERF, "--rerun", "1", "--rows", "101"])
        stamp = self.read(run_dir, RECORD)
        self.delete_kernel()
        result = self.run_json(["rerun-close", run_dir, "1"])
        self.assertEqual(result["discarded"], [])
        self.assertEqual(self.read(run_dir, RECORD), stamp)

    def test_rerun_close_forgets_the_record_of_a_rerun_file_it_discards(self):
        run_dir = self.validated_run()
        self.write_rerun(run_dir, evidence=[GHOST])
        result = self.run_json(["rerun-close", run_dir, "1"])
        self.assertEqual(result["discarded"], [f"{PERF}-rerun-01.json"])
        self.assertEqual(sorted(self.read(run_dir, RECORD)["files"]), [f"{PERF}.json"])

    def test_rerun_start_forgets_the_record_of_an_abandoned_rerun_file(self):
        run_dir = self.validated_run()
        self.write_rerun(run_dir)
        self.run_main(["validate", run_dir, PERF, "--rerun", "1", "--rows", "101"])
        self.assertIn(f"{PERF}-rerun-01.json", self.read(run_dir, RECORD)["files"])
        result = self.run_json(["rerun-start", run_dir])
        self.assertEqual(result["abandoned"], [f"{PERF}-rerun-01.json"])
        self.assertEqual(sorted(self.read(run_dir, RECORD)["files"]), [f"{PERF}.json"])


class DiscardedTest(FrozenCase):
    def test_plan_prints_discarded_with_reason(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [GHOST])
        summary, err = self.plan(run_dir)
        reasons = self.reasons(summary)
        self.assertEqual(sorted(reasons), [101, 104])
        self.assertIn("ghost", reasons[101])
        self.assertIn(f"{PERF}.json", reasons[101])
        self.assertIn("Non évaluable", reasons[101])
        self.assertEqual(summary["discarded_count"], 2)
        self.assertTrue(all(set(item) == {"ref", "reason"} for item in summary["discarded"]))
        self.assertIn("CQ-101", err)
        self.assertIn("ghost", err)
        self.assertIn("CQ-104", err)
        self.assertIn("2", err)

    def test_the_summary_keys_are_still_printed(self):
        run_dir = self.make_run()
        summary, _ = self.plan(run_dir)
        stored = self.read(run_dir, "notion/plan.json")["summary"]
        self.assertEqual({k: v for k, v in summary.items() if k in stored}, stored)
        self.assertEqual(set(summary) - set(stored), {"discarded", "discarded_count"})

    def test_the_discarded_list_does_not_enter_the_plan(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        first = self.read(run_dir, "notion/plan.json")
        self.assertNotIn("discarded", first)
        self.assertNotIn("discarded", first["summary"])
        self.set_evidence(run_dir, [GHOST])
        self.plan(run_dir)
        self.assertNotIn("discarded", self.read(run_dir, "notion/plan.json"))

    def test_a_plan_with_nothing_set_aside_says_so_by_a_zero_and_prints_nothing_on_stderr(self):
        run_dir = self.make_run()
        self.edit_finding(run_dir, "code-securite", 104, verdict="ko", todo="x")
        summary, err = self.plan(run_dir)
        self.assertEqual((summary["discarded"], summary["discarded_count"]), ([], 0))
        self.assertNotIn("écart", err)

    def test_a_row_of_a_lot_that_wrote_no_file_is_named(self):
        run_dir = self.make_run()
        (run_dir / "findings" / "code-securite.json").unlink()
        summary, _ = self.plan(run_dir)
        reasons = self.reasons(summary)
        self.assertEqual(sorted(reasons), [103, 104])
        self.assertIn("findings/code-securite.json", reasons[103])
        self.assertIn("introuvable", reasons[103])

    def test_the_rows_that_share_a_reason_are_grouped_on_stderr(self):
        run_dir = self.make_run()
        (run_dir / "findings" / "code-securite.json").unlink()
        summary, err = self.plan(run_dir)
        lines = [line for line in err.splitlines() if line.startswith("- ")]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("- CQ-103, CQ-104 : "))
        self.assertEqual(summary["discarded_count"], 2)

    def test_a_rerun_file_never_reports_the_rows_it_was_not_asked_for(self):
        run_dir = self.make_run()
        source = self.read(run_dir, f"findings/{PERF}.json")["findings"][0]
        source["needs_probes"] = []
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", {"lot": PERF, "findings": [source]})
        summary, err = self.plan(run_dir)
        self.assertEqual(self.reasons(summary).get(102), None)
        self.assertEqual(sorted(self.reasons(summary)), [104])

    def test_a_row_the_file_does_not_hold_is_named(self):
        run_dir = self.make_run()
        document = self.read(run_dir, "findings/code-performance.json")
        document["findings"] = document["findings"][:1]
        self.write(run_dir, "findings/code-performance.json", document)
        summary, _ = self.plan(run_dir)
        self.assertIn("aucun constat", self.reasons(summary)[102])
        self.assertIn(f"{PERF}.json", self.reasons(summary)[102])

    def test_a_finding_set_aside_in_a_later_file_names_the_one_that_is_kept(self):
        run_dir = self.make_run()
        document = self.read(run_dir, f"findings/{PERF}.json")
        document["findings"][0]["evidence"] = [GHOST]
        document["findings"][0]["needs_probes"] = []
        self.write(run_dir, f"findings/{PERF}-followup.json", document)
        summary, _ = self.plan(run_dir)
        reason = self.reasons(summary)[101]
        self.assertIn(f"{PERF}-followup.json", reason)
        self.assertIn("ghost", reason)
        self.assertIn(f"{PERF}.json", reason)
        self.assertIn("gardé", reason)
        self.assertNotIn(101, summary["unknown"])

    def test_the_reason_never_carries_a_long_dash(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [GHOST])
        summary, err = self.plan(run_dir)
        text = json.dumps(summary, ensure_ascii=False) + err
        self.assertNotIn(chr(0x2014), text)


class WrittenRowTest(FrozenCase):
    def write_journal(self, run_dir, *entries):
        self.write(run_dir, "notion/applied.json", {"applied": list(entries)})

    def test_plan_never_replaces_comment_of_written_row_with_unknown(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        self.write_journal(
            run_dir,
            {"key": "comment:page-101:aaaaaaaaaaaa", "kind": "comment", "ref": 101,
             "verdict": "ko", "at": "2026-10-20T15:00:00Z"},
        )
        self.set_evidence(run_dir, [GHOST])
        self.edit_finding(run_dir, PERF, 102, evidence=[GHOST])
        summary, err = self.plan(run_dir)
        plan = self.read(run_dir, "notion/plan.json")
        self.assertEqual([op for op in plan["ops"] if op["ref"] == 101], [])
        self.assertEqual(self.comment_of(run_dir, 102)[0]["verdict"], "unknown")
        self.assertIn(101, summary["unknown"])
        self.assertIn("commentaire déjà écrit", self.reasons(summary)[101])
        self.assertNotIn("commentaire déjà écrit", self.reasons(summary)[102])
        self.assertIn("CQ-101", err)

    def test_a_status_entry_of_an_earlier_run_of_the_copy_protects_the_row_too(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        earlier = run_dir.parent / "2026-10-19-1000"
        self.write(earlier, "context.json", {"notion": {"copy_data_source_id": self.copy_id(run_dir)}})
        self.write(
            earlier, "notion/applied.json",
            {"applied": [{"key": "status:page-101:x", "kind": "status", "ref": 101,
                          "value": "À traiter", "at": "2026-10-19T10:00:00Z"}]},
        )
        self.set_evidence(run_dir, [GHOST])
        self.plan(run_dir)
        plan = self.read(run_dir, "notion/plan.json")
        self.assertEqual([op for op in plan["ops"] if op["ref"] == 101], [])

    def copy_id(self, run_dir):
        return self.read(run_dir, "context.json")["notion"]["copy_data_source_id"]

    def test_a_row_the_journal_wrote_as_unknown_is_planned_again_the_same(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        first = self.read(run_dir, "notion/plan.json")
        unknown = next(op for op in first["ops"] if op["ref"] == 104)
        self.assertEqual(unknown["verdict"], "unknown")
        self.write_journal(
            run_dir,
            {"key": unknown["key"], "kind": "comment", "ref": 104, "verdict": "unknown",
             "at": "2026-10-20T15:00:00Z"},
        )
        self.plan(run_dir)
        again = self.read(run_dir, "notion/plan.json")
        self.assertEqual(again["plan_id"], first["plan_id"])
        self.assertEqual(again["ops"], first["ops"])

    def test_a_row_with_a_valid_finding_is_planned_whatever_the_journal_holds(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        self.write_journal(
            run_dir,
            {"key": "comment:page-101:aaaaaaaaaaaa", "kind": "comment", "ref": 101,
             "verdict": "ko", "at": "2026-10-20T15:00:00Z"},
        )
        self.plan(run_dir)
        plan = self.read(run_dir, "notion/plan.json")
        self.assertEqual(self.comment_of(run_dir, 101)[0]["verdict"], "ko")


class RecetteScenarioTest(AuditCase):
    """The recette of the review: a cited file is gone and a tool failed on its relaunch."""

    def make_recette_run(self):
        """A run whose recette lists 102, judged again by findings/code-performance-rerun-01.json."""
        run_dir = self.make_run()
        self.write(run_dir, "comments.json", {})
        row = {"ref": 102, "page_id": f"{102:032x}", "status": "Mis en place",
               "skill_last": "À traiter", "comments": []}
        self.write(run_dir, "notion/recette.json", {"rows": [row]})
        self.write(run_dir, "reruns.json", {"closed": ["01"]})
        self.edit_row(run_dir, 102, status="Mis en place")
        source = next(
            item for item in self.read(run_dir, f"findings/{PERF}.json")["findings"]
            if item["ref"] == 102
        )
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", {"lot": PERF, "findings": [source]})
        return run_dir

    def scenario(self, *, validate):
        run_dir = self.make_recette_run()
        if validate:
            self.assertEqual(self.validate_errors(run_dir, PERF), [])
            code, out, err = self.run_main(
                ["validate", run_dir, PERF, "--rerun", "1", "--rows", "102"]
            )
            self.assertEqual(code, 0, err)
        self.run_json(["plan", run_dir, "--recette", "01"])
        first = self.read(run_dir, "notion/plan.json")
        (self.repo / "app" / "Http" / "Kernel.php").unlink()
        (run_dir / "tools" / "http" / "summary.json").unlink()
        self.write(run_dir, "tools/http/result.json", {"tool": "http", "status": "failed"})
        self.run_json(["plan", run_dir, "--recette", "01"])
        return first, self.read(run_dir, "notion/plan.json")

    def test_the_rows_not_judged_again_keep_their_status_and_their_comment(self):
        first, second = self.scenario(validate=True)
        self.assertEqual(second["ops"], first["ops"])
        self.assertEqual(second["plan_id"], first["plan_id"])
        self.assertEqual(second["summary"]["unknown"], first["summary"]["unknown"])
        self.assertTrue(any(op["ref"] == 101 and op["kind"] == "comment" for op in second["ops"]))

    def test_without_the_record_the_same_scenario_replaces_them(self):
        first, second = self.scenario(validate=False)
        self.assertNotEqual(second["plan_id"], first["plan_id"])
        refs = {op["ref"] for op in second["ops"] if op["kind"] == "comment"
                and op["verdict"] == "unknown"}
        self.assertIn(101, refs)


class ToolManifestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)

    def tool(self, name, result, attempt=None):
        folder = self.run_dir / "tools" / name
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
        if attempt is not None:
            (folder / "result-last-attempt.json").write_text(json.dumps(attempt), encoding="utf-8")

    def test_the_manifest_ignores_a_folder_left_set_aside_by_an_interrupted_launch(self):
        self.tool("http", {"tool": "http", "status": "ok"})
        self.tool("http.prev", {"tool": "http", "status": "ok"})
        names = [item["tool"] for item in report.manifest(self.run_dir)["tools"]]
        self.assertEqual(names, ["http"])

    def test_the_manifest_reports_the_last_attempt_of_a_tool(self):
        attempt = {"tool": "http", "status": "failed", "reason": "502", "started_at": "2026-11-14T10:00:00Z",
                   "files": ["x"], "counts": {"a": 1}}
        self.tool("http", {"tool": "http", "status": "ok", "started_at": "2026-11-01T09:00:00Z"}, attempt)
        self.tool("lighthouse", {"tool": "lighthouse", "status": "ok"})
        by_name = {item["tool"]: item for item in report.manifest(self.run_dir)["tools"]}
        self.assertEqual(
            by_name["http"]["last_attempt"],
            {"status": "failed", "reason": "502", "started_at": "2026-11-14T10:00:00Z"},
        )
        self.assertNotIn("last_attempt", by_name["lighthouse"])

    def test_an_unreadable_last_attempt_is_reported_as_failed(self):
        self.tool("http", {"tool": "http", "status": "ok"})
        (self.run_dir / "tools" / "http" / "result-last-attempt.json").write_text("{", encoding="utf-8")
        item = report.manifest(self.run_dir)["tools"][0]
        self.assertEqual(item["last_attempt"]["status"], "failed")

    def test_the_synthesis_says_the_last_attempt_failed_and_the_result_is_kept(self):
        item = {
            "tool": "http", "status": "ok",
            "last_attempt": {"status": "failed", "reason": "502", "started_at": "2026-11-14T10:00:00Z"},
        }
        lines = report._tool_lines({"tools": [item, {"tool": "assets", "status": "ok"}]})
        self.assertEqual(lines[1], "- assets : ok")
        self.assertTrue(lines[0].startswith("- http : ok"))
        self.assertIn("dernier essai", lines[0])
        self.assertIn("en échec", lines[0])
        self.assertIn("502", lines[0])
        self.assertIn("conservé", lines[0])
        self.assertNotIn(chr(0x2014), lines[0])


if __name__ == "__main__":
    unittest.main()
