"""State of a fix pass (fix/state.json) and the fingerprint of the dev's own checkout."""
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from cqa import fix_names
from cqa.notion_read import write_json

STATE_FILE = "fix/state.json"
STAGES = ("keys", "questions", "notion", "summary")
STEPS = ("selected", "started", "fixed", "checked", "reviewed", "published", "done")
OUTCOMES = ("pending", "fixed", "skipped", "question", "dropped", "done_before")
MODES = ("autonomous", "present")
PUSHES = ("mr", "local", "dry_run")
WORKTREE_DIRS = "fix/[!_]*/*/wt"
HISTORY_DIR = "fix/_history"
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class StateError(ValueError):
    """A fix/state.json that cannot be read as a fix pass."""


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
            or not _SLUG.fullmatch(str(key.get("slug")))
            or not _branch_name(key.get("branch"))
        ):
            raise StateError(f"{STATE_FILE} : la clé « {name} » est invalide.")
    return state


def _branch_name(branch) -> bool:
    if branch is None:
        return True
    return isinstance(branch, str) and any(
        branch.startswith(prefix) and _SLUG.fullmatch(branch[len(prefix):])
        for prefix in (fix_names.BRANCH_PREFIX, fix_names.DRY_RUN_PREFIX)
    )


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


def new_state(candidates: dict, *, mode: str, push: str, notion: bool) -> dict:
    """Return the state of a new pass over the keys of fix/candidates.json."""
    keys = {}
    for entry in candidates.get("keys") or []:
        rows = {
            str(row["ref"]): {"outcome": "pending", "reason": None, "commits": [], "pass": 0,
                              "attention": None}
            for row in entry.get("rows") or []
        }
        keys[entry["key"]] = {
            "repo": entry["repo"],
            "category": entry.get("category"),
            "slug": entry["slug"],
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
            "rows": rows,
        }
    return {
        "started_at": now(),
        "finished_at": None,
        "stage": "keys",
        "mode": mode,
        "push": push,
        "notion": bool(notion) and push != "dry_run",
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
