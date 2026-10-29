import json
import re
import subprocess
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

import cqa_fix
from cqa import fix_mr, fix_state
from tests.cqa.test_fix_git import GitCase, HOST, PREFIX, flight, mr
from tests.cqa.test_git_tools import commit, git as real_git

KEY = "front/securite"
REPO = f"{HOST}/acme/shop"
STALE = "Description de MR périmée : relancez mr-body."
BAD_MESSAGE = "message de commit hors convention"
RUN_ID = "2026-10-29-0930"
ROW_ID = "01234567-89ab-cdef-0123-456789abcdef"
ROW_URL = "https://www.notion.so/0123456789abcdef0123456789abcdef"
COPY_ID = "fedcba98-7654-3210-fedc-ba9876543210"
COPY_URL = "https://www.notion.so/fedcba9876543210fedcba9876543210"
MR_URL = f"https://{HOST}/acme/shop/-/merge_requests/12"
HEADINGS = ["## Contexte", "## Correctifs", "## Vérification", "## Points d'attention",
            "## En attente d'une décision", "## Hors-scope", "## Suite"]
SUITE = ("Après fusion et déploiement en préprod : passer les lignes en « Mis en place » dans "
         "Notion, puis lancer `/gm:checklist-quality-audit recette`.")
CI = "- Pipeline CI de la MR : lint et tests du projet, s'il les lance."
SEMI = "**Semi-auto : à valider avec soin.**"
DIVERGED = "La branche distante a divergé : intégrez ses commits à la main, rien n'a été poussé."
PHP = "Syntaxe PHP (php -l)"
STYLE = "stylelint (configuration du projet)"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def section(text, heading, level="##"):
    """Return the lines of a section, from its heading to the next heading of the same level."""
    lines = text.splitlines()
    start = lines.index(f"{level} {heading}") + 1
    end = next((index for index in range(start, len(lines))
                if lines[index].startswith(f"{level} ")), len(lines))
    return [line for line in lines[start:end] if line.strip()]


def fixed_row(**change):
    row = {"ref": 107, "title": "En-têtes de sécurité", "url": ROW_URL, "semi_auto": False,
           "human_check": None, "summary": "Aucun en-tête CSP sur les pages publiques.",
           "change": "Ajoute l'en-tête CSP dans le thème.",
           "uat": "Ouvrir l'accueil et lire les en-têtes.", "commits": ["1a2b3c4d"]}
    row.update(change)
    return row


def full_body(**change):
    values = {
        "run_id": RUN_ID, "category": "SÉCURITÉ", "copy_url": COPY_URL,
        "fixed": [fixed_row()],
        "checks": [
            {"name": PHP, "status": "passed", "files": ["web/a.php"], "detail": ""},
            {"name": STYLE, "status": "not_run", "files": ["web/a.css"],
             "detail": "boîte à outils indisponible"},
        ],
        "not_run": [{"name": "Makefile (lint)", "command": "make lint"}],
        "attention": ["Nommer la constante."],
        "waiting": [{"ref": 12, "title": "security.txt", "text": "Quel contact ?", "url": None}],
        "left": [{"ref": 40, "title": "Bannière cookies", "reason": "Manuel",
                  "url": "https://www.notion.so/p40"}],
    }
    values.update(change)
    return fix_mr.body(**values)


class TitleAndBodyTest(unittest.TestCase):
    def test_title_counts_the_rows(self):
        self.assertEqual(fix_mr.title("SÉCURITÉ", 5), "Checklist PRE-MEL : correctifs SÉCURITÉ (5 lignes)")
        self.assertEqual(fix_mr.title("SÉCURITÉ", 1), "Checklist PRE-MEL : correctifs SÉCURITÉ (1 ligne)")

    def test_body_has_every_section_and_no_attribution(self):
        text = full_body()
        self.assertEqual([line for line in text.splitlines() if line.startswith("## ")], HEADINGS)
        context = section(text, "Contexte")
        self.assertEqual(
            context[0], "Audit PRE-MEL du 29/10/2026 (run 2026-10-29-0930), catégorie SÉCURITÉ."
        )
        self.assertTrue(any(COPY_URL in line for line in context))
        self.assertTrue(any("brouillon" in line for line in context))
        fixes = section(text, "Correctifs")
        self.assertEqual(fixes[0], "### CQ-107 : En-têtes de sécurité")
        joined = "\n".join(fixes)
        for expected in (ROW_URL, "Aucun en-tête CSP sur les pages publiques.",
                         "Ajoute l'en-tête CSP dans le thème.", "1a2b3c4d",
                         "Ouvrir l'accueil et lire les en-têtes."):
            self.assertIn(expected, joined)
        self.assertNotIn(SEMI, text)
        self.assertEqual(section(text, "Vérification"), [
            f"- ✅ {PHP} (web/a.php)",
            f"- Non lancé : {STYLE} (web/a.css) : boîte à outils indisponible",
            "- Non lancé : Makefile (lint) : `make lint`",
            CI,
        ])
        self.assertEqual(section(text, "Points d'attention"), ["- Nommer la constante."])
        self.assertEqual(section(text, "En attente d'une décision"),
                         ["- CQ-12 : security.txt : Quel contact ?"])
        self.assertEqual(section(text, "Hors-scope"),
                         ["- CQ-40 : Bannière cookies : Manuel ([Notion](https://www.notion.so/p40))"])
        self.assertEqual(section(text, "Suite"), [SUITE])
        for forbidden in ("Co-Authored", "Generated with", "\u2014"):
            self.assertNotIn(forbidden, text)
        self.assertTrue(text.endswith("\n"))

    def test_empty_sections_are_left_out(self):
        text = full_body(attention=[], waiting=[], left=[], copy_url=None)
        self.assertEqual([line for line in text.splitlines() if line.startswith("## ")],
                         ["## Contexte", "## Correctifs", "## Vérification", "## Suite"])
        self.assertNotIn("notion.so/fedcba", text)
        text = full_body(fixed=[], checks=None, not_run=[])
        self.assertNotIn("## Correctifs", text)
        self.assertEqual(section(text, "Vérification"), [
            "- Non lancé : contrôles de syntaxe et stylelint : aucun résultat pour cette branche",
            CI,
        ])

    def test_body_replaces_long_dashes_of_the_data(self):
        text = full_body(fixed=[fixed_row(title="Cookies \u2014 bannière")])
        self.assertIn("### CQ-107 : Cookies - bannière", text)
        self.assertNotIn("\u2014", text)
        added = fix_mr.addition(
            fixed=[], checks=None, not_run=[], attention=[], waiting=[],
            left=[{"ref": 41, "title": "Bannière \u2014 cookies", "reason": "Manuel \u2014 UI/UX",
                   "url": None}],
        )
        self.assertEqual(added, "## Hors-scope\n\n- CQ-41 : Bannière - cookies : Manuel - UI/UX\n")

    def test_same_description_ignores_line_endings_and_trailing_spaces(self):
        self.assertTrue(fix_mr.same_description("## A\r\n\r\nTexte.  \r\n", "## A\n\nTexte.\n"))
        self.assertFalse(fix_mr.same_description("## A\n\nTexte.\n", "## A\n\nAutre.\n"))

    def test_semi_auto_rows_are_flagged(self):
        text = full_body(fixed=[
            fixed_row(semi_auto=True, human_check="Vérifier le rendu des iframes."),
            fixed_row(ref=108, title="Autre ligne"),
        ])
        fixes = section(text, "Correctifs")
        first = fixes[:fixes.index("### CQ-108 : Autre ligne")]
        self.assertIn(f"{SEMI} À valider : Vérifier le rendu des iframes.", first)
        self.assertEqual(text.count(SEMI), 1)
        alone = full_body(fixed=[fixed_row(semi_auto=True)])
        self.assertIn(SEMI, section(alone, "Correctifs"))

    def test_failed_checks_and_checks_without_files(self):
        text = full_body(checks=[
            {"name": "JSON", "status": "failed", "files": ["a.json"], "detail": "Unexpected token"},
            {"name": STYLE, "status": "not_run", "files": [], "detail": "boîte à outils indisponible"},
        ], not_run=[])
        self.assertEqual(section(text, "Vérification"), [
            "- ❌ Échec : JSON (a.json) : Unexpected token",
            f"- Non lancé : {STYLE} : boîte à outils indisponible",
            CI,
        ])

    def test_earlier_fixes_of_a_reused_branch_are_named(self):
        text = full_body(earlier=[{"ref": 2, "title": "Deux"}, {"ref": 9, "title": None}])
        self.assertIn(
            "La branche porte aussi des correctifs d'une passe précédente : CQ-2 (Deux), CQ-9.",
            section(text, "Contexte"),
        )


