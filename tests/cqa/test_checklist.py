import json
import unittest
from pathlib import Path

from cqa.checklist import (
    REQUIRED_PROPERTIES,
    STATUS_IMPLEMENTED,
    STATUS_NA,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_STANDBY,
    STATUS_TODO,
    fiche_filename,
    match_reference,
    normalize_row,
    ref_label,
    validate_schema,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BOT_ID = "bot-1"

STATUSES = [
    "À traiter",
    "En standby",
    "Mise en place partielle",
    "Mis en place",
    "Ok / Recetté",
    "Non applicable",
]
TECHNOS = ["Laravel", "Drupal", "Nuxt", "Wordpress"]
TOOLS = [
    "Code",
    "CLI local",
    "HTTP",
    "Navigateur",
    "Audit web",
    "Crawler",
    "SSH serveur",
    "BDD",
    "Comptes de test",
    "Service externe",
    "Intervention humaine",
]


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def config(data_source, name):
    prop = data_source["properties"][name]
    return prop[prop["type"]]


def drop_option(data_source, name, option):
    settings = config(data_source, name)
    settings["options"] = [item for item in settings["options"] if item["name"] != option]


def rich_text(*parts):
    return {"type": "rich_text", "rich_text": [{"type": "text", "plain_text": p} for p in parts]}


def title(*parts):
    return {"type": "title", "title": [{"type": "text", "plain_text": p} for p in parts]}


def select(name):
    return {"type": "select", "select": None if name is None else {"name": name}}


def multi_select(*names):
    return {"type": "multi_select", "multi_select": [{"name": name} for name in names]}


def page_with(overrides):
    data = load("page_row.json")
    data["properties"].update(overrides)
    return data


def row(page_id, ref, row_title):
    return {"page_id": page_id, "ref": ref, "title": row_title}


class StatusConstantsTest(unittest.TestCase):
    def test_values(self):
        values = [
            STATUS_TODO,
            STATUS_STANDBY,
            STATUS_PARTIAL,
            STATUS_IMPLEMENTED,
            STATUS_OK,
            STATUS_NA,
        ]
        self.assertEqual(values, STATUSES)


class ValidateSchemaTest(unittest.TestCase):
    def test_valid_schema(self):
        self.assertEqual(validate_schema(load("data_source_v2.json")), [])

    def test_old_template_refused(self):
        ds = load("data_source_v2.json")
        del ds["properties"]["Réf"]
        drop_option(ds, "Statut", "Non applicable")
        problems = validate_schema(ds)
        self.assertIn("propriété manquante : Réf", problems)
        self.assertIn("statut manquant : Non applicable", problems)

    def test_required_properties(self):
        expected = {
            "Tâche": "title",
            "Réf": "unique_id",
            "Réf modèle": "rich_text",
            "Statut": "status",
            "Assignée à": "multi_select",
            "Catégorie": "select",
            "Sous-catégorie": "select",
            "Mise en ligne": "select",
            "Section": "multi_select",
            "Technos concernées": "multi_select",
            "Phase": "multi_select",
            "Outils requis": "multi_select",
            "Automatisation skill": "select",
            "Outillage skill": "rich_text",
        }
        self.assertEqual(REQUIRED_PROPERTIES, expected)

    def test_ref_modele_is_required(self):
        ds = load("data_source_v2.json")
        del ds["properties"]["Réf modèle"]
        self.assertEqual(validate_schema(ds), ["propriété manquante : Réf modèle"])

    def test_each_missing_property_is_reported(self):
        for name in REQUIRED_PROPERTIES:
            with self.subTest(name):
                ds = load("data_source_v2.json")
                del ds["properties"][name]
                self.assertEqual(validate_schema(ds), [f"propriété manquante : {name}"])

    def test_each_wrong_type_is_reported(self):
        for name, expected in REQUIRED_PROPERTIES.items():
            with self.subTest(name):
                ds = load("data_source_v2.json")
                ds["properties"][name]["type"] = "checkbox"
                message = "type de propriété incorrect : {} (attendu : {}, trouvé : checkbox)"
                self.assertEqual(validate_schema(ds), [message.format(name, expected)])

    def test_options_of_a_mistyped_property_are_not_checked(self):
        ds = load("data_source_v2.json")
        prop = ds["properties"]["Statut"]
        prop["type"] = "select"
        prop["select"] = {"options": [{"name": "À traiter"}]}
        del prop["status"]
        message = "type de propriété incorrect : Statut (attendu : status, trouvé : select)"
        self.assertEqual(validate_schema(ds), [message])

    def test_each_required_option_is_checked(self):
        required = {
            "Statut": ("statut manquant", STATUSES),
            "Section": ("option de Section manquante", ["Front", "Back"]),
            "Technos concernées": ("techno manquante", TECHNOS),
            "Outils requis": ("outil manquant", TOOLS),
        }
        for name, (message, options) in required.items():
            for option in options:
                with self.subTest(property=name, option=option):
                    ds = load("data_source_v2.json")
                    drop_option(ds, name, option)
                    self.assertEqual(validate_schema(ds), [f"{message} : {option}"])

    def test_property_without_options_misses_every_option(self):
        ds = load("data_source_v2.json")
        ds["properties"]["Statut"]["status"] = {}
        ds["properties"]["Section"]["multi_select"] = None
        expected = [f"statut manquant : {name}" for name in STATUSES]
        expected += ["option de Section manquante : Front", "option de Section manquante : Back"]
        self.assertEqual(validate_schema(ds), expected)

    def test_extra_properties_and_options_are_tolerated(self):
        ds = load("data_source_v2.json")
        extra = {"id": "x1", "name": "Notes", "type": "rich_text", "rich_text": {}}
        ds["properties"]["Notes"] = extra
        config(ds, "Section")["options"].append({"id": "e3", "name": "Infra", "color": "gray"})
        self.assertEqual(validate_schema(ds), [])

    def test_all_problems_are_reported_in_a_stable_order(self):
        ds = load("data_source_v2.json")
        del ds["properties"]["Réf modèle"]
        ds["properties"]["Phase"]["type"] = "select"
        drop_option(ds, "Technos concernées", "Nuxt")
        drop_option(ds, "Outils requis", "BDD")
        drop_option(ds, "Outils requis", "Crawler")
        self.assertEqual(
            validate_schema(ds),
            [
                "propriété manquante : Réf modèle",
                "type de propriété incorrect : Phase (attendu : multi_select, trouvé : select)",
                "techno manquante : Nuxt",
                "outil manquant : Crawler",
                "outil manquant : BDD",
            ],
        )

    def test_data_source_without_properties(self):
        expected = [f"propriété manquante : {name}" for name in REQUIRED_PROPERTIES]
        for ds in ({}, {"properties": None}, {"properties": {}}):
            with self.subTest(data_source=ds):
                self.assertEqual(validate_schema(ds), expected)


class NormalizeRowTest(unittest.TestCase):
    def test_normalize_row(self):
        result = normalize_row(load("page_row.json"), bot_id="bot-1")
        self.assertEqual(result["ref"], 79)
        self.assertTrue(result["blocking"])
        self.assertEqual(result["tools"], ["Code", "Navigateur"])
        self.assertFalse(result["last_edited_by_bot"])

    def test_every_field(self):
        expected = {
            "page_id": "0123456789abcdef0123456789abcdef",
            "ref": 79,
            "title": "Activer la compression des réponses HTTP",
            "status": "À traiter",
            "assignees": ["Alice", "Bruno"],
            "category": "QUALITÉ",
            "subcategory": "Performance",
            "blocking": True,
            "sections": ["Back"],
            "technos": ["Laravel", "Drupal"],
            "phases": ["Build", "Recette J-30"],
            "tools": ["Code", "Navigateur"],
            "automation": "Partielle",
            "tooling_hint": "Lire les en-têtes avec la sonde HTTP.",
            "last_edited_by_bot": False,
        }
        self.assertEqual(normalize_row(load("page_row.json"), bot_id=BOT_ID), expected)

    def test_ref_from_ref_modele(self):
        cases = (("CQ-79", 79), ("", None))
        for text, expected in cases:
            with self.subTest(text=text):
                page = page_with({"Réf modèle": rich_text(text)})
                self.assertEqual(normalize_row(page, bot_id=BOT_ID)["ref"], expected)

    def test_ref_modele_without_text_parts(self):
        page = page_with({"Réf modèle": rich_text()})
        self.assertIsNone(normalize_row(page, bot_id=BOT_ID)["ref"])

    def test_ref_modele_tolerates_case_spaces_and_zero_padding(self):
        for text in (" CQ-79 ", "cq-79", "CQ-079", "CQ-0079"):
            with self.subTest(text=text):
                page = page_with({"Réf modèle": rich_text(text)})
                self.assertEqual(normalize_row(page, bot_id=BOT_ID)["ref"], 79)

    def test_ref_modele_parts_are_joined(self):
        page = page_with({"Réf modèle": rich_text("CQ-", "79")})
        self.assertEqual(normalize_row(page, bot_id=BOT_ID)["ref"], 79)

    def test_malformed_ref_modele_is_none(self):
        for text in ("   ", "79", "CQ-", "CQ79", "CQ-7x", "CQ-79 bis", "XX-79", "CQ--79", "CQ-٧٩"):
            with self.subTest(text=text):
                page = page_with({"Réf modèle": rich_text(text)})
                self.assertIsNone(normalize_row(page, bot_id=BOT_ID)["ref"])

    def test_auto_ref_is_not_the_key(self):
        page = page_with({"Réf modèle": rich_text()})
        self.assertEqual(page["properties"]["Réf"]["unique_id"]["number"], 12)
        self.assertIsNone(normalize_row(page, bot_id=BOT_ID)["ref"])

    def test_last_edited_by_bot(self):
        page = load("page_row.json")
        page["last_edited_by"]["id"] = BOT_ID
        self.assertIs(normalize_row(page, bot_id=BOT_ID)["last_edited_by_bot"], True)

    def test_last_edited_by_another_user(self):
        page = load("page_row.json")
        self.assertIs(normalize_row(page, bot_id=BOT_ID)["last_edited_by_bot"], False)

    def test_unknown_editor_counts_as_human(self):
        pages = [load("page_row.json")]
        del pages[0]["last_edited_by"]
        for editor in (None, {}, {"object": "user"}, {"object": "user", "id": ""}):
            pages.append(load("page_row.json"))
            pages[-1]["last_edited_by"] = editor
        for page in pages:
            for bot_id in (BOT_ID, "", None):
                with self.subTest(editor=page.get("last_edited_by", "absent"), bot_id=bot_id):
                    result = normalize_row(page, bot_id=bot_id)
                    self.assertIs(result["last_edited_by_bot"], False)

    def test_blocking_only_for_bloquant(self):
        for value, expected in (("Bloquant", True), ("Non bloquant", False), (None, False)):
            with self.subTest(value=value):
                page = page_with({"Mise en ligne": select(value)})
                self.assertIs(normalize_row(page, bot_id=BOT_ID)["blocking"], expected)

    def test_unset_values(self):
        overrides = {
            "Statut": {"type": "status", "status": None},
            "Catégorie": select(None),
            "Sous-catégorie": select(None),
            "Mise en ligne": select(None),
            "Automatisation skill": select(None),
            "Outillage skill": rich_text(),
        }
        for name in ("Assignée à", "Section", "Technos concernées", "Phase", "Outils requis"):
            overrides[name] = multi_select()
        result = normalize_row(page_with(overrides), bot_id=BOT_ID)
        expected = {
            "status": None,
            "category": None,
            "subcategory": None,
            "automation": None,
            "tooling_hint": "",
            "blocking": False,
            "assignees": [],
            "sections": [],
            "technos": [],
            "phases": [],
            "tools": [],
        }
        self.assertEqual({key: result[key] for key in expected}, expected)

    def test_missing_properties_read_as_unset(self):
        page = load("page_row.json")
        page["properties"] = {}
        result = normalize_row(page, bot_id=BOT_ID)
        self.assertEqual(result["page_id"], page["id"])
        self.assertIsNone(result["ref"])
        self.assertEqual(result["title"], "")
        self.assertIsNone(result["status"])
        self.assertEqual(result["tools"], [])

    def test_text_parts_are_joined(self):
        overrides = {
            "Tâche": title("Activer ", "la compression"),
            "Outillage skill": rich_text("a", "b"),
        }
        result = normalize_row(page_with(overrides), bot_id=BOT_ID)
        self.assertEqual(result["title"], "Activer la compression")
        self.assertEqual(result["tooling_hint"], "ab")


class LabelTest(unittest.TestCase):
    def test_ref_label(self):
        self.assertEqual(ref_label(79), "CQ-79")
        self.assertEqual(ref_label(5), "CQ-5")

    def test_fiche_filename(self):
        cases = ((79, "CQ-079.md"), (5, "CQ-005.md"), (141, "CQ-141.md"), (1000, "CQ-1000.md"))
        for ref, expected in cases:
            with self.subTest(ref=ref):
                self.assertEqual(fiche_filename(ref), expected)


class MatchReferenceTest(unittest.TestCase):
    def test_match_by_ref_then_title(self):
        ref_rows = [row("r1", 1, "Alpha"), row("r2", 2, "Beta"), row("r3", 3, "Gamma")]
        copy_rows = [row("c1", 1, "Alpha"), row("c2", None, "Beta")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[0]), (copy_rows[1], ref_rows[1])])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [ref_rows[2]])

    def test_reused_ref_with_another_title_follows_the_title(self):
        ref_rows = [row("r1", 1, "Alpha"), row("r2", 2, "Beta")]
        copy_rows = [row("c1", 1, "Beta")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[1])])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [ref_rows[0]])

    def test_ref_alone_never_takes_a_paired_reference_row(self):
        ref_rows = [row("r1", 1, "Alpha"), row("r2", 2, "Beta")]
        copy_rows = [row("c1", 1, "Beta"), row("c2", 2, "Gamma")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[1])])
        self.assertEqual(unmatched, [copy_rows[1]])
        self.assertEqual(missing, [ref_rows[0]])

    def test_ref_alone_matches_an_edited_title(self):
        ref_rows = [row("r1", 1, "Alpha"), row("r2", 2, "Beta")]
        copy_rows = [row("c1", 1, "Alpha reworded"), row("c2", 2, "Beta")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[0]), (copy_rows[1], ref_rows[1])])
        self.assertEqual((unmatched, missing), ([], []))

    def test_ref_and_title_win_over_title_alone(self):
        ref_rows = [row("r1", 1, "Same"), row("r2", 2, "Same")]
        copy_rows = [row("c1", 2, "Same")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[1])])
        self.assertEqual(missing, [ref_rows[0]])

    def test_title_alone_uses_only_unpaired_reference_rows(self):
        ref_rows = [row("r1", 1, "Same"), row("r2", 2, "Same")]
        copy_rows = [row("c1", 2, "Same"), row("c2", None, "Same")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[1]), (copy_rows[1], ref_rows[0])])
        self.assertEqual((unmatched, missing), ([], []))

    def test_ambiguous_title_is_not_paired(self):
        ref_rows = [row("r1", 1, "Same"), row("r2", 2, "Same")]
        copy_rows = [row("c1", None, "Same")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, copy_rows)
        self.assertEqual(missing, ref_rows)

    def test_ambiguous_title_is_not_paired_when_refs_are_missing_too(self):
        ref_rows = [row("r1", None, "Same"), row("r2", None, "Same")]
        copy_rows = [row("c1", None, "Same")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, copy_rows)
        self.assertEqual(missing, ref_rows)

    def test_title_comparison_ignores_case_and_surrounding_spaces(self):
        ref_rows = [row("r1", 1, "Activer la compression")]
        copy_rows = [row("c1", None, "  ACTIVER la Compression ")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[0])])
        self.assertEqual((unmatched, missing), ([], []))

    def test_rows_without_ref_do_not_pair_with_each_other(self):
        ref_rows = [row("r1", None, "Alpha")]
        copy_rows = [row("c1", None, "Beta")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, copy_rows)
        self.assertEqual(missing, ref_rows)

    def test_empty_titles_never_match_by_title(self):
        ref_rows = [row("r1", 1, "")]
        copy_rows = [row("c1", None, "  ")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, copy_rows)
        self.assertEqual(missing, ref_rows)

    def test_each_row_is_paired_once(self):
        ref_rows = [row("r1", 1, "Alpha")]
        copy_rows = [row("c1", 1, "Alpha"), row("c2", 1, "Alpha")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(pairs, [(copy_rows[0], ref_rows[0])])
        self.assertEqual(unmatched, [copy_rows[1]])
        self.assertEqual(missing, [])

    def test_results_keep_source_order(self):
        ref_rows = [row(f"r{n}", n, name) for n, name in enumerate("ABCDE", start=1)]
        copy_rows = [
            row("c1", None, "C"),
            row("c2", 9, "zzz"),
            row("c3", 2, "B"),
            row("c4", None, "Y"),
            row("c5", 4, "D edited"),
        ]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual(
            pairs,
            [(copy_rows[0], ref_rows[2]), (copy_rows[2], ref_rows[1]), (copy_rows[4], ref_rows[3])],
        )
        self.assertEqual(unmatched, [copy_rows[1], copy_rows[3]])
        self.assertEqual(missing, [ref_rows[0], ref_rows[4]])

    def test_rows_are_returned_as_given(self):
        ref_rows = [row("r1", 1, "Alpha"), row("r2", 2, "Beta")]
        copy_rows = [row("c1", 1, "Alpha"), row("c2", 7, "Other")]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertIs(pairs[0][0], copy_rows[0])
        self.assertIs(pairs[0][1], ref_rows[0])
        self.assertIs(unmatched[0], copy_rows[1])
        self.assertIs(missing[0], ref_rows[1])

    def test_empty_inputs(self):
        rows = [row("x1", 1, "Alpha")]
        self.assertEqual(match_reference([], []), ([], [], []))
        self.assertEqual(match_reference([], rows), ([], [], rows))
        self.assertEqual(match_reference(rows, []), ([], rows, []))

    def test_normalized_rows_are_matched(self):
        def page_of(ref_text, name):
            return page_with({"Réf modèle": rich_text(ref_text), "Tâche": title(name)})

        ref_rows = [normalize_row(page_of("CQ-1", "Alpha"), BOT_ID)]
        ref_rows.append(normalize_row(page_of("CQ-2", "Beta"), BOT_ID))
        copy_rows = [normalize_row(page_of("", "beta"), BOT_ID)]
        pairs, unmatched, missing = match_reference(copy_rows, ref_rows)
        self.assertEqual([(copy["title"], ref["ref"]) for copy, ref in pairs], [("beta", 2)])
        self.assertEqual(unmatched, [])
        self.assertEqual([ref["ref"] for ref in missing], [1])
