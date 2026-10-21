import copy
import unittest
from datetime import date

from cqa.comments import (
    CONFIDENCE_LABELS,
    VERDICT_LABELS,
    format_comment,
    format_excluded_comment,
    format_page_summary,
    parse_verdict,
    to_rich_text,
)
from cqa.secrets import scan_text

EM_DASH = chr(0x2014)
RUN_DATE = date(2026, 10, 20)
LEAD = "Côme D"
VERSION = "0.9.0"
SHA_NUXT = "3f2a1c9" + "e" * 33
SHA_LARAVEL = "8b1d4e2" + "f" * 33
REPO_NUXT = {
    "name": "nuxt",
    "web_url": "https://gitlab.example.org/acme/front",
    "audited_sha": SHA_NUXT,
}
REPO_LARAVEL = {
    "name": "laravel",
    "web_url": "https://gitlab.example.org/acme/back",
    "audited_sha": SHA_LARAVEL,
}
ROW_79 = {"ref": 79, "title": "Ordre de tabulation logique"}
BLOB_NUXT = f"https://gitlab.example.org/acme/front/-/blob/{SHA_NUXT}"
HEADER = "Audit PRE-MEL · 20/10/2026 · lancé par Côme D"
FOOTER = "Réf CQ-79 · develop@3f2a1c9 · gm:checklist-quality-audit 0.9.0"

F_KO = {
    "ref": 79,
    "verdict": "ko",
    "confidence": "high",
    "summary": (
        "3 éléments ont un tabindex positif : l'ordre de tabulation saute le menu principal."
    ),
    "evidence": [
        {"type": "file", "repo": "nuxt", "path": "components/AppHeader.vue", "line": 42},
        {
            "type": "page",
            "url": "https://preprod.example.org/actualites/x",
            "detail": "parcours au clavier",
        },
        {"type": "tool", "tool": "a11y", "ref": "a11y/summary.json#home/3"},
    ],
    "todo": "Supprimer les tabindex positifs et suivre l'ordre du DOM.",
    "human_check": None,
    "divergence": None,
    "needs_probes": [],
}

EXPECTED_KO_TEXT = "\n".join(
    [
        HEADER,
        "Verdict : À traiter (confiance haute)",
        "Constat : 3 éléments ont un tabindex positif : l'ordre de tabulation saute le menu"
        " principal.",
        "Preuves :",
        f"- components/AppHeader.vue:42 ({BLOB_NUXT}/components/AppHeader.vue#L42)",
        "- https://preprod.example.org/actualites/x : parcours au clavier",
        "- a11y : a11y/summary.json#home/3",
        "À faire : Supprimer les tabindex positifs et suivre l'ordre du DOM.",
        FOOTER,
    ]
)

PAGE_SUMMARY = {
    "by_status_after": {
        "Ok / Recetté": 40,
        "À traiter": 12,
        "Non applicable": 8,
        "Mise en place partielle": 3,
        "Mis en place": 5,
        "En standby": 0,
    },
    "blocking_non_conform": [79, 81],
    "unknown": 4,
    "human_overrides": [
        {"ref": 12, "before": "Ok / Recetté", "after": "À traiter"},
        {"ref": 14, "before": "Mis en place", "after": "Mise en place partielle"},
    ],
    "deferred": 6,
    "missing_in_copy": [{"ref": 3, "title": "Titre absent de la copie"}],
}

EXPECTED_PAGE_TEXT = "\n".join(
    [
        HEADER,
        "Décompte par statut :",
        "- À traiter : 12",
        "- Mise en place partielle : 3",
        "- Mis en place : 5",
        "- Ok / Recetté : 40",
        "- Non applicable : 8",
        "Lignes bloquantes non conformes (2) : CQ-79, CQ-81",
        "Lignes non évaluées : 4",
        "Statuts posés par un humain et contredits par l'audit (2) : CQ-12, CQ-14",
        "Lignes reportées à J0 / J+7 : 6",
        "Lignes du modèle absentes de la copie : 1",
    ]
)


def finding(**changes):
    return {**F_KO, **changes}


def page_evidence(number, detail="constat"):
    return {
        "type": "page",
        "url": f"https://preprod.example.org/page-{number}",
        "detail": detail,
    }


def comment(item, repos=None, lead=LEAD):
    return format_comment(
        finding=item,
        row=ROW_79,
        run_date=RUN_DATE,
        lead_option=lead,
        repos=[REPO_NUXT] if repos is None else repos,
        plugin_version=VERSION,
    )