class MaterialTest(unittest.TestCase):
    def setUp(self):
        def row(outcome, reason=None, commits=(), attention=None, number=1):
            return {"outcome": outcome, "reason": reason, "commits": list(commits),
                    "pass": number, "attention": attention}

        self.sha = "0123456789abcdef0123456789abcdef01234567"
        self.key = {"repo": "front", "category": "SÉCURITÉ", "slug": "securite", "mr": None,
                    "rows": {
                        "3": row("fixed", commits=[self.sha], attention="Vérifier le cache du CDN."),
                        "5": row("skipped", "Demande une décision du client."),
                        "6": row("dropped", "Revue bloquante : échappement manquant."),
                        "12": row("question", "Quel contact pour security.txt ?"),
                    }}

        def info(ref, title, page_id, **more):
            return {"ref": ref, "title": title, "page_id": page_id, "summary": f"Constat {ref}.",
                    "semi_auto": False, "human_check": None, **more}

        self.candidates = {
            "keys": [
                {"key": KEY, "repo": "front", "category": "SÉCURITÉ", "slug": "securite", "rows": [
                    info(3, "Trois", ROW_ID), info(5, "Cinq", "p5"), info(6, "Six", "p6"),
                    info(12, "Douze", "p12"),
                ]},
                {"key": "back/securite", "repo": "back", "category": "SÉCURITÉ",
                 "slug": "securite", "rows": [info(41, "Quarante et un", "p41")]},
            ],
            "left": [
                {"ref": 40, "title": "Quarante", "category": "SÉCURITÉ", "reason": "Manuel", "url": None},
                {"ref": 41, "title": "Quarante et un", "category": "SÉCURITÉ",
                 "reason": "repo hors GitLab", "url": None},
                {"ref": 42, "title": "Quarante-deux", "category": "SÉCURITÉ",
                 "reason": "Détection seule", "url": None},
                {"ref": 43, "title": "Quarante-trois", "category": "SÉCURITÉ",
                 "reason": "non évaluée par l'audit : lancer d'abord une relance", "url": None},
                {"ref": 50, "title": "Cinquante", "category": "QUALITÉ", "reason": "Manuel", "url": None},
                {"ref": 51, "title": "Cinquante et un", "category": "SÉCURITÉ",
                 "reason": f"déjà sur la branche {PREFIX}securite", "url": None},
                {"ref": 52, "title": "Cinquante-deux", "category": "SÉCURITÉ",
                 "reason": "déjà dans la MR !12", "url": MR_URL},
                {"ref": 53, "title": "Cinquante-trois", "category": "SÉCURITÉ",
                 "reason": "déjà dans la MR !9", "url": f"https://{HOST}/acme/back/-/merge_requests/9"},
            ],
        }
        self.notion_rows = [{"ref": 40, "page_id": "p40"}, {"ref": 41, "page_id": "wrong"},
                            {"ref": 42, "page_id": "p42"}]

    def material(self, reports=(), reviews=(), checks=None, changed=(), branch_refs=(3, 51), since=0):
        return fix_mr.material(
            self.key, self.candidates, self.notion_rows, list(reports), list(reviews), checks,
            list(changed), set(branch_refs), since=since,
        )

    def test_hors_scope_lists_left_rows_of_the_category(self):
        self.key["mr"] = {"iid": 12, "url": MR_URL}
        found = self.material()
        text = fix_mr.body(run_id=RUN_ID, category="SÉCURITÉ", copy_url=None, **found)
        self.assertEqual(section(text, "Hors-scope"), [
            "- CQ-5 : Cinq : Demande une décision du client. ([Notion](https://www.notion.so/p5))",
            "- CQ-6 : Six : Revue bloquante : échappement manquant. ([Notion](https://www.notion.so/p6))",
            "- CQ-40 : Quarante : Manuel ([Notion](https://www.notion.so/p40))",
            "- CQ-41 : Quarante et un : repo hors GitLab ([Notion](https://www.notion.so/p41))",
            "- CQ-42 : Quarante-deux : Détection seule ([Notion](https://www.notion.so/p42))",
            "- CQ-43 : Quarante-trois : non évaluée par l'audit : lancer d'abord une relance",
            "- CQ-53 : Cinquante-trois : déjà dans la MR !9",
        ])
        self.assertEqual(section(text, "En attente d'une décision"), [
            "- CQ-12 : Douze : Quel contact pour security.txt ? ([Notion](https://www.notion.so/p12))",
        ])
        self.assertEqual(section(text, "Correctifs")[0], "### CQ-3 : Trois")
        self.assertIn(ROW_URL, "\n".join(section(text, "Correctifs")))

    def test_attention_accumulates_over_reviews(self):
        reports = [(1, {"rows": [
            {"ref": 3, "outcome": "fixed", "attention": "Vérifier le cache du CDN."},
            {"ref": 6, "outcome": "fixed", "attention": "Abandon : correctif trop risqué."},
            {"ref": 5, "outcome": "skipped", "attention": "Hors du repo."},
        ]})]
        reviews = [(1, {"attention": ["Nommer la constante."]}),
                   (2, {"attention": ["Ajouter un test.", "Nommer la constante."]})]
        found = self.material(reports=reports, reviews=reviews)
        self.assertEqual(found["attention"], [
            "Nommer la constante.", "Ajouter un test.", "CQ-3 : Vérifier le cache du CDN.",
        ])
        text = fix_mr.body(run_id=RUN_ID, category="SÉCURITÉ", copy_url=None, **found)
        self.assertEqual(section(text, "Points d'attention"), [
            "- Nommer la constante.", "- Ajouter un test.", "- CQ-3 : Vérifier le cache du CDN.",
        ])
        later = self.material(reports=reports, reviews=reviews, since=1)
        self.assertEqual(later["attention"], ["Ajouter un test.", "Nommer la constante."])

    def test_material_takes_the_latest_report_and_the_current_files(self):
        reports = [
            (1, {"rows": [{"ref": 3, "outcome": "fixed", "change": "c1", "uat": "u1"}]}),
            (2, {"rows": [{"ref": 3, "outcome": "fixed", "change": "c2", "uat": "u2"}]}),
            (3, {"rows": [{"ref": 12, "outcome": "question"}]}),
        ]
        checks = {"checks": [
            {"name": PHP, "status": "passed", "files": ["a.php", "gone.php"], "detail": ""},
            {"name": "JSON", "status": "failed", "files": ["gone.json"], "detail": "x"},
            {"name": STYLE, "status": "not_run", "files": [], "detail": "boîte à outils indisponible"},
        ], "not_run": [{"name": "Makefile (lint)", "command": "make lint", "why": "w"}]}
        found = self.material(reports=reports, checks=checks, changed=["a.php"],
                              branch_refs=(2, 3, 51))
        self.assertEqual(found["fixed"], [{
            "ref": 3, "title": "Trois", "url": ROW_URL, "semi_auto": False, "human_check": None,
            "summary": "Constat 3.", "change": "c2", "uat": "u2", "commits": [self.sha[:8]],
        }])
        self.assertEqual(found["checks"], [
            {"name": PHP, "status": "passed", "files": ["a.php"], "detail": ""},
            {"name": STYLE, "status": "not_run", "files": [], "detail": "boîte à outils indisponible"},
        ])
        self.assertEqual(found["not_run"], [{"name": "Makefile (lint)", "command": "make lint"}])
        self.assertEqual(found["earlier"],
                         [{"ref": 2, "title": None}, {"ref": 51, "title": "Cinquante et un"}])
        self.assertIsNone(self.material()["checks"])
        self.assertEqual(self.material(since=1)["fixed"], [])


