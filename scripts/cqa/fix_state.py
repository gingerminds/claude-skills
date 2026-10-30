"""State of a fix pass (fix/state.json) and the fingerprint of the dev's own checkout.

A key is a repo: one branch and one worktree (fix/<repo>/wt), and its sections, run one
after the other. A pass opened by the v2 fix mode has one key per repo and category
(« <repo>/<slug> », with a slug); it stays readable so that it can be abandoned.
"""
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cqa import fix_names
from cqa.notion_read import write_json

STATE_FILE = "fix/state.json"
STAGES = ("keys", "questions", "notion", "summary")
STEPS = ("selected", "started", "fixed", "checked", "reviewed", "published", "done")
OUTCOMES = ("pending", "fixed", "skipped", "question", "dropped", "done_before")
SECTION_STATES = ("pending", "active", "settled", "failed")
MODES = ("autonomous", "present")
PUSHES = ("mr", "local", "dry_run")
# The worktree of a key, then the one of a v2 key (fix/<repo>/<slug>/wt).
WORKTREE_DIRS = ("fix/[!_]*/wt", "fix/[!_]*/*/wt")
HISTORY_DIR = "fix/_history"
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ALIAS = re.compile(r"[a-z0-9]+")
_BLOB = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# The secret labels the lead may waive (key["secret_waivers"]): the heuristic ones of the
# guard (fix_guard.HEURISTIC_SECRET_LABELS, kept equal there).
WAIVABLE_LABELS = frozenset({"credential_assignment", "credential_define"})


class StateError(ValueError):
    """A fix/state.json that cannot be read as a fix pass."""


LINKS_FILE = "fix/mr-links.json"


def read_mr_links(run_dir: Path) -> dict:
    """Return {ref: [url, ...]} from fix/mr-links.json, {} when the file is absent.

    Raises ValueError when the file cannot be read as {"links": {"<ref>": [{"url": ...}]}}.
    """
    return {ref: [entry["url"] for entry in entries]
            for ref, entries in read_mr_link_entries(run_dir).items()}