def excluded(verdict="na", detail="tâche propre à Wordpress, projet Laravel + Nuxt", lead=LEAD):
    return format_excluded_comment(
        row=ROW_79,
        verdict=verdict,
        detail=detail,
        run_date=RUN_DATE,
        lead_option=lead,
        repos=[REPO_NUXT],
        plugin_version=VERSION,
    )


def page_summary(summary=PAGE_SUMMARY, lead=LEAD):
    return format_page_summary(summary=summary, run_date=RUN_DATE, lead_option=lead)


def contents(rich_text):
    return [item["text"]["content"] for item in rich_text]


def links(rich_text):
    return [item["text"]["link"]["url"] for item in rich_text if "link" in item["text"]]


class LabelsTest(unittest.TestCase):
    def test_verdict_labels(self):
        self.assertEqual(
            VERDICT_LABELS,
            {
                "ok": "Ok / Recetté",
                "ok_human": "Mis en place, à valider",
                "partial": "Mise en place partielle",
                "ko": "À traiter",
                "na": "Non applicable",
                "unknown": "Non évaluable",
            },
        )

    def test_confidence_labels(self):
        self.assertEqual(CONFIDENCE_LABELS, {"high": "haute", "medium": "moyenne", "low": "basse"})


class FormatCommentTest(unittest.TestCase):
    def test_exact_comment(self):
        self.assertEqual(
            format_comment(
                finding=F_KO,
                row=ROW_79,
                run_date=date(2026, 10, 20),
                lead_option="Côme D",
                repos=[REPO_NUXT],
                plugin_version="0.9.0",
            ),
            EXPECTED_KO_TEXT,
        )

    def test_verdict_line_per_confidence(self):
        for confidence, label in (("high", "haute"), ("medium", "moyenne"), ("low", "basse")):
            with self.subTest(confidence=confidence):
                lines = comment(finding(confidence=confidence)).split("\n")
                self.assertEqual(lines[1], f"Verdict : À traiter (confiance {label})")

    def test_optional_lines_follow_the_template_order(self):
        text = comment(
            finding(
                todo="Corriger.",
                human_check="Vérifier au lecteur d'écran.",
                divergence="Le correctif est absent de la préprod.",
            )
        )
        self.assertEqual(
            text.split("\n")[-4:],
            [
                "À faire : Corriger.",
                "À valider : Vérifier au lecteur d'écran.",
                "Écart code / préprod : Le correctif est absent de la préprod.",
                FOOTER,
            ],
        )

    def test_empty_optional_fields_leave_no_line(self):
        for empty in (None, "", "  "):
            with self.subTest(empty=empty):
                text = comment(finding(todo=empty, human_check=empty, divergence=empty))
                for label in ("À faire", "À valider", "Écart code / préprod"):
                    self.assertNotIn(label, text)

    def test_missing_optional_keys_are_tolerated(self):
        minimal = {key: F_KO[key] for key in ("ref", "verdict", "confidence", "summary")}
        self.assertEqual(
            comment(minimal).split("\n"),
            [
                HEADER,
                "Verdict : À traiter (confiance haute)",
                f"Constat : {F_KO['summary']}",
                FOOTER,
            ],
        )

    def test_no_evidence_leaves_no_evidence_block(self):
        self.assertNotIn("Preuves", comment(finding(evidence=[])))

    def test_file_evidence_without_line_has_no_anchor(self):
        evidence = [{"type": "file", "repo": "nuxt", "path": "nuxt.config.ts"}]
        text = comment(finding(evidence=evidence))
        self.assertIn(f"\n- nuxt.config.ts ({BLOB_NUXT}/nuxt.config.ts)\n", text)
        self.assertNotIn("#L", text)

    def test_file_evidence_links_to_its_own_repo(self):
        evidence = [{"type": "file", "repo": "laravel", "path": "app/Http/Kernel.php", "line": 7}]
        text = comment(finding(evidence=evidence), repos=[REPO_NUXT, REPO_LARAVEL])
        blob = f"https://gitlab.example.org/acme/back/-/blob/{SHA_LARAVEL}"
        self.assertIn(f"\n- app/Http/Kernel.php:7 ({blob}/app/Http/Kernel.php#L7)\n", text)

    def test_file_evidence_of_an_unknown_repo_has_no_link(self):
        evidence = [{"type": "file", "repo": "ghost", "path": "a.vue", "line": 3}]
        text = comment(finding(evidence=evidence))
        self.assertIn("\n- a.vue:3\n", text)
        self.assertNotIn("/-/blob/", text)

    def test_page_evidence_without_detail_is_the_url(self):
        evidence = [{"type": "page", "url": "https://preprod.example.org/contact"}]
        self.assertIn(
            "\n- https://preprod.example.org/contact\n", comment(finding(evidence=evidence))
        )

    def test_http_evidence(self):
        evidence = [
            {"type": "http", "url": "https://preprod.example.org/.env", "detail": "404 attendu"}
        ]
        self.assertIn(
            "\n- https://preprod.example.org/.env : 404 attendu\n",
            comment(finding(evidence=evidence)),
        )

    def test_unknown_evidence_type_is_refused(self):
        with self.assertRaises(ValueError):
            comment(finding(evidence=[{"type": "video", "url": "https://example.org/"}]))

    def test_two_repos_footer(self):
        text = comment(F_KO, repos=[REPO_NUXT, REPO_LARAVEL])
        self.assertEqual(
            text.split("\n")[-1],
            "Réf CQ-79 · nuxt@3f2a1c9, laravel@8b1d4e2 · gm:checklist-quality-audit 0.9.0",
        )

    def test_five_evidences_are_all_shown(self):
        text = comment(finding(evidence=[page_evidence(n) for n in range(5)]))
        self.assertEqual(text.count("\n- "), 5)
        self.assertNotIn("autre", text)

    def test_evidences_past_five_are_replaced_by_a_count(self):
        text = comment(finding(evidence=[page_evidence(n) for n in range(8)]))
        self.assertEqual(text.count("\n- "), 6)
        self.assertIn("/page-4 : constat\n- et 3 autres preuves\n", text)
        self.assertNotIn("/page-5", text)

    def test_a_single_extra_evidence_uses_the_singular(self):
        text = comment(finding(evidence=[page_evidence(n) for n in range(6)]))
        self.assertIn("\n- et 1 autre preuve\n", text)

    def test_cap_1500(self):
        evidence = [page_evidence(n, "parcours au clavier") for n in range(20)]
        text = comment(finding(summary="s" * 600, evidence=evidence))
        self.assertLessEqual(len(text), 1500)
        self.assertIn("autres preuves", text)
        self.assertIn("- et 15 autres preuves\n", text)
        self.assertIn("Constat : " + "s" * 600 + "\n", text)

    def test_cap_drops_evidence_from_the_end(self):
        evidence = [page_evidence(n, "d" * 300) for n in range(8)]
        text = comment(finding(evidence=evidence))
        self.assertLessEqual(len(text), 1500)
        shown = [n for n in range(8) if f"/page-{n} :" in text]
        self.assertTrue(1 <= len(shown) < 5)
        self.assertEqual(shown, list(range(len(shown))))
        self.assertIn(f"- et {8 - len(shown)} autres preuves\n", text)
        self.assertIn("Constat : " + F_KO["summary"] + "\n", text)
        self.assertEqual(text.split("\n")[-1], FOOTER)

    def test_cap_truncates_the_summary_last(self):
        todo = "t" * 1000
        text = comment(finding(summary="s" * 600, todo=todo, evidence=[]))
        self.assertLessEqual(len(text), 1500)
        self.assertIn("À faire : " + todo + "\n", text)
        constat = next(line for line in text.split("\n") if line.startswith("Constat : "))
        self.assertTrue(constat.endswith("…"))
        self.assertTrue(constat.startswith("Constat : " + "s" * 100))
        self.assertLess(len(constat), len("Constat : ") + 600)
        self.assertEqual(text.split("\n")[-1], FOOTER)

    def test_cap_cuts_the_other_lines_when_the_summary_is_not_enough(self):
        text = comment(
            finding(
                summary="s" * 600,
                todo="t" * 1500,
                human_check="h" * 1500,
                divergence="d" * 1500,
                evidence=[],
            )
        )
        self.assertEqual(len(text), 1500)
        lines = text.split("\n")
        self.assertEqual(lines[2], "Constat : …")
        for label in ("À faire", "À valider", "Écart code / préprod"):
            self.assertTrue(any(line.startswith(label + " : ") for line in lines))
        self.assertEqual(lines[-1], FOOTER)

    def test_cap_holds_for_oversized_fields(self):
        huge = "x" * 5000
        cases = {
            "summary": finding(summary=huge),
            "todo": finding(todo=huge),
            "human_check and divergence": finding(human_check=huge, divergence=huge),
            "everything": finding(
                summary=huge,
                todo=huge,
                human_check=huge,
                divergence=huge,
                evidence=[page_evidence(n, huge) for n in range(40)],
            ),
            "one huge evidence": finding(evidence=[page_evidence(0, huge)]),
        }
        for name, item in cases.items():
            with self.subTest(name):
                text = comment(item)
                self.assertLessEqual(len(text), 1500)
                self.assertEqual(text.split("\n")[-1], FOOTER)
                self.assertEqual(parse_verdict(text), "ko")

    def test_cap_holds_whatever_the_lead_name(self):
        self.assertLessEqual(len(comment(F_KO, lead="x" * 3000)), 1500)

    def test_finding_is_not_modified(self):
        item = finding(summary="s" * 5000, evidence=[page_evidence(n) for n in range(20)])
        before = copy.deepcopy(item)
        comment(item)
        self.assertEqual(item, before)

    def test_no_em_dash(self):
        dirty = f"a {EM_DASH} b"
        text = comment(
            finding(
                summary=dirty,
                todo=dirty,
                human_check=dirty,
                divergence=dirty,
                evidence=[page_evidence(0, dirty)],
            )
        )
        self.assertNotIn(EM_DASH, text)
        self.assertIn("Constat : a - b\n", text)
        self.assertIn("À faire : a - b\n", text)
        self.assertIn("/page-0 : a - b\n", text)


