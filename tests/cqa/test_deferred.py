import json
import tempfile
import unittest
from pathlib import Path

from cqa import deferred
from tests.cqa.test_cli_audit import AuditCase


class DeferredModuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)

    def test_no_file_means_no_entry(self):
        self.assertEqual(deferred.open_items(self.run_dir), [])

    def test_unknown_kind_is_refused(self):
        with self.assertRaisesRegex(ValueError, "login"):
            deferred.add(self.run_dir, "other", "x")
        self.assertFalse((self.run_dir / "deferred.json").exists())

    def test_fix_deferred_kinds(self):
        self.assertIn("fix_question", deferred.KINDS)
        self.assertNotIn("fix_publish", deferred.KINDS)
        entry = deferred.add(self.run_dir, "fix_question", "Quel contact pour security.txt ?", "CQ-12")
        self.assertEqual(entry["kind"], "fix_question")

    def test_empty_detail_is_refused(self):
        with self.assertRaises(ValueError):
            deferred.add(self.run_dir, "gap", "  ")

    def test_malformed_ref_is_refused(self):
        with self.assertRaisesRegex(ValueError, "CQ-12"):
            deferred.add(self.run_dir, "gap", "x", "CQ-1,abc")

    def test_resolving_an_unknown_id_is_refused(self):
        with self.assertRaises(ValueError):
            deferred.resolve(self.run_dir, 3)

    def test_resolving_keeps_the_entry_and_the_answer(self):
        """A key left for the lead stays known after the pass resumes: the entry is resolved
        with the lead's answer, never deleted."""
        entry = deferred.add(self.run_dir, "fix_question", "front : clé laissée : commit de fusion")
        deferred.resolve(self.run_dir, entry["id"], answer="  Laisser cette clé ")
        [item] = deferred.all_items(self.run_dir)
        self.assertEqual((item["resolved"], item["answer"]), (True, "Laisser cette clé"))
        other = deferred.add(self.run_dir, "gap", "écart")
        deferred.resolve(self.run_dir, other["id"])
        self.assertNotIn("answer", deferred.all_items(self.run_dir)[1])

    def test_a_damaged_file_is_an_error(self):
        (self.run_dir / "deferred.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            deferred.open_items(self.run_dir)

    def test_malformed_items_are_an_error_not_a_traceback(self):
        for text in ('{"items": ["x"]}', '{"items": [{"id": "1"}]}', '{"items": [{"id": true}]}'):
            with self.subTest(text=text):
                (self.run_dir / "deferred.json").write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "illisible"):
                    deferred.open_items(self.run_dir)
                with self.assertRaisesRegex(ValueError, "illisible"):
                    deferred.add(self.run_dir, "gap", "x")


class DeferredCommandTest(AuditCase):
    def test_deferred_queue_roundtrip(self):
        run_dir = self.make_run()
        first = self.run_json(
            ["defer", run_dir, "--kind", "ssh_refused", "--detail", "docker restart web (écriture)",
             "--refs", "CQ-5, 12"]
        )
        second = self.run_json(["defer", run_dir, "--kind", "login", "--detail", "Back-office"])
        self.assertEqual((first["id"], second["id"]), (1, 2))
        document = self.read(run_dir, "deferred.json")
        self.assertEqual(set(document), {"items"})
        item = document["items"][0]
        self.assertEqual(set(item), {"id", "kind", "detail", "refs", "at", "resolved"})
        self.assertEqual((item["kind"], item["refs"], item["resolved"]), ("ssh_refused", [5, 12], False))
        self.assertRegex(item["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(document["items"][1]["refs"], [])

        listed = self.run_json(["deferred", run_dir])
        self.assertEqual([entry["id"] for entry in listed["items"]], [1, 2])

        closed = self.run_json(["deferred", run_dir, "--resolve", "1"])
        self.assertEqual([entry["id"] for entry in closed["items"]], [2])
        self.assertTrue(self.read(run_dir, "deferred.json")["items"][0]["resolved"])
        again = self.run_json(["defer", run_dir, "--kind", "gap", "--detail", "préprod en retard"])
        self.assertEqual(again["id"], 3)

        code, out, err = self.run_main(["deferred", run_dir, "--resolve", "9"])
        self.assertEqual(code, 2)
        self.assertIn("9", err)

    def test_deferred_lists_fix_kinds_apart(self):
        run_dir = self.make_run()
        self.run_json(["defer", run_dir, "--kind", "gap", "--detail", "écart"])
        self.run_json(["defer", run_dir, "--kind", "fix_question", "--detail", "Quel contact ?",
                       "--refs", "CQ-12"])
        self.run_json(["defer", run_dir, "--kind", "question", "--detail", "Autre"])
        audit = self.run_json(["deferred", run_dir])
        self.assertEqual([item["id"] for item in audit["items"]], [1, 3])
        fix = self.run_json(["deferred", run_dir, "--fix"])
        self.assertEqual([item["id"] for item in fix["items"]], [2])
        closed = self.run_json(["deferred", run_dir, "--fix", "--resolve", "2"])
        self.assertEqual(closed["items"], [])
        after = self.run_json(["deferred", run_dir])
        self.assertEqual([item["id"] for item in after["items"]], [1, 3])
        self.run_json(["deferred", run_dir, "--resolve", "1"])
        self.assertEqual([i["id"] for i in self.run_json(["deferred", run_dir])["items"]], [3])

    def test_deferred_resolves_with_the_lead_answer(self):
        run_dir = self.make_run()
        self.run_json(["defer", run_dir, "--kind", "fix_question", "--detail",
                       "front : clé laissée : commit de fusion"])
        closed = self.run_json(["deferred", run_dir, "--fix", "--resolve", "1", "--answer",
                                "C'est réglé, reprendre"])
        self.assertEqual(closed["items"], [])
        [item] = self.read(run_dir, "deferred.json")["items"]
        self.assertEqual((item["resolved"], item["answer"]), (True, "C'est réglé, reprendre"))
        code, out, err = self.run_main(["deferred", run_dir, "--fix", "--answer", "Laisser"])
        self.assertEqual(code, 2)
        self.assertIn("--resolve", err)
        self.assertNotIn("Traceback", err)

    def test_report_labels_fix_kinds(self):
        from cqa import report
        self.assertEqual(report._DEFERRED_KINDS["fix_question"], "question de correctif")

    def test_a_damaged_queue_exits_with_a_usage_error(self):
        run_dir = self.make_run()
        (run_dir / "deferred.json").write_text('{"items": ["x"]}', encoding="utf-8")
        code, out, err = self.run_main(["deferred", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("illisible", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_kind_and_bad_ref_exit_with_usage_errors(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["defer", run_dir, "--kind", "nope", "--detail", "x"])
        self.assertEqual(code, 2)
        self.assertIn("n'est pas un type connu", err)
        self.assertNotIn("Traceback", err)
        code, out, err = self.run_main(
            ["defer", run_dir, "--kind", "gap", "--detail", "x", "--refs", "CQ-1,abc"]
        )
        self.assertEqual(code, 2)
        self.assertIn("abc", err)
        self.assertFalse((run_dir / "deferred.json").exists())


if __name__ == "__main__":
    unittest.main()