class MergeDescriptionTest(unittest.TestCase):
    TODAY = date(2026, 10, 30)
    ADDITION = "### Correctifs\n\n#### CQ-12 : Douze\n\n- Changement : x"
    HORS = "- CQ-41 : Quarante et un : Manuel"

    def test_merge_description_keeps_reviewer_edits(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                head = newline.join([
                    "## Contexte", "", "Texte retouché par le relecteur  ", "", "## Correctifs", "",
                    "### CQ-107 : En-têtes", "", "- Changement : retouché, voir !15", "", "",
                ])
                tail = newline.join(["## Suite", "", "Note du relecteur.", ""])
                old = head + newline.join(["## Hors-scope", "", "- CQ-40 : Bannière : Manuel", "", ""]) + tail
                merged = fix_mr.merge_description(old, self.ADDITION, self.HORS, self.TODAY)
                self.assertEqual(
                    merged,
                    head + "## Ajouts du 30/10/2026\n\n" + self.ADDITION + "\n\n## Hors-scope\n\n"
                    + self.HORS + "\n\n" + tail,
                )

    def test_merge_description_without_hors_scope_appends_at_the_end(self):
        for old in ("## Contexte\n\nTexte.\n", "## Contexte\n\nTexte.\n\n", "## Contexte\n\nTexte."):
            with self.subTest(old=old):
                merged = fix_mr.merge_description(old, self.ADDITION, self.HORS, self.TODAY)
                self.assertTrue(merged.startswith(old))
                self.assertEqual(
                    merged[len(old):].lstrip("\n"),
                    "## Ajouts du 30/10/2026\n\n" + self.ADDITION + "\n\n## Hors-scope\n\n"
                    + self.HORS + "\n",
                )
                self.assertIn("Texte.\n\n## Ajouts", merged)

    def test_merge_description_edge_cases(self):
        old = "## Contexte\n\nTexte.\n\n## Hors-scope\n\n- CQ-40 : Bannière : Manuel\n"
        self.assertEqual(fix_mr.merge_description(old, "", self.HORS, self.TODAY),
                         "## Contexte\n\nTexte.\n\n## Hors-scope\n\n" + self.HORS + "\n")
        self.assertEqual(fix_mr.merge_description(old, self.ADDITION, "", self.TODAY),
                         "## Contexte\n\nTexte.\n\n## Ajouts du 30/10/2026\n\n" + self.ADDITION + "\n")
        nested = "## Contexte\n\n### Hors-scope\n\nNote.\n"
        merged = fix_mr.merge_description(nested, "", self.HORS, self.TODAY)
        self.assertEqual(merged, nested + "\n## Hors-scope\n\n" + self.HORS + "\n")

    def test_addition_file_round_trip(self):
        text = fix_mr.addition(
            fixed=[fixed_row(ref=12, title="Douze")], checks=[], not_run=[], attention=["A."],
            waiting=[], left=[{"ref": 41, "title": "X", "reason": "Manuel", "url": None}],
        )
        addition, hors = fix_mr.split_addition(text)
        self.assertTrue(addition.startswith("### Correctifs\n\n#### CQ-12 : Douze"))
        self.assertIn("### Points d'attention\n\n- A.", addition)
        self.assertIn("### Vérification", addition)
        self.assertNotIn("## Hors-scope", addition)
        self.assertEqual(hors, "- CQ-41 : X : Manuel")
        empty = fix_mr.addition(fixed=[], checks=None, not_run=[{"name": "n", "command": "c"}],
                                attention=[], waiting=[], left=[])
        self.assertEqual(fix_mr.split_addition(empty), ("", ""))


def done(command, out="", code=0, err=""):
    return subprocess.CompletedProcess(command, code, out, err)


def allowed(args):
    """glab may only read the API, create a draft MR and update a description, on REPO."""
    if args[:1] == ["api"]:
        return len(args) == 2 and not args[1].startswith("-")
    if args[:2] == ["mr", "create"]:
        forbidden = ("--push", "--reviewer", "--label", "-l", "--remove-source-branch", "--fill")
        return ("--draft" in args and args[2:4] == ["-R", REPO]
                and not any(flag in args for flag in forbidden))
    if args[:2] == ["mr", "update"]:
        return (len(args) == 6 and args[2].isdigit() and args[3:5] == ["-R", REPO]
                and args[5].startswith("--description="))
    return False


class FakeGitLab:
    """Runs git for real and answers glab as GitLab would; records every call."""

    def __init__(self):
        self.git, self.calls, self.mrs = [], [], {}
        self.next_iid, self.create_output = 12, None

    def url(self, iid):
        return f"https://{HOST}/acme/shop/-/merge_requests/{iid}"

    def add(self, iid, branch, description="", state="opened"):
        self.mrs[iid] = {"iid": iid, "web_url": self.url(iid), "state": state,
                         "source_branch": branch, "description": description}

    def __call__(self, command, **kwargs):
        if command[0] == "git":
            self.git.append(list(command))
            return subprocess.run(command, **kwargs)
        args = list(command[1:])
        self.calls.append((args, kwargs))
        if args[0] == "api":
            single = re.fullmatch(r"projects/acme%2Fshop/merge_requests/([0-9]+)", args[1])
            if single and int(single.group(1)) in self.mrs:
                return done(command, json.dumps(self.mrs[int(single.group(1))]))
            listing = re.fullmatch(
                r"projects/acme%2Fshop/merge_requests\?source_branch=([^&]+)&state=opened", args[1])
            if listing:
                branch = unquote(listing.group(1))
                return done(command, json.dumps([
                    item for item in self.mrs.values()
                    if item["source_branch"] == branch and item["state"] == "opened"
                ]))
            return done(command, "", 1, "404 Not Found")
        if args[:2] == ["mr", "create"]:
            iid = self.next_iid
            description = next(a for a in args if a.startswith("--description="))
            self.add(iid, args[args.index("--source-branch") + 1], description[len("--description="):])
            out = self.create_output
            if out is None:
                out = f"\n!{iid} Draft: correctifs ({self.mrs[iid]['source_branch']})\n {self.url(iid)}\n\n"
            return done(command, out)
        if args[:2] == ["mr", "update"]:
            description = next(a for a in args if a.startswith("--description="))
            self.mrs[int(args[2])]["description"] = description[len("--description="):]
            return done(command, self.url(int(args[2])) + "\n")
        return done(command, "", 1, "unexpected glab call")


class MrCase(GitCase):
    def setUp(self):
        super().setUp()
        target = self.root / RUN_ID
        self.run_dir.rename(target)
        self.run_dir = target
        self.folder = self.run_dir / "fix" / "front" / "securite"
        self.gitlab = FakeGitLab()

    def tearDown(self):
        self.assert_allowed()
        super().tearDown()

    def cli(self, *argv):
        code, out, err = self.run_main([argv[0], self.run_dir, *argv[1:]], run=self.gitlab)
        return code, out, err

    def write_doc(self, name, document):
        (self.folder / name).write_text(json.dumps(document), encoding="utf-8")

    def reviewed(self, push="mr", fix=True):
        """Open a pass, fix CQ-3 and skip CQ-5 in pass 1, then review: the key is reviewed."""
        self.open_pass(push=push)
        started = self.start_ok()
        self.worktree = Path(started["worktree"])
        self.branch = started["branch"]
        rows = [{"ref": 5, "outcome": "skipped", "reason": "Demande une décision de l'équipe UI/UX."}]
        if fix:
            self.fixed = self.fixer_commit(self.worktree, "a3.txt", "fix(CQ-3): add a")
            rows.insert(0, {"ref": 3, "outcome": "fixed", "change": "Ajoute a.", "uat": "Lire a.",
                            "commits": [self.fixed], "attention": "Vérifier le cache du CDN."})
        else:
            rows.insert(0, {"ref": 3, "outcome": "skipped", "reason": "Déjà corrigé sur develop."})
        self.write_doc("report-1.json", {"key": KEY, "pass": 1, "rows": rows})
        code, out, err = self.cli("validate", KEY, "report", "--pass", "1")
        self.assertEqual(code, 0, err)
        verdicts = [{"ref": 3, "verdict": "ok", "notes": None}] if fix else []
        self.write_doc("review-1.json", {"key": KEY, "pass": 1, "attention": ["Nommer la constante."],
                                         "rows": verdicts})
        code, out, err = self.cli("validate", KEY, "review", "--pass", "1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["step"], "reviewed")
        return started

    def remote(self, branch):
        found = subprocess.run(
            ["git", "-C", str(self.origin), "for-each-ref", "--format=%(objectname)",
             f"refs/heads/{branch}"], capture_output=True, text=True, check=True,
        )
        return found.stdout.strip()

    def links(self):
        return json.loads((self.run_dir / "fix" / "mr-links.json").read_text(encoding="utf-8"))

    def glab_args(self, *prefix):
        return [args for args, _ in self.gitlab.calls if args[:len(prefix)] == list(prefix)]

    def assert_allowed(self):
        for args, kwargs in self.gitlab.calls:
            self.assertTrue(allowed(args), args)
            self.assertEqual(kwargs["env"]["GITLAB_HOST"], HOST)

    def open_mr(self, state="opened", description="## Contexte\n\nTexte du relecteur.\n"):
        """A branch pushed by an earlier pass, with its MR !12, taken over by start."""
        branch = PREFIX + "securite"
        self.push_branch(branch, "fix(CQ-107): add headers")
        self.write_run(flight(open=[mr(12, branch, [107])]))
        self.gitlab.add(12, branch, description, state)
        return branch


class MrBodyTest(MrCase):
    def test_mr_body_writes_the_title_and_the_description(self):
        self.reviewed()
        context = json.loads((self.run_dir / "context.json").read_text(encoding="utf-8"))
        context["notion"] = {"copy_page_id": COPY_ID}
        (self.run_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
        self.write_doc("checks.json", {
            "checks": [{"name": PHP, "status": "passed", "files": ["a3.txt"], "detail": ""}],
            "not_run": [{"name": "Makefile (lint)", "command": "make lint", "why": "w"}],
        })
        code, out, err = self.cli("mr-body", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {
            "title": "Checklist PRE-MEL : correctifs SECURITE (1 ligne)",
            "file": "fix/front/securite/mr.md", "kind": "create", "fixed": [3], "waiting": [],
            "out_of_scope": [5],
        })
        self.assertEqual((self.folder / "mr-title.txt").read_text(encoding="utf-8"),
                         "Checklist PRE-MEL : correctifs SECURITE (1 ligne)\n")
        text = (self.folder / "mr.md").read_text(encoding="utf-8")
        self.assertEqual(section(text, "Contexte")[0],
                         f"Audit PRE-MEL du 29/10/2026 (run {RUN_ID}), catégorie SECURITE.")
        self.assertIn(COPY_URL, text)
        fixes = section(text, "Correctifs")
        self.assertEqual(fixes[0], "### CQ-3 : Ligne 3")
        for expected in ("Ajoute a.", "Lire a.", self.fixed[:8]):
            self.assertIn(expected, "\n".join(fixes))
        self.assertEqual(section(text, "Vérification"),
                         [f"- ✅ {PHP} (a3.txt)", "- Non lancé : Makefile (lint) : `make lint`", CI])
        self.assertEqual(section(text, "Points d'attention"),
                         ["- Nommer la constante.", "- CQ-3 : Vérifier le cache du CDN."])
        self.assertEqual(section(text, "Hors-scope"),
                         ["- CQ-5 : Ligne 5 : Demande une décision de l'équipe UI/UX."])
        self.assertFalse((self.folder / "mr-addition.md").exists())

    def test_mr_body_writes_an_addition_when_the_key_has_an_mr(self):
        self.open_mr()
        self.reviewed()
        (self.folder / "mr.md").write_text("stale", encoding="utf-8")
        code, out, err = self.cli("mr-body", KEY)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["kind"], result["file"]), ("update", "fix/front/securite/mr-addition.md"))
        self.assertFalse((self.folder / "mr.md").exists())
        addition, hors = fix_mr.split_addition(
            (self.folder / "mr-addition.md").read_text(encoding="utf-8"))
        self.assertTrue(addition.startswith("### Correctifs\n\n#### CQ-3 : Ligne 3"))
        self.assertNotIn("CQ-107", addition)
        self.assertEqual(hors, "- CQ-5 : Ligne 5 : Demande une décision de l'équipe UI/UX.")

    def test_mr_body_needs_a_reviewed_key_and_a_run_id(self):
        self.open_pass()
        self.start_ok()
        code, out, err = self.cli("mr-body", KEY)
        self.assertEqual(code, 2)
        self.assertIn("reviewed", err)
        state = self.state()
        state["keys"][KEY]["step"] = "reviewed"
        fix_state.save(self.run_dir, state)
        renamed = self.root / "run"
        self.run_dir.rename(renamed)
        self.run_dir = renamed
        code, out, err = self.cli("mr-body", KEY)
        self.assertEqual(code, 6)
        self.assertIn("AAAA-MM-JJ-HHMM", err)

    def test_mr_body_and_publish_refuse_a_secret(self):
        self.reviewed()
        report = json.loads((self.folder / "report-1.json").read_text(encoding="utf-8"))
        report["rows"][0]["change"] = f"Retire la clé {AWS} du thème."
        self.write_doc("report-1.json", report)
        code, out, err = self.cli("mr-body", KEY)
        self.assertEqual(code, 7)
        self.assertNotIn(AWS, out + err)
        self.assertFalse((self.folder / "mr.md").exists())
        report["rows"][0]["change"] = "Retire la clé du thème."
        self.write_doc("report-1.json", report)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        with (self.folder / "mr.md").open("a", encoding="utf-8") as handle:
            handle.write(f"\nclé : {AWS}\n")
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 7)
        self.assertNotIn(AWS, out + err)
        self.assertEqual(self.remote(self.branch), "")
        self.assertEqual(self.glab_args("mr"), [])