class FormatExcludedCommentTest(unittest.TestCase):
    def test_exact_comment(self):
        self.assertEqual(
            excluded(),
            "\n".join(
                [
                    HEADER,
                    "Verdict : Non applicable",
                    "Constat : tâche propre à Wordpress, projet Laravel + Nuxt",
                    FOOTER,
                ]
            ),
        )

    def test_unknown_verdict(self):
        text = excluded(verdict="unknown", detail="outils indisponibles : SSH serveur, BDD")
        self.assertEqual(
            text.split("\n")[1:3],
            ["Verdict : Non évaluable", "Constat : outils indisponibles : SSH serveur, BDD"],
        )

    def test_cap_truncates_the_detail(self):
        text = excluded(detail="d" * 5000)
        self.assertLessEqual(len(text), 1500)
        lines = text.split("\n")
        self.assertTrue(lines[2].startswith("Constat : " + "d" * 100))
        self.assertTrue(lines[2].endswith("…"))
        self.assertEqual(lines[-1], FOOTER)
        self.assertEqual(parse_verdict(text), "na")

    def test_cap_holds_whatever_the_lead_name(self):
        self.assertLessEqual(len(excluded(lead="x" * 3000)), 1500)

    def test_no_em_dash(self):
        self.assertNotIn(EM_DASH, excluded(detail=f"a {EM_DASH} b"))


