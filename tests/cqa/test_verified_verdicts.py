"""D-03, A-11 and F-12: a favourable verdict rests on evidence that the code has checked."""
import json
import tempfile
import unittest
from pathlib import Path

from cqa import rerun
from cqa.findings import apply_guardrails

from tests.cqa.test_cli_audit import AuditCase

PERF = "code-performance"
RECORD = f"findings/{PERF}.checked.json"
OUTSIDE = "https://evil.example.net/x"
PAGE = {"type": "page", "url": "https://preprod.example.org/", "detail": "page vue"}
HTTP = {"type": "http", "url": "https://preprod.example.org/robots.txt", "detail": "200"}
FILE = {"type": "file", "repo": "laravel", "path": "app/Http/Kernel.php"}
TOOL = {"type": "tool", "tool": "http", "ref": "summary.json#root"}


class VerifiedCase(AuditCase):
    def make_run(self):
        run_dir = super().make_run()
        self.write(run_dir, "comments.json", {})
        return run_dir

    def judge(self, run_dir, ref, verdict, evidence, *, lot=PERF, **changes):
        changes.setdefault("confidence", "high")
        changes.setdefault("todo", None if verdict in ("ok", "na") else "À faire.")
        changes.setdefault("needs_probes", [])
        self.edit_finding(run_dir, lot, ref, verdict=verdict, evidence=evidence, **changes)

    def errors_of(self, run_dir, ref):
        return [error for error in self.validate_errors(run_dir, PERF) if f"CQ-{ref} " in error]

    def plan(self, run_dir, *flags):
        code, out, err = self.run_main(["plan", run_dir, *flags])
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def ops(self, run_dir, ref, kind):
        plan = self.read(run_dir, "notion/plan.json")
        return [op for op in plan["ops"] if op["ref"] == ref and op["kind"] == kind]

    def status_after(self, run_dir, ref):
        found = self.ops(run_dir, ref, "status")
        return found[0]["after"] if found else None

    def comment_verdict(self, run_dir, ref):
        (op,) = self.ops(run_dir, ref, "comment")
        return op["verdict"]

    def reasons(self, summary):
        return {item["ref"]: item["reason"] for item in summary["discarded"]}

    def set_context(self, run_dir, **changes):
        self.edit(run_dir, "context.json", lambda context: context.update(changes))

    def set_tool_status(self, run_dir, tool, status, *, summary=True):
        folder = run_dir / "tools" / tool
        folder.mkdir(parents=True, exist_ok=True)
        self.write(run_dir, f"tools/{tool}/result.json", {"tool": tool, "status": status, "reason": "x"})
        if summary:
            self.write(run_dir, f"tools/{tool}/summary.json", {"tool": tool, "findings": []})


class NaWithoutEvidenceTest(VerifiedCase):
    def test_na_without_evidence_becomes_unknown_even_on_blocking_row(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "na", [], summary="Rien à auditer.")
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertIn(101, summary["blocking_non_conform"])
        self.assertNotEqual(self.status_after(run_dir, 101), "Non applicable")
        self.assertEqual(self.ops(run_dir, 101, "comment")[0]["verdict"], "unknown")

    def test_na_with_valid_evidence_stays_na(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "na", [FILE], summary="Rien à auditer.")
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertNotIn(101, summary["blocking_non_conform"])
        self.assertEqual(self.status_after(run_dir, 101), "Non applicable")

    def test_na_with_evidence_that_fails_the_check_is_not_applicable_nowhere(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "na", [{"type": "page", "url": OUTSIDE}], summary="Rien.")
        self.assertEqual(len(self.errors_of(run_dir, 101)), 1)
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])

    def test_unknown_partial_and_ko_without_evidence_stay_allowed(self):
        run_dir = self.make_run()
        for ref, verdict in ((101, "partial"), (102, "unknown")):
            self.judge(run_dir, ref, verdict, [], summary="Pas de preuve disponible.")
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        summary, _ = self.plan(run_dir)
        self.assertEqual(self.status_after(run_dir, 101), "Mise en place partielle")
        self.assertIn(102, summary["unknown"])


