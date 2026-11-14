"""Probes of the audit: the HTTP requests the toolbox sends to the target (probes.json)."""
import json
import re
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

from cqa import contract
from cqa.findings import (
    PHP_FILE_REASON,
    UTF8_REASON,
    is_utf8_encodable,
    requests_php_file,
    validate_document,
)
from cqa.target import validate_url

_PROD_PHASES = ("J0", "J+7")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_CONTENT_PROBES = (
    ("root", "/"),
    ("robots", "/robots.txt"),
    ("sitemap", "/sitemap.xml"),
    ("security-txt", "/.well-known/security.txt"),
)
_SENSITIVE_PROBES = (
    ("env", "/.env"),
    ("git-head", "/.git/HEAD"),
    ("composer-json", "/composer.json"),
    ("composer-lock", "/composer.lock"),
    ("package-json", "/package.json"),
    ("phpinfo", "/phpinfo.php"),
)
_TECHNO_PROBES = {
    "wordpress": (
        ("wp-login", "/wp-login.php"),
        ("xmlrpc", "/xmlrpc.php"),
        ("wp-users", "/wp-json/wp/v2/users"),
    ),
    "drupal": (
        ("drupal-login", "/user/login"),
        ("drupal-changelog", "/CHANGELOG.txt"),
        ("drupal-core-changelog", "/core/CHANGELOG.txt"),
    ),
    "laravel": (
        ("laravel-telescope", "/telescope"),
        ("laravel-horizon", "/horizon"),
        ("laravel-ignition", "/_ignition/health-check"),
    ),
}

_EXPOSURE_PATHS = frozenset(
    path.casefold()
    for group in (_SENSITIVE_PROBES, *_TECHNO_PROBES.values())
    for _, path in group
)
_BACKTICKS = re.compile(r"`([^`\n]+)`")
_TARGET_PATH = re.compile(r"/[A-Za-z0-9._~%/+=:@&-]*(?:\?[A-Za-z0-9._~%/+=:@&?-]*)?")
_SYSTEM_ROOTS = frozenset(
    {"etc", "var", "usr", "home", "opt", "srv", "tmp", "bin", "dev", "proc", "root", "mnt", "lib"}
)
_SENSITIVE_PATH = re.compile(
    r"(?:^|/)\.(?:env|git|svn|hg|htaccess|htpasswd|ssh|aws)(?:[/.\-_?]|$)"
    r"|(?:^|/)(?:composer\.(?:json|lock)|package(?:-lock)?\.json|phpinfo\.php|wp-config\.php"
    r"|auth\.json)(?:\?|$)"
    r"|\.(?:sql|bak|old|key|pem|log|zip|tar|gz)(?:\?|$)",
    re.IGNORECASE,
)


def is_prod_run(context: dict) -> bool:
    """Whether the audit targets the prod: a J0 or J+7 phase, and a prod that is live."""
    target = context.get("target")
    phase = context.get("phase_filter")
    return (
        isinstance(target, dict)
        and target.get("prod_live") is True
        and isinstance(phase, str)
        and any(tag in phase for tag in _PROD_PHASES)
    )


PREPROD_IS_PROD = (
    "context.json : « target.preprod_url » désigne le même site que « target.prod_url » (même hôte "
    "et même port) alors que l'audit ne cible pas la prod : la prod serait auditée comme une "
    "préprod (robots.txt ignoré, crawl et tests complets sur le site en ligne). Corriger "
    "« target.preprod_url » avec l'adresse de la préprod ; pour auditer la prod, lancer l'audit "
    "avec la phase « MEL J0 » ou « Post-MEL J+7 » et la prod en ligne."
)


def preprod_is_prod(context: dict) -> bool:
    """Whether the audit targets the preprod while target.preprod_url is the site of
    target.prod_url: the same host and port, whatever the scheme, the path and the case (the
    http and the https address of one site are one site). A prod run never is."""
    target = context.get("target")
    if not isinstance(target, dict) or is_prod_run(context):
        return False
    preprod, prod = _site(target.get("preprod_url")), _site(target.get("prod_url"))
    return preprod is not None and preprod == prod


def _site(url) -> Optional[tuple]:
    """Return (host, port) of a URL, the port None when it is the default one of its scheme."""
    host = url_host(url)
    if host is None:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if port == _DEFAULT_PORTS.get(parts.scheme.lower()):
        port = None
    return host, port


