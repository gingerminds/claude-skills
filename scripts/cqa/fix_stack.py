"""Read-only stack core (WordPress, Drupal) for the fix sub-agents.

Finds the core in the dev's checkout, reads the version the project pins, and for WordPress
downloads the matching official release into a shared cache. Nothing is ever written into
the dev's folders.
"""

import hashlib
import hmac
import http.client
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from urllib.parse import urlsplit

HOST = "wordpress.org"
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024
MAX_MEMBERS = 20000
TIMEOUT = 30
MARKERS = {"wordpress": "wp-includes/version.php", "drupal": "core/lib/Drupal.php"}
_VERSION = re.compile(r"\d+\.\d+(\.\d+)?", re.ASCII)
_SKIPPED = {".git", "node_modules", "uploads", "cache", "tests"}
_SEARCH_DEPTH = 4
_COMPOSER_PACKAGES = {"wordpress": ("johnpbloch/wordpress", "roots/wordpress"),
                      "drupal": ("drupal/core", "drupal/core-recommended")}
_WP_VERSION = re.compile(r"\$wp_version\s*=\s*'([^']+)'")
_DRUPAL_VERSION = re.compile(r"const\s+VERSION\s*=\s*'([^']+)'")
_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?wordpress:(\d+\.\d+(?:\.\d+)?)(?![\d.])",
                   re.IGNORECASE | re.MULTILINE | re.ASCII)


class StackError(ValueError):
    """A problem with the request, worded for the lead (exit 2)."""


class StackRefused(Exception):
    """A download or an archive that must not be trusted (exit 5); nothing is kept."""


def valid_version(version) -> bool:
    return isinstance(version, str) and _VERSION.fullmatch(version) is not None


def cache_root() -> Path:
    return Path.home() / ".cache" / "gm-audit" / "stacks"


def archive_url(version: str) -> str:
    if not valid_version(version):
        raise StackError(f"Version invalide : {version!r}.")
    return f"https://{HOST}/wordpress-{version}.tar.gz"