class PageOriginTest(VerifiedCase):
    def test_page_evidence_outside_target_origin_is_refused(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [{"type": "page", "url": OUTSIDE, "detail": "vu"}])
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("preuve n° 1 (page)", errors[0])
        self.assertIn("https://evil.example.net/x", errors[0])
        self.assertIn("evil.example.net", errors[0])
        self.assertIn("preprod.example.org", errors[0])
        summary, err = self.plan(run_dir)
        self.assertNotIn(102, summary["unknown"])
        self.assertEqual(self.comment_verdict(run_dir, 102), "ok_human")
        self.assertNotEqual(self.status_after(run_dir, 102), "Ok / Recetté")
        self.assertIn("evil.example.net", self.reasons(summary)[102])

    def test_a_page_of_the_target_is_accepted_on_any_scheme_port_and_case(self):
        run_dir = self.make_run()
        for url in (
            "https://preprod.example.org/",
            "http://PREPROD.example.org:8080/a?b=1#c",
            "https://preprod.example.org",
        ):
            with self.subTest(url=url):
                self.judge(run_dir, 102, "ok", [{"type": "page", "url": url, "detail": "vu"}])
                self.assertEqual(self.errors_of(run_dir, 102), [])

    def test_a_look_alike_host_is_refused(self):
        run_dir = self.make_run()
        for url in (
            "https://preprod.example.org.evil.example.net/",
            "https://evilpreprod.example.org/",
            "https://example.org/",
            "https://sub.preprod.example.org/",
            "not a url",
        ):
            with self.subTest(url=url):
                self.judge(run_dir, 102, "ok", [{"type": "page", "url": url, "detail": "vu"}])
                self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_the_hosts_of_the_declared_sites_are_accepted(self):
        run_dir = self.make_run()
        sites = [
            {
                "domain": "shop.example.org",
                "backoffice": "commun",
                "sso": False,
                "home": "https://shop.example.org/",
            }
        ]
        self.set_context(run_dir, sites=sites)
        for url in ("https://shop.example.org/panier", "https://preprod.example.org/"):
            with self.subTest(url=url):
                self.judge(run_dir, 102, "ok", [{"type": "page", "url": url, "detail": "vu"}])
                self.assertEqual(self.errors_of(run_dir, 102), [])
        self.judge(run_dir, 102, "ok", [{"type": "page", "url": OUTSIDE, "detail": "vu"}])
        self.assertIn("shop.example.org", self.errors_of(run_dir, 102)[0])
        self.assertIn("preprod.example.org", self.errors_of(run_dir, 102)[0])

    def test_the_prod_host_is_expected_on_a_prod_run_only(self):
        run_dir = self.make_run()
        prod = {"type": "page", "url": "https://www.example.org/", "detail": "vu"}
        target = {"preprod_url": "https://preprod.example.org", "prod_url": "https://www.example.org"}
        self.set_context(run_dir, target={**target, "prod_live": True, "access": "open"})
        self.judge(run_dir, 102, "ok", [prod])
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)
        self.set_context(run_dir, phase_filter="MEL J0")
        self.judge(run_dir, 102, "ok", [prod])
        self.assertEqual(self.errors_of(run_dir, 102), [])

    def test_a_context_without_any_host_refuses_every_page(self):
        run_dir = self.make_run()
        self.set_context(
            run_dir,
            target={"preprod_url": None, "prod_url": None, "prod_live": False, "access": "open"},
        )
        self.judge(run_dir, 102, "ok", [PAGE])
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("target.preprod_url", errors[0])

    def test_the_message_never_carries_credentials_or_the_query(self):
        run_dir = self.make_run()
        url = "https://camille:secret-value@evil.example.net/x?token=abc123"
        self.judge(run_dir, 102, "ok", [{"type": "page", "url": url, "detail": "vu"}])
        error = self.errors_of(run_dir, 102)[0]
        for text in ("camille", "secret-value", "abc123"):
            self.assertNotIn(text, error)

    def test_a_run_without_record_is_checked_with_the_new_rules(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [{"type": "page", "url": OUTSIDE, "detail": "vu"}])
        self.assertFalse((run_dir / RECORD).exists())
        summary, _ = self.plan(run_dir)
        self.assertEqual(self.comment_verdict(run_dir, 102), "ok_human")
        self.assertIn("evil.example.net", self.reasons(summary)[102])


