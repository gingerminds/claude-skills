import contextlib
import io
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import cqa_notion
from cqa.comments import to_rich_text
from cqa.notion_api import NotionClient, NotionError
from cqa.notion_write import ReferenceWriteRefused, apply_plan
from cqa.plan import operation_key
from tests.cqa.fakes import Call, FakeClock, apply_report, failure

BOT = "bot-1"
OPTIONS = ["Inès", "Côme D"]
SECRET_HEADER = "Authorization: Basic dXNlcjpwYXNz"


def dashed(number):
    """Return a dashed UUID built from a number, so no id literal lands in the sources."""
    return str(uuid.UUID(f"{number:032x}"))


REF_DATABASE = dashed(4)
COPY_DS = dashed(3)
REF_DS = dashed(5)
REF_PAGE = dashed(6)
COPY_PAGE = dashed(10)
PAGE_1, PAGE_2, PAGE_3 = dashed(21), dashed(22), dashed(23)
OUTSIDE_PAGE = dashed(99)


def reply(status, body):
    return status, {}, json.dumps(body).encode("utf-8")


def http_failure(status, code="object_not_found", message="Could not find page"):
    """Return the (status, body) a FakeNotion page error is scripted with."""
    return status, failure(status, code, message)[2]


class FakeNotion:
    """Stateful transport: stores comments and page updates, and can lose a connection.

    post_faults maps the number of a POST /comments to "lost_request" (nothing stored) or
    "lost_response" (stored, but the client sees a network failure). page_errors maps a page id
    to an HTTP failure (status, body), page_unreachable holds pages whose PATCH never answers
    and comments_unreachable makes every GET /comments fail. A post fault may also be
    "rate_limited" (429 with Retry-After, nothing stored). live_options are the "Assignée à"
    options of the copy, and data_source_error an HTTP failure (status, body) of its schema read.
    pages maps a page id to the id of its child database, for resolve.
    writes lists what was stored, in order: (label, page id, payload).
    """

    def __init__(self):
        self.calls = []
        self.writes = []
        self.comments = {}
        self.databases = {}
        self.pages = {}
        self.post_faults = {}
        self.page_errors = {}
        self.page_unreachable = set()
        self.comments_unreachable = False
        self.live_options = list(OPTIONS)
        self.data_source_error = None
        self.posts = 0
        self.comment_errors = {}

    def __call__(self, method, url, headers, data):
        self.calls.append(Call(method, url, dict(headers), data))
        parts = urlsplit(url)
        path = parts.path[len("/v1") :]
        body = None if data is None else json.loads(data)
        if method == "GET" and path == "/comments":
            if self.comments_unreachable:
                raise URLError("unreachable")
            block = parse_qs(parts.query)["block_id"][0]
            results = self.comments.get(block, [])
            return reply(
                200, {"object": "list", "results": results, "has_more": False, "next_cursor": None}
            )
        if method == "POST" and path == "/comments":
            return self._post_comment(body)
        if path.startswith("/comments/") and method in ("PATCH", "DELETE"):
            return self._change_comment(method, path.rsplit("/", 1)[1], body)
        if method == "PATCH" and path.startswith("/pages/"):
            return self._patch_page(path.rsplit("/", 1)[1], body["properties"])
        if method == "GET" and path.startswith("/data_sources/"):
            if self.data_source_error:
                return reply(*self.data_source_error)
            listed = [{"name": name} for name in self.live_options]
            schema = {"Assignée à": {"type": "multi_select", "multi_select": {"options": listed}}}
            return reply(200, {"object": "data_source", "properties": schema})
        if method == "GET" and path.startswith("/blocks/") and path.endswith("/children"):
            database = self.pages[path.split("/")[2]]
            results = [{"type": "child_database", "id": database}]
            return reply(
                200, {"object": "list", "results": results, "has_more": False, "next_cursor": None}
            )
        if method == "GET" and path.startswith("/databases/"):
            if path.rsplit("/", 1)[1] in self.pages:
                return reply(*http_failure(404, "object_not_found", "Not a database"))
            sources = self.databases[path.rsplit("/", 1)[1]]
            listed = [{"id": item, "name": "Source"} for item in sources]
            return reply(200, {"data_sources": listed})
        raise AssertionError(f"unscripted request: {method} {path}")

    def _post_comment(self, body):
        self.posts += 1
        fault = self.post_faults.get(self.posts)
        if fault == "lost_request":
            raise URLError("connection reset")
        if fault == "rate_limited":
            payload = failure(429, "rate_limited", "Slow down")[2]
            return 429, {"Retry-After": "2"}, json.dumps(payload).encode("utf-8")
        page = body["parent"]["page_id"]
        text = "".join(item["text"]["content"] for item in body["rich_text"])
        entry = {
            "object": "comment",
            "id": f"created-{self.posts}",
            "created_by": {"object": "user", "id": BOT},
            "rich_text": [{"plain_text": item["text"]["content"]} for item in body["rich_text"]],
        }
        self.comments.setdefault(page, []).append(entry)
        self.writes.append(("comment", page, text))
        if fault == "lost_response":
            raise URLError("connection reset")
        return reply(200, {"object": "comment", "id": entry["id"]})

    def _change_comment(self, method, comment_id, body):
        action = "update" if method == "PATCH" else "delete"
        if action in self.comment_errors:
            status, payload = self.comment_errors[action]
            return reply(status, payload)
        for page, entries in self.comments.items():
            for entry in entries:
                if entry.get("id") != comment_id:
                    continue
                if action == "delete":
                    entries.remove(entry)
                else:
                    text = "".join(item["text"]["content"] for item in body["rich_text"])
                    entry["rich_text"] = [{"plain_text": text}]
                self.writes.append((action, page, comment_id))
                return reply(200, {"object": "comment", "id": comment_id})
        return reply(*http_failure(404, "object_not_found", "Could not find comment"))

    def _patch_page(self, page, properties):
        if page in self.page_unreachable:
            raise URLError("unreachable")
        if page in self.page_errors:
            status, payload = self.page_errors[page]
            return reply(status, payload)
        label = "status" if "Statut" in properties else "assign"
        self.writes.append((label, page, properties))
        return reply(200, {"object": "page", "id": page})

    def add_comment(self, page, text, author=BOT, comment_id=None, created=None):
        entry = {
            "object": "comment",
            "id": comment_id or f"seed-{len(self.comments.get(page, []))}",
            "created_time": created or "2026-10-01T10:00:00.000Z",
            "created_by": {"object": "user", "id": author},
            "rich_text": [{"plain_text": text}],
        }
        self.comments.setdefault(page, []).append(entry)

    def texts(self, page):
        return [
            "".join(part["plain_text"] for part in entry["rich_text"])
            for entry in self.comments.get(page, [])
        ]

    def stored(self, page):
        return [text for label, where, text in self.writes if label == "comment" and where == page]

    def requests(self, method):
        return [call for call in self.calls if call.method == method]

    def write_requests(self):
        return self.requests("PATCH") + self.requests("POST")


def verdict_text(label, note="Constat"):
    return f"{note}\nVerdict : {label}"


class StubClient:
    """Exposes the three methods apply_plan may use. Any other access fails the test."""

    def __init__(self):
        self.updates = []
        self.created = []

    def update_page(self, page_id, properties):
        self.updates.append((page_id, properties))
        return {}

    def create_comment(self, page_id, rich_text):
        self.created.append((page_id, rich_text))
        return {}

    def comments(self, block_id):
        return []

    def data_source(self, data_source_id):
        listed = [{"name": name} for name in OPTIONS]
        return {"properties": {"Assignée à": {"multi_select": {"options": listed}}}}


def op(kind, page_id, ref, *, after=None, text="", verdict="ko", before=None, override=False):
    return {
        "key": operation_key(kind, page_id, after, text),
        "kind": kind,
        "page_id": page_id,
        "ref": ref,
        "verdict": verdict,
        "before": before,
        "after": after,
        "text": text,
        "human_override": override,
    }


def status_op(page_id, ref, after="Ok / Recetté", before="À traiter", override=False):
    return op("status", page_id, ref, after=after, before=before, override=override)


def assign_op(page_id, ref, after):
    return op("assign", page_id, ref, after=after, before=[], verdict="ok_human")


def comment_op(page_id, ref, text="Constat : à corriger"):
    return op("comment", page_id, ref, text=text)


def page_comment_op(text="Décompte par statut", page_id=COPY_PAGE):
    return op("page_comment", page_id, None, text=text, verdict=None)


def row_state(page_id, ref, before, after="Ok / Recetté"):
    return {"page_id": page_id, "ref": ref, "blocking": False, "before": before, "after": after}


