import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import cqa_notion
from cqa.notion_api import NotionClient, NotionError
from cqa.notion_read import (
    assemble_rows,
    assignee_options,
    load_rows,
    load_skill_comments,
    resolve,
    write_fiches,
)
from tests.cqa.fakes import Call, FakeClock, failure, listing, make_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BOT = "bot-1"


def fake_id(number):
    """Return a 32-hex id built from a number, so no id literal lands in the sources."""
    return f"{number:032x}"


def dashed(number):
    return str(uuid.UUID(fake_id(number)))


PAGE = dashed(1)
DATABASE = dashed(2)
COPY_DS = dashed(3)
REF_DATABASE = dashed(4)
REF_DS = dashed(5)
CHILD_DATABASE = dashed(6)
OTHER_DS = dashed(7)


def ok(body):
    return 200, {}, body


def database(*data_source_ids):
    body = {
        "object": "database",
        "id": DATABASE,
        "data_sources": [{"id": item, "name": "Source"} for item in data_source_ids],
    }
    return ok(body)


def rich(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text}] if text else []}


def page(page_id, ref, title, status="À traiter", editor=None):
    return {
        "object": "page",
        "id": page_id,
        "last_edited_by": {"object": "user", "id": editor} if editor else {},
        "properties": {
            "Tâche": {"type": "title", "title": [{"plain_text": title}]},
            "Réf modèle": rich(f"CQ-{ref}" if ref else ""),
            "Statut": {"type": "status", "status": {"name": status}},
        },
    }


def comment(text, author=BOT):
    return {
        "object": "comment",
        "created_by": {"object": "user", "id": author},
        "rich_text": [{"plain_text": text}],
    }


def row(page_id, ref, title="Titre", status="À traiter", reference_page_id=None):
    return {
        "page_id": page_id,
        "ref": ref,
        "title": title,
        "status": status,
        "reference_page_id": reference_page_id,
    }


def markdown(text="# Fiche\n", truncated=False):
    return ok({"object": "page_markdown", "markdown": text, "truncated": truncated})


