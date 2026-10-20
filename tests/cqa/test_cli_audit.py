import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from cqa import probes, report
from cqa.notion_write import apply_plan, validate_plan
from cqa.plan import operation_key
from tests.cqa import test_cli_prepare as prepare

FIXTURE_RUN = Path(__file__).resolve().parent / "fixtures" / "run"
RUN_ID = "2026-10-20-1430"
EM_DASH = chr(0x2014)
NO_SECRETS = {"NOTION_TOKEN": "", "GM_AUDIT_AUTH_PASS": "", "GM_SF_LICENCE_KEY": ""}
OP_KEYS = {
    "key",
    "kind",
    "page_id",
    "ref",
    "verdict",
    "before",
    "after",
    "text",
    "human_override",
}
PROBE_KEYS = {"id", "method", "path", "headers", "body", "sensitive"}
SENSITIVE_PATHS = [
    "/.env",
    "/.git/HEAD",
    "/composer.json",
    "/composer.lock",
    "/package.json",
    "/phpinfo.php",
]
BASE = "https://preprod.example.org"
EXPOSURE_PATHS = [
    "/telescope",
    "/horizon",
    "/_ignition/health-check",
    "/wp-json/wp/v2/users",
    "/xmlrpc.php",
    "/CHANGELOG.txt",
    "/core/CHANGELOG.txt",
    "/user/login",
    "/wp-login.php",
]
ESCAPING_URLS = [
    f"{BASE}//evil.example.net/x",
    f"{BASE}///evil.example.net/x",
    f"{BASE}/\\evil.example.net/x",
    f"{BASE}/\\\\evil.example.net/x",
    f"{BASE}//evil.example.net",
]