class HttpProbeTest(VerifiedCase):
    def test_http_evidence_must_match_a_probe(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [HTTP])
        self.assertEqual(self.errors_of(run_dir, 102), [])
        never_sent = {"type": "http", "url": "https://preprod.example.org/secret", "detail": "403"}
        self.judge(run_dir, 102, "ok", [never_sent])
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("preuve n° 1 (http)", errors[0])
        self.assertIn("sonde", errors[0])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(102, summary["unknown"])
        self.assertEqual(self.comment_verdict(run_dir, 102), "ok_human")
        self.assertNotEqual(self.status_after(run_dir, 102), "Ok / Recetté")

    def test_the_match_is_made_on_host_path_and_query(self):
        run_dir = self.make_run()
        cases = {
            "https://preprod.example.org/": True,
            "http://preprod.example.org/": True,
            "https://preprod.example.org": True,
            "https://PREPROD.example.org:443/robots.txt": True,
            "https://preprod.example.org/robots.txt#top": True,
            "https://preprod.example.org/robots.txt?x=1": False,
            "https://preprod.example.org/robots.txt/": False,
            "https://preprod.example.org/ROBOTS.txt": False,
            "https://preprod.example.org/other": False,
        }
        for url, accepted in cases.items():
            with self.subTest(url=url):
                evidence = {"type": "http", "url": url, "detail": "200"}
                self.judge(run_dir, 102, "ok", [evidence])
                self.assertEqual(self.errors_of(run_dir, 102) == [], accepted)

    def test_a_probe_with_a_query_is_matched_with_it(self):
        run_dir = self.make_run()
        probes = self.read(run_dir, "probes.json")
        probes["probes"].append({"id": "q", "method": "GET", "path": "/search?q=a", "headers": {}})
        self.write(run_dir, "probes.json", probes)
        good = {"type": "http", "url": "https://preprod.example.org/search?q=a", "detail": "200"}
        bad = {"type": "http", "url": "https://preprod.example.org/search?q=b", "detail": "200"}
        self.judge(run_dir, 102, "ok", [good])
        self.assertEqual(self.errors_of(run_dir, 102), [])
        self.judge(run_dir, 102, "ok", [bad])
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_a_probe_the_tool_never_ran_is_no_evidence(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [HTTP])
        for status in ("failed", "skipped"):
            with self.subTest(status=status):
                self.set_tool_status(run_dir, "http", status)
                self.assertEqual(len(self.errors_of(run_dir, 102)), 1)
        (run_dir / "tools" / "http" / "result.json").unlink()
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_a_run_without_probes_file_has_no_probe_evidence(self):
        run_dir = self.make_run()
        (run_dir / "probes.json").unlink()
        self.judge(run_dir, 102, "ok", [HTTP])
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_a_damaged_probes_file_has_no_probe_evidence(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [HTTP])
        for content in ("{", "[]", '{"probes": 3}', '{"base_url": 3, "probes": [1, {"path": 5}]}'):
            with self.subTest(content=content):
                (run_dir / "probes.json").write_text(content, encoding="utf-8")
                self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_the_probes_of_the_followup_count_once_the_followup_ran(self):
        run_dir = self.make_run()
        self.write(
            run_dir,
            "probes-followup.json",
            {"base_url": "https://preprod.example.org", "probes": [{"id": "f", "method": "GET", "path": "/telescope"}]},
        )
        asked = {"type": "http", "url": "https://preprod.example.org/telescope", "detail": "404"}
        self.judge(run_dir, 102, "ok", [asked])
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)
        self.set_tool_status(run_dir, "http-followup", "ok")
        self.assertEqual(self.errors_of(run_dir, 102), [])
        self.set_tool_status(run_dir, "http-followup", "failed")
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_a_probe_of_the_first_sweep_does_not_need_the_followup(self):
        run_dir = self.make_run()
        self.set_tool_status(run_dir, "http-followup", "failed")
        self.judge(run_dir, 102, "ok", [HTTP])
        self.assertEqual(self.errors_of(run_dir, 102), [])

    def test_http_evidence_outside_the_origin_is_refused_whatever_the_probes(self):
        run_dir = self.make_run()
        probes = self.read(run_dir, "probes.json")
        probes["probes"].append(
            {"id": "far", "method": "GET", "path": "https://evil.example.net/x", "headers": {}}
        )
        self.write(run_dir, "probes.json", probes)
        self.judge(run_dir, 102, "ok", [{"type": "http", "url": OUTSIDE, "detail": "200"}])
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("evil.example.net", errors[0])

    def test_the_probe_check_is_frozen_at_validation(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [HTTP])
        self.assertEqual(self.validate_errors(run_dir, PERF), [])
        (run_dir / "probes.json").unlink()
        self.set_tool_status(run_dir, "http", "failed")
        summary, _ = self.plan(run_dir)
        self.assertNotIn(102, summary["unknown"])
        entry = self.read(run_dir, RECORD)["files"][f"{PERF}.json"]["findings"]
        self.assertEqual([item["problems"] for item in entry if item["ref"] == 102], [[]])