class RoutedTransport:
    """Transport answering from a {"METHOD /path": response} table, whatever the call order.

    A comments request is keyed with its block: "GET /comments?block_id=<id>". An unscripted
    request fails the test.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, data):
        self.calls.append(Call(method, url, dict(headers), data))
        parts = urlsplit(url)
        key = f"{method} {parts.path[len('/v1'):]}"
        block = parse_qs(parts.query).get("block_id")
        if block:
            key += f"?block_id={block[0]}"
        if key not in self.routes:
            raise AssertionError(f"unscripted request: {key}")
        status, response_headers, body = self.routes[key]
        return status, dict(response_headers), json.dumps(body).encode("utf-8")

    def keys(self):
        """The requests received, as table keys, in order."""
        result = []
        for call in self.calls:
            parts = urlsplit(call.url)
            key = f"{call.method} {parts.path[len('/v1'):]}"
            block = parse_qs(parts.query).get("block_id")
            result.append(key + (f"?block_id={block[0]}" if block else ""))
        return result


def routed_client(routes):
    transport = RoutedTransport(routes)
    clock = FakeClock()
    client = NotionClient("tok", transport=transport, sleep=clock.sleep, clock=clock)
    return client, transport


class ResolveTest(unittest.TestCase):
    def test_database_id_resolves_directly(self):
        client, transport, _ = make_client([database(COPY_DS)])
        found = resolve(client, DATABASE)
        self.assertEqual(
            found, {"page_id": None, "database_id": DATABASE, "data_source_id": COPY_DS}
        )
        self.assertEqual(len(transport.calls), 1)

    def test_url_is_accepted(self):
        client, transport, _ = make_client([database(COPY_DS)])
        resolve(client, f"https://www.notion.so/ws/Checklist-{fake_id(2)}?v={fake_id(9)}")
        self.assertTrue(transport.calls[0].url.endswith(f"/databases/{DATABASE}"))

    def test_resolve_page_with_child_database(self):
        children = listing(
            [{"type": "paragraph", "id": "b1"}, {"type": "child_database", "id": CHILD_DATABASE}]
        )
        client, transport, _ = make_client([failure(404), children, database(COPY_DS)])
        found = resolve(client, PAGE)
        self.assertEqual(
            found, {"page_id": PAGE, "database_id": CHILD_DATABASE, "data_source_id": COPY_DS}
        )
        self.assertEqual(
            [(call.method, urlsplit(call.url).path) for call in transport.calls],
            [
                ("GET", f"/v1/databases/{PAGE}"),
                ("GET", f"/v1/blocks/{PAGE}/children"),
                ("GET", f"/v1/databases/{CHILD_DATABASE}"),
            ],
        )

    def test_resolve_first_child_database_wins(self):
        blocks = [
            {"type": "child_database", "id": CHILD_DATABASE},
            {"type": "child_database", "id": "b2"},
        ]
        children = listing(blocks)
        client, transport, _ = make_client([failure(404), children, database(COPY_DS)])
        resolve(client, PAGE)
        self.assertTrue(transport.calls[2].url.endswith(f"/databases/{CHILD_DATABASE}"))

    def test_page_id_rejected_with_validation_error_is_a_page_too(self):
        message = "Provided ID is a page, not a database."
        refusal = failure(400, "validation_error", message)
        children = listing([{"type": "child_database", "id": CHILD_DATABASE}])
        client, _, _ = make_client([refusal, children, database(COPY_DS)])
        self.assertEqual(resolve(client, PAGE)["data_source_id"], COPY_DS)

    def test_other_api_errors_are_not_swallowed(self):
        for status in (400, 401, 403, 429):
            with self.subTest(status=status):
                client, transport, _ = make_client([failure(status, "restricted_resource")] * 5)
                with self.assertRaises(NotionError) as caught:
                    resolve(client, PAGE)
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn("/blocks/", transport.calls[-1].url)

    def test_page_without_child_database(self):
        children = listing([{"type": "paragraph", "id": "b1"}])
        client, _, _ = make_client([failure(404), children])
        with self.assertRaises(ValueError) as caught:
            resolve(client, PAGE)
        self.assertIn("base de données", str(caught.exception))

    def test_resolve_two_data_sources_refused(self):
        client, _, _ = make_client([database(COPY_DS, OTHER_DS)])
        with self.assertRaises(ValueError) as caught:
            resolve(client, DATABASE)
        self.assertIn("plusieurs data sources", str(caught.exception))

    def test_two_data_sources_refused_behind_a_page(self):
        children = listing([{"type": "child_database", "id": CHILD_DATABASE}])
        client, _, _ = make_client([failure(404), children, database(COPY_DS, OTHER_DS)])
        with self.assertRaises(ValueError) as caught:
            resolve(client, PAGE)
        self.assertIn("plusieurs data sources", str(caught.exception))

    def test_database_without_data_source(self):
        client, _, _ = make_client([database()])
        with self.assertRaises(ValueError) as caught:
            resolve(client, DATABASE)
        self.assertIn("aucune data source", str(caught.exception))

    def test_not_a_notion_reference(self):
        client, transport, _ = make_client([])
        with self.assertRaises(ValueError):
            resolve(client, "https://example.org/not-notion")
        self.assertEqual(transport.calls, [])


class LoadRowsTest(unittest.TestCase):
    def test_rows_are_normalized_and_paginated(self):
        pages = [
            listing([page("p1", 79, "Activer la compression", editor=BOT)], "cursor-1"),
            listing([page("p2", 80, "Forcer HTTPS", status="En standby")]),
        ]
        client, transport, _ = make_client(pages)
        rows = load_rows(client, COPY_DS, BOT)
        self.assertEqual([item["ref"] for item in rows], [79, 80])
        self.assertEqual([item["title"] for item in rows][1], "Forcer HTTPS")
        self.assertEqual([item["last_edited_by_bot"] for item in rows], [True, False])
        self.assertEqual(rows[1]["status"], "En standby")
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(transport.calls[0].url.endswith(f"/data_sources/{COPY_DS}/query"))


class AssembleRowsTest(unittest.TestCase):
    def test_pairs_take_the_reference_ref_and_fiche(self):
        copy_rows = [row("c1", 12, "Un"), row("c2", 80, "Deux")]
        ref_rows = [row("r1", 79, "Un"), row("r2", 80, "Deux")]
        rows, missing = assemble_rows(copy_rows, ref_rows)
        self.assertEqual([item["ref"] for item in rows], [79, 80])
        self.assertEqual([item["reference_page_id"] for item in rows], ["r1", "r2"])
        self.assertEqual(
            [item["fiche_path"] for item in rows], ["fiches/CQ-079.md", "fiches/CQ-080.md"]
        )
        self.assertEqual(missing, [])

    def test_unmatched_row_has_no_ref_nor_fiche(self):
        copy_rows = [row("c1", 79, "Un"), row("c2", 79, "Ajoutée par l'équipe")]
        ref_rows = [row("r1", 79, "Un")]
        rows, missing = assemble_rows(copy_rows, ref_rows)
        unmatched = rows[1]
        self.assertEqual(unmatched["page_id"], "c2")
        self.assertIsNone(unmatched["ref"])
        self.assertIsNone(unmatched["reference_page_id"])
        self.assertIsNone(unmatched["fiche_path"])
        self.assertEqual(rows[0]["ref"], 79)
        self.assertEqual(missing, [])

    def test_missing_in_copy_lists_ref_and_title(self):
        copy_rows = [row("c1", 79, "Un")]
        ref_rows = [row("r1", 79, "Un"), row("r2", 81, "Trois")]
        rows, missing = assemble_rows(copy_rows, ref_rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(missing, [{"ref": 81, "title": "Trois"}])

    def test_reference_row_without_ref_gets_no_fiche(self):
        copy_rows = [row("c1", 5, "Un")]
        ref_rows = [row("r1", None, "Un")]
        rows, _ = assemble_rows(copy_rows, ref_rows)
        self.assertIsNone(rows[0]["ref"])
        self.assertEqual(rows[0]["reference_page_id"], "r1")
        self.assertIsNone(rows[0]["fiche_path"])

    def test_source_rows_are_left_untouched(self):
        copy_rows = [row("c1", 12, "Un")]
        assemble_rows(copy_rows, [row("r1", 79, "Un")])
        self.assertEqual(copy_rows[0]["ref"], 12)
        self.assertNotIn("fiche_path", copy_rows[0])


class AssigneeOptionsTest(unittest.TestCase):
    def test_options_in_schema_order(self):
        data_source = json.loads((FIXTURES / "data_source_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(assignee_options(data_source), ["Alice", "Bruno", "Chloé"])

    def test_missing_property_gives_no_option(self):
        self.assertEqual(assignee_options({"properties": {}}), [])
        self.assertEqual(assignee_options({}), [])


class WriteFichesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name)

    def pairs(self, *refs):
        return [(row(f"c{ref}", ref), row(f"r{ref}", ref)) for ref in refs]

    def test_writes_fiches_and_index(self):
        responses = [markdown("# Compression\nÀ tester\n"), markdown("# HTTPS\n", truncated=True)]
        client, transport, _ = make_client(responses)
        index = write_fiches(client, self.run_dir, self.pairs(79, 80))
        self.assertEqual(
            index,
            {
                "79": {"path": "fiches/CQ-079.md", "truncated": False},
                "80": {"path": "fiches/CQ-080.md", "truncated": True},
            },
        )
        fiche = self.run_dir / "fiches" / "CQ-079.md"
        self.assertEqual(fiche.read_text(encoding="utf-8"), "# Compression\nÀ tester\n")
        saved = json.loads((self.run_dir / "fiches" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, index)
        self.assertEqual(
            [urlsplit(call.url).path for call in transport.calls],
            ["/v1/pages/r79/markdown", "/v1/pages/r80/markdown"],
        )

    def test_second_call_makes_no_markdown_request(self):
        client, transport, _ = make_client([markdown(truncated=True)])
        first = write_fiches(client, self.run_dir, self.pairs(79))
        second = write_fiches(client, self.run_dir, self.pairs(79))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(second, first)
        self.assertTrue(second["79"]["truncated"])

    def test_only_missing_fiches_are_fetched(self):
        client, transport, _ = make_client([markdown("un"), markdown("deux")])
        write_fiches(client, self.run_dir, self.pairs(79))
        index = write_fiches(client, self.run_dir, self.pairs(79, 80))
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(transport.calls[1].url.endswith("/pages/r80/markdown"))
        self.assertEqual(sorted(index), ["79", "80"])

    def test_file_without_index_entry_is_fetched_again(self):
        fiches = self.run_dir / "fiches"
        fiches.mkdir()
        (fiches / "CQ-079.md").write_text("interrupted", encoding="utf-8")
        client, transport, _ = make_client([markdown("complete")])
        write_fiches(client, self.run_dir, self.pairs(79))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual((fiches / "CQ-079.md").read_text(encoding="utf-8"), "complete")

    def test_index_entry_without_file_is_fetched_again(self):
        client, transport, _ = make_client([markdown("un"), markdown("un again")])
        write_fiches(client, self.run_dir, self.pairs(79))
        (self.run_dir / "fiches" / "CQ-079.md").unlink()
        write_fiches(client, self.run_dir, self.pairs(79))
        self.assertEqual(len(transport.calls), 2)

    def test_unreadable_index_is_ignored(self):
        fiches = self.run_dir / "fiches"
        fiches.mkdir()
        (fiches / "CQ-079.md").write_text("old", encoding="utf-8")
        (fiches / "index.json").write_text("{not json", encoding="utf-8")
        client, transport, _ = make_client([markdown("new")])
        index = write_fiches(client, self.run_dir, self.pairs(79))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(index["79"]["path"], "fiches/CQ-079.md")

    def test_failure_keeps_the_fiches_already_fetched(self):
        client, transport, _ = make_client([markdown("un"), failure(403, "restricted_resource")])
        with self.assertRaises(NotionError):
            write_fiches(client, self.run_dir, self.pairs(79, 80))
        saved = json.loads((self.run_dir / "fiches" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved), ["79"])
        client, transport, _ = make_client([markdown("deux")])
        index = write_fiches(client, self.run_dir, self.pairs(79, 80))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(sorted(index), ["79", "80"])

    def test_pairs_without_ref_are_skipped(self):
        pairs = [(row("c1", None), row("r1", None))] + self.pairs(79)
        client, transport, _ = make_client([markdown()])
        index = write_fiches(client, self.run_dir, pairs)
        self.assertEqual(list(index), ["79"])
        self.assertEqual(len(transport.calls), 1)

    def test_a_ref_is_fetched_once(self):
        pairs = self.pairs(79) + [(row("c2", 79), row("r79b", 79))]
        client, transport, _ = make_client([markdown()])
        write_fiches(client, self.run_dir, pairs)
        self.assertEqual(len(transport.calls), 1)

    def test_no_pairs_writes_an_empty_index(self):
        client, transport, _ = make_client([])
        self.assertEqual(write_fiches(client, self.run_dir, []), {})
        self.assertEqual(transport.calls, [])
        saved = (self.run_dir / "fiches" / "index.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(saved), {})


class SkillCommentsTest(unittest.TestCase):
    def test_skill_comments_only_bot(self):
        rows = [row("p1", 79)]
        thread = listing(
            [
                comment("Verdict : Ok / Recetté", author="user-1"),
                comment("Constat : en place\nVerdict : À traiter"),
                comment("Un simple message"),
            ]
        )
        client, transport = routed_client({"GET /comments?block_id=p1": thread})
        self.assertEqual(load_skill_comments(client, rows, BOT), {79: ["ko"]})

    def test_verdicts_keep_the_comment_order(self):
        thread = listing([comment("Verdict : À traiter"), comment("Verdict : Ok / Recetté")])
        client, _ = routed_client({"GET /comments?block_id=p1": thread})
        self.assertEqual(load_skill_comments(client, [row("p1", 79)], BOT), {79: ["ko", "ok"]})

    def test_rich_text_parts_are_joined(self):
        split = {
            "created_by": {"id": BOT},
            "rich_text": [
                {"plain_text": "Constat : x\nVerdict : "},
                {"plain_text": "Non applicable"},
            ],
        }
        client, _ = routed_client({"GET /comments?block_id=p1": listing([split])})
        self.assertEqual(load_skill_comments(client, [row("p1", 79)], BOT), {79: ["na"]})

    def test_standby_rows_are_ignored(self):
        rows = [row("p1", 79, status="En standby"), row("p2", 80)]
        client, transport = routed_client(
            {"GET /comments?block_id=p2": listing([comment("Verdict : Ok / Recetté")])}
        )
        self.assertEqual(load_skill_comments(client, rows, BOT), {80: ["ok"]})
        self.assertEqual(transport.keys(), ["GET /comments?block_id=p2"])

    def test_rows_without_ref_are_ignored(self):
        client, transport = routed_client({})
        self.assertEqual(load_skill_comments(client, [row("p1", None)], BOT), {})
        self.assertEqual(transport.calls, [])

    def test_rows_without_verdict_are_left_out(self):
        rows = [row("p1", 79), row("p2", 80)]
        client, _ = routed_client(
            {
                "GET /comments?block_id=p1": listing([]),
                "GET /comments?block_id=p2": listing([comment("Rien à signaler")]),
            }
        )
        self.assertEqual(load_skill_comments(client, rows, BOT), {})

    def test_unknown_verdict_label_is_left_out(self):
        thread = listing([comment("Verdict : Peut-être")])
        client, _ = routed_client({"GET /comments?block_id=p1": thread})
        self.assertEqual(load_skill_comments(client, [row("p1", 79)], BOT), {})


class CliTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name)

    def write_json(self, name, value):
        (self.run_dir / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def write_rows_json(self, rows):
        document = {"copy_data_source_id": COPY_DS, "rows": rows, "missing_in_copy": []}
        self.write_json("rows.json", document)

    def write_context(self, **notion):
        values = {"copy_data_source_id": COPY_DS, "bot_id": BOT}
        self.write_json("context.json", {"schema": 1, "notion": {**values, **notion}})

    def read_json(self, name):
        return json.loads((self.run_dir / name).read_text(encoding="utf-8"))

    def run_main(self, argv, client=None, env=None):
        """Run cqa_notion.main with a controlled environment; return (code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_notion.main([str(item) for item in argv], client=client)
        return code, out.getvalue(), err.getvalue()


class WhoamiTest(CliTestCase):
    def test_prints_the_bot(self):
        client, _ = routed_client(
            {"GET /users/me": ok({"object": "user", "id": BOT, "name": "Audit é", "type": "bot"})}
        )
        code, out, _ = self.run_main(["whoami"], client)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"bot_id": BOT, "name": "Audit é"})
        self.assertIn("Audit é", out)

    def test_missing_token_exit_2(self):
        code, out, err = self.run_main(["whoami"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("NOTION_TOKEN", err)

    def test_blank_token_exit_2(self):
        code, _, err = self.run_main(["whoami"], env={"NOTION_TOKEN": "  "})
        self.assertEqual(code, 2)
        self.assertIn("NOTION_TOKEN", err)

    def test_every_command_needs_the_token(self):
        commands = (
            ["resolve", PAGE],
            ["schema", COPY_DS],
            ["rows", self.run_dir],
            ["fiches", self.run_dir],
            ["comments", self.run_dir],
        )
        for argv in commands:
            with self.subTest(command=argv[0]):
                code, _, err = self.run_main(argv)
                self.assertEqual(code, 2)
                self.assertIn("NOTION_TOKEN", err)

    def test_token_from_the_environment_builds_the_client(self):
        with mock.patch("cqa_notion.NotionClient") as factory:
            factory.return_value.me.return_value = {"id": BOT, "name": "Audit"}
            code, out, _ = self.run_main(["whoami"], env={"NOTION_TOKEN": " secret-token\n"})
        self.assertEqual(code, 0)
        factory.assert_called_once_with("secret-token")
        self.assertEqual(json.loads(out)["bot_id"], BOT)

    def test_api_error_exit_3(self):
        client, _, _ = make_client([failure(401, "unauthorized", "API token is invalid.")])
        code, out, err = self.run_main(["whoami"], client)
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("401", err)
        self.assertIn("unauthorized", err)

    def test_usage_error_exit_2(self):
        for argv in ([], ["unknown"], ["resolve"]):
            with self.subTest(argv=argv):
                code, out, _ = self.run_main(argv, routed_client({})[0])
                self.assertEqual(code, 2)
                self.assertEqual(out, "")

    def test_help_exit_0(self):
        code, _, _ = self.run_main(["--help"], routed_client({})[0])
        self.assertEqual(code, 0)


class ResolveCommandTest(CliTestCase):
    def test_prints_the_resolution(self):
        client, _, _ = make_client([database(COPY_DS)])
        code, out, _ = self.run_main(["resolve", f"https://www.notion.so/{fake_id(2)}"], client)
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out), {"page_id": None, "database_id": DATABASE, "data_source_id": COPY_DS}
        )

    def test_two_data_sources_exit_2(self):
        client, _, _ = make_client([database(COPY_DS, OTHER_DS)])
        code, out, err = self.run_main(["resolve", DATABASE], client)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("plusieurs data sources", err)

    def test_bad_reference_exit_2(self):
        code, _, err = self.run_main(["resolve", "pas-un-lien"], routed_client({})[0])
        self.assertEqual(code, 2)
        self.assertIn("Notion", err)