def target_url(context: dict) -> str:
    """Return the base URL of the audit: the prod when is_prod_run, else the preprod.

    Raises ValueError, with a message that never quotes the URL, when the URL is missing, not a
    plain http(s) URL, or carries a query or a fragment (a token could hide there), or when the
    audit targets the preprod and its URL is the prod (preprod_is_prod).
    """
    target = context.get("target")
    if not isinstance(target, dict):
        raise ValueError("context.json : « target » est absent.")
    if preprod_is_prod(context):
        raise ValueError(PREPROD_IS_PROD)
    key = "prod_url" if is_prod_run(context) else "preprod_url"
    url = target.get(key)
    if not isinstance(url, str) or not url:
        raise ValueError(f"context.json : « target.{key} » est absent ou vide.")
    return _checked_base(url, f"target.{key}")


def app_path(context: dict) -> str:
    """Return the path the application is served under: the path of target_url (context.json
    target), "" when it cannot be read (the PHP entry points are then those of the root)."""
    try:
        return urlsplit(target_url(context)).path
    except ValueError:
        return ""


def target_hosts(context: dict, *, pages: bool = False) -> list:
    """Return the hosts of the audit target, sorted: the evidence of a finding is on one of them.

    The host of target.preprod_url, the one of target.prod_url on a prod run (is_prod_run), and
    the domain and the home host of each entry of sites. With pages, also the host of each URL of
    key_pages, which the browser may visit (a back-office on its own host): a page evidence may
    be there, an http evidence never (it is a probe of the toolbox). An unreadable URL is left
    out.
    """
    target = context.get("target")
    target = target if isinstance(target, dict) else {}
    urls = [target.get("preprod_url")]
    if is_prod_run(context):
        urls.append(target.get("prod_url"))
    sites = context.get("sites")
    for site in sites if isinstance(sites, list) else []:
        if isinstance(site, dict):
            domain = site.get("domain")
            urls.extend([site.get("home"), f"https://{domain}" if isinstance(domain, str) else None])
    key_pages = context.get("key_pages") if pages else None
    for page in key_pages if isinstance(key_pages, list) else []:
        if isinstance(page, dict):
            urls.append(page.get("url"))
    return sorted({host for host in (url_host(url) for url in urls) if host})


def url_host(url) -> Optional[str]:
    """Return the host of a URL in lower case, without a trailing dot, None when it has none."""
    if not isinstance(url, str):
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def build_probes(
    *,
    base_url: str,
    technos: list[str],
    rows: list[dict],
    fiches_dir: Path,
    key_pages: list[dict],
) -> dict:
    """Return {"base_url", "probes", "discarded"}: probes is the probes.json list, the fixed set
    then the key pages and the fiche citations.

    Order: content probes (body kept), sensitive probes (never any body), probes of each project
    techno, key pages (headers only), then the paths and target URLs that the fiches of the rows
    asking for HTTP quote between backticks. Every path is relative to the origin of base_url
    and resolves to it (the one absolute form is the http:// version of the same host), and a
    probe that repeats an earlier one is left out. All probes are GET.

    A key page or a fiche path that runs a PHP file outside the routes of the application (see
    requests_php_file, under the path of base_url) is never probed: discarded lists it as
    {"source": "page"|"fiche", "ref" (fiche only), "path", "reason"}, once per source, ref and
    path.
    """
    base = _checked_base(base_url, "base_url")
    parts = urlsplit(base)
    origin = _origin(parts)
    collected = _Collected(origin)
    discarded = []

    def cited(source: dict, name: str, path: str, **rule) -> bool:
        if collected.repeats(path):
            return False
        if requests_php_file(path, parts.path):
            entry = {**source, "path": path, "reason": PHP_FILE_REASON}
            if entry not in discarded:
                discarded.append(entry)
            return False
        return collected.add(name, path, **rule)

    for name, path in _CONTENT_PROBES:
        collected.add(name, path, body="keep")
    collected.add("not-found", f"/cqa-404-{uuid.uuid4().hex[:8]}", body="keep")
    insecure = _http_version(urlsplit(base))
    if insecure:
        collected.add("http-version", insecure, body="keep", absolute=True)
    for name, path in _SENSITIVE_PROBES:
        collected.add(name, path, body="none", sensitive=True)
    for techno in dict.fromkeys(str(item).casefold() for item in technos):
        for name, path in _TECHNO_PROBES.get(techno, ()):
            collected.add(name, path, body="none", sensitive=True)
    pages = 0
    for page in key_pages:
        path = _target_path(page.get("url") if isinstance(page, dict) else None, origin)
        if path and cited(
            {"source": "page"},
            f"page-{pages + 1}",
            path,
            body="none",
            sensitive=_is_sensitive(path),
        ):
            pages += 1
    for row in sorted((row for row in rows if _asks_http(row)), key=lambda row: row["ref"]):
        count = 0
        source = {"source": "fiche", "ref": row["ref"]}
        for path in _fiche_paths(row, Path(fiches_dir), origin):
            if cited(source, f"fiche-{row['ref']}-{count + 1}", path, **_content_rule(path)):
                count += 1
    return {"base_url": base, "probes": collected.probes, "discarded": discarded}


