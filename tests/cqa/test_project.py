import tempfile
import unittest
from pathlib import Path

from cqa.project import parse_project_md, project_slug, sibling_repos

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParseProjectMdTest(unittest.TestCase):
    def test_parse_fixture(self):
        p = parse_project_md(read("PROJECT.md"))
        self.assertEqual(p["lead"], "Julien Durand")
        self.assertEqual(p["devs"][1], {"name": "Léa Martin", "role": "front"})
        self.assertEqual(p["branches"], {"prod": "master", "preprod": "release"})

    def test_fixture_values(self):
        p = parse_project_md(read("PROJECT.md"))
        self.assertEqual(
            p["devs"],
            [
                {"name": "Julien Durand", "role": "fullstack"},
                {"name": "Léa Martin", "role": "front"},
                {"name": "Vincent Roux", "role": "back"},
            ],
        )
        self.assertEqual(p["prod_url"], "https://www.example.org/")
        self.assertEqual(p["preprod_url"], "https://preprod.example.org/")
        self.assertEqual(p["protection"], "Basic Auth sur preprod – creds dans Vaultwarden")
        self.assertEqual(p["repo_url"], "https://gitlab.example.org/acme/acme-front")
        self.assertEqual(p["vaultwarden"], "GM > Acme")

    def test_exact_keys(self):
        self.assertEqual(
            sorted(parse_project_md("")),
            sorted(
                [
                    "lead",
                    "devs",
                    "prod_url",
                    "preprod_url",
                    "protection",
                    "repo_url",
                    "branches",
                    "vaultwarden",
                ]
            ),
        )

    def test_empty_text(self):
        self.assertEqual(
            parse_project_md(""),
            {
                "lead": None,
                "devs": [],
                "prod_url": None,
                "preprod_url": None,
                "protection": None,
                "repo_url": None,
                "branches": {"prod": None, "preprod": None},
                "vaultwarden": None,
            },
        )

    def test_label_without_colon(self):
        text = (
            "- **Branches Git**\n"
            "    - Branche prod : `main`\n"
            "    - Branche preprod : `staging`\n"
        )
        self.assertEqual(
            parse_project_md(text)["branches"], {"prod": "main", "preprod": "staging"}
        )

    def test_label_with_colon_after_the_bold_title(self):
        text = (
            "- **Branches Git** :\n"
            "    - Branche prod : `main`\n"
            "    - Branche preprod : `staging`\n"
        )
        self.assertEqual(
            parse_project_md(text)["branches"], {"prod": "main", "preprod": "staging"}
        )

    def test_colon_inside_the_bold_label(self):
        self.assertEqual(parse_project_md("- **Lead technique :** Anna Roy\n")["lead"], "Anna Roy")

    def test_bold_label_without_any_colon(self):
        self.assertEqual(parse_project_md("- **Lead technique** Anna Roy\n")["lead"], "Anna Roy")

    def test_plain_labels_and_other_bullet_markers(self):
        text = "* Lead technique : Anna Roy\n* URL du repo : https://gitlab.example.org/acme/app\n"
        p = parse_project_md(text)
        self.assertEqual(p["lead"], "Anna Roy")
        self.assertEqual(p["repo_url"], "https://gitlab.example.org/acme/app")

    def test_labels_ignore_case_and_accents(self):
        text = "- **ACCES PROTEGES ?** : Non\n- **url production** : https://www.example.org\n"
        p = parse_project_md(text)
        self.assertEqual(p["protection"], "Non")
        self.assertEqual(p["prod_url"], "https://www.example.org")

    def test_label_must_match_in_full(self):
        text = (
            "- **Lead technique adjoint** : Anna Roy\n"
            "- **Autres branches longue durée** : `develop`\n"
        )
        p = parse_project_md(text)
        self.assertIsNone(p["lead"])
        self.assertEqual(p["branches"], {"prod": None, "preprod": None})

    def test_nested_bullets_with_tabs_and_bold_labels(self):
        text = (
            "- **Branches Git**\n"
            "\t- **Branche prod** : `main`\n"
            "        - **Branche preprod** : `staging`\n"
        )
        self.assertEqual(
            parse_project_md(text)["branches"], {"prod": "main", "preprod": "staging"}
        )

    def test_backticks_are_removed(self):
        text = "- **URL du repo** : `https://gitlab.example.org/acme/app`\n"
        self.assertEqual(parse_project_md(text)["repo_url"], "https://gitlab.example.org/acme/app")

    def test_na_and_empty_values_are_none(self):
        text = (
            "- **URL Production** : N/A\n"
            "- **URL Preprod / Staging** :\n"
            "- **Accès protégés ?** : n/a\n"
            "- **URL du repo** : ``\n"
            "- **Collection Vaultwarden**\n"
            "- **Lead technique** : `N/A`\n"
        )
        p = parse_project_md(text)
        for key in ("prod_url", "preprod_url", "protection", "repo_url", "vaultwarden", "lead"):
            self.assertIsNone(p[key], key)

    def test_na_branch_is_none(self):
        text = "- **Branches Git** :\n    - Branche prod : `master`\n    - Branche preprod : N/A\n"
        self.assertEqual(parse_project_md(text)["branches"], {"prod": "master", "preprod": None})

    def test_value_may_contain_colons(self):
        text = "- **URL Production** : https://www.example.org:8443/\n"
        self.assertEqual(parse_project_md(text)["prod_url"], "https://www.example.org:8443/")

    def test_first_occurrence_wins(self):
        text = "- **Lead technique** : Anna Roy\n- **Lead technique** : Ben Roy\n"
        self.assertEqual(parse_project_md(text)["lead"], "Anna Roy")

    def test_crlf_line_endings(self):
        text = (
            "- **Lead technique** : Anna Roy\r\n"
            "- **URL du repo** : https://gitlab.example.org/acme/app\r\n"
        )
        p = parse_project_md(text)
        self.assertEqual(p["lead"], "Anna Roy")
        self.assertEqual(p["repo_url"], "https://gitlab.example.org/acme/app")

    def test_dev_roles(self):
        text = (
            "- **Devs principaux** : Ana Roy (full-stack), Ben Roy (front), Cy Roy (back),"
            " Di Roy, Ed Roy (Front-End), Flo Roy (Back end), Gus Roy (fullstack),"
            " Hal Roy (design)\n"
        )
        self.assertEqual(
            parse_project_md(text)["devs"],
            [
                {"name": "Ana Roy", "role": "fullstack"},
                {"name": "Ben Roy", "role": "front"},
                {"name": "Cy Roy", "role": "back"},
                {"name": "Di Roy", "role": None},
                {"name": "Ed Roy", "role": "front"},
                {"name": "Flo Roy", "role": "back"},
                {"name": "Gus Roy", "role": "fullstack"},
                {"name": "Hal Roy", "role": None},
            ],
        )

    def test_devs_with_odd_separators(self):
        text = "- **Devs principaux** : Ana Roy (front) ,  Ben Roy (back); ,Cy Roy\n"
        self.assertEqual(
            parse_project_md(text)["devs"],
            [
                {"name": "Ana Roy", "role": "front"},
                {"name": "Ben Roy", "role": "back"},
                {"name": "Cy Roy", "role": None},
            ],
        )

    def test_comma_inside_a_role_does_not_split(self):
        text = "- **Devs principaux** : Ana Roy (front, back), Ben Roy (back)\n"
        self.assertEqual(
            parse_project_md(text)["devs"],
            [{"name": "Ana Roy", "role": None}, {"name": "Ben Roy", "role": "back"}],
        )

    def test_no_devs(self):
        for value in ("N/A", "", "`N/A`"):
            self.assertEqual(parse_project_md(f"- **Devs principaux** : {value}\n")["devs"], [])

    def test_unrelated_lines_are_ignored(self):
        text = "# PROJECT.md\n\nProse: with a colon\n- **Nom du client** : Acme\n- plain bullet\n"
        self.assertEqual(parse_project_md(text), parse_project_md(""))