class ToolResultTest(VerifiedCase):
    def test_tool_evidence_refused_when_tool_failed(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [TOOL])
        self.assertEqual(self.errors_of(run_dir, 102), [])
        self.set_tool_status(run_dir, "http", "failed")
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("preuve n° 1 (tool)", errors[0])
        self.assertIn("failed", errors[0])
        self.assertIn("tools/http/result.json", errors[0])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(102, summary["unknown"])
        self.assertEqual(self.comment_verdict(run_dir, 102), "ok_human")
        self.assertNotEqual(self.status_after(run_dir, 102), "Ok / Recetté")

    def test_a_skipped_tool_is_refused_too(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [TOOL])
        self.set_tool_status(run_dir, "http", "skipped")
        self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_a_tool_not_applicable_is_accepted(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [TOOL])
        self.set_tool_status(run_dir, "http", "not_applicable")
        self.assertEqual(self.errors_of(run_dir, 102), [])

    def test_a_tool_without_result_or_with_a_damaged_one_is_refused(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [TOOL])
        (run_dir / "tools" / "http" / "result.json").unlink()
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("result.json", errors[0])
        for content in ("{", "[]", '{"status": 3}', "{}"):
            with self.subTest(content=content):
                (run_dir / "tools" / "http" / "result.json").write_text(content, encoding="utf-8")
                self.assertEqual(len(self.errors_of(run_dir, 102)), 1)

    def test_a_tool_without_summary_keeps_its_own_message(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [{"type": "tool", "tool": "lighthouse", "ref": "summary.json#x"}])
        self.set_tool_status(run_dir, "lighthouse", "ok", summary=False)
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("sans résumé", errors[0])

    def test_the_result_of_a_failed_tool_is_named_before_its_missing_summary(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [{"type": "tool", "tool": "lighthouse", "ref": "summary.json#x"}])
        errors = self.errors_of(run_dir, 102)
        self.assertEqual(len(errors), 1)
        self.assertIn("failed", errors[0])
        self.assertIn("navigateur de test indisponible", errors[0])


UNMATCHED = {"type": "http", "url": "https://preprod.example.org/telescope", "detail": "200 : exposé"}


