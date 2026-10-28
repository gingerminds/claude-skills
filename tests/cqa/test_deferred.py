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

    def test_empty_detail_is_refused(self):
        with self.assertRaises(ValueError):
            deferred.add(self.run_dir, "gap", "  ")

    def test_malformed_ref_is_refused(self):
        with self.assertRaisesRegex(ValueError, "CQ-12"):
            deferred.add(self.run_dir, "gap", "x", "CQ-1,abc")

    def test_resolving_an_unknown_id_is_refused(self):
        with self.assertRaises(ValueError):
            deferred.resolve(self.run_dir, 3)

    def test_a_damaged_file_is_an_error(self):
        (self.run_dir / "deferred.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            deferred.open_items(self.run_dir)


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

    def test_unknown_kind_and_bad_ref_exit_with_usage_errors(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["defer", run_dir, "--kind", "nope", "--detail", "x"])
        self.assertEqual(code, 2)
        code, out, err = self.run_main(
            ["defer", run_dir, "--kind", "gap", "--detail", "x", "--refs", "CQ-1,abc"]
        )
        self.assertEqual(code, 2)
        self.assertIn("abc", err)
        self.assertFalse((run_dir / "deferred.json").exists())


if __name__ == "__main__":
    unittest.main()