class _SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != HOST:
            raise StackRefused(f"Redirection refusée vers {parts.hostname or newurl}.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int = MAX_ARCHIVE) -> bytes:
    """Return the body of an https://wordpress.org URL, at most limit bytes."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise StackRefused(f"Téléchargement refusé : seul https://{HOST} est autorisé.")
    opener = urllib.request.build_opener(_SameHost)
    try:
        with opener.open(url, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except http.client.HTTPException as error:
        raise OSError(f"Téléchargement interrompu : {error!r}")
    if len(data) > limit:
        raise StackRefused(f"Téléchargement refusé : plus de {limit} octets.")
    return data


def marker_in(stack: str, folder: Path) -> bool:
    return (folder / MARKERS[stack]).is_file()


def find_core(stack: str, base: Path) -> Optional[Path]:
    """Return the folder under base (depth limited, shallowest first) holding the core marker."""
    base = Path(base)
    if not base.is_dir():
        return None
    level = [base]
    for _ in range(_SEARCH_DEPTH):
        following = []
        for folder in level:
            if marker_in(stack, folder):
                return folder.resolve()
            try:
                children = sorted((entry for entry in folder.iterdir()
                                   if entry.is_dir() and not entry.is_symlink()
                                   and entry.name not in _SKIPPED),
                                  key=lambda entry: (entry.name == "vendor", entry.name))
            except OSError:
                continue
            following.extend(children)
        level = following
    found = next((folder for folder in level if marker_in(stack, folder)), None)
    return found.resolve() if found else None


def _read(path: Path) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _exact(text) -> Optional[str]:
    if not isinstance(text, str):
        return None
    text = text.strip()
    text = text[1:] if text[:1] in ("v", "=") else text
    return text if valid_version(text) else None


def read_version(stack: str, repo: Path, core: Optional[Path]) -> Optional[str]:
    """Return the version of the core, else the one the project pins, else None."""
    if core is not None:
        text = _read(Path(core) / MARKERS[stack])
        found = re.search(_WP_VERSION if stack == "wordpress" else _DRUPAL_VERSION, text or "")
        if found and valid_version(found.group(1)):
            return found.group(1)
    names = _COMPOSER_PACKAGES[stack]
    repo = Path(repo)
    lock = _json(repo / "composer.lock")
    for section in ("packages", "packages-dev"):
        for package in (lock or {}).get(section) or []:
            if isinstance(package, dict) and package.get("name") in names:
                version = _exact(package.get("version"))
                if version:
                    return version
    manifest = _json(repo / "composer.json")
    for section in ("require", "require-dev"):
        required = (manifest or {}).get(section)
        for name in names:
            version = _exact(required.get(name)) if isinstance(required, dict) else None
            if version:
                return version
    if stack == "wordpress":
        for dockerfile in _dockerfiles(repo):
            found = _FROM.search(_read(dockerfile) or "")
            if found:
                return found.group(1)
    return None


def _json(path: Path):
    text = _read(path)
    try:
        value = json.loads(text) if text else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _dockerfiles(repo: Path) -> list:
    found = [repo / "Dockerfile"]
    for pattern in ("docker/Dockerfile*", "docker/*/Dockerfile*", "Dockerfile.*"):
        found.extend(sorted(repo.glob(pattern)))
    return [path for path in found if path.is_file()]


def check_path(stack: str, folder) -> Path:
    """Return the resolved folder when it holds the core marker of the stack."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise StackError(f"Le dossier {path} n'existe pas.")
    resolved = path.resolve()
    if not marker_in(stack, resolved):
        raise StackError(
            f"Le dossier {resolved} ne contient pas le cœur {stack} ({MARKERS[stack]} introuvable)."
        )
    return resolved


def cached(stack: str, version: str, root: Optional[Path] = None) -> Optional[Path]:
    if stack != "wordpress" or not valid_version(version):
        return None
    target = (root or cache_root()) / stack / version
    return target.resolve() if marker_in(stack, target) else None


def has_data_filter() -> bool:
    return hasattr(tarfile, "data_filter")


def _clean_stale(parent: Path) -> None:
    if not parent.is_dir() or parent.is_symlink():
        return
    limit = time.time() - 86400
    for entry in parent.glob(".tmp-*"):
        try:
            if entry.is_dir() and not entry.is_symlink() and entry.stat().st_mtime < limit:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def download(version: str, root: Path, fetcher: Optional[Callable] = None) -> Path:
    """Return the cache folder of a WordPress release, downloading it when missing."""
    fetcher = fetcher or fetch
    url = archive_url(version)
    root = Path(root)
    target = root / "wordpress" / version
    if marker_in("wordpress", target):
        return target.resolve()
    if not has_data_filter():
        raise StackError(
            "Cette version de Python ne sait pas extraire une archive en sécurité "
            "(tarfile.data_filter) : indiquez un dossier (--path)."
        )
    _clean_stale(root / "wordpress")
    archive = fetcher(url, MAX_ARCHIVE)
    expected = _expected_sha1(fetcher(url + ".sha1", 1024))
    if not hmac.compare_digest(hashlib.sha1(archive).hexdigest(), expected):
        raise StackRefused(
            "L'empreinte SHA-1 de l'archive ne correspond pas à celle de wordpress.org : "
            "rien n'a été extrait."
        )
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as error:
        raise StackRefused(f"Archive illisible : {error}")
    with tar:
        members = _safe_members(tar)
        parent = root / "wordpress"
        parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".tmp-", dir=parent))
        try:
            try:
                tar.extractall(work, members=members, filter="data")
            except (tarfile.TarError, OSError, EOFError, KeyError) as error:
                raise StackRefused(f"Extraction refusée : {error}")
            core = work / "wordpress"
            if core.is_symlink() or not core.is_dir() or not marker_in("wordpress", core):
                raise StackRefused("L'archive ne contient pas wordpress/wp-includes/version.php.")
            if not marker_in("wordpress", target):
                if target.exists() or target.is_symlink():
                    shutil.rmtree(target) if target.is_dir() and not target.is_symlink() \
                        else target.unlink()
                try:
                    os.rename(core, target)
                except OSError:
                    if not marker_in("wordpress", target):
                        raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    return target.resolve()


def _expected_sha1(raw: bytes) -> str:
    words = raw.decode("ascii", "replace").split()
    digest = words[0].lower() if words else ""
    if not re.fullmatch(r"[0-9a-f]{40}", digest):
        raise StackRefused("Fichier .sha1 illisible : rien n'a été extrait.")
    return digest


def _inside(path: PurePosixPath) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def _safe_members(tar: tarfile.TarFile) -> list:
    members = tar.getmembers()
    if len(members) > MAX_MEMBERS:
        raise StackRefused("Archive refusée : trop de fichiers.")
    total = 0
    for member in members:
        name = PurePosixPath(member.name)
        if not _inside(name) or "\\" in member.name or "\0" in member.name:
            raise StackRefused(f"Archive refusée : chemin « {member.name} » hors du dossier.")
        if member.isfile():
            total += member.size
            if total > MAX_UNPACKED:
                raise StackRefused("Archive refusée : contenu trop volumineux.")
        elif member.issym() or member.islnk():
            raise StackRefused(
                "Archive refusée : elle contient des liens (symboliques ou durs). "
                "Indiquez un dossier (--path)."
            )
        elif not member.isdir():
            raise StackRefused(f"Archive refusée : « {member.name} » n'est pas un fichier.")
    return members