class PublishTest(MrCase):
    def test_publish_creates_a_draft_mr_assigned_to_the_lead(self):
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        real_git(self.repo, "config", "push.pushOption", "ci.skip")
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        head = self.head(self.worktree)
        self.assertEqual(self.remote(self.branch), head)
        pushes = [command for command in self.gitlab.git if "push" in command]
        self.assertEqual(len(pushes), 1)
        push = pushes[0]
        self.assertIn("core.hooksPath=/dev/null", push)
        self.assertIn("push.pushOption=", push)
        self.assertEqual(push[-2:], ["origin", f"HEAD:refs/heads/{self.branch}"])
        for flag in ("--force", "-f", "-u", "--set-upstream", "--force-with-lease", "--mirror",
                     "--all", "--tags", "--delete", "--follow-tags"):
            self.assertNotIn(flag, push)
        self.assertFalse(any(arg.startswith("+") for arg in push))
        creates = [(args, kwargs) for args, kwargs in self.gitlab.calls if args[:2] == ["mr", "create"]]
        self.assertEqual(len(creates), 1)
        args, kwargs = creates[0]
        self.assertIn("--draft", args)
        self.assertIn("--yes", args)
        self.assertEqual(args[args.index("-R") + 1], REPO)
        self.assertEqual(args[args.index("--source-branch") + 1], self.branch)
        self.assertEqual(args[args.index("--target-branch") + 1], "develop")
        self.assertEqual(args[args.index("--assignee") + 1], "camille")
        self.assertEqual(args[args.index("--title") + 1],
                         "Checklist PRE-MEL : correctifs SECURITE (1 ligne)")
        description = (self.folder / "mr.md").read_text(encoding="utf-8")
        self.assertIn(f"--description={description}", args)
        self.assertEqual(kwargs["env"]["GITLAB_HOST"], HOST)
        self.assertEqual(Path(kwargs["cwd"]).resolve(), self.worktree.resolve())
        result = json.loads(out)
        self.assertEqual((result["mr"], result["created"], result["pushed"]),
                         ({"iid": 12, "url": MR_URL}, True, True))
        key = self.key()
        self.assertEqual((key["mr"], key["step"]), ({"iid": 12, "url": MR_URL}, "published"))
        self.assertEqual(self.links(), {"links": {"3": [{"repo": "front", "url": MR_URL, "iid": 12}]}})
        self.assertEqual(self.tracking_config(), "")
        self.assert_allowed()

    def test_publish_runs_no_dev_hook(self):
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        marker = self.root / "marker"
        for name in ("pre-push", "reference-transaction", "post-checkout", "post-commit"):
            hook = self.repo / ".git" / "hooks" / name
            hook.write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n", encoding="utf-8")
            hook.chmod(0o755)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.remote(self.branch), self.head(self.worktree))
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 0, err)
        self.assertFalse(marker.exists())
        real_git(self.repo, "push", "-q", "origin", f"{self.branch}:refs/heads/witness")
        self.assertTrue(marker.exists())

    def test_publish_keeps_links_of_an_earlier_pass(self):
        back = {"repo": "back", "url": f"https://{HOST}/acme/back/-/merge_requests/7", "iid": 7}
        earlier = {"links": {"3": [back], "40": [back]}}
        (self.run_dir / "fix" / "mr-links.json").write_text(json.dumps(earlier), encoding="utf-8")
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        front = {"repo": "front", "url": MR_URL, "iid": 12}
        self.assertEqual(self.links(), {"links": {"3": [back, front], "40": [back]}})
        state = self.state()
        state["keys"][KEY].update(step="reviewed")
        fix_state.save(self.run_dir, state)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        self.assertEqual(self.links(), {"links": {"3": [back, front], "40": [back]}})
        (self.run_dir / "fix" / "mr-links.json").write_text("{", encoding="utf-8")
        state = self.state()
        state["keys"][KEY].update(step="reviewed")
        fix_state.save(self.run_dir, state)
        self.assertEqual(self.cli("publish", KEY)[0], 6)

    def test_publish_updates_an_existing_mr_instead_of_creating_one(self):
        old = ("## Contexte\n\nTexte retouché par le relecteur.\n\n## Hors-scope\n\n"
               "- CQ-40 : Bannière : Manuel\n\n## Suite\n\nNote du relecteur.\n")
        branch = self.open_mr(description=old)
        self.reviewed()
        self.assertEqual(self.branch, branch)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.glab_args("mr", "create"), [])
        self.assertIn(["api", "projects/acme%2Fshop/merge_requests/12"], self.glab_args("api"))
        updates = self.glab_args("mr", "update")
        self.assertEqual([args[2:5] for args in updates], [["12", "-R", REPO]])
        new = self.gitlab.mrs[12]["description"]
        today = date.today().strftime("%d/%m/%Y")
        self.assertTrue(new.startswith(
            f"## Contexte\n\nTexte retouché par le relecteur.\n\n## Ajouts du {today}\n\n"
            "### Correctifs\n\n#### CQ-3 : Ligne 3\n"))
        self.assertTrue(new.endswith(
            "## Hors-scope\n\n- CQ-5 : Ligne 5 : Demande une décision de l'équipe UI/UX.\n\n"
            "## Suite\n\nNote du relecteur.\n"))
        self.assertNotIn("CQ-40", new)
        self.assertEqual(self.remote(branch), self.head(self.worktree))
        result = json.loads(out)
        self.assertEqual((result["created"], result["updated"]), (False, True))
        self.assertEqual(self.links(), {"links": {"3": [{"repo": "front", "url": MR_URL, "iid": 12}]}})
        self.assertEqual((self.key()["mr"]["iid"], self.key()["step"]), (12, "published"))
        self.assert_allowed()

    def test_publish_uses_only_allowed_glab_commands(self):
        self.gitlab.create_output = "created\n"
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        self.assertEqual(self.key()["mr"], {"iid": 12, "url": MR_URL})
        self.assertEqual(self.cli("finish", KEY)[0], 0)
        self.assertEqual(self.start_ok()["branch"], self.branch)
        self.fixer_commit(self.worktree, "b5.txt", "fix(CQ-5): add b")
        self.write_doc("report-2.json", {"key": KEY, "pass": 2, "rows": [
            {"ref": 5, "outcome": "fixed", "change": "Ajoute b.", "uat": "Lire b.",
             "commits": [self.head(self.worktree)]}]})
        self.assertEqual(self.cli("validate", KEY, "report", "--pass", "2")[0], 0)
        self.write_doc("review-2.json", {"key": KEY, "pass": 2, "attention": ["Remarque 2."],
                                         "rows": [{"ref": 5, "verdict": "ok", "notes": None}]})
        self.assertEqual(self.cli("validate", KEY, "review", "--pass", "2")[0], 0)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        addition = (self.folder / "mr-addition.md").read_text(encoding="utf-8")
        self.assertIn("#### CQ-5 : Ligne 5", addition)
        self.assertNotIn("CQ-3", addition)
        self.assertIn("- Remarque 2.", addition)
        self.assertNotIn("Nommer la constante.", addition)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        self.gitlab.mrs[12]["state"] = "merged"
        state = self.state()
        state["keys"][KEY]["step"] = "reviewed"
        fix_state.save(self.run_dir, state)
        self.assertEqual(self.cli("publish", KEY)[0], 5)
        self.assertEqual(len(self.glab_args("mr", "create")), 1)
        self.assertEqual(len(self.glab_args("mr", "update")), 1)
        self.assertEqual(self.links()["links"]["5"], [{"repo": "front", "url": MR_URL, "iid": 12}])
        self.assert_allowed()

    def test_publish_refuses_a_diverged_branch(self):
        branch = self.open_mr()
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        reviewer = self.push_branch(branch, "Apply 1 suggestion(s) to 1 file(s)",
                                    start=f"origin/{branch}")
        self.gitlab.git.clear()
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 5)
        self.assertIn(DIVERGED, err)
        self.assertEqual(self.remote(branch), reviewer)
        fetched = [command for command in self.gitlab.git
                   if "fetch" in command and any(branch in arg for arg in command)]
        self.assertTrue(fetched)
        self.assertEqual(self.glab_args("mr"), [])
        self.assertEqual(self.key()["step"], "reviewed")
        self.assertFalse((self.run_dir / "fix" / "mr-links.json").exists())

    def test_publish_refuses_a_merged_or_closed_mr(self):
        branch = self.open_mr()
        before = self.remote(branch)
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        for state, word in (("merged", "fusionnée"), ("closed", "fermée")):
            with self.subTest(state=state):
                self.gitlab.mrs[12]["state"] = state
                code, out, err = self.cli("publish", KEY)
                self.assertEqual(code, 5)
                self.assertIn(
                    f"La MR !12 est {word} : relancez une passe fix pour repartir d'une nouvelle "
                    "branche.", err)
                self.assertEqual(self.remote(branch), before)
        self.assertEqual(self.glab_args("mr"), [])
        self.assertEqual(self.key()["step"], "reviewed")

    def test_publish_refuses_local_dry_run_and_not_ready_keys(self):
        for mode in ("local", "dry_run", "not_ready"):
            with self.subTest(mode=mode):
                if mode == "not_ready":
                    path = self.run_dir / "fix" / "candidates.json"
                    candidates = json.loads(path.read_text(encoding="utf-8"))
                    candidates["keys"][0]["push_ready"] = False
                    path.write_text(json.dumps(candidates), encoding="utf-8")
                self.reviewed(push="mr" if mode == "not_ready" else mode)
                code, out, err = self.cli("publish", KEY)
                self.assertEqual(code, 5, err)
                self.assertIn(self.branch, err)
                self.assertEqual(self.remote(self.branch), "")
                self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), self.fixed)
                self.assertEqual(self.key()["step"], "reviewed")
                self.assertEqual(self.gitlab.calls, [])
                code, out, err = self.cli("finish", KEY)
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["kept_local"], True)
                self.assertEqual(self.key()["step"], "done")
                self.assertFalse(self.worktree.exists())
                self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), self.fixed)
                self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)

    def test_publish_reruns_the_guard(self):
        self.reviewed()
        self.fixer_commit(self.worktree, "b.txt", "fix: forgot the ref")
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 5)
        self.assertEqual([problem["reason"] for problem in json.loads(out)["problems"]],
                         [BAD_MESSAGE])
        self.assertEqual(self.remote(self.branch), "")
        self.assertEqual(self.glab_args("mr"), [])
        self.assertEqual(self.key()["step"], "reviewed")

    def test_publish_refuses_a_stale_description(self):
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.fixer_commit(self.worktree, "b5.txt", "fix(CQ-5): add b")
        self.write_doc("report-2.json", {"key": KEY, "pass": 2, "rows": [
            {"ref": 5, "outcome": "fixed", "change": "Ajoute b.", "uat": "Lire b.",
             "commits": [self.head(self.worktree)]}]})
        self.assertEqual(self.cli("validate", KEY, "report", "--pass", "2")[0], 0)
        self.write_doc("review-2.json", {"key": KEY, "pass": 2, "attention": [],
                                         "rows": [{"ref": 5, "verdict": "ok", "notes": None}]})
        self.assertEqual(self.cli("validate", KEY, "review", "--pass", "2")[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 2)
        self.assertIn(STALE, err)
        self.assertEqual(self.remote(self.branch), "")
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("drop", KEY, "--refs", "CQ-5", "--reason", "Retirée.")[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 2)
        self.assertIn(STALE, err)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertIn("- CQ-5 : Ligne 5 : Retirée.", self.gitlab.mrs[12]["description"])
        self.assertEqual(self.glab_args("mr", "update"), [])

    def test_publish_checks_a_taken_over_branch_never_pushed(self):
        branch = PREFIX + "securite"
        real_git(self.repo, "checkout", "-q", "-b", branch)
        commit(self.repo, "s3.txt", "fix(CQ-3): earlier fix")
        commit(self.repo, "s40.txt", "fix(CQ-40): earlier fix of another row")
        oops = commit(self.repo, "oops.txt", "oops: no ref")
        real_git(self.repo, "checkout", "-q", "develop")
        base = self.head(self.repo, "refs/remotes/origin/develop")
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3, 40]}]))
        self.open_pass()
        started = self.start_ok()
        self.assertEqual((started["reused"], started["start_sha"], started["done_refs"]),
                         ("branch", base, [3, 40]))
        key = self.key()
        self.assertEqual((key["taken_head"], key["rows"]["3"]["outcome"]), (oops, "done_before"))
        self.worktree, self.branch = Path(started["worktree"]), started["branch"]
        self.fix_row_5()
        code, out, err = self.cli("guard", KEY)
        self.assertEqual(code, 5)
        expected = [{"sha": oops, "ref": None, "path": None, "reason": BAD_MESSAGE}]
        self.assertEqual(json.loads(out)["problems"], expected)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(out)["problems"], expected)
        self.assertEqual(self.remote(branch), "")
        self.assertEqual(self.glab_args("mr"), [])

    def test_publish_links_the_rows_already_on_the_branch(self):
        branch = PREFIX + "securite"
        real_git(self.repo, "checkout", "-q", "-b", branch)
        commit(self.repo, "s3.txt", "fix(CQ-3): earlier fix")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3]}]))
        self.open_pass()
        started = self.start_ok()
        self.worktree, self.branch = Path(started["worktree"]), started["branch"]
        self.fix_row_5()
        code, out, err = self.cli("mr-body", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["title"], "Checklist PRE-MEL : correctifs SECURITE (2 lignes)")
        text = (self.folder / "mr.md").read_text(encoding="utf-8")
        self.assertIn("La branche porte aussi des correctifs d'une passe précédente : CQ-3 (Ligne 3).",
                      text)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.remote(branch), self.head(self.worktree))
        link = [{"repo": "front", "url": MR_URL, "iid": 12}]
        self.assertEqual(self.links(), {"links": {"3": link, "5": link}})
        self.assertEqual(json.loads(out)["links"], [3, 5])

    def fix_row_5(self):
        """Fix CQ-5 in pass 1 of a key whose CQ-3 is done_before, then review: reviewed."""
        sha = self.fixer_commit(self.worktree, "b5.txt", "fix(CQ-5): add b")
        self.write_doc("report-1.json", {"key": KEY, "pass": 1, "rows": [
            {"ref": 5, "outcome": "fixed", "change": "Ajoute b.", "uat": "Lire b.", "commits": [sha]}]})
        self.assertEqual(self.cli("validate", KEY, "report", "--pass", "1")[0], 0)
        self.write_doc("review-1.json", {"key": KEY, "pass": 1, "attention": [],
                                         "rows": [{"ref": 5, "verdict": "ok", "notes": None}]})
        self.assertEqual(self.cli("validate", KEY, "review", "--pass", "1")[0], 0)
        self.assertEqual(self.key()["step"], "reviewed")

    def test_publish_and_finish_follow_the_steps(self):
        self.open_pass()
        self.start_ok()
        for command in ("mr-body", "publish", "finish"):
            with self.subTest(command=command):
                code, out, err = self.cli(command, KEY)
                self.assertEqual(code, 2, err)
                self.assertIn("started", err)
        self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)
        self.reviewed()
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 2)
        self.assertIn("published", err)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 2)
        self.assertIn("cqa_fix.py mr-body", err)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 2)
        self.assertIn("published", err)
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["removed"], result["branch"], result["kept_local"]),
                         (str(self.worktree), self.branch, False))
        self.assertEqual(self.key()["step"], "done")
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), self.fixed)
        self.assertEqual(self.remote(self.branch), self.fixed)
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 2)

    def test_publish_takes_over_an_open_mr_found_on_gitlab(self):
        self.reviewed()
        self.gitlab.add(13, self.branch, "Écrite à la main.")
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 2)
        self.assertIn("La MR !13 est déjà ouverte", err)
        self.assertIn("cqa_fix.py mr-body", err)
        self.assertEqual(self.remote(self.branch), "")
        self.assertEqual(self.key()["mr"], {"iid": 13, "url": self.gitlab.url(13)})
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertTrue((self.folder / "mr-addition.md").is_file())
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertTrue(self.gitlab.mrs[13]["description"].startswith("Écrite à la main.\n\n## Ajouts du "))
        self.assertEqual(self.glab_args("mr", "create"), [])
        self.assertEqual(self.remote(self.branch), self.head(self.worktree))
        self.assert_allowed()

    def test_publish_adopts_its_own_mr_after_an_interruption(self):
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        state = self.state()
        state["keys"][KEY].update(step="reviewed", mr=None)
        fix_state.save(self.run_dir, state)
        (self.run_dir / "fix" / "mr-links.json").unlink()
        ours = (self.folder / "mr.md").read_text(encoding="utf-8")
        stored = ours.replace("\n", "  \r\n")
        self.gitlab.mrs[12]["description"] = stored
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.glab_args("mr", "create")), 1)
        self.assertEqual(self.glab_args("mr", "update"), [])
        self.assertEqual(self.key()["mr"], {"iid": 12, "url": MR_URL})
        self.assertEqual(self.links(), {"links": {"3": [{"repo": "front", "url": MR_URL, "iid": 12}]}})
        self.assertEqual(self.gitlab.mrs[12]["description"], stored)

    def test_publish_resumes_after_a_failure_once_the_mr_exists(self):
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        real_write = cqa_fix.write_json

        def failing(path, value):
            if Path(path).name == "mr-links.json":
                raise OSError("disque plein")
            real_write(path, value)

        with mock.patch.object(cqa_fix, "write_json", failing):
            code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 2)
        self.assertIn("disque plein", err)
        key = self.key()
        self.assertEqual((key["mr"], key["step"]), (None, "reviewed"))
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.glab_args("mr", "create")), 1)
        self.assertEqual(self.glab_args("mr", "update"), [])
        self.assertEqual(self.gitlab.mrs[12]["description"].count("### CQ-3 : Ligne 3"), 1)
        self.assertEqual((self.key()["mr"], self.key()["step"]), ({"iid": 12, "url": MR_URL}, "published"))
        self.assertEqual(self.links(), {"links": {"3": [{"repo": "front", "url": MR_URL, "iid": 12}]}})

    def test_publish_without_any_commit_publishes_nothing(self):
        self.reviewed(fix=False)
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        code, out, err = self.cli("publish", KEY)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["pushed"], result["mr"], result["created"]), (False, None, False))
        self.assertEqual(self.remote(self.branch), "")
        self.assertEqual(self.gitlab.calls, [])
        self.assertEqual(self.key()["step"], "published")
        self.assertFalse((self.run_dir / "fix" / "mr-links.json").exists())


