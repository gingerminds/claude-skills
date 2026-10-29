"""Branches and worktrees of the fix mode, made in the dev's repo without touching their checkout.

Every command goes through fix_names.git (hooks of the repo disabled). Nothing here checks
out, resets, stashes or deletes anything in the dev's own working tree, and no branch gets
an upstream: a new branch is created with --no-track.
"""
from pathlib import Path
from typing import Optional

from cqa import fix_names
from cqa.fix_state import git_out

CHECKED_OUT = (
    "La branche {branch} est extraite dans votre dossier : changez de branche, puis relancez."
)
_MAX_SUFFIX = 1000


class GitRefused(Exception):
    """A git situation the fix mode does not change on its own, worded for the lead."""


def open_new(path, run, *, slug: str, flight: dict, push: str, base: str, worktree: Path) -> dict:
    """First start of a key: take over or create its branch, then add its worktree.

    A real pass takes over the branch of an open MR of the category, else an unmerged branch
    without MR, else creates the first free name from origin/<base>. A dry run always creates
    a dry-run/ branch. Returns branch, start_sha, reused (None, "mr" or "branch") and mr.
    """
    if not fetch(path, run):
        raise GitRefused(
            f"git fetch origin a échoué dans {path} : vérifiez l'accès au dépôt, puis relancez."
        )
    base_sha = rev(path, run, f"refs/remotes/origin/{base}")
    if base_sha is None:
        raise GitRefused(f"origin/{base} est introuvable dans {path}.")
    choice = None if push == "dry_run" else reusable(flight, slug)
    if choice is None:
        prefix = fix_names.DRY_RUN_PREFIX if push == "dry_run" else fix_names.BRANCH_PREFIX
        branch = free_branch(path, run, prefix, slug, flight)
        _add_worktree(path, run, worktree, "--no-track", "-b", branch, str(worktree), base_sha)
        return {"branch": branch, "start_sha": base_sha, "reused": None, "mr": None}
    reused, branch, mr = choice
    head = _checkout(path, run, branch, push, worktree, first=True)
    return {"branch": branch, "start_sha": head, "reused": reused, "mr": mr}


def reopen(path, run, *, branch: str, push: str, worktree: Path) -> None:
    """Later start of a key: keep its worktree, or add it again on the same branch."""
    worktree = Path(worktree)
    if worktree.exists():
        entry = next((e for e in worktrees(path, run) if _same(e["path"], worktree)), None)
        if entry is None:
            raise GitRefused(
                f"Le dossier {worktree} n'est pas un worktree de {path} : retirez-le, puis relancez."
            )
        if entry["branch"] != branch:
            raise GitRefused(
                f"Le worktree {worktree} n'est plus sur la branche {branch} : "
                "remettez-l'y ou retirez-le (git worktree remove), puis relancez."
            )
        return
    _checkout(path, run, branch, push, worktree, first=False)


def reusable(flight: dict, slug: str) -> Optional[tuple]:
    """Return (reused, branch, mr) for the branch a real pass takes over, or None.

    An open MR wins over a branch without MR; the unsuffixed name wins, then the lowest
    suffix. The branch of a merged or closed MR is never taken over.
    """
    prefix = fix_names.BRANCH_PREFIX

    def fits(branch) -> bool:
        return (isinstance(branch, str) and branch not in barred
                and fix_names.branch_matches(branch, prefix, slug))

    def order(branch: str) -> int:
        rest = branch[len(prefix + slug):]
        return int(rest[1:]) if rest else 0

    barred = _mr_branches(flight, ("merged", "closed"))
    mrs = [mr for mr in _entries(flight, "open") if fits(mr.get("branch"))]
    if mrs:
        mr = min(mrs, key=lambda entry: order(entry["branch"]))
        return "mr", mr["branch"], {"iid": mr.get("iid"), "url": mr.get("url")}
    branches = [
        entry["branch"] for entry in _entries(flight, "branches") if fits(entry.get("branch"))
    ]
    if branches:
        return "branch", min(branches, key=order), None
    return None


def free_branch(path, run, prefix: str, slug: str, flight: dict) -> str:
    """Return the first of <prefix><slug>, <prefix><slug>-2... that no branch or MR uses."""
    taken = _mr_branches(flight, ("open", "closed", "merged"))
    for ref in ("refs/heads/", "refs/remotes/origin/"):
        listing = fix_names.git(path, run, "for-each-ref", "--format=%(refname)", ref)
        taken.update(line[len(ref):] for line in listing.stdout.splitlines())
    remote = fix_names.git(path, run, "ls-remote", "--heads", "origin")
    if remote.returncode != 0:
        raise GitRefused(f"git ls-remote origin a échoué dans {path} : {remote.stderr.strip()}")
    for line in remote.stdout.splitlines():
        ref = line.split("\t", 1)[-1]
        if ref.startswith("refs/heads/"):
            taken.add(ref[len("refs/heads/"):])
    for number in range(1, _MAX_SUFFIX):
        name = prefix + slug if number == 1 else f"{prefix}{slug}-{number}"
        if name not in taken:
            return name
    raise GitRefused(f"Aucun nom de branche libre pour {prefix}{slug}.")


def fetch(path, run, *refspecs: str) -> bool:
    """Fetch origin (or only the given refspecs); return True on success."""
    return fix_names.git(path, run, "fetch", "--quiet", "origin", *refspecs).returncode == 0