class ParseVerdictTest(unittest.TestCase):
    def test_parse_roundtrip(self):
        for verdict in VERDICT_LABELS:
            for confidence in CONFIDENCE_LABELS:
                with self.subTest(verdict=verdict, confidence=confidence):
                    text = comment(finding(verdict=verdict, confidence=confidence))
                    self.assertEqual(parse_verdict(text), verdict)

    def test_parse_roundtrip_of_excluded_comments(self):
        for verdict in VERDICT_LABELS:
            with self.subTest(verdict=verdict):
                self.assertEqual(parse_verdict(excluded(verdict=verdict)), verdict)

    def test_reads_the_line_anywhere_in_the_text(self):
        text = "Intro\nVerdict : Mis en place, à valider (confiance moyenne)\nSuite"
        self.assertEqual(parse_verdict(text), "ok_human")

    def test_confidence_is_optional(self):
        self.assertEqual(parse_verdict("Verdict : Non applicable"), "na")

    def test_tolerates_line_endings_and_spacing(self):
        crlf = "Audit\r\nVerdict :  Ok / Recetté  (confiance haute)\r\n"
        self.assertEqual(parse_verdict(crlf), "ok")
        self.assertEqual(parse_verdict("Verdict\xa0: À traiter"), "ko")

    def test_first_verdict_line_wins(self):
        text = "Verdict : À traiter (confiance haute)\nVerdict : Ok / Recetté (confiance haute)"
        self.assertEqual(parse_verdict(text), "ko")

    def test_none_without_a_verdict_line(self):
        for text in ("", "Pas de verdict ici", "Le Verdict : À traiter est discutable"):
            with self.subTest(text=text):
                self.assertIsNone(parse_verdict(text))

    def test_none_for_an_unknown_label(self):
        self.assertIsNone(parse_verdict("Verdict : Peut-être (confiance haute)"))