class FinishTest(MrCase):
    def test_finish_refuses_a_dirty_worktree(self):
        self.reviewed(push="local")
        (self.worktree / "draft.txt").write_text("uncommitted", encoding="utf-8")
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 5)
        self.assertIn("non commitées", err)
        self.assertTrue(self.worktree.is_dir())
        self.assertEqual(self.key()["step"], "reviewed")
        (self.worktree / "draft.txt").unlink()
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 0, err)
        self.assertFalse(self.worktree.exists())
        self.assertNotIn(self.worktree.resolve(), self.worktree_paths())
        self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), self.fixed)
        self.assertEqual(self.key()["step"], "done")

    def test_finish_keep_local_after_a_refused_publish(self):
        self.reviewed()
        self.fixer_commit(self.worktree, "b.txt", "fix: forgot the ref")
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 5)
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 2)
        self.assertIn("--keep-local", err)
        head = self.head(self.worktree)
        code, out, err = self.cli("finish", KEY, "--keep-local")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["removed"], result["kept_local"], result["unpushed"]),
                         (str(self.worktree), True, 2))
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), head)
        self.assertEqual(self.remote(self.branch), "")
        key = self.key()
        self.assertEqual((key["step"], key["kept_local"]), ("done", True))

    def test_finish_keep_local_refuses_a_published_key(self):
        self.reviewed()
        self.assertEqual(self.cli("mr-body", KEY)[0], 0)
        self.assertEqual(self.cli("publish", KEY)[0], 0)
        code, out, err = self.cli("finish", KEY, "--keep-local")
        self.assertEqual(code, 2)
        self.assertIn("déjà publiée", err)
        self.assertTrue(self.worktree.is_dir())
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["kept_local"], False)

    def test_finish_after_the_worktree_is_gone(self):
        self.reviewed(push="local")
        real_git(self.repo, "worktree", "remove", str(self.worktree))
        code, out, err = self.cli("finish", KEY)
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(out)["removed"])
        self.assertEqual(self.key()["step"], "done")


if __name__ == "__main__":
    unittest.main()