def make_plan(*ops, copy_ds=COPY_DS, options=OPTIONS):
    return {
        "run_id": "2026-10-20-1430",
        "copy_data_source_id": copy_ds,
        "copy_page_id": COPY_PAGE,
        "assignee_options": list(options),
        "ops": list(ops),
        "summary": {},
        "rows": [],
    }


class ApplyTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name)
        self.notion = FakeNotion()
        self.clock = FakeClock()
        self.log = []

    def client(self):
        return NotionClient(
            "tok", transport=self.notion, sleep=self.clock.sleep, clock=self.clock
        )

    def apply(self, *ops, client=None, plan=None, **overrides):
        options = {
            "run_dir": self.run_dir,
            "reference_ds_id": REF_DS,
            "row_page_ids": {PAGE_1, PAGE_2, PAGE_3},
            "copy_page_id": COPY_PAGE,
            "known_secrets": [],
            "bot_id": BOT,
            "log": self.log.append,
        }
        options.update(overrides)
        return apply_plan(client or self.client(), plan or make_plan(*ops), **options)

    def applied_path(self):
        return self.run_dir / "notion" / "applied.json"

    def applied(self):
        return json.loads(self.applied_path().read_text(encoding="utf-8"))["applied"]

    def applied_keys(self):
        return [entry["key"] for entry in self.applied()]


class RequestBodiesTest(ApplyTestCase):
    def test_status_body(self):
        result = self.apply(status_op(PAGE_1, 1, after="Mise en place partielle"))
        self.assertEqual(result, apply_report(1, 0))
        [call] = self.notion.requests("PATCH")
        self.assertTrue(call.url.endswith(f"/v1/pages/{PAGE_1}"))
        self.assertEqual(
            call.body, {"properties": {"Statut": {"status": {"name": "Mise en place partielle"}}}}
        )

    def test_assign_body_keeps_the_order_of_names(self):
        self.apply(assign_op(PAGE_1, 1, ["Inès", "Côme D"]))
        [call] = self.notion.requests("PATCH")
        expected = {"Assignée à": {"multi_select": [{"name": "Inès"}, {"name": "Côme D"}]}}
        self.assertEqual(call.body, {"properties": expected})

    def test_comment_body(self):
        text = "Constat : voir https://example.org/page.\nÀ faire : corriger."
        self.apply(comment_op(PAGE_1, 1, text))
        [call] = self.notion.requests("POST")
        self.assertTrue(call.url.endswith("/v1/comments"))
        self.assertEqual(
            call.body, {"parent": {"page_id": PAGE_1}, "rich_text": to_rich_text(text)}
        )

    def test_only_the_public_client_methods_are_used(self):
        client = StubClient()
        plan_ops = [
            status_op(PAGE_1, 1),
            assign_op(PAGE_1, 1, ["Inès"]),
            comment_op(PAGE_1, 1, "Constat"),
            page_comment_op("Synthèse"),
        ]
        result = self.apply(*plan_ops, client=client)
        self.assertEqual(result["done"], 4)
        self.assertEqual(
            client.updates,
            [
                (PAGE_1, {"Statut": {"status": {"name": "Ok / Recetté"}}}),
                (PAGE_1, {"Assignée à": {"multi_select": [{"name": "Inès"}]}}),
            ],
        )
        self.assertEqual(
            client.created,
            [(PAGE_1, to_rich_text("Constat")), (COPY_PAGE, to_rich_text("Synthèse"))],
        )

    def test_assignment_may_clear_the_assignees(self):
        result = self.apply(assign_op(PAGE_1, 1, []))
        self.assertEqual(result["done"], 1)
        [call] = self.notion.requests("PATCH")
        self.assertEqual(call.body, {"properties": {"Assignée à": {"multi_select": []}}})


class ReferenceProtectionTest(ApplyTestCase):
    def test_reference_refused(self):
        plan = make_plan(status_op(PAGE_1, 1), copy_ds=REF_DS)
        with self.assertRaises(ValueError) as caught:
            self.apply(plan=plan)
        self.assertIsInstance(caught.exception, ReferenceWriteRefused)
        self.assertEqual(str(caught.exception), "écriture refusée : data source du modèle")
        self.assertEqual(self.notion.calls, [])
        self.assertFalse(self.applied_path().exists())

    def test_reference_page_refused(self):
        for reference in (REF_PAGE, REF_PAGE.replace("-", "").upper()):
            with self.subTest(reference=reference):
                with self.assertRaises(ReferenceWriteRefused) as caught:
                    self.apply(
                        status_op(PAGE_1, 1),
                        page_comment_op(page_id=REF_PAGE),
                        copy_page_id=REF_PAGE,
                        reference_page_id=reference,
                    )
                self.assertEqual(str(caught.exception), "écriture refusée : page du modèle")
        self.assertEqual(self.notion.calls, [])
        self.assertFalse(self.applied_path().exists())

    def test_known_reference_page_lets_the_copy_page_be_commented(self):
        result = self.apply(page_comment_op(), reference_page_id=REF_PAGE)
        self.assertEqual(result["done"], 1)

    def test_reference_refused_whatever_the_id_spelling(self):
        for copy_ds, reference in (
            (REF_DS.replace("-", ""), REF_DS),
            (REF_DS, REF_DS.replace("-", "").upper()),
        ):
            with self.subTest(copy_ds=copy_ds):
                plan = make_plan(status_op(PAGE_1, 1), copy_ds=copy_ds)
                with self.assertRaises(ReferenceWriteRefused):
                    self.apply(plan=plan, reference_ds_id=reference)
        self.assertEqual(self.notion.calls, [])

    def test_plan_without_copy_data_source_is_refused(self):
        for value in (None, ""):
            with self.subTest(value=value):
                plan = make_plan(status_op(PAGE_1, 1), copy_ds=value)
                with self.assertRaises(ValueError):
                    self.apply(plan=plan)
        self.assertEqual(self.notion.calls, [])

    def test_missing_reference_id_is_refused(self):
        with self.assertRaises(ValueError):
            self.apply(status_op(PAGE_1, 1), reference_ds_id="")
        self.assertEqual(self.notion.calls, [])


class AllowedPagesTest(ApplyTestCase):
    def test_page_outside_the_copy_is_blocked_without_request(self):
        refused = status_op(OUTSIDE_PAGE, 9)
        result = self.apply(refused, status_op(PAGE_1, 1))
        self.assertEqual(result["done"], 1)
        self.assertEqual(
            result["blocked"], [{"key": refused["key"], "labels": ["page_outside_copy"]}]
        )
        self.assertEqual([page for _, page, _ in self.notion.writes], [PAGE_1])
        self.assertEqual(len(self.notion.calls), 1)
        self.assertNotIn(refused["key"], self.applied_keys())

    def test_page_ids_are_compared_without_dashes_or_case(self):
        result = self.apply(
            status_op(PAGE_1, 1), row_page_ids={PAGE_1.replace("-", "").upper()}
        )
        self.assertEqual(result["done"], 1)

    def test_page_comment_allowed_on_copy_page(self):
        result = self.apply(page_comment_op("Synthèse"))
        self.assertEqual(result, apply_report(1, 0))
        self.assertEqual(self.notion.stored(COPY_PAGE), ["Synthèse"])

    def test_page_comment_elsewhere_is_blocked(self):
        for page in (PAGE_1, OUTSIDE_PAGE):
            with self.subTest(page=page):
                result = self.apply(page_comment_op(page_id=page))
                self.assertEqual(result["done"], 0)
                labels = [item["labels"] for item in result["blocked"]]
                self.assertEqual(labels, [["page_outside_copy"]])
        self.assertEqual(self.notion.calls, [])

    def test_page_comment_without_copy_page_is_blocked(self):
        result = self.apply(page_comment_op(), copy_page_id=None)
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(self.notion.calls, [])

    def test_comment_on_the_copy_page_needs_a_row_of_that_page(self):
        result = self.apply(comment_op(COPY_PAGE, 1))
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(self.notion.calls, [])


