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

    def test_ref_of_subject_accepts_both_forms(self):
        self.assertEqual(fix_names.ref_of_subject("feat(seo CQ-3): x"), 3)
        self.assertEqual(fix_names.ref_of_subject("feat(CQ-3): x"), 3)
        self.assertIsNone(fix_names.ref_of_subject("feat(seo): x"))


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
