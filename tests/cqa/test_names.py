import unittest

from cqa.checklist import (
    STATUS_IMPLEMENTED,
    STATUS_NA,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_STANDBY,
    STATUS_TODO,
)
from cqa.names import match_option, normalize, propose_assignment

OPTIONS = ["Inès", "Côme D", "Côme L", "Éloi", "Aurèle"]


def row(ref, sections, status=STATUS_TODO, **extra):
    return {
        "page_id": f"page-{ref}",
        "ref": ref,
        "title": f"Task {ref}",
        "status": status,
        "sections": sections,
        **extra,
    }


TEAM = [
    {"name": "Côme Dorval", "role": "fullstack", "option": "Côme D"},
    {"name": "Inès Morel", "role": "front", "option": "Inès"},
    {"name": "Aurèle Roussel", "role": "back", "option": "Aurèle"},
]


def assignees(proposal):
    return {item["ref"]: item["assignee"] for item in proposal}


class NormalizeTest(unittest.TestCase):
    def test_strips_accents_case_and_spaces(self):
        self.assertEqual(normalize("  Éloi  DUPRAT "), "eloi duprat")

    def test_collapses_any_whitespace_including_nbsp(self):
        self.assertEqual(normalize("Inès\xa0\tMorel"), "ines morel")

    def test_decomposed_accents(self):
        self.assertEqual(normalize("Inès"), "ines")

    def test_empty(self):
        self.assertEqual(normalize(""), "")


class MatchOptionTest(unittest.TestCase):
    def test_match_option(self):
        self.assertEqual(match_option("Côme Lestrade", OPTIONS), "Côme L")
        self.assertEqual(match_option("Eloi Duprat", OPTIONS), "Éloi")
        self.assertEqual(match_option("Ines Morel", OPTIONS), "Inès")

    def test_match_option_unknown(self):
        self.assertIsNone(match_option("Zoé Petit", OPTIONS))

    def test_same_first_name_needs_the_initial(self):
        self.assertEqual(match_option("côme dorval", OPTIONS), "Côme D")
        self.assertIsNone(match_option("Côme Marchal", OPTIONS))

    def test_first_name_alone_is_ambiguous_with_homonyms(self):
        self.assertIsNone(match_option("Côme", OPTIONS))

    def test_first_name_alone_matches_a_single_bare_option(self):
        self.assertEqual(match_option("Aurèle", OPTIONS), "Aurèle")

    def test_single_bare_option_wins_whatever_the_surname(self):
        self.assertEqual(match_option("Aurèle Roussel", OPTIONS), "Aurèle")

    def test_bare_option_and_homonym_with_initial(self):
        options = ["Inès", "Inès B"]
        self.assertEqual(match_option("Inès Bastide", options), "Inès B")
        self.assertIsNone(match_option("Inès Morel", options))

    def test_single_option_with_an_initial_still_needs_the_initial(self):
        self.assertEqual(match_option("Côme Dorval", ["Côme D"]), "Côme D")
        self.assertIsNone(match_option("Côme Lestrade", ["Côme D"]))

    def test_first_name_must_be_the_whole_first_word(self):
        self.assertIsNone(match_option("Cômelie Dorval", OPTIONS))

    def test_no_options_or_empty_name(self):
        self.assertIsNone(match_option("Inès Morel", []))
        self.assertIsNone(match_option("   ", OPTIONS))


