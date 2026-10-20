"""Reachability of the audit target: what the preprod answers to a HEAD request."""
import base64
import http.client
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

    A GET follows when the server answers 405 to the HEAD. Redirects stay on the origin of
    the URL (scheme, host and port): a redirect elsewhere is not followed and its status is
    the answer, so credentials never leave the target. auth is a (user, password) pair sent
    as Basic authentication. status is None when nothing answered. opener is an object with
    open(request, timeout=...) returning a response usable in a with block; it defaults to a
    urllib opener. Raises ValueError for a URL validate_url refuses.
    """
    validate_url(url)
    if opener is None:
        opener = urllib.request.build_opener(_SameOriginRedirect())
    headers = {"User-Agent": f"GingermindsAudit/{plugin_version()}"}
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    status = _status(opener, url, "HEAD", headers, timeout)
    if status == 405:
        status = _status(opener, url, "GET", headers, timeout)
    return {"status": status, "access": classify_access(status)}


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            if _origin(newurl) != _origin(req.full_url):
                return None
        except ValueError:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _origin(url: str) -> tuple:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


def _status(opener, url: str, method: str, headers: dict, timeout: float) -> Optional[int]:
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.getcode()
    except urllib.error.HTTPError as error:
        error.close()
        return error.code
    except (urllib.error.URLError, http.client.HTTPException, OSError, UnicodeError):
        return None