class ProjectSlugTest(unittest.TestCase):
    def test_slug(self):
        names = ["Acme - Shop - Laravel", "Acme - Shop - Nuxt 3"]
        self.assertEqual(project_slug(names), "acme-shop")
        self.assertEqual(project_slug(["Acme - Nuxt"]), "acme")

    def test_single_repo_with_client_project_techno(self):
        self.assertEqual(project_slug(["Acme - Shop - Laravel"]), "acme-shop")

    def test_name_without_techno_part(self):
        self.assertEqual(project_slug(["Acme"]), "acme")

    def test_only_the_common_leading_parts_are_kept(self):
        self.assertEqual(project_slug(["Acme - Shop - Laravel", "Acme - Blog - Nuxt"]), "acme")

    def test_ascii_lowercase_and_dashes(self):
        slug = project_slug(["Société Élan & Co - Boutique (v2) - Drupal"])
        self.assertEqual(slug, "societe-elan-co-boutique-v2")

    def test_runs_of_separators_collapse(self):
        self.assertEqual(project_slug(["Acme  -  Shop - Nuxt"]), "acme-shop")

    def test_no_common_part_is_refused(self):
        with self.assertRaises(ValueError):
            project_slug(["Acme - Nuxt", "Beta - Nuxt"])

    def test_no_names_is_refused(self):
        with self.assertRaises(ValueError):
            project_slug([])


class SiblingReposTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def make(self, name, git=True):
        path = self.root / name
        path.mkdir()
        if git:
            (path / ".git").mkdir()
        return path

    def test_siblings_with_the_same_client_and_project(self):
        laravel = self.make("Acme - Shop - Laravel")
        nuxt = self.make("Acme - Shop - Nuxt 3")
        self.make("Acme - Shop - Docs", git=False)
        self.make("Acme - Blog - Nuxt")
        self.make("Acme - Shopping - Nuxt")
        self.make("Beta - Shop - Laravel")
        (self.root / "Acme - Shop - Notes").write_text("not a directory", encoding="utf-8")
        self.assertEqual(sibling_repos(laravel), [nuxt])

    def test_the_repo_itself_is_excluded_and_results_are_sorted(self):
        nuxt = self.make("Acme - Shop - Nuxt 3")
        laravel = self.make("Acme - Shop - Laravel")
        drupal = self.make("Acme - Shop - Drupal")
        self.assertEqual(sibling_repos(nuxt), [drupal, laravel])

    def test_git_may_be_a_file(self):
        laravel = self.make("Acme - Shop - Laravel")
        linked = self.make("Acme - Shop - Nuxt", git=False)
        (linked / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        self.assertEqual(sibling_repos(laravel), [linked])

    def test_client_and_techno_only(self):
        nuxt = self.make("Acme - Nuxt")
        laravel = self.make("Acme - Laravel")
        self.make("Beta - Laravel")
        self.assertEqual(sibling_repos(nuxt), [laravel])

    def test_no_sibling(self):
        laravel = self.make("Acme - Shop - Laravel")
        self.assertEqual(sibling_repos(laravel), [])

    def test_name_without_separator_matches_the_client_prefix(self):
        client = self.make("Acme")
        nuxt = self.make("Acme - Nuxt")
        self.assertEqual(sibling_repos(client), [nuxt])


if __name__ == "__main__":
    unittest.main()
