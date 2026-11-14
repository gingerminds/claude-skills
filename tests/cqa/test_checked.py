"""The evidence check frozen when a findings file is validated (findings/<lot>.checked.json)."""
import hashlib
import json
import re
import shutil
from pathlib import Path

from cqa import checked, rerun
from tests.cqa.test_rerun import RerunCase, file_evidence, finding

LOT = "code-securite"
NAME = f"{LOT}.json"
RECORD = f"findings/{LOT}.checked.json"
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def evidence_of(path="app/a.php"):
    return [file_evidence(path), {"type": "tool", "tool": "http", "ref": "summary.json#root"}]


class FreezeCase(RerunCase):
    def setUp(self):
        super().setUp()
        repo = self.run_dir / "repo"
        (repo / "app").mkdir(parents=True)
        (repo / "app" / "a.php").write_text("<?php\n", encoding="utf-8")
        (repo / "app" / "b.php").write_text("<?php\n", encoding="utf-8")
        self.write("tools/http/summary.json", {})
        self.first_round(*(finding(ref, evidence=evidence_of()) for ref in (1, 2, 3)), fill=False)

    @property
    def lot(self):
        return self.lots[0]

    def context(self):
        return json.loads((self.run_dir / "context.json").read_text(encoding="utf-8"))

    def read(self, freeze=None, name=NAME):
        return rerun.read_grounded_findings(
            self.run_dir, self.context(), self.lot, name, freeze=freeze
        )

    def record(self):
        return json.loads((self.run_dir / RECORD).read_text(encoding="utf-8"))

    def refs(self, found):
        return sorted(item["ref"] for item in found[0])

    def drop_file(self, path="app/a.php"):
        (self.run_dir / "repo" / path).unlink()


class RecordTest(FreezeCase):
    def test_reading_without_freeze_writes_nothing(self):
        self.read()
        self.assertFalse((self.run_dir / RECORD).exists())

    def test_refresh_records_what_was_checked_for_each_finding(self):
        found = self.read(freeze="refresh")
        self.assertEqual((self.refs(found), found[1]), ([1, 2, 3], []))
        text = (self.run_dir / "findings" / NAME).read_bytes()
        document = self.record()
        self.assertEqual(document["lot"], LOT)
        block = document["files"][NAME]
        self.assertEqual(block["sha256"], hashlib.sha256(text).hexdigest())
        self.assertEqual([entry["ref"] for entry in block["findings"]], [1, 2, 3])
        entry = block["findings"][0]
        self.assertEqual(entry["files"], [{"repo": "laravel", "path": "app/a.php"}])
        self.assertEqual(entry["tools"], ["http"])
        self.assertEqual(entry["problems"], [])
        self.assertRegex(entry["checked_at"], INSTANT)

    def test_a_finding_without_file_or_tool_evidence_gets_an_empty_entry(self):
        self.first_round(
            finding(1, evidence=[{"type": "page", "url": "https://preprod.example.org/"}]),
            fill=False,
        )
        self.read(freeze="refresh")
        entry = self.record()["files"][NAME]["findings"][0]
        self.assertEqual((entry["files"], entry["tools"], entry["problems"]), ([], [], []))

    def test_the_problems_of_a_false_evidence_are_recorded_and_returned(self):
        self.first_round(
            finding(1, evidence=[file_evidence("app/ghost.php")]),
            finding(2, evidence=evidence_of()),
            fill=False,
        )
        found = self.read(freeze="refresh")
        self.assertEqual(self.refs(found), [2])
        refused = [error for error in found[1] if "ghost" in error]
        self.assertEqual(len(refused), 1)
        self.assertIn("CQ-1", refused[0])
        self.assertIn("app/ghost.php", refused[0])
        by_ref = {entry["ref"]: entry for entry in self.record()["files"][NAME]["findings"]}
        self.assertIn("app/ghost.php", by_ref[1]["problems"][0])
        self.assertEqual(by_ref[2]["problems"], [])

    def test_a_file_without_a_valid_finding_leaves_no_record(self):
        (self.run_dir / "findings" / NAME).write_text('{"lot": "x"}', encoding="utf-8")
        self.read(freeze="refresh")
        self.assertFalse((self.run_dir / RECORD).exists())

    def test_a_missing_or_unreadable_file_leaves_no_record(self):
        self.assertIsNone(self.read(freeze="refresh", name="code-securite-rerun-01.json"))
        (self.run_dir / "findings" / NAME).write_text("{", encoding="utf-8")
        found = self.read(freeze="refresh")
        self.assertEqual(found[0], [])
        self.assertIn("illisible", found[1][0])
        self.assertFalse((self.run_dir / RECORD).exists())

    def test_each_file_of_the_lot_has_its_own_block(self):
        self.findings(
            "code-securite-followup", finding(1, evidence=evidence_of("app/b.php")), lot=LOT
        )
        self.read(freeze="refresh")
        self.read(freeze="refresh", name="code-securite-followup.json")
        files = self.record()["files"]
        self.assertEqual(sorted(files), sorted([NAME, "code-securite-followup.json"]))
        self.assertEqual(files[NAME]["findings"][0]["files"][0]["path"], "app/a.php")
        self.assertEqual(files["code-securite-followup.json"]["findings"][0]["files"][0]["path"], "app/b.php")


