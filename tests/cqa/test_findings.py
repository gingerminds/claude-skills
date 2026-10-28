import copy
import itertools
import json
import unittest
from pathlib import Path

from cqa.findings import (
    CONFIDENCES,
    EVIDENCE_REQUIRED,
    MAX_FIELD_LENGTH,
    MAX_SUMMARY_LENGTH,
    PROBE_METHODS,
    REQUIRED_FINDING_KEYS,
    VERDICTS,
    apply_guardrails,
    requests_php_file,
    validate_document,
    worst,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "checklist-quality-audit"
    / "schemas"
    / "finding.schema.json"
)
EM_DASH = chr(0x2014)
HUMAN = "Intervention humaine"
LOT_REFS = {79, 80}
# A GET runs a PHP file: only the public entry points of the application may be probed.
PHP_FILE_PATHS = [
    "/wp-content/uploads/x.php",
    "/x.PHP",
    "/x.phtml",
    "/x.php5",
    "/x.phar",
    "/x.php/foo",
    "/x.php?y=1",
    "/x.%70hp",
    "/blog/index.php",
    "/wp-admin/admin-ajax.php?action=heartbeat",
    "/x.php.jpg",
    "/x.pht",
    "/x.php%20",
    "/x.php%00",
]
PHP_ALLOWED_PATHS = [
    "/index.php",
    "/index.php?q=node/1",
    "/wp-login.php",
    "/wp-login.php?action=lostpassword",
    "/wp-admin/admin-ajax.php",
    "/wp-config.php.bak",
]
PHP_FILE_ERROR = "fichier PHP hors des routes de l'application"
# The entry points sit under the path the application is served from (context.json target).
APP_PATH = "/blog"
PHP_ALLOWED_UNDER_APP = [
    "/blog/index.php",
    "/blog/index.php?p=12",
    "/blog/index.php/node/1",
    "/blog/wp-login.php",
    "/blog/wp-admin/admin-ajax.php",
]
PHP_REFUSED_UNDER_APP = [
    "/index.php",
    "/wp-login.php",
    "/blogger/index.php",
    "/blog/x.php",
    "/blog/../index.php",
    "/blog/index.php/../x.php",
    "/blog/wp-admin/admin-ajax.php?action=heartbeat",
]


def make_finding(**overrides):
    finding = {
        "ref": 79,
        "verdict": "ko",
        "confidence": "high",
        "summary": "3 éléments ont un tabindex positif : l'ordre de tabulation saute le menu.",
        "evidence": [
            {"type": "file", "repo": "nuxt", "path": "components/AppHeader.vue", "line": 42},
            {
                "type": "page",
                "url": "https://preprod.example.org/actualites/x",
                "detail": "parcours au clavier",
            },
            {"type": "tool", "tool": "a11y", "ref": "summary.json#home/3"},
        ],
        "todo": "Supprimer les tabindex positifs et suivre l'ordre du DOM.",
        "human_check": None,
        "divergence": None,
        "needs_probes": [],
    }
    finding.update(overrides)
    return finding


def make_ok(**overrides):
    overrides.setdefault("todo", None)
    return make_finding(verdict="ok", **overrides)


def make_document(*findings, lot="front-1"):
    return {"lot": lot, "findings": list(findings)}


def validate(*findings, lot_refs=None, **document_overrides):
    document = make_document(*findings)
    document.update(document_overrides)
    return validate_document(document, LOT_REFS if lot_refs is None else lot_refs)


def make_row(ref=79, tools=("Code",)):
    return {"ref": ref, "title": f"Task {ref}", "tools": list(tools)}


def guard(finding, row=None, followup_done=False):
    return apply_guardrails(finding, row or make_row(), followup_done=followup_done)


class ConstantsTest(unittest.TestCase):
    def test_verdicts_order_worst_to_best(self):
        self.assertEqual(VERDICTS, ("ko", "partial", "unknown", "ok_human", "ok", "na"))

    def test_confidences(self):
        self.assertEqual(CONFIDENCES, ("high", "medium", "low"))

    def test_required_keys(self):
        self.assertEqual(
            set(REQUIRED_FINDING_KEYS),
            {
                "ref",
                "verdict",
                "confidence",
                "summary",
                "evidence",
                "todo",
                "human_check",
                "divergence",
                "needs_probes",
            },
        )

    def test_limits(self):
        self.assertEqual(MAX_SUMMARY_LENGTH, 600)
        self.assertEqual(MAX_FIELD_LENGTH, 400)
        self.assertEqual(PROBE_METHODS, ("GET", "HEAD"))