class SchemaCommandTest(CliTestCase):
    def fixture_response(self):
        return ok(json.loads((FIXTURES / "data_source_v2.json").read_text(encoding="utf-8")))

    def test_valid_schema(self):
        client, transport, _ = make_client([self.fixture_response()])
        code, out, _ = self.run_main(["schema", COPY_DS], client)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["ok"], True)
        self.assertEqual(json.loads(out)["problems"], [])
        self.assertTrue(transport.calls[0].url.endswith(f"/data_sources/{COPY_DS}"))

    def test_schema_lists_assignee_options(self):
        client, _, _ = make_client([self.fixture_response()])
        code, out, _ = self.run_main(["schema", COPY_DS], client)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignee_options"], ["Alice", "Bruno", "Chloé"])
        self.assertIn("Chloé", out)

    def test_invalid_schema_exit_6(self):
        body = self.fixture_response()[2]
        del body["properties"]["Réf modèle"]
        client, _, _ = make_client([ok(body)])
        code, out, _ = self.run_main(["schema", COPY_DS], client)
        self.assertEqual(code, 6)
        report = json.loads(out)
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"], ["propriété manquante : Réf modèle"])
        self.assertEqual(report["assignee_options"], ["Alice", "Bruno", "Chloé"])

    def test_invalid_schema_without_assignee_property(self):
        body = self.fixture_response()[2]
        del body["properties"]["Assignée à"]
        client, _, _ = make_client([ok(body)])
        code, out, _ = self.run_main(["schema", COPY_DS], client)
        self.assertEqual(code, 6)
        self.assertEqual(json.loads(out)["assignee_options"], [])

    def test_bad_identifier_exit_2(self):
        client, transport, _ = make_client([])
        code, _, _ = self.run_main(["schema", "../pages"], client)
        self.assertEqual(code, 2)
        self.assertEqual(transport.calls, [])


