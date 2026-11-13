import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cqa import probes, scenarios
from cqa.findings import ACTION_URL_REASON, PHP_FILE_REASON
from tests.cqa.test_findings import make_ok, make_scenario

BASE = "https://preprod.example.org"
CAP_REASON = "plafond de 20 scénarios par suivi atteint"
ORIGIN_REASON = "URL hors de l'origine de la cible"


def request(index, **overrides):
    return make_scenario(id=f"s-{index}", **overrides)


def held_valid(document, refs, **_):
    return list(document["findings"]), []


def requests_of(urls, prefix, per_finding=3):
    """The findings [(ref, needs_scenarios)] that ask, 3 at most each, for one scenario per URL."""
    needs = [make_scenario(id=f"{prefix}-{n}", url=url) for n, url in enumerate(urls)]
    chunks = [needs[start : start + per_finding] for start in range(0, len(needs), per_finding)]
    return [(index + 1, chunk) for index, chunk in enumerate(chunks)]


class ScenarioRunCase(unittest.TestCase):
    """A run folder with a context, a lots.json and the first-round findings of its lots."""

    def make_run(self, lots, base=BASE):
        """lots: {lot id: [(ref, needs_scenarios), ...]}; each ref is a valid finding."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name)
        self.write(run_dir, "context.json", {"target": {"preprod_url": base}})
        entries = [
            {"id": lot_id, "refs": [ref for ref, _ in findings]}
            for lot_id, findings in lots.items()
        ]
        self.write(run_dir, "lots.json", {"lots": entries})
        for lot_id, findings in lots.items():
            self.write_findings(run_dir, lot_id, findings)
        return run_dir

    def write(self, run_dir, name, value):
        path = run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def write_findings(self, run_dir, lot_id, findings, name=None):
        items = [make_ok(ref=ref, needs_scenarios=needs) for ref, needs in findings]
        self.write(run_dir, f"findings/{name or lot_id}.json", {"lot": lot_id, "findings": items})


class FollowupScenariosTest(ScenarioRunCase):
    def test_followup_scenarios_collects_valid_requests_with_lot_namespaced_ids(self):
        run_dir = self.make_run({"code-securite": [(103, [make_scenario()])]})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(set(result), {"base_url", "scenarios", "lots", "discarded"})
        self.assertEqual(result["base_url"], BASE)
        self.assertEqual(result["lots"], ["code-securite"])
        self.assertEqual(result["discarded"], [])
        expected = make_scenario(id="code-securite:menu-burger")
        self.assertEqual(result["scenarios"], [expected])
        self.assertEqual(list(result["scenarios"][0]), ["id", "url", "profile", "steps"])

    def test_requests_of_several_lots_keep_their_order_and_their_lot(self):
        run_dir = self.make_run(
            {
                "code-performance": [(1, [make_scenario(id="menu")]), (2, [])],
                "code-securite": [(3, [make_scenario(id="menu", profile="paysage")])],
            }
        )
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(
            [(item["id"], item["profile"]) for item in result["scenarios"]],
            [("code-performance:menu", "portrait"), ("code-securite:menu", "paysage")],
        )
        self.assertEqual(result["lots"], ["code-performance", "code-securite"])

    def test_nothing_asked_gives_an_empty_collection(self):
        run_dir = self.make_run({"code-securite": [(103, [])]})
        self.assertEqual(
            scenarios.followup_scenarios(run_dir),
            {"base_url": BASE, "scenarios": [], "lots": [], "discarded": []},
        )

    def test_a_finding_without_the_field_asks_for_nothing(self):
        run_dir = self.make_run({"code-securite": [(103, [])]})
        document = json.loads((run_dir / "findings" / "code-securite.json").read_text("utf-8"))
        del document["findings"][0]["needs_scenarios"]
        self.write(run_dir, "findings/code-securite.json", document)
        self.assertEqual(scenarios.followup_scenarios(run_dir)["scenarios"], [])
        self.assertEqual(scenarios.pending_lots(run_dir, [{"id": "code-securite", "refs": [103]}]), [])

    def test_urls_of_another_origin_or_a_php_file_are_discarded_with_their_reason(self):
        foreign = [
            "https://other.example.net/p",
            "//other.example.net/x",
            "https://preprod.example.org.other.example.net/",
            "http://preprod.example.org/plain",
            "https://preprod.example.org:8443/port",
        ]
        php = f"{BASE}/wp-content/uploads/x.php"
        run_dir = self.make_run(
            {"code-securite": requests_of(foreign, "o") + [(3, [make_scenario(id="php", url=php)])]}
        )
        document = json.loads((run_dir / "findings" / "code-securite.json").read_text("utf-8"))
        valid, errors = scenarios.validate_document(document, {1, 2, 3})
        self.assertEqual([item["ref"] for item in valid], [1, 2])
        self.assertTrue(any(PHP_FILE_REASON in error for error in errors))

        with mock.patch.object(scenarios, "validate_document", held_valid):
            result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result["scenarios"], [])
        self.assertEqual(result["lots"], [])
        shown = foreign + [php]
        self.assertEqual([item["url"] for item in result["discarded"]], shown)
        self.assertTrue(all(item["reason"] for item in result["discarded"]))
        self.assertEqual(result["discarded"][-1]["reason"], PHP_FILE_REASON)
        self.assertEqual(result["discarded"][2]["reason"], ORIGIN_REASON)
        self.assertEqual(
            [(item["lot"], item["ref"], item["id"]) for item in result["discarded"]][-2:],
            [("code-securite", 2, "o-4"), ("code-securite", 3, "php")],
        )

        real = scenarios.followup_scenarios(run_dir)
        self.assertEqual(real["scenarios"], [])
        self.assertEqual([item["url"] for item in real["discarded"]], foreign)
        self.assertTrue(all(item["reason"] for item in real["discarded"]))

    def test_hosts_that_resolve_elsewhere_are_discarded(self):
        urls = [
            f"{BASE}//evil.example.net/x",
            f"{BASE}/\\evil.example.net/x",
            "https://sub.preprod.example.org/",
            "ftp://preprod.example.org/file",
        ]
        run_dir = self.make_run({"code-securite": requests_of(urls, "h")})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result["scenarios"], [])
        self.assertEqual([item["url"] for item in result["discarded"]], urls)
        self.assertTrue(all(item["reason"] for item in result["discarded"]))

    def test_action_urls_are_discarded_with_their_reason_and_never_collected(self):
        actions = [
            f"{BASE}/user/logout",
            f"{BASE}/?action=trash",
            f"{BASE}/node/1/delete",
            f"{BASE}/page?token=abc",
            f"{BASE}/%6Cogout",
        ]
        run_dir = self.make_run(
            {
                "code-securite": [
                    (1, [make_scenario(id=f"a-{n}", url=url) for n, url in enumerate(actions[:3])]),
                    (2, [make_scenario(id=f"a-{n}", url=url) for n, url in enumerate(actions[3:], 3)]),
                    (3, [make_scenario(id="fine", url=f"{BASE}/produits/delete-pro-fr")]),
                ]
            }
        )
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual([item["id"] for item in result["scenarios"]], ["code-securite:fine"])
        self.assertEqual([item["url"] for item in result["discarded"]], actions)
        self.assertEqual({item["reason"] for item in result["discarded"]}, {ACTION_URL_REASON})
        self.assertEqual(ACTION_URL_REASON, "URL d'action refusée")
        self.assertEqual(result["lots"], ["code-securite"])

    def test_an_action_url_alone_leaves_the_lot_out_of_the_relaunch(self):
        run_dir = self.make_run({"code-securite": [(1, [make_scenario(url=f"{BASE}/user/logout")])]})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual((result["scenarios"], result["lots"]), ([], []))
        self.assertEqual(len(result["discarded"]), 1)
        self.assertEqual(scenarios.pending_lots(run_dir, [{"id": "code-securite", "refs": [1]}]), ["code-securite"])

    def test_the_url_is_the_origin_of_the_target_plus_the_path_and_the_query(self):
        run_dir = self.make_run(
            {
                "code-securite": [
                    (
                        1,
                        [
                            make_scenario(id="a", url="https://PREPROD.example.org:443/a/b?x=1&y=2#top"),
                            make_scenario(id="b", url="https://preprod.example.org"),
                        ],
                    )
                ]
            }
        )
        urls = [item["url"] for item in scenarios.followup_scenarios(run_dir)["scenarios"]]
        self.assertEqual(urls, [f"{BASE}/a/b?x=1&y=2", f"{BASE}/"])

    def test_the_target_of_a_prod_run_is_the_prod(self):
        run_dir = self.make_run({"code-securite": [(1, [make_scenario(url="https://www.example.org/a")])]})
        self.write(
            run_dir,
            "context.json",
            {
                "phase_filter": "MEL J0",
                "target": {
                    "preprod_url": BASE,
                    "prod_url": "https://www.example.org",
                    "prod_live": True,
                },
            },
        )
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result["base_url"], "https://www.example.org")
        self.assertEqual([item["url"] for item in result["scenarios"]], ["https://www.example.org/a"])

    def test_the_path_of_the_application_decides_which_php_file_is_an_entry_point(self):
        run_dir = self.make_run(
            {
                "code-performance": [
                    (1, [make_scenario(id="entry", url=f"{BASE}/blog/index.php?p=12")])
                ],
                "code-securite": [(2, [make_scenario(id="other", url=f"{BASE}/blog/x.php")])],
            },
            base=f"{BASE}/blog/",
        )
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result["base_url"], f"{BASE}/blog/")
        self.assertEqual([item["url"] for item in result["scenarios"]], [f"{BASE}/blog/index.php?p=12"])
        self.assertEqual((result["lots"], result["discarded"]), (["code-performance"], []))
        lots = [
            {"id": "code-performance", "refs": [1]},
            {"id": "code-securite", "refs": [2]},
        ]
        self.assertEqual(scenarios.pending_lots(run_dir, lots, "/blog/"), ["code-performance"])
        self.assertEqual(scenarios.pending_lots(run_dir, lots), [])

    def test_credentials_are_never_shown(self):
        run_dir = self.make_run(
            {"code-securite": [(1, [make_scenario(url="https://user:pw@preprod.example.org/creds")])]}
        )
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result["scenarios"], [])
        self.assertEqual(len(result["discarded"]), 1)
        self.assertNotIn("pw", json.dumps(result))

    def test_cap_of_20_discards_the_rest_and_only_lots_with_a_kept_scenario_are_relaunched(self):
        performance = [
            (ref, [request(ref * 10 + n) for n in range(3 if ref < 7 else 2)])
            for ref in range(1, 8)
        ]
        securite = [(11, [request(110 + n) for n in range(3)]), (12, [request(120 + n) for n in range(2)])]
        run_dir = self.make_run({"code-performance": performance, "code-securite": securite})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(len(result["scenarios"]), 20)
        self.assertEqual(result["lots"], ["code-performance"])
        self.assertEqual(
            {item["id"].partition(":")[0] for item in result["scenarios"]}, {"code-performance"}
        )
        self.assertEqual(len(result["discarded"]), 5)
        self.assertEqual({item["lot"] for item in result["discarded"]}, {"code-securite"})
        self.assertEqual({item["reason"] for item in result["discarded"]}, {CAP_REASON})
        self.assertEqual(scenarios.MAX_FOLLOWUP_SCENARIOS, 20)
        lots = [
            {"id": "code-performance", "refs": [ref for ref, _ in performance]},
            {"id": "code-securite", "refs": [11, 12]},
        ]
        self.assertEqual(scenarios.pending_lots(run_dir, lots), ["code-performance", "code-securite"])

    def test_a_discarded_request_does_not_use_one_of_the_20_slots(self):
        needs = [request(n) for n in range(1, 22)]
        needs[4] = request(5, url="https://other.example.net/p")
        findings = [(ref, needs[(ref - 1) * 3 : ref * 3]) for ref in range(1, 8)]
        run_dir = self.make_run({"code-performance": findings})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(len(result["scenarios"]), 20)
        reasons = [item["reason"] for item in result["discarded"]]
        self.assertEqual(reasons, [ORIGIN_REASON])

    def test_invalid_findings_and_followup_files_are_ignored(self):
        run_dir = self.make_run({"code-securite": [(103, []), (104, [])]})
        document = json.loads((run_dir / "findings" / "code-securite.json").read_text("utf-8"))
        document["findings"][1].update(verdict="excellent", needs_scenarios=[make_scenario(id="bad")])
        self.write(run_dir, "findings/code-securite.json", document)
        for name in ("code-securite-followup", "code-securite-rerun-01"):
            self.write_findings(run_dir, "code-securite", [(103, [make_scenario(id="late")])], name)
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result, {"base_url": BASE, "scenarios": [], "lots": [], "discarded": []})
        lots = [{"id": "code-securite", "refs": [103, 104]}]
        self.assertEqual(scenarios.pending_lots(run_dir, lots), [])

    def test_a_lot_without_a_first_round_file_is_ignored(self):
        run_dir = self.make_run({"code-securite": [(103, [make_scenario()])]})
        lots = [{"id": "code-securite", "refs": [103]}, {"id": "code-performance", "refs": [1]}]
        self.write(run_dir, "lots.json", {"lots": lots})
        self.assertEqual(scenarios.followup_scenarios(run_dir)["lots"], ["code-securite"])
        self.assertEqual(scenarios.pending_lots(run_dir, lots), ["code-securite"])

    def test_pending_lots_keeps_the_lots_that_asked_whatever_becomes_of_the_requests(self):
        run_dir = self.make_run(
            {
                "code-performance": [(1, [make_scenario(url="https://other.example.net/p")])],
                "code-securite": [(2, [make_scenario()])],
                "code-qualite": [(3, [])],
            }
        )
        lots = [
            {"id": "code-performance", "refs": [1]},
            {"id": "code-securite", "refs": [2]},
            {"id": "code-qualite", "refs": [3]},
        ]
        self.assertEqual(scenarios.pending_lots(run_dir, lots), ["code-performance", "code-securite"])
        self.assertEqual(scenarios.followup_scenarios(run_dir)["lots"], ["code-securite"])

    def test_pending_lots_ignores_the_probes_of_a_finding(self):
        run_dir = self.make_run({"code-securite": [(1, [])]})
        document = json.loads((run_dir / "findings" / "code-securite.json").read_text("utf-8"))
        document["findings"][0]["needs_probes"] = [{"url": f"{BASE}/telescope", "method": "GET"}]
        self.write(run_dir, "findings/code-securite.json", document)
        lots = [{"id": "code-securite", "refs": [1]}]
        self.assertEqual(scenarios.pending_lots(run_dir, lots), [])
        self.assertEqual(probes.pending_lots(run_dir, lots), ["code-securite"])

    def test_hostile_selectors_travel_as_data(self):
        hostile = "a'); fetch('/x'); ('"
        steps = [{"type": "tap", "selector": hostile}, {"type": "state", "selector": "xpath=//a"}]
        run_dir = self.make_run({"code-securite": [(1, [make_scenario(steps=steps)])]})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual(result["scenarios"][0]["steps"], steps)

    def test_the_files_are_read_with_a_hint_when_missing(self):
        run_dir = self.make_run({"code-securite": [(1, [])]})
        (run_dir / "lots.json").unlink()
        with self.assertRaisesRegex(ValueError, "lots.json"):
            scenarios.followup_scenarios(run_dir)
        run_dir = self.make_run({"code-securite": [(1, [])]})
        (run_dir / "context.json").unlink()
        with self.assertRaisesRegex(ValueError, "context.json"):
            scenarios.followup_scenarios(run_dir)

    def test_a_target_with_a_query_is_refused_without_quoting_it(self):
        run_dir = self.make_run({"code-securite": [(1, [])]}, base=f"{BASE}/?token=abc")
        with self.assertRaises(ValueError) as caught:
            scenarios.followup_scenarios(run_dir)
        self.assertNotIn("abc", str(caught.exception))

    def test_a_context_that_is_not_an_object_is_refused(self):
        run_dir = self.make_run({"code-securite": [(1, [])]})
        self.write(run_dir, "context.json", ["not", "an", "object"])
        with self.assertRaisesRegex(ValueError, "objet JSON"):
            scenarios.followup_scenarios(run_dir)

    def test_a_malformed_lots_file_is_refused(self):
        run_dir = self.make_run({"code-securite": [(1, [])]})
        self.write(run_dir, "lots.json", {"lots": "none"})
        with self.assertRaisesRegex(ValueError, "lots"):
            scenarios.followup_scenarios(run_dir)

    def test_the_collection_reads_nothing_of_the_followup_files_of_the_tools(self):
        run_dir = self.make_run({"code-securite": [(1, [make_scenario()])]})
        self.write(run_dir, "scenarios-followup.json", {"scenarios": [{"id": "stale:x"}]})
        self.write(run_dir, "probes-followup.json", {"probes": []})
        result = scenarios.followup_scenarios(run_dir)
        self.assertEqual([item["id"] for item in result["scenarios"]], ["code-securite:menu-burger"])


if __name__ == "__main__":
    unittest.main()