class FrozenReadTest(FreezeCase):
    def test_a_recorded_finding_survives_the_deletion_of_its_cited_file(self):
        self.read(freeze="refresh")
        self.drop_file()
        found = self.read()
        self.assertEqual((self.refs(found), found[1]), ([1, 2, 3], []))

    def test_a_recorded_finding_survives_the_loss_of_its_tool_summary(self):
        self.read(freeze="refresh")
        (self.run_dir / "tools" / "http" / "summary.json").unlink()
        self.assertEqual(self.refs(self.read()), [1, 2, 3])

    def test_without_a_record_the_check_is_played_against_the_run_again(self):
        self.drop_file()
        found = self.read()
        self.assertEqual(self.refs(found), [])
        self.assertEqual(len(found[1]), 3)

    def test_a_recorded_problem_stays_even_when_the_file_exists_again(self):
        self.drop_file()
        self.read(freeze="refresh")
        (self.run_dir / "repo" / "app" / "a.php").write_text("<?php\n", encoding="utf-8")
        found = self.read()
        self.assertEqual(self.refs(found), [])
        self.assertEqual(len(found[1]), 3)
        self.assertIn("introuvable", found[1][0])

    def test_refreshing_checks_the_run_again_and_replaces_the_record(self):
        self.drop_file()
        self.read(freeze="refresh")
        (self.run_dir / "repo" / "app" / "a.php").write_text("<?php\n", encoding="utf-8")
        found = self.read(freeze="refresh")
        self.assertEqual((self.refs(found), found[1]), ([1, 2, 3], []))
        self.assertEqual(self.record()["files"][NAME]["findings"][0]["problems"], [])

    def test_a_file_rewritten_after_its_validation_is_not_covered_by_the_record(self):
        self.read(freeze="refresh")
        self.first_round(
            *(finding(ref, evidence=evidence_of("app/b.php")) for ref in (1, 2, 3)), fill=False
        )
        self.drop_file("app/b.php")
        found = self.read()
        self.assertEqual(self.refs(found), [])
        self.assertIn("app/b.php", found[1][0])

    def test_a_ref_the_record_does_not_hold_is_checked_against_the_run(self):
        self.read(freeze="refresh")
        document = self.record()
        document["files"][NAME]["findings"] = [
            entry for entry in document["files"][NAME]["findings"] if entry["ref"] != 3
        ]
        (self.run_dir / RECORD).write_text(json.dumps(document), encoding="utf-8")
        self.drop_file()
        found = self.read()
        self.assertEqual(self.refs(found), [1, 2])
        self.assertEqual(len(found[1]), 1)
        self.assertIn("CQ-3", found[1][0])

    def test_damaged_records_are_ignored_and_the_check_is_played_again(self):
        self.read(freeze="refresh")
        good = self.record()
        sha = good["files"][NAME]["sha256"]
        damaged = {
            "not json": "{",
            "a list": "[]",
            "no files": json.dumps({"lot": LOT}),
            "files is a list": json.dumps({"files": []}),
            "block is a list": json.dumps({"files": {NAME: []}}),
            "no findings": json.dumps({"files": {NAME: {"sha256": sha}}}),
            "findings is text": json.dumps({"files": {NAME: {"sha256": sha, "findings": "x"}}}),
            "problems are not texts": json.dumps(
                {"files": {NAME: {"sha256": sha, "findings": [{"ref": 1, "problems": [1]}]}}}
            ),
            "ref is not a number": json.dumps(
                {"files": {NAME: {"sha256": sha, "findings": [{"ref": "1", "problems": []}]}}}
            ),
        }
        for label, text in damaged.items():
            with self.subTest(label):
                (self.run_dir / RECORD).write_text(text, encoding="utf-8")
                self.drop_file()
                found = self.read()
                self.assertEqual(self.refs(found), [], label)
                self.assertEqual(len(found[1]), 3, label)
                (self.run_dir / "repo" / "app" / "a.php").write_text("<?php\n", encoding="utf-8")

    def test_a_record_for_a_folder_instead_of_a_file_is_ignored(self):
        (self.run_dir / RECORD).mkdir()
        self.drop_file()
        self.assertEqual(self.refs(self.read()), [])