class RowsCommandTest(CliTestCase):
    REFERENCE_URL = f"https://www.notion.so/ws/Modele-{fake_id(4)}"

    def routes(self, ref_ds=REF_DS, copy_pages=None, ref_pages=None):
        copy_pages = copy_pages or [
            page("c1", 12, "Activer la compression", editor=BOT),
            page("c2", 13, "Forcer HTTPS", status="En standby"),
        ]
        ref_pages = ref_pages or [
            page("r1", 79, "Activer la compression"),
            page("r2", 80, "Forcer HTTPS"),
            page("r3", 81, "Limiter les redirections"),
        ]
        return {
            f"POST /data_sources/{COPY_DS}/query": ok(listing(copy_pages)[2]),
            f"GET /databases/{REF_DATABASE}": database(ref_ds),
            f"POST /data_sources/{ref_ds}/query": ok(listing(ref_pages)[2]),
        }

    def run_rows(self, routes=None):
        client, transport = routed_client(routes or self.routes())
        env = {"NOTION_TOKEN": "tok", "GM_CQA_REFERENCE": self.REFERENCE_URL}
        return self.run_main(["rows", self.run_dir], client, env) + (transport,)

    def test_rows_cli_writes_rows_json(self):
        self.write_context()
        code, out, err, _ = self.run_rows()
        self.assertEqual((code, err), (0, ""))
        document = self.read_json("rows.json")
        self.assertEqual(list(document), ["copy_data_source_id", "rows", "missing_in_copy"])
        self.assertEqual(document["copy_data_source_id"], COPY_DS)
        self.assertEqual(len(document["rows"]), 2)
        self.assertEqual(
            document["missing_in_copy"], [{"ref": 81, "title": "Limiter les redirections"}]
        )
        first, second = document["rows"]
        self.assertEqual(
            (first["page_id"], first["ref"], first["reference_page_id"], first["fiche_path"]),
            ("c1", 79, "r1", "fiches/CQ-079.md"),
        )
        self.assertEqual(
            (second["ref"], second["reference_page_id"], second["fiche_path"], second["status"]),
            (80, "r2", "fiches/CQ-080.md", "En standby"),
        )
        self.assertTrue(first["last_edited_by_bot"])
        self.assertFalse(second["last_edited_by_bot"])
        self.assertEqual(
            json.loads(out), {"rows": 2, "matched": 2, "unmatched": 0, "missing_in_copy": 1}
        )

    def test_rows_json_keeps_accents(self):
        self.write_context()
        self.run_rows()
        text = (self.run_dir / "rows.json").read_text(encoding="utf-8")
        self.assertIn("Limiter les redirections", text)
        self.assertIn("À traiter", text)
        self.assertNotIn("\\u", text)

    def test_unmatched_row_is_written_without_ref(self):
        self.write_context()
        copy_pages = [page("c1", 79, "Activer la compression"), page("c2", 79, "Tâche ajoutée")]
        ref_pages = [page("r1", 79, "Activer la compression")]
        code, out, _, _ = self.run_rows(self.routes(copy_pages=copy_pages, ref_pages=ref_pages))
        self.assertEqual(code, 0)
        rows = self.read_json("rows.json")["rows"]
        self.assertEqual([item["ref"] for item in rows], [79, None])
        self.assertIsNone(rows[1]["fiche_path"])
        self.assertIsNone(rows[1]["reference_page_id"])
        self.assertEqual(json.loads(out)["unmatched"], 1)

    def test_rows_reads_only(self):
        self.write_context()
        _, _, _, transport = self.run_rows()
        self.assertEqual({call.method for call in transport.calls}, {"GET", "POST"})
        posts = [call for call in transport.calls if call.method == "POST"]
        self.assertTrue(all(call.url.split("?")[0].endswith("/query") for call in posts))

    def test_reference_is_resolved_from_the_environment(self):
        self.write_context()
        _, _, _, transport = self.run_rows()
        self.assertIn(f"GET /databases/{REF_DATABASE}", transport.keys())

    def test_missing_reference_variable_exit_2(self):
        self.write_context()
        client, transport = routed_client({})
        code, out, err = self.run_main(["rows", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("GM_CQA_REFERENCE", err)
        self.assertEqual(transport.calls, [])

    def test_missing_context_exit_2(self):
        code, _, err, transport = self.run_rows()
        self.assertEqual(code, 2)
        self.assertIn("context.json", err)
        self.assertEqual(transport.calls, [])

    def test_context_without_bot_exit_2(self):
        self.write_context(bot_id="")
        code, _, err, transport = self.run_rows()
        self.assertEqual(code, 2)
        self.assertIn("bot_id", err)
        self.assertEqual(transport.calls, [])

    def test_unreadable_context_exit_2(self):
        (self.run_dir / "context.json").write_text("{not json", encoding="utf-8")
        code, _, err, _ = self.run_rows()
        self.assertEqual(code, 2)
        self.assertIn("context.json", err)

    def test_reference_equal_to_copy_exit_2(self):
        self.write_context()
        routes = self.routes(ref_ds=COPY_DS)
        code, out, err, _ = self.run_rows(routes)
        self.assertEqual(code, 2)
        self.assertIn("même data source", err)
        self.assertFalse((self.run_dir / "rows.json").exists())

    def test_api_error_exit_3_and_no_rows_json(self):
        self.write_context()
        routes = self.routes()
        routes[f"POST /data_sources/{COPY_DS}/query"] = failure(403, "restricted_resource")
        code, _, err, _ = self.run_rows(routes)
        self.assertEqual(code, 3)
        self.assertIn("restricted_resource", err)
        self.assertFalse((self.run_dir / "rows.json").exists())


def dated_comment(text, when, author="user-1"):
    return {
        "object": "comment",
        "created_by": {"object": "user", "id": author},
        "created_time": when,
        "rich_text": [{"plain_text": text}],
    }


class RecetteCommandTest(CliTestCase):
    REFERENCE_URL = f"https://www.notion.so/ws/Modele-{fake_id(4)}"
    ENV = {"NOTION_TOKEN": "tok", "GM_CQA_REFERENCE": REFERENCE_URL}

    def routes(self, comments=None):
        copy_pages = [
            page("c1", 79, "Activer la compression", status="Mis en place"),
            page("c2", 80, "Forcer HTTPS", status="Mis en place"),
            page("c3", 81, "Limiter les redirections"),
            page("c4", 82, "Autre ligne"),
        ]
        ref_pages = [
            page("r1", 79, "Activer la compression"),
            page("r2", 80, "Forcer HTTPS"),
            page("r3", 81, "Limiter les redirections"),
            page("r4", 82, "Autre ligne"),
        ]
        routes = {
            f"POST /data_sources/{COPY_DS}/query": ok(listing(copy_pages)[2]),
            f"GET /databases/{REF_DATABASE}": database(REF_DS),
            f"POST /data_sources/{REF_DS}/query": ok(listing(ref_pages)[2]),
        }
        for block in ("c1", "c2", "c3"):
            routes[f"GET /comments?block_id={block}"] = ok(listing((comments or {}).get(block, []))[2])
        return routes

    def write_audit(self, *, journal=True):
        plan_rows = [
            {"page_id": "c1", "ref": 79, "blocking": False, "before": "À traiter", "after": "À traiter"},
            {"page_id": "c2", "ref": 80, "blocking": False, "before": "À traiter", "after": "Mis en place"},
        ]
        operation = {"key": "status:c2", "kind": "status", "ref": 80, "before": "À traiter",
                     "after": "Mis en place"}
        self.write_json("notion/plan.json", {"rows": plan_rows, "ops": [operation]})
        entry = {"key": "status:c2", "kind": "status", "ref": 80, "verdict": "ok_human"}
        self.write_json("notion/applied.json", {"applied": [entry] if journal else []})

    def run_recette(self, comments=None, git_run=None, options=()):
        client, transport = routed_client(self.routes(comments))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.ENV, clear=True):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_notion.main(
                    ["recette", str(self.run_dir), *options], client=client, git_run=git_run
                )
        return code, out.getvalue(), err.getvalue(), transport

    def setUp(self):
        super().setUp()
        (self.run_dir / "notion").mkdir()
        (self.run_dir / "findings").mkdir()
        self.write_context()

    def test_recette_writes_the_rows_the_devs_fixed(self):
        self.write_audit()
        comments = {
            "c1": [
                dated_comment("Premier avis", "2026-10-02T10:00:00.000Z"),
                dated_comment("Constat\nVerdict : À traiter", "2026-10-01T10:00:00.000Z", BOT),
                dated_comment("MR : https://gitlab.example.org/acme/back/-/merge_requests/7",
                              "2026-10-03T10:00:00.000Z"),
            ]
        }
        code, out, err, transport = self.run_recette(comments)
        self.assertEqual(code, 0, err)
        document = self.read_json("notion/recette.json")
        self.assertEqual(list(document), ["rows"])
        [item] = document["rows"]
        self.assertEqual(
            item,
            {
                "ref": 79,
                "page_id": "c1",
                "status": "Mis en place",
                "skill_last": "À traiter",
                "comments": [
                    "MR : https://gitlab.example.org/acme/back/-/merge_requests/7",
                    "Premier avis",
                ],
            },
        )
        self.assertEqual(json.loads(out)["recette"], [79])
        self.assertEqual(len(self.read_json("rows.json")["rows"]), 4)
        posts = [call for call in transport.calls if call.method == "POST"]
        self.assertTrue(posts)
        self.assertTrue(all(call.url.split("?")[0].endswith("/query") for call in posts))
        self.assertEqual({call.method for call in transport.calls}, {"GET", "POST"})

    def test_recette_command_is_registered(self):
        code, _, _ = self.run_main(["recette", "--help"], None, {})
        self.assertEqual(code, 0)

    def test_recette_without_and_refs_options(self):
        self.write_audit()
        code, out, err, _ = self.run_recette(options=["--without", "CQ-79", "--refs", "CQ-81,CQ-999"])
        self.assertEqual(code, 0, err)
        found = {item["ref"]: item for item in self.read_json("notion/recette.json")["rows"]}
        self.assertEqual(sorted(found), [81])
        self.assertEqual(found[81]["status"], "À traiter")
        self.assertIn("CQ-999", err)
        code, _, _, _ = self.run_recette(options=[])
        refs = [item["ref"] for item in self.read_json("notion/recette.json")["rows"]]
        self.assertEqual(refs, [79])
        code, _, err, _ = self.run_recette(options=["--refs", "abc"])
        self.assertEqual(code, 2)

    def test_recette_still_a_candidate_after_a_later_plan(self):
        self.write_json("notion/plan.json", {"rows": [
            {"page_id": "c1", "ref": 79, "blocking": False, "before": "Mis en place",
             "after": "Mis en place"}], "ops": []})
        entry = {"key": "old", "kind": "status", "ref": 79, "value": "À traiter"}
        self.write_json("notion/applied.json", {"applied": [entry]})
        self.run_recette()
        [item] = self.read_json("notion/recette.json")["rows"][:1]
        self.assertEqual((item["ref"], item["skill_last"]), (79, "À traiter"))

    def test_recette_masks_secrets_in_the_comments(self):
        self.write_audit()
        secret = "DB_PASSWORD=Adm1nP4ss2024"
        self.run_recette({"c1": [dated_comment(f"Config : {secret}", "2026-10-02T10:00:00.000Z")]})
        text = (self.run_dir / "notion" / "recette.json").read_text(encoding="utf-8")
        self.assertNotIn("Adm1nP4ss2024", text)
        self.assertIn("Config", text)

    def test_recette_prints_the_notes_of_the_candidate_search(self):
        self.write_audit()
        code, _, err, _ = self.run_recette()
        self.assertIn("lots.json", err)

    def test_recette_warns_about_an_unreadable_journal(self):
        self.write_audit()
        (self.run_dir / "notion" / "applied.json").write_text("{", encoding="utf-8")
        code, _, err, _ = self.run_recette()
        self.assertEqual(code, 0)
        self.assertIn("applied.json est illisible", err)
        self.assertIn("se déduit du plan", err)
        self.assertNotIn("plan.json est illisible", err)

    def test_recette_creates_the_notion_folder(self):
        (self.run_dir / "notion").rmdir()
        code, _, err, _ = self.run_recette()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.run_dir / "notion" / "recette.json").is_file())

    def test_recette_keeps_five_comments_at_most(self):
        self.write_audit()
        many = [dated_comment(f"avis {n}", f"2026-10-{n:02d}T10:00:00.000Z") for n in range(1, 8)]
        self.run_recette({"c1": many})
        [item] = self.read_json("notion/recette.json")["rows"]
        self.assertEqual(item["comments"], [f"avis {n}" for n in range(7, 2, -1)])

    def test_recette_on_a_legacy_run(self):
        self.write_audit(journal=False)
        entry = {"key": "comment:c1:abc", "kind": "comment", "ref": 79, "verdict": "ko"}
        self.write_json("notion/applied.json", {"applied": [entry]})
        code, _, err, _ = self.run_recette({"c1": [dated_comment("ok", "2026-10-02T10:00:00.000Z")]})
        self.assertEqual(code, 0, err)
        refs = [item["ref"] for item in self.read_json("notion/recette.json")["rows"]]
        self.assertEqual(refs, [79, 80])

    def test_recette_without_any_journal(self):
        code, _, err, _ = self.run_recette()
        self.assertEqual(code, 0, err)
        refs = [item["ref"] for item in self.read_json("notion/recette.json")["rows"]]
        self.assertEqual(refs, [79, 80])

    def test_recette_adds_the_rows_whose_code_changed(self):
        self.write_audit()
        self.write_json(
            "lots.json",
            {"lots": [{"id": "code-a", "kind": "code", "refs": [79, 80, 81, 82]}], "excluded": []},
        )
        evidence = [{"type": "file", "repo": "laravel", "path": "app/Redirect.php", "line": 1}]
        finding = {
            "ref": 81, "verdict": "ko", "confidence": "high", "summary": "x", "evidence": evidence,
            "todo": "x", "human_check": None, "divergence": None, "needs_probes": [],
        }
        self.write_json("findings/code-a.json", {"lot": "code-a", "findings": [finding]})
        context = self.read_json("context.json")
        context["repos"] = [{"name": "laravel", "path": "/work/repos/laravel", "audited_sha": "a" * 40}]
        self.write_json("context.json", context)

        def git(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "app/Redirect.php\n", "")

        code, _, err, _ = self.run_recette(git_run=git)
        self.assertEqual(code, 0, err)
        found = {item["ref"]: item for item in self.read_json("notion/recette.json")["rows"]}
        self.assertEqual(sorted(found), [79, 81])
        self.assertEqual(found[81]["status"], "À traiter")
        self.assertEqual(found[81]["comments"], [])