class SecretGateTest(ApplyTestCase):
    def test_secret_blocks_operation(self):
        leaking = comment_op(PAGE_1, 1, f"Constat : en-tête relevé\n{SECRET_HEADER}")
        clean = comment_op(PAGE_2, 2, "Constat : propre")
        result = self.apply(leaking, clean)
        self.assertEqual(result["done"], 1)
        self.assertEqual(
            result["blocked"], [{"key": leaking["key"], "labels": ["authorization_header"]}]
        )
        self.assertNotIn(PAGE_1, self.notion.comments)
        self.assertEqual(self.notion.stored(PAGE_2), ["Constat : propre"])
        for call in self.notion.calls:
            self.assertNotIn(PAGE_1, call.url)
            self.assertNotIn("dXNlcjpwYXNz", call.data.decode("utf-8") if call.data else "")
        self.assertNotIn(leaking["key"], self.applied_keys())

    def test_blocked_operation_is_not_applied_after_a_rerun_either(self):
        leaking = comment_op(PAGE_1, 1, SECRET_HEADER)
        self.apply(leaking)
        result = self.apply(leaking)
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(self.notion.calls, [])

    def test_secret_in_the_target_value_blocks_too(self):
        status = status_op(PAGE_1, 1, after="Ok token=abcdefgh12345678")
        result = self.apply(status)
        self.assertEqual(result["blocked"][0]["labels"], ["credential_assignment"])
        self.assertEqual(self.notion.calls, [])

    def test_secret_in_an_assignee_name_blocks_too(self):
        assign = assign_op(PAGE_1, 1, ["Inès", "s3cr3t-value-xyz"])
        plan = make_plan(assign, options=[*OPTIONS, "s3cr3t-value-xyz"])
        self.notion.live_options = [*OPTIONS, "s3cr3t-value-xyz"]
        result = self.apply(plan=plan, known_secrets=["s3cr3t-value-xyz"])
        self.assertEqual(result["blocked"][0]["labels"], ["known_value"])
        self.assertEqual(self.notion.write_requests(), [])

    def test_known_value_from_the_environment_blocks(self):
        comment = comment_op(PAGE_1, 1, "Le mot de passe est hunter2-hunter2.")
        result = self.apply(comment, known_secrets=["hunter2-hunter2"])
        self.assertEqual(result["blocked"][0]["labels"], ["known_value"])
        self.assertEqual(self.notion.calls, [])

    def test_none_and_empty_values_are_accepted(self):
        status = {**status_op(PAGE_1, 1, before=None), "text": None}
        comment = {**comment_op(PAGE_1, 1, "Constat"), "before": None, "after": None}
        result = self.apply(status, comment)
        self.assertEqual((result["done"], result["blocked"]), (2, []))

    def test_logs_never_carry_the_blocked_text(self):
        self.apply(comment_op(PAGE_1, 1, SECRET_HEADER))
        self.assertTrue(self.log)
        self.assertNotIn("dXNlcjpwYXNz", "\n".join(self.log))


class UnknownOptionTest(ApplyTestCase):
    def test_unknown_option_blocked(self):
        refused = assign_op(PAGE_1, 1, ["Zoé"])
        result = self.apply(refused)
        self.assertEqual(result["done"], 0)
        self.assertEqual(
            result["blocked"], [{"key": refused["key"], "labels": ["unknown_option"]}]
        )
        self.assertEqual(self.notion.write_requests(), [])

    def test_one_unknown_name_blocks_the_whole_assignment(self):
        result = self.apply(assign_op(PAGE_1, 1, ["Inès", "Zoé"]))
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(self.notion.write_requests(), [])

    def test_option_names_are_compared_exactly(self):
        result = self.apply(assign_op(PAGE_1, 1, ["ines"]))
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(self.notion.write_requests(), [])

    def test_plan_without_options_blocks_every_assignment(self):
        plan = make_plan(assign_op(PAGE_1, 1, ["Inès"]), options=[])
        del plan["assignee_options"]
        result = self.apply(plan=plan)
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(self.notion.write_requests(), [])

    def test_known_option_is_written(self):
        result = self.apply(assign_op(PAGE_1, 1, ["Côme D"]))
        self.assertEqual(result["done"], 1)

    def test_option_removed_from_the_copy_since_the_plan_is_blocked(self):
        self.notion.live_options = ["Côme D"]
        lost = assign_op(PAGE_1, 1, ["Inès"])
        kept = assign_op(PAGE_2, 2, ["Côme D"])
        result = self.apply(lost, kept)
        self.assertEqual(result["done"], 1)
        self.assertEqual(result["blocked"], [{"key": lost["key"], "labels": ["unknown_option"]}])
        self.assertEqual([page for _, page, _ in self.notion.writes], [PAGE_2])
        self.assertEqual(len(self.notion.requests("PATCH")), 1)
        self.assertNotIn(lost["key"], self.applied_keys())

    def test_option_added_to_the_copy_but_not_planned_is_blocked(self):
        self.notion.live_options = [*OPTIONS, "Zoé"]
        result = self.apply(assign_op(PAGE_1, 1, ["Zoé"]))
        self.assertEqual(result["blocked"][0]["labels"], ["unknown_option"])
        self.assertEqual(self.notion.write_requests(), [])

    def test_copy_without_the_assignee_property_blocks_every_assignment(self):
        self.notion.live_options = []
        result = self.apply(assign_op(PAGE_1, 1, ["Inès"]))
        self.assertEqual(result["blocked"][0]["labels"], ["unknown_option"])
        self.assertEqual(self.notion.write_requests(), [])

    def test_live_schema_is_read_once_from_the_copy_data_source(self):
        self.apply(assign_op(PAGE_1, 1, ["Inès"]), assign_op(PAGE_2, 2, ["Côme D"]))
        schema_reads = [call for call in self.notion.calls if "/data_sources/" in call.url]
        self.assertEqual(len(schema_reads), 1)
        self.assertTrue(schema_reads[0].url.endswith(f"/v1/data_sources/{COPY_DS}"))
        self.assertEqual(schema_reads[0].method, "GET")

    def test_schema_is_not_read_without_an_assignment(self):
        self.apply(status_op(PAGE_1, 1), comment_op(PAGE_1, 1))
        self.assertEqual([call for call in self.notion.calls if "/data_sources/" in call.url], [])

    def test_schema_read_failure_stops_before_any_write(self):
        self.notion.data_source_error = http_failure(404, "object_not_found", "Gone")
        with self.assertRaises(NotionError):
            self.apply(status_op(PAGE_1, 1), assign_op(PAGE_1, 1, ["Inès"]))
        self.assertEqual(self.notion.write_requests(), [])
        self.assertFalse(self.applied_path().exists())


class OrderTest(ApplyTestCase):
    def test_order_status_before_comment(self):
        shuffled = [
            comment_op(PAGE_2, 2, "Constat 2"),
            page_comment_op("Synthèse"),
            status_op(PAGE_2, 2),
            assign_op(PAGE_2, 2, ["Inès"]),
            comment_op(PAGE_1, 1, "Constat 1"),
            status_op(PAGE_1, 1),
        ]
        self.apply(*shuffled)
        self.assertEqual(
            [(label, page) for label, page, _ in self.notion.writes],
            [
                ("status", PAGE_1),
                ("comment", PAGE_1),
                ("status", PAGE_2),
                ("assign", PAGE_2),
                ("comment", PAGE_2),
                ("comment", COPY_PAGE),
            ],
        )

    def test_refs_are_ordered_numerically(self):
        self.apply(status_op(PAGE_1, 10), status_op(PAGE_2, 9), status_op(PAGE_3, 100))
        self.assertEqual(
            [page for _, page, _ in self.notion.writes], [PAGE_2, PAGE_1, PAGE_3]
        )

    def test_page_comments_come_last_in_plan_order(self):
        self.apply(
            page_comment_op("Un"), comment_op(PAGE_1, 99, "Constat"), page_comment_op("Deux")
        )
        self.assertEqual(
            [(label, text) for label, _, text in self.notion.writes],
            [("comment", "Constat"), ("comment", "Un"), ("comment", "Deux")],
        )


class PlanScopedJournalTest(ApplyTestCase):
    def planned(self, plan_id):
        plan = make_plan(status_op(PAGE_1, 1, after="Mise en place partielle", before="À traiter"))
        if plan_id is not None:
            plan["plan_id"] = plan_id
        return plan

    def test_same_target_status_is_written_again_by_a_later_plan(self):
        first = self.apply(plan=self.planned("aaa"))
        self.assertEqual(first, apply_report(1, 0))
        second = self.apply(plan=self.planned("bbb"))
        self.assertEqual(second, apply_report(1, 0))
        self.assertEqual([label for label, _, _ in self.notion.writes], ["status", "status"])
        self.assertEqual([entry["plan_id"] for entry in self.applied()], ["aaa", "bbb"])

    def test_resuming_the_same_plan_still_skips(self):
        self.apply(plan=self.planned("aaa"))
        again = self.apply(plan=self.planned("aaa"))
        self.assertEqual(again, apply_report(0, 1))
        self.assertEqual(len(self.notion.writes), 1)

    def test_legacy_plan_and_legacy_journal_still_skip(self):
        self.apply(plan=self.planned(None))
        self.assertNotIn("plan_id", self.applied()[0])
        again = self.apply(plan=self.planned(None))
        self.assertEqual(again, apply_report(0, 1))

    def test_legacy_entry_does_not_match_a_plan_with_an_id(self):
        self.apply(plan=self.planned(None))
        again = self.apply(plan=self.planned("aaa"))
        self.assertEqual(again, apply_report(1, 0))


