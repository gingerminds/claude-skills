import http.client
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from cqa.notion_api import NotionClient, NotionError, extract_id, urllib_transport
from tests.cqa.fakes import FakeClock, FakeTransport, failure, listing, make_client

API = "https://api.notion.com/v1"
ID = "0123456789abcdef0123456789abcdef"
DASHED_ID = "01234567-89ab-cdef-0123-456789abcdef"
VIEW_ID = "fedcba9876543210fedcba9876543210"
OK = (200, {}, {"object": "user"})


def items(*ids):
    return [{"id": item_id} for item_id in ids]


def markdown_response(truncated=False):
    body = {
        "object": "page_markdown",
        "id": "p1",
        "markdown": "# Titre\n",
        "truncated": truncated,
        "unknown_block_ids": [],
    }
    return 200, {}, body


class RequestTest(unittest.TestCase):
    def test_headers(self):
        client, transport, _ = make_client([OK, OK])
        client.request("GET", "/users/me")
        client.request("POST", "/comments", body={"text": "é"})
        get, post = transport.calls
        for call in (get, post):
            self.assertEqual(call.headers["Authorization"], "Bearer tok")
            self.assertEqual(call.headers["Notion-Version"], "2026-03-11")
        self.assertNotIn("Content-Type", get.headers)
        self.assertEqual(post.headers["Content-Type"], "application/json")

    def test_version_is_configurable(self):
        client, transport, _ = make_client([OK], version="2099-01-01")
        client.request("GET", "/users/me")
        self.assertEqual(transport.calls[0].headers["Notion-Version"], "2099-01-01")

    def test_token_whitespace_is_stripped(self):
        transport = FakeTransport([OK])
        client = NotionClient(" tok\n", transport=transport, sleep=FakeClock().sleep)
        client.request("GET", "/users/me")
        self.assertEqual(transport.calls[0].headers["Authorization"], "Bearer tok")

    def test_method_url_and_body(self):
        client, transport, _ = make_client([OK, OK])
        client.request("GET", "/users/me")
        client.request("PATCH", "pages/p1", body={"properties": {"Nom": "é"}})
        get, patch = transport.calls
        self.assertEqual((get.method, get.url, get.data), ("GET", API + "/users/me", None))
        self.assertEqual((patch.method, patch.url), ("PATCH", API + "/pages/p1"))
        self.assertIsInstance(patch.data, bytes)
        self.assertEqual(patch.body, {"properties": {"Nom": "é"}})

    def test_returns_the_decoded_body(self):
        client, _, _ = make_client([(200, {}, {"object": "user", "id": "u1"})])
        self.assertEqual(client.request("GET", "/users/me"), {"object": "user", "id": "u1"})

    def test_query_string(self):
        client, transport, _ = make_client([OK, OK])
        client.request("GET", "/comments", query={"block_id": "b 1&x", "page_size": 100})
        client.request("GET", "/users/me", query={})
        with_query, without_query = transport.calls
        self.assertEqual(with_query.url, API + "/comments?block_id=b+1%26x&page_size=100")
        self.assertEqual(without_query.url, API + "/users/me")

    def test_4xx_no_retry(self):
        client, transport, clock = make_client([failure(404)])
        with self.assertRaises(NotionError) as caught:
            client.request("GET", "/pages/p1")
        error = caught.exception
        self.assertEqual(
            (error.status, error.code, error.message),
            (404, "object_not_found", "Could not find page"),
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_every_other_4xx_fails_at_once(self):
        for status in (400, 401, 403, 409, 422):
            with self.subTest(status=status):
                client, transport, clock = make_client([failure(status)])
                with self.assertRaises(NotionError) as caught:
                    client.request("GET", "/pages/p1")
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(clock.sleeps, [])

    def test_error_body_that_is_not_json(self):
        client, _, _ = make_client([(404, {}, b"<html>Not found</html>")])
        with self.assertRaises(NotionError) as caught:
            client.request("GET", "/pages/p1")
        error = caught.exception
        self.assertEqual((error.status, error.code), (404, "http_404"))
        self.assertIn("404", error.message)

    def test_network_failure_becomes_a_notion_error(self):
        for cause in (OSError("Name or service not known"), http.client.IncompleteRead(b"")):
            with self.subTest(cause=type(cause).__name__):
                client, transport, clock = make_client([cause] * 5)
                with self.assertRaises(NotionError) as caught:
                    client.request("GET", "/users/me")
                error = caught.exception
                self.assertEqual((error.status, error.code), (0, "network_error"))
                self.assertIn(str(cause), error.message)
                self.assertIs(error.__cause__, cause)
                self.assertEqual(len(transport.calls), 5)
                self.assertEqual(clock.sleeps, [1, 2, 4, 8])

    def test_success_body_must_be_a_json_object(self):
        for raw in (b"", b"<html></html>", b"[1, 2]"):
            with self.subTest(raw=raw):
                client, _, _ = make_client([(200, {}, raw)])
                with self.assertRaises(NotionError) as caught:
                    client.request("GET", "/users/me")
                error = caught.exception
                self.assertEqual((error.status, error.code), (200, "invalid_response"))


class NotionErrorTest(unittest.TestCase):
    def test_attributes_and_message(self):
        error = NotionError(404, "object_not_found", "Could not find page")
        self.assertIsInstance(error, Exception)
        self.assertEqual(
            (error.status, error.code, error.message),
            (404, "object_not_found", "Could not find page"),
        )
        self.assertEqual(str(error), "Erreur Notion 404 (object_not_found) : Could not find page")

    def test_message_when_notion_was_not_reached(self):
        error = NotionError(0, "network_error", "Hors ligne")
        self.assertEqual(str(error), "Erreur Notion (network_error) : Hors ligne")


class RetryTest(unittest.TestCase):
    # Each scenario runs with a clock that moves with sleep() and with a frozen one:
    # a retry delay and the throttle interval must never add up.
    def test_retry_after_429(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                rate_limited = failure(429, "rate_limited", "Slow down", {"Retry-After": "3"})
                client, transport, clock = make_client([rate_limited, OK], frozen=frozen)
                self.assertEqual(client.request("GET", "/users/me"), {"object": "user"})
                self.assertEqual(clock.sleeps, [3])
                self.assertEqual(len(transport.calls), 2)

    def test_retry_after_is_found_whatever_its_case(self):
        client, _, clock = make_client([failure(429, headers={"retry-after": "2"}), OK])
        client.request("GET", "/users/me")
        self.assertEqual(clock.sleeps, [2])

    def test_429_without_a_usable_retry_after_backs_off(self):
        unusable = ({}, {"Retry-After": "soon"}, {"Retry-After": "-5"}, {"Retry-After": "nan"})
        for headers in unusable + ({"Retry-After": "inf"},):
            with self.subTest(headers=headers):
                client, _, clock = make_client([failure(429, headers=headers), OK])
                client.request("GET", "/users/me")
                self.assertEqual(clock.sleeps, [1])

    def test_retry_5xx_backoff(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                responses = [failure(503), failure(503), OK]
                client, transport, clock = make_client(responses, frozen=frozen)
                self.assertEqual(client.request("GET", "/users/me"), {"object": "user"})
                self.assertEqual(clock.sleeps, [1, 2])
                self.assertEqual(len(transport.calls), 3)

    def test_gives_up_after_5(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                responses = [failure(503, "service_unavailable", "Down")] * 5
                client, transport, clock = make_client(responses, frozen=frozen)
                with self.assertRaises(NotionError) as caught:
                    client.request("GET", "/users/me")
                error = caught.exception
                self.assertEqual((error.status, error.code), (503, "service_unavailable"))
                self.assertEqual(len(transport.calls), 5)
                self.assertEqual(clock.sleeps, [1, 2, 4, 8])

    def test_429_shares_the_five_attempts(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                rate_limited = failure(429, "rate_limited", headers={"Retry-After": "1"})
                client, transport, clock = make_client([rate_limited] * 5, frozen=frozen)
                with self.assertRaises(NotionError) as caught:
                    client.request("GET", "/users/me")
                error = caught.exception
                self.assertEqual((error.status, error.code), (429, "rate_limited"))
                self.assertEqual(len(transport.calls), 5)
                self.assertEqual(clock.sleeps, [1, 1, 1, 1])

    def test_5xx_body_that_is_not_json(self):
        client, transport, _ = make_client([(502, {}, b"<html>Bad Gateway</html>")] * 5)
        with self.assertRaises(NotionError) as caught:
            client.request("GET", "/users/me")
        error = caught.exception
        self.assertEqual((error.status, error.code), (502, "http_502"))
        self.assertEqual(len(transport.calls), 5)

    def test_a_retry_resends_the_same_request(self):
        client, transport, _ = make_client([failure(503), OK])
        client.request("PATCH", "/pages/p1", body={"a": 1})
        first, second = transport.calls
        self.assertEqual(first, second)

    def test_network_failure_is_retried(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                client, transport, clock = make_client([OSError("down"), OK], frozen=frozen)
                self.assertEqual(client.request("GET", "/users/me"), {"object": "user"})
                self.assertEqual(clock.sleeps, [1])
                self.assertEqual(len(transport.calls), 2)

    def test_failure_kinds_share_the_five_attempts(self):
        responses = [
            OSError("down"),
            failure(503),
            failure(429, headers={"Retry-After": "1"}),
            OSError("down"),
            failure(503),
        ]
        client, transport, clock = make_client(responses)
        with self.assertRaises(NotionError) as caught:
            client.request("GET", "/users/me")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(len(transport.calls), 5)
        self.assertEqual(clock.sleeps, [1, 2, 1, 8])

    def test_idempotent_flag_controls_the_replay_of_server_and_network_errors(self):
        for idempotent, expected_calls in ((True, 2), (False, 1)):
            for label, first in (("server error", failure(503)), ("network", OSError("down"))):
                with self.subTest(idempotent=idempotent, first=label):
                    client, transport, clock = make_client([first, OK])
                    if idempotent:
                        self.assertEqual(
                            client.request("POST", "/x", body={}, idempotent=True),
                            {"object": "user"},
                        )
                    else:
                        with self.assertRaises(NotionError):
                            client.request("POST", "/x", body={}, idempotent=False)
                    self.assertEqual(len(transport.calls), expected_calls)
                    self.assertEqual(clock.sleeps, [1] if idempotent else [])


class WriteRetryTest(unittest.TestCase):
    RICH_TEXT = [{"type": "text", "text": {"content": "Contrôle validé"}}]
    CREATED = (200, {}, {"object": "comment", "id": "c1"})

    def test_create_comment_is_not_replayed_on_a_server_error(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                client, transport, clock = make_client([failure(status, "server_error", "Boom")])
                with self.assertRaises(NotionError) as caught:
                    client.create_comment("p1", self.RICH_TEXT)
                error = caught.exception
                self.assertEqual((error.status, error.code), (status, "server_error"))
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(clock.sleeps, [])

    def test_create_comment_is_not_replayed_after_a_network_failure(self):
        for cause in (OSError("down"), http.client.IncompleteRead(b"")):
            with self.subTest(cause=type(cause).__name__):
                client, transport, clock = make_client([cause])
                with self.assertRaises(NotionError) as caught:
                    client.create_comment("p1", self.RICH_TEXT)
                error = caught.exception
                self.assertEqual((error.status, error.code), (0, "network_error"))
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(clock.sleeps, [])

    def test_create_comment_is_retried_when_rate_limited(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                rate_limited = failure(429, "rate_limited", headers={"Retry-After": "2"})
                client, transport, clock = make_client([rate_limited, self.CREATED], frozen=frozen)
                created = client.create_comment("p1", self.RICH_TEXT)
                self.assertEqual(created, {"object": "comment", "id": "c1"})
                self.assertEqual(clock.sleeps, [2])
                first, second = transport.calls
                self.assertEqual(first, second)

    def test_create_comment_stops_at_a_server_error_after_a_rate_limit(self):
        rate_limited = failure(429, "rate_limited", headers={"Retry-After": "1"})
        client, transport, clock = make_client([rate_limited, failure(503)])
        with self.assertRaises(NotionError) as caught:
            client.create_comment("p1", self.RICH_TEXT)
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(clock.sleeps, [1])

    def test_other_calls_still_retry_server_errors_and_network_failures(self):
        calls = [
            ("update_page", ("p1", {"Statut": {"status": {"name": "Mis en place"}}}), OK),
            ("query_data_source", ("ds1",), listing(items("r1"))),
            ("page_markdown", ("p1",), markdown_response()),
        ]
        first_failures = {"server error": failure(503), "network failure": OSError("down")}
        for name, args, success in calls:
            for label, first in first_failures.items():
                with self.subTest(call=name, first=label):
                    client, transport, clock = make_client([first, success])
                    getattr(client, name)(*args)
                    self.assertEqual(len(transport.calls), 2)
                    self.assertEqual(clock.sleeps, [1])


class ThrottleTest(unittest.TestCase):
    def test_throttle(self):
        client, _, clock = make_client([OK] * 5, frozen=True)
        for _ in range(5):
            client.request("GET", "/users/me")
        self.assertEqual(len(clock.sleeps), 4)
        self.assertAlmostEqual(sum(clock.sleeps), 1.6)

    def test_rate_sets_the_interval(self):
        client, _, clock = make_client([OK] * 3, frozen=True, rate=10)
        for _ in range(3):
            client.request("GET", "/users/me")
        self.assertEqual(len(clock.sleeps), 2)
        self.assertAlmostEqual(sum(clock.sleeps), 0.2)

    def test_requests_are_spaced_by_the_interval(self):
        clock = FakeClock()
        transport = FakeTransport([OK] * 5)
        sent_at = []

        def timed(*call):
            sent_at.append(clock.now)
            return transport(*call)

        client = NotionClient("tok", transport=timed, sleep=clock.sleep, clock=clock)
        for _ in range(5):
            client.request("GET", "/users/me")
        for earlier, later in zip(sent_at, sent_at[1:]):
            self.assertGreaterEqual(later - earlier, 0.4 - 1e-9)
        self.assertAlmostEqual(sent_at[-1], 1.6)

    def test_wait_is_what_remains_of_the_interval(self):
        clock = FakeClock()
        transport = FakeTransport([OK] * 3)

        def slow(*call):
            clock.now += 0.3
            return transport(*call)

        client = NotionClient("tok", transport=slow, sleep=clock.sleep, clock=clock)
        for _ in range(3):
            client.request("GET", "/users/me")
        self.assertEqual(len(clock.sleeps), 2)
        for wait in clock.sleeps:
            self.assertAlmostEqual(wait, 0.1)

    def test_no_wait_when_requests_are_already_slow(self):
        clock = FakeClock()
        transport = FakeTransport([OK] * 3)

        def slow(*call):
            clock.now += 1.0
            return transport(*call)

        client = NotionClient("tok", transport=slow, sleep=clock.sleep, clock=clock)
        for _ in range(3):
            client.request("GET", "/users/me")
        self.assertEqual(clock.sleeps, [])

    def test_every_page_goes_through_the_throttle(self):
        pages = [listing(items("a"), "c1"), listing(items("b"))]
        client, _, clock = make_client(pages, frozen=True)
        client.comments("b1")
        self.assertEqual(clock.sleeps, [0.4])

    def test_a_retry_never_beats_the_interval(self):
        for retry_after in ("0", "0.1"):
            for frozen in (False, True):
                with self.subTest(retry_after=retry_after, frozen=frozen):
                    responses = [failure(429, headers={"Retry-After": retry_after}), OK]
                    client, _, clock = make_client(responses, frozen=frozen)
                    client.request("GET", "/users/me")
                    self.assertEqual(clock.sleeps, [0.4])


class PaginateTest(unittest.TestCase):
    def test_paginate_post(self):
        pages = [listing(items("a", "b"), "c1"), listing(items("c"))]
        client, transport, _ = make_client(pages)
        results = client.paginate("POST", "/data_sources/ds1/query", body={"filter": {"x": 1}})
        self.assertEqual(results, items("a", "b", "c"))
        first, second = transport.calls
        self.assertEqual(first.body, {"filter": {"x": 1}, "page_size": 100})
        self.assertEqual(
            second.body, {"filter": {"x": 1}, "page_size": 100, "start_cursor": "c1"}
        )
        self.assertEqual(first.url, API + "/data_sources/ds1/query")
        self.assertEqual(second.url, first.url)

    def test_paginate_get(self):
        pages = [listing(items("a"), "c1"), listing(items("b"), "c2"), listing(items("c"))]
        client, transport, _ = make_client(pages)
        results = client.paginate("GET", "/comments", query={"block_id": "b1"})
        self.assertEqual(results, items("a", "b", "c"))
        queries = [parse_qs(urlsplit(call.url).query) for call in transport.calls]
        base = {"block_id": ["b1"], "page_size": ["100"]}
        self.assertEqual(queries[0], base)
        self.assertEqual(queries[1], {**base, "start_cursor": ["c1"]})
        self.assertEqual(queries[2], {**base, "start_cursor": ["c2"]})
        self.assertEqual([call.data for call in transport.calls], [None, None, None])

    def test_single_page(self):
        client, transport, _ = make_client([listing([])])
        self.assertEqual(client.paginate("GET", "/comments"), [])
        self.assertEqual(len(transport.calls), 1)

    def test_arguments_are_left_untouched(self):
        pages = [listing(items("a"), "c1"), listing([]), listing(items("b"), "c1"), listing([])]
        client, _, _ = make_client(pages)
        body, query = {"filter": 1}, {"block_id": "b1"}
        client.paginate("POST", "/x", body=body, query=query)
        client.paginate("GET", "/x", body=body, query=query)
        self.assertEqual(body, {"filter": 1})
        self.assertEqual(query, {"block_id": "b1"})

    def test_method_case_does_not_matter(self):
        client, transport, _ = make_client([listing(items("a"), "c1"), listing([])])
        client.paginate("post", "/x")
        self.assertEqual(transport.calls[1].body, {"page_size": 100, "start_cursor": "c1"})

    def test_more_pages_without_a_cursor_is_an_error(self):
        broken = 200, {}, {"object": "list", "results": [], "has_more": True, "next_cursor": None}
        client, transport, _ = make_client([broken])
        with self.assertRaises(NotionError) as caught:
            client.paginate("POST", "/x")
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertEqual(len(transport.calls), 1)

    def test_an_error_on_a_later_page_is_raised(self):
        client, _, _ = make_client([listing(items("a"), "c1"), failure(404)])
        with self.assertRaises(NotionError) as caught:
            client.paginate("GET", "/comments")
        self.assertEqual(caught.exception.status, 404)


class EndpointsTest(unittest.TestCase):
    def test_single_object_reads(self):
        cases = [
            ("me", (), "/users/me"),
            ("database", ("db1",), "/databases/db1"),
            ("data_source", ("ds1",), "/data_sources/ds1"),
        ]
        for name, args, path in cases:
            with self.subTest(name):
                client, transport, _ = make_client([(200, {}, {"object": name})])
                self.assertEqual(getattr(client, name)(*args), {"object": name})
                (call,) = transport.calls
                self.assertEqual((call.method, call.url, call.data), ("GET", API + path, None))

    def test_block_children_and_comments_paginate_in_the_query_string(self):
        cases = [
            ("block_children", "/blocks/b1/children", {"page_size": ["100"]}),
            ("comments", "/comments", {"block_id": ["b1"], "page_size": ["100"]}),
        ]
        for name, path, expected_query in cases:
            with self.subTest(name):
                client, transport, _ = make_client([listing(items("x", "y"))])
                self.assertEqual(getattr(client, name)("b1"), items("x", "y"))
                (call,) = transport.calls
                self.assertEqual(call.method, "GET")
                self.assertEqual(call.url.split("?")[0], API + path)
                self.assertEqual(parse_qs(urlsplit(call.url).query), expected_query)

    def test_query_data_source(self):
        client, transport, _ = make_client([listing(items("r1"), "c1"), listing(items("r2"))])
        self.assertEqual(client.query_data_source("ds1"), items("r1", "r2"))
        first, second = transport.calls
        self.assertEqual((first.method, first.url), ("POST", API + "/data_sources/ds1/query"))
        self.assertEqual(first.body, {"page_size": 100})
        self.assertEqual(second.body, {"page_size": 100, "start_cursor": "c1"})

    def test_page_markdown(self):
        client, transport, _ = make_client([markdown_response()])
        self.assertEqual(client.page_markdown("p1"), {"markdown": "# Titre\n", "truncated": False})
        (call,) = transport.calls
        expected = ("GET", API + "/pages/p1/markdown", None)
        self.assertEqual((call.method, call.url, call.data), expected)

    def test_page_markdown_reports_truncation(self):
        client, _, _ = make_client([markdown_response(truncated=True)])
        self.assertIs(client.page_markdown("p1")["truncated"], True)

    def test_update_page(self):
        properties = {"Statut": {"status": {"name": "Mis en place"}}}
        client, transport, _ = make_client([(200, {}, {"object": "page", "id": "p1"})])
        self.assertEqual(client.update_page("p1", properties), {"object": "page", "id": "p1"})
        (call,) = transport.calls
        self.assertEqual((call.method, call.url), ("PATCH", API + "/pages/p1"))
        self.assertEqual(call.body, {"properties": properties})

    def test_create_comment(self):
        rich_text = [{"type": "text", "text": {"content": "Contrôle validé"}}]
        client, transport, _ = make_client([(200, {}, {"object": "comment", "id": "c1"})])
        self.assertEqual(client.create_comment("p1", rich_text), {"object": "comment", "id": "c1"})
        (call,) = transport.calls
        self.assertEqual((call.method, call.url), ("POST", API + "/comments"))
        self.assertEqual(call.body, {"parent": {"page_id": "p1"}, "rich_text": rich_text})


class ExtractIdTest(unittest.TestCase):
    def test_extract_id(self):
        self.assertEqual(
            extract_id("https://www.notion.so/ws/Titre-0123456789abcdef0123456789abcdef"),
            "01234567-89ab-cdef-0123-456789abcdef",
        )

    def test_accepted_forms(self):
        forms = [
            ID,
            DASHED_ID,
            ID.upper(),
            f" {ID}\n",
            f"https://www.notion.so/{ID}",
            f"https://www.notion.so/ws/Titre-{ID}?pvs=4",
            f"https://www.notion.so/ws/Titre-{ID}/",
            f"https://www.notion.so/ws/Titre-{ID}#{VIEW_ID}",
            f"https://www.notion.so/ws/Titre-{DASHED_ID}",
            f"www.notion.so/Titre-{ID}",
            f"https://example.notion.site/Titre-{ID}",
            f"notion://www.notion.so/Titre-{ID}",
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(extract_id(form), DASHED_ID)

    def test_database_url_ignores_the_view_id(self):
        self.assertEqual(extract_id(f"https://www.notion.so/ws/{ID}?v={VIEW_ID}&pvs=4"), DASHED_ID)

    def test_rejects_values_without_an_id(self):
        values = [
            "",
            "   ",
            "https://www.notion.so/ws/Titre",
            ID[:-1],
            ID + "0",
            "g" * 32,
            f"https://www.notion.so/?p={ID}",
        ]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "identifiant Notion"):
                    extract_id(value)


class EchoHandler(BaseHTTPRequestHandler):
    def respond(self):
        length = int(self.headers.get("Content-Length", 0))
        echo = {
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "body": self.rfile.read(length).decode("utf-8"),
        }
        payload = json.dumps(echo).encode("utf-8")
        self.send_response(404 if self.path.startswith("/missing") else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Example", "yes")
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = respond

    def log_message(self, *args):
        pass


class UrllibTransportTest(unittest.TestCase):
    def setUp(self):
        server = HTTPServer(("localhost", 0), EchoHandler)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        no_proxy = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        no_proxy.start()
        self.addCleanup(no_proxy.stop)
        self.base = f"http://localhost:{server.server_port}"

    def test_round_trip(self):
        headers = {"Authorization": "Bearer tok", "Content-Type": "application/json"}
        url = self.base + "/pages/p1?a=1"
        status, response_headers, raw = urllib_transport("PATCH", url, headers, b'{"k":1}')
        echo = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["X-Example"], "yes")
        self.assertEqual(echo["method"], "PATCH")
        self.assertEqual(echo["path"], "/pages/p1?a=1")
        self.assertEqual(echo["authorization"], "Bearer tok")
        self.assertEqual(echo["content_type"], "application/json")
        self.assertEqual(echo["body"], '{"k":1}')

    def test_error_status_is_returned_not_raised(self):
        status, response_headers, raw = urllib_transport("GET", self.base + "/missing", {}, None)
        self.assertEqual(status, 404)
        self.assertEqual(response_headers["X-Example"], "yes")
        self.assertEqual(json.loads(raw)["method"], "GET")

    def test_refused_connection_raises_an_os_error(self):
        with socket.socket() as probe:
            probe.bind(("localhost", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(OSError):
            urllib_transport("GET", f"http://localhost:{port}/", {}, None)