class WorstTest(unittest.TestCase):
    def test_worst(self):
        self.assertEqual(worst(["ok", "partial", "na"]), "partial")

    def test_follows_the_verdict_order_for_every_pair(self):
        for better in range(len(VERDICTS)):
            for lesser in range(better + 1, len(VERDICTS)):
                with self.subTest(pair=(VERDICTS[better], VERDICTS[lesser])):
                    self.assertEqual(worst([VERDICTS[lesser], VERDICTS[better]]), VERDICTS[better])
                    self.assertEqual(worst([VERDICTS[better], VERDICTS[lesser]]), VERDICTS[better])

    def test_na_is_the_best(self):
        self.assertEqual(worst(["na", "ok"]), "ok")
        self.assertEqual(worst(["na", "na"]), "na")

    def test_accepts_any_iterable(self):
        self.assertEqual(worst(verdict for verdict in ("ok_human", "ok")), "ok_human")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            worst([])

    def test_unknown_verdict_raises(self):
        with self.assertRaises(ValueError):
            worst(["ok", "fine"])


class ValidateDocumentTest(unittest.TestCase):
    def test_valid_document(self):
        valid, errors = validate(make_finding(), make_finding(ref=80, verdict="ok"))
        self.assertEqual(errors, [])
        self.assertEqual([item["ref"] for item in valid], [79, 80])
        self.assertEqual(valid[0], make_finding())

    def test_returns_copies(self):
        document = make_document(make_finding(), make_finding(ref=80))
        before = copy.deepcopy(document)
        valid, errors = validate_document(document, LOT_REFS)
        self.assertEqual(errors, [])
        self.assertEqual(document, before)
        valid[0]["evidence"].append({"type": "tool", "tool": "x", "ref": "y"})
        self.assertEqual(document, before)

    def test_validation_errors(self):
        no_ref = make_finding()
        del no_ref["ref"]
        valid, errors = validate(
            make_finding(ref=999),
            make_finding(ref=79, verdict="great"),
            no_ref,
            make_finding(ref=80),
        )
        self.assertEqual([item["ref"] for item in valid], [80])
        self.assertEqual(
            errors,
            [
                "constat CQ-999 : Réf hors du lot",
                "constat CQ-79 : verdict « great » inconnu "
                "(valeurs possibles : ko, partial, unknown, ok_human, ok, na)",
                "constat n° 3 : champ manquant : ref",
            ],
        )

    def test_duplicate_ref_is_reported_once_and_rejects_every_copy(self):
        valid, errors = validate(
            make_finding(ref=79),
            make_finding(ref=79, verdict="ok"),
            make_finding(ref=79, verdict="na"),
            make_finding(ref=80),
        )
        self.assertEqual(errors, ["Réf en double : CQ-79 (3 constats)"])
        self.assertEqual([item["ref"] for item in valid], [80])

    def test_missing_finding(self):
        valid, errors = validate(make_finding(ref=79), lot_refs={79, 12, 3})
        self.assertEqual(errors, ["constat manquant : CQ-3", "constat manquant : CQ-12"])
        self.assertEqual([item["ref"] for item in valid], [79])

    def test_empty_findings_report_every_row(self):
        valid, errors = validate(lot_refs={80, 79})
        self.assertEqual(valid, [])
        self.assertEqual(errors, ["constat manquant : CQ-79", "constat manquant : CQ-80"])

    def test_invalid_finding_is_not_also_reported_missing(self):
        valid, errors = validate(
            make_finding(ref=79, confidence="sure"), make_finding(ref=80), lot_refs=LOT_REFS
        )
        self.assertEqual(valid[0]["ref"], 80)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("constat CQ-79 : confiance « sure » inconnue"))

    def test_every_problem_of_a_finding_is_reported_at_once(self):
        bad = make_finding(verdict="great", confidence="sure", summary="")
        valid, errors = validate(bad, make_finding(ref=80))
        self.assertEqual(len(errors), 3)
        self.assertEqual(len(valid), 1)

    def test_missing_keys_are_named(self):
        item = make_finding()
        for key in ("summary", "needs_probes"):
            del item[key]
        _, errors = validate(item, make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : champs manquants : summary, needs_probes"])

    def test_every_required_key_is_enforced(self):
        for key in REQUIRED_FINDING_KEYS:
            with self.subTest(key=key):
                item = make_finding()
                del item[key]
                valid, errors = validate(item, make_finding(ref=80))
                self.assertEqual([entry["ref"] for entry in valid], [80])
                self.assertTrue(errors)
                self.assertIn(key, errors[0])

    def test_nullable_fields_accept_null(self):
        item = make_finding(todo=None, human_check=None, divergence=None)
        valid, errors = validate(item, make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 2)

    def test_ref_must_be_an_integer(self):
        for value in ("79", 79.0, True, None, [79]):
            with self.subTest(value=value):
                valid, errors = validate(make_finding(ref=value), make_finding(ref=80))
                self.assertEqual([entry["ref"] for entry in valid], [80])
                self.assertEqual(errors[0], "constat n° 1 : « ref » doit être un entier")

    def test_verdict_must_be_a_known_string(self):
        for value in ("OK", "ok ", 1, None, ["ko"], {"a": 1}):
            with self.subTest(value=value):
                valid, errors = validate(make_finding(verdict=value), make_finding(ref=80))
                self.assertEqual(len(valid), 1)
                self.assertEqual(len(errors), 1)
                self.assertIn("verdict", errors[0])

    def test_every_verdict_is_accepted(self):
        for verdict in VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertEqual(validate(make_finding(verdict=verdict), make_finding(ref=80))[1], [])

    def test_confidence(self):
        for value in CONFIDENCES:
            self.assertEqual(validate(make_finding(confidence=value), make_finding(ref=80))[1], [])
        for value in ("High", "certain", None, 3):
            with self.subTest(value=value):
                _, errors = validate(make_finding(confidence=value), make_finding(ref=80))
                self.assertEqual(len(errors), 1)
                self.assertIn("confiance", errors[0])
                self.assertIn("high, medium, low", errors[0])

    def test_summary_bounds(self):
        ok_lengths = (1, MAX_SUMMARY_LENGTH)
        for length in ok_lengths:
            with self.subTest(length=length):
                _, errors = validate(make_finding(summary="x" * length), make_finding(ref=80))
                self.assertEqual(errors, [])
        _, errors = validate(make_finding(summary="x" * 601), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : « summary » dépasse 600 caractères (601)"])
        _, errors = validate(make_finding(summary=""), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : « summary » ne doit pas être vide"])
        _, errors = validate(make_finding(summary="  \n "), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : « summary » ne doit pas être vide"])
        _, errors = validate(make_finding(summary=12), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : « summary » doit être un texte"])

    def test_optional_texts_are_bounded_to_400_characters(self):
        for key in ("todo", "human_check", "divergence"):
            with self.subTest(key=key):
                valid, errors = validate(
                    make_finding(**{key: "é" * 400}), make_finding(ref=80), lot_refs=LOT_REFS
                )
                self.assertEqual(errors, [])
                self.assertEqual(len(valid), 2)
                valid, errors = validate(
                    make_finding(**{key: "é" * 401}), make_finding(ref=80), lot_refs=LOT_REFS
                )
                self.assertEqual(errors, [f"constat CQ-79 : « {key} » dépasse 400 caractères (401)"])
                self.assertEqual([item["ref"] for item in valid], [80])

    def test_optional_texts_must_be_text_or_null(self):
        for key in ("todo", "human_check", "divergence"):
            with self.subTest(key=key):
                _, errors = validate(make_finding(**{key: ["a"]}), make_finding(ref=80))
                self.assertEqual(errors, [f"constat CQ-79 : « {key} » doit être un texte ou null"])

    def test_evidence_must_be_a_list_of_objects(self):
        _, errors = validate(make_finding(evidence="file.php"), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : « evidence » doit être une liste"])
        _, errors = validate(make_finding(evidence=["file.php"]), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : preuve n° 1 : doit être un objet"])

    def test_empty_evidence_is_valid(self):
        self.assertEqual(validate(make_finding(evidence=[]), make_finding(ref=80))[1], [])

    def test_evidence_type(self):
        _, errors = validate(make_finding(evidence=[{"type": "log"}]), make_finding(ref=80))
        self.assertEqual(
            errors,
            ["constat CQ-79 : preuve n° 1 : type « log » inconnu (file, page, tool, http)"],
        )
        _, errors = validate(make_finding(evidence=[{"path": "a"}]), make_finding(ref=80))
        self.assertEqual(
            errors,
            ["constat CQ-79 : preuve n° 1 : type manquant (file, page, tool, http)"],
        )

    def test_evidence_required_keys_per_type(self):
        self.assertEqual(
            EVIDENCE_REQUIRED,
            {
                "file": ("repo", "path"),
                "page": ("url",),
                "tool": ("tool", "ref"),
                "http": ("url", "detail"),
            },
        )
        full = {
            "file": {"type": "file", "repo": "nuxt", "path": "a.vue"},
            "page": {"type": "page", "url": "https://preprod.example.org/"},
            "tool": {"type": "tool", "tool": "a11y", "ref": "summary.json#1"},
            "http": {"type": "http", "url": "https://preprod.example.org/", "detail": "200"},
        }
        for kind, keys in EVIDENCE_REQUIRED.items():
            self.assertEqual(validate(make_finding(evidence=[full[kind]]), make_finding(ref=80))[1], [])
            for key in keys:
                with self.subTest(kind=kind, key=key):
                    item = dict(full[kind])
                    del item[key]
                    valid, errors = validate(make_finding(evidence=[item]), make_finding(ref=80))
                    self.assertEqual([entry["ref"] for entry in valid], [80])
                    self.assertEqual(
                        errors,
                        [f"constat CQ-79 : preuve n° 1 ({kind}) : « {key} » manquant"],
                    )
                    item[key] = "  "
                    _, errors = validate(make_finding(evidence=[item]), make_finding(ref=80))
                    self.assertEqual(
                        errors,
                        [f"constat CQ-79 : preuve n° 1 ({kind}) : « {key} » doit être un texte non vide"],
                    )

    def test_file_line_is_an_optional_positive_integer(self):
        base = {"type": "file", "repo": "nuxt", "path": "a.vue"}
        for extra in ({}, {"line": None}, {"line": 1}, {"line": 4200}):
            with self.subTest(extra=extra):
                item = dict(base, **extra)
                self.assertEqual(validate(make_finding(evidence=[item]), make_finding(ref=80))[1], [])
        for value in ("42", 4.5, True, 0, -3):
            with self.subTest(line=value):
                item = dict(base, line=value)
                _, errors = validate(make_finding(evidence=[item]), make_finding(ref=80))
                self.assertEqual(
                    errors,
                    ["constat CQ-79 : preuve n° 1 (file) : « line » doit être un entier positif"],
                )

    def test_page_detail_is_optional_text(self):
        base = {"type": "page", "url": "https://preprod.example.org/"}
        for extra in ({}, {"detail": None}, {"detail": "parcours au clavier"}):
            item = dict(base, **extra)
            self.assertEqual(validate(make_finding(evidence=[item]), make_finding(ref=80))[1], [])
        _, errors = validate(make_finding(evidence=[dict(base, detail=5)]), make_finding(ref=80))
        self.assertEqual(
            errors, ["constat CQ-79 : preuve n° 1 (page) : « detail » doit être un texte ou null"]
        )

    def test_evidence_errors_name_the_evidence_index(self):
        bad = {"type": "file", "repo": "nuxt"}
        _, errors = validate(
            make_finding(evidence=[{"type": "tool", "tool": "a", "ref": "b"}, bad]),
            make_finding(ref=80),
        )
        self.assertEqual(errors, ["constat CQ-79 : preuve n° 2 (file) : « path » manquant"])

    def test_tool_dates_are_optional_and_checked(self):
        valid, errors = validate(make_finding(), make_finding(ref=80))
        self.assertEqual(errors, [])
        dates = {"crawl": "2026-10-27T21:15:05.068Z", "a11y": "2026-10-27T21:15:05Z"}
        valid, errors = validate(make_finding(tool_dates=dates), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["tool_dates"], dates)
        cases = {
            "Crawl": ("2026-10-27T21:15:05Z", "nom d'outil « Crawl » invalide"),
            "crawl": ("hier", "date « hier » invalide pour l'outil « crawl »"),
        }
        for name, (value, message) in cases.items():
            with self.subTest(name=name):
                valid, errors = validate(make_finding(tool_dates={name: value}), make_finding(ref=80))
                self.assertEqual([item["ref"] for item in valid], [80])
                self.assertEqual(errors, [f"constat CQ-79 : « tool_dates » : {message}"])
        for value in (["crawl"], "2026-10-27T21:15:05Z", None):
            with self.subTest(value=value):
                valid, errors = validate(make_finding(tool_dates=value), make_finding(ref=80))
                self.assertEqual(
                    errors, ["constat CQ-79 : « tool_dates » doit être un objet {outil: date}"]
                )

    def test_needs_probes(self):
        probes = [
            {"url": "https://preprod.example.org/robots.txt", "method": "GET"},
            {"url": "https://preprod.example.org/", "method": "HEAD"},
        ]
        valid, errors = validate(make_finding(needs_probes=probes), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["needs_probes"], probes)

    def test_needs_probes_rules(self):
        cases = {
            "method": (
                [{"url": "https://preprod.example.org/", "method": "POST"}],
                "constat CQ-79 : sonde n° 1 : « method » doit valoir GET ou HEAD",
            ),
            "lowercase": (
                [{"url": "https://preprod.example.org/", "method": "get"}],
                "constat CQ-79 : sonde n° 1 : « method » doit valoir GET ou HEAD",
            ),
            "no method": (
                [{"url": "https://preprod.example.org/"}],
                "constat CQ-79 : sonde n° 1 : « method » manquant",
            ),
            "no url": (
                [{"method": "GET"}],
                "constat CQ-79 : sonde n° 1 : « url » manquant",
            ),
            "blank url": (
                [{"url": " ", "method": "GET"}],
                "constat CQ-79 : sonde n° 1 : « url » doit être un texte non vide",
            ),
            "not an object": (
                ["https://preprod.example.org/"],
                "constat CQ-79 : sonde n° 1 : doit être un objet",
            ),
        }
        for name, (probes, expected) in cases.items():
            with self.subTest(name=name):
                valid, errors = validate(make_finding(needs_probes=probes), make_finding(ref=80))
                self.assertEqual(errors, [expected])
                self.assertEqual([item["ref"] for item in valid], [80])
        _, errors = validate(make_finding(needs_probes="GET /"), make_finding(ref=80))
        self.assertEqual(errors, ["constat CQ-79 : « needs_probes » doit être une liste"])

    def test_needs_probes_refuse_php_files_outside_the_routes(self):
        for path in PHP_FILE_PATHS:
            with self.subTest(path=path):
                probes = [{"url": f"https://preprod.example.org{path}", "method": "GET"}]
                valid, errors = validate(make_finding(needs_probes=probes), make_finding(ref=80))
                self.assertEqual(len(errors), 1)
                self.assertTrue(errors[0].startswith(f"constat CQ-79 : sonde n° 1 : {PHP_FILE_ERROR}"))
                self.assertEqual([item["ref"] for item in valid], [80])
        probes = [
            {"url": f"https://preprod.example.org{path}", "method": "HEAD"}
            for path in PHP_ALLOWED_PATHS
        ]
        valid, errors = validate(make_finding(needs_probes=probes), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 2)

    def test_needs_probes_follow_the_path_of_the_application(self):
        def errors_for(paths, app_path):
            base = "https://preprod.example.org"
            probes = [{"url": f"{base}{path}", "method": "GET"} for path in paths]
            document = make_document(make_finding(needs_probes=probes), make_finding(ref=80))
            return validate_document(document, LOT_REFS, app_path=app_path)[1]

        self.assertEqual(errors_for(PHP_ALLOWED_UNDER_APP, APP_PATH), [])
        self.assertEqual(errors_for(PHP_ALLOWED_UNDER_APP, f"{APP_PATH}/"), [])
        for path in PHP_REFUSED_UNDER_APP:
            with self.subTest(path=path):
                errors = errors_for([path], APP_PATH)
                self.assertEqual(len(errors), 1)
                self.assertIn(PHP_FILE_ERROR, errors[0])
                self.assertIn("/blog/index.php", errors[0])

    def test_extra_keys_are_tolerated(self):
        valid, errors = validate(make_finding(notes="x"), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 2)

    def test_document_must_be_an_object(self):
        for document in ([], "text", None, 3):
            with self.subTest(document=document):
                valid, errors = validate_document(document, LOT_REFS)
                self.assertEqual(valid, [])
                self.assertEqual(len(errors), 1)
                self.assertIn("objet JSON", errors[0])

    def test_findings_key(self):
        valid, errors = validate_document({"lot": "front-1"}, LOT_REFS)
        self.assertEqual((valid, errors), ([], ["champ « findings » manquant"]))
        valid, errors = validate_document({"lot": "front-1", "findings": {}}, LOT_REFS)
        self.assertEqual((valid, errors), ([], ["« findings » doit être une liste"]))

    def test_non_object_finding(self):
        valid, errors = validate("CQ-79", make_finding(ref=79), make_finding(ref=80))
        self.assertEqual(len(valid), 2)
        self.assertEqual(errors, ["constat n° 1 : doit être un objet JSON"])

    def test_lot_key(self):
        findings = [make_finding(ref=79), make_finding(ref=80)]
        valid, errors = validate_document({"findings": findings}, LOT_REFS)
        self.assertEqual(errors, ["champ « lot » manquant"])
        self.assertEqual(len(valid), 2)
        for value in ("", "  ", 4, None):
            with self.subTest(value=value):
                _, errors = validate_document({"lot": value, "findings": findings}, LOT_REFS)
                self.assertEqual(errors, ["« lot » doit être un texte non vide"])

    def test_no_em_dash_in_messages(self):
        bad = make_finding(
            verdict="x",
            confidence="y",
            summary="",
            todo="t" * 500,
            evidence=[{"type": "z"}, 5],
            needs_probes=[{"method": "PUT"}],
        )
        _, errors = validate(bad, make_finding(ref=999), lot_refs={79, 1})
        self.assertGreater(len(errors), 5)
        for message in errors:
            self.assertNotIn(EM_DASH, message)


class RequestsPhpFileTest(unittest.TestCase):
    def test_entry_points_under_the_application_path(self):
        for app_path in (APP_PATH, f"{APP_PATH}/", "/Blog"):
            for path in PHP_ALLOWED_UNDER_APP:
                with self.subTest(app_path=app_path, path=path):
                    url = f"https://preprod.example.org{path}"
                    self.assertFalse(requests_php_file(url, app_path))
        for path in PHP_REFUSED_UNDER_APP:
            with self.subTest(path=path):
                self.assertTrue(requests_php_file(f"https://preprod.example.org{path}", APP_PATH))

    def test_front_controller_routes_at_the_root(self):
        for app_path in ("", "/"):
            for path in ("/index.php/node/1", "/index.php/", "/INDEX.PHP/node/1?page=2"):
                with self.subTest(app_path=app_path, path=path):
                    url = f"https://preprod.example.org{path}"
                    self.assertFalse(requests_php_file(url, app_path))
        for path in (
            "/index.php/../x.php",
            "/index.php/./node",
            "/index.php%2F..%2Fx.php",
            "/index.php/uploads/x.php",
            "/wp-login.php/x",
            "/wp-admin/admin-ajax.php/x",
        ):
            with self.subTest(path=path):
                self.assertTrue(requests_php_file(f"https://preprod.example.org{path}"))

    def test_disguised_php_files_are_refused(self):
        for path in (
            "/x.php.jpg",
            "/uploads/x.PHP.png",
            "/x.pht",
            "/x.php%20",
            "/x.php%00",
            "/x.php%2500",
            "/x.php.",
            "/x.php;.jpg",
            "/x.php::$DATA",
            "/x.php.bak/y",
        ):
            with self.subTest(path=path):
                self.assertTrue(requests_php_file(f"https://preprod.example.org{path}"))

    def test_backups_and_other_extensions_are_not_php_files(self):
        for path in (
            "/wp-config.php.bak",
            "/wp-config.php.old",
            "/wp-config.php.orig",
            "/wp-config.php.save",
            "/wp-config.php~",
            "/x.phpx",
            "/x.php_old",
            "/composer.json",
        ):
            with self.subTest(path=path):
                self.assertFalse(requests_php_file(f"https://preprod.example.org{path}"))


class GuardrailsTest(unittest.TestCase):
    def test_solid_ok_is_kept(self):
        result = guard(make_ok())
        self.assertEqual(result["verdict"], "ok")
        self.assertIsNone(result["guardrail"])

    def test_ok_without_evidence_becomes_ok_human(self):
        result = guard(make_ok(evidence=[]))
        self.assertEqual(result["verdict"], "ok_human")
        self.assertIn("preuve", result["guardrail"])

    def test_ok_without_high_confidence_becomes_ok_human(self):
        for confidence in ("medium", "low"):
            with self.subTest(confidence=confidence):
                result = guard(make_ok(confidence=confidence))
                self.assertEqual(result["verdict"], "ok_human")
                self.assertIn("confiance", result["guardrail"])

    def test_other_verdicts_are_not_touched_by_the_evidence_rule(self):
        for verdict in ("ko", "partial", "unknown", "ok_human"):
            with self.subTest(verdict=verdict):
                result = guard(make_finding(verdict=verdict, evidence=[], confidence="low"))
                self.assertEqual(result["verdict"], verdict)
                self.assertIsNone(result["guardrail"])

    def test_guardrail_human_tool(self):
        row = make_row(tools=("Code", HUMAN))
        result = guard(make_ok(), row)
        self.assertEqual(result["verdict"], "ok_human")
        self.assertIn("humaine", result["guardrail"])

    def test_human_tool_leaves_lower_verdicts(self):
        row = make_row(tools=(HUMAN,))
        for verdict in ("ko", "partial", "unknown", "ok_human", "na"):
            with self.subTest(verdict=verdict):
                result = guard(make_finding(verdict=verdict), row)
                self.assertEqual(result["verdict"], verdict)
                self.assertIsNone(result["guardrail"])

    def test_row_without_tools_key(self):
        result = apply_guardrails(make_ok(), {"ref": 79}, followup_done=False)
        self.assertEqual(result["verdict"], "ok")

    def test_guardrail_divergence(self):
        for verdict in ("ok", "ok_human"):
            with self.subTest(verdict=verdict):
                result = guard(make_finding(verdict=verdict, divergence="Corrigé sur develop, pas déployé."))
                self.assertEqual(result["verdict"], "partial")
                self.assertIn("écart", result["guardrail"])

    def test_divergence_does_not_touch_other_verdicts(self):
        for verdict in ("ko", "partial", "unknown", "na"):
            with self.subTest(verdict=verdict):
                result = guard(make_finding(verdict=verdict, divergence="Écart constaté."))
                self.assertEqual(result["verdict"], verdict)
                self.assertIsNone(result["guardrail"])

    def test_empty_divergence_is_not_a_divergence(self):
        for value in (None, "", "  \n"):
            with self.subTest(value=value):
                result = guard(make_ok(divergence=value))
                self.assertEqual(result["verdict"], "ok")
                self.assertIsNone(result["guardrail"])

    def test_guardrail_pending_probes(self):
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        result = guard(make_ok(needs_probes=probes), followup_done=True)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIn("sondes", result["guardrail"])

    def test_pending_probes_before_the_follow_up_are_kept(self):
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        result = guard(make_finding(verdict="ko", needs_probes=probes), followup_done=False)
        self.assertEqual(result["verdict"], "ko")
        self.assertIsNone(result["guardrail"])

    def test_pending_probes_keep_a_failing_verdict_after_the_follow_up(self):
        probes = [{"url": "https://preprod.example.org/", "method": "HEAD"}]
        for verdict in ("ko", "partial"):
            with self.subTest(verdict=verdict):
                result = guard(make_finding(verdict=verdict, needs_probes=probes), followup_done=True)
                self.assertEqual(result["verdict"], verdict)
                self.assertIsNone(result["guardrail"])

    def test_pending_probes_cap_a_passing_verdict_at_unknown_after_the_follow_up(self):
        probes = [{"url": "https://preprod.example.org/", "method": "HEAD"}]
        for verdict in ("ok_human", "ok", "na"):
            with self.subTest(verdict=verdict):
                result = guard(make_finding(verdict=verdict, needs_probes=probes), followup_done=True)
                self.assertEqual(result["verdict"], "unknown")
                self.assertIn("sondes", result["guardrail"])
                self.assertIn(f"{verdict} abaissé à unknown", result["guardrail"])

    def test_unknown_with_pending_probes_is_unchanged(self):
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        result = guard(make_finding(verdict="unknown", needs_probes=probes), followup_done=True)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIsNone(result["guardrail"])

    def test_no_pending_probes_after_the_follow_up(self):
        result = guard(make_ok(needs_probes=[]), followup_done=True)
        self.assertEqual(result["verdict"], "ok")

    def test_rules_chain_in_order(self):
        weak_ok = make_ok(evidence=[], divergence="Écart.")
        result = guard(weak_ok)
        self.assertEqual(result["verdict"], "partial")
        self.assertIn("preuve", result["guardrail"])
        self.assertIn("écart", result["guardrail"])

    def test_probes_cap_comes_after_the_first_three_rules(self):
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        human = make_row(tools=(HUMAN,))
        result = guard(make_ok(needs_probes=probes), human, followup_done=True)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIn("humaine", result["guardrail"])
        self.assertIn("ok_human abaissé à unknown", result["guardrail"])
        result = guard(make_ok(divergence="Écart.", needs_probes=probes), human, followup_done=True)
        self.assertEqual(result["verdict"], "partial")
        self.assertIn("écart", result["guardrail"])
        self.assertNotIn("sondes", result["guardrail"])

    def test_ok_with_a_todo_or_a_human_check_becomes_ok_human(self):
        for key, word in (("todo", "à faire"), ("human_check", "à valider")):
            with self.subTest(key=key):
                result = guard(make_ok(**{key: "Vérifier le menu mobile."}))
                self.assertEqual(result["verdict"], "ok_human")
                self.assertIn(word, result["guardrail"])
                self.assertIn("ok abaissé à ok_human", result["guardrail"])

    def test_blank_todo_and_human_check_do_not_count(self):
        for value in (None, "", "  \n"):
            with self.subTest(value=value):
                result = guard(make_ok(todo=value, human_check=value))
                self.assertEqual(result["verdict"], "ok")
                self.assertIsNone(result["guardrail"])

    def test_todo_and_human_check_do_not_touch_other_verdicts(self):
        for verdict in ("ko", "partial", "unknown", "ok_human", "na"):
            with self.subTest(verdict=verdict):
                finding = make_finding(verdict=verdict, todo="À faire.", human_check="À valider.")
                result = guard(finding)
                self.assertEqual(result["verdict"], verdict)
                self.assertIsNone(result["guardrail"])

    def test_na_without_high_confidence_becomes_unknown(self):
        for confidence in ("medium", "low"):
            with self.subTest(confidence=confidence):
                result = guard(make_finding(verdict="na", confidence=confidence))
                self.assertEqual(result["verdict"], "unknown")
                self.assertIn("confiance", result["guardrail"])
                self.assertIn("na abaissé à unknown", result["guardrail"])

    def test_na_with_high_confidence_is_kept(self):
        result = guard(make_finding(verdict="na", confidence="high", evidence=[]))
        self.assertEqual(result["verdict"], "na")
        self.assertIsNone(result["guardrail"])

    def test_contradiction_rules_come_after_the_first_four(self):
        result = guard(make_ok(todo="À faire.", divergence="Écart."))
        self.assertEqual(result["verdict"], "partial")
        self.assertIn("écart", result["guardrail"])
        self.assertNotIn("à faire", result["guardrail"])
        result = guard(make_ok(todo="À faire.", evidence=[]))
        self.assertEqual(result["verdict"], "ok_human")
        self.assertIn("preuve", result["guardrail"])
        self.assertNotIn("à faire", result["guardrail"])

    def test_pending_probes_come_before_the_contradiction_rules(self):
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        result = guard(make_ok(todo="À faire.", needs_probes=probes), followup_done=True)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIn("sondes", result["guardrail"])
        self.assertNotIn("à faire", result["guardrail"])
        result = guard(
            make_finding(verdict="na", confidence="low", needs_probes=probes), followup_done=True
        )
        self.assertEqual(result["verdict"], "unknown")
        self.assertIn("sondes", result["guardrail"])
        self.assertNotIn("confiance", result["guardrail"])

    def test_returns_a_copy(self):
        finding = make_ok(evidence=[])
        before = copy.deepcopy(finding)
        result = guard(finding)
        self.assertEqual(finding, before)
        self.assertNotIn("guardrail", finding)
        result["todo"] = "changed"
        result["needs_probes"].append("x")
        self.assertEqual(finding, before)

    def test_other_fields_are_kept(self):
        finding = make_ok(confidence="low", human_check="À relire.")
        result = guard(finding)
        for key, value in finding.items():
            if key != "verdict":
                self.assertEqual(result[key], value)

    def test_guardrail_written_by_a_model_is_overwritten(self):
        result = guard(make_ok(guardrail="trust me"))
        self.assertIsNone(result["guardrail"])

    def test_guardrails_never_raise_a_verdict(self):
        rank = {verdict: index for index, verdict in enumerate(VERDICTS)}
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        evidence = make_finding()["evidence"]
        cases = itertools.product(
            VERDICTS,
            ("high", "low"),
            ([], evidence),
            (None, "Écart."),
            (None, "À faire."),
            ([], probes),
            (False, True),
            (("Code",), (HUMAN,)),
        )
        for verdict, confidence, ev, divergence, todo, needs, followup, tools in cases:
            finding = make_finding(
                verdict=verdict,
                confidence=confidence,
                evidence=ev,
                divergence=divergence,
                todo=todo,
                needs_probes=needs,
            )
            result = guard(finding, make_row(tools=tools), followup_done=followup)
            with self.subTest(
                verdict=verdict,
                confidence=confidence,
                evidence=bool(ev),
                divergence=divergence,
                todo=todo,
                probes=bool(needs),
                followup=followup,
                tools=tools,
            ):
                self.assertLessEqual(rank[result["verdict"]], rank[verdict])


class SchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCHEMA_PATH.read_text(encoding="utf-8")
        cls.schema = json.loads(cls.text)
        cls.finding = cls.schema["$defs"]["finding"]

    def test_schema_matches_validator(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["$defs"]["finding"]["required"]), set(REQUIRED_FINDING_KEYS))
        self.assertEqual(schema["$defs"]["finding"]["properties"]["verdict"]["enum"], list(VERDICTS))

    def test_dialect_and_document_shape(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(set(self.schema["required"]), {"lot", "findings"})
        self.assertEqual(self.schema["properties"]["findings"]["items"], {"$ref": "#/$defs/finding"})

    def test_every_property_of_the_finding_is_described(self):
        self.assertEqual(
            set(self.finding["properties"]), set(REQUIRED_FINDING_KEYS) | {"tool_dates"}
        )
        for name, definition in self.finding["properties"].items():
            with self.subTest(name=name):
                self.assertTrue(definition.get("description"))

    def test_descriptions_state_the_guardrails(self):
        properties = self.finding["properties"]
        verdict = properties["verdict"]["description"]
        self.assertIn("todo and human_check must be null", verdict)
        self.assertIn("na needs high confidence", verdict)
        for key in ("todo", "human_check"):
            self.assertIn("an ok with a filled", properties[key]["description"])
        probes = properties["needs_probes"]["description"]
        self.assertIn("unknown", probes)
        self.assertIn("ko and partial are kept", probes)

    def test_confidence_enum(self):
        self.assertEqual(self.finding["properties"]["confidence"]["enum"], list(CONFIDENCES))

    def test_summary_bounds(self):
        summary = self.finding["properties"]["summary"]
        self.assertEqual(summary["type"], "string")
        self.assertEqual(summary["minLength"], 1)
        self.assertEqual(summary["maxLength"], MAX_SUMMARY_LENGTH)

    def test_optional_texts_are_bounded(self):
        for key in ("todo", "human_check", "divergence"):
            with self.subTest(key=key):
                definition = self.finding["properties"][key]
                self.assertEqual(definition["type"], ["string", "null"])
                self.assertEqual(definition["maxLength"], MAX_FIELD_LENGTH)

    def test_ref_is_an_integer(self):
        self.assertEqual(self.finding["properties"]["ref"]["type"], "integer")

    def test_evidence_branches_match_the_validator(self):
        branches = self.schema["$defs"]
        items = self.finding["properties"]["evidence"]["items"]["oneOf"]
        by_type = {}
        for branch in items:
            definition = branches[branch["$ref"].rsplit("/", 1)[1]]
            by_type[definition["properties"]["type"]["const"]] = definition
        self.assertEqual(set(by_type), set(EVIDENCE_REQUIRED))
        for kind, keys in EVIDENCE_REQUIRED.items():
            with self.subTest(kind=kind):
                self.assertEqual(set(by_type[kind]["required"]), {"type", *keys})

    def test_file_line_definition(self):
        line = self.schema["$defs"]["evidence_file"]["properties"]["line"]
        self.assertEqual(line["type"], ["integer", "null"])
        self.assertEqual(line["minimum"], 1)

    def test_probe_definition(self):
        items = self.finding["properties"]["needs_probes"]["items"]
        probe = self.schema["$defs"][items["$ref"].rsplit("/", 1)[1]]
        self.assertEqual(set(probe["required"]), {"url", "method"})
        self.assertEqual(probe["properties"]["method"]["enum"], list(PROBE_METHODS))

    def test_example_is_valid_for_the_validator(self):
        examples = self.finding["examples"]
        self.assertTrue(examples)
        for example in examples:
            with self.subTest(ref=example["ref"]):
                valid, errors = validate_document(
                    {"lot": "front-1", "findings": [example]}, {example["ref"]}
                )
                self.assertEqual(errors, [])
                self.assertEqual(valid, [example])

    def test_no_em_dash(self):
        self.assertNotIn(EM_DASH, self.text)


if __name__ == "__main__":
    unittest.main()
