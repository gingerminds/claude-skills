import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cqa import fix_gitleaks
from cqa.fix_gitleaks import GITLEAKS_DOWN, Gitleaks

LAUNCHER = Path("/x/cqa-toolbox.sh")
CANARY = "CANARY-VALUE-NEVER-COPIED"
TEXT = "fix(securite CQ-3): pay\n\n<?php\n$x = 1;\n"


def finding(rule="stripe-access-token", first=4, last=4, **fields):
    return {"RuleID": rule, "StartLine": first, "EndLine": last, "Match": CANARY,
            "Secret": CANARY, "File": "", "Description": rule, **fields}


class FakeLauncher:
    """Answers bash cqa-toolbox.sh secrets <in> <out_dir> as the toolbox would: records the
    call and what it read, writes the report (when given) and returns the exit code."""

    def __init__(self, report="[]", code=0, stderr="", raises=None):
        self.report, self.code, self.stderr, self.raises = report, code, stderr, raises
        self.calls, self.folder, self.read = [], None, None

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        source = Path(command[3])
        self.folder = source.parent
        self.read = source.read_bytes()
        if self.raises is not None:
            raise self.raises
        if self.code == 0 and self.report is not None:
            (Path(command[4]) / fix_gitleaks.REPORT).write_text(self.report, encoding="utf-8")
        return subprocess.CompletedProcess(command, self.code, "", self.stderr)


class ReportTest(unittest.TestCase):
    """Only RuleID, StartLine and EndLine are read; any doubtful shape makes the report
    unreadable rather than dropping a finding."""

    def parse(self, document, lines=10):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / fix_gitleaks.REPORT
            text = document if isinstance(document, str) else json.dumps(document)
            path.write_text(text, encoding="utf-8")
            return fix_gitleaks._findings(path, lines)

    def test_a_valid_report_gives_rules_and_lines(self):
        self.assertEqual(self.parse([]), [])
        self.assertEqual(self.parse("null"), [])
        self.assertEqual(self.parse([finding(), finding("generic-api-key", 2, 3)]),
                         [("stripe-access-token", 4, 4), ("generic-api-key", 2, 3)])
        self.assertEqual(self.parse([finding(first=4, last=2)]), [("stripe-access-token", 4, 4)])
        self.assertEqual(self.parse([finding(first=4, last=50)]), [("stripe-access-token", 4, 10)])
        self.assertEqual(self.parse([finding(first=10, last=10)]), [("stripe-access-token", 10, 10)])

    def test_a_doubtful_report_is_unreadable(self):
        for case, document in (
            ("object", {}),
            ("text", '"x"'),
            ("entry not an object", [1]),
            ("no rule", [{"StartLine": 1, "EndLine": 1}]),
            ("empty rule", [finding(rule="")]),
            ("rule not a text", [finding(rule=3)]),
            ("line 0", [finding(first=0)]),
            ("line as bool", [finding(first=True)]),
            ("line as text", [finding(last="4")]),
            ("line missing", [{"RuleID": "r", "EndLine": 2}]),
            ("start beyond the input", [finding(first=11, last=11)]),
            ("one bad among good", [finding(), finding(first=0)]),
            ("broken JSON", "[{"),
        ):
            with self.subTest(case=case):
                self.assertIsNone(self.parse(document))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(fix_gitleaks._findings(Path(tmp) / "absent.json", 10))


class ScannerTest(unittest.TestCase):
    def scan(self, launcher, text=TEXT):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            found = Gitleaks(launcher, LAUNCHER)(text)
        return found, err.getvalue()

    def test_a_run_feeds_the_text_and_cleans_its_folder(self):
        launcher = FakeLauncher(json.dumps([finding()]))
        scanner = Gitleaks(launcher, LAUNCHER)
        self.assertEqual(scanner(TEXT), [("stripe-access-token", 4, 4)])
        self.assertIsNone(scanner.note)
        self.assertIsNone(scanner.reason)
        (command, kwargs), = launcher.calls
        self.assertEqual(command[:3], ["bash", str(LAUNCHER), "secrets"])
        self.assertEqual(Path(command[3]).name, fix_gitleaks.INPUT)
        self.assertEqual(Path(command[4]).parent, launcher.folder)
        self.assertTrue(launcher.folder.name.startswith("cqa-secrets-"))
        self.assertEqual(launcher.read, TEXT.encode("utf-8"))
        self.assertEqual(kwargs, {"capture_output": True, "text": True, "encoding": "utf-8",
                                  "errors": "replace"})
        self.assertFalse(launcher.folder.exists())

    def test_without_gitleaks_the_note_and_the_reason_say_why(self):
        for case, launcher, reason in (
            ("no Docker", FakeLauncher(code=2, stderr="Docker est introuvable : installez Docker.\n"),
             "Docker est introuvable : installez Docker."),
            ("gitleaks failed", FakeLauncher(code=1, stderr="x\nsecrets : gitleaks a échoué.\n"),
             "secrets : gitleaks a échoué."),
            ("no message", FakeLauncher(code=2), "échec sans message"),
            ("no report", FakeLauncher(report=None), "rapport gitleaks illisible"),
            ("broken report", FakeLauncher("[{"), "rapport gitleaks illisible"),
            ("start beyond the input", FakeLauncher(json.dumps([finding(first=5)])),
             "rapport gitleaks illisible"),
        ):
            with self.subTest(case=case):
                scanner = Gitleaks(launcher, LAUNCHER)
                self.assertIsNone(scanner(TEXT))
                self.assertEqual((scanner.note, scanner.reason), (GITLEAKS_DOWN, reason))
                self.assertFalse(launcher.folder.exists())
        launcher = FakeLauncher(json.dumps([finding(first=4)]))
        self.assertEqual(Gitleaks(launcher, LAUNCHER)("a\nb\nc\nd"), [("stripe-access-token", 4, 4)])

    def test_the_folder_goes_even_when_the_launcher_raises(self):
        launcher = FakeLauncher(raises=OSError("boom"))
        with self.assertRaises(OSError):
            Gitleaks(launcher, LAUNCHER)(TEXT)
        self.assertIsNotNone(launcher.folder)
        self.assertFalse(launcher.folder.exists())

    def test_a_folder_that_cannot_be_removed_is_named_on_stderr(self):
        launcher = FakeLauncher(json.dumps([finding()]))
        with mock.patch.object(fix_gitleaks.shutil, "rmtree", side_effect=OSError("busy")):
            found, err = self.scan(launcher)
        self.assertEqual(found, [("stripe-access-token", 4, 4)])
        self.assertIn(str(launcher.folder), err)
        self.assertIn("supprim", err)
        for text in (CANARY, "$x = 1;", "fix(securite CQ-3)"):
            self.assertNotIn(text, err)
        self.assertTrue(launcher.folder.exists())
        fix_gitleaks.shutil.rmtree(launcher.folder)