class RefusedEvidenceTest(VerifiedCase):
    """R-02: a piece of evidence the new checks refuse is dropped from the finding, which stays."""

    def comment(self, run_dir, ref):
        (op,) = self.ops(run_dir, ref, "comment")
        return op

    def test_ko_with_valid_file_and_unmatched_http_evidence_stays_ko(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "ko", [FILE, UNMATCHED])
        errors = self.errors_of(run_dir, 101)
        self.assertEqual(len(errors), 1)
        self.assertIn("preuve n° 2 (http) écartée", errors[0])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertIn(101, summary["blocking_non_conform"])
        op = self.comment(run_dir, 101)
        self.assertEqual(op["verdict"], "ko")
        self.assertIn("app/Http/Kernel.php", op["text"])
        self.assertIn("https://preprod.example.org/telescope", op["text"])
        self.assertIn("preuve écartée", op["text"])

    def test_partial_citing_failed_tool_stays_partial(self):
        run_dir = self.make_run()
        self.set_tool_status(run_dir, "lighthouse", "failed")
        self.judge(run_dir, 101, "partial", [{"type": "tool", "tool": "lighthouse", "ref": "summary.json#home"}])
        errors = self.errors_of(run_dir, 101)
        self.assertEqual(len(errors), 1)
        self.assertIn("preuve n° 1 (tool) écartée", errors[0])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertEqual(self.status_after(run_dir, 101), "Mise en place partielle")
        self.assertEqual(self.comment(run_dir, 101)["verdict"], "partial")

    def test_ok_with_only_an_unmatched_http_evidence_becomes_ok_human(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "ok", [UNMATCHED])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertEqual(self.status_after(run_dir, 101), "Mis en place")
        self.assertEqual(self.comment(run_dir, 101)["verdict"], "ok_human")
        kinds = [op["kind"] for op in self.read(run_dir, "notion/plan.json")["ops"] if op["ref"] == 101]
        self.assertEqual(kinds, ["status", "assign", "comment"])

    def test_na_with_only_a_refused_evidence_becomes_unknown(self):
        run_dir = self.make_run()
        for evidence in ({"type": "page", "url": OUTSIDE}, UNMATCHED):
            with self.subTest(evidence=evidence["type"]):
                self.judge(run_dir, 101, "na", [evidence], summary="Rien à auditer.")
                summary, _ = self.plan(run_dir)
                self.assertIn(101, summary["unknown"])
                self.assertIn(101, summary["blocking_non_conform"])
                self.assertEqual(self.comment(run_dir, 101)["verdict"], "unknown")

    def test_removed_evidence_is_named_in_discarded(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "ko", [FILE, UNMATCHED])
        self.assertEqual(len(self.errors_of(run_dir, 101)), 1)
        summary, err = self.plan(run_dir)
        reason = self.reasons(summary)[101]
        self.assertIn(f"findings/{PERF}.json", reason)
        self.assertIn("preuve n° 2 (http) écartée", reason)
        self.assertIn("aucune sonde", reason)
        self.assertIn("gardé sans cette preuve", reason)
        self.assertIn(f"- CQ-101 : {reason}", err)
        record = self.read(run_dir, RECORD)["files"][f"{PERF}.json"]["findings"]
        (entry,) = [item for item in record if item["ref"] == 101]
        self.assertEqual(entry["problems"], [])
        self.assertEqual([item["evidence"] for item in entry["refused"]], [2])

    def test_the_guardrail_names_the_removed_evidence(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "ok", [UNMATCHED])
        self.validate_errors(run_dir, PERF)
        context = self.read(run_dir, "context.json")
        lot = self.read(run_dir, "lots.json")["lots"][0]
        found = rerun.read_grounded_findings(run_dir, context, lot, f"{PERF}.json")[0]
        (item,) = [entry for entry in found if entry["ref"] == 101]
        self.assertEqual(item["evidence"], [])
        judged = apply_guardrails(item, {"ref": 101, "tools": ["Code"]}, followup_done=False)
        self.assertEqual(judged["verdict"], "ok_human")
        self.assertTrue(judged["guardrail"].startswith("preuve n° 1 (http) écartée : aucune sonde"))
        self.assertIn("aucune preuve : ok abaissé à ok_human", judged["guardrail"])

    def test_the_old_checks_still_set_the_finding_aside(self):
        run_dir = self.make_run()
        ghost = {"type": "file", "repo": "laravel", "path": "ghost.php"}
        self.judge(run_dir, 101, "ko", [ghost, UNMATCHED])
        self.assertEqual(len(self.errors_of(run_dir, 101)), 2)
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertIn("Non évaluable", self.reasons(summary)[101])
        self.judge(run_dir, 101, "ko", [{"type": "tool", "tool": "crawl", "ref": "summary.json"}])
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])

    def test_a_failed_tool_without_summary_still_sets_the_finding_aside(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "partial", [{"type": "tool", "tool": "lighthouse", "ref": "summary.json#home"}])
        errors = self.errors_of(run_dir, 101)
        self.assertEqual(len(errors), 1)
        self.assertNotIn("écartée", errors[0])
        summary, _ = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])

    def test_rerun_close_keeps_a_rerun_file_whose_only_problem_is_a_refused_evidence(self):
        run_dir = self.make_run()
        self.run_json(["rerun-start", run_dir])
        document = self.read(run_dir, f"findings/{PERF}.json")
        (first,) = [item for item in document["findings"] if item["ref"] == 101]
        first.update(evidence=[FILE, UNMATCHED], needs_probes=[])
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", {"lot": PERF, "findings": [first]})
        self.assertEqual(self.run_json(["rerun-close", run_dir, "1", "--rows", "101"])["discarded"], [])
        summary, _ = self.plan(run_dir)
        self.assertNotIn(101, summary["unknown"])
        self.assertIn("rerun-01", self.reasons(summary)[101])