def rev(path, run, ref: str) -> Optional[str]:
    """Return the commit SHA of ref, or None when it does not exist."""
    return git_out(path, run, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def worktrees(path, run) -> list:
    """Return the worktrees of the repo, main one first: [{"path", "branch"}]."""
    result = fix_names.git(path, run, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        raise GitRefused(f"git worktree list a échoué dans {path} : {result.stderr.strip()}")
    found = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            found.append({"path": Path(line[len("worktree "):]), "branch": None})
        elif line.startswith("branch refs/heads/") and found:
            found[-1]["branch"] = line[len("branch refs/heads/"):]
    return found


def remove_worktree(path, run, worktree: Path) -> bool:
    """Remove a worktree of the fix mode (its uncommitted changes are lost); False when absent."""
    if not Path(worktree).exists():
        return False
    result = fix_names.git(path, run, "worktree", "remove", "--force", str(worktree))
    if result.returncode != 0:
        raise GitRefused(f"Le worktree {worktree} n'a pas pu être retiré : {result.stderr.strip()}")
    return True


def unpushed(path, run, branch: str) -> int:
    """Return how many commits of the local branch no origin branch has (0 when absent)."""
    if rev(path, run, f"refs/heads/{branch}") is None:
        return 0
    count = git_out(
        path, run, "rev-list", "--count", f"refs/heads/{branch}", "--not", "--remotes=origin"
    )
    return int(count) if count and count.isdigit() else 0


def refs_between(path, run, base_ref: str, tip: str) -> list:
    """Return the sorted checklist refs of the commit subjects of base_ref..tip."""
    if not tip or rev(path, run, base_ref) is None:
        return []
    log = fix_names.git(path, run, "log", "--format=%s", f"{base_ref}..{tip}")
    subjects = log.stdout.splitlines() if log.returncode == 0 else []
    refs = {fix_names.ref_of_subject(subject) for subject in subjects}
    return sorted(ref for ref in refs if ref is not None)


def _checkout(path, run, branch: str, push: str, worktree: Path, *, first: bool) -> str:
    """Add the worktree on an existing branch and return its head.

    With push mr the local branch first catches up with origin: created from it when absent,
    fast-forwarded when behind. Local commits that origin lacks stop the first start; on a
    later start they are the pass's own unpublished commits and are kept.
    """
    _refuse_if_checked_out(path, run, branch, worktree)
    remote = None
    if push == "mr" and fetch(path, run, f"+refs/heads/{branch}:refs/remotes/origin/{branch}"):
        remote = rev(path, run, f"refs/remotes/origin/{branch}")
    local = rev(path, run, f"refs/heads/{branch}")
    if local is None:
        source = remote if push == "mr" else rev(path, run, f"refs/remotes/origin/{branch}")
        if source is None:
            raise GitRefused(f"La branche {branch} est introuvable, ni en local ni sur origin.")
        _add_worktree(path, run, worktree, "--no-track", "-b", branch, str(worktree), source)
        return source
    if remote is not None and remote != local:
        if _is_ancestor(path, run, local, remote):
            moved = fix_names.git(path, run, "branch", "--no-track", "-f", branch, remote)
            if moved.returncode != 0:
                raise GitRefused(f"La branche {branch} n'a pas pu avancer : {moved.stderr.strip()}")
            local = remote
        elif first or not _is_ancestor(path, run, remote, local):
            raise GitRefused(_ahead_message(path, run, branch, remote, local))
    _add_worktree(path, run, worktree, str(worktree), branch)
    return local


def _refuse_if_checked_out(path, run, branch: str, worktree: Path) -> None:
    for index, entry in enumerate(worktrees(path, run)):
        if entry["branch"] != branch or _same(entry["path"], worktree):
            continue
        if index == 0 or _same(entry["path"], path):
            raise GitRefused(CHECKED_OUT.format(branch=branch))
        if not Path(entry["path"]).exists():
            raise GitRefused(
                f"La branche {branch} est encore liée au worktree disparu {entry['path']} : "
                "lancez git worktree prune dans votre repo, puis relancez."
            )
        raise GitRefused(
            f"La branche {branch} est extraite dans {entry['path']} : "
            "retirez ce worktree ou changez-y de branche, puis relancez."
        )


def _ahead_message(path, run, branch: str, remote: str, local: str) -> str:
    log = fix_names.git(path, run, "log", "--format=%h %s", f"{remote}..{local}")
    commits = "; ".join(line for line in log.stdout.splitlines() if line.strip())
    return (
        f"La branche locale {branch} porte des commits absents de origin/{branch} : {commits}. "
        "Poussez-les ou retirez-les vous-même, puis relancez."
    )


def _add_worktree(path, run, worktree: Path, *args: str) -> None:
    result = fix_names.git(path, run, "worktree", "add", *args)
    if result.returncode != 0:
        raise GitRefused(f"git worktree add a échoué pour {worktree} : {result.stderr.strip()}")


def _is_ancestor(path, run, ancestor: str, descendant: str) -> bool:
    return fix_names.git(path, run, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _entries(flight: dict, name: str) -> list:
    return [entry for entry in (flight or {}).get(name) or [] if isinstance(entry, dict)]


def _mr_branches(flight: dict, names: tuple) -> set:
    return {
        entry["branch"] for name in names for entry in _entries(flight, name)
        if isinstance(entry.get("branch"), str)
    }


def _same(first, second) -> bool:
    return Path(first).resolve() == Path(second).resolve()