def origin_of(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or {"http": 80, "https": 443}[parts.scheme]


class AuditCase(prepare.CliCase):
    """Command tests on a copy of the fixture run, in a temporary cache layout."""

    def run_main(self, argv, env=None, **kwargs):
        return super().run_main(argv, env={**NO_SECRETS, **(env or {})}, **kwargs)

    def make_run(self):
        base = self.tmp_dir()
        run_dir = base / "cache" / "acme-shop" / RUN_ID
        shutil.copytree(FIXTURE_RUN, run_dir)
        self.repo = base / "repos" / "laravel"
        (self.repo / "app" / "Http").mkdir(parents=True)
        (self.repo / "app" / "Http" / "Kernel.php").write_text("<?php\n", encoding="utf-8")
        self.edit(run_dir, "context.json", self._set_repo_path)
        return run_dir

    def _set_repo_path(self, context):
        context["repos"][0]["path"] = str(self.repo)

    def read(self, run_dir, name):
        return json.loads((run_dir / name).read_text(encoding="utf-8"))

    def write(self, run_dir, name, value):
        path = run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def edit(self, run_dir, name, change):
        """Apply change(document) to a JSON file of the run and write it back."""
        document = self.read(run_dir, name)
        change(document)
        self.write(run_dir, name, document)

    def edit_row(self, run_dir, row_ref, **changes):
        def change(document):
            for row in document["rows"]:
                if row["ref"] == row_ref:
                    row.update(changes)

        self.edit(run_dir, "rows.json", change)

    def edit_finding(self, run_dir, lot, finding_ref, **changes):
        def change(document):
            for item in document["findings"]:
                if item["ref"] == finding_ref:
                    item.update(changes)

        self.edit(run_dir, f"findings/{lot}.json", change)

    def set_evidence(self, run_dir, evidence):
        self.edit_finding(run_dir, "code-performance", 101, evidence=evidence)

    def validate_errors(self, run_dir, lot="code-performance"):
        code, out, err = self.run_main(["validate", run_dir, lot])
        document = json.loads(out)
        self.assertEqual(code, 0 if document["ok"] else 6, err)
        return document["errors"]


class CoverageAndLotsTest(AuditCase):
    def test_coverage_counts(self):
        run_dir = self.make_run()
        result = self.run_json(["coverage", run_dir])
        self.assertEqual(result, self.read(run_dir, "lots.json")["coverage"])
        self.assertEqual(result["total"], 6)
        self.assertEqual((result["evaluable"], result["evaluable_human"]), (4, 1))
        self.assertEqual((result["not_evaluable"], result["na"]), (1, 1))
        self.assertEqual([gain["tool"] for gain in result["gains"]], ["Navigateur"])

    def test_lots_writes_lots_json(self):
        run_dir = self.make_run()
        expected = self.read(run_dir, "lots.json")
        (run_dir / "lots.json").unlink()
        summary = self.run_json(["lots", run_dir])
        self.assertEqual(self.read(run_dir, "lots.json"), expected)
        ids = [lot["id"] for lot in summary["lots"]]
        self.assertEqual(ids, ["code-performance", "code-securite"])
        self.assertEqual([lot["rows"] for lot in summary["lots"]], [2, 2])
        self.assertEqual(summary["excluded"], {"techno": 1, "no_tools": 1})
        self.assertEqual(summary["coverage"], expected["coverage"])

    def test_lots_follow_the_selection_of_the_context(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda context: context.update(phase_filter="MEL J0"))
        summary = self.run_json(["lots", run_dir])
        self.assertEqual(summary["lots"], [])
        self.assertEqual(summary["coverage"]["deferred"], 5)
        self.assertEqual(summary["coverage"]["na"], 1)
        self.assertEqual(self.read(run_dir, "lots.json")["lots"], [])

    def test_missing_rows_json_is_a_usage_error(self):
        run_dir = self.make_run()
        (run_dir / "rows.json").unlink()
        code, out, err = self.run_main(["lots", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("rows.json", err)
        self.assertEqual(out, "")

    def test_invalid_context_is_refused(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda context: context.pop("technos"))
        code, out, err = self.run_main(["coverage", run_dir])
        self.assertEqual(code, 6)
        self.assertIn("technos", err)

    def test_missing_context_is_refused(self):
        code, out, err = self.run_main(["coverage", self.tmp_dir()])
        self.assertEqual(code, 6)
        self.assertIn("context.json", err)

    def test_kept_row_without_ref_is_a_usage_error(self):
        run_dir = self.make_run()
        self.edit_row(run_dir, 101, ref=None)
        code, out, err = self.run_main(["lots", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("ref", err)


class BuildProbesTest(unittest.TestCase):
    def build(self, technos=("Laravel",), rows=(), key_pages=(), base_url=BASE, fiches=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        for name, content in (fiches or {}).items():
            (folder / name).write_text(content, encoding="utf-8")
        return probes.build_probes(
            base_url=base_url,
            technos=list(technos),
            rows=list(rows),
            fiches_dir=folder,
            key_pages=list(key_pages),
        )

    def by_path(self, document):
        return {probe["path"]: probe for probe in document["probes"]}

    def test_contract_shape(self):
        document = self.build()
        self.assertEqual(set(document), {"base_url", "probes"})
        self.assertEqual(document["base_url"], BASE)
        ids = [probe["id"] for probe in document["probes"]]
        self.assertEqual(len(ids), len(set(ids)))
        for probe in document["probes"]:
            self.assertEqual(set(probe), PROBE_KEYS)
            self.assertIn(probe["method"], ("GET", "HEAD"))
            self.assertIn(probe["body"], ("keep", "none"))
            self.assertEqual(probe["headers"], {})
            self.assertIsInstance(probe["sensitive"], bool)

    def test_base_set_in_order(self):
        paths = [probe["path"] for probe in self.build(technos=())["probes"]]
        content = ["/", "/robots.txt", "/sitemap.xml", "/.well-known/security.txt"]
        self.assertEqual(paths[:4], content)
        self.assertRegex(paths[4], r"^/cqa-404-[0-9a-f]{8}$")
        self.assertEqual(paths[5], "http://preprod.example.org/")
        self.assertEqual(paths[6:], SENSITIVE_PATHS)

    def test_not_found_probe_is_random(self):
        first = self.build(technos=())["probes"][4]["path"]
        second = self.build(technos=())["probes"][4]["path"]
        self.assertNotEqual(first, second)

    def test_content_probes_keep_the_body(self):
        found = self.by_path(self.build(technos=()))
        for path in ("/", "/robots.txt", "/sitemap.xml", "/.well-known/security.txt"):
            self.assertEqual((found[path]["method"], found[path]["body"]), ("GET", "keep"))
            self.assertFalse(found[path]["sensitive"])
        self.assertEqual(found["http://preprod.example.org/"]["body"], "keep")

    def test_sensitive_probes_never_keep_the_body(self):
        found = self.by_path(self.build(technos=()))
        for path in SENSITIVE_PATHS:
            self.assertEqual(found[path]["method"], "GET", path)
            self.assertEqual(found[path]["body"], "none", path)
            self.assertTrue(found[path]["sensitive"], path)

    def test_techno_probes(self):
        expected = {
            "WordPress": ["/wp-login.php", "/xmlrpc.php", "/wp-json/wp/v2/users"],
            "Wordpress": ["/wp-login.php", "/xmlrpc.php", "/wp-json/wp/v2/users"],
            "Drupal": ["/user/login", "/CHANGELOG.txt", "/core/CHANGELOG.txt"],
            "Laravel": ["/telescope", "/horizon", "/_ignition/health-check"],
        }
        every = {path for paths in expected.values() for path in paths}
        for techno, paths in expected.items():
            found = self.by_path(self.build(technos=[techno]))
            self.assertTrue(set(paths) <= set(found), techno)
            self.assertFalse((every - set(paths)) & set(found), techno)
            for path in paths:
                self.assertEqual(found[path]["method"], "GET")
                self.assertEqual(found[path]["body"], "none")

    def test_nuxt_alone_adds_nothing(self):
        def stable(document):
            return [probe["path"] for probe in document["probes"] if "cqa-404" not in probe["path"]]

        self.assertEqual(stable(self.build(technos=["Nuxt"])), stable(self.build(technos=[])))

    def test_key_pages_keep_headers_only(self):
        pages = [
            {"template": "Accueil", "url": f"{BASE}/"},
            {"template": "Article", "url": f"{BASE}/actualites/exemple"},
            {"template": "Recherche", "url": f"{BASE}/recherche?q=test"},
            {"template": "Externe", "url": "https://other.example.net/page"},
        ]
        document = self.build(technos=(), key_pages=pages)
        found = self.by_path(document)
        self.assertEqual(sum(1 for probe in document["probes"] if probe["path"] == "/"), 1)
        for path in ("/actualites/exemple", "/recherche?q=test"):
            self.assertEqual((found[path]["method"], found[path]["body"]), ("GET", "none"))
            self.assertFalse(found[path]["sensitive"])
        self.assertFalse([path for path in found if "other.example.net" in path])

    def test_ids_of_key_pages_and_fiche_probes_are_consecutive(self):
        rows = [{"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}]
        fiches = {"CQ-101.md": f"`/` `/a` `/a` `{BASE}/b`"}
        pages = [{"template": "Accueil", "url": f"{BASE}/"}, {"template": "B", "url": f"{BASE}/p"}]
        document = self.build(technos=(), rows=rows, fiches=fiches, key_pages=pages)
        ids = [probe["id"] for probe in document["probes"] if probe["id"][:4] in ("page", "fich")]
        self.assertEqual(ids, ["page-1", "fiche-101-1", "fiche-101-2"])

    def test_fiche_paths_of_http_rows_only(self):
        rows = [
            {"ref": 101, "tools": ["Code", "HTTP"], "fiche_path": "fiches/CQ-101.md"},
            {"ref": 102, "tools": ["Code"], "fiche_path": "fiches/CQ-102.md"},
        ]
        fiches = {
            "CQ-101.md": f"Tester `/admin/login`, `/admin/login` et `{BASE}/api/status`.",
            "CQ-102.md": "Tester `/not-for-http`.",
        }
        found = self.by_path(self.build(technos=(), rows=rows, fiches=fiches))
        login = found["/admin/login"]
        self.assertEqual((login["method"], login["body"]), ("GET", "keep"))
        self.assertIn("/api/status", found)
        self.assertNotIn("/not-for-http", found)

    def test_fiche_citations_that_are_not_target_paths_are_ignored(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        ignored = [
            "https://other.example.net/page",
            "http://preprod.example.org/plain",
            "/produit/<slug>",
            "/user/{id}",
            "/produit/*",
            "/etc/nginx/nginx.conf",
            "/var/www/html",
            "//cdn.example.net/x.js",
            "/a b",
            "public/images",
            "Content-Encoding",
            "/../secret",
            "/page#ancre",
            "https://preprod.example.org//evil.example.net/x",
            "https://preprod.example.org/\\evil.example.net/x",
            "/\\evil.example.net/x",
        ]
        text = " ".join(f"`{item}`" for item in ignored) + " `/valide` `/home`"
        document = self.build(technos=(), rows=[row], fiches={"CQ-101.md": text})
        paths = [probe["path"] for probe in document["probes"] if probe["id"].startswith("fiche")]
        self.assertEqual(paths, ["/valide", "/home"])

    def test_urls_that_resolve_to_another_host_are_ignored(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        cited = ESCAPING_URLS + ["/\\evil.example.net/x", "/\\\\evil.example.net"]
        text = " ".join(f"`{item}`" for item in cited) + " `/valide`"
        pages = [{"template": "T", "url": url} for url in ESCAPING_URLS]
        document = self.build(
            technos=("Laravel",), rows=[row], fiches={"CQ-101.md": text}, key_pages=pages
        )
        ids = [probe["id"] for probe in document["probes"]]
        self.assertFalse([name for name in ids if name.startswith("page-")])
        self.assertEqual([name for name in ids if name.startswith("fiche-")], ["fiche-101-1"])

    def test_no_probe_leaves_the_origin_of_the_target(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        text = " ".join(f"`{item}`" for item in ESCAPING_URLS) + f" `{BASE}/ok` `/ok2`"
        pages = [{"template": "T", "url": url} for url in ESCAPING_URLS]
        pages.append({"template": "Ok", "url": f"{BASE}/page"})
        document = self.build(
            technos=("Laravel", "Drupal", "Wordpress"),
            rows=[row],
            fiches={"CQ-101.md": text},
            key_pages=pages,
        )
        absolute = [probe["path"] for probe in document["probes"] if "://" in probe["path"]]
        self.assertEqual(absolute, ["http://preprod.example.org/"])
        for probe in document["probes"]:
            if probe["path"] in absolute:
                continue
            joined = urljoin(BASE + "/", probe["path"])
            self.assertEqual(origin_of(joined), origin_of(BASE), probe["path"])
            self.assertFalse(probe["path"].startswith("//"), probe["path"])
            self.assertNotIn("\\", probe["path"])
        self.assertGreater(len(document["probes"]), 20)

    def test_stays_on_origin(self):
        origin = origin_of(BASE)
        allowed = ["/", "/a", "/a/b?c=d", "/%5Cevil.example.net", "/a//b", "/a:b"]
        refused = [
            "",
            "a",
            "//evil.example.net/x",
            "///x",
            "/\\evil.example.net/x",
            "/a\\b",
            "/\t/evil.example.net",
            "/\n/evil.example.net",
            "/a b",
            "/a\x7f",
            "http://evil.example.net/",
            "https://preprod.example.org/",
        ]
        for path in allowed:
            self.assertTrue(probes._stays_on_origin(path, origin), repr(path))
        for path in refused:
            self.assertFalse(probes._stays_on_origin(path, origin), repr(path))
        self.assertTrue(probes._stays_on_origin("/a", ("https", "::1", 8443)))

    def test_the_collection_refuses_a_path_that_leaves_the_origin(self):
        collected = probes._Collected(origin_of(BASE))
        self.assertFalse(collected.add("a", "//evil.example.net/x", body="keep"))
        self.assertFalse(collected.add("b", "/\\evil.example.net/x", body="keep"))
        self.assertFalse(collected.add("c", "https://evil.example.net/", body="keep"))
        insecure = "http://preprod.example.org/"
        self.assertTrue(collected.add("d", insecure, body="keep", absolute=True))
        self.assertTrue(collected.add("e", "/fine", body="keep"))
        self.assertEqual([probe["id"] for probe in collected.probes], ["d", "e"])

    def test_exposure_paths_never_keep_the_body_whatever_the_source(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        cited = EXPOSURE_PATHS + ["/wp-json/wp/v2/users?per_page=1", "/changelog.txt"]
        text = " ".join(f"`{item}`" for item in cited) + " `/accueil`"
        pages = [{"template": "Login", "url": f"{BASE}/user/login"}]
        found = self.by_path(
            self.build(technos=(), rows=[row], fiches={"CQ-101.md": text}, key_pages=pages)
        )
        for path in cited:
            self.assertEqual((found[path]["body"], found[path]["sensitive"]), ("none", True), path)
        plain = found["/accueil"]
        self.assertEqual((plain["body"], plain["sensitive"]), ("keep", False))

    def test_every_sensitive_base_path_stays_sensitive_from_any_source(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        cited = [f"{path}?x=1" for path in SENSITIVE_PATHS]
        text = " ".join(f"`{path}`" for path in cited)
        found = self.by_path(self.build(technos=(), rows=[row], fiches={"CQ-101.md": text}))
        for path in cited:
            self.assertEqual((found[path]["body"], found[path]["sensitive"]), ("none", True), path)

    def test_sensitive_fiche_paths_never_keep_the_body(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        text = "`/.env.backup` `/backup/db.sql` `/storage/logs/app.log` `/.git/config` `/accueil`"
        found = self.by_path(self.build(technos=(), rows=[row], fiches={"CQ-101.md": text}))
        for path in ("/.env.backup", "/backup/db.sql", "/storage/logs/app.log", "/.git/config"):
            self.assertEqual((found[path]["body"], found[path]["sensitive"]), ("none", True), path)
        plain = found["/accueil"]
        self.assertEqual((plain["body"], plain["sensitive"]), ("keep", False))

    def test_missing_fiche_is_skipped(self):
        row = {"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}
        document = self.build(technos=(), rows=[row])
        self.assertFalse([probe for probe in document["probes"] if probe["id"].startswith("fiche")])

    def test_no_http_version_unless_the_base_is_plain_https(self):
        for base in ("http://preprod.example.org", "https://preprod.example.org:8443"):
            paths = [probe["path"] for probe in self.build(technos=(), base_url=base)["probes"]]
            self.assertFalse([path for path in paths if path.startswith("http://")], base)
        document = self.build(technos=(), base_url="https://preprod.example.org:443")
        paths = [probe["path"] for probe in document["probes"]]
        self.assertIn("http://preprod.example.org/", paths)

    def test_refused_base_urls(self):
        for base in ("ftp://preprod.example.org", "https://user:pw@preprod.example.org", "preprod"):
            with self.assertRaises(ValueError):
                self.build(base_url=base)

    def test_every_probe_is_a_safe_method(self):
        rows = [{"ref": 101, "tools": ["HTTP"], "fiche_path": "fiches/CQ-101.md"}]
        document = self.build(
            technos=["Laravel", "Drupal", "Wordpress"],
            rows=rows,
            fiches={"CQ-101.md": "`/x`"},
            key_pages=[{"template": "A", "url": f"{BASE}/a"}],
        )
        self.assertEqual({probe["method"] for probe in document["probes"]}, {"GET"})


class ProbesCommandTest(AuditCase):
    def test_probes_include_fiche_paths(self):
        run_dir = self.make_run()
        result = self.run_json(["probes", run_dir])
        document = self.read(run_dir, "probes.json")
        self.assertEqual(set(document), {"base_url", "probes"})
        self.assertEqual(document["base_url"], BASE)
        found = {probe["path"]: probe for probe in document["probes"]}
        self.assertEqual(found["/admin/login"]["method"], "GET")
        self.assertIn("/api/status", found)
        self.assertNotIn("/not-for-http", found)
        self.assertEqual(result["file"], "probes.json")
        self.assertEqual(result["count"], len(document["probes"]))
        self.assertEqual(result["from_fiches"], 2)

    def test_probes_sensitive_body_none(self):
        run_dir = self.make_run()
        self.run_json(["probes", run_dir])
        found = {probe["path"]: probe for probe in self.read(run_dir, "probes.json")["probes"]}
        for path in SENSITIVE_PATHS:
            self.assertEqual(found[path]["body"], "none")
            self.assertTrue(found[path]["sensitive"])

    def test_probes_use_the_technos_key_pages_and_kept_rows(self):
        run_dir = self.make_run()
        self.run_json(["probes", run_dir])
        paths = [probe["path"] for probe in self.read(run_dir, "probes.json")["probes"]]
        self.assertIn("/telescope", paths)
        self.assertNotIn("/wp-login.php", paths)
        self.assertIn("/actualites/exemple", paths)
        self.assertEqual(paths.count("/"), 1)
        self.assertFalse([path for path in paths if "other.example.net" in path])

    def test_probes_ignore_rows_left_out_of_the_audit(self):
        run_dir = self.make_run()
        self.edit_row(run_dir, 101, status="En standby")
        self.run_json(["probes", run_dir])
        paths = [probe["path"] for probe in self.read(run_dir, "probes.json")["probes"]]
        self.assertNotIn("/admin/login", paths)

    def test_probes_target_the_prod_on_a_prod_phase(self):
        run_dir = self.make_run()

        def change(context):
            context["phase_filter"] = "MEL J0"
            context["target"].update(prod_url="https://www.example.org", prod_live=True)

        self.edit(run_dir, "context.json", change)
        self.run_json(["probes", run_dir])
        self.assertEqual(self.read(run_dir, "probes.json")["base_url"], "https://www.example.org")

    def test_probes_refuse_a_target_with_a_query(self):
        run_dir = self.make_run()
        self.edit(
            run_dir,
            "context.json",
            lambda context: context["target"].update(preprod_url=f"{BASE}/?token=abc"),
        )
        code, out, err = self.run_main(["probes", run_dir])
        self.assertEqual(code, 2)
        self.assertNotIn("abc", err)


class FollowupProbesTest(AuditCase):
    def test_followup_groups_and_deduplicates(self):
        run_dir = self.make_run()
        document = probes.followup_probes(run_dir)
        self.assertEqual(document["base_url"], BASE)
        self.assertEqual(
            [(probe["method"], probe["path"]) for probe in document["probes"]],
            [("GET", "/telescope"), ("HEAD", "/status")],
        )
        for probe in document["probes"]:
            self.assertEqual(set(probe), PROBE_KEYS)
            self.assertEqual(probe["headers"], {})
        self.assertEqual(
            [(probe["body"], probe["sensitive"]) for probe in document["probes"]],
            [("none", True), ("none", False)],
        )
        ids = [probe["id"] for probe in document["probes"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(document["lots"], ["code-performance", "code-securite"])

    def test_followup_discards_urls_outside_the_target_origin(self):
        run_dir = self.make_run()
        document = probes.followup_probes(run_dir)
        self.assertEqual(len(document["discarded"]), 1)
        item = document["discarded"][0]
        self.assertEqual(
            (item["lot"], item["ref"], item["url"]),
            ("code-securite", 103, "https://other.example.net/probe"),
        )
        self.assertTrue(item["reason"])

    def test_followup_ignores_invalid_findings(self):
        run_dir = self.make_run()
        paths = [probe["path"] for probe in probes.followup_probes(run_dir)["probes"]]
        self.assertNotIn("/hidden", paths)

    def test_followup_discard_reasons(self):
        run_dir = self.make_run()
        urls = [
            "http://preprod.example.org/plain",
            "https://preprod.example.org:8443/port",
            "https://user:pw@preprod.example.org/creds",
            "https://preprod.example.org/with space",
            "https://sub.preprod.example.org/sub",
            "https://preprod.example.org.evil.example.net/look-alike",
            "ftp://preprod.example.org/file",
        ]
        needs = [{"url": url, "method": "GET"} for url in urls]
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=needs)
        self.edit_finding(run_dir, "code-securite", 103, needs_probes=[])
        document = probes.followup_probes(run_dir)
        self.assertEqual(document["probes"], [])
        shown = [url.replace("user:pw@", "") for url in urls]
        self.assertEqual([item["url"] for item in document["discarded"]], shown)
        self.assertNotIn("pw", json.dumps(document["discarded"]))
        self.assertTrue(all(item["reason"] for item in document["discarded"]))

    def test_followup_refuses_urls_that_resolve_to_another_host(self):
        run_dir = self.make_run()
        needs = [{"url": url, "method": "GET"} for url in ESCAPING_URLS]
        needs.append({"url": f"{BASE}/fine", "method": "GET"})
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=needs)
        self.edit_finding(run_dir, "code-securite", 103, needs_probes=[])
        document = probes.followup_probes(run_dir)
        self.assertEqual([probe["path"] for probe in document["probes"]], ["/fine"])
        self.assertEqual([item["url"] for item in document["discarded"]], ESCAPING_URLS)
        self.assertTrue(all(item["reason"] for item in document["discarded"]))
        for probe in document["probes"]:
            self.assertEqual(origin_of(urljoin(BASE + "/", probe["path"])), origin_of(BASE))

    def test_followup_keeps_query_and_same_origin_variants(self):
        run_dir = self.make_run()
        needs = [
            {"url": "https://PREPROD.example.org:443/Search?q=a", "method": "GET"},
            {"url": "https://preprod.example.org", "method": "HEAD"},
        ]
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=needs)
        self.edit_finding(run_dir, "code-securite", 103, needs_probes=[])
        found = [(p["method"], p["path"]) for p in probes.followup_probes(run_dir)["probes"]]
        self.assertEqual(found, [("GET", "/Search?q=a"), ("HEAD", "/")])

    def test_followup_sensitive_paths_never_keep_the_body(self):
        run_dir = self.make_run()
        needs = [{"url": f"{BASE}/.env", "method": "GET"}]
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=needs)
        self.edit_finding(run_dir, "code-securite", 103, needs_probes=[])
        probe = probes.followup_probes(run_dir)["probes"][0]
        self.assertEqual((probe["body"], probe["sensitive"]), ("none", True))

    def test_followup_target_is_the_prod_on_a_prod_phase(self):
        run_dir = self.make_run()

        def change(context):
            context["phase_filter"] = "Post-MEL J+7"
            context["target"].update(prod_url="https://www.example.org", prod_live=True)

        self.edit(run_dir, "context.json", change)
        needs = [{"url": "https://www.example.org/a", "method": "GET"}]
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=needs)
        document = probes.followup_probes(run_dir)
        self.assertEqual(document["base_url"], "https://www.example.org")
        self.assertEqual([probe["path"] for probe in document["probes"]], ["/a"])
        self.assertIn(f"{BASE}/telescope", [item["url"] for item in document["discarded"]])

    def test_command_writes_the_contract_file_and_reports_the_rest(self):
        run_dir = self.make_run()
        result = self.run_json(["probes", run_dir, "--followup"])
        document = self.read(run_dir, "probes-followup.json")
        self.assertEqual(set(document), {"base_url", "probes"})
        self.assertEqual(len(document["probes"]), 2)
        self.assertFalse((run_dir / "probes.json").exists())
        self.assertEqual(result["file"], "probes-followup.json")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["lots"], ["code-performance", "code-securite"])
        self.assertEqual(result["discarded"][0]["url"], "https://other.example.net/probe")

    def test_command_writes_an_empty_file_when_nothing_is_pending(self):
        run_dir = self.make_run()
        for lot, ref in (("code-performance", 101), ("code-securite", 103)):
            self.edit_finding(run_dir, lot, ref, needs_probes=[])
        result = self.run_json(["probes", run_dir, "--followup"])
        self.assertEqual(self.read(run_dir, "probes-followup.json")["probes"], [])
        self.assertEqual((result["count"], result["lots"], result["discarded"]), (0, [], []))

    def test_followup_needs_lots_json(self):
        run_dir = self.make_run()
        (run_dir / "lots.json").unlink()
        code, out, err = self.run_main(["probes", run_dir, "--followup"])
        self.assertEqual(code, 2)
        self.assertIn("lots.json", err)


class ValidateTest(AuditCase):
    def test_valid_lot(self):
        run_dir = self.make_run()
        self.assertEqual(self.validate_errors(run_dir, "code-performance"), [])

    def test_invalid_finding_is_reported_with_exit_6(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["validate", run_dir, "code-securite"])
        document = json.loads(out)
        self.assertEqual(code, 6)
        self.assertFalse(document["ok"])
        self.assertEqual(len(document["errors"]), 1)
        self.assertIn("CQ-104", document["errors"][0])
        self.assertIn("excellent", document["errors"][0])

    def test_unknown_lot_is_a_usage_error(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["validate", run_dir, "code-ghost"])
        self.assertEqual(code, 2)
        self.assertIn("code-ghost", err)

    def test_missing_findings_file(self):
        run_dir = self.make_run()
        (run_dir / "findings" / "code-performance.json").unlink()
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("findings/code-performance.json", errors[0])

    def test_unreadable_findings_file(self):
        run_dir = self.make_run()
        (run_dir / "findings" / "code-performance.json").write_text("{nope", encoding="utf-8")
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("illisible", errors[0])

    def test_finding_outside_the_lot_and_missing_row(self):
        run_dir = self.make_run()
        self.edit_finding(run_dir, "code-performance", 102, ref=103)
        errors = self.validate_errors(run_dir)
        self.assertTrue(any("hors du lot" in error for error in errors))
        self.assertTrue(any("CQ-102" in error and "manquant" in error for error in errors))

    def test_followup_flag_reads_the_followup_file(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["validate", run_dir, "code-performance", "--followup"])
        self.assertEqual(code, 6)
        self.assertIn("code-performance-followup.json", json.loads(out)["errors"][0])
        shutil.copy(
            run_dir / "findings" / "code-performance.json",
            run_dir / "findings" / "code-performance-followup.json",
        )
        self.assertEqual(
            json.loads(self.run_main(["validate", run_dir, "code-performance", "--followup"])[1]),
            {"ok": True, "errors": []},
        )

    def test_followup_flag_does_not_read_the_first_round(self):
        run_dir = self.make_run()
        (run_dir / "findings" / "code-securite-followup.json").write_text(
            json.dumps({"lot": "code-securite", "findings": []}), encoding="utf-8"
        )
        code, out, err = self.run_main(["validate", run_dir, "code-securite", "--followup"])
        errors = json.loads(out)["errors"]
        self.assertEqual(code, 6)
        self.assertEqual(sorted(error for error in errors if "manquant" in error), [
            "constat manquant : CQ-103",
            "constat manquant : CQ-104",
        ])

    def test_file_evidence_names_a_repo_of_the_context(self):
        run_dir = self.make_run()
        ghost = {"type": "file", "repo": "ghost", "path": "app/Http/Kernel.php"}
        self.set_evidence(run_dir, [ghost])
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("CQ-101", errors[0])
        self.assertIn("ghost", errors[0])
        self.assertIn("laravel", errors[0])

    def test_file_evidence_names_an_existing_path(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [{"type": "file", "repo": "laravel", "path": "app/Missing.php"}])
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("app/Missing.php", errors[0])
        self.assertIn("laravel", errors[0])

    def test_file_evidence_refuses_paths_outside_the_repo(self):
        run_dir = self.make_run()
        (self.repo.parent / "outside.txt").write_text("x", encoding="utf-8")
        for path in ("../outside.txt", "/etc/passwd", "app/../../outside.txt"):
            self.set_evidence(run_dir, [{"type": "file", "repo": "laravel", "path": path}])
            errors = self.validate_errors(run_dir)
            self.assertEqual(len(errors), 1, path)
            self.assertIn("hors du repo", errors[0], path)

    def test_file_evidence_never_looks_outside_the_repo(self):
        run_dir = self.make_run()
        for path in ("../absent.txt", "/absent/file.txt", "app/../../absent.txt"):
            self.set_evidence(run_dir, [{"type": "file", "repo": "laravel", "path": path}])
            errors = self.validate_errors(run_dir)
            self.assertEqual(len(errors), 1, path)
            self.assertIn("hors du repo", errors[0], path)

    def test_file_evidence_refuses_a_symlink_leaving_the_repo(self):
        run_dir = self.make_run()
        (self.repo.parent / "outside.txt").write_text("x", encoding="utf-8")
        (self.repo / "link.txt").symlink_to(self.repo.parent / "outside.txt")
        self.set_evidence(run_dir, [{"type": "file", "repo": "laravel", "path": "link.txt"}])
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("hors du repo", errors[0])

    def test_file_evidence_accepts_a_directory_and_a_file(self):
        run_dir = self.make_run()
        self.set_evidence(
            run_dir,
            [
                {"type": "file", "repo": "laravel", "path": "app/Http/Kernel.php", "line": 3},
                {"type": "file", "repo": "laravel", "path": "app/Http"},
            ],
        )
        self.assertEqual(self.validate_errors(run_dir), [])

    def test_tool_evidence_needs_a_summary(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [{"type": "tool", "tool": "a11y", "ref": "a11y/summary.json#x"}])
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("a11y", errors[0])
        self.assertIn("tools/a11y/summary.json", errors[0])
        known = {"type": "tool", "tool": "http", "ref": "http/summary.json#root"}
        self.set_evidence(run_dir, [known])
        self.assertEqual(self.validate_errors(run_dir), [])

    def test_tool_evidence_with_a_path_in_the_name(self):
        run_dir = self.make_run()
        (run_dir / "tools" / "elsewhere").mkdir()
        (run_dir / "elsewhere.json").write_text("{}", encoding="utf-8")
        for name in ("../elsewhere", "http/../http", "", " "):
            self.set_evidence(run_dir, [{"type": "tool", "tool": name, "ref": "x"}])
            self.assertTrue(self.validate_errors(run_dir), repr(name))

    def test_other_evidence_types_are_not_checked_against_the_run(self):
        run_dir = self.make_run()
        self.set_evidence(
            run_dir,
            [
                {"type": "page", "url": "https://elsewhere.example.net/", "detail": None},
                {"type": "http", "url": "https://elsewhere.example.net/", "detail": "x"},
            ],
        )
        self.assertEqual(self.validate_errors(run_dir), [])

    def test_evidence_errors_are_numbered_like_the_document_errors(self):
        run_dir = self.make_run()
        self.set_evidence(
            run_dir,
            [
                {"type": "page", "url": "https://elsewhere.example.net/", "detail": None},
                {"type": "file", "repo": "ghost", "path": "a.php"},
                {"type": "tool", "tool": "ghost", "ref": "x"},
            ],
        )
        errors = self.validate_errors(run_dir)
        self.assertEqual(len(errors), 2)
        self.assertIn("preuve n° 2", errors[0])
        self.assertIn("preuve n° 3", errors[1])

    def test_followup_findings_get_the_same_evidence_checks(self):
        run_dir = self.make_run()
        document = self.read(run_dir, "findings/code-performance.json")
        document["findings"][0]["evidence"] = [{"type": "file", "repo": "ghost", "path": "a.php"}]
        self.write(run_dir, "findings/code-performance-followup.json", document)
        code, out, err = self.run_main(["validate", run_dir, "code-performance", "--followup"])
        self.assertEqual(code, 6)
        self.assertIn("ghost", json.loads(out)["errors"][0])


class PlanTest(AuditCase):
    def plan(self, run_dir, *flags):
        return self.run_json(["plan", run_dir, *flags])

    def ops(self, run_dir, name="notion/plan.json"):
        return {(op["ref"], op["kind"]): op for op in self.read(run_dir, name)["ops"]}

    def test_lots_and_plan_end_to_end(self):
        run_dir = self.make_run()
        (run_dir / "lots.json").unlink()
        self.run_json(["lots", run_dir])
        self.assertEqual(self.validate_errors(run_dir, "code-performance"), [])
        self.assertEqual(len(self.validate_errors(run_dir, "code-securite")), 1)
        summary = self.plan(run_dir)
        self.assertEqual(
            summary["by_status_after"],
            {
                "À traiter": 3,
                "Mise en place partielle": 1,
                "Ok / Recetté": 1,
                "Non applicable": 1,
            },
        )
        self.assertEqual(summary["blocking_non_conform"], [101, 103])
        self.assertEqual(summary["unknown"], [104, 106])
        self.assertEqual(
            summary["human_overrides"],
            [{"ref": 102, "before": "Mis en place", "after": "Ok / Recetté"}],
        )
        self.assertEqual(summary["deferred"], [])
        missing = [{"ref": 190, "title": "Auditer les sauvegardes"}]
        self.assertEqual(summary["missing_in_copy"], missing)
        plan = self.read(run_dir, "notion/plan.json")
        self.assertEqual(plan["summary"], summary)
        self.assertEqual(
            set(plan),
            {
                "run_id",
                "copy_data_source_id",
                "copy_page_id",
                "assignee_options",
                "ops",
                "summary",
                "rows",
            },
        )
        self.assertEqual(plan["run_id"], RUN_ID)
        self.assertEqual(plan["copy_data_source_id"], "ds-copy")
        self.assertEqual(plan["copy_page_id"], "page-copy")
        self.assertEqual(plan["assignee_options"], ["Léa", "Marc", "Julien D"])
        validate_plan(plan)

    def test_plan_operations(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        ops = self.ops(run_dir)
        self.assertEqual(
            sorted(ops),
            [
                (102, "comment"),
                (102, "status"),
                (103, "comment"),
                (103, "status"),
                (104, "comment"),
                (105, "comment"),
                (105, "status"),
                (106, "comment"),
            ],
        )
        for op in ops.values():
            self.assertEqual(set(op), OP_KEYS)
        self.assertEqual(ops[(102, "status")]["after"], "Ok / Recetté")
        self.assertTrue(ops[(102, "status")]["human_override"])
        self.assertEqual(ops[(103, "status")]["after"], "Mise en place partielle")
        self.assertFalse(ops[(103, "status")]["human_override"])
        self.assertEqual(ops[(105, "status")]["after"], "Non applicable")
        self.assertEqual(ops[(104, "comment")]["verdict"], "unknown")
        self.assertEqual(ops[(106, "comment")]["verdict"], "unknown")
        self.assertIn("outils indisponibles : Navigateur", ops[(106, "comment")]["text"])

    def test_comments_json_keys_are_read_as_integers(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        self.assertNotIn((101, "comment"), self.ops(run_dir))
        self.write(run_dir, "comments.json", {})
        self.plan(run_dir)
        self.assertIn((101, "comment"), self.ops(run_dir))

    def test_comments_json_with_a_foreign_key_is_refused(self):
        run_dir = self.make_run()
        self.write(run_dir, "comments.json", {"CQ-101": ["ko"]})
        code, out, err = self.run_main(["plan", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("comments.json", err)
        self.assertFalse((run_dir / "notion" / "plan.json").exists())

    def test_comment_text_uses_the_run_date_and_the_context(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda context: context.update(run_id="2026-03-02-0900"))
        self.plan(run_dir)
        text = self.ops(run_dir)[(103, "comment")]["text"]
        self.assertIn("Audit PRE-MEL · 02/03/2026 · lancé par Julien D", text)
        self.assertIn("develop@3f2a1c9", text)
        self.assertIn("gm:checklist-quality-audit 0.9.0", text)
        self.assertLessEqual(len(text), 1500)

    def test_plan_is_reproducible(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        first = (run_dir / "notion" / "plan.json").read_text(encoding="utf-8")
        self.plan(run_dir)
        self.assertEqual((run_dir / "notion" / "plan.json").read_text(encoding="utf-8"), first)

    def test_skip_human_overrides(self):
        run_dir = self.make_run()
        summary = self.plan(run_dir, "--skip-human-overrides")
        ops = self.ops(run_dir)
        self.assertFalse([key for key in ops if key[0] == 102])
        self.assertEqual(len(ops), 6)
        self.assertEqual(summary["human_overrides"], [])
        self.assertEqual(
            summary["by_status_after"],
            {"À traiter": 3, "Mise en place partielle": 1, "Mis en place": 1, "Non applicable": 1},
        )
        self.assertEqual(self.read(run_dir, "notion/plan.json")["summary"], summary)

    def test_history_of_earlier_runs_spares_repeated_comments(self):
        run_dir = self.make_run()
        earlier = run_dir.parent / "2026-10-19-1000"
        self.write(earlier, "context.json", {"notion": {"copy_data_source_id": "ds-copy"}})
        entries = [
            {"key": "k1", "kind": "comment", "ref": 104, "verdict": "unknown", "at": "x"},
            {"key": "k2", "kind": "comment", "ref": 106, "verdict": "unknown", "at": "x"},
        ]
        self.write(earlier, "notion/applied.json", {"applied": entries})
        self.plan(run_dir)
        refs = {ref for ref, kind in self.ops(run_dir) if kind == "comment"}
        self.assertEqual(refs, {102, 103, 105})

    def test_history_of_another_copy_is_ignored(self):
        run_dir = self.make_run()
        earlier = run_dir.parent / "2026-10-19-1000"
        self.write(earlier, "context.json", {"notion": {"copy_data_source_id": "ds-other"}})
        entries = [{"key": "k1", "kind": "comment", "ref": 104, "verdict": "unknown", "at": "x"}]
        self.write(earlier, "notion/applied.json", {"applied": entries})
        self.plan(run_dir)
        self.assertIn((104, "comment"), self.ops(run_dir))

    def test_plan_reads_the_missing_rows_of_rows_json(self):
        run_dir = self.make_run()
        self.edit(run_dir, "rows.json", lambda doc: doc.update(missing_in_copy=[]))
        self.assertEqual(self.plan(run_dir)["missing_in_copy"], [])
        two = [{"ref": 7, "title": "A"}, {"ref": 8, "title": "B"}]
        self.edit(run_dir, "rows.json", lambda doc: doc.update(missing_in_copy=two))
        self.assertEqual(len(self.plan(run_dir)["missing_in_copy"]), 2)

    def test_findings_with_unfounded_evidence_are_not_planned(self):
        run_dir = self.make_run()
        self.set_evidence(run_dir, [{"type": "file", "repo": "ghost", "path": "a.php"}])
        summary = self.plan(run_dir)
        self.assertIn(101, summary["unknown"])
        self.assertEqual(self.ops(run_dir)[(101, "comment")]["verdict"], "unknown")

    def test_followup_marks_every_ref_of_the_relaunched_lots(self):
        run_dir = self.make_run()
        pending = [{"url": "https://other.example.net/probe", "method": "GET"}]
        self.edit_finding(run_dir, "code-securite", 103, verdict="ok", needs_probes=pending)
        self.assertNotIn(103, self.plan(run_dir)["unknown"])
        findings = self.read(run_dir, "findings/code-securite.json")["findings"]
        followup = {"lot": "code-securite", "findings": [findings[0]]}
        self.write(run_dir, "findings/code-securite-followup.json", followup)
        summary = self.plan(run_dir)
        self.assertIn(103, summary["unknown"])
        self.assertNotIn((103, "status"), self.ops(run_dir))

    def discard_everything_but_one_ok_probe(self, run_dir):
        """102 is ok and asks for a probe outside the origin: no probe can answer it."""
        pending = [{"url": "https://other.example.net/probe", "method": "GET"}]
        self.edit_finding(run_dir, "code-performance", 102, needs_probes=pending)
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=[])
        self.edit_finding(run_dir, "code-securite", 103, needs_probes=[])

    def test_unprobeable_pending_probes_cap_the_verdict_once_followup_was_requested(self):
        run_dir = self.make_run()
        self.discard_everything_but_one_ok_probe(run_dir)
        result = self.run_json(["probes", run_dir, "--followup"])
        self.assertEqual((result["count"], result["lots"]), (0, ["code-performance"]))
        self.assertFalse((run_dir / "findings" / "code-performance-followup.json").exists())
        summary = self.plan(run_dir)
        self.assertIn(102, summary["unknown"])
        self.assertNotIn((102, "status"), self.ops(run_dir))
        self.assertEqual(self.ops(run_dir)[(102, "comment")]["verdict"], "unknown")

    def test_the_cap_only_covers_the_lots_with_pending_probes(self):
        run_dir = self.make_run()
        self.discard_everything_but_one_ok_probe(run_dir)
        self.edit_finding(run_dir, "code-securite", 103, verdict="ok", todo=None)
        self.run_json(["probes", run_dir, "--followup"])
        summary = self.plan(run_dir)
        self.assertNotIn(103, summary["unknown"])
        self.assertEqual(self.ops(run_dir)[(103, "status")]["after"], "Ok / Recetté")

    def test_no_cap_before_the_followup_was_requested(self):
        run_dir = self.make_run()
        self.discard_everything_but_one_ok_probe(run_dir)
        self.assertNotIn(102, self.plan(run_dir)["unknown"])
        self.assertEqual(self.ops(run_dir)[(102, "status")]["after"], "Ok / Recetté")

    def test_without_followup_a_pending_probe_does_not_lower_the_verdict(self):
        run_dir = self.make_run()
        pending = [{"url": "https://other.example.net/probe", "method": "GET"}]
        self.edit_finding(run_dir, "code-performance", 102, needs_probes=pending)
        summary = self.plan(run_dir)
        self.assertNotIn(102, summary["unknown"])
        self.assertEqual(self.ops(run_dir)[(102, "status")]["after"], "Ok / Recetté")

    def test_followup_findings_replace_the_first_round(self):
        run_dir = self.make_run()
        followup = self.read(run_dir, "findings/code-performance.json")
        followup["findings"][1].update(verdict="ko", summary="Une image PNG subsiste.")
        self.write(run_dir, "findings/code-performance-followup.json", followup)
        self.plan(run_dir)
        ops = self.ops(run_dir)
        self.assertEqual(ops[(102, "status")]["after"], "À traiter")
        self.assertIn("Une image PNG subsiste.", ops[(102, "comment")]["text"])
        self.assertEqual(ops[(102, "comment")]["verdict"], "ko")

    def test_partial_followup_keeps_the_other_first_round_findings(self):
        run_dir = self.make_run()
        followup = self.read(run_dir, "findings/code-performance.json")
        followup["findings"] = [item for item in followup["findings"] if item["ref"] == 102]
        followup["findings"][0].update(verdict="ko", summary="Une image PNG subsiste.")
        self.write(run_dir, "findings/code-performance-followup.json", followup)
        self.plan(run_dir)
        self.assertEqual(self.ops(run_dir)[(102, "comment")]["verdict"], "ko")
        self.assertNotIn((101, "comment"), self.ops(run_dir))

    def test_missing_inputs_are_usage_errors(self):
        for name in ("lots.json", "rows.json", "comments.json"):
            run_dir = self.make_run()
            (run_dir / name).unlink()
            code, out, err = self.run_main(["plan", run_dir])
            self.assertEqual(code, 2, name)
            self.assertIn(name, err)
            self.assertFalse((run_dir / "notion" / "plan.json").exists())

    def test_damaged_lots_json_is_a_usage_error(self):
        for damage in (
            lambda doc: doc["excluded"][0].pop("detail"),
            lambda doc: doc["excluded"].append(3),
            lambda doc: doc["lots"][0].pop("refs"),
            lambda doc: doc.pop("excluded"),
        ):
            run_dir = self.make_run()
            self.edit(run_dir, "lots.json", damage)
            code, out, err = self.run_main(["plan", run_dir])
            self.assertEqual(code, 2)
            self.assertIn("lots.json", err)

    def test_context_must_carry_what_comments_need(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda c: c["repos"][0].update(audited_sha=None))
        code, out, err = self.run_main(["plan", run_dir])
        self.assertEqual(code, 6)
        self.assertIn("audited_sha", err)
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda c: c["lead"].update(option=None))
        code, out, err = self.run_main(["plan", run_dir])
        self.assertEqual(code, 6)
        self.assertIn("lead", err)
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda c: c.update(run_id="today"))
        code, out, err = self.run_main(["plan", run_dir])
        self.assertEqual(code, 6)
        self.assertIn("run_id", err)

    def test_plan_carries_no_secret_and_no_em_dash(self):
        run_dir = self.make_run()
        self.plan(run_dir)
        text = (run_dir / "notion" / "plan.json").read_text(encoding="utf-8")
        self.assertNotIn(EM_DASH, text)
        code, out, err = self.run_main(["scan", run_dir])
        self.assertEqual(code, 0, out)


class ManifestTest(AuditCase):
    def test_manifest_collects_the_results(self):
        run_dir = self.make_run()
        document = self.run_json(["manifest", run_dir])
        self.assertEqual(document, self.read(run_dir, "tools/manifest.json"))
        self.assertEqual(document["toolbox_version"], "1.2.0")
        self.assertEqual([item["tool"] for item in document["tools"]], ["http", "lighthouse"])
        self.assertEqual(document["tools"][0], self.read(run_dir, "tools/http/result.json"))
        self.assertEqual(document["tools"][1]["status"], "failed")

    def test_manifest_reports_a_tool_without_result_as_failed(self):
        run_dir = self.make_run()
        (run_dir / "tools" / "nu").mkdir()
        (run_dir / "tools" / "testssl").mkdir()
        (run_dir / "tools" / "testssl" / "result.json").write_text("{nope", encoding="utf-8")
        tools = report.manifest(run_dir)["tools"]
        self.assertEqual([item["tool"] for item in tools], ["http", "lighthouse", "nu", "testssl"])
        for item in tools[2:]:
            self.assertEqual(item["status"], "failed")
            self.assertIn("result.json", item["reason"])

    def test_a_result_without_tool_or_status_takes_the_folder_name(self):
        run_dir = self.make_run()
        self.write(run_dir, "tools/assets/result.json", {"reason": "x"})
        tools = {item["tool"]: item for item in report.manifest(run_dir)["tools"]}
        self.assertEqual(sorted(tools), ["assets", "http", "lighthouse"])
        self.assertEqual(tools["assets"]["reason"], "x")
        self.write(run_dir, "tools/nu/result.json", {"tool": 5, "status": "ok"})
        self.assertIn("nu", [item["tool"] for item in report.manifest(run_dir)["tools"]])

    def test_manifest_without_tools(self):
        run_dir = self.make_run()
        shutil.rmtree(run_dir / "tools")
        self.assertEqual(report.manifest(run_dir), {"toolbox_version": None, "tools": []})

    def test_manifest_ignores_its_own_file(self):
        run_dir = self.make_run()
        self.run_json(["manifest", run_dir])
        self.assertEqual(len(self.run_json(["manifest", run_dir])["tools"]), 2)

    def test_manifest_takes_the_version_of_the_first_result_that_has_one(self):
        run_dir = self.make_run()
        self.run_json(["skip", run_dir, "assets", "raison"])
        document = report.manifest(run_dir)
        tools = [item["tool"] for item in document["tools"]]
        self.assertEqual(tools, ["assets", "http", "lighthouse"])
        self.assertEqual(document["toolbox_version"], "1.2.0")


class SynthesisTest(AuditCase):
    def make_planned_run(self):
        run_dir = self.make_run()
        self.run_json(["plan", run_dir])
        return run_dir

    def test_synthesis_texts(self):
        run_dir = self.make_planned_run()
        result = self.run_json(["synthesis", run_dir])
        text = (run_dir / "synthese.md").read_text(encoding="utf-8")
        comment = (run_dir / "notion" / "page-comment.txt").read_text(encoding="utf-8")
        self.assertEqual(result["synthese"], "synthese.md")
        self.assertEqual(result["page_comment"], "notion/page-comment.txt")
        self.assertEqual(result["page_comment_length"], len(comment))
        blocking = text.split("## Lignes bloquantes")[1].split("##")[0]
        self.assertIn("CQ-101", blocking)
        self.assertIn("CQ-103", blocking)
        self.assertNotIn("CQ-102", blocking)
        self.assertLessEqual(len(comment), 1500)
        self.assertIn("CQ-101", comment)
        self.assertIn("lancé par Julien D", comment)
        self.assertNotIn(EM_DASH, text + comment)

    def test_synthesis_sections(self):
        run_dir = self.make_planned_run()
        text, comment = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 17))
        for heading in (
            "## Couverture",
            "## Décompte par statut",
            "## Décompte par catégorie",
            "## Lignes bloquantes non conformes",
            "## Lignes non évaluées",
            "## Statuts posés par un humain",
            "## Lignes reportées à J0 / J+7",
            "## Lignes du modèle absentes de la copie",
            "## Écarts relevés",
            "## Outils",
        ):
            self.assertIn(heading, text)
        self.assertIn("Durée de l'audit : 47 min", text)
        self.assertIn("- À traiter : 3", text)
        self.assertIn("- PERFORMANCE : 2 lignes (À traiter : 1, Ok / Recetté : 1)", text)
        self.assertIn("- CQ-190 : Auditer les sauvegardes", text)
        self.assertIn("CQ-102", text.split("## Statuts posés par un humain")[1].split("##")[0])
        self.assertIn("- Navigateur : 1 ligne", text)
        self.assertIn("- lighthouse : en échec (navigateur de test indisponible)", text)
        self.assertIn("- http : ok", text)

    def test_unknown_rows_come_with_their_reason(self):
        run_dir = self.make_planned_run()
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        unknown = text.split("## Lignes non évaluées")[1].split("##")[0]
        self.assertIn("CQ-104", unknown)
        self.assertIn("constat invalide ou absent", unknown)
        self.assertIn("CQ-106", unknown)
        self.assertIn("outils indisponibles : Navigateur", unknown)

    def test_unknown_row_whose_comment_is_already_in_notion(self):
        run_dir = self.make_run()
        self.write(run_dir, "comments.json", {"101": ["ko"], "104": ["unknown"]})
        self.run_json(["plan", run_dir])
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        line = [item for item in text.splitlines() if item.startswith("- CQ-104")][0]
        self.assertIn("Notion", line)

    def test_excluded_unknown_row_keeps_its_reason_without_a_new_comment(self):
        run_dir = self.make_run()
        self.write(run_dir, "comments.json", {"101": ["ko"], "106": ["unknown"]})
        self.run_json(["plan", run_dir])
        ops = self.read(run_dir, "notion/plan.json")["ops"]
        self.assertFalse([op for op in ops if op["ref"] == 106])
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        line = [item for item in text.splitlines() if item.startswith("- CQ-106")][0]
        self.assertIn("outils indisponibles : Navigateur", line)

    def test_coverage_never_adds_the_human_rows_again(self):
        run_dir = self.make_planned_run()

        def change(lots):
            lots["coverage"].update(evaluable=10, evaluable_human=3)

        self.edit(run_dir, "lots.json", change)
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        self.assertIn("- Évaluables : 10 (dont 3 avec validation humaine)", text)
        self.assertNotIn("13", text.split("## Couverture")[1].split("##")[0])

    def test_synthesis_survives_a_tool_result_without_tool_or_status(self):
        run_dir = self.make_planned_run()
        self.write(run_dir, "tools/assets/result.json", {"reason": "x"})
        self.write(run_dir, "tools/nu/result.json", {"tool": "nu", "status": ["ok"]})
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        outils = text.split("## Outils")[1]
        self.assertIn("- assets : statut inconnu (x)", outils)
        self.assertIn("- nu : statut inconnu", outils)
        self.assertIn("- http : ok", outils)

    def test_alignment_gaps(self):
        run_dir = self.make_run()

        def change(context):
            context["repos"][0].update(aligned=False, deployed_sha="a1b2c3d" + "0" * 33)

        self.edit(run_dir, "context.json", change)
        self.run_json(["plan", run_dir])
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        gaps = text.split("## Écarts relevés")[1].split("##")[0]
        self.assertIn("laravel", gaps)
        self.assertIn("a1b2c3d", gaps)
        self.assertIn("3f2a1c9", gaps)

    def test_no_gap_when_aligned_or_unknown(self):
        run_dir = self.make_planned_run()
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        self.assertIn("Aucun.", text.split("## Écarts relevés")[1].split("##")[0])

    def test_em_dash_in_a_title_never_reaches_the_texts(self):
        run_dir = self.make_run()
        self.edit_row(run_dir, 101, title=f"Compression {EM_DASH} HTTP")
        self.run_json(["plan", run_dir])
        text, comment = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        self.assertNotIn(EM_DASH, text + comment)
        self.assertIn("Compression - HTTP", text)

    def test_long_summaries_stay_within_the_comment_limit(self):
        run_dir = self.make_run()

        def change(document):
            document["rows"] = [
                {**document["rows"][0], "ref": 1000 + n, "page_id": f"page-x{n}", "blocking": True}
                for n in range(300)
            ]
            document["rows"] += [row for row in self.read(run_dir, "rows.json")["rows"][1:]]

        self.edit(run_dir, "rows.json", change)
        self.edit(run_dir, "lots.json", lambda doc: doc.update(lots=[], excluded=[]))
        self.run_json(["plan", run_dir])
        _, comment = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        self.assertLessEqual(len(comment), 1500)

    def test_duration_formats(self):
        run_dir = self.make_planned_run()
        start = datetime(2026, 10, 20, 14, 30)
        for now, expected in (
            (datetime(2026, 10, 20, 14, 30, 20), "moins d'une minute"),
            (datetime(2026, 10, 20, 15, 35), "1 h 05 min"),
            (datetime(2026, 10, 20, 16, 30), "2 h 00 min"),
            (datetime(2026, 10, 20, 14, 0), "non mesurée"),
        ):
            text, _ = report.synthesis(run_dir, now=now)
            self.assertIn(f"Durée de l'audit : {expected}", text, now)
        self.assertEqual(report.run_start("2026-10-20-1430"), start)

    def test_run_start_refuses_a_bad_run_id(self):
        for value in ("today", "2026-13-40-2599", "2026-10-20", None, 5):
            with self.assertRaises(ValueError):
                report.run_start(value)

    def test_synthesis_needs_the_plan(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["synthesis", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("plan", err)
        self.assertFalse((run_dir / "synthese.md").exists())

    def test_synthesis_of_a_damaged_plan_is_a_usage_error(self):
        run_dir = self.make_planned_run()
        self.write(run_dir, "notion/plan.json", {"summary": 3})
        code, out, err = self.run_main(["synthesis", run_dir])
        self.assertEqual(code, 2)
        self.assertFalse((run_dir / "synthese.md").exists())

    def test_synthesis_follows_the_written_plan(self):
        run_dir = self.make_run()
        self.run_json(["plan", run_dir, "--skip-human-overrides"])
        text, _ = report.synthesis(run_dir, now=datetime(2026, 10, 20, 15, 0))
        self.assertIn("Aucun.", text.split("## Statuts posés par un humain")[1].split("##")[0])


class ScanTest(AuditCase):
    def test_scan_detects_secret(self):
        run_dir = self.make_run()
        raw = run_dir / "tools" / "http" / "raw"
        raw.mkdir(parents=True)
        (raw / "x.json").write_text('{"line": "DB_PASSWORD=hunter22"}', encoding="utf-8")
        code, out, err = self.run_main(["scan", run_dir])
        self.assertEqual(code, 7)
        document = json.loads(out)
        self.assertFalse(document["ok"])
        self.assertEqual(
            document["found"], [{"path": "tools/http/raw/x.json", "label": "credential_assignment"}]
        )
        self.assertNotIn("hunter22", out + err)

    def test_scan_of_the_clean_fixture(self):
        run_dir = self.make_run()
        self.assertEqual(self.run_json(["scan", run_dir]), {"ok": True, "found": []})

    def test_scan_knows_the_session_secrets(self):
        run_dir = self.make_run()
        (run_dir / "synthese.md").write_text("jeton tok-abcdef123456 oublié", encoding="utf-8")
        code, out, err = self.run_main(["scan", run_dir], env={"NOTION_TOKEN": "tok-abcdef123456"})
        self.assertEqual(code, 7)
        found = [{"path": "synthese.md", "label": "known_value"}]
        self.assertEqual(json.loads(out)["found"], found)
        self.assertNotIn("abcdef123456", out + err)

    def test_scan_of_a_missing_folder(self):
        code, out, err = self.run_main(["scan", self.tmp_dir() / "absent"])
        self.assertEqual(code, 2)


class OpenapiSpecsTest(AuditCase):
    def test_openapi_specs(self):
        run_dir = self.make_run()
        files = [
            "openapi.yaml",
            "docs/swagger.json",
            "api/openapi-v2.yml",
            "api/Swagger-UI.JSON",
            "vendor/pkg/openapi.yaml",
            "node_modules/pkg/swagger.yaml",
            ".git/openapi.json",
            "docs/openapi.md",
            "docs/my-openapi.yaml",
            "README.md",
        ]
        for name in files:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        code, out, err = self.run_main(["openapi-specs", run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.splitlines(),
            [
                "laravel\tapi/Swagger-UI.JSON",
                "laravel\tapi/openapi-v2.yml",
                "laravel\tdocs/swagger.json",
                "laravel\topenapi.yaml",
            ],
        )

    def test_openapi_specs_skip_symlinks_and_unsafe_names(self):
        run_dir = self.make_run()
        (self.repo.parent / "outside.yaml").write_text("x", encoding="utf-8")
        (self.repo / "openapi.yaml").symlink_to(self.repo.parent / "outside.yaml")
        (self.repo / "swagger\ttab.json").write_text("x", encoding="utf-8")
        (self.repo / "swagger.json").write_text("x", encoding="utf-8")
        code, out, err = self.run_main(["openapi-specs", run_dir])
        self.assertEqual(out.splitlines(), ["laravel\tswagger.json"])

    def test_openapi_specs_without_spec_prints_nothing(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["openapi-specs", run_dir])
        self.assertEqual((code, out), (0, ""))

    def test_openapi_specs_of_several_repos(self):
        run_dir = self.make_run()
        second = self.repo.parent / "nuxt"
        second.mkdir()
        (second / "openapi.json").write_text("x", encoding="utf-8")
        (self.repo / "openapi.yaml").write_text("x", encoding="utf-8")

        def change(context):
            context["repos"].append({**context["repos"][0], "name": "nuxt", "path": str(second)})

        self.edit(run_dir, "context.json", change)
        code, out, err = self.run_main(["openapi-specs", run_dir])
        self.assertEqual(out.splitlines(), ["laravel\topenapi.yaml", "nuxt\topenapi.json"])

    def test_openapi_specs_warn_about_a_missing_repo(self):
        run_dir = self.make_run()
        shutil.rmtree(self.repo)
        code, out, err = self.run_main(["openapi-specs", run_dir])
        self.assertEqual((code, out), (0, ""))
        self.assertIn("laravel", err)

    def test_openapi_specs_need_repos(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda context: context.update(repos=[]))
        code, out, err = self.run_main(["openapi-specs", run_dir])
        self.assertEqual(code, 6)


class SkipTest(AuditCase):
    def test_skip_writes_result(self):
        run_dir = self.make_run()
        result = self.run_json(["skip", run_dir, "yellowlab", "authentification requise"])
        document = self.read(run_dir, "tools/yellowlab/result.json")
        self.assertEqual(result, document)
        self.assertEqual(
            set(document),
            {
                "tool",
                "toolbox_version",
                "tool_version",
                "status",
                "reason",
                "started_at",
                "duration_s",
                "targets",
                "files",
                "counts",
            },
        )
        self.assertEqual(document["tool"], "yellowlab")
        self.assertEqual(document["status"], "skipped")
        self.assertEqual(document["reason"], "authentification requise")
        self.assertIsNone(document["toolbox_version"])
        self.assertIsNone(document["tool_version"])
        self.assertEqual(document["duration_s"], 0)
        self.assertEqual((document["targets"], document["files"], document["counts"]), ([], [], {}))
        self.assertRegex(document["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_skip_overwrites_an_earlier_result(self):
        run_dir = self.make_run()
        self.run_json(["skip", run_dir, "http", "relance"])
        self.assertEqual(self.read(run_dir, "tools/http/result.json")["status"], "skipped")

    def test_skip_refuses_unsafe_tool_names_and_empty_reasons(self):
        run_dir = self.make_run()
        for tool in ("../x", "a/b", "A b", "", ".hidden", "x" * 80):
            code, out, err = self.run_main(["skip", run_dir, tool, "raison"])
            self.assertEqual(code, 2, tool)
        for reason in ("", "   "):
            code, out, err = self.run_main(["skip", run_dir, "nu", reason])
            self.assertEqual(code, 2, repr(reason))
        self.assertFalse((run_dir / "tools" / "nu").exists())

    def test_skip_result_appears_in_the_manifest(self):
        run_dir = self.make_run()
        self.run_json(["skip", run_dir, "schemathesis", "authentification requise"])
        tools = {item["tool"]: item for item in report.manifest(run_dir)["tools"]}
        self.assertEqual(tools["schemathesis"]["status"], "skipped")


class AssignTest(AuditCase):
    def assign(self, run_dir, *flags):
        return self.run_json(["assign", run_dir, *flags])

    def proposed(self, document):
        return {item["ref"]: item["assignee"] for item in document["proposal"]}

    def test_assign_proposal(self):
        run_dir = self.make_run()
        document = self.assign(run_dir)
        self.assertEqual(document, self.read(run_dir, "notion/assign-proposal.json"))
        expected = {101: "Marc", 103: "Marc", 104: "Léa", 105: "Léa", 106: "Léa"}
        self.assertEqual(self.proposed(document), expected)
        self.assertEqual(document["sans_option"], ["Zoé Bernard"])
        self.assertFalse(document["include_unknown"])
        self.assertEqual(
            set(document["proposal"][0]), {"ref", "page_id", "title", "sections", "assignee"}
        )

    def test_members_without_option_are_never_proposed(self):
        run_dir = self.make_run()
        document = self.assign(run_dir)
        self.assertFalse([item for item in document["proposal"] if not item["assignee"]])
        self.edit(run_dir, "context.json", lambda c: c["team"].append(dict(c["team"][2])))
        self.assertEqual(self.assign(run_dir)["sans_option"], ["Zoé Bernard"])

    def test_team_without_any_option_falls_back_to_the_lead(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda c: c.update(team=[]))
        document = self.assign(run_dir)
        self.assertEqual({item["assignee"] for item in document["proposal"]}, {"Julien D"})
        self.assertEqual(document["sans_option"], [])

    def test_unknown_rows_are_added_on_request(self):
        run_dir = self.make_run()
        for ref in (101, 102, 104, 106):
            self.edit_row(run_dir, ref, status="Mis en place")
        self.assertEqual(sorted(self.proposed(self.assign(run_dir))), [103, 105])
        document = self.assign(run_dir, "--include-unknown")
        self.assertEqual(sorted(self.proposed(document)), [103, 104, 105, 106])
        self.assertTrue(document["include_unknown"])

    def test_unknown_verdict_covers_findings_and_exclusions(self):
        run_dir = self.make_run()
        for ref in (101, 102, 104, 105, 106):
            self.edit_row(run_dir, ref, status="Mis en place")
        proposed = self.proposed(self.assign(run_dir, "--include-unknown"))
        self.assertEqual(sorted(proposed), [103, 104, 106])

    def test_rows_without_ref_are_never_taken_for_unknown(self):
        run_dir = self.make_run()
        for ref in (101, 102, 103, 104, 105, 106):
            self.edit_row(run_dir, ref, status="Mis en place")

        def add_unpaired(document):
            document["rows"].append(
                {**document["rows"][0], "ref": None, "page_id": "page-none", "fiche_path": None}
            )

        self.edit(run_dir, "rows.json", add_unpaired)
        orphan = {"ref": None, "page_id": "page-none", "reason": "no_fiche", "verdict": "unknown"}
        self.edit(
            run_dir,
            "lots.json",
            lambda doc: doc["excluded"].append({**orphan, "detail": "x", "tools": []}),
        )
        proposed = self.proposed(self.assign(run_dir, "--include-unknown"))
        self.assertEqual(sorted(proposed), [104, 106])

    def test_guardrails_decide_the_verdict_of_a_row(self):
        run_dir = self.make_run()
        self.edit_row(run_dir, 102, status="Mis en place")
        pending = [{"url": "https://other.example.net/probe", "method": "GET"}]
        self.edit_finding(run_dir, "code-performance", 102, needs_probes=pending)
        self.assertNotIn(102, self.proposed(self.assign(run_dir, "--include-unknown")))
        followup = self.read(run_dir, "findings/code-performance.json")
        self.write(run_dir, "findings/code-performance-followup.json", followup)
        self.assertIn(102, self.proposed(self.assign(run_dir, "--include-unknown")))

    def test_unprobeable_pending_probes_make_a_row_unknown(self):
        run_dir = self.make_run()
        self.edit_row(run_dir, 102, status="Mis en place")
        pending = [{"url": "https://other.example.net/probe", "method": "GET"}]
        self.edit_finding(run_dir, "code-performance", 102, needs_probes=pending)
        self.edit_finding(run_dir, "code-performance", 101, needs_probes=[])
        self.run_json(["probes", run_dir, "--followup"])
        self.assertIn(102, self.proposed(self.assign(run_dir, "--include-unknown")))

    def test_include_unknown_needs_lots_json(self):
        run_dir = self.make_run()
        (run_dir / "lots.json").unlink()
        code, out, err = self.run_main(["assign", run_dir, "--include-unknown"])
        self.assertEqual(code, 2)
        self.assertIn("lots.json", err)
        self.assertEqual(sorted(self.proposed(self.assign(run_dir))), [101, 103, 104, 105, 106])

    def test_assign_needs_a_lead_option(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda context: context["lead"].update(option=None))
        code, out, err = self.run_main(["assign", run_dir])
        self.assertEqual(code, 6)
        self.assertIn("lead", err)


class AssignPlanTest(AuditCase):
    def prepare_plan(self, run_dir, edit=None):
        self.run_json(["assign", run_dir])
        if edit:
            self.edit(run_dir, "notion/assign-proposal.json", edit)
        return self.run_json(["assign-plan", run_dir])

    def plan(self, run_dir):
        return self.read(run_dir, "notion/assign-plan.json")

    def entry_for_102(self, assignee):
        return {
            "ref": 102,
            "page_id": "page-102",
            "title": "x",
            "sections": [],
            "assignee": assignee,
        }

    def test_assign_plan(self):
        run_dir = self.make_run()
        result = self.prepare_plan(run_dir)
        plan = self.plan(run_dir)
        self.assertEqual(
            set(plan),
            {"run_id", "copy_data_source_id", "copy_page_id", "assignee_options", "ops"},
        )
        self.assertEqual(plan["run_id"], RUN_ID)
        self.assertEqual(plan["copy_data_source_id"], "ds-copy")
        self.assertEqual(plan["copy_page_id"], "page-copy")
        self.assertEqual(plan["assignee_options"], ["Léa", "Marc", "Julien D"])
        validate_plan(plan)
        assigns = [op for op in plan["ops"] if op["kind"] == "assign"]
        self.assertEqual(
            [(op["ref"], op["page_id"], op["before"], op["after"]) for op in assigns],
            [
                (101, "page-101", [], ["Marc"]),
                (103, "page-103", [], ["Marc"]),
                (104, "page-104", [], ["Léa"]),
                (105, "page-105", [], ["Léa"]),
                (106, "page-106", [], ["Léa"]),
            ],
        )
        for op in plan["ops"]:
            self.assertEqual(set(op), OP_KEYS)
            self.assertFalse(op["human_override"])
        for op in assigns:
            self.assertEqual(op["key"], operation_key("assign", op["page_id"], op["after"], ""))
            self.assertEqual(op["text"], "")
        self.assertEqual(result["assigned"], 5)
        self.assertEqual(result["file"], "notion/assign-plan.json")
        self.assertTrue(result["page_comment"])

    def test_page_comment_summarizes_the_assignments(self):
        run_dir = self.make_run()
        self.prepare_plan(run_dir)
        plan = self.plan(run_dir)
        comments = [op for op in plan["ops"] if op["kind"] == "page_comment"]
        self.assertEqual(len(comments), 1)
        op = comments[0]
        self.assertEqual(plan["ops"][-1], op)
        self.assertEqual(op["page_id"], "page-copy")
        self.assertEqual(op["key"], operation_key("page_comment", "page-copy", None, op["text"]))
        self.assertIn("Répartition PRE-MEL · 20/10/2026 · lancée par Julien D", op["text"])
        self.assertIn("5 lignes assignées", op["text"])
        self.assertIn("- Léa : 3", op["text"])
        self.assertIn("- Marc : 2", op["text"])
        self.assertLessEqual(len(op["text"]), 1500)
        self.assertNotIn(EM_DASH, op["text"])
        validate_plan(plan)

    def test_page_comment_stays_within_the_limit(self):
        run_dir = self.make_run()
        names = [f"Dev{number:03d}-" + "x" * 20 for number in range(100)]
        self.edit(run_dir, "context.json", lambda c: c["notion"].update(assignee_options=names))

        def many_rows(document):
            template = document["rows"][0]
            document["rows"] = [
                {**template, "ref": 2000 + n, "page_id": f"page-m{n}", "status": "À traiter"}
                for n in range(100)
            ]

        self.edit(run_dir, "rows.json", many_rows)
        self.run_json(["assign", run_dir])
        proposal = self.read(run_dir, "notion/assign-proposal.json")
        for number, item in enumerate(proposal["proposal"]):
            item["assignee"] = names[number]
        self.write(run_dir, "notion/assign-proposal.json", proposal)
        self.run_json(["assign-plan", run_dir])
        text = [op for op in self.plan(run_dir)["ops"] if op["kind"] == "page_comment"][0]["text"]
        self.assertEqual(len(text), 1500)
        self.assertTrue(text.endswith("…"))
        self.assertIn("100 lignes assignées", text)

    def test_no_page_comment_without_copy_page(self):
        run_dir = self.make_run()
        self.edit(run_dir, "context.json", lambda c: c["notion"].update(copy_page_id=None))
        result = self.prepare_plan(run_dir)
        plan = self.plan(run_dir)
        self.assertIsNone(plan["copy_page_id"])
        self.assertFalse([op for op in plan["ops"] if op["kind"] == "page_comment"])
        self.assertEqual(len(plan["ops"]), 5)
        self.assertFalse(result["page_comment"])

    def test_no_page_comment_when_nothing_is_assigned(self):
        run_dir = self.make_run()
        self.prepare_plan(run_dir, lambda document: document.update(proposal=[]))
        plan = self.plan(run_dir)
        self.assertEqual(plan["ops"], [])

    def test_the_orchestrator_can_adjust_the_proposal(self):
        run_dir = self.make_run()

        def adjust(document):
            by_ref = {item["ref"]: item for item in document["proposal"]}
            by_ref[104]["assignee"] = "Marc"
            by_ref[105]["assignee"] = None
            document["proposal"] = [item for item in document["proposal"] if item["ref"] != 106]

        result = self.prepare_plan(run_dir, adjust)
        assigns = [op for op in self.plan(run_dir)["ops"] if op["kind"] == "assign"]
        self.assertEqual(
            [(op["ref"], op["after"]) for op in assigns],
            [(101, ["Marc"]), (103, ["Marc"]), (104, ["Marc"])],
        )
        self.assertEqual(result["assigned"], 3)
        text = [op for op in self.plan(run_dir)["ops"] if op["kind"] == "page_comment"][0]["text"]
        self.assertIn("3 lignes assignées", text)
        self.assertIn("- Marc : 3", text)

    def test_the_chosen_assignee_is_added_to_the_existing_ones(self):
        run_dir = self.make_run()
        self.edit_row(run_dir, 102, status="À traiter")

        def adjust(document):
            document["proposal"] = [self.entry_for_102("Marc")]

        self.prepare_plan(run_dir, adjust)
        assign = [op for op in self.plan(run_dir)["ops"] if op["kind"] == "assign"][0]
        self.assertEqual((assign["before"], assign["after"]), (["Léa"], ["Léa", "Marc"]))

    def test_an_assignee_already_on_the_row_is_left_alone(self):
        run_dir = self.make_run()

        def adjust(document):
            document["proposal"] = [self.entry_for_102("Léa")]

        result = self.prepare_plan(run_dir, adjust)
        self.assertEqual(self.plan(run_dir)["ops"], [])
        self.assertEqual((result["assigned"], result["unchanged"]), (0, 1))

    def test_an_option_that_does_not_exist_is_refused(self):
        run_dir = self.make_run()
        self.run_json(["assign", run_dir])

        def adjust(document):
            document["proposal"][0]["assignee"] = "Zoé"

        self.edit(run_dir, "notion/assign-proposal.json", adjust)
        code, out, err = self.run_main(["assign-plan", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("Zoé", err)
        self.assertFalse((run_dir / "notion" / "assign-plan.json").exists())

    def test_a_page_outside_the_rows_is_refused(self):
        run_dir = self.make_run()
        self.run_json(["assign", run_dir])
        self.edit(
            run_dir,
            "notion/assign-proposal.json",
            lambda document: document["proposal"][0].update(page_id="page-ghost"),
        )
        code, out, err = self.run_main(["assign-plan", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("page-ghost", err)

    def test_a_row_listed_twice_is_refused(self):
        run_dir = self.make_run()
        self.run_json(["assign", run_dir])
        self.edit(
            run_dir,
            "notion/assign-proposal.json",
            lambda document: document["proposal"].append(dict(document["proposal"][0])),
        )
        code, out, err = self.run_main(["assign-plan", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("CQ-101", err)

    def test_damaged_proposals_are_usage_errors(self):
        run_dir = self.make_run()
        code, out, err = self.run_main(["assign-plan", run_dir])
        self.assertEqual(code, 2)
        self.assertIn("assign-proposal.json", err)
        for document in ({"proposal": 3}, {"proposal": [3]}, {"proposal": [{"page_id": 4}]}, []):
            self.write(run_dir, "notion/assign-proposal.json", document)
            code, out, err = self.run_main(["assign-plan", run_dir])
            self.assertEqual(code, 2, document)

    def test_plan_is_reproducible(self):
        run_dir = self.make_run()
        self.prepare_plan(run_dir)
        first = (run_dir / "notion" / "assign-plan.json").read_text(encoding="utf-8")
        self.run_json(["assign-plan", run_dir])
        written = (run_dir / "notion" / "assign-plan.json").read_text(encoding="utf-8")
        self.assertEqual(written, first)


class FakeNotion:
    """The part of the Notion client that apply_plan uses, recording what it writes."""

    def __init__(self, options):
        self.options = options
        self.writes = []

    def data_source(self, data_source_id):
        options = [{"name": name} for name in self.options]
        return {"properties": {"Assignée à": {"multi_select": {"options": options}}}}

    def comments(self, page_id):
        return []

    def update_page(self, page_id, properties):
        self.writes.append(("update", page_id, properties))

    def create_comment(self, page_id, rich_text):
        self.writes.append(("comment", page_id, rich_text))


class ApplyContractTest(AuditCase):
    def apply(self, run_dir, name):
        plan = self.read(run_dir, name)
        client = FakeNotion(["Léa", "Marc", "Julien D"])
        context = self.read(run_dir, "context.json")
        rows = self.read(run_dir, "rows.json")["rows"]
        result = apply_plan(
            client,
            plan,
            run_dir=run_dir,
            reference_ds_id=context["notion"]["reference_data_source_id"],
            row_page_ids={row["page_id"] for row in rows},
            copy_page_id=context["notion"]["copy_page_id"],
            known_secrets=[],
            bot_id=context["notion"]["bot_id"],
            log=lambda message: None,
        )
        return plan, result, client

    def test_plan_is_applicable(self):
        run_dir = self.make_run()
        self.run_json(["plan", run_dir])
        plan, result, client = self.apply(run_dir, "notion/plan.json")
        self.assertEqual(result, {"done": 8, "skipped": 0, "blocked": [], "errors": []})
        self.assertEqual(len(client.writes), 8)

    def test_assign_plan_is_applicable(self):
        run_dir = self.make_run()
        self.run_json(["assign", run_dir])
        self.run_json(["assign-plan", run_dir])
        plan, result, client = self.apply(run_dir, "notion/assign-plan.json")
        self.assertEqual(result, {"done": 6, "skipped": 0, "blocked": [], "errors": []})
        kinds = [write[0] for write in client.writes]
        self.assertEqual(kinds.count("update"), 5)
        self.assertEqual(client.writes[-1][:2], ("comment", "page-copy"))
        marc = ("update", "page-101", {"Assignée à": {"multi_select": [{"name": "Marc"}]}})
        self.assertEqual(client.writes[0], marc)

    def test_applied_assign_plan_is_resumable(self):
        run_dir = self.make_run()
        self.run_json(["assign", run_dir])
        self.run_json(["assign-plan", run_dir])
        self.apply(run_dir, "notion/assign-plan.json")
        plan, result, client = self.apply(run_dir, "notion/assign-plan.json")
        self.assertEqual((result["done"], result["skipped"]), (0, 6))
        self.assertEqual(client.writes, [])


class RegistrationTest(AuditCase):
    def test_every_command_is_registered(self):
        for name in (
            "coverage",
            "lots",
            "probes",
            "validate",
            "plan",
            "manifest",
            "synthesis",
            "scan",
            "openapi-specs",
            "skip",
            "assign",
            "assign-plan",
        ):
            code, out, err = self.run_main([name, "--help"])
            self.assertEqual(code, 0, name)

    def test_preparation_commands_are_still_there(self):
        code, out, err = self.run_main(["context", "--help"])
        self.assertEqual(code, 0)

    def test_fixture_is_clean(self):
        text = "".join(
            path.read_text(encoding="utf-8")
            for path in FIXTURE_RUN.rglob("*")
            if path.is_file()
        )
        self.assertNotIn(EM_DASH, text)
        self.assertNotRegex(text, r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        self.assertNotRegex(text, r"\b[0-9a-f]{32}\b")
        for host in re.findall(r"https?://([^/\"\s`]+)", text):
            self.assertTrue(host.endswith("example.org") or host.endswith("example.net"), host)


if __name__ == "__main__":
    unittest.main()