class JournalTest(ApplyTestCase):
    def test_journal_entries(self):
        first, second = status_op(PAGE_1, 1), comment_op(PAGE_1, 1)
        self.apply(first, second)
        entries = self.applied()
        self.assertEqual([entry["key"] for entry in entries], [first["key"], second["key"]])
        self.assertEqual(
            [(entry["kind"], entry["ref"], entry["verdict"]) for entry in entries],
            [("status", 1, "ko"), ("comment", 1, "ko")],
        )
        base = {"key", "kind", "ref", "verdict", "at"}
        for entry in entries:
            extra = {"comment_id", "mode"} if entry["kind"] == "comment" else {"value"}
            self.assertEqual(set(entry), base | extra)
            self.assertIsNotNone(datetime.fromisoformat(entry["at"]).tzinfo)

    def test_journal_is_written_atomically_after_each_success(self):
        ops = [status_op(PAGE_1, 1), comment_op(PAGE_1, 1), status_op(PAGE_2, 2)]
        with mock.patch("os.replace", wraps=os.replace) as replace:
            self.apply(*ops)
        targets = [Path(call.args[1]) for call in replace.call_args_list]
        sources = [Path(call.args[0]).name for call in replace.call_args_list]
        self.assertEqual(targets, [self.applied_path()] * 3)
        self.assertEqual(sources, ["applied.json.tmp"] * 3)
        self.assertEqual(os.listdir(self.applied_path().parent), ["applied.json"])

    def test_journal_is_up_to_date_when_the_run_stops(self):
        self.notion.post_faults = {2: "lost_request"}
        ops = [comment_op(PAGE_1, 1), comment_op(PAGE_2, 2), comment_op(PAGE_3, 3)]
        self.apply(*ops)
        self.assertEqual(self.applied_keys(), [ops[0]["key"]])

    def test_resume_skips_applied_keys_and_keeps_other_entries(self):
        done, todo = status_op(PAGE_1, 1), status_op(PAGE_2, 2)
        self.applied_path().parent.mkdir(parents=True)
        stamp = "2026-10-20T10:00:00+00:00"
        other = "assign:x:0123456789ab"
        earlier = [
            {"key": done["key"], "kind": "status", "ref": 1, "verdict": "ko", "at": stamp},
            {"key": other, "kind": "assign", "ref": 5, "verdict": None, "at": stamp},
        ]
        self.applied_path().write_text(json.dumps({"applied": earlier}), encoding="utf-8")
        result = self.apply(done, todo)
        self.assertEqual(result, apply_report(1, 1))
        self.assertEqual([page for _, page, _ in self.notion.writes], [PAGE_2])
        self.assertEqual(self.applied_keys(), [done["key"], other, todo["key"]])

    def test_the_same_key_twice_in_a_plan_is_written_once(self):
        item = status_op(PAGE_1, 1)
        result = self.apply(item, dict(item))
        self.assertEqual((result["done"], result["skipped"]), (1, 1))
        self.assertEqual(len(self.notion.writes), 1)

    def test_unreadable_journal_stops_before_any_request(self):
        self.applied_path().parent.mkdir(parents=True)
        for content in ("{", "[]", '{"applied": "x"}', '{"applied": [1]}'):
            with self.subTest(content=content):
                self.applied_path().write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    self.apply(status_op(PAGE_1, 1))
                self.assertIn("applied.json", str(caught.exception))
        self.assertEqual(self.notion.calls, [])


class ResumeTest(ApplyTestCase):
    def comment_ops(self):
        return [
            comment_op(PAGE_1, 1, "Constat A"),
            comment_op(PAGE_2, 2, "Constat B"),
            comment_op(PAGE_3, 3, "Constat C"),
        ]

    def assert_each_comment_once(self, ops):
        self.assertEqual(self.notion.stored(PAGE_1), ["Constat A"])
        self.assertEqual(self.notion.stored(PAGE_2), ["Constat B"])
        self.assertEqual(self.notion.stored(PAGE_3), ["Constat C"])
        keys = self.applied_keys()
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sorted(keys), sorted(item["key"] for item in ops))

    def test_apply_prints_only_written_blocked_and_errors(self):
        done = [comment_op(PAGE_1, n, f"Constat {n}") for n in range(247)]
        self.apply(*done)
        self.log.clear()
        fresh = [comment_op(PAGE_2, n, f"Nouveau {n}") for n in range(3)]
        blocked = status_op(OUTSIDE_PAGE, 9)
        result = self.apply(*done, *fresh, blocked)
        self.assertEqual(
            {key: result[key] for key in ("done", "skipped", "errors")},
            {"done": 3, "skipped": 247, "errors": []},
        )
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(len(self.log), 4)
        self.assertEqual(sum(line.endswith(" : écrit") for line in self.log), 3)
        self.assertEqual(sum("bloqué" in line for line in self.log), 1)
        self.assertFalse(any("déjà" in line for line in self.log))

    def test_resume_without_duplicates(self):
        ops = self.comment_ops()
        self.notion.post_faults = {2: "lost_request"}
        first = self.apply(*ops)
        self.assertEqual(first["done"], 1)
        self.assertEqual([item["key"] for item in first["errors"]], [ops[1]["key"]])
        self.assertEqual(self.notion.posts, 2)
        second = self.apply(*ops)
        self.assertEqual(second, apply_report(2, 1))
        self.assert_each_comment_once(ops)

    def test_resume_after_a_lost_response_does_not_post_twice(self):
        ops = self.comment_ops()
        self.notion.post_faults = {2: "lost_response"}
        first = self.apply(*ops)
        self.assertEqual(first["done"], 1)
        self.assertEqual(len(first["errors"]), 1)
        second = self.apply(*ops)
        self.assertEqual(second, apply_report(1, 2))
        self.assertEqual(self.notion.posts, 3)
        self.assert_each_comment_once(ops)

    def test_resume_after_a_series_of_429(self):
        ops = self.comment_ops()
        self.notion.post_faults = {number: "rate_limited" for number in range(2, 7)}
        first = self.apply(*ops)
        self.assertEqual(first["done"], 1)
        self.assertEqual([item["key"] for item in first["errors"]], [ops[1]["key"]])
        self.assertIn("429", first["errors"][0]["message"])
        self.assertIn("relancer", first["errors"][0]["message"])
        self.assertEqual(self.notion.posts, 6)
        self.assertEqual(self.clock.sleeps.count(2.0), 4)
        self.assertEqual(self.notion.stored(PAGE_3), [])
        self.assertEqual(self.applied_keys(), [ops[0]["key"]])
        second = self.apply(*ops)
        self.assertEqual(second, apply_report(2, 1))
        self.assertEqual(self.notion.posts, 8)
        self.assert_each_comment_once(ops)

    def test_a_429_that_clears_within_the_retries_does_not_stop_the_plan(self):
        ops = self.comment_ops()
        self.notion.post_faults = {2: "rate_limited", 3: "rate_limited"}
        result = self.apply(*ops)
        self.assertEqual(result, apply_report(3, 0))
        self.assertEqual(self.clock.sleeps.count(2.0), 2)
        self.assert_each_comment_once(ops)

    def test_comment_already_on_the_page_is_not_sent_again(self):
        item = comment_op(PAGE_1, 1, "Constat A")
        self.notion.add_comment(PAGE_1, "Constat A")
        result = self.apply(item)
        self.assertEqual(result, apply_report(0, 1))
        self.assertEqual(self.notion.requests("POST"), [])
        self.assertEqual(self.applied_keys(), [item["key"]])

    def test_duplicate_check_ignores_edge_whitespace(self):
        self.notion.add_comment(PAGE_1, "Constat A\n")
        result = self.apply(comment_op(PAGE_1, 1, "Constat A"))
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(self.notion.requests("POST"), [])

    def test_comment_of_someone_else_or_with_other_text_does_not_count(self):
        self.notion.add_comment(PAGE_1, "Constat A", author="user-1")
        self.notion.add_comment(PAGE_1, "Constat A modifié")
        result = self.apply(comment_op(PAGE_1, 1, "Constat A"))
        self.assertEqual(result["done"], 1)
        self.assertEqual(self.notion.stored(PAGE_1), ["Constat A"])

    def test_duplicate_check_reads_the_page_before_each_comment(self):
        self.apply(comment_op(PAGE_1, 1), status_op(PAGE_1, 1))
        calls = [(call.method, urlsplit(call.url).path[len("/v1") :]) for call in self.notion.calls]
        self.assertEqual(
            calls,
            [("PATCH", f"/pages/{PAGE_1}"), ("GET", "/comments"), ("POST", "/comments")],
        )
        self.assertIn(f"block_id={PAGE_1}", self.notion.calls[1].url)

    def test_page_comment_is_not_duplicated_either(self):
        item = page_comment_op("Décompte")
        self.notion.add_comment(COPY_PAGE, "Décompte")
        result = self.apply(item)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(self.notion.requests("POST"), [])

    def test_without_bot_id_any_author_counts(self):
        self.notion.add_comment(PAGE_1, "Constat A", author="user-1")
        result = self.apply(comment_op(PAGE_1, 1, "Constat A"), bot_id=None)
        self.assertEqual(result["skipped"], 1)


