"""Reachability of the audit target: what the preprod answers to a HEAD request."""
import base64
import http.client
import ipaddress
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

from cqa.version import plugin_version

_DEFAULT_PORTS = {"http": 80, "https": 443}


def classify_access(status: Optional[int]) -> str:
    """Return how the target can be reached from this machine, given an HTTP status.

    200 to 399 is "open", 401 "basic_auth", 403 "ip_filtered" (the agency network is let
    through, other networks are not), anything else, a missing status included, "unreachable".
    """
    if isinstance(status, int) and 200 <= status <= 399:
        return "open"
    if status == 401:
        return "basic_auth"
    if status == 403:
        return "ip_filtered"
    return "unreachable"


def validate_url(url: str) -> str:
    """Return url when it is a plain http or https URL, else raise ValueError.

    Credentials in the URL, whitespace and control characters are refused. The messages never
    quote the URL, which could hold a password.
    """
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        raise ValueError("L'URL est invalide.") from None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError("L'URL doit commencer par http:// ou https:// et nommer un hôte.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("L'URL ne doit pas contenir d'identifiants.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("L'URL ne doit contenir ni espace ni caractère de contrôle.")
    return url


def probe_target(
    url: str,
    *,
    auth: Optional[tuple] = None,
    opener=None,
    timeout: float = 10.0,
) -> dict:
    """Return {"status", "access"} for the answer of url to a HEAD request.

    A GET follows when the server answers 405 to the HEAD. A redirect is followed when it
    stays on the origin of the URL (scheme, host and port) or only upgrades http to https on
    the same host (port 80 to 443, or the same explicit port). Any other redirect, a downgrade
    to http included, is not followed: its 3xx status is the answer and its Location header is
    added as "location", so credentials never leave the target. status is None when nothing
    answered.

    auth is a (user, password) pair sent as Basic authentication; it is refused for an http
    URL, except on a loopback address, because it would travel in clear. opener is an object
    with open(request, timeout=...) returning a response usable in a with block; it defaults
    to build_opener(). Raises ValueError for a URL validate_url refuses.
    """
    validate_url(url)
    if auth is not None:
        _require_encryption(url)
    if opener is None:
        opener = build_opener(authenticated=auth is not None)
    headers = _headers(auth)
    status, location = _answer(opener, url, "HEAD", headers, timeout)
    if status == 405:
        status, location = _answer(opener, url, "GET", headers, timeout)
    result = {"status": status, "access": classify_access(status)}
    if location:
        result["location"] = location
    return result


def fetch_page(
    url: str,
    *,
    auth: Optional[tuple] = None,
    opener=None,
    timeout: float = 10.0,
    max_bytes: int = 1_000_000,
    with_body: bool = False,
) -> dict:
    """Return {"status", "url", "body"} for a GET of url, under the rules of probe_target.

    status is None when nothing answered. url is the final address after the redirects probe_target
    follows. body is the text of the page (max_bytes at most) when with_body is true and the
    answer is 2xx, else "". Raises ValueError for a URL validate_url refuses, or for credentials
    on a clear-text URL.
    """
    validate_url(url)
    if auth is not None:
        _require_encryption(url)
    if opener is None:
        opener = build_opener(authenticated=auth is not None)
    request = urllib.request.Request(url, headers=_headers(auth), method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            body = ""
            if with_body and 200 <= status < 300:
                body = response.read(max_bytes).decode("utf-8", errors="replace")
            return {"status": status, "url": response.geturl() or url, "body": body}
    except urllib.error.HTTPError as error:
        error.close()
        return {"status": error.code, "url": url, "body": ""}
    except (urllib.error.URLError, http.client.HTTPException, OSError, UnicodeError):
        return {"status": None, "url": url, "body": ""}


def _headers(auth: Optional[tuple]) -> dict:
    headers = {"User-Agent": f"GingermindsAudit/{plugin_version()}"}
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def build_opener(*handlers, authenticated: bool = False):
    """Return the urllib opener of probe_target.

    Redirects follow the rule of probe_target. With authenticated, the proxies of the
    environment are ignored so that none of them receives the credentials. Extra handlers
    are consulted first (for tests).
    """
    chain = list(handlers)
    if authenticated:
        chain.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*chain, _SameOriginRedirect())


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _may_follow(req.full_url, newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _may_follow(current: str, new: str) -> bool:
    try:
        parts = urlsplit(new)
        if parts.username is not None or parts.password is not None:
            return False
        before, after = _origin(current), _origin(new)
    except ValueError:
        return False
    return after == before or _is_upgrade(before, after)


def _origin(url: str) -> tuple:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


def _is_upgrade(before: tuple, after: tuple) -> bool:
    if (before[0], after[0]) != ("http", "https") or before[1] != after[1]:
        return False
    expected = _DEFAULT_PORTS["https"] if before[2] == _DEFAULT_PORTS["http"] else before[2]
    return after[2] == expected


def _require_encryption(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" and not _is_loopback(parts.hostname):
        raise ValueError(
            "Les identifiants ne sont envoyés qu'en https : sur une URL en http, "
            "ils circuleraient en clair."
        )


def _is_loopback(host: Optional[str]) -> bool:
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _answer(opener, url: str, method: str, headers: dict, timeout: float) -> tuple:
    """Return (status, location) of one request; (None, None) when nothing answered."""
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            return status, _location(status, getattr(response, "headers", None))
    except urllib.error.HTTPError as error:
        location = _location(error.code, error.headers)
        error.close()
        return error.code, location
    except (urllib.error.URLError, http.client.HTTPException, OSError, UnicodeError):
        return None, None


def _location(status: int, headers) -> Optional[str]:
    if headers is None or not 300 <= status < 400:
        return None
    value = headers.get("Location")
    return value if isinstance(value, str) and value else None
