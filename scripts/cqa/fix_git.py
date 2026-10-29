"""Branches and worktrees of the fix mode, made in the dev's repo without touching their checkout.

Every command goes through fix_names.git (hooks of the repo disabled). Nothing here checks
out, resets, stashes or deletes anything in the dev's own working tree, and no branch gets
an upstream: a new branch is created with --no-track. Only drop_commits rewrites a branch,
inside the key's own worktree, and never a commit that a remote already has.
"""
import re
from pathlib import Path
from typing import Optional

from cqa import fix_gitlab, fix_names
from cqa.fix_names import git_out

CHECKED_OUT = (
    "La branche {branch} est extraite dans votre dossier : changez de branche, puis relancez."
)
DIVERGED = "La branche distante a divergé : intégrez ses commits à la main, rien n'a été poussé."
_MAX_SUFFIX = 1000
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class GitRefused(Exception):
    """A git failure, or a git situation to sort out first, worded for the lead (exit 2).

    A change that a guard refuses raises Refused instead (exit 5).
    """


class Refused(Exception):
    """A change of a fix branch that a guard refuses, worded for the lead (exit 5).

    A git failure, or a situation the lead sorts out first, raises GitRefused (exit 2).
    """


def open_new(path, run, *, slug: str, flight: dict, push: str, base: str, worktree: Path) -> dict:
    """First start of a key: take over or create its branch, then add its worktree.

    A real pass takes over the branch of an open MR of the category, else an unmerged branch
    without MR, else creates the first free name from origin/<base>. A dry run always creates
    a dry-run/ branch. Returns branch, start_sha, reused (None, "mr" or "branch"), mr and
    taken_head (the head of a taken-over branch, None for a new one).

    start_sha is the head of the taken-over branch, except for a branch without MR that origin
    does not have in a pass that pushes: its merge-base with origin/<base>, so that the guard
    also judges the commits that were never pushed.
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
        return {"branch": branch, "start_sha": base_sha, "reused": None, "mr": None,
                "taken_head": None}
    reused, branch, mr = choice
    start = None
    if push == "mr" and reused == "branch" and not remote_has(path, run, branch):
        local = rev(path, run, f"refs/heads/{branch}")
        if local is not None:
            start = _merge_base(path, run, local, base_sha, branch, base)
    head = _checkout(path, run, branch, push, worktree, first=True)
    return {"branch": branch, "start_sha": start or head, "reused": reused, "mr": mr,
            "taken_head": head}


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
    suffix. The branch of a merged or closed MR is never taken over, and no branch is when
    GitLab could not be read (it may belong to a closed or squash-merged MR).
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
    if fix_gitlab.NO_API_NOTE in ((flight or {}).get("notes") or []):
        return None
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


def remote_has(path, run, branch: str) -> bool:
    """Return True when origin has the branch (asked to origin, not read from stale refs)."""
    ref = f"refs/heads/{branch}"
    listing = fix_names.git(path, run, "ls-remote", "--heads", "origin", ref)
    if listing.returncode != 0:
        raise GitRefused(f"git ls-remote origin a échoué dans {path} : {listing.stderr.strip()}")
    return any(line.split("\t", 1)[-1] == ref for line in listing.stdout.splitlines())


def _merge_base(path, run, local: str, base_sha: str, branch: str, base: str) -> str:
    found = git_out(path, run, "merge-base", local, base_sha)
    if not is_sha(found):
        raise GitRefused(f"La branche {branch} n'a aucun commit commun avec origin/{base}.")
    return found


def fetch(path, run, *refspecs: str) -> bool:
    """Fetch origin (or only the given refspecs); return True on success."""
    return fix_names.git(path, run, "fetch", "--quiet", "origin", *refspecs).returncode == 0


def rev(path, run, ref: str) -> Optional[str]:
    """Return the commit SHA of ref, or None when it does not exist."""
    return git_out(path, run, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def is_ancestor(path, run, ancestor: str, descendant: str) -> bool:
    """Return True when ancestor is descendant or one of its ancestors."""
    found = fix_names.git(path, run, "merge-base", "--is-ancestor", ancestor, descendant)
    return found.returncode == 0


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


def is_sha(text) -> bool:
    """Return True for a full commit SHA (40 or 64 lowercase hex digits)."""
    return isinstance(text, str) and _SHA.fullmatch(text) is not None


def commits_in_range(path, run, start_sha: str) -> list:
    """Return the commits of <start_sha>..HEAD, parents before children.

    Each is {"sha", "parents", "message", "ref"}: ref is the checklist ref of the first line
    of the message, or None.
    """
    log = fix_names.git(
        path, run, "log", "-z", "--reverse", "--topo-order", "--no-show-signature",
        "--format=%H%x00%P%x00%B", f"{start_sha}..HEAD",
    )
    if log.returncode != 0:
        raise GitRefused(
            f"git log {start_sha[:12]}..HEAD a échoué dans {path} : {log.stderr.strip()}"
        )
    fields = log.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    commits = []
    for index in range(0, len(fields), 3):
        record = fields[index:index + 3]
        if len(record) != 3 or not is_sha(record[0]):
            raise GitRefused(f"git log {start_sha[:12]}..HEAD : sortie illisible dans {path}.")
        sha, parents, message = record
        commits.append({
            "sha": sha,
            "parents": parents.split(),
            "message": message,
            "ref": fix_names.ref_of_subject(message.split("\n", 1)[0]),
        })
    return commits


def drop_commits(worktree: Path, run, *, branch: str, start_sha: str, refs: set, linked: dict,
                 allowed: set) -> dict:
    """Rebuild the branch of worktree without the commits of refs, from <start_sha>..HEAD.

    Left out: the commits whose subject carries a dropped ref, then the rows whose recorded
    commits (linked: ref -> SHAs) include a left-out commit, until nothing changes. Without
    any left-out commit, nothing is checked nor changed in git. Otherwise the commits kept
    after the first left-out one are replayed by cherry-pick on a detached HEAD; a commit
    that conflicts, or becomes empty, drops its ref as well (when it is in allowed) and the
    replay starts again. The branch then moves to the new head, only if it has not moved
    meanwhile.

    Returns {"dropped": refs dropped (refs included), "rewritten": {old SHA: new SHA},
    "unattached": None, or why the worktree could not go back on the moved branch}.
    Raises Refused for a rewritten history or a merge in the range, uncommitted changes, a
    left-out commit already on a remote, or a commit without an allowed ref that does not
    replay; GitRefused for any other git failure. In both cases the branch is unchanged and
    the worktree is back on it.
    """
    commits = commits_in_range(worktree, run, start_sha)
    dropped = set(refs)
    removed = _left_out(commits, dropped, linked)
    if not removed:
        return {"dropped": dropped, "rewritten": {}, "unattached": None}
    if not is_ancestor(worktree, run, start_sha, "HEAD"):
        raise Refused(
            f"La branche {branch} ne contient plus le départ de la passe ({start_sha[:12]}) : "
            "son historique a été réécrit, rien n'a changé."
        )
    merge = next((commit for commit in commits if len(commit["parents"]) > 1), None)
    if merge is not None:
        raise Refused(
            f"La branche {branch} contient le commit de fusion {merge['sha'][:12]} depuis le "
            "départ de la passe : retirez-le à la main, rien n'a changé."
        )
    status = fix_names.git(worktree, run, "status", "--porcelain=v1", "-uall")
    if status.returncode != 0 or status.stdout.strip():
        raise Refused(
            f"Le worktree {worktree} a des modifications non commitées : commitez-les ou "
            "retirez-les, puis relancez. Rien n'a changé."
        )
    old_head = _head(worktree, run)
    try:
        rewritten = _rebuild(worktree, run, branch, commits, dropped, linked, allowed)
        moved = fix_names.git(
            worktree, run, "update-ref", "-m", "cqa_fix drop", f"refs/heads/{branch}",
            _head(worktree, run), old_head,
        )
        if moved.returncode != 0:
            raise Refused(
                f"La branche {branch} a bougé pendant le retrait : rien n'a changé, relancez."
            )
    except Exception:
        _restore(worktree, run, branch)
        raise
    attached = fix_names.git(worktree, run, "checkout", "-q", branch)
    unattached = None
    if attached.returncode != 0:
        unattached = (
            f"La branche {branch} est reconstruite, mais le worktree {worktree} n'a pas pu y "
            f"revenir : {attached.stderr.strip()}. Lancez git checkout {branch} dans ce "
            "worktree, puis relancez."
        )
    return {"dropped": dropped, "rewritten": rewritten, "unattached": unattached}


def _checkout(path, run, branch: str, push: str, worktree: Path, *, first: bool) -> str:
    """Add the worktree on an existing branch and return its head.

    With push mr the local branch first catches up with origin: created from it when absent,
    fast-forwarded when behind. Local commits that origin lacks stop the first start; on a
    later start they are the pass's own unpublished commits and are kept. A remote that moved
    too (not contained in the local branch) stops any start: the branches diverged.
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
        if is_ancestor(path, run, local, remote):
            moved = fix_names.git(path, run, "branch", "--no-track", "-f", branch, remote)
            if moved.returncode != 0:
                raise GitRefused(f"La branche {branch} n'a pas pu avancer : {moved.stderr.strip()}")
            local = remote
        elif not is_ancestor(path, run, remote, local):
            raise GitRefused(_ahead_message(path, run, branch, remote, local, diverged=True))
        elif first:
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


