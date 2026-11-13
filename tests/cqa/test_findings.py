import copy
import itertools
import json
import unittest
from pathlib import Path

from cqa.findings import (
    ACTION_SEGMENTS,
    ACTION_URL_REASON,
    ACTION_VALUES,
    CONFIDENCES,
    EVIDENCE_REQUIRED,
    MAX_FIELD_LENGTH,
    MAX_SCENARIOS,
    MAX_SELECTOR,
    MAX_STEPS,
    MAX_SUMMARY_LENGTH,
    PROBE_METHODS,
    REQUIRED_FINDING_KEYS,
    SCENARIO_PRESS_KEYS,
    SCENARIO_PROFILES,
    SCENARIO_STEP_TYPES,
    TOKEN_PARAMS,
    VERDICTS,
    _SCENARIO_ID,
    apply_guardrails,
    requests_action_url,
    requests_php_file,
    utf16_length,
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


def make_scenario(**overrides):
    scenario = {
        "id": "menu-burger",
        "url": "https://preprod.example.org/",
        "profile": "portrait",
        "steps": [
            {"type": "tap", "selector": "#menu-toggle"},
            {"type": "press", "key": "Escape"},
            {"type": "tab", "count": 3, "shift": True},
            {"type": "select", "selector": "#pays", "value": "FR"},
            {"type": "state", "selector": "#menu"},
            {"type": "capture"},
        ],
    }
    scenario.update(overrides)
    return scenario


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

    def test_needs_scenarios_is_not_a_required_key(self):
        self.assertNotIn("needs_scenarios", REQUIRED_FINDING_KEYS)
        self.assertEqual(len(REQUIRED_FINDING_KEYS), 9)

    def test_scenario_lists_and_limits_copy_the_toolbox(self):
        # Copied as is from gm-audit (STEP_TYPES, PRESS_KEYS, FOLLOWUP_LIMITS): no test links the two repos.
        self.assertEqual(SCENARIO_PROFILES, ("portrait", "paysage"))
        self.assertEqual(SCENARIO_STEP_TYPES, ("tab", "press", "tap", "select", "state", "capture"))
        self.assertEqual(
            SCENARIO_PRESS_KEYS,
            (
                "Escape", "Tab", "Shift+Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                "Home", "End",
            ),
        )
        self.assertEqual((MAX_SCENARIOS, MAX_STEPS, MAX_SELECTOR), (3, 15, 200))
        self.assertEqual(_SCENARIO_ID.pattern, "[a-z0-9-]{1,40}")
        for good in ("a", "menu-burger", "0-9", "a" * 40):
            with self.subTest(good=good):
                self.assertTrue(_SCENARIO_ID.fullmatch(good))
        for bad in ("", "Bad_ID", "a" * 41, "a b", "a:b", "A", "é", "a\n"):
            with self.subTest(bad=bad):
                self.assertFalse(_SCENARIO_ID.fullmatch(bad))


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

    def test_optional_refs_are_not_reported_missing(self):
        document = make_document(make_finding(ref=79))
        valid, errors = validate_document(document, {79, 80}, optional_refs=frozenset({80}))
        self.assertEqual((errors, [item["ref"] for item in valid]), ([], [79]))
        valid, errors = validate_document(document, {79, 80, 81}, optional_refs=frozenset({80}))
        self.assertEqual(errors, ["constat manquant : CQ-81"])

    def test_an_optional_ref_given_by_the_file_is_validated_like_any_other(self):
        document = make_document(make_finding(ref=79), make_finding(ref=80, confidence="sure"))
        valid, errors = validate_document(document, {79, 80}, optional_refs=frozenset({80}))
        self.assertEqual([item["ref"] for item in valid], [79])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("constat CQ-80 : confiance"))

    def test_optional_refs_do_not_widen_the_lot(self):
        document = make_document(make_finding(ref=79), make_finding(ref=999))
        valid, errors = validate_document(document, {79}, optional_refs=frozenset({999}))
        self.assertEqual([item["ref"] for item in valid], [79])
        self.assertEqual(errors, ["constat CQ-999 : Réf hors du lot"])

    def test_a_forbidden_ref_is_an_error_and_never_valid(self):
        document = make_document(make_finding(ref=79), make_finding(ref=80))
        valid, errors = validate_document(
            document, {79, 80}, optional_refs=frozenset({80}), forbidden_refs=frozenset({80})
        )
        self.assertEqual([item["ref"] for item in valid], [79])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("constat CQ-80 : ligne jugée d'après un document"))
        self.assertNotIn(EM_DASH, errors[0])

    def test_a_forbidden_ref_the_file_does_not_give_is_no_error(self):
        valid, errors = validate_document(
            make_document(make_finding(ref=79)),
            {79, 80},
            optional_refs=frozenset({80}),
            forbidden_refs=frozenset({80}),
        )
        self.assertEqual((errors, [item["ref"] for item in valid]), ([], [79]))

    def test_no_forbidden_ref_by_default(self):
        valid, errors = validate_document(
            make_document(make_finding(ref=79), make_finding(ref=80)), {79, 80}
        )
        self.assertEqual((errors, len(valid)), ([], 2))

    def test_optional_refs_default_to_none(self):
        valid, errors = validate_document(make_document(make_finding(ref=79)), {79, 80})
        self.assertEqual(errors, ["constat manquant : CQ-80"])

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
            ["constat CQ-79 : preuve n° 1 : type « log » inconnu (file, page, tool, http, document)"],
        )
        _, errors = validate(make_finding(evidence=[{"path": "a"}]), make_finding(ref=80))
        self.assertEqual(
            errors,
            ["constat CQ-79 : preuve n° 1 : type manquant (file, page, tool, http, document)"],
        )

    def test_evidence_required_keys_per_type(self):
        self.assertEqual(
            EVIDENCE_REQUIRED,
            {
                "file": ("repo", "path"),
                "page": ("url",),
                "tool": ("tool", "ref"),
                "http": ("url", "detail"),
                "document": ("doc", "ref"),
            },
        )
        full = {
            "file": {"type": "file", "repo": "nuxt", "path": "a.vue"},
            "page": {"type": "page", "url": "https://preprod.example.org/"},
            "tool": {"type": "tool", "tool": "a11y", "ref": "summary.json#1"},
            "http": {"type": "http", "url": "https://preprod.example.org/", "detail": "200"},
            "document": {"type": "document", "doc": "d1", "ref": "critères 7.3"},
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

    def test_document_evidence_requires_doc_and_ref(self):
        full = {"type": "document", "doc": "d1", "ref": "critères 7.3"}
        valid, errors = validate(make_finding(evidence=[full]), make_finding(ref=80))
        self.assertEqual((errors, [entry["ref"] for entry in valid]), ([], [79, 80]))
        for key in ("doc", "ref"):
            with self.subTest(key=key):
                item = {name: value for name, value in full.items() if name != key}
                _, errors = validate(make_finding(evidence=[item]), make_finding(ref=80))
                self.assertEqual(
                    errors, [f"constat CQ-79 : preuve n° 1 (document) : « {key} » manquant"]
                )
                _, errors = validate(
                    make_finding(evidence=[{**full, key: ""}]), make_finding(ref=80)
                )
                self.assertEqual(
                    errors,
                    [f"constat CQ-79 : preuve n° 1 (document) : « {key} » doit être un texte non vide"],
                )

    def test_a_finding_marked_as_taken_from_a_document_is_valid(self):
        finding = make_finding(
            evidence=[{"type": "document", "doc": "d1", "ref": "critères 7.3"}],
            source="context_doc",
            in_repo=False,
        )
        valid, errors = validate(finding, make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["source"], "context_doc")

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
        valid, errors = validate(make_finding(tool_dates={}), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["tool_dates"], {})
        for bad in (
            "2026-10-27T21:15:05+00:00",
            "2026-10-27T21:15:05",
            "",
            123,
            "٢٠٢٦-١٠-٢٧T٢١:١٥:٠٥Z",
        ):
            with self.subTest(date=bad):
                valid, errors = validate(make_finding(tool_dates={"crawl": bad}), make_finding(ref=80))
                self.assertEqual([item["ref"] for item in valid], [80])
                self.assertEqual(len(errors), 1)
                self.assertIn("invalide pour l'outil « crawl »", errors[0])
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
                self.assertEqual([item["ref"] for item in valid], [80])
                self.assertEqual(
                    errors, ["constat CQ-79 : « tool_dates » doit être un objet {outil: date}"]
                )

    def test_in_repo_is_optional_and_boolean(self):
        for value in (True, False):
            with self.subTest(value=value):
                valid, errors = validate(make_finding(in_repo=value), make_finding(ref=80))
                self.assertEqual(errors, [])
                self.assertEqual(valid[0]["in_repo"], value)
        valid, errors = validate(make_finding(), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertNotIn("in_repo", valid[0])
        for value in (None, "false", 0, 1, []):
            with self.subTest(value=value):
                valid, errors = validate(make_finding(in_repo=value), make_finding(ref=80))
                self.assertEqual([item["ref"] for item in valid], [80])
                self.assertEqual(
                    errors, ["constat CQ-79 : « in_repo » doit être un booléen (true ou false)"]
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


ORIGIN = "https://preprod.example.org"


def scenario_errors(scenarios):
    """Errors of a document whose finding CQ-79 asks for the scenarios, the finding of CQ-80 being fine."""
    valid, errors = validate(make_finding(needs_scenarios=scenarios), make_finding(ref=80))
    return valid, errors


def only_error(testcase, scenarios):
    valid, errors = scenario_errors(scenarios)
    testcase.assertEqual([item["ref"] for item in valid], [80])
    testcase.assertEqual(len(errors), 1, errors)
    prefix = "constat CQ-79 : "
    testcase.assertTrue(errors[0].startswith(prefix), errors[0])
    return errors[0][len(prefix):]


class NeedsScenariosTest(unittest.TestCase):
    def test_needs_scenarios_is_optional_and_a_valid_one_passes(self):
        valid, errors = validate(make_finding(), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 2)
        self.assertNotIn("needs_scenarios", valid[0])
        scenarios = [make_scenario()]
        valid, errors = validate(make_finding(needs_scenarios=scenarios), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["needs_scenarios"], scenarios)
        valid, errors = validate(make_finding(needs_scenarios=[]), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["needs_scenarios"], [])

    def test_the_step_defaults_and_the_three_scenarios_are_valid(self):
        steps = [
            {"type": "tab"},
            {"type": "tab", "count": 30},
            {"type": "tab", "count": 1, "shift": False},
            {"type": "select", "selector": "#pays", "value": ""},
            {"type": "state", "selector": "x" * MAX_SELECTOR},
        ]
        for key in SCENARIO_PRESS_KEYS:
            steps.append({"type": "press", "key": key})
        scenarios = [
            make_scenario(id=f"s{number}", profile=profile, steps=steps[:MAX_STEPS])
            for number, profile in enumerate(("portrait", "paysage", "portrait"))
        ]
        valid, errors = validate(make_finding(needs_scenarios=scenarios), make_finding(ref=80))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["needs_scenarios"], scenarios)
        fifteen = make_scenario(steps=[{"type": "capture"}] * MAX_STEPS)
        _, errors = scenario_errors([fifteen])
        self.assertEqual(errors, [])

    def test_a_valid_request_is_returned_unchanged_and_is_a_copy(self):
        scenarios = [make_scenario()]
        before = copy.deepcopy(scenarios)
        valid, _ = validate(make_finding(needs_scenarios=scenarios), make_finding(ref=80))
        self.assertEqual(scenarios, before)
        valid[0]["needs_scenarios"][0]["steps"].append({"type": "capture"})
        self.assertEqual(scenarios, before)

    def test_hostile_scenario_requests_are_refused_with_their_reason(self):
        step_tap = {"type": "tap", "selector": "#a"}

        def steps_of(*steps):
            return make_scenario(steps=list(steps))

        cases = {
            "four scenarios": (
                [make_scenario(id=f"s{number}") for number in range(4)],
                "« needs_scenarios » : 4 scénarios (3 au plus)",
            ),
            "sixteen steps": (
                [make_scenario(steps=[step_tap] * 16)],
                "scénario n° 1 : 16 étapes (15 au plus)",
            ),
            "step type eval": (
                [steps_of(step_tap, {"type": "eval", "selector": "#a"})],
                "scénario n° 1 : étape n° 2 : type « eval » inconnu "
                "(tab, press, tap, select, state, capture)",
            ),
            "selector of 201 characters": (
                [steps_of({"type": "tap", "selector": "a" * 201})],
                "scénario n° 1 : étape n° 1 : « selector » dépasse 200 caractères (201)",
            ),
            "value of 201 characters": (
                [steps_of({"type": "select", "selector": "#a", "value": "v" * 201})],
                "scénario n° 1 : étape n° 1 : « value » dépasse 200 caractères (201)",
            ),
            "selector with a line break": (
                [steps_of({"type": "tap", "selector": "#a\n#b"})],
                "scénario n° 1 : étape n° 1 : « selector » contient un caractère de contrôle",
            ),
            "value with a control character": (
                [steps_of({"type": "select", "selector": "#a", "value": "x\x00y"})],
                "scénario n° 1 : étape n° 1 : « value » contient un caractère de contrôle",
            ),
            "Enter key": (
                [steps_of({"type": "press", "key": "Enter"})],
                "scénario n° 1 : étape n° 1 : « key » doit valoir Escape, Tab, Shift+Tab, ArrowUp, "
                "ArrowDown, ArrowLeft, ArrowRight, Home ou End",
            ),
            "key without a name": (
                [steps_of({"type": "press"})],
                "scénario n° 1 : étape n° 1 : « key » manquant",
            ),
            "script key on a step": (
                [steps_of({"type": "tap", "selector": "#a", "script": "alert(1)"})],
                "scénario n° 1 : étape n° 1 : clé « script » non admise pour l'étape tap "
                "(clés admises : selector)",
            ),
            "selector on a capture": (
                [steps_of({"type": "capture", "selector": "#a"})],
                "scénario n° 1 : étape n° 1 : clé « selector » non admise pour l'étape capture "
                "(aucune clé admise)",
            ),
            "script key on a scenario": (
                [make_scenario(script="alert(1)")],
                "scénario n° 1 : clé « script » non admise (id, url, profile, steps)",
            ),
            "uppercase id": (
                [make_scenario(id="Bad_ID")],
                "scénario n° 1 : « id » doit correspondre à [a-z0-9-]{1,40} (« Bad_ID »)",
            ),
            "id of 41 characters": (
                [make_scenario(id="a" * 41)],
                f"scénario n° 1 : « id » doit correspondre à [a-z0-9-]{{1,40}} (« {'a' * 41} »)",
            ),
            "lot prefix in the id": (
                [make_scenario(id="lot:menu")],
                "scénario n° 1 : « id » doit correspondre à [a-z0-9-]{1,40} (« lot:menu »)",
            ),
            "id that is not a text": (
                [make_scenario(id=12)],
                "scénario n° 1 : « id » doit correspondre à [a-z0-9-]{1,40} (12)",
            ),
            "unknown profile": (
                [make_scenario(profile="tablette")],
                "scénario n° 1 : « profile » doit valoir portrait ou paysage",
            ),
            "count of 31": (
                [steps_of({"type": "tab", "count": 31})],
                "scénario n° 1 : étape n° 1 : « count » doit être un entier de 1 à 30",
            ),
            "count of 0": (
                [steps_of({"type": "tab", "count": 0})],
                "scénario n° 1 : étape n° 1 : « count » doit être un entier de 1 à 30",
            ),
            "count as a text": (
                [steps_of({"type": "tab", "count": "3"})],
                "scénario n° 1 : étape n° 1 : « count » doit être un entier de 1 à 30",
            ),
            "count as a boolean": (
                [steps_of({"type": "tab", "count": True})],
                "scénario n° 1 : étape n° 1 : « count » doit être un entier de 1 à 30",
            ),
            "count as a float": (
                [steps_of({"type": "tab", "count": 2.0})],
                "scénario n° 1 : étape n° 1 : « count » doit être un entier de 1 à 30",
            ),
            "shift as a text": (
                [steps_of({"type": "tab", "shift": "oui"})],
                "scénario n° 1 : étape n° 1 : « shift » doit être un booléen (true ou false)",
            ),
        }
        for name, (scenarios, expected) in cases.items():
            with self.subTest(name=name):
                problem = only_error(self, scenarios)
                self.assertEqual(problem, expected)
                self.assertNotIn(EM_DASH, problem)
        for url in (
            "https://preprod.example.org/wp-content/uploads/x.php",
            "https://preprod.example.org/x.PHP?y=1",
            "https://preprod.example.org/blog/index.php",
        ):
            with self.subTest(url=url):
                problem = only_error(self, [make_scenario(url=url)])
                self.assertTrue(
                    problem.startswith(f"scénario n° 1 : {PHP_FILE_ERROR} (seuls /index.php"), problem
                )
        problem = only_error(self, [make_scenario(url="https://preprod.example.org/" + "a" * 2000)])
        self.assertEqual(problem, "scénario n° 1 : « url » dépasse 2000 caractères (2028)")

    def test_the_length_of_the_url_is_counted_in_utf16_units_like_the_tool(self):
        # 987 emoji: 1015 code points but 2002 UTF-16 units, which the tool refuses.
        head = "https://preprod.example.org/"
        problem = only_error(self, [make_scenario(url=head + "\U0001F600" * 987)])
        self.assertEqual(problem, "scénario n° 1 : « url » dépasse 2000 caractères (2002)")
        problem = only_error(self, [make_scenario(url=head + "\U0001F600" * 1000)])
        self.assertEqual(problem, "scénario n° 1 : « url » dépasse 2000 caractères (2028)")
        valid, errors = scenario_errors([make_scenario(url=head + "\U0001F600" * 986)])
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 2)
        self.assertEqual(utf16_length("a\U0001F600é"), 4)

    def test_the_php_rule_follows_the_path_of_the_application(self):
        def errors_for(paths, app_path):
            scenarios = [make_scenario(url=f"{ORIGIN}{path}") for path in paths]
            document = make_document(make_finding(needs_scenarios=scenarios[:1]), make_finding(ref=80))
            return validate_document(document, LOT_REFS, app_path=app_path)[1]

        self.assertEqual(errors_for(["/blog/index.php"], APP_PATH), [])
        errors = errors_for(["/index.php"], APP_PATH)
        self.assertEqual(len(errors), 1)
        self.assertIn(PHP_FILE_ERROR, errors[0])
        self.assertIn("/blog/index.php", errors[0])

    def test_hostile_selectors_are_data_that_is_never_changed(self):
        selectors = [
            "a'); fetch('/x'); ('",
            "a\"); location.href='/y'; (\"",
            "xpath=//a",
            "a >> text=x",
            "text=Menu",
            "#bouton-menu >> nth=0",
            "</script><script>alert(1)</script>",
            "${process.exit()}",
            "`rm -rf /`",
            "javascript:alert(1)",
            "é",
        ]
        steps = [{"type": "tap", "selector": selector} for selector in selectors[:MAX_STEPS]]
        scenarios = [make_scenario(steps=steps)]
        document = make_document(make_finding(needs_scenarios=scenarios), make_finding(ref=80))
        before = copy.deepcopy(document)
        valid, errors = validate_document(document, LOT_REFS)
        self.assertEqual(errors, [])
        self.assertEqual(document, before)
        self.assertEqual(valid[0]["needs_scenarios"], scenarios)

    def test_malformed_requests_are_refused_and_never_raise(self):
        huge = {"type": "tap", "selector": "#a"}
        shapes = [
            None,
            "menu",
            12,
            {},
            {"id": "a"},
            ["menu"],
            [None],
            [[]],
            [{}],
            [make_scenario(id=None)],
            [make_scenario(id="")],
            [make_scenario(id=["a"])],
            [make_scenario(url=None)],
            [make_scenario(url="")],
            [make_scenario(url="   ")],
            [make_scenario(url=["https://preprod.example.org/"])],
            [make_scenario(url="https://preprod.example.org/" + "a" * 2000)],
            [make_scenario(profile=None)],
            [make_scenario(profile="PORTRAIT")],
            [make_scenario(profile=["portrait"])],
            [make_scenario(steps=None)],
            [make_scenario(steps=[])],
            [make_scenario(steps="tap #a")],
            [make_scenario(steps={"type": "tap", "selector": "#a"})],
            [make_scenario(steps=[None])],
            [make_scenario(steps=["tap"])],
            [make_scenario(steps=[[]])],
            [make_scenario(steps=[{}])],
            [make_scenario(steps=[{"type": None}])],
            [make_scenario(steps=[{"type": ["tap"]}])],
            [make_scenario(steps=[{"type": "TAP", "selector": "#a"}])],
            [make_scenario(steps=[{"type": "navigate", "url": "/user/logout"}])],
            [make_scenario(steps=[{"type": "tap"}])],
            [make_scenario(steps=[{"type": "tap", "selector": None}])],
            [make_scenario(steps=[{"type": "tap", "selector": ["#a"]}])],
            [make_scenario(steps=[{"type": "tap", "selector": ""}])],
            [make_scenario(steps=[{"type": "tap", "selector": "   "}])],
            [make_scenario(steps=[{"type": "tap", "selector": "#a\u2028"}])],
            [make_scenario(steps=[{"type": "tap", "selector": "#a\u2029"}])],
            [make_scenario(steps=[{"type": "tap", "selector": "#a\x7f"}])],
            [make_scenario(steps=[{"type": "tap", "selector": "#a\x85"}])],
            [make_scenario(steps=[{"type": "tap", "selector": "#a\r"}])],
            [make_scenario(steps=[{"type": "tap", "selector": "#a", "value": "x"}])],
            [make_scenario(steps=[{"type": "select", "selector": "#a"}])],
            [make_scenario(steps=[{"type": "select", "selector": "#a", "value": None}])],
            [make_scenario(steps=[{"type": "select", "selector": "#a", "value": 3}])],
            [make_scenario(steps=[{"type": "select", "value": "x"}])],
            [make_scenario(steps=[{"type": "state"}])],
            [make_scenario(steps=[{"type": "press", "key": "enter"}])],
            [make_scenario(steps=[{"type": "press", "key": ["Escape"]}])],
            [make_scenario(steps=[{"type": "press", "key": "Escape", "selector": "#a"}])],
            [make_scenario(steps=[{"type": "tab", "count": -1}])],
            [make_scenario(steps=[{"type": "tab", "count": None}])],
            [make_scenario(steps=[{"type": "tab", "shift": None}])],
            [make_scenario(steps=[{"type": "tab", "shift": 1}])],
            [make_scenario(steps=[{"type": "tab", "key": "Tab"}])],
            [make_scenario(steps=[huge] * (MAX_STEPS + 1))],
            [make_scenario(steps=[huge] * 1000)],
            [make_scenario(), make_scenario(id="b"), make_scenario(id="c"), make_scenario(id="d")],
            [make_scenario(), make_scenario()],
            [make_scenario(extra=1)],
        ]
        for shape in shapes:
            with self.subTest(shape=repr(shape)[:80]):
                valid, errors = scenario_errors(shape)
                self.assertEqual([item["ref"] for item in valid], [80])
                self.assertTrue(errors)
                for message in errors:
                    self.assertTrue(message.startswith("constat CQ-79 : "), message)
                    self.assertNotIn(EM_DASH, message)
                    self.assertLessEqual(len(message), 400)

    def test_a_huge_request_is_reported_without_flooding_the_errors(self):
        # The count is reported, and only the steps and scenarios that the caps allow are examined.
        steps = [{"type": "tap", "selector": "#a\n"}] * 5000
        _, errors = scenario_errors([make_scenario(steps=steps)])
        self.assertEqual(errors[0], "constat CQ-79 : scénario n° 1 : 5000 étapes (15 au plus)")
        self.assertEqual(len(errors), 1 + MAX_STEPS)
        self.assertIn(f"étape n° {MAX_STEPS} : ", errors[-1])
        scenarios = [make_scenario(id=f"s{number}") for number in range(100)]
        _, errors = scenario_errors(scenarios)
        self.assertEqual(errors, ["constat CQ-79 : « needs_scenarios » : 100 scénarios (3 au plus)"])
        scenarios = [make_scenario(id="Bad_ID") for number in range(100)]
        _, errors = scenario_errors(scenarios)
        self.assertEqual(len(errors), 1 + MAX_SCENARIOS)

    def test_every_problem_of_the_request_is_reported_in_one_pass(self):
        scenarios = [
            make_scenario(id="Bad_ID", profile="tablette", steps=[{"type": "eval"}, {"type": "capture", "x": 1}]),
            make_scenario(id="ok-one", steps=[{"type": "press", "key": "Enter"}]),
        ]
        _, errors = scenario_errors(scenarios)
        self.assertEqual(len(errors), 5, errors)
        joined = "\n".join(errors)
        for needle in (
            "scénario n° 1 : « id »",
            "scénario n° 1 : « profile »",
            "scénario n° 1 : étape n° 1 : type « eval » inconnu",
            "scénario n° 1 : étape n° 2 : clé « x » non admise",
            "scénario n° 2 : étape n° 1 : « key » doit valoir",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, joined)

    def test_the_other_findings_of_the_document_are_not_affected(self):
        valid, errors = validate(
            make_finding(needs_scenarios=[make_scenario(id="Bad_ID")]),
            make_finding(ref=80, needs_scenarios=[make_scenario(id="ok")]),
        )
        self.assertEqual([item["ref"] for item in valid], [80])
        self.assertEqual(len(errors), 1)

    def test_duplicate_scenario_ids_invalidate_every_holder(self):
        first = make_scenario(id="menu-burger")
        second = make_scenario(id="menu-burger", profile="paysage")
        other = make_scenario(id="menu-footer")
        valid, errors = validate(
            make_finding(ref=79, needs_scenarios=[first]),
            make_finding(ref=80, needs_scenarios=[second, other]),
            make_finding(ref=81, needs_scenarios=[make_scenario(id="menu-lang")]),
            lot_refs={79, 80, 81},
        )
        self.assertEqual([item["ref"] for item in valid], [81])
        self.assertEqual(errors, ["identifiant de scénario en double : menu-burger (CQ-79, CQ-80)"])

    def test_duplicate_scenario_ids_are_reported_once_per_id_and_in_every_finding(self):
        valid, errors = validate(
            make_finding(ref=79, needs_scenarios=[make_scenario(id="a"), make_scenario(id="b")]),
            make_finding(ref=80, needs_scenarios=[make_scenario(id="b"), make_scenario(id="a")]),
            make_finding(ref=81, needs_scenarios=[make_scenario(id="a")]),
            lot_refs={79, 80, 81},
        )
        self.assertEqual(valid, [])
        self.assertEqual(
            errors,
            [
                "identifiant de scénario en double : a (CQ-79, CQ-80, CQ-81)",
                "identifiant de scénario en double : b (CQ-79, CQ-80)",
            ],
        )

    def test_an_id_repeated_in_one_finding_invalidates_it(self):
        scenarios = [make_scenario(id="menu"), make_scenario(id="other"), make_scenario(id="menu")]
        problem = only_error(self, scenarios)
        self.assertEqual(
            problem, "scénario n° 3 : « id » « menu » déjà pris par le scénario n° 1"
        )

    def test_a_clash_is_counted_between_findings_of_the_lot_only(self):
        # A finding outside the lot is dropped anyway: its ids must not invalidate a good finding.
        valid, errors = validate(
            make_finding(ref=79, needs_scenarios=[make_scenario(id="menu")]),
            make_finding(ref=999, needs_scenarios=[make_scenario(id="menu")]),
            lot_refs={79, 80},
        )
        self.assertEqual([item["ref"] for item in valid], [79])
        self.assertEqual(errors[0], "constat CQ-999 : Réf hors du lot")
        self.assertNotIn("identifiant de scénario en double", "\n".join(errors))

    def test_an_invalid_id_is_not_reported_as_a_clash(self):
        _, errors = validate(
            make_finding(ref=79, needs_scenarios=[make_scenario(id="Bad_ID")]),
            make_finding(ref=80, needs_scenarios=[make_scenario(id="Bad_ID")]),
        )
        self.assertEqual(len(errors), 2)
        self.assertNotIn("identifiant de scénario en double", "\n".join(errors))

    def test_a_clash_with_a_finding_invalid_for_another_reason_is_reported(self):
        valid, errors = validate(
            make_finding(ref=79, summary="", needs_scenarios=[make_scenario(id="menu")]),
            make_finding(ref=80, needs_scenarios=[make_scenario(id="menu")]),
        )
        self.assertEqual(valid, [])
        self.assertIn("identifiant de scénario en double : menu (CQ-79, CQ-80)", errors)

    def test_the_same_id_in_two_documents_is_not_a_clash(self):
        for lot in ("front-1", "front-2"):
            document = make_document(
                make_finding(needs_scenarios=[make_scenario(id="menu")]), make_finding(ref=80), lot=lot
            )
            valid, errors = validate_document(document, LOT_REFS)
            self.assertEqual(errors, [])
            self.assertEqual(len(valid), 2)

    def test_scenarios_do_not_change_the_probe_rules(self):
        probes = [{"url": "https://preprod.example.org/", "method": "POST"}]
        valid, errors = validate(
            make_finding(needs_probes=probes, needs_scenarios=[make_scenario()]), make_finding(ref=80)
        )
        self.assertEqual(errors, ["constat CQ-79 : sonde n° 1 : « method » doit valoir GET ou HEAD"])
        self.assertEqual([item["ref"] for item in valid], [80])


class RequestsActionUrlTest(unittest.TestCase):
    """The mirror of ACTION_SEGMENTS, ACTION_VALUES, TOKEN_PARAMS and isActionUrl of gm-audit."""

    def test_the_closed_lists_copy_the_toolbox(self):
        self.assertEqual(
            ACTION_SEGMENTS,
            (
                "logout", "signout", "sign-out", "deconnexion", "deconnecter", "delete", "remove",
                "trash", "publish", "unpublish", "unsubscribe", "desinscription", "cron",
                "clear-cache", "flush",
            ),
        )
        self.assertEqual(
            ACTION_VALUES, ("trash", "delete", "remove", "logout", "publish", "unpublish", "clear")
        )
        self.assertEqual(
            TOKEN_PARAMS, ("token", "_token", "csrf", "nonce", "_wpnonce", "form_token", "key")
        )
        self.assertEqual(ACTION_URL_REASON, "URL d'action refusée")

    def test_action_urls_are_refused(self):
        # Every word of the lists, as a whole segment at any depth, as a value of action, every token.
        for word in ACTION_SEGMENTS:
            for path in (f"/{word}", f"/fr/{word}", f"/a/b/{word}/c", f"/{word}/", f"/{word.upper()}"):
                with self.subTest(path=path):
                    self.assertTrue(requests_action_url(f"{ORIGIN}{path}"))
        for value in ACTION_VALUES:
            for query in (f"action={value}", f"a=1&action={value}&b=2", f"ACTION={value.upper()}"):
                with self.subTest(query=query):
                    self.assertTrue(requests_action_url(f"{ORIGIN}/page?{query}"))
        for name in TOKEN_PARAMS:
            for query in (f"{name}=abc", f"{name}=", name, f"a=1&{name.upper()}=x"):
                with self.subTest(query=query):
                    self.assertTrue(requests_action_url(f"{ORIGIN}/page?{query}"))
        # The path is decoded the way a server decodes it before it routes it.
        for path in (
            "/user/logout",
            "/fr/user/logout",
            "/user/%6cogout",
            "/user/%4C%4fGOUT",
            "/user%2flogout",
            "/user%2Flogout",
            "/user%5clogout",
            "/user/./logout",
            "/user/x/../logout",
            "/user/%2e/logout",
        ):
            with self.subTest(path=path):
                self.assertTrue(requests_action_url(f"{ORIGIN}{path}"))
        for query in (
            "action=%74rash", "%61ction=trash", "%5ftoken=1", "form%5Ftoken=1", "action=tr%61sh",
        ):
            with self.subTest(query=query):
                self.assertTrue(requests_action_url(f"{ORIGIN}/page?{query}"))
        self.assertTrue(requests_action_url(f"{ORIGIN}/actions.html?action=trash"))
        self.assertTrue(requests_action_url(f"{ORIGIN}/?action=trash#top"))

    def test_a_word_inside_another_word_is_not_an_action(self):
        for url in (
            f"{ORIGIN}/",
            f"{ORIGIN}",
            f"{ORIGIN}/contact",
            f"{ORIGIN}/produits/delete-pro-fr",
            f"{ORIGIN}/blog?action-figure=1",
            f"{ORIGIN}/tag/trash-talk",
            f"{ORIGIN}/cron-job-offers",
            f"{ORIGIN}/flushing",
            f"{ORIGIN}/logout-faq/removed",
            f"{ORIGIN}/actions.html",
            f"{ORIGIN}/logout.php",
            f"{ORIGIN}/logout;x=1",
            f"{ORIGIN}/page?action=view",
            f"{ORIGIN}/page?action=",
            f"{ORIGIN}/page?action",
            f"{ORIGIN}/page?action=trashcan",
            f"{ORIGIN}/page?actions=trash",
            f"{ORIGIN}/page?keyword=a&tokens=b&mykey=c&nonces=d",
            f"{ORIGIN}/page?view=logout",
            f"{ORIGIN}/page?q=delete&page=2#nonce",
            f"{ORIGIN}/page#x?token=1",
            f"{ORIGIN}/produits?type=publish",
            f"{ORIGIN}/%C3%A9t%C3%A9",
            f"{ORIGIN}/100%",
            f"{ORIGIN}/%zz/%",
            f"{ORIGIN}/page?a=1?token=2",
            f"{ORIGIN}/page?action%3Dtrash",
            f"{ORIGIN}/page?q=%2Btoken",
            f"{ORIGIN}/%256cogout",
        ):
            with self.subTest(url=url):
                self.assertFalse(requests_action_url(url))

    def test_the_decoding_follows_the_url_parser_of_the_tool(self):
        # new URL() drops tabs and line breaks, trims, and reads a backslash as a slash.
        for url in (
            f"{ORIGIN}\\user\\logout",
            f"{ORIGIN}/user\\logout",
            f"{ORIGIN}/user/log\tout",
            f"{ORIGIN}/user/log\nout",
            f"{ORIGIN}/user/log\r\nout",
            f"  {ORIGIN}/user/logout",
            f"{ORIGIN}/user/logout  ",
            f"\x00{ORIGIN}/user/logout",
            f"{ORIGIN}/page?to\tken=1",
            f"{ORIGIN}/page?action=tra\nsh",
            f"{ORIGIN}/page?a+b=1&action=trash",
            f"{ORIGIN}/page?token",
            f"{ORIGIN}/page?&&token=1",
            f"{ORIGIN}/page?x=1;token=2&token=3",
            "/user/logout",
            "//other.example.net/user/logout",
            "https://other.example.net/user/logout",
            f"{ORIGIN}/%C3%A9/logout",
        ):
            with self.subTest(url=url):
                self.assertTrue(requests_action_url(url))
        # A semicolon is not a separator of parameters (it was one in old versions of the standard library).
        self.assertFalse(requests_action_url(f"{ORIGIN}/page?x=1;token=2"))
        # The fragment never goes to the server.
        self.assertFalse(requests_action_url(f"{ORIGIN}/page#/user/logout"))
        # A plus sign is a space in a query, and %2B stays a plus sign.
        for query in ("to+ken=1", "+token=1", "%2Btoken=1"):
            with self.subTest(query=query):
                self.assertFalse(requests_action_url(f"{ORIGIN}/page?{query}"))

    def test_a_dot_segment_is_not_resolved_so_the_mirror_is_never_laxer_than_the_tool(self):
        # The tool resolves "/logout/../x" to "/x" and lets it pass; the mirror refuses it, which is harmless.
        for path in ("/logout/../x", "/logout/%2e%2e/x", "/a/b/../../logout"):
            with self.subTest(path=path):
                self.assertTrue(requests_action_url(f"{ORIGIN}{path}"))

    def test_anything_that_is_not_a_url_is_left_to_the_other_checks(self):
        for value in (None, 12, [], {}, b"/logout", "", "http://[::1/logout", "not a url"):
            with self.subTest(value=value):
                self.assertFalse(requests_action_url(value))


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

    def test_guardrail_4_covers_scenarios_and_probes(self):
        probes = [{"url": "https://preprod.example.org/", "method": "GET"}]
        scenarios = [make_scenario()]
        pending = {
            "scenarios only": {"needs_scenarios": scenarios},
            "probes only": {"needs_probes": probes},
            "both": {"needs_probes": probes, "needs_scenarios": scenarios},
        }
        for name, needs in pending.items():
            for verdict in ("ok_human", "ok", "na"):
                with self.subTest(name=name, verdict=verdict):
                    result = guard(make_finding(verdict=verdict, **needs), followup_done=True)
                    self.assertEqual(result["verdict"], "unknown")
                    self.assertEqual(
                        result["guardrail"],
                        f"sondes ou scénarios en attente après la relance : {verdict} abaissé à unknown",
                    )
            for verdict in ("ko", "partial", "unknown"):
                with self.subTest(name=name, verdict=verdict, kept=True):
                    result = guard(make_finding(verdict=verdict, **needs), followup_done=True)
                    self.assertEqual(result["verdict"], verdict)
                    self.assertIsNone(result["guardrail"])
            with self.subTest(name=name, before_the_follow_up=True):
                result = guard(make_ok(**needs), followup_done=False)
                self.assertEqual(result["verdict"], "ok")
                self.assertIsNone(result["guardrail"])

    def test_answered_scenarios_do_not_lower_a_verdict(self):
        for needs in ({"needs_scenarios": []}, {}):
            with self.subTest(needs=needs):
                result = guard(make_ok(**needs), followup_done=True)
                self.assertEqual(result["verdict"], "ok")
                self.assertIsNone(result["guardrail"])

    def test_pending_scenarios_come_before_the_contradiction_rules(self):
        scenarios = [make_scenario()]
        result = guard(make_ok(todo="À faire.", needs_scenarios=scenarios), followup_done=True)
        self.assertEqual(result["verdict"], "unknown")
        self.assertIn("scénarios", result["guardrail"])
        self.assertNotIn("à faire", result["guardrail"])
        result = guard(make_ok(divergence="Écart.", needs_scenarios=scenarios), followup_done=True)
        self.assertEqual(result["verdict"], "partial")
        self.assertNotIn("scénarios", result["guardrail"])

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
            ([], [make_scenario()]),
            (False, True),
            (("Code",), (HUMAN,)),
        )
        for verdict, confidence, ev, divergence, todo, needs, scenarios, followup, tools in cases:
            finding = make_finding(
                verdict=verdict,
                confidence=confidence,
                evidence=ev,
                divergence=divergence,
                todo=todo,
                needs_probes=needs,
                needs_scenarios=scenarios,
            )
            result = guard(finding, make_row(tools=tools), followup_done=followup)
            with self.subTest(
                verdict=verdict,
                confidence=confidence,
                evidence=bool(ev),
                divergence=divergence,
                todo=todo,
                probes=bool(needs),
                scenarios=bool(scenarios),
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
            set(self.finding["properties"]),
            set(REQUIRED_FINDING_KEYS) | {"needs_scenarios", "tool_dates", "in_repo", "source"},
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
        for key in ("needs_probes", "needs_scenarios"):
            with self.subTest(key=key):
                description = properties[key]["description"]
                self.assertIn("unknown", description)
                self.assertIn("ko and partial are kept", description)

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
