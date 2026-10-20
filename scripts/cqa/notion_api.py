"""Notion API client: the only code that talks to Notion. Throttled, retried, paginated."""
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Callable, Optional

API_BASE = "https://api.notion.com/v1"
DEFAULT_VERSION = "2026-03-11"
PAGE_SIZE = 100
BACKOFF_SECONDS = (1, 2, 4, 8)
REQUEST_TIMEOUT = 60

Transport = Callable[[str, str, dict, Optional[bytes]], tuple[int, dict, bytes]]

_NOTION_ID = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?![0-9a-f])",
    re.IGNORECASE,
)


class NotionError(Exception):
    """A failed Notion call. status is the HTTP status, or 0 when Notion was not reached."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(status, code, message)
        self.status = status
        self.code = code
        self.message = message

    def __str__(self) -> str:
        status = f" {self.status}" if self.status else ""
        return f"Erreur Notion{status} ({self.code}) : {self.message}"


def extract_id(url_or_id: str) -> str:
    """Return the dashed UUID of a Notion URL or identifier, with or without dashes.

    Only the last path segment is read, so the view id of a database URL is ignored.
    """
    path = urllib.parse.urlsplit(url_or_id.strip()).path
    segments = [segment for segment in path.split("/") if segment]
    found = _NOTION_ID.findall(segments[-1]) if segments else []
    if not found:
        raise ValueError("Aucun identifiant Notion (32 caractères hexadécimaux) trouvé.")
    return str(uuid.UUID(found[-1]))


def urllib_transport(
    method: str, url: str, headers: dict, data: Optional[bytes]
) -> tuple[int, dict, bytes]:
    """Send one request with urllib. An HTTP error status is returned like any other."""
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, dict(error.headers), error.read()


class NotionClient:
    """Notion REST client. Requests are spaced by 1 / rate seconds, 429 and 5xx are retried."""

    def __init__(
        self,
        token: str,
        *,
        version: str = DEFAULT_VERSION,
        rate: float = 2.5,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._token = token.strip()
        self._version = version
        self._interval = 1 / rate
        self._transport = urllib_transport if transport is None else transport
        self._sleep = sleep
        self._clock = clock
        self._last_sent: Optional[float] = None

    def request(
        self, method: str, path: str, *, body: Optional[dict] = None, query: Optional[dict] = None
    ) -> dict:
        """Send one call and return its JSON object. 429 and 5xx are retried, others raise."""
        url = f"{API_BASE}/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._token}", "Notion-Version": self._version}
        if data is not None:
            headers["Content-Type"] = "application/json"
        delays = iter(BACKOFF_SECONDS)
        delay = 0
        while True:
            self._wait_turn(delay)
            status, response_headers, raw = self._send(method.upper(), url, headers, data)
            if 200 <= status < 300:
                return _success_body(status, raw)
            delay = next(delays, None)
            if delay is None or not (status == 429 or status >= 500):
                raise _api_error(status, raw)
            if status == 429:
                delay = _retry_after(response_headers, delay)

    def paginate(
        self, method: str, path: str, *, body: Optional[dict] = None, query: Optional[dict] = None
    ) -> list[dict]:
        """Return the results of every page.

        start_cursor and page_size go in the body for a POST, in the query string otherwise.
        """
        method = method.upper()
        results = []
        cursor = None
        while True:
            params = {"page_size": PAGE_SIZE}
            if cursor:
                params["start_cursor"] = cursor
            if method == "POST":
                page = self.request(method, path, body={**(body or {}), **params}, query=query)
            else:
                page = self.request(method, path, body=body, query={**(query or {}), **params})
            results.extend(page["results"])
            if not page.get("has_more"):
                return results
            cursor = page.get("next_cursor")
            if not cursor:
                message = "Pagination incohérente : has_more sans next_cursor."
                raise NotionError(200, "invalid_response", message)

    def me(self) -> dict:
        return self.request("GET", "/users/me")

    def database(self, database_id: str) -> dict:
        return self.request("GET", f"/databases/{database_id}")

    def data_source(self, ds_id: str) -> dict:
        return self.request("GET", f"/data_sources/{ds_id}")

    def block_children(self, block_id: str) -> list[dict]:
        return self.paginate("GET", f"/blocks/{block_id}/children")

    def query_data_source(self, ds_id: str) -> list[dict]:
        return self.paginate("POST", f"/data_sources/{ds_id}/query")

    def page_markdown(self, page_id: str) -> dict:
        """Return the page as {"markdown", "truncated"}. truncated is true when Notion cut it."""
        data = self.request("GET", f"/pages/{page_id}/markdown")
        return {"markdown": data["markdown"], "truncated": data["truncated"]}

    def comments(self, block_id: str) -> list[dict]:
        return self.paginate("GET", "/comments", query={"block_id": block_id})

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"/pages/{page_id}", body={"properties": properties})

    def create_comment(self, page_id: str, rich_text: list[dict]) -> dict:
        body = {"parent": {"page_id": page_id}, "rich_text": rich_text}
        return self.request("POST", "/comments", body=body)

    def _wait_turn(self, delay: float) -> None:
        """Sleep until delay has passed and the interval since the previous send has too."""
        wait = delay
        if self._last_sent is not None:
            wait = max(wait, self._interval - (self._clock() - self._last_sent))
        if wait > 0:
            self._sleep(wait)
        self._last_sent = self._clock()

    def _send(
        self, method: str, url: str, headers: dict, data: Optional[bytes]
    ) -> tuple[int, dict, bytes]:
        try:
            return self._transport(method, url, headers, data)
        except (OSError, http.client.HTTPException) as error:
            reason = str(error) or type(error).__name__
            message = f"Connexion à Notion impossible : {reason}"
            raise NotionError(0, "network_error", message) from error


def _json_object(raw: bytes) -> Optional[dict]:
    """Decode a JSON object, or return None when raw is anything else."""
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _success_body(status: int, raw: bytes) -> dict:
    payload = _json_object(raw)
    if payload is None:
        raise NotionError(status, "invalid_response", "Réponse de Notion illisible (JSON attendu).")
    return payload


def _api_error(status: int, raw: bytes) -> NotionError:
    """Build the error of a failed response, from Notion's JSON body when it has one."""
    payload = _json_object(raw) or {}
    code = payload.get("code")
    message = payload.get("message")
    return NotionError(
        status,
        code if isinstance(code, str) and code else f"http_{status}",
        message if isinstance(message, str) and message else f"Réponse HTTP {status} sans détail.",
    )


def _retry_after(headers: dict, default: float) -> float:
    """Return the Retry-After seconds of a response, or default when absent or unusable."""
    value = next((value for name, value in headers.items() if name.lower() == "retry-after"), None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return seconds if 0 <= seconds < math.inf else default
