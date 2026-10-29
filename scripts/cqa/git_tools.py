"""Git facts of the audit: repo state, alignment with the preprod commit, GitLab links."""
import json
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

_PREPROD_ENVIRONMENT = re.compile(r"pr[eé]prod|staging|recette|release", re.IGNORECASE)
_SCP_LIKE = re.compile(r"(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>[^/].*)")
_GIT_SUFFIX = re.compile(r"\.git$")


def remote_web_url(remote: str) -> str:
    """Return the web URL of a git remote, without credentials, port of ssh or ".git".

    Handles "git@host:grp/repo.git", "ssh://git@host:2222/grp/repo.git" and
    "https://host/grp/repo.git". The scheme is https, except for an http remote. Raises
    ValueError when the remote is not of the form host plus a path of two segments at least.
    """
    scheme, host, port, path = _parse_remote(remote)
    if scheme == "http":
        host = f"{host}:{port}" if port else host
        return f"http://{host}/{path}"
    host = f"{host}:{port}" if scheme == "https" and port else host
    return f"https://{host}/{path}"


def same_gitlab_group(a: str, b: str) -> bool:
    """Return True when two remotes sit on the same host and the same group.

    The path is compared without its last segment, the repository name. A remote that is
    not understood is never a sibling.
    """
    try:
        _, host_a, _, path_a = _parse_remote(a)
        _, host_b, _, path_b = _parse_remote(b)
    except ValueError:
        return False
    return host_a == host_b and path_a.rpartition("/")[0] == path_b.rpartition("/")[0]


def blob_url(web_url: str, sha: str, path: str, line: Optional[int] = None) -> str:
    """Return the GitLab link of a file at a commit, anchored on line when there is one.

    The path is percent-encoded, slashes kept, so a path with a space gives a valid link.
    """
    url = f"{web_url}/-/blob/{sha}/{quote(path, safe='/')}"
    return url if line is None else f"{url}#L{line}"


def repo_state(path: Path, base: str = "develop", run=subprocess.run) -> dict:
    """Return the state of a local repo against origin/<base>.

    The keys are branch ("HEAD" when detached), clean (no change, untracked files included),
    head (full sha), ahead and behind (commits of HEAD not in origin/<base>, and the
    reverse; both None when origin/<base> is unknown) and fetched. origin is fetched first;
    when that fails, fetched is False and the counts rely on the last fetch. Raises
    RuntimeError when path is not a usable git repository.
    """
    fetched = _git(path, run, "fetch", "origin", "--quiet").returncode == 0
    branch = _checked(path, run, "rev-parse", "--abbrev-ref", "HEAD")
    head = _checked(path, run, "rev-parse", "HEAD")
    clean = not _checked(path, run, "status", "--porcelain")
    counts = _git(path, run, "rev-list", "--left-right", "--count", f"origin/{base}...HEAD")
    behind = ahead = None
    if counts.returncode == 0:
        behind, ahead = (int(number) for number in counts.stdout.split())
    return {
        "branch": branch,
        "clean": clean,
        "head": head,
        "ahead": ahead,
        "behind": behind,
        "fetched": fetched,
    }


def alignment(
    path: Path, deployed_sha: str, base_ref: str = "origin/develop", run=subprocess.run
) -> dict:
    """Return whether the deployed commit contains base_ref, and what it lacks if not.

    aligned is True when base_ref is an ancestor of deployed_sha, False when commits of
    base_ref are missing (listed newest first as sha and title), None when it cannot be told:
    reason is then "commit_inconnu" (deployed_sha is not in the repo) or "base_inconnue"
    (base_ref cannot be resolved).
    """
    if _git(path, run, "cat-file", "-e", f"{deployed_sha}^{{commit}}").returncode != 0:
        return _alignment(None, [], "commit_inconnu")
    ancestor = _git(path, run, "merge-base", "--is-ancestor", base_ref, deployed_sha)
    if ancestor.returncode == 0:
        return _alignment(True, [], None)
    if ancestor.returncode != 1:
        return _alignment(None, [], "base_inconnue")
    log = _git(path, run, "log", "--format=%h%x09%s", f"{deployed_sha}..{base_ref}")
    missing = []
    for line in log.stdout.split("\n"):
        if line:
            sha, _, title = line.partition("\t")
            missing.append({"sha": sha, "title": title})
    return _alignment(False, missing, None)


def deployed_from_gitlab(
    project_path: str, preprod_branch: Optional[str], run=subprocess.run
) -> dict:
    """Return the commit deployed in preprod, read from GitLab with the glab CLI.

    The source is the last successful deployment of the first environment whose name looks
    like preprod, staging, recette or release; failing that, the last successful pipeline
    of preprod_branch. The result is {"sha": None, "source": None} when GitLab tells
    nothing, glab missing or not authenticated included. glab targets the host of the
    current directory's remote, or its default host.
    """
    project = f"projects/{quote(project_path, safe='')}"
    name = _preprod_environment(_glab_json(run, f"{project}/environments"))
    if name:
        query = f"environment={quote(name, safe='')}&status=success"
        query += "&order_by=finished_at&sort=desc&per_page=1"
        sha = _first_sha(_glab_json(run, f"{project}/deployments?{query}"))
        if sha:
            return {"sha": sha, "source": "environment"}
    if preprod_branch:
        query = f"ref={quote(preprod_branch, safe='')}&status=success&per_page=1"
        sha = _first_sha(_glab_json(run, f"{project}/pipelines?{query}"))
        if sha:
            return {"sha": sha, "source": "pipeline"}
    return {"sha": None, "source": None}


def _parse_remote(remote: str) -> tuple:
    text = remote.strip()
    if "://" in text:
        parts = urlsplit(text)
        scheme, host, port, path = parts.scheme.lower(), parts.hostname, parts.port, parts.path
    else:
        found = _SCP_LIKE.fullmatch(text)
        if not found:
            raise ValueError(f"remote git non reconnu : {remote!r}")
        scheme, host, port, path = "ssh", found.group("host").lower(), None, found.group("path")
    path = _GIT_SUFFIX.sub("", path.strip("/")).strip("/")
    if not host or "/" not in path:
        raise ValueError(f"remote git non reconnu : {remote!r}")
    return scheme, host, port, path


def _git(path: Path, run, *args: str):
    return run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _checked(path: Path, run, *args: str) -> str:
    result = _git(path, run, *args)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} a échoué dans {path} : {result.stderr.strip()}")
    return result.stdout.strip()


def _alignment(aligned: Optional[bool], missing: list, reason: Optional[str]) -> dict:
    return {"aligned": aligned, "missing": missing, "reason": reason}


def _glab_json(run, api_path: str):
    try:
        result = run(
            ["glab", "api", api_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def _preprod_environment(environments) -> Optional[str]:
    if not isinstance(environments, list):
        return None
    for item in environments:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and _PREPROD_ENVIRONMENT.search(name):
            return name
    return None


def _first_sha(items) -> Optional[str]:
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    sha = items[0].get("sha")
    return sha if isinstance(sha, str) and sha else None