class ToRichTextTest(unittest.TestCase):
    URL = "https://gitlab.example.org/a/-/blob/x/f.vue#L4"

    def test_rich_text_links_and_chunks(self):
        rt = to_rich_text("voir https://gitlab.example.org/a/-/blob/x/f.vue#L4 ok " + "a" * 2500)
        self.assertEqual(
            rt[1]["text"]["link"]["url"], "https://gitlab.example.org/a/-/blob/x/f.vue#L4"
        )
        self.assertTrue(all(len(i["text"]["content"]) <= 2000 for i in rt))
        self.assertEqual(rt[0], {"type": "text", "text": {"content": "voir "}})
        self.assertEqual(
            rt[1],
            {"type": "text", "text": {"content": self.URL, "link": {"url": self.URL}}},
        )
        self.assertEqual(len(contents(rt)[2]), 2000)
        self.assertEqual("".join(contents(rt)), f"voir {self.URL} ok " + "a" * 2500)

    def test_plain_text_is_one_object_without_link(self):
        self.assertEqual(
            to_rich_text("bonjour"), [{"type": "text", "text": {"content": "bonjour"}}]
        )

    def test_empty_text(self):
        self.assertEqual(to_rich_text(""), [])

    def test_every_url_is_its_own_object(self):
        rt = to_rich_text("a https://example.org/1 b http://example.org/2 c")
        self.assertEqual(
            contents(rt),
            ["a ", "https://example.org/1", " b ", "http://example.org/2", " c"],
        )
        self.assertEqual(links(rt), ["https://example.org/1", "http://example.org/2"])

    def test_link_stops_before_a_closing_parenthesis(self):
        rt = to_rich_text(f"lien ({self.URL}) ok")
        self.assertEqual(contents(rt), ["lien (", self.URL, ") ok"])
        self.assertEqual(links(rt), [self.URL])

    def test_link_drops_trailing_punctuation(self):
        for suffix in (".", ",", ";", ":", "!", "?", "`", "'", '"', "*", ">", "…", ")."):
            with self.subTest(suffix=suffix):
                rt = to_rich_text(f"voir {self.URL}{suffix} suite")
                self.assertEqual(links(rt), [self.URL])
                self.assertEqual("".join(contents(rt)), f"voir {self.URL}{suffix} suite")

    def test_link_keeps_balanced_parentheses(self):
        url = "https://example.org/wiki/Foo_(bar)"
        self.assertEqual(links(to_rich_text(f"voir {url}")), [url])
        self.assertEqual(links(to_rich_text(f"({url})")), [url])

    def test_scheme_alone_is_not_a_link(self):
        for text in ("https://", "(https://)", "http://."):
            with self.subTest(text=text):
                rt = to_rich_text(text)
                self.assertEqual(links(rt), [])
                self.assertEqual("".join(contents(rt)), text)

    def test_chunk_boundary(self):
        self.assertEqual([len(c) for c in contents(to_rich_text("a" * 2000))], [2000])
        self.assertEqual([len(c) for c in contents(to_rich_text("a" * 2001))], [2000, 1])
        self.assertEqual([len(c) for c in contents(to_rich_text("a" * 4001))], [2000, 2000, 1])

    def test_chunks_stay_within_the_limit_in_utf16_units(self):
        text = "\U0001F600" * 1500
        rt = to_rich_text(text)
        for chunk in contents(rt):
            self.assertLessEqual(len(chunk.encode("utf-16-le")) // 2, 2000)
        self.assertEqual("".join(contents(rt)), text)

    def test_url_too_long_for_a_link_stays_plain(self):
        text = "https://example.org/" + "a" * 2100
        rt = to_rich_text(text)
        self.assertEqual(links(rt), [])
        self.assertTrue(all(len(c) <= 2000 for c in contents(rt)))
        self.assertEqual("".join(contents(rt)), text)

    def test_comment_evidence_links_are_clean(self):
        rt = to_rich_text(comment(F_KO))
        self.assertEqual(
            links(rt),
            [
                f"{BLOB_NUXT}/components/AppHeader.vue#L42",
                "https://preprod.example.org/actualites/x",
            ],
        )
        self.assertEqual("".join(contents(rt)), EXPECTED_KO_TEXT)


class FormatPageSummaryTest(unittest.TestCase):
    def test_exact_summary(self):
        self.assertEqual(page_summary(), EXPECTED_PAGE_TEXT)

    def test_no_blocking_line_is_stated(self):
        text = page_summary({"by_status_after": {"Ok / Recetté": 3}, "blocking_non_conform": []})
        self.assertEqual(
            text.split("\n"),
            [
                HEADER,
                "Décompte par statut :",
                "- Ok / Recetté : 3",
                "Lignes bloquantes non conformes : aucune",
            ],
        )

    def test_empty_summary(self):
        self.assertEqual(
            page_summary({}).split("\n"),
            [HEADER, "Lignes bloquantes non conformes : aucune"],
        )

    def test_counts_may_be_numbers_or_lists(self):
        as_numbers = {"unknown": 4, "deferred": 6, "missing_in_copy": 1}
        as_lists = {"unknown": [1, 2, 3, 4], "deferred": [5] * 6, "missing_in_copy": [{"ref": 3}]}
        self.assertEqual(page_summary(as_numbers), page_summary(as_lists))
        self.assertEqual(
            page_summary(as_numbers).split("\n")[2:],
            [
                "Lignes non évaluées : 4",
                "Lignes reportées à J0 / J+7 : 6",
                "Lignes du modèle absentes de la copie : 1",
            ],
        )

    def test_zero_counts_leave_no_line(self):
        empty = {"unknown": 0, "deferred": [], "missing_in_copy": 0, "human_overrides": []}
        text = page_summary(empty)
        self.assertEqual(text.split("\n"), [HEADER, "Lignes bloquantes non conformes : aucune"])

    def test_refs_may_already_be_labels(self):
        text = page_summary({"blocking_non_conform": ["CQ-79", "CQ-81"]})
        self.assertIn("Lignes bloquantes non conformes (2) : CQ-79, CQ-81", text)

    def test_page_summary_cap(self):
        summary = {**PAGE_SUMMARY, "blocking_non_conform": list(range(100, 300))}
        text = page_summary(summary)
        self.assertLessEqual(len(text), 1500)
        self.assertIn("- À traiter : 12", text)
        self.assertIn("Lignes non évaluées : 4", text)
        line = next(item for item in text.split("\n") if item.startswith("Lignes bloquantes"))
        self.assertIn("(200) : CQ-100, CQ-101, CQ-102", line)
        shown = line.count("CQ-")
        self.assertTrue(0 < shown < 200)
        self.assertTrue(line.endswith(f" et {200 - shown} autres"))

    def test_page_summary_cap_keeps_a_count_when_no_ref_fits(self):
        count_line = "Lignes bloquantes non conformes : 30"
        fixed = "\n".join([HEADER, "Décompte par statut :", "-  : 1", count_line])
        status = "s" * (1500 - len(fixed) - 1)
        summary = {
            "by_status_after": {status: 1},
            "blocking_non_conform": list(range(100, 130)),
        }
        self.assertEqual(
            page_summary(summary),
            "\n".join([HEADER, "Décompte par statut :", f"- {status} : 1", count_line]),
        )

    def test_page_summary_cap_holds_for_extreme_inputs(self):
        summary = {"by_status_after": {f"Statut {n} " + "x" * 80: n + 1 for n in range(100)}}
        self.assertLessEqual(len(page_summary(summary)), 1500)
        self.assertLessEqual(len(page_summary(PAGE_SUMMARY, lead="x" * 3000)), 1500)

    def test_unknown_statuses_follow_the_known_ones(self):
        text = page_summary({"by_status_after": {"Autre": 2, "À traiter": 1}})
        self.assertEqual(
            text.split("\n")[1:4], ["Décompte par statut :", "- À traiter : 1", "- Autre : 2"]
        )

    def test_no_em_dash(self):
        text = page_summary({"by_status_after": {f"A {EM_DASH} B": 1}})
        self.assertNotIn(EM_DASH, text)
        self.assertIn("- A - B : 1", text)


class SecretScanTest(unittest.TestCase):
    def test_formatted_comments_pass_the_scan_that_gates_writes(self):
        texts = [
            comment(F_KO),
            comment(finding(verdict="ok_human", human_check="Valider.", divergence="Écart.")),
            excluded(),
            page_summary(),
        ]
        for text in texts:
            with self.subTest(first_line=text.split("\n")[1]):
                self.assertEqual(scan_text(text, []), [])


if __name__ == "__main__":
    unittest.main()