def followup_probes(run_dir: Path) -> dict:
    """Return the probes the findings of a run still ask for, from the origin of the audit only.

    The result is {"base_url", "probes", "lots", "discarded"}. probes follows the toolbox format
    and is deduplicated by method and path; lots lists the lots with at least one pending probe;
    discarded lists {lot, ref, url, reason} for each URL kept out because it is not on the origin
    of the target (a probe must never leave it), runs a PHP file outside the application routes
    (validate already refuses it, this is a second barrier), or is unusable. Only the valid
    findings of findings/<lot>.json are read, never the follow-up files.
    """
    run_dir = Path(run_dir)
    context = _read_json(run_dir / "context.json", "le diagnostic")
    if not isinstance(context, dict):
        raise ValueError("context.json : un objet JSON est attendu.")
    base = target_url(context)
    parts = urlsplit(base)
    origin = _origin(parts)
    lots = _lot_entries(_read_json(run_dir / "lots.json", "cqa_plan.py lots"))
    collected = _Collected(origin)
    discarded = []
    pending = list(_pending(run_dir, lots, parts.path))
    for lot_id, ref, probe in pending:
        path, reason = _followup_path(probe["url"], origin, parts.path)
        if path is None:
            shown = _public_url(probe["url"])
            discarded.append({"lot": lot_id, "ref": ref, "url": shown, "reason": reason})
            continue
        collected.add(
            f"followup-{len(collected.probes) + 1}",
            path,
            method=probe["method"],
            **_content_rule(path, probe["method"]),
        )
    return {
        "base_url": base,
        "probes": collected.probes,
        "lots": list(dict.fromkeys(lot_id for lot_id, _, _ in pending)),
        "discarded": discarded,
    }


def pending_lots(run_dir: Path, lots: list, app_path: str = "") -> list:
    """Return the ids of the lots whose valid first-round findings still ask for probes.

    Only the path of the application (app_path) matters, for the validity of the findings: a
    lot is pending whatever becomes of its URLs.
    """
    pending = _pending(Path(run_dir), _lot_entries({"lots": lots}), app_path)
    return list(dict.fromkeys(lot_id for lot_id, _, _ in pending))


def _pending(run_dir: Path, lots: list, app_path: str):
    """Yield (lot id, ref, probe) for each needs_probes entry of the valid first-round findings."""
    for lot, document, strict in first_round_files(run_dir, lots):
        valid, _ = validate_document(
            document,
            set(lot["refs"]),
            app_path=app_path,
            agent_file=strict,
            lot_id=lot["id"] if strict else None,
        )
        for item in valid:
            for probe in item["needs_probes"]:
                yield lot["id"], item["ref"], probe


def first_round_files(run_dir: Path, lots: list):
    """Yield (lot, document, strict) for each readable first-round findings file of the lots.

    strict tells whether the file is read under the rules of 0.18.1 (cqa.contract): a file of a run
    started before them that validate never checked is not read as an agent file.
    """
    context = contract.run_context(run_dir)
    for lot in lots:
        document = _first_round(run_dir, lot["id"])
        if document:
            yield lot, document, contract.strict_file(run_dir, context, lot["id"], f"{lot['id']}.json")


class _Collected:
    """The probes built so far, without a repeated (method, path).

    Whatever its source, a path that does not resolve to the target origin is refused here.
    Only a probe flagged absolute (the http:// version of the base) skips that check.
    """

    def __init__(self, origin: tuple):
        self.probes = []
        self._origin = origin
        self._seen = set()

    def repeats(self, path, method="GET") -> bool:
        """Whether a probe with this method and path is already collected."""
        return (method, path) in self._seen

    def add(self, name, path, *, body, sensitive=False, method="GET", absolute=False) -> bool:
        """Add a probe and return True, unless it repeats an earlier one or leaves the origin."""
        key = (method, path)
        if key in self._seen or not (absolute or _stays_on_origin(path, self._origin)):
            return False
        self._seen.add(key)
        self.probes.append(
            {
                "id": name,
                "method": method,
                "path": path,
                "headers": {},
                "body": body,
                "sensitive": sensitive,
            }
        )
        return True


def _checked_base(url: str, label: str) -> str:
    try:
        validate_url(url)
    except ValueError as error:
        raise ValueError(f"context.json : « {label} » : {error}") from None
    if "?" in url or "#" in url:
        raise ValueError(
            f"context.json : « {label} » ne doit contenir ni « ? » ni « # » : "
            "une requête pourrait porter un jeton."
        )
    return url


