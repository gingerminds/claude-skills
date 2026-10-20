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

OPTIONS = ["Léa", "Julien D", "Julien L", "Maël", "Vincent"]


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
    {"name": "Julien Durand", "role": "fullstack", "option": "Julien D"},
    {"name": "Léa Martin", "role": "front", "option": "Léa"},
    {"name": "Vincent Roux", "role": "back", "option": "Vincent"},
]


def assignees(proposal):
    return {item["ref"]: item["assignee"] for item in proposal}


class NormalizeTest(unittest.TestCase):
    def test_strips_accents_case_and_spaces(self):
        self.assertEqual(normalize("  Maël  DUPONT "), "mael dupont")

    def test_collapses_any_whitespace_including_nbsp(self):
        self.assertEqual(normalize("Léa\xa0\tMartin"), "lea martin")

    def test_decomposed_accents(self):
        self.assertEqual(normalize("Léa"), "lea")

    def test_empty(self):
        self.assertEqual(normalize(""), "")


class MatchOptionTest(unittest.TestCase):
    def test_match_option(self):
        self.assertEqual(match_option("Julien Lambert", OPTIONS), "Julien L")
        self.assertEqual(match_option("Mael Dupont", OPTIONS), "Maël")
        self.assertEqual(match_option("Lea Martin", OPTIONS), "Léa")

    def test_match_option_unknown(self):
        self.assertIsNone(match_option("Zoé Petit", OPTIONS))

    def test_same_first_name_needs_the_initial(self):
        self.assertEqual(match_option("julien durand", OPTIONS), "Julien D")
        self.assertIsNone(match_option("Julien Martin", OPTIONS))

    def test_first_name_alone_is_ambiguous_with_homonyms(self):
        self.assertIsNone(match_option("Julien", OPTIONS))

    def test_first_name_alone_matches_a_single_bare_option(self):
        self.assertEqual(match_option("Vincent", OPTIONS), "Vincent")

    def test_single_bare_option_wins_whatever_the_surname(self):
        self.assertEqual(match_option("Vincent Roux", OPTIONS), "Vincent")

    def test_bare_option_and_homonym_with_initial(self):
        options = ["Léa", "Léa B"]
        self.assertEqual(match_option("Léa Bernard", options), "Léa B")
        self.assertIsNone(match_option("Léa Martin", options))

    def test_single_option_with_an_initial_still_needs_the_initial(self):
        self.assertEqual(match_option("Julien Durand", ["Julien D"]), "Julien D")
        self.assertIsNone(match_option("Julien Lambert", ["Julien D"]))

    def test_first_name_must_be_the_whole_first_word(self):
        self.assertIsNone(match_option("Julienne Durand", OPTIONS))

    def test_no_options_or_empty_name(self):
        self.assertIsNone(match_option("Léa Martin", []))
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
        proposal = propose_assignment(rows, TEAM, "Julien D")
        self.assertEqual(
            assignees(proposal),
            {1: "Julien D", 2: "Léa", 3: "Vincent", 4: "Julien D", 5: "Julien D"},
        )

    def test_item_shape_and_input_order(self):
        rows = [row(9, ["Back"]), row(3, ["Front", "Back"], title="Both")]
        proposal = propose_assignment(rows, TEAM, "Julien D")
        self.assertEqual(
            proposal,
            [
                {
                    "ref": 9,
                    "page_id": "page-9",
                    "title": "Task 9",
                    "sections": ["Back"],
                    "assignee": "Julien D",
                },
                {
                    "ref": 3,
                    "page_id": "page-3",
                    "title": "Both",
                    "sections": ["Front", "Back"],
                    "assignee": "Julien D",
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
        proposal = propose_assignment(rows, TEAM, "Julien D")
        self.assertEqual([item["ref"] for item in proposal], [1, 2])

    def test_unknown_rows_only_when_asked(self):
        rows = [
            row(1, ["Back"], status=STATUS_IMPLEMENTED, verdict="unknown"),
            row(2, ["Back"], status=STATUS_IMPLEMENTED, verdict="ok"),
            row(3, ["Back"], status=STATUS_TODO, verdict="unknown"),
        ]
        self.assertEqual([item["ref"] for item in propose_assignment(rows, TEAM, "Julien D")], [3])
        proposal = propose_assignment(rows, TEAM, "Julien D", include_unknown=True)
        self.assertEqual([item["ref"] for item in proposal], [1, 3])

    def test_front_only_candidates(self):
        team = [
            {"name": "Back Dev", "role": "back", "option": "Vincent"},
            {"name": "Front Dev", "role": "front", "option": "Léa"},
        ]
        proposal = propose_assignment([row(1, ["Front"]), row(2, ["Front"])], team, "Julien D")
        self.assertEqual(assignees(proposal), {1: "Léa", 2: "Léa"})

    def test_back_only_candidates(self):
        team = [
            {"name": "Front Dev", "role": "front", "option": "Léa"},
            {"name": "Back Dev", "role": "back", "option": "Vincent"},
        ]
        proposal = propose_assignment([row(1, ["Back"]), row(2, ["Back"])], team, "Julien D")
        self.assertEqual(assignees(proposal), {1: "Vincent", 2: "Vincent"})

    def test_front_and_back_prefers_fullstack(self):
        rows = [row(1, ["Front", "Back"]), row(2, ["Back", "Front"])]
        proposal = propose_assignment(rows, TEAM, "Lead")
        self.assertEqual(assignees(proposal), {1: "Julien D", 2: "Julien D"})

    def test_front_and_back_without_fullstack_uses_everyone(self):
        team = [
            {"name": "Front Dev", "role": "front", "option": "Léa"},
            {"name": "Back Dev", "role": "back", "option": "Vincent"},
        ]
        rows = [row(1, ["Front", "Back"]), row(2, ["Front", "Back"]), row(3, ["Front", "Back"])]
        proposal = propose_assignment(rows, team, "Julien D")
        self.assertEqual(assignees(proposal), {1: "Léa", 2: "Vincent", 3: "Léa"})

    def test_no_section_is_treated_as_front_and_back(self):
        proposal = propose_assignment([row(1, [])], TEAM, "Lead")
        self.assertEqual(assignees(proposal), {1: "Julien D"})

    def test_none_role_counts_as_fullstack(self):
        team = [
            {"name": "Back Dev", "role": "back", "option": "Vincent"},
            {"name": "No Role", "role": None, "option": "Maël"},
        ]
        rows = [row(1, ["Front"]), row(2, ["Front", "Back"])]
        proposal = propose_assignment(rows, team, "Lead")
        self.assertEqual(assignees(proposal), {1: "Maël", 2: "Maël"})

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
        team = [{"name": "Back Dev", "role": "back", "option": "Vincent"}]
        proposal = propose_assignment([row(1, ["Front"]), row(2, ["Back"])], team, "Julien D")
        self.assertEqual(assignees(proposal), {1: "Julien D", 2: "Vincent"})

    def test_empty_team_goes_to_the_lead(self):
        rows = [row(1, ["Front"]), row(2, ["Front", "Back"])]
        proposal = propose_assignment(rows, [], "Julien D")
        self.assertEqual(assignees(proposal), {1: "Julien D", 2: "Julien D"})

    def test_rows_given_to_the_lead_count_in_his_load(self):
        team = [
            {"name": "Lead", "role": "back", "option": "Julien D"},
            {"name": "Back Dev", "role": "back", "option": "Vincent"},
        ]
        proposal = propose_assignment([row(1, ["Front"]), row(2, ["Back"])], team, "Julien D")
        self.assertEqual(assignees(proposal), {1: "Julien D", 2: "Vincent"})

    def test_input_rows_are_untouched(self):
        rows = [row(1, ["Front"])]
        before = [dict(item) for item in rows]
        propose_assignment(rows, TEAM, "Julien D")
        self.assertEqual(rows, before)

    def test_sections_are_copied(self):
        rows = [row(1, ["Front"])]
        proposal = propose_assignment(rows, TEAM, "Julien D")
        proposal[0]["sections"].append("Back")
        self.assertEqual(rows[0]["sections"], ["Front"])


if __name__ == "__main__":
    unittest.main()