def _ahead_message(path, run, branch: str, remote: str, local: str, *, diverged=False) -> str:
    log = fix_names.git(path, run, "log", "--format=%h %s", f"{remote}..{local}")
    commits = "; ".join(line for line in log.stdout.splitlines() if line.strip())
    if diverged:
        return f"{DIVERGED} Branche {branch}, commits locaux absents de origin/{branch} : {commits}."
    return (
        f"La branche locale {branch} porte des commits absents de origin/{branch} : {commits}. "
        "Poussez-les ou retirez-les vous-même, puis relancez."
    )


def _add_worktree(path, run, worktree: Path, *args: str) -> None:
    result = fix_names.git(path, run, "worktree", "add", *args)
    if result.returncode != 0:
        raise GitRefused(f"git worktree add a échoué pour {worktree} : {result.stderr.strip()}")


def _left_out(commits: list, dropped: set, linked: dict) -> set:
    """Grow dropped with the rows whose recorded commits are left out; return the left-out SHAs."""
    while True:
        removed = {commit["sha"] for commit in commits if commit["ref"] in dropped}
        more = {
            ref for ref, shas in linked.items()
            if ref not in dropped and removed.intersection(shas or ())
        }
        if not more:
            return removed
        dropped.update(more)


def _remote_with(worktree: Path, run, branch: str, sha: str) -> Optional[str]:
    """Return a remote branch that has the commit, or None.

    The rebuild rewrites every commit from the first left-out one, and a remote that has a
    commit has all its ancestors, so checking that first commit is enough. The remote branch
    is fetched first when it exists; a failed fetch leaves the known remote refs to decide.
    """
    fetch(worktree, run, f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    found = fix_names.git(
        worktree, run, "for-each-ref", f"--contains={sha}", "--format=%(refname:short)",
        "refs/remotes/",
    )
    if found.returncode != 0:
        raise GitRefused(f"git for-each-ref a échoué dans {worktree} : {found.stderr.strip()}")
    remotes = found.stdout.split()
    return remotes[0] if remotes else None


def _rebuild(worktree: Path, run, branch: str, commits: list, dropped: set, linked: dict,
             allowed: set) -> dict:
    """Replay the kept commits until one pass goes through; return old -> new SHAs.

    Leaves the worktree on a detached HEAD: the caller moves the branch, or restores it.
    """
    removed = _left_out(commits, dropped, linked)
    checked = len(commits)
    while True:
        index = next(i for i, commit in enumerate(commits) if commit["sha"] in removed)
        if index < checked:
            remote = _remote_with(worktree, run, branch, commits[index]["sha"])
            if remote is not None:
                raise Refused(
                    f"Le commit {commits[index]['sha'][:12]} ({_subject(commits[index])}) est "
                    f"déjà sur {remote} : il ne peut plus être retiré de la branche. "
                    "Rien n'a changé."
                )
            checked = index
        kept = [commit for commit in commits[index:] if commit["sha"] not in removed]
        rewritten, failed = _replay(worktree, run, commits[index]["parents"][0], kept)
        if failed is None:
            return rewritten
        if failed["ref"] not in allowed:
            raise Refused(
                f"Le commit {failed['sha'][:12]} ({_subject(failed)}) ne se rejoue pas sans les "
                "commits retirés et n'appartient à aucune ligne retirable : retirez-le à la "
                "main. Rien n'a changé."
            )
        dropped.add(failed["ref"])
        removed = _left_out(commits, dropped, linked)


def _replay(worktree: Path, run, base: str, kept: list) -> tuple:
    """Cherry-pick kept on a detached base; return (old -> new SHAs, commit that conflicted).

    A cherry-pick that conflicts or becomes empty is aborted and returned. Any other failure
    (signing, identity, lock, filter) raises GitRefused with git's message.
    """
    detached = fix_names.git(worktree, run, "checkout", "-q", "--detach", base)
    if detached.returncode != 0:
        raise GitRefused(
            f"git checkout --detach a échoué dans {worktree} : {detached.stderr.strip()}"
        )
    rewritten = {}
    for commit in kept:
        picked = fix_names.git(
            worktree, run, "-c", "rerere.enabled=false", "cherry-pick", "--allow-empty",
            commit["sha"],
        )
        if picked.returncode != 0:
            conflict = _conflicted(worktree, run)
            fix_names.git(worktree, run, "cherry-pick", "--abort")
            if not conflict:
                raise GitRefused(
                    f"git cherry-pick {commit['sha'][:12]} a échoué dans {worktree} : "
                    f"{picked.stderr.strip()}"
                )
            return rewritten, commit
        rewritten[commit["sha"]] = rev(worktree, run, "HEAD")
    return rewritten, None


def _conflicted(worktree: Path, run) -> bool:
    """True when a stopped cherry-pick left unmerged paths, or nothing to commit."""
    unmerged = fix_names.git(worktree, run, "ls-files", "-u")
    if unmerged.returncode != 0:
        return False
    if unmerged.stdout.strip():
        return True
    if rev(worktree, run, "CHERRY_PICK_HEAD") is None:
        return False
    return fix_names.git(worktree, run, "diff", "--cached", "--quiet", "HEAD").returncode == 0


def _restore(worktree: Path, run, branch: str) -> None:
    """Put the worktree back on its branch, which a failed rebuild has not moved."""
    fix_names.git(worktree, run, "cherry-pick", "--abort")
    fix_names.git(worktree, run, "checkout", "-q", "--force", branch)


def _head(worktree: Path, run) -> str:
    head = rev(worktree, run, "HEAD")
    if head is None:
        raise GitRefused(f"git rev-parse HEAD a échoué dans {worktree}.")
    return head


def _subject(commit: dict) -> str:
    return commit["message"].split("\n", 1)[0]


def _entries(flight: dict, name: str) -> list:
    return [entry for entry in (flight or {}).get(name) or [] if isinstance(entry, dict)]


def _mr_branches(flight: dict, names: tuple) -> set:
    return {
        entry["branch"] for name in names for entry in _entries(flight, name)
        if isinstance(entry.get("branch"), str)
    }


def _same(first, second) -> bool:
    return Path(first).resolve() == Path(second).resolve()
