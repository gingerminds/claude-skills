import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cqa import rerun
from tests.cqa.test_findings import make_finding

SHA = "3f2a1c9eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def finding(ref, verdict="ok", **overrides):
    return make_finding(ref=ref, verdict=verdict, todo=None if verdict == "ok" else "x", **overrides)


def file_evidence(path, repo="laravel"):
    return {"type": "file", "repo": repo, "path": path, "line": 1}


def git_answering(stdout="", code=0, calls=None):
    def run(command, **kwargs):
        if calls is not None:
            calls.append(command)
        return subprocess.CompletedProcess(command, code, stdout, "fatal: bad object" if code else "")

    return run


class RerunCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        (self.run_dir / "findings").mkdir()
        self.lots = [
            {"id": "code-securite", "kind": "code", "needs_toolbox": False, "refs": [1, 2, 3]},
            {"id": "server", "kind": "server", "needs_toolbox": False, "refs": [4]},
            {"id": "browser-autres", "kind": "browser", "needs_toolbox": False, "refs": [5]},
        ]
        self.excluded = []
        self.available = ["Code", "HTTP"]
        self.write_lots()
        self.write_context()
        self.rows = [
            {"ref": 1, "tools": ["Code"], "category": "Sécurité"},
            {"ref": 2, "tools": ["Code"], "category": "Sécurité"},
            {"ref": 3, "tools": ["Code"], "category": "Sécurité"},
            {"ref": 4, "tools": ["SSH serveur"], "category": "Autre"},
            {"ref": 5, "tools": ["Navigateur"], "category": "Autre"},
        ]
        self.write_rows()
        self.findings("code-securite")
        self.findings("server", finding(4), lot="server", fill=False)
        self.findings("browser-autres", finding(5), lot="browser-autres", fill=False)

    def write_rows(self):
        self.write("rows.json", {"rows": self.rows})

    def write(self, name, document):
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")

    def write_context(self, sha=SHA):
        self.write(
            "context.json",
            {
                "repos": [{"name": "laravel", "path": str(self.run_dir / "repo"), "audited_sha": sha}],
                "available_tools": self.available,
            },
        )

    def write_lots(self):
        self.write("lots.json", {"lots": self.lots, "excluded": self.excluded, "coverage": {}})

    def findings(self, name, *items, lot="code-securite", fill=True):
        items = list(items)
        if fill and "-" not in name.replace("code-securite", "", 1):
            given = {item["ref"] for item in items}
            refs = next(entry["refs"] for entry in self.lots if entry["id"] == lot)
            items += [finding(ref) for ref in refs if ref not in given]
        self.write(f"findings/{name}.json", {"lot": lot, "findings": items})

    def tool_result(self, tool, started_at):
        self.write(f"tools/{tool}/result.json", {"tool": tool, "started_at": started_at})

    def candidates(self, extra_refs=(), git_run=None):
        return rerun.rerun_candidates(
            self.run_dir, extra_refs=list(extra_refs), git_run=git_run or git_answering()
        )

    def reasons(self, result):
        return {item["ref"]: item["reasons"] for item in result["candidates"]}


class LatestFindingsTest(RerunCase):
    def test_latest_finding_wins_across_files(self):
        self.findings("code-securite", finding(1, "ko"), finding(2, "ko"), finding(3, "ko"))
        self.findings("code-securite-followup", finding(1, "partial"))
        self.findings("code-securite-rerun-01", finding(1, "ok"), finding(2, "partial"))
        self.findings("code-securite-rerun-02", finding(2, "ok"))
        latest = rerun.latest_findings(self.run_dir, self.lots)
        self.assertEqual(latest[1]["verdict"], "ok")
        self.assertEqual(latest[2]["verdict"], "ok")
        self.assertEqual(latest[3]["verdict"], "ko")

    def test_rerun_without_the_row_keeps_the_previous_finding(self):
        self.findings("code-securite", finding(1, "ko"))
        self.findings("code-securite-rerun-01", finding(1, "ok"))
        self.findings("code-securite-rerun-02", finding(2, "ok"))
        latest = rerun.latest_findings(self.run_dir, self.lots)
        self.assertEqual(latest[1]["verdict"], "ok")
        self.assertEqual(latest[2]["verdict"], "ok")

    def test_rerun_files_are_ordered_numerically(self):
        self.findings("code-securite", finding(1, "ko"))
        self.findings("code-securite-rerun-2", finding(1, "partial"))
        self.findings("code-securite-rerun-10", finding(1, "ok"))
        self.assertEqual(rerun.latest_findings(self.run_dir, self.lots)[1]["verdict"], "ok")

    def test_invalid_rerun_row_keeps_the_previous_finding(self):
        self.findings("code-securite", finding(1, "ko"))
        self.findings("code-securite-rerun-01", {"ref": 1, "verdict": "ok"})
        self.assertEqual(rerun.latest_findings(self.run_dir, self.lots)[1]["verdict"], "ko")

    def test_sibling_lot_files_are_not_mixed_up(self):
        self.lots.append({"id": "code-securite-outils", "kind": "code", "needs_toolbox": True, "refs": [6]})
        self.findings("code-securite-outils-rerun-01", finding(6, "ko"), lot="code-securite-outils")
        latest = rerun.latest_findings(self.run_dir, self.lots)
        self.assertEqual(latest[6]["verdict"], "ko")
        self.assertEqual({ref for ref in latest if ref in (1, 2, 3)}, {1, 2, 3})
        self.assertEqual(latest[1]["verdict"], "ok")