def read_mr_link_entries(run_dir: Path) -> dict:
    """Return {ref: [entry, ...]} from fix/mr-links.json, in the order of the file (the newest
    link last); {} when the file is absent. An entry is {"repo", "url", "iid", "partial",
    "status_before"}; a file of the v2 fix mode has neither of the last two: partial is then
    False and status_before None (also for a value that is no status name).

    Raises ValueError when the file cannot be read as {"links": {"<ref>": [{"url": ...}]}}.
    """
    try:
        document = json.loads((Path(run_dir) / LINKS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError(f"{LINKS_FILE} est illisible : {error}")
    links = document.get("links") if isinstance(document, dict) else None
    if not isinstance(links, dict):
        raise ValueError(f"{LINKS_FILE} est illisible : « links » doit être un objet.")
    found = {}
    for key, entries in links.items():
        listed = entries if isinstance(entries, list) else [None]
        urls = [entry.get("url") if isinstance(entry, dict) else None for entry in listed]
        if not str(key).isdigit() or not all(isinstance(url, str) and url.strip() for url in urls):
            raise ValueError(f"{LINKS_FILE} est illisible : « {key} » doit lister des entrées avec une « url ».")
        if listed:
            found[int(key)] = [_link_entry(entry) for entry in listed]
    return found


def _link_entry(entry: dict) -> dict:
    before = entry.get("status_before")
    return {**entry, "partial": bool(entry.get("partial")),
            "status_before": before if isinstance(before, str) and before.strip() else None}


def now() -> str:
    """Return the current UTC time as written in the state."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(run_dir: Path) -> dict:
    """Return the state of the pass. Raises FileNotFoundError, or StateError when invalid."""
    path = Path(run_dir) / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as error:
        raise StateError(f"{STATE_FILE} est illisible : {error}")
    if not isinstance(state, dict) or not isinstance(state.get("keys"), dict):
        raise StateError(f"{STATE_FILE} : un objet avec « keys » est attendu.")
    if state.get("stage") not in STAGES or state.get("push") not in PUSHES:
        raise StateError(f"{STATE_FILE} : « stage » ou « push » inconnu.")
    for name, key in state["keys"].items():
        if (
            not isinstance(key, dict)
            or not isinstance(key.get("rows"), dict)
            or not _REPO.fullmatch(str(key.get("repo")))
            or not (_v2_key(key) if "slug" in key else _key(name, key))
        ):
            raise StateError(f"{STATE_FILE} : la clé « {name} » est invalide.")
        if not _waivers_ok(key.get("secret_waivers")):
            raise StateError(
                f"{STATE_FILE} : « secret_waivers » de la clé « {name} » est invalide : une "
                "liste d'objets {\"blob\", \"path\", \"line\", \"label\", \"at\"} est attendue "
                "(blob : SHA du contenu du fichier ; line : entier à partir de 1 ; label : "
                f"{' ou '.join(sorted(WAIVABLE_LABELS))})."
            )
        if not _left_ok(key.get("left")):
            raise StateError(
                f"{STATE_FILE} : « left » de la clé « {name} » est invalide : null, ou un objet "
                "{\"reason\", \"at\"} écrit par cqa_fix.py leave, est attendu."
            )
    return state


def _left_ok(value) -> bool:
    """True for a key not left (None), or left to the lead ({"reason", "at"}, cqa_fix.py leave)."""
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and isinstance(value.get("reason"), str) and value["reason"].strip() != ""
        and isinstance(value.get("at"), str)
    )


def is_left(key: dict) -> bool:
    """True for a key left to the lead: its branch and worktree stay as they are, and reorder,
    reword, mr-body, publish and finish refuse it until leave --clear."""
    return isinstance(key.get("left"), dict)


def _waivers_ok(value) -> bool:
    """True for no waivers, or a list of secret waivers that the guard can match."""
    if value is None:
        return True
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("blob"), str) and _BLOB.fullmatch(item["blob"]) is not None
        and isinstance(item.get("path"), str) and item["path"] != ""
        and isinstance(item.get("line"), int) and not isinstance(item["line"], bool)
        and item["line"] >= 1
        and isinstance(item.get("label"), str) and item["label"] in WAIVABLE_LABELS
        for item in value
    )


def is_v2(state: dict) -> bool:
    """Return True for a pass opened by the v2 fix mode (keys per repo and category)."""
    return any(isinstance(key, dict) and "slug" in key for key in state["keys"].values())


def _key(name: str, key: dict) -> bool:
    branch = key.get("branch")
    sections = key.get("sections")
    return (
        name == key.get("repo")
        and (branch is None or isinstance(branch, str) and any(
            fix_names.is_pass_branch(branch, base)
            for base in (fix_names.BRANCH_BASE, fix_names.DRY_RUN_BASE)
        ))
        and isinstance(sections, list)
        and all(
            isinstance(section, dict) and _ALIAS.fullmatch(str(section.get("alias")))
            and section.get("state") in SECTION_STATES and isinstance(section.get("refs"), list)
            for section in sections
        )
    )


def _v2_key(key: dict) -> bool:
    branch = key.get("branch")
    return _SLUG.fullmatch(str(key.get("slug"))) is not None and (branch is None or (
        isinstance(branch, str) and any(
            branch.startswith(prefix) and _SLUG.fullmatch(branch[len(prefix):])
            for prefix in (fix_names.BRANCH_PREFIX, fix_names.DRY_RUN_PREFIX)
        )
    ))


def save(run_dir: Path, state: dict) -> None:
    """Write the state atomically (temporary file, then rename)."""
    write_json(Path(run_dir) / STATE_FILE, state)


def pending(run_dir: Path) -> bool:
    """Return True when fix/state.json exists without finished_at (an unreadable one counts)."""
    path = Path(run_dir) / STATE_FILE
    if not path.is_file():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not (isinstance(state, dict) and state.get("finished_at"))


def new_state(candidates: dict, *, mode: str, push: str, notion: bool,
              project_name: Optional[str] = None) -> dict:
    """Return the state of a new pass over the keys of fix/candidates.json.

    Each key is a repo with its sections in order, all pending; each row records the alias
    of its section. Raises ValueError (or KeyError, TypeError) for candidates of another
    shape, such as those of the v2 fix mode.
    """
    keys = {}
    for entry in candidates.get("keys") or []:
        if entry["key"] != entry["repo"] or not _REPO.fullmatch(str(entry["repo"])):
            raise ValueError(f"clé « {entry['key']} » : le nom du repo est attendu.")
        sections, rows = [], {}
        for section in entry["sections"]:
            alias = section["alias"]
            if not _ALIAS.fullmatch(str(alias)) or any(item["alias"] == alias for item in sections):
                raise ValueError(f"clé « {entry['key']} » : section « {alias} » invalide.")
            refs = [row["ref"] for row in section["rows"]]
            sections.append({"category": section.get("category"), "alias": alias, "refs": refs,
                             "state": "pending", "reason": None})
            for ref in refs:
                rows[str(ref)] = {"outcome": "pending", "reason": None, "commits": [], "pass": 0,
                                  "attention": None, "section": alias}
        keys[entry["key"]] = {
            "repo": entry["repo"],
            "push_ready": bool(entry.get("push_ready")),
            "branch": None,
            "base": None,
            "start_sha": None,
            "worktree": None,
            "reused": None,
            "mr": None,
            "step": "selected",
            "pass": 0,
            "dev_fingerprint": None,
            "bases": {},
            "reviews": {},
            "reports": [],
            "sections": sections,
            "pass_sections": {},
            "rows": rows,
            "left": None,
        }
    return {
        "started_at": now(),
        "finished_at": None,
        "stage": "keys",
        "mode": mode,
        "push": push,
        "notion": bool(notion) and push != "dry_run",
        "project_name": project_name,
        "keys": keys,
    }


def dev_fingerprint(repo_path, run) -> dict:
    """Return what the dev's checkout looks like: HEAD, branch, stash and dirty paths.

    status maps each path of git status (untracked files included) to its status code and the
    size and modification time of the file; no content is read. Nothing is written in the
    repo, not even the index.
    """
    head = fix_names.git_out(repo_path, run, "rev-parse", "--verify", "--quiet", "HEAD")
    branch = fix_names.git_out(repo_path, run, "symbolic-ref", "--quiet", "--short", "HEAD")
    stash = _checked(repo_path, run, "git stash list", "stash", "list", "--format=%H")
    status = _checked(
        repo_path, run, "git status",
        "--no-optional-locks", "status", "--porcelain=v1", "-uall", "-z",
    )
    return {
        "head": head,
        "branch": branch,
        "stash": stash.split(),
        "status": {
            path: f"{code} {_stat(Path(repo_path) / path)}"
            for path, code in _status_entries(status)
        },
    }


def fingerprint_changes(before: dict, after: dict) -> Optional[dict]:
    """Return what changed between two fingerprints of the dev's checkout, or None.

    paths lists the paths whose status entry changed, appeared or disappeared; head, branch
    and stash say whether those moved.
    """
    old, new = before.get("status") or {}, after.get("status") or {}
    changes = {
        "paths": sorted(path for path in set(old) | set(new) if old.get(path) != new.get(path)),
        "head": before.get("head") != after.get("head"),
        "branch": before.get("branch") != after.get("branch"),
        "stash": before.get("stash") != after.get("stash"),
    }
    return changes if any(changes.values()) else None


def dev_checkout_alert(key: dict, repo_path, run) -> Optional[dict]:
    """Return what changed in the dev's checkout since the last alert of the key (or since its
    start), as fingerprint_changes does, or None.

    An alert moves the reference (key["dev_fingerprint"]) to the current fingerprint, so that
    one change is reported once; the caller saves the state. A key without a reference gives
    None.
    """
    before = key.get("dev_fingerprint")
    if not isinstance(before, dict):
        return None
    after = dev_fingerprint(repo_path, run)
    changes = fingerprint_changes(before, after)
    if changes is not None:
        key["dev_fingerprint"] = after
    return changes


def _checked(repo_path, run, label: str, *args: str) -> str:
    result = fix_names.git(repo_path, run, *args)
    if result.returncode != 0:
        raise ValueError(f"{label} a échoué dans {repo_path} : {result.stderr.strip()}")
    return result.stdout


def _status_entries(text: str) -> list:
    """Return (path, XY) pairs of git status --porcelain=v1 -z; a rename gives its new path."""
    fields = text.split("\0")
    entries, index = [], 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        code, path = field[:2], field[3:]
        if "R" in code or "C" in code:
            index += 1
        entries.append((path, code))
    return entries


def _stat(path: Path) -> str:
    """Return the kind, size and mtime of a path, without following links or reading it."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISLNK(info.st_mode):
        kind = "link"
    elif stat.S_ISDIR(info.st_mode):
        return "dir"
    else:
        return "special"
    return f"{kind}:{info.st_size}:{info.st_mtime_ns}"