class EnsureAndForgetTest(FreezeCase):
    def test_ensure_records_a_file_no_record_covers(self):
        self.read(freeze="ensure")
        self.assertEqual([e["ref"] for e in self.record()["files"][NAME]["findings"]], [1, 2, 3])

    def test_ensure_keeps_the_record_that_already_covers_the_file(self):
        self.read(freeze="refresh")
        before = (self.run_dir / RECORD).read_text(encoding="utf-8")
        self.drop_file()
        found = self.read(freeze="ensure")
        self.assertEqual(self.refs(found), [1, 2, 3])
        self.assertEqual((self.run_dir / RECORD).read_text(encoding="utf-8"), before)

    def test_ensure_records_again_a_file_rewritten_since(self):
        self.read(freeze="refresh")
        self.first_round(finding(1, evidence=evidence_of("app/b.php")), fill=False)
        self.read(freeze="ensure")
        entry = self.record()["files"][NAME]["findings"][0]
        self.assertEqual(entry["files"], [{"repo": "laravel", "path": "app/b.php"}])

    def test_forget_removes_the_block_of_one_file_only(self):
        self.findings("code-securite-rerun-01", finding(1, evidence=evidence_of()), lot=LOT)
        self.read(freeze="refresh")
        self.read(freeze="refresh", name="code-securite-rerun-01.json")
        checked.forget(self.run_dir, LOT, "code-securite-rerun-01.json")
        self.assertEqual(sorted(self.record()["files"]), [NAME])

    def test_forgetting_the_last_block_removes_the_record(self):
        self.read(freeze="refresh")
        checked.forget(self.run_dir, LOT, NAME)
        self.assertFalse((self.run_dir / RECORD).exists())

    def test_forget_without_a_record_or_a_block_is_silent(self):
        checked.forget(self.run_dir, LOT, NAME)
        self.read(freeze="refresh")
        checked.forget(self.run_dir, LOT, "code-securite-rerun-09.json")
        self.assertEqual(sorted(self.record()["files"]), [NAME])
        (self.run_dir / RECORD).write_text("{", encoding="utf-8")
        checked.forget(self.run_dir, LOT, NAME)

    def test_a_damaged_record_is_replaced_by_a_refresh(self):
        (self.run_dir / RECORD).write_text("{", encoding="utf-8")
        self.read(freeze="refresh")
        self.assertEqual(sorted(self.record()["files"]), [NAME])

    def test_the_record_holds_no_absolute_path(self):
        self.read(freeze="refresh")
        self.assertNotIn(str(self.run_dir), (self.run_dir / RECORD).read_text(encoding="utf-8"))
        shutil.rmtree(self.run_dir / "repo")
        self.assertEqual(self.refs(self.read()), [1, 2, 3])