class CommentReplacementTest(ApplyTestCase):
    def seed_pilot_state(self):
        """Two skill comments (different verdicts and dates) and two human ones."""
        self.notion.add_comment(
            PAGE_1, verdict_text("À traiter"), comment_id="old", created="2026-10-01T10:00:00.000Z"
        )
        self.notion.add_comment(PAGE_1, "Note humaine A", author="user-1", comment_id="human-a")
        self.notion.add_comment(
            PAGE_1, verdict_text("Ok / Recetté"), comment_id="new", created="2026-10-10T10:00:00Z"
        )
        self.notion.add_comment(PAGE_1, "Note humaine B", author="user-2", comment_id="human-b")

    def test_rewrite_keeps_one_skill_comment_and_all_human_ones(self):
        self.seed_pilot_state()
        text = verdict_text("À traiter", "Constat revu")
        result = self.apply(comment_op(PAGE_1, 1, text))
        self.assertEqual(result["done"], 1)
        self.assertEqual(self.notion.requests("POST"), [])
        [update] = self.notion.requests("PATCH")
        self.assertTrue(update.url.endswith("/v1/comments/new"))
        [delete] = self.notion.requests("DELETE")
        self.assertTrue(delete.url.endswith("/v1/comments/old"))
        self.assertEqual(
            sorted(self.notion.texts(PAGE_1)), sorted([text, "Note humaine A", "Note humaine B"])
        )
        [entry] = self.applied()
        self.assertEqual((entry["comment_id"], entry["mode"]), ("new", "update"))

    def test_first_comment_is_created(self):
        self.notion.add_comment(PAGE_1, "Note humaine", author="user-1")
        text = verdict_text("Ok / Recetté")
        result = self.apply(comment_op(PAGE_1, 1, text))
        self.assertEqual(result["done"], 1)
        self.assertEqual(self.notion.stored(PAGE_1), [text])
        [entry] = self.applied()
        self.assertEqual((entry["comment_id"], entry["mode"]), ("created-1", "create"))

    def test_a_skill_comment_without_verdict_is_not_replaced(self):
        self.notion.add_comment(PAGE_1, "Synthèse sans verdict")
        self.apply(comment_op(PAGE_1, 1, verdict_text("Ok / Recetté")))
        self.assertEqual(self.notion.requests("DELETE"), [])
        self.assertEqual(len(self.notion.texts(PAGE_1)), 2)

    def test_comment_replacement_fallbacks(self):
        self.seed_pilot_state()
        self.notion.comment_errors = {"update": http_failure(403, "restricted_resource", "No")}
        text = verdict_text("À traiter", "Constat revu")
        result = self.apply(comment_op(PAGE_1, 1, text))
        self.assertEqual(result["replace_failed"], [])
        self.assertEqual(len(self.notion.requests("DELETE")), 2)
        self.assertEqual(
            sorted(self.notion.texts(PAGE_1)), sorted([text, "Note humaine A", "Note humaine B"])
        )
        [entry] = self.applied()
        self.assertEqual((entry["comment_id"], entry["mode"]), ("created-1", "recreate"))

    def test_comment_replacement_fallbacks_when_delete_is_refused_too(self):
        self.seed_pilot_state()
        refused = http_failure(403, "restricted_resource", "No")
        self.notion.comment_errors = {"update": refused, "delete": refused}
        item = comment_op(PAGE_1, 1, verdict_text("À traiter", "Constat revu"))
        result = self.apply(item)
        self.assertEqual((result["done"], result["errors"]), (1, []))
        self.assertEqual(len(self.notion.requests("POST")), 1)
        self.assertEqual(len(self.notion.texts(PAGE_1)), 5)
        self.assertEqual({entry["key"] for entry in result["replace_failed"]}, {item["key"]})
        self.assertEqual({entry["ref"] for entry in result["replace_failed"]}, {1})
        self.assertTrue(all(entry["reason"] for entry in result["replace_failed"]))
        [entry] = self.applied()
        self.assertEqual(entry["mode"], "create")

    def test_delete_of_a_surplus_comment_refused_is_reported_after_the_update(self):
        self.seed_pilot_state()
        self.notion.comment_errors = {"delete": http_failure(403, "restricted_resource", "No")}
        result = self.apply(comment_op(PAGE_1, 1, verdict_text("Ok / Recetté", "Revu")))
        self.assertEqual(len(result["replace_failed"]), 1)
        self.assertEqual(self.notion.requests("POST"), [])
        [entry] = self.applied()
        self.assertEqual(entry["mode"], "update")


class CommentReplacementEdgeTest(ApplyTestCase):
    def skill(self, label, comment_id, created="2026-10-01T10:00:00Z"):
        self.notion.add_comment(
            PAGE_1, verdict_text(label), comment_id=comment_id, created=created
        )

    def test_identical_to_older_skill_comment_rewrites_newest(self):
        self.skill("Ok / Recetté", "a", "2026-10-01T10:00:00Z")
        self.skill("À traiter", "b", "2026-10-05T10:00:00Z")
        text = verdict_text("Ok / Recetté")
        result = self.apply(comment_op(PAGE_1, 1, text))
        self.assertEqual(result["done"], 1)
        self.assertEqual(self.notion.texts(PAGE_1), [text])
        [update] = self.notion.requests("PATCH")
        self.assertTrue(update.url.endswith("/comments/b"))
        [entry] = self.applied()
        self.assertEqual((entry["comment_id"], entry["mode"]), ("b", "update"))

    def test_identical_to_newest_still_removes_surplus(self):
        self.skill("À traiter", "a", "2026-10-01T10:00:00Z")
        self.skill("Ok / Recetté", "b", "2026-10-05T10:00:00Z")
        self.notion.add_comment(PAGE_1, "Note humaine", author="user-1")
        text = verdict_text("Ok / Recetté")
        result = self.apply(comment_op(PAGE_1, 1, text))
        self.assertEqual((result["done"], result["skipped"]), (0, 1))
        self.assertEqual(self.notion.requests("PATCH"), [])
        self.assertEqual(self.notion.requests("POST"), [])
        self.assertEqual(sorted(self.notion.texts(PAGE_1)), sorted([text, "Note humaine"]))
        [entry] = self.applied()
        self.assertNotIn("mode", entry)

    def test_identical_to_newest_with_refused_surplus_deletion_is_reported(self):
        self.skill("À traiter", "a", "2026-10-01T10:00:00Z")
        self.skill("Ok / Recetté", "b", "2026-10-05T10:00:00Z")
        self.notion.comment_errors = {"delete": http_failure(403, "restricted_resource", "No")}
        result = self.apply(comment_op(PAGE_1, 1, verdict_text("Ok / Recetté")))
        self.assertEqual((result["skipped"], len(result["replace_failed"])), (1, 1))

    def test_update_not_found_deletes_the_others_then_creates(self):
        self.skill("À traiter", "a", "2026-10-01T10:00:00Z")
        self.skill("Ok / Recetté", "b", "2026-10-05T10:00:00Z")
        self.notion.comment_errors = {"update": http_failure(404, "object_not_found", "Gone")}
        text = verdict_text("Mise en place partielle")
        result = self.apply(comment_op(PAGE_1, 1, text))
        self.assertEqual((result["done"], result["errors"], result["replace_failed"]), (1, [], []))
        self.assertEqual(self.notion.texts(PAGE_1), [text])
        [entry] = self.applied()
        self.assertEqual(entry["mode"], "recreate")

    def test_surplus_delete_not_found_counts_as_deleted(self):
        self.skill("À traiter", "a", "2026-10-01T10:00:00Z")
        self.skill("Ok / Recetté", "b", "2026-10-05T10:00:00Z")
        self.notion.comment_errors = {"delete": http_failure(404, "object_not_found", "Gone")}
        result = self.apply(comment_op(PAGE_1, 1, verdict_text("Mise en place partielle")))
        self.assertEqual((result["done"], result["errors"], result["replace_failed"]), (1, [], []))
        self.assertEqual(self.applied()[0]["mode"], "update")

    def test_newest_on_equal_or_missing_created_time_is_the_last_listed(self):
        for created in ("2026-10-01T10:00:00Z", None):
            with self.subTest(created=created):
                self.notion = FakeNotion()
                self.run_dir = self.run_dir / str(created)
                self.skill("À traiter", "a", created or "")
                self.skill("Ok / Recetté", "b", created or "")
                self.apply(comment_op(PAGE_1, 1, verdict_text("Mise en place partielle")))
                [update] = self.notion.requests("PATCH")
                self.assertTrue(update.url.endswith("/comments/b"))

    def test_without_bot_id_a_comment_is_created(self):
        self.skill("À traiter", "a")
        self.apply(comment_op(PAGE_1, 1, verdict_text("Ok / Recetté")), bot_id=None)
        self.assertEqual(self.notion.requests("PATCH"), [])
        self.assertEqual(len(self.notion.requests("POST")), 1)

    def test_page_comment_entry_has_comment_id_and_create_mode(self):
        self.apply(page_comment_op("Décompte"))
        [entry] = self.applied()
        self.assertEqual((entry["comment_id"], entry["mode"]), ("created-1", "create"))


