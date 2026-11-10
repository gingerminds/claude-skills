import unittest

from cqa import categories, fix_names


def make_rows():
    return [
        {"ref": 1, "category": "ACCESSIBILITÉ"},
        {"ref": 2, "category": "SÉCURITÉ"},
        {"ref": 3, "category": "ACCESSIBILITÉ"},
        {"ref": 4, "category": "SEO / GEO"},
        {"ref": 5, "category": None},
        {"ref": 6},
    ]


class FoldTest(unittest.TestCase):
    def test_fold_drops_accents_case_and_separators(self):
        self.assertEqual(categories.fold("ACCESSIBILITÉ"), "accessibilite")
        self.assertEqual(categories.fold("SEO / GEO"), "seo geo")
        self.assertEqual(categories.fold("  ÉCO-CONCEPTION /  WEBPERFORMANCE "), "eco conception webperformance")
        self.assertEqual(categories.fold("RGPD & LÉGAL"), "rgpd legal")

    def test_fold_of_decomposed_accents_and_of_nothing(self):
        self.assertEqual(categories.fold("Sécurité"), "securite")
        self.assertEqual(categories.fold(""), "")
        self.assertEqual(categories.fold(None), "")


class AvailableTest(unittest.TestCase):
    def test_available_in_order_of_first_appearance(self):
        self.assertEqual(
            categories.available(make_rows()),
            [
                {"name": "ACCESSIBILITÉ", "alias": "accessibilite", "rows": 2},
                {"name": "SÉCURITÉ", "alias": "securite", "rows": 1},
                {"name": "SEO / GEO", "alias": "seo", "rows": 1},
            ],
        )

    def test_alias_is_the_one_of_the_fix_sections(self):
        for entry in categories.available(make_rows()):
            self.assertEqual(entry["alias"], fix_names.section_alias(entry["name"]))

    def test_available_without_rows(self):
        self.assertEqual(categories.available([]), [])


class ResolveTest(unittest.TestCase):
    def test_resolve_accepts_notion_name_alias_accents_and_case(self):
        rows = make_rows()
        self.assertEqual(categories.resolve(["accessibilite"], rows), (["ACCESSIBILITÉ"], []))
        self.assertEqual(
            categories.resolve(["Securite", "seo"], rows), (["SÉCURITÉ", "SEO / GEO"], [])
        )
        self.assertEqual(categories.resolve(["a11y"], rows), ([], ["a11y"]))

    def test_resolve_accepts_the_exact_notion_name(self):
        rows = make_rows()
        self.assertEqual(categories.resolve(["SEO / GEO"], rows), (["SEO / GEO"], []))
        self.assertEqual(categories.resolve(["seo-geo"], rows), (["SEO / GEO"], []))
        self.assertEqual(categories.resolve(["ACCESSIBILITÉ"], rows), (["ACCESSIBILITÉ"], []))

    def test_resolve_returns_the_names_in_the_order_of_the_rows(self):
        rows = make_rows()
        self.assertEqual(
            categories.resolve(["seo", "securite", "accessibilite"], rows),
            (["ACCESSIBILITÉ", "SÉCURITÉ", "SEO / GEO"], []),
        )

    def test_resolve_dedupes_and_lists_every_unknown_name_in_the_asked_order(self):
        rows = make_rows()
        self.assertEqual(
            categories.resolve(["securite", "SÉCURITÉ", "zzz", "a11y", "zzz"], rows),
            (["SÉCURITÉ"], ["zzz", "a11y"]),
        )

    def test_resolve_refuses_an_empty_name(self):
        self.assertEqual(categories.resolve(["", "  "], make_rows()), ([], ["", "  "]))

    def test_an_exact_name_wins_over_a_shared_alias(self):
        rows = [{"category": "ÉCO-CONCEPTION"}, {"category": "ÉCO-RESPONSABLE"}]
        self.assertEqual(categories.resolve(["eco"], rows), (["ÉCO-CONCEPTION", "ÉCO-RESPONSABLE"], []))
        self.assertEqual(categories.resolve(["eco conception"], rows), (["ÉCO-CONCEPTION"], []))


class InScopeTest(unittest.TestCase):
    def test_in_scope_without_filter_and_for_a_row_without_category(self):
        row = {"ref": 1, "category": "SÉCURITÉ"}
        self.assertTrue(categories.in_scope(row, None))
        self.assertTrue(categories.in_scope(row, []))
        self.assertTrue(categories.in_scope({"ref": 2}, None))
        self.assertTrue(categories.in_scope({"ref": 2, "category": None}, None))
        self.assertFalse(categories.in_scope({"ref": 2}, ["SÉCURITÉ"]))
        self.assertFalse(categories.in_scope({"ref": 2, "category": None}, ["SÉCURITÉ"]))

    def test_in_scope_with_a_filter_compares_the_notion_name(self):
        row = {"ref": 1, "category": "SÉCURITÉ"}
        self.assertTrue(categories.in_scope(row, ["SÉCURITÉ", "QUALITÉ"]))
        self.assertFalse(categories.in_scope(row, ["QUALITÉ"]))
        self.assertFalse(categories.in_scope(row, ["securite"]))


if __name__ == "__main__":
    unittest.main()
