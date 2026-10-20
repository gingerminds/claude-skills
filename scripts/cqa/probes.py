"""Probes of the audit: the HTTP requests the toolbox sends to the target (probes.json)."""
import json
import re
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from cqa.findings import validate_document
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


def target_url(context: dict) -> str:
    """Return the base URL of the audit: the prod when is_prod_run, else the preprod.

    Raises ValueError, with a message that never quotes the URL, when the URL is missing, not a
    plain http(s) URL, or carries a query or a fragment (a token could hide there).
    """
    target = context.get("target")
    if not isinstance(target, dict):
        raise ValueError("context.json : « target » est absent.")
    key = "prod_url" if is_prod_run(context) else "preprod_url"
    url = target.get(key)
    if not isinstance(url, str) or not url:
        raise ValueError(f"context.json : « target.{key} » est absent ou vide.")
    return _checked_base(url, f"target.{key}")


def build_probes(
    *,
    base_url: str,
    technos: list[str],
    rows: list[dict],
    fiches_dir: Path,
    key_pages: list[dict],
) -> dict:
    """Return the probes.json document: the fixed set, then the key pages and the fiche citations.

    Order: content probes (body kept), sensitive probes (never any body), probes of each project
    techno, key pages (headers only), then the paths and target URLs that the fiches of the rows
    asking for HTTP quote between backticks. Every path is relative to the origin of base_url,
    and a probe that repeats an earlier one is left out. All probes are GET.
    """
    base = _checked_base(base_url, "base_url")
    origin = _origin(urlsplit(base))
    collected = _Collected()
    for name, path in _CONTENT_PROBES:
        collected.add(name, path, body="keep")
    collected.add("not-found", f"/cqa-404-{uuid.uuid4().hex[:8]}", body="keep")
    insecure = _http_version(urlsplit(base))
    if insecure:
        collected.add("http-version", insecure, body="keep")
    for name, path in _SENSITIVE_PROBES:
        collected.add(name, path, body="none", sensitive=True)
    for techno in dict.fromkeys(str(item).casefold() for item in technos):
        for name, path in _TECHNO_PROBES.get(techno, ()):
            collected.add(name, path, body="none", sensitive=True)
    pages = 0
    for page in key_pages:
        path = _target_path(page.get("url") if isinstance(page, dict) else None, origin)
        if path and collected.add(f"page-{pages + 1}", path, body="none"):
            pages += 1
    for row in sorted((row for row in rows if _asks_http(row)), key=lambda row: row["ref"]):
        cited = 0
        for path in _fiche_paths(row, Path(fiches_dir), origin):
            if collected.add(f"fiche-{row['ref']}-{cited + 1}", path, **_content_rule(path)):
                cited += 1
    return {"base_url": base, "probes": collected.probes}


def followup_probes(run_dir: Path) -> dict:
    """Return the probes the findings of a run still ask for, from the origin of the audit only.

    The result is {"base_url", "probes", "lots", "discarded"}. probes follows the toolbox format
    and is deduplicated by method and path; lots lists the lots with at least one pending probe;
    discarded lists {lot, ref, url, reason} for each URL kept out because it is not on the origin
    of the target (a probe must never leave it), or is unusable. Only the valid findings of
    findings/<lot>.json are read, never the follow-up files.
    """
    run_dir = Path(run_dir)
    context = _read_json(run_dir / "context.json", "le diagnostic")
    if not isinstance(context, dict):
        raise ValueError("context.json : un objet JSON est attendu.")
    base = target_url(context)
    origin = _origin(urlsplit(base))
    lots = _lot_entries(_read_json(run_dir / "lots.json", "cqa_plan.py lots"))
    collected = _Collected()
    pending_lots, discarded = [], []
    for lot in lots:
        document = _first_round(run_dir, lot["id"])
        valid, _ = validate_document(document, set(lot["refs"])) if document else ([], [])
        found = False
        for item in valid:
            for probe in item["needs_probes"]:
                found = True
                path, reason = _followup_path(probe["url"], origin)
                if path is None:
                    shown = _public_url(probe["url"])
                    discarded.append(
                        {"lot": lot["id"], "ref": item["ref"], "url": shown, "reason": reason}
                    )
                    continue
                collected.add(
                    f"followup-{len(collected.probes) + 1}",
                    path,
                    method=probe["method"],
                    **_content_rule(path, probe["method"]),
                )
        if found:
            pending_lots.append(lot["id"])
    return {
        "base_url": base,
        "probes": collected.probes,
        "lots": pending_lots,
        "discarded": discarded,
    }


class _Collected:
    """The probes built so far, without a repeated (method, path)."""

    def __init__(self):
        self.probes = []
        self._seen = set()

    def add(self, name, path, *, body, sensitive=False, method="GET") -> bool:
        """Add a probe and return True, unless it repeats an earlier one."""
        key = (method, path)
        if key in self._seen:
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
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _followup_path(url: str, origin) -> tuple:
    try:
        validate_url(url)
    except ValueError as error:
        return None, str(error)
    path = _target_path(url, origin)
    if path is None:
        return None, "URL hors de l'origine de la cible"
    return path, None


def _public_url(url: str) -> str:
    """Return the URL without the credentials it may carry."""
    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        netloc = parts.netloc.rpartition("@")[2]
        return parts._replace(netloc=netloc).geturl()
    except ValueError:
        return url


def _is_sensitive(path: str) -> bool:
    return bool(_SENSITIVE_PATH.search(path))


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