class FichesCommandTest(CliTestCase):
    def write_rows(self):
        rows = [
            row("c1", 79, reference_page_id="r1"),
            row("c2", 80, reference_page_id="r2"),
            row("c3", None),
        ]
        self.write_rows_json(rows)

    def routes(self):
        return {
            "GET /pages/r1/markdown": markdown("# Un\n"),
            "GET /pages/r2/markdown": markdown("# Deux\n", truncated=True),
        }

    def test_writes_the_fiches(self):
        self.write_rows()
        client, transport = routed_client(self.routes())
        code, out, _ = self.run_main(["fiches", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.run_dir / "fiches" / "CQ-079.md").read_text(encoding="utf-8"), "# Un\n"
        )
        self.assertEqual(
            self.read_json("fiches/index.json"),
            {
                "79": {"path": "fiches/CQ-079.md", "truncated": False},
                "80": {"path": "fiches/CQ-080.md", "truncated": True},
            },
        )
        self.assertEqual(json.loads(out), {"fiches": 2, "truncated": [80]})

    def test_fiches_cached(self):
        self.write_rows()
        client, transport = routed_client(self.routes())
        env = {"NOTION_TOKEN": "tok"}
        self.run_main(["fiches", self.run_dir], client, env)
        requests = len(transport.calls)
        code, out, _ = self.run_main(["fiches", self.run_dir], client, env)
        self.assertEqual(code, 0)
        self.assertEqual(len(transport.calls), requests)
        self.assertEqual(json.loads(out), {"fiches": 2, "truncated": [80]})

    def test_rows_json_required(self):
        client, transport = routed_client({})
        code, out, err = self.run_main(["fiches", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("rows.json", err)
        self.assertEqual(transport.calls, [])

    def test_api_error_exit_3_keeps_the_index(self):
        self.write_rows()
        routes = self.routes()
        routes["GET /pages/r2/markdown"] = failure(404)
        client, _ = routed_client(routes)
        code, _, err = self.run_main(["fiches", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 3)
        self.assertIn("404", err)
        self.assertEqual(list(self.read_json("fiches/index.json")), ["79"])


class CommentsCommandTest(CliTestCase):
    def write_rows(self):
        rows = [
            row("p1", 79),
            row("p2", 80, status="En standby"),
            row("p3", None),
            row("p4", 82),
        ]
        self.write_rows_json(rows)

    def test_writes_comments_json(self):
        self.write_context()
        self.write_rows()
        client, transport = routed_client(
            {
                "GET /comments?block_id=p1": listing(
                    [comment("Verdict : À traiter"), comment("Verdict : Ok / Recetté", "user-1")]
                ),
                "GET /comments?block_id=p4": listing([comment("Rien")]),
            }
        )
        code, out, _ = self.run_main(["comments", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 0)
        self.assertEqual(self.read_json("comments.json"), {"79": ["ko"]})
        self.assertEqual(json.loads(out), {"refs_with_verdicts": 1})
        self.assertEqual(
            transport.keys(), ["GET /comments?block_id=p1", "GET /comments?block_id=p4"]
        )

    def test_no_verdict_writes_an_empty_object(self):
        self.write_context()
        self.write_rows_json([])
        client, _ = routed_client({})
        code, out, _ = self.run_main(["comments", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 0)
        self.assertEqual(self.read_json("comments.json"), {})

    def test_rows_json_required(self):
        self.write_context()
        client, _ = routed_client({})
        code, _, err = self.run_main(["comments", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 2)
        self.assertIn("rows.json", err)

    def test_context_required(self):
        self.write_rows()
        client, _ = routed_client({})
        code, _, err = self.run_main(["comments", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 2)
        self.assertIn("context.json", err)

    def test_api_error_exit_3_and_no_comments_json(self):
        self.write_context()
        self.write_rows()
        forbidden = failure(403, "restricted_resource")
        client, _ = routed_client({"GET /comments?block_id=p1": forbidden})
        code, _, _ = self.run_main(["comments", self.run_dir], client, {"NOTION_TOKEN": "tok"})
        self.assertEqual(code, 3)
        self.assertFalse((self.run_dir / "comments.json").exists())


class SourceHygieneTest(unittest.TestCase):
    def test_no_em_dash(self):
        root = Path(__file__).resolve().parents[2]
        paths = [
            root / "scripts" / "cqa" / "notion_read.py",
            root / "scripts" / "cqa_notion.py",
            Path(__file__),
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
