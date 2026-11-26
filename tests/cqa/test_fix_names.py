import unittest

from cqa import fix_names


class SectionAliasTest(unittest.TestCase):
    def test_section_aliases(self):
        for name, alias in (
            ("SÉCURITÉ", "securite"),
            ("QUALITÉ", "qualite"),
            ("ACCESSIBILITÉ", "accessibilite"),
            ("SEO / GEO", "seo"),
            ("ÉCO-CONCEPTION / WEBPERFORMANCE", "eco"),
            ("RGPD & LÉGAL", "rgpd"),
            ("PROCESS INTERNE", "process"),
            ("QA DIVERSE", "qa"),
        ):
            self.assertEqual(fix_names.section_alias(name), alias)
        self.assertEqual(len(fix_names.SECTION_ALIASES), 7)

    def test_subject_max(self):
        self.assertEqual(fix_names.SUBJECT_MAX, 72)


class ParseSubjectTest(unittest.TestCase):
    def test_parse_subject(self):
        self.assertEqual(fix_names.parse_subject("fix(securite CQ-107): add headers"), ("securite", 107))
        self.assertEqual(fix_names.parse_subject("fix(CQ-107): add headers"), (None, 107))
        for subject in ("fix(securite): x", "fix(securite CQ-107):x", "Merge branch 'develop'"):
            self.assertIsNone(fix_names.parse_subject(subject))

    def test_the_guard_subject_is_the_015_form(self):
        self.assertTrue(fix_names.SUBJECT.match("fix(securite CQ-107): add headers"))
        for subject in ("fix(CQ-107): add headers", "fix(Securite CQ-107): x", "wip(seo CQ-1): x"):
            self.assertIsNone(fix_names.SUBJECT.match(subject))

    def test_ref_of_subject_accepts_both_forms(self):
        self.assertEqual(fix_names.ref_of_subject("feat(seo CQ-3): x"), 3)
        self.assertEqual(fix_names.ref_of_subject("feat(CQ-3): x"), 3)
        self.assertIsNone(fix_names.ref_of_subject("feat(seo): x"))


class InstallSubjectTest(unittest.TestCase):
    def subject(self, form, packages, alias="securite", ref=3):
        names = [item.rsplit("@", 1)[0] if item.rfind("@") > 0 else item for item in packages]
        return fix_names.install_subject(alias, ref, form, names, packages)

    def test_install_subjects_follow_the_guard_convention(self):
        for found, expected in (
            (self.subject("dev", ["stylelint"]),
             "chore(securite CQ-3): add stylelint as a dev dependency"),
            (self.subject("dev", ["stylelint@16.0.0", "@scope/tool@^1"]),
             "chore(securite CQ-3): add stylelint, @scope/tool as a dev dependency"),
            (self.subject("exact", ["lodash@4.17.21"]), "chore(securite CQ-3): pin lodash to 4.17.21"),
            (self.subject("lock", []), "chore(securite CQ-3): update the lock file"),
        ):
            self.assertEqual(found, expected)
            self.assertTrue(fix_names.SUBJECT.match(found), found)
            self.assertEqual(fix_names.parse_subject(found), ("securite", 3))

    def test_a_long_list_of_packages_is_counted_to_stay_within_the_limit(self):
        names = [f"a-rather-long-package-name-{number}" for number in range(4)]
        found = fix_names.install_subject("accessibilite", 12345, "dev", names, names)
        self.assertEqual(found, "chore(accessibilite CQ-12345): add 4 packages as a dev dependency")
        self.assertLessEqual(len(found), fix_names.SUBJECT_MAX)
        found = fix_names.install_subject("securite", 3, "exact", ["x" * 80],
                                          ["x" * 80 + "@1.0.0"])
        self.assertEqual(found, "chore(securite CQ-3): pin a dependency to an exact version")
        for alias, ref in (("a", 1), ("accessibilite", 99999)):
            for form in ("dev", "exact", "lock"):
                found = fix_names.install_subject(alias, ref, form, names, [f"{n}@1" for n in names])
                self.assertLessEqual(len(found), fix_names.SUBJECT_MAX, found)
                self.assertTrue(fix_names.SUBJECT.match(found), found)


class DocumentationTest(unittest.TestCase):
    def test_documentation_files(self):
        for path in ("README.md", "docs/guide.rst", "docs/intro.adoc", "CHANGELOG.MD"):
            self.assertTrue(fix_names.is_documentation(path), path)
        for path in ("robots.txt", "web/.well-known/security.txt", "llms.txt", "ads.txt",
                     "notes.txt", "README", "docs/md", "index.php"):
            self.assertFalse(fix_names.is_documentation(path), path)


class BranchTest(unittest.TestCase):
    def test_pass_and_fix_branches(self):
        base = fix_names.BRANCH_BASE
        self.assertEqual(base, "feature/checklist-qa-fix")
        self.assertEqual(fix_names.DRY_RUN_BASE, "dry-run/checklist-qa-fix")
        for name in (base, base + "-2"):
            self.assertTrue(fix_names.is_pass_branch(name, base))
            self.assertTrue(fix_names.is_fix_branch(name))
        for name in (base + "-securite", base + "-seo-geo-2"):
            self.assertFalse(fix_names.is_pass_branch(name, base))
            self.assertTrue(fix_names.is_fix_branch(name))
        self.assertTrue(fix_names.is_pass_branch("dry-run/checklist-qa-fix-3", fix_names.DRY_RUN_BASE))
        self.assertFalse(fix_names.is_fix_branch("dry-run/checklist-qa-fix-3"))
        self.assertFalse(fix_names.is_fix_branch("feature/other"))
        self.assertFalse(fix_names.is_fix_branch(base + "x"))