def _origin(parts) -> tuple:
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


def _http_version(parts) -> Optional[str]:
    if parts.scheme.lower() != "https" or parts.port not in (None, 443):
        return None
    netloc = parts.netloc[: -len(":443")] if parts.netloc.endswith(":443") else parts.netloc
    return f"http://{netloc}/"


def _target_path(url, origin) -> Optional[str]:
    """Return path?query of a URL on the target origin, None for any other URL."""
    if not isinstance(url, str):
        return None
    try:
        validate_url(url)
        parts = urlsplit(url)
        same = _origin(parts) == origin
    except ValueError:
        return None
    if not same:
        return None
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return path if _stays_on_origin(path, origin) else None


def _stays_on_origin(path: str, origin: tuple) -> bool:
    """Whether a path resolved against the target gives a URL on the target origin.

    "//host/x" and "/\\host/x" (a backslash counts as a slash for browsers and Node) point to
    another host, as does a path holding a control character that URL parsers drop.
    """
    if not path.startswith("/") or path.startswith("//"):
        return False
    if any(char == "\\" or ord(char) <= 32 or ord(char) == 127 for char in path):
        return False
    scheme, host, port = origin
    base = f"{scheme}://{f'[{host}]' if ':' in host else host}:{port}/"
    try:
        return _origin(urlsplit(urljoin(base, path))) == origin
    except ValueError:
        return False


def _followup_path(url: str, origin, app_path: str) -> tuple:
    if not is_utf8_encodable(url):
        return None, UTF8_REASON
    try:
        validate_url(url)
    except ValueError as error:
        return None, str(error)
    path = _target_path(url, origin)
    if path is None:
        return None, "URL hors de l'origine de la cible"
    if requests_php_file(url, app_path):
        return None, PHP_FILE_REASON
    return path, None


def _printable(text: str) -> str:
    """Return the text with a "?" for each lone surrogate, so that it can be printed and written
    to a UTF-8 file."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _public_url(url: str) -> str:
    """Return the URL without the credentials it may carry (see _printable)."""
    url = _printable(url)
    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        netloc = parts.netloc.rpartition("@")[2]
        return parts._replace(netloc=netloc).geturl()
    except ValueError:
        return url


def _is_sensitive(path: str) -> bool:
    """Whether a path exposes something that must never be stored: sensitive base paths, the
    techno exposure paths (whatever the project technos), and what the patterns recognize."""
    bare = path.partition("?")[0].casefold().rstrip("/") or "/"
    return bare in _EXPOSURE_PATHS or bool(_SENSITIVE_PATH.search(path))


def _content_rule(path: str, method: str = "GET") -> dict:
    sensitive = _is_sensitive(path)
    return {"body": "none" if sensitive or method == "HEAD" else "keep", "sensitive": sensitive}


def _asks_http(row: dict) -> bool:
    return "HTTP" in (row.get("tools") or []) and bool(row.get("fiche_path")) and isinstance(
        row.get("ref"), int
    )


def _fiche_paths(row: dict, fiches_dir: Path, origin) -> list:
    try:
        text = (fiches_dir / Path(row["fiche_path"]).name).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    paths = []
    for span in _BACKTICKS.findall(text):
        candidate = span.strip()
        if candidate.startswith(("http://", "https://")):
            path = _target_path(candidate, origin)
        elif _is_target_path(candidate):
            path = candidate
        else:
            path = None
        if path:
            paths.append(path)
    return paths


def _is_target_path(text: str) -> bool:
    if not _TARGET_PATH.fullmatch(text) or text.startswith("//"):
        return False
    segments = text.partition("?")[0].split("/")[1:]
    if ".." in segments:
        return False
    return not (len(segments) > 1 and segments[0] in _SYSTEM_ROOTS)


def _lot_entries(document) -> list:
    lots = document.get("lots") if isinstance(document, dict) else None
    sound = isinstance(lots, list) and all(
        isinstance(lot, dict)
        and isinstance(lot.get("id"), str)
        and isinstance(lot.get("refs"), list)
        for lot in lots
    )
    if not sound:
        raise ValueError("lots.json : la liste « lots » est invalide.")
    return lots


def _first_round(run_dir: Path, lot_id: str):
    try:
        return json.loads((run_dir / "findings" / f"{lot_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_json(path: Path, hint: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{path.name} est introuvable dans {path.parent} : lancer d'abord {hint}.")
    except (OSError, ValueError) as error:
        raise ValueError(f"{path.name} est illisible : {error}")
