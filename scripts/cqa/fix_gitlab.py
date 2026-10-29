"""GitLab reading of the fix mode: access diagnostic and fixes already in flight."""
import json
import os
import re
from typing import Optional
from urllib.parse import quote

from cqa import fix_names
from cqa.git_tools import _parse_remote

_PUSH_LEVEL = 30
NO_API_NOTE = "GitLab illisible : les MR existantes ne sont pas connues."
# What glab prints when GitLab refuses the token; any other failure never reached GitLab.
_REFUSED = re.compile(r"\b40[13]\b|unauthori[sz]ed|forbidden|glab auth login", re.IGNORECASE)


def host_and_path(remote: str) -> tuple:
    """Return (host, project path) of a remote; the host carries the port for https."""
    scheme, host, port, path = _parse_remote(remote)
    return (f"{host}:{port}" if scheme == "https" and port else host), path


def glab(run, host: str, *args: str, cwd=None):
    """Run glab against host, with a copy of the environment. Raises OSError if glab is absent."""
    env = dict(os.environ)
    env["GITLAB_HOST"] = host
    return run(
        ["glab", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )


def glab_json(run, host: str, api_path: str, paginate: bool = False) -> Optional[object]:
    """Return the JSON answer of glab api, or None when glab fails or does not answer in JSON."""
    args = ["api", *(["--paginate"] if paginate else []), api_path]
    try:
        result = glab(run, host, *args)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return _parse_pages(result.stdout, paginate)


def _parse_pages(text: str, paginate: bool):
    decoder = json.JSONDecoder()
    values, index, text = [], 0, text or ""
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError:
            return None
        values.append(value)
    if not values:
        return None
    if not paginate:
        return values[0] if len(values) == 1 else None
    if all(isinstance(value, list) for value in values):
        return [item for value in values for item in value]
    return values[0] if len(values) == 1 else None


def repo_access(run, repo: dict) -> dict:
    """Return the GitLab access diagnostic of a repo.

    status is ok, not_gitlab, glab_missing, unauthenticated (GitLab answered 401 or 403),
    unreachable (glab api user failed without such an answer: network, VPN, proxy, DNS) or
    no_push. user and base are set when status is ok (base: develop when the remote has it,
    else the default branch).
    """
    result = {"repo": repo.get("name"), "host": None, "project": None,
              "status": "not_gitlab", "user": None, "base": None}
    try:
        host, project = host_and_path(repo.get("remote") or "")
    except ValueError:
        return result
    result["host"], result["project"] = host, project
    if host.split(":")[0] == "github.com":
        return result
    try:
        user = glab(run, host, "api", "user")
    except OSError:
        result["status"] = "glab_missing"
        return result
    if user.returncode != 0:
        refused = _REFUSED.search(f"{user.stdout or ''}\n{user.stderr or ''}")
        result["status"] = "unauthenticated" if refused else "unreachable"
        return result
    if glab_json(run, host, "version") is None:
        return result
    try:
        username = json.loads(user.stdout).get("username")
    except (ValueError, AttributeError):
        username = None
    info = glab_json(run, host, f"projects/{quote(project, safe='')}")
    if not isinstance(info, dict) or _access_level(info) < _PUSH_LEVEL:
        result["status"] = "no_push"
        return result
    result["status"], result["user"] = "ok", username
    heads = fix_names.git(repo.get("path"), run, "ls-remote", "--heads", "origin", "develop")
    has_develop = heads.returncode == 0 and heads.stdout.strip() != ""
    result["base"] = "develop" if has_develop else info.get("default_branch")
    return result


def _access_level(info: dict) -> int:
    levels = []
    permissions = info.get("permissions")
    for key in ("project_access", "group_access"):
        entry = permissions.get(key) if isinstance(permissions, dict) else None
        level = entry.get("access_level") if isinstance(entry, dict) else None
        if isinstance(level, int):
            levels.append(level)
    return max(levels, default=0)


def in_flight(run, repo: dict, host: str, project: str, base: str, *, api: bool) -> dict:
    """Return the fix MRs and branches already there, with the checklist refs they carry.

    open and closed come from the MR listings; merged only holds the MRs found for the
    branches that git still knows, one query per branch. The refs of an MR whose commits
    cannot be read are None. When GitLab answers only partly, the three lists are empty and
    a note says so.
    """
    found = {"open": [], "closed": [], "merged": [], "branches": [], "notes": []}
    path = repo.get("path")
    encoded = quote(project, safe="")
    local = sorted(_branches(path, run).items())
    tied = set()
    if api:
        for key, state in (("open", "opened"), ("closed", "closed")):
            listing = glab_json(
                run, host, f"projects/{encoded}/merge_requests?state={state}&per_page=100",
                paginate=True,
            )
            if not isinstance(listing, list):
                api = False
                break
            for mr in listing:
                entry = _mr_entry(run, host, encoded, mr, found["notes"])
                if entry:
                    found[key].append(entry)
                    tied.add(entry["branch"])
    candidates = [(b, w) for b, w in local if b not in tied]
    candidates = [(b, w) for b, w in candidates if not _merged(path, run, _source(b, w), base)]
    for branch, where in candidates:
        if api:
            query = f"merge_requests?source_branch={quote(branch, safe='')}&state=merged&per_page=100"
            hits = glab_json(run, host, f"projects/{encoded}/{query}")
            if not isinstance(hits, list):
                api = False
            else:
                entries = [_mr_entry(run, host, encoded, mr, found["notes"]) for mr in hits]
                entries = [entry for entry in entries if entry]
                if entries:
                    found["merged"].extend(entries)
                    continue
        if not api:
            break
    if not api:
        found["open"], found["closed"], found["merged"] = [], [], []
        found["notes"] = [note for note in found["notes"] if not note.startswith("Commits")]
        found["notes"].append(NO_API_NOTE)
    merged_branches = {entry["branch"] for entry in found["merged"]}
    for branch, where in candidates:
        if branch in merged_branches:
            continue
        log = fix_names.git(
            path, run, "log", "--format=%s", f"origin/{base}..{_source(branch, where)}"
        )
        subjects = log.stdout.splitlines() if log.returncode == 0 else []
        found["branches"].append({"branch": branch, "where": where, "refs": _refs(subjects)})
    return found


def _source(branch: str, where: str) -> str:
    return branch if where != "origin" else f"origin/{branch}"


def _mr_entry(run, host: str, encoded: str, mr, notes: list):
    branch = mr.get("source_branch") if isinstance(mr, dict) else None
    if not isinstance(branch, str) or not fix_names.is_fix_branch(branch):
        return None
    commits = glab_json(
        run, host, f"projects/{encoded}/merge_requests/{mr.get('iid')}/commits?per_page=100",
        paginate=True,
    )
    if isinstance(commits, list):
        refs = _refs([c.get("title", "") for c in commits if isinstance(c, dict)])
    else:
        refs = None
        notes.append(f"Commits de la MR !{mr.get('iid')} illisibles : ses Réf sont inconnues.")
    return {"iid": mr.get("iid"), "url": mr.get("web_url"), "branch": branch, "refs": refs}


def _refs(subjects: list) -> list:
    refs = {fix_names.ref_of_subject(subject) for subject in subjects}
    return sorted(ref for ref in refs if ref is not None)


def _branches(path, run) -> dict:
    names = {}
    for ref, strip, where in (("refs/heads/", "refs/heads/", "local"),
                              ("refs/remotes/origin/", "refs/remotes/origin/", "origin")):
        out = fix_names.git(path, run, "for-each-ref", "--format=%(refname)", ref)
        for line in out.stdout.splitlines() if out.returncode == 0 else []:
            name = line[len(strip):]
            if not fix_names.is_fix_branch(name):
                continue
            names[name] = "both" if name in names else where
    return names


def _merged(path, run, source: str, base: str) -> bool:
    check = fix_names.git(path, run, "merge-base", "--is-ancestor", source, f"origin/{base}")
    return check.returncode == 0