class AgentFileCliTest(VerifiedCase):
    def test_validate_refuses_what_only_the_code_writes(self):
        run_dir = self.make_run()
        document_evidence = {"type": "document", "doc": "d1", "ref": "critères 7.3"}
        original = self.read(run_dir, f"findings/{PERF}.json")
        for name, changes in (
            ("document", {"evidence": [document_evidence]}),
            ("source", {"source": "context_doc"}),
            ("guardrail", {"guardrail": "trust me"}),
            ("unknown key", {"foo": 1}),
        ):
            with self.subTest(case=name):
                self.write(run_dir, f"findings/{PERF}.json", original)
                self.edit_finding(run_dir, PERF, 102, **changes)
                errors = self.errors_of(run_dir, 102)
                self.assertEqual(len(errors), 1, errors)
        self.write(run_dir, f"findings/{PERF}.json", original)
        self.assertEqual(self.validate_errors(run_dir, PERF), [])

    def test_plan_leaves_the_finding_of_an_agent_that_wrote_a_source_unread(self):
        run_dir = self.make_run()
        self.judge(run_dir, 102, "ok", [TOOL])
        self.edit_finding(run_dir, PERF, 102, source="context_doc")
        summary, _ = self.plan(run_dir)
        self.assertIn(102, summary["unknown"])
        self.assertNotEqual(self.status_after(run_dir, 102), "Ok / Recetté")

    def test_a_document_evidence_does_not_lift_the_guardrail_without_evidence(self):
        run_dir = self.make_run()
        evidence = [{"type": "document", "doc": "d1", "ref": "Rapport d1"}]
        self.judge(run_dir, 102, "ok", evidence)
        summary, _ = self.plan(run_dir)
        self.assertIn(102, summary["unknown"])

    def test_the_followup_and_rerun_files_are_agent_files_too(self):
        run_dir = self.make_run()
        document = self.read(run_dir, f"findings/{PERF}.json")
        for item in document["findings"]:
            item["foo"] = 1
            item["needs_probes"] = []
        self.write(run_dir, f"findings/{PERF}-followup.json", document)
        code, out, err = self.run_main(["validate", run_dir, PERF, "--followup"])
        self.assertEqual(code, 6, err)
        self.assertEqual(len(json.loads(out)["errors"]), 2)