class ErrorHandlingTest(ApplyTestCase):
    def test_network_error_stops_the_plan(self):
        ops = [status_op(PAGE_1, 1), status_op(PAGE_2, 2), status_op(PAGE_3, 3)]
        self.notion.page_unreachable = {PAGE_2}
        result = self.apply(*ops)
        self.assertEqual(result["done"], 1)
        [error] = result["errors"]
        self.assertEqual(error["key"], ops[1]["key"])
        self.assertIn("Connexion à Notion impossible", error["message"])
        self.assertIn("relancer", error["message"])
        self.assertEqual(self.applied_keys(), [ops[0]["key"]])
        self.assertEqual(len(self.notion.requests("PATCH")), 1 + 5)
        self.assertEqual(self.clock.sleeps.count(8), 1)

    def test_network_failure_of_the_duplicate_check_stops_too(self):
        self.notion.comments_unreachable = True
        result = self.apply(comment_op(PAGE_1, 1), comment_op(PAGE_2, 2))
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.notion.requests("POST"), [])
        self.assertEqual(len(self.notion.requests("GET")), 5)

    def test_api_error_on_one_page_leaves_the_others_going(self):
        ops = [
            status_op(PAGE_1, 1),
            comment_op(PAGE_1, 1),
            status_op(PAGE_2, 2),
            comment_op(PAGE_2, 2),
        ]
        self.notion.page_errors = {PAGE_1: http_failure(400, "validation_error", "Bad option")}
        result = self.apply(*ops)
        self.assertEqual(result["done"], 2)
        self.assertEqual([item["key"] for item in result["errors"]], [ops[0]["key"], ops[1]["key"]])
        self.assertIn("validation_error", result["errors"][0]["message"])
        self.assertIn("non tentée", result["errors"][1]["message"])
        self.assertEqual(self.notion.stored(PAGE_1), [])
        self.assertEqual(self.notion.stored(PAGE_2), ["Constat : à corriger"])
        self.assertEqual(self.applied_keys(), [ops[2]["key"], ops[3]["key"]])

    def test_unauthorized_stops_the_plan(self):
        self.notion.page_errors = {PAGE_1: http_failure(401, "unauthorized", "Bad token")}
        result = self.apply(status_op(PAGE_1, 1), status_op(PAGE_2, 2))
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("relancer", result["errors"][0]["message"])
        self.assertEqual(len(self.notion.requests("PATCH")), 1)

    def test_server_error_stops_the_plan_after_the_client_retries(self):
        self.notion.page_errors = {PAGE_1: http_failure(500, "internal_server_error", "Oops")}
        result = self.apply(status_op(PAGE_1, 1), status_op(PAGE_2, 2))
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(self.notion.requests("PATCH")), 5)
        self.assertEqual(self.notion.writes, [])

    def test_forbidden_page_does_not_stop_the_others(self):
        self.notion.page_errors = {PAGE_1: http_failure(403, "restricted_resource", "No access")}
        result = self.apply(status_op(PAGE_1, 1), status_op(PAGE_2, 2))
        self.assertEqual(result["done"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual([page for _, page, _ in self.notion.writes], [PAGE_2])

    def test_progress_is_logged_in_french(self):
        self.apply(status_op(PAGE_1, 12), comment_op(PAGE_1, 12))
        self.assertTrue(any("CQ-12" in line for line in self.log))


class PlanValidationTest(ApplyTestCase):
    def test_malformed_plans_are_refused_before_any_request(self):
        good = status_op(PAGE_1, 1)
        bad_ops = {
            "ops missing": None,
            "not a dict": "x",
            "no key": {**good, "key": ""},
            "unknown kind": {**good, "kind": "delete"},
            "no page": {**good, "page_id": ""},
            "status without name": {**good, "after": None},
            "assign without list": {**assign_op(PAGE_1, 1, ["Inès"]), "after": "Inès"},
            "assign with a number": {**assign_op(PAGE_1, 1, ["Inès"]), "after": [3]},
            "empty comment": {**comment_op(PAGE_1, 1), "text": "  "},
            "comment without text": {**comment_op(PAGE_1, 1), "text": None},
            "text not a string": {**good, "text": 3},
        }
        for name, bad in bad_ops.items():
            with self.subTest(name):
                plan = make_plan(good)
                if bad is None:
                    del plan["ops"]
                else:
                    plan["ops"].append(bad)
                with self.assertRaises(ValueError) as caught:
                    self.apply(plan=plan)
                self.assertNotIsInstance(caught.exception, ReferenceWriteRefused)
        self.assertEqual(self.notion.calls, [])


class CliTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name)
        self.notion = FakeNotion()
        self.clock = FakeClock()
        self.write_context()
        self.write_rows([PAGE_1, PAGE_2, PAGE_3])

    def client(self):
        return NotionClient(
            "tok", transport=self.notion, sleep=self.clock.sleep, clock=self.clock
        )

    def write_json(self, name, value):
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def write_context(self, without=(), **notion):
        values = {
            "copy_page_id": COPY_PAGE,
            "copy_data_source_id": COPY_DS,
            "reference_data_source_id": REF_DS,
            "reference_page_id": REF_PAGE,
            "bot_id": BOT,
            "assignee_options": OPTIONS,
        }
        values.update(notion)
        for key in without:
            del values[key]
        self.write_json("context.json", {"schema": 1, "run_id": "r1", "notion": values})

    def write_rows(self, page_ids):
        rows = [{"page_id": page, "ref": index + 1} for index, page in enumerate(page_ids)]
        self.write_json(
            "rows.json", {"copy_data_source_id": COPY_DS, "rows": rows, "missing_in_copy": []}
        )

    def write_plan(self, *ops, name="notion/plan.json", **fields):
        self.write_json(name, {**make_plan(*ops), **fields})

    def read_json(self, name):
        return json.loads((self.run_dir / name).read_text(encoding="utf-8"))

    def run_main(self, argv, env=None, bare=False):
        """Run cqa_notion.main; bare=True lets it build its client from the environment."""
        out, err = io.StringIO(), io.StringIO()
        environment = {"NOTION_TOKEN": "tok"} if env is None else env
        client = None if bare else self.client()
        with mock.patch.dict(os.environ, environment, clear=True):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_notion.main([str(item) for item in argv], client=client)
        return code, out.getvalue(), err.getvalue()


class ApplyCommandTest(CliTestCase):
    def test_applies_the_plan(self):
        self.write_plan(status_op(PAGE_1, 1), comment_op(PAGE_1, 1), page_comment_op())
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out), apply_report(3, 0)
        )
        self.assertEqual(
            [label for label, _, _ in self.notion.writes], ["status", "comment", "comment"]
        )
        self.assertEqual(len(self.read_json("notion/applied.json")["applied"]), 3)
        self.assertIn("CQ-1", err)

    def test_status_entries_carry_the_value_written(self):
        self.write_plan(status_op(PAGE_1, 1, after="À traiter", before="Mis en place"))
        self.run_main(["apply", self.run_dir])
        [entry] = self.read_json("notion/applied.json")["applied"]
        self.assertEqual(entry["value"], "À traiter")

    def test_comment_replacement_refused_still_exits_zero(self):
        self.notion.add_comment(PAGE_1, verdict_text("À traiter"))
        refused = http_failure(403, "restricted_resource", "No")
        self.notion.comment_errors = {"update": refused, "delete": refused}
        self.write_plan(comment_op(PAGE_1, 1, verdict_text("Ok / Recetté", "Revu")))
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual((report["done"], len(report["replace_failed"])), (1, 1))

    def test_rerun_writes_nothing_more(self):
        self.write_plan(status_op(PAGE_1, 1), comment_op(PAGE_1, 1))
        self.run_main(["apply", self.run_dir])
        writes = list(self.notion.writes)
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skipped"], 2)
        self.assertEqual(self.notion.writes, writes)

    def test_plan_option_reads_another_plan(self):
        self.write_plan(status_op(PAGE_1, 1))
        self.write_plan(assign_op(PAGE_2, 2, ["Inès"]), name="notion/assign-plan.json")
        code, out, _ = self.run_main(["apply", self.run_dir, "--plan", "notion/assign-plan.json"])
        self.assertEqual(code, 0)
        written = [(label, page) for label, page, _ in self.notion.writes]
        self.assertEqual(written, [("assign", PAGE_2)])

    def test_both_plans_share_the_journal(self):
        self.write_plan(status_op(PAGE_1, 1))
        self.write_plan(assign_op(PAGE_1, 1, ["Inès"]), name="notion/assign-plan.json")
        self.run_main(["apply", self.run_dir])
        self.run_main(["apply", self.run_dir, "--plan", "notion/assign-plan.json"])
        entries = self.read_json("notion/applied.json")["applied"]
        self.assertEqual([entry["kind"] for entry in entries], ["status", "assign"])

    def test_skip_human_overrides_leaves_those_pages_alone(self):
        self.write_plan(
            status_op(PAGE_1, 1, override=True),
            comment_op(PAGE_1, 1, "Constat 1"),
            status_op(PAGE_2, 2),
            comment_op(PAGE_2, 2, "Constat 2"),
            rows=[
                row_state(PAGE_1, 1, before="Mis en place"),
                row_state(PAGE_2, 2, before="À traiter"),
            ],
            summary={
                "by_status_after": {},
                "blocking_non_conform": [],
                "unknown": [],
                "human_overrides": [],
                "deferred": [],
                "missing_in_copy": [],
            },
        )
        code, out, _ = self.run_main(["apply", self.run_dir, "--skip-human-overrides"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["done"], 2)
        self.assertEqual({page for _, page, _ in self.notion.writes}, {PAGE_2})

    def test_without_the_option_human_overrides_are_written(self):
        self.write_plan(status_op(PAGE_1, 1, override=True))
        self.run_main(["apply", self.run_dir])
        self.assertEqual(len(self.notion.writes), 1)

    def test_skip_human_overrides_on_a_plan_without_rows_is_a_usage_error(self):
        self.write_plan(status_op(PAGE_1, 1))
        plan = self.read_json("notion/plan.json")
        del plan["rows"]
        self.write_json("notion/plan.json", plan)
        code, _, err = self.run_main(["apply", self.run_dir, "--skip-human-overrides"])
        self.assertEqual(code, 2)
        self.assertIn("rows", err)
        self.assertEqual(self.notion.calls, [])

    def test_malformed_plans_exit_2_with_or_without_the_option(self):
        good = status_op(PAGE_1, 1)
        plans = {
            "ops null": {"ops": None},
            "ops not a list": {"ops": "x"},
            "op not an object": {"ops": [good, 3]},
            "op without page": {"ops": [{**good, "page_id": None}]},
            "op of unknown kind": {"ops": [{**good, "kind": "delete"}]},
        }
        for name, fields in plans.items():
            for flag in ([], ["--skip-human-overrides"]):
                with self.subTest(name, flag=flag):
                    self.write_plan(**fields, rows=[], summary={})
                    code, out, err = self.run_main(["apply", self.run_dir, *flag])
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn("plan invalide", err)
                    self.assertNotIn("Traceback", err)
        self.assertEqual(self.notion.calls, [])

    def test_plan_that_is_not_an_object_exit_2(self):
        self.write_json("notion/plan.json", [])
        code, out, err = self.run_main(["apply", self.run_dir, "--skip-human-overrides"])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("plan.json", err)

    def test_skip_human_overrides_with_malformed_rows_or_summary_exit_2(self):
        for name, fields in (
            ("rows null", {"rows": None}),
            ("rows not objects", {"rows": [1]}),
            ("summary null", {"summary": None}),
        ):
            with self.subTest(name):
                self.write_plan(status_op(PAGE_1, 1, override=True), **fields)
                code, out, err = self.run_main(["apply", self.run_dir, "--skip-human-overrides"])
                self.assertEqual((code, out), (2, ""))
                self.assertIn("plan.json", err)
        self.assertEqual(self.notion.calls, [])

    def test_option_removed_from_the_copy_is_blocked_exit_7(self):
        self.notion.live_options = ["Côme D"]
        self.write_plan(assign_op(PAGE_1, 1, ["Inès"]))
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["blocked"][0]["labels"], ["unknown_option"])
        self.assertEqual(self.notion.write_requests(), [])

    def test_schema_read_failure_exit_3(self):
        self.notion.data_source_error = http_failure(404, "object_not_found", "Gone")
        self.write_plan(assign_op(PAGE_1, 1, ["Inès"]))
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual((code, out), (3, ""))
        self.assertIn("object_not_found", err)
        self.assertEqual(self.notion.write_requests(), [])

    def test_rate_limit_series_exit_3_then_resume(self):
        self.write_plan(
            comment_op(PAGE_1, 1, "Constat A"),
            comment_op(PAGE_2, 2, "Constat B"),
            comment_op(PAGE_3, 3, "Constat C"),
        )
        self.notion.post_faults = {number: "rate_limited" for number in range(2, 7)}
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["done"], 1)
        self.assertIn("429", err)
        self.assertEqual(len(self.read_json("notion/applied.json")["applied"]), 1)
        self.assertEqual(self.clock.sleeps.count(2.0), 4)
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), apply_report(2, 1))
        for page, text in ((PAGE_1, "Constat A"), (PAGE_2, "Constat B"), (PAGE_3, "Constat C")):
            self.assertEqual(self.notion.stored(page), [text])

    def test_reference_refused_exit_8(self):
        self.write_plan(status_op(PAGE_1, 1), copy_data_source_id=REF_DS)
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 8)
        self.assertEqual(out, "")
        self.assertIn("écriture refusée : data source du modèle", err)
        self.assertEqual(self.notion.calls, [])

    def test_page_comment_on_the_reference_page_refused_exit_8(self):
        self.write_context(copy_page_id=REF_PAGE)
        self.write_plan(assign_op(PAGE_1, 1, ["Inès"]), page_comment_op(page_id=REF_PAGE))
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual((code, out), (8, ""))
        self.assertIn("écriture refusée : page du modèle", err)
        self.assertEqual(self.notion.calls, [])

    def test_reference_is_resolved_from_the_environment_when_the_context_lacks_it(self):
        self.write_context(reference_data_source_id=None)
        self.notion.databases = {REF_DATABASE: [REF_DS]}
        self.write_plan(status_op(PAGE_1, 1), copy_data_source_id=REF_DS)
        url = f"https://www.notion.so/ws/Modele-{REF_DATABASE.replace('-', '')}"
        code, _, err = self.run_main(
            ["apply", self.run_dir], {"NOTION_TOKEN": "tok", "GM_CQA_REFERENCE": url}
        )
        self.assertEqual(code, 8)
        self.assertEqual(self.notion.writes, [])

    def test_unknown_reference_is_a_usage_error(self):
        self.write_context(reference_data_source_id=None)
        self.write_plan(status_op(PAGE_1, 1))
        code, _, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 2)
        self.assertIn("GM_CQA_REFERENCE", err)
        self.assertEqual(self.notion.calls, [])

    def test_secret_blocked_exit_7(self):
        leaking = comment_op(PAGE_1, 1, SECRET_HEADER)
        self.write_plan(leaking, comment_op(PAGE_2, 2, "Constat propre"))
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 7)
        report = json.loads(out)
        self.assertEqual(report["done"], 1)
        expected = [{"key": leaking["key"], "labels": ["authorization_header"]}]
        self.assertEqual(report["blocked"], expected)
        self.assertNotIn("dXNlcjpwYXNz", out + err)
        self.assertEqual(self.notion.stored(PAGE_2), ["Constat propre"])

    def test_known_values_come_from_the_environment(self):
        self.write_plan(comment_op(PAGE_1, 1, "Le jeton est abcdef123456 ici"))
        env = {"NOTION_TOKEN": "tok", "GM_AUDIT_AUTH_PASS": "abcdef123456"}
        code, out, _ = self.run_main(["apply", self.run_dir], env)
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["blocked"][0]["labels"], ["known_value"])
        self.assertEqual(self.notion.calls, [])

    def test_unknown_option_blocked_exit_7(self):
        self.write_plan(assign_op(PAGE_1, 1, ["Zoé"]))
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["blocked"][0]["labels"], ["unknown_option"])

    def test_network_error_exit_3_then_resume(self):
        self.write_plan(
            comment_op(PAGE_1, 1, "Constat A"),
            comment_op(PAGE_2, 2, "Constat B"),
            comment_op(PAGE_3, 3, "Constat C"),
        )
        self.notion.post_faults = {2: "lost_response"}
        code, out, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["done"], 1)
        self.assertIn("Connexion à Notion impossible", err)
        self.assertEqual(len(self.read_json("notion/applied.json")["applied"]), 1)
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), apply_report(1, 2))
        for page, text in ((PAGE_1, "Constat A"), (PAGE_2, "Constat B"), (PAGE_3, "Constat C")):
            self.assertEqual(self.notion.stored(page), [text])

    def test_errors_win_over_blocked_in_the_exit_code(self):
        self.write_plan(comment_op(PAGE_1, 1, SECRET_HEADER), status_op(PAGE_2, 2))
        self.notion.page_unreachable = {PAGE_2}
        code, _, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 3)

    def test_missing_files_are_usage_errors(self):
        for name in ("notion/plan.json", "rows.json", "context.json"):
            with self.subTest(name=name):
                self.write_context()
                self.write_rows([PAGE_1])
                self.write_plan(status_op(PAGE_1, 1))
                (self.run_dir / name).unlink()
                code, out, err = self.run_main(["apply", self.run_dir])
                self.assertEqual((code, out), (2, ""))
                self.assertIn(Path(name).name, err)
        self.assertEqual(self.notion.calls, [])

    def test_context_without_bot_is_a_usage_error(self):
        self.write_context(bot_id=None)
        self.write_plan(status_op(PAGE_1, 1))
        code, _, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 2)
        self.assertIn("bot_id", err)

    def test_pages_come_from_rows_json(self):
        self.write_rows([PAGE_2])
        self.write_plan(status_op(PAGE_1, 1), status_op(PAGE_2, 2))
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 7)
        self.assertEqual([page for _, page, _ in self.notion.writes], [PAGE_2])

    def test_rows_without_a_page_id_allow_nothing(self):
        self.write_json("rows.json", {"rows": [1, {"ref": 2}]})
        self.write_plan(status_op(PAGE_1, 1))
        code, out, _ = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["blocked"][0]["labels"], ["page_outside_copy"])
        self.assertEqual(self.notion.calls, [])

    def test_rows_json_without_rows_is_a_usage_error(self):
        self.write_json("rows.json", {})
        self.write_plan(status_op(PAGE_1, 1))
        code, _, err = self.run_main(["apply", self.run_dir])
        self.assertEqual(code, 2)
        self.assertIn("rows.json", err)
        self.assertEqual(self.notion.calls, [])

    def test_token_is_required(self):
        self.write_plan(status_op(PAGE_1, 1))
        code, out, err = self.run_main(["apply", self.run_dir], env={}, bare=True)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("NOTION_TOKEN", err)


