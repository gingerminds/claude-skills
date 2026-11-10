"""Test doubles for the Notion client, shared by the cqa tests."""
import json
from typing import NamedTuple, Optional

from cqa.notion_api import NotionClient


class Call(NamedTuple):
    """One request as the client sent it to the transport."""

    method: str
    url: str
    headers: dict
    data: Optional[bytes]

    @property
    def body(self):
        """The JSON body decoded, or None when the request had none."""
        return None if self.data is None else json.loads(self.data)


class FakeTransport:
    """Transport that replays scripted (status, headers, body) responses in order.

    A body is a JSON value or raw bytes. An exception instance in place of a response
    is raised instead, to simulate a network failure. Requests are kept in calls.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, data):
        self.calls.append(Call(method, url, dict(headers), data))
        index = len(self.calls) - 1
        if index >= len(self._responses):
            raise AssertionError(f"FakeTransport: no response scripted for call {index + 1}")
        response = self._responses[index]
        if isinstance(response, BaseException):
            raise response
        status, response_headers, body = response
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        return status, dict(response_headers), raw


class FakeClock:
    """Injectable clock and sleep. sleep() records its argument and moves time forward.

    A frozen clock never moves, which makes every throttle wait the full interval.
    """

    def __init__(self, start=0.0, frozen=False):
        self.now = start
        self.frozen = frozen
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if not self.frozen:
            self.now += seconds


def make_client(responses, frozen=False, **options):
    """Return (client, transport, clock): a client on a scripted transport and a fake clock."""
    transport = FakeTransport(responses)
    clock = FakeClock(frozen=frozen)
    client = NotionClient("tok", transport=transport, sleep=clock.sleep, clock=clock, **options)
    return client, transport, clock


def failure(status, code="object_not_found", message="Could not find page", headers=None):
    body = {"object": "error", "status": status, "code": code, "message": message}
    return status, headers or {}, body


def listing(results, next_cursor=None):
    body = {
        "object": "list",
        "results": results,
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
    }
    return 200, {}, body


def apply_report(done, skipped):
    """Return the report of an apply that blocked and failed nothing."""
    return {"done": done, "skipped": skipped, "blocked": [], "errors": [], "replace_failed": [],
            "status_changed": []}