class LotFieldCliTest(VerifiedCase):
    def test_lot_field_must_match_validated_lot(self):
        run_dir = self.make_run()
        document = self.read(run_dir, f"findings/{PERF}.json")
        document["lot"] = "code-securite"
        self.write(run_dir, f"findings/{PERF}.json", document)
        errors = self.validate_errors(run_dir, PERF)
        self.assertEqual(len(errors), 1)
        self.assertIn("« code-securite »", errors[0])
        self.assertIn(f"« {PERF} »", errors[0])
        summary, _ = self.plan(run_dir)
        self.assertTrue({101, 102} <= set(summary["unknown"]))
        self.assertIn("lot", self.reasons(summary)[101])

    def test_a_followup_or_rerun_file_of_another_lot_is_refused(self):
        run_dir = self.make_run()
        self.judge(run_dir, 101, "ko", [FILE])
        self.judge(run_dir, 102, "ok", [TOOL])
        document = self.read(run_dir, f"findings/{PERF}.json")
        document["lot"] = "code-securite"
        self.write(run_dir, f"findings/{PERF}-rerun-01.json", document)
        code, out, err = self.run_main(
            ["validate", run_dir, PERF, "--rerun", "1", "--rows", "101,102"]
        )
        self.assertEqual(code, 6, err)
        self.assertTrue(any("« code-securite »" in error for error in json.loads(out)["errors"]))


class DocumentEvidenceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        (self.run_dir / "findings").mkdir()
        self.lot = {"id": "context-docs", "kind": "documents", "refs": [2]}

    def write_findings(self, *evidence):
        finding = {
            "ref": 2,
            "verdict": "ko",
            "confidence": "high",
            "source": "context_doc",
            "summary": "Rapport « Audit » (2026-10-01) : 1 point non conforme.",
            "evidence": list(evidence),
            "todo": "Corriger.",
            "human_check": None,
            "divergence": None,
            "needs_probes": [],
        }
        document = {"lot": "context-docs", "findings": [finding]}
        (self.run_dir / "findings" / "context-docs.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    def read(self):
        context = {"plugin_version": "0.18.1"}
        return rerun.read_grounded_findings(self.run_dir, context, self.lot, "context-docs.json")

    def make_document(self, doc_id="d1"):
        folder = self.run_dir / "context-docs" / doc_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "items.json").write_text(
            json.dumps({"document": {"id": doc_id}, "items": []}), encoding="utf-8"
        )

    def test_document_evidence_must_exist_in_context_docs(self):
        self.write_findings({"type": "document", "doc": "d1", "ref": "critères 7.3"})
        valid, errors = self.read()
        self.assertEqual(valid, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("CQ-2", errors[0])
        self.assertIn("preuve n° 1 (document)", errors[0])
        self.assertIn("d1", errors[0])
        self.assertIn("context-docs/", errors[0])
        self.make_document("d1")
        valid, errors = self.read()
        self.assertEqual((errors, [item["ref"] for item in valid]), ([], [2]))

    def test_a_document_that_is_not_imported_is_not_evidence(self):
        self.write_findings({"type": "document", "doc": "d1", "ref": "critères 7.3"})
        (self.run_dir / "context-docs" / "d1").mkdir(parents=True)
        self.assertEqual(len(self.read()[1]), 1)

    def test_a_document_id_cannot_climb_out_of_context_docs(self):
        self.make_document("d1")
        for doc in ("../d1", "d1/../d1", "", "d1/", "x" * 30):
            with self.subTest(doc=doc):
                self.write_findings({"type": "document", "doc": doc, "ref": "r"})
                self.assertEqual(len(self.read()[1]), 1)

    def test_context_docs_file_still_validates(self):
        self.make_document("d1")
        self.make_document("d2")
        self.write_findings(
            {"type": "document", "doc": "d1", "ref": "critères 7.3"},
            {"type": "document", "doc": "d2", "ref": "critères 1.1"},
        )
        valid, errors = self.read()
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["source"], "context_doc")
        self.assertEqual(len(valid[0]["evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