class ProposeAssignmentTest(unittest.TestCase):
    def test_propose_assignment(self):
        rows = [
            row(1, ["Front"]),
            row(2, ["Front"]),
            row(3, ["Back"]),
            row(4, ["Back"]),
            row(5, ["Front", "Back"]),
            row(6, ["Front"], status=STATUS_IMPLEMENTED),
        ]
        proposal = propose_assignment(rows, TEAM, "Côme D")
        self.assertEqual(
            assignees(proposal),
            {1: "Côme D", 2: "Inès", 3: "Aurèle", 4: "Côme D", 5: "Côme D"},
        )

    def test_item_shape_and_input_order(self):
        rows = [row(9, ["Back"]), row(3, ["Front", "Back"], title="Both")]
        proposal = propose_assignment(rows, TEAM, "Côme D")
        self.assertEqual(
            proposal,
            [
                {
                    "ref": 9,
                    "page_id": "page-9",
                    "title": "Task 9",
                    "sections": ["Back"],
                    "assignee": "Côme D",
                },
                {
                    "ref": 3,
                    "page_id": "page-3",
                    "title": "Both",
                    "sections": ["Front", "Back"],
                    "assignee": "Côme D",
                },
            ],
        )

    def test_kept_statuses(self):
        rows = [
            row(1, ["Front"], status=STATUS_TODO),
            row(2, ["Front"], status=STATUS_PARTIAL),
            row(3, ["Front"], status=STATUS_STANDBY),
            row(4, ["Front"], status=STATUS_IMPLEMENTED),
            row(5, ["Front"], status=STATUS_OK),
            row(6, ["Front"], status=STATUS_NA),
            row(7, ["Front"], status=None),
        ]
        proposal = propose_assignment(rows, TEAM, "Côme D")
        self.assertEqual([item["ref"] for item in proposal], [1, 2])

    def test_unknown_rows_only_when_asked(self):
        rows = [
            row(1, ["Back"], status=STATUS_IMPLEMENTED, verdict="unknown"),
            row(2, ["Back"], status=STATUS_IMPLEMENTED, verdict="ok"),
            row(3, ["Back"], status=STATUS_TODO, verdict="unknown"),
        ]
        self.assertEqual([item["ref"] for item in propose_assignment(rows, TEAM, "Côme D")], [3])
        proposal = propose_assignment(rows, TEAM, "Côme D", include_unknown=True)
        self.assertEqual([item["ref"] for item in proposal], [1, 3])

    def test_front_only_candidates(self):
        team = [
            {"name": "Back Dev", "role": "back", "option": "Aurèle"},
            {"name": "Front Dev", "role": "front", "option": "Inès"},
        ]
        proposal = propose_assignment([row(1, ["Front"]), row(2, ["Front"])], team, "Côme D")
        self.assertEqual(assignees(proposal), {1: "Inès", 2: "Inès"})

    def test_back_only_candidates(self):
        team = [
            {"name": "Front Dev", "role": "front", "option": "Inès"},
            {"name": "Back Dev", "role": "back", "option": "Aurèle"},
        ]
        proposal = propose_assignment([row(1, ["Back"]), row(2, ["Back"])], team, "Côme D")
        self.assertEqual(assignees(proposal), {1: "Aurèle", 2: "Aurèle"})

    def test_front_and_back_prefers_fullstack(self):
        rows = [row(1, ["Front", "Back"]), row(2, ["Back", "Front"])]
        proposal = propose_assignment(rows, TEAM, "Lead")
        self.assertEqual(assignees(proposal), {1: "Côme D", 2: "Côme D"})

    def test_front_and_back_without_fullstack_uses_everyone(self):
        team = [
            {"name": "Front Dev", "role": "front", "option": "Inès"},
            {"name": "Back Dev", "role": "back", "option": "Aurèle"},
        ]
        rows = [row(1, ["Front", "Back"]), row(2, ["Front", "Back"]), row(3, ["Front", "Back"])]
        proposal = propose_assignment(rows, team, "Côme D")
        self.assertEqual(assignees(proposal), {1: "Inès", 2: "Aurèle", 3: "Inès"})

    def test_no_section_is_treated_as_front_and_back(self):
        proposal = propose_assignment([row(1, [])], TEAM, "Lead")
        self.assertEqual(assignees(proposal), {1: "Côme D"})

    def test_none_role_counts_as_fullstack(self):
        team = [
            {"name": "Back Dev", "role": "back", "option": "Aurèle"},
            {"name": "No Role", "role": None, "option": "Éloi"},
        ]
        rows = [row(1, ["Front"]), row(2, ["Front", "Back"])]
        proposal = propose_assignment(rows, team, "Lead")
        self.assertEqual(assignees(proposal), {1: "Éloi", 2: "Éloi"})

    def test_least_loaded_then_project_order(self):
        team = [
            {"name": "A", "role": "fullstack", "option": "Anna"},
            {"name": "B", "role": "fullstack", "option": "Ben"},
        ]
        rows = [row(n, ["Front"]) for n in range(1, 6)]
        proposal = propose_assignment(rows, team, "Lead")
        self.assertEqual(
            [item["assignee"] for item in proposal], ["Anna", "Ben", "Anna", "Ben", "Anna"]
        )

    def test_no_candidate_goes_to_the_lead(self):
        team = [{"name": "Back Dev", "role": "back", "option": "Aurèle"}]
        proposal = propose_assignment([row(1, ["Front"]), row(2, ["Back"])], team, "Côme D")
        self.assertEqual(assignees(proposal), {1: "Côme D", 2: "Aurèle"})

    def test_empty_team_goes_to_the_lead(self):
        rows = [row(1, ["Front"]), row(2, ["Front", "Back"])]
        proposal = propose_assignment(rows, [], "Côme D")
        self.assertEqual(assignees(proposal), {1: "Côme D", 2: "Côme D"})

    def test_rows_given_to_the_lead_count_in_his_load(self):
        team = [
            {"name": "Lead", "role": "back", "option": "Côme D"},
            {"name": "Back Dev", "role": "back", "option": "Aurèle"},
        ]
        proposal = propose_assignment([row(1, ["Front"]), row(2, ["Back"])], team, "Côme D")
        self.assertEqual(assignees(proposal), {1: "Côme D", 2: "Aurèle"})

    def test_input_rows_are_untouched(self):
        rows = [row(1, ["Front"])]
        before = [dict(item) for item in rows]
        propose_assignment(rows, TEAM, "Côme D")
        self.assertEqual(rows, before)

    def test_sections_are_copied(self):
        rows = [row(1, ["Front"])]
        proposal = propose_assignment(rows, TEAM, "Côme D")
        proposal[0]["sections"].append("Back")
        self.assertEqual(rows[0]["sections"], ["Front"])


if __name__ == "__main__":
    unittest.main()