class CommentPageCommandTest(CliTestCase):
    def write_text(self, text="Audit PRE-MEL\nDécompte par statut :\n- Ok / Recetté : 3\n"):
        path = self.run_dir / "page-comment.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_writes_a_page_comment_on_the_copy_page(self):
        path = self.write_text()
        code, out, _ = self.run_main(["comment-page", self.run_dir, path])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), apply_report(1, 0))
        self.assertEqual(
            self.notion.stored(COPY_PAGE),
            ["Audit PRE-MEL\nDécompte par statut :\n- Ok / Recetté : 3"],
        )
        [entry] = self.read_json("notion/applied.json")["applied"]
        text = "Audit PRE-MEL\nDécompte par statut :\n- Ok / Recetté : 3"
        self.assertEqual(entry["key"], operation_key("page_comment", COPY_PAGE, None, text))
        self.assertEqual((entry["kind"], entry["ref"]), ("page_comment", None))

    def test_rerun_does_not_duplicate(self):
        path = self.write_text()
        self.run_main(["comment-page", self.run_dir, path])
        code, out, _ = self.run_main(["comment-page", self.run_dir, path])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skipped"], 1)
        self.assertEqual(len(self.notion.stored(COPY_PAGE)), 1)

    def test_rerun_after_a_lost_response_does_not_duplicate(self):
        path = self.write_text()
        self.notion.post_faults = {1: "lost_response"}
        code, _, _ = self.run_main(["comment-page", self.run_dir, path])
        self.assertEqual(code, 3)
        code, out, _ = self.run_main(["comment-page", self.run_dir, path])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skipped"], 1)
        self.assertEqual(len(self.notion.stored(COPY_PAGE)), 1)

    def test_null_copy_page_writes_nothing(self):
        self.write_context(copy_page_id=None)
        code, out, err = self.run_main(["comment-page", self.run_dir, self.run_dir / "absent.txt"])
        self.assertEqual(code, 0)
        self.assertEqual(self.notion.calls, [])
        self.assertIn("copy_page_id", err)
        self.assertEqual(json.loads(out)["done"], 0)
        self.assertFalse((self.run_dir / "notion" / "applied.json").exists())

    def test_secret_blocked_exit_7(self):
        path = self.write_text(f"Synthèse\n{SECRET_HEADER}")
        code, out, err = self.run_main(["comment-page", self.run_dir, path])
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["blocked"][0]["labels"], ["authorization_header"])
        self.assertEqual(self.notion.calls, [])
        self.assertNotIn("dXNlcjpwYXNz", out + err)

    def test_reference_refused_exit_8(self):
        self.write_context(copy_data_source_id=REF_DS)
        code, _, err = self.run_main(["comment-page", self.run_dir, self.write_text()])
        self.assertEqual(code, 8)
        self.assertIn("écriture refusée", err)
        self.assertEqual(self.notion.calls, [])

    def test_reference_page_refused_exit_8(self):
        for page in (REF_PAGE, REF_PAGE.replace("-", "").upper()):
            with self.subTest(page=page):
                self.write_context(copy_page_id=page)
                code, out, err = self.run_main(["comment-page", self.run_dir, self.write_text()])
                self.assertEqual((code, out), (8, ""))
                self.assertIn("écriture refusée : page du modèle", err)
                self.assertEqual(self.notion.calls, [])

    def test_reference_page_is_resolved_from_the_environment_when_the_context_lacks_it(self):
        self.notion.pages = {REF_PAGE: REF_DATABASE}
        self.notion.databases = {REF_DATABASE: [REF_DS]}
        self.write_context(copy_page_id=REF_PAGE, without=("reference_page_id",))
        url = f"https://www.notion.so/ws/Modele-{REF_PAGE.replace('-', '')}"
        code, _, err = self.run_main(
            ["comment-page", self.run_dir, self.write_text()],
            {"NOTION_TOKEN": "tok", "GM_CQA_REFERENCE": url},
        )
        self.assertEqual(code, 8)
        self.assertIn("page du modèle", err)
        self.assertEqual(self.notion.write_requests(), [])

    def test_unknown_reference_page_is_a_usage_error(self):
        self.write_context(without=("reference_page_id",))
        code, _, err = self.run_main(["comment-page", self.run_dir, self.write_text()])
        self.assertEqual(code, 2)
        self.assertIn("GM_CQA_REFERENCE", err)
        self.assertEqual(self.notion.calls, [])

    def test_null_reference_page_lets_the_copy_page_be_commented(self):
        self.write_context(reference_page_id=None)
        code, out, _ = self.run_main(["comment-page", self.run_dir, self.write_text()])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["done"], 1)

    def test_invalid_reference_page_is_a_usage_error(self):
        self.write_context(reference_page_id=3)
        code, _, err = self.run_main(["comment-page", self.run_dir, self.write_text()])
        self.assertEqual(code, 2)
        self.assertIn("reference_page_id", err)
        self.assertEqual(self.notion.calls, [])

    def test_text_file_problems_are_usage_errors(self):
        cases = (
            ("missing", None, "absent.txt"),
            ("empty", "  \n", "vide"),
            ("too long", "a" * 1501, "1500"),
        )
        for name, text, needle in cases:
            with self.subTest(name):
                path = self.run_dir / "absent.txt" if text is None else self.write_text(text)
                code, out, err = self.run_main(["comment-page", self.run_dir, path])
                self.assertEqual((code, out), (2, ""))
                self.assertIn(needle, err)
        self.assertEqual(self.notion.calls, [])

    def test_text_at_the_limit_is_accepted(self):
        code, _, _ = self.run_main(["comment-page", self.run_dir, self.write_text("a" * 1500)])
        self.assertEqual(code, 0)

    def test_does_not_need_rows_json(self):
        (self.run_dir / "rows.json").unlink()
        code, _, _ = self.run_main(["comment-page", self.run_dir, self.write_text()])
        self.assertEqual(code, 0)

    def test_token_is_required(self):
        path = self.write_text()
        code, out, err = self.run_main(["comment-page", self.run_dir, path], env={}, bare=True)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("NOTION_TOKEN", err)


class SourceHygieneTest(unittest.TestCase):
    def test_no_em_dash(self):
        root = Path(__file__).resolve().parents[2]
        paths = [
            root / "scripts" / "cqa" / "notion_write.py",
            root / "scripts" / "cqa_notion.py",
            Path(__file__),
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