class CandidatesTest(RerunCase):
    def test_unknown_finding_is_a_candidate(self):
        self.findings("code-securite", finding(1, "unknown"), finding(2, "ok"), finding(3, "ko"))
        result = self.candidates()
        self.assertEqual(self.reasons(result), {1: ["cause_levee"]})
        self.assertEqual(result["candidates"][0]["lot"], "code-securite")
        self.assertEqual(result["lots"], ["code-securite"])
        self.assertEqual(result["estimate_min"], 5)

    def exclude(self, ref, tools, category="Sécurité", reason="no_tools"):
        self.excluded.append(
            {"ref": ref, "page_id": f"p{ref}", "reason": reason, "verdict": "unknown",
             "detail": "x", "tools": tools}
        )
        self.rows.append({"ref": ref, "tools": tools, "category": category})
        self.write_lots()
        self.write_rows()

    def test_tools_now_available_make_excluded_rows_candidates(self):
        self.exclude(7, ["HTTP", "Intervention humaine"])
        self.exclude(8, ["HTTP", "SSH serveur"])
        self.exclude(9, ["HTTP"], reason="standby")
        result = self.candidates()
        self.assertEqual(self.reasons(result), {7: ["cause_levee"]})
        self.assertEqual(result["candidates"][0]["lot"], "code-securite")
        self.assertEqual(result["lots"], ["code-securite"])
        self.assertEqual(result["estimate_min"], 5)
        self.assertEqual(result["notes"], [])

    def test_excluded_row_without_a_fitting_lot_keeps_no_lot(self):
        self.exclude(7, ["HTTP"], category="Performance")
        result = self.candidates()
        self.assertEqual(self.reasons(result), {7: ["cause_levee"]})
        self.assertIsNone(result["candidates"][0]["lot"])
        self.assertEqual(result["lots"], [])
        self.assertEqual(result["estimate_min"], 0)
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("CQ-7", result["notes"][0])
        self.assertIn("nouveau run", result["notes"][0])

    def test_excluded_row_missing_from_rows_has_no_lot_and_a_note(self):
        self.exclude(7, ["HTTP"])
        self.rows.pop()
        self.write_rows()
        result = self.candidates()
        self.assertIsNone(result["candidates"][0]["lot"])
        self.assertEqual(len(result["notes"]), 1)

    def test_row_without_valid_finding_is_unknown(self):
        self.findings("code-securite", finding(1), finding(2), fill=False)
        result = self.candidates()
        self.assertEqual(self.reasons(result), {3: ["cause_levee"]})
        self.assertEqual(result["candidates"][0]["details"], ["aucun constat valide"])
        self.assertEqual(result["candidates"][0]["lot"], "code-securite")

    def test_older_tool_date_is_a_candidate(self):
        self.findings(
            "code-securite",
            finding(1, tool_dates={"crawl": "2026-10-20T21:15:00.000Z"}),
            finding(2, tool_dates={"crawl": "2026-10-21T21:15:00Z"}),
        )
        self.tool_result("crawl", "2026-10-21T21:15:00.000Z")
        result = self.candidates()
        self.assertEqual(self.reasons(result), {1: ["outil_relance"]})
        self.assertEqual(result["tools"], ["crawl"])

    def test_same_instant_in_two_formats_is_not_newer(self):
        self.findings("code-securite", finding(1, tool_dates={"crawl": "2026-10-20T21:15:05Z"}))
        self.tool_result("crawl", "2026-10-20T21:15:05.000Z")
        self.assertEqual(self.candidates()["candidates"], [])

    def test_tool_without_result_is_no_reason(self):
        self.findings("code-securite", finding(1, tool_dates={"crawl": "2026-10-20T21:15:05Z"}))
        result = self.candidates()
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["notes"], [])

    def test_invalid_dates_give_a_note_and_no_reason(self):
        self.findings(
            "code-securite",
            finding(1, tool_dates={"crawl": "2026-13-45T99:99:99Z"}),
            finding(2, tool_dates={"lighthouse": "2026-10-20T21:15:05Z"}),
        )
        self.tool_result("crawl", "2026-10-21T21:15:05Z")
        self.tool_result("lighthouse", "not a date")
        result = self.candidates()
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["notes"]), 2)
        self.assertTrue(all("crawl" in n or "lighthouse" in n for n in result["notes"]))

    def test_changed_file_is_a_candidate(self):
        self.findings(
            "code-securite",
            finding(1, evidence=[file_evidence("app/Kernel.php")]),
            finding(2, evidence=[file_evidence("app/Other.php")]),
        )
        calls = []
        result = self.candidates(git_run=git_answering("app/Kernel.php\nREADME.md\n", calls=calls))
        self.assertEqual(self.reasons(result), {1: ["code_modifie"]})
        self.assertIn(f"{SHA}..HEAD", calls[0])
        self.assertIn("--name-only", calls[0])

    def test_code_changed_without_audited_commit(self):
        self.findings("code-securite", finding(1, evidence=[file_evidence("app/Kernel.php")]))
        result = self.candidates(git_run=git_answering(code=128))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("laravel", result["notes"][0])

    def test_missing_git_binary_gives_a_note(self):
        self.findings("code-securite", finding(1, evidence=[file_evidence("app/Kernel.php")]))

        def run(command, **kwargs):
            raise FileNotFoundError("git")

        result = self.candidates(git_run=run)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["notes"]), 1)

    def test_lead_refs_are_candidates(self):
        self.findings("code-securite", finding(1), finding(2))
        result = self.candidates(extra_refs=[2, 5, 99])
        self.assertEqual(self.reasons(result), {2: ["lead"], 5: ["lead"]})
        self.assertEqual(result["lots"], ["code-securite", "browser-autres"])
        self.assertEqual(result["estimate_min"], 5 + 15)
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("CQ-99", result["notes"][0])

    def test_estimate_by_lot_kind(self):
        self.findings("code-securite", finding(1, "unknown"))
        self.findings("server", finding(4, "unknown"), lot="server")
        self.findings("browser-autres", finding(5, "unknown"), lot="browser-autres")
        self.assertEqual(self.candidates()["estimate_min"], 5 + 10 + 15)

    def test_rerun_candidates_on_a_legacy_run(self):
        self.findings(
            "code-securite",
            finding(1, "unknown"),
            finding(2, evidence=[file_evidence("app/Kernel.php")]),
        )
        self.tool_result("crawl", "2026-10-21T21:15:00Z")
        result = self.candidates(extra_refs=[3], git_run=git_answering("app/Kernel.php\n"))
        self.assertEqual(
            self.reasons(result),
            {1: ["cause_levee"], 2: ["code_modifie"], 3: ["lead"]},
        )
        self.assertEqual(result["tools"], [])

    def test_several_reasons_are_kept_in_order(self):
        self.findings(
            "code-securite",
            finding(
                1,
                "unknown",
                evidence=[file_evidence("a.php")],
                tool_dates={"crawl": "2026-10-20T21:15:00Z"},
            ),
        )
        self.tool_result("crawl", "2026-10-21T21:15:00Z")
        result = self.candidates(extra_refs=[1], git_run=git_answering("a.php\n"))
        self.assertEqual(
            self.reasons(result)[1], ["cause_levee", "outil_relance", "code_modifie", "lead"]
        )
        self.assertEqual(len(result["candidates"][0]["details"]), 4)


if __name__ == "__main__":
    unittest.main()
