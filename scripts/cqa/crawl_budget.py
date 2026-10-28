"""Crawl duration proposed at diagnostic time, from the sitemap and the response time."""
import html
import math
import re
import time
import zlib
from typing import Callable, Optional
from urllib.parse import urlsplit

MIN_MINUTES = 30
MAX_MINUTES = 120
DEFAULT_MINUTES = 30
MAX_SITEMAP_DEPTH = 2
MAX_SITEMAP_FILES = 50
MAX_SITEMAP_BYTES = 10 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
PAGE_PAUSE = 0.5

_LOC = re.compile(r"<(?:\w+:)?loc>\s*(.*?)\s*</(?:\w+:)?loc>", re.DOTALL | re.IGNORECASE)
_ROBOTS_SITEMAP = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def propose_crawl_minutes(
    sitemap_urls: Optional[int], mean_response_s: Optional[float], rate: float = 2.0
) -> int:
    """Minutes for the crawl: sitemap size, 3.5 requests per URL, slower of rate and response."""
    if not sitemap_urls:
        return DEFAULT_MINUTES
    per_request = max(1 / rate, mean_response_s or 0)
    minutes = round(sitemap_urls * 3.5 * per_request * 1.2 / 60, 6)
    rounded = math.ceil(minutes / 15) * 15
    return max(MIN_MINUTES, min(MAX_MINUTES, rounded))


def crawl_max_urls(minutes: int) -> int:
    return min(8000, max(500, round(minutes * 2000 / 30)))


def count_sitemap_urls(
    origin: str,
    fetch: Callable,
    prod_origin: Optional[str] = None,
    *,
    sleep: Callable = time.sleep,
    delay: float = 0.0,
    deadline: Optional[float] = None,
) -> Optional[int]:
    """Count the distinct URLs (path and query) of the sitemaps of origin; None if none answers.

    Seeds are origin/sitemap.xml and the Sitemap lines of robots.txt. An address on prod_origin
    is read on origin. Other hosts are ignored.
    """
    origin = origin.rstrip("/")
    state = {"files": 0, "answered": False, "requests": 0}

    def get(url: str) -> Optional[bytes]:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        if state["requests"]:
            sleep(delay)
        state["requests"] += 1
        status, body, _ = fetch(url)
        if status != 200 or not isinstance(body, (bytes, bytearray)):
            return None
        return bytes(body)

    def read(url: str) -> Optional[str]:
        if state["files"] >= MAX_SITEMAP_FILES:
            return None
        state["files"] += 1
        body = get(url)
        if body is None:
            return None
        state["answered"] = True
        return _unpack(body, url)

    seeds = [origin + "/sitemap.xml"]
    robots = get(origin + "/robots.txt") or b""
    for line in _ROBOTS_SITEMAP.findall(robots[:MAX_PAGE_BYTES].decode("utf-8", errors="replace")):
        mapped = on_target(line, origin, prod_origin)
        if mapped and mapped not in seeds:
            seeds.append(mapped)
    found, seen = set(), set()
    queue = [(url, 0) for url in seeds]
    while queue:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        text = read(url)
        if text is None:
            continue
        locations = [html.unescape(item) for item in _LOC.findall(text)]
        if re.search(r"<(?:\w+:)?sitemapindex", text, re.IGNORECASE):
            if depth < MAX_SITEMAP_DEPTH:
                for item in locations:
                    mapped = on_target(item, origin, prod_origin)
                    if mapped:
                        queue.append((mapped, depth + 1))
            continue
        for item in locations:
            mapped = on_target(item, origin, prod_origin)
            if mapped:
                parts = urlsplit(mapped)
                found.add((parts.path or "/", parts.query))
    return len(found) if state["answered"] else None


def mean_response_time(
    urls,
    fetch: Callable,
    *,
    sleep: Callable = time.sleep,
    deadline: Optional[float] = None,
    timed: Optional[list] = None,
) -> Optional[float]:
    """Mean seconds to the end of the body of the pages that answered below 400, else None."""
    times = timed if timed is not None else []
    for index, url in enumerate(urls):
        if deadline is not None and time.monotonic() >= deadline:
            break
        if index:
            sleep(PAGE_PAUSE)
        status, _, elapsed = fetch(url)
        if isinstance(status, int) and status < 400:
            times.append(elapsed)
    return sum(times) / len(times) if times else None


def on_target(url: str, origin: str, prod_origin: Optional[str]) -> Optional[str]:
    """Return url on origin when it is on origin or prod_origin, else None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    base = f"{parts.scheme}://{parts.netloc}".lower()
    if base != origin.lower() and base != (prod_origin or "").rstrip("/").lower():
        return None
    rest = parts.path or "/"
    if parts.query:
        rest += "?" + parts.query
    return origin + rest


def _unpack(body: bytes, url: str) -> Optional[str]:
    """Return the text of a sitemap (gunzipped when needed); None above the size cap."""
    if len(body) > MAX_SITEMAP_BYTES:
        return None
    if body[:2] == b"\x1f\x8b" or urlsplit(url).path.endswith(".gz"):
        try:
            unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = unpacker.decompress(body, MAX_SITEMAP_BYTES + 1)
        except (zlib.error, OSError, EOFError):
            return None
        if len(body) > MAX_SITEMAP_BYTES or unpacker.unconsumed_tail:
            return None
    return body.decode("utf-8", errors="replace")
