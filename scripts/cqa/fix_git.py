"""Branches and worktrees of the fix mode, made in the dev's repo without touching their checkout.

Every command goes through fix_names.git (hooks of the repo disabled). Nothing here checks
out, resets, stashes or deletes anything in the dev's own working tree, and no branch gets
an upstream: a new branch is created with --no-track. Only drop_commits, reorder_commits and
reword_commit rewrite a branch, and save_leftovers resets a worktree, always inside the key's
own worktree, and never a commit that a remote already has. They all replay commits with
replay: a conflict limited to documentation files is settled by re-applying the lines the
replayed commit adds, never those of a removed commit.
"""
import bisect
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from cqa import fix_gitlab, fix_names
from cqa.fix_names import git_out

CHECKED_OUT = (
    "La branche {branch} est extraite dans votre dossier : changez de branche, puis relancez."
)
DIVERGED = "La branche distante a divergé : intégrez ses commits à la main, rien n'a été poussé."
_MAX_SUFFIX = 1000
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# The merge a replay runs for every path, whatever merge driver the repo's attributes or
# config name: git's text merge, through git merge-file, whose conflict stops the cherry-pick.
_TEXT_MERGE = "git merge-file -q --marker-size=%L %A %O %B"


class GitRefused(Exception):
    """A git failure, or a git situation to sort out first, worded for the lead (exit 2).

    A change that a guard refuses raises Refused instead (exit 5).
    """


class Refused(Exception):
    """A change of a fix branch that a guard refuses, worded for the lead (exit 5).

    A git failure, or a situation the lead sorts out first, raises GitRefused (exit 2).
    """


def open_new(path, run, *, flight: dict, push: str, base: str, worktree: Path) -> dict:
    """First start of a key: take over or create the pass branch of the repo, then add its
    worktree.

    A real pass takes over the pass branch of an open MR, else an unmerged pass branch without
    MR, else creates the first free name from origin/<base> (see reusable and free_branch). A
    dry run always creates a dry-run/ branch. Returns branch, start_sha, reused (None, "mr" or
    "branch"), mr and taken_head (the head of a taken-over branch, None for a new one).

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
    choice = None if push == "dry_run" else reusable(flight)
    if choice is None:
        name = fix_names.DRY_RUN_BASE if push == "dry_run" else fix_names.BRANCH_BASE
        branch = free_branch(path, run, name, flight)
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


def reusable(flight: dict) -> Optional[tuple]:
    """Return (reused, branch, mr) for the pass branch a real pass takes over, or None.

    Only a pass branch (fix_names.is_pass_branch) is taken over, never a v2 per-category one.
    An open MR wins over a branch without MR; the unsuffixed name wins, then the lowest
    suffix. The branch of a merged or closed MR is never taken over, and no branch is when
    GitLab could not be read (it may belong to a closed or squash-merged MR).
    """
    base = fix_names.BRANCH_BASE

    def fits(branch) -> bool:
        return (isinstance(branch, str) and branch not in barred
                and fix_names.is_pass_branch(branch, base))

    def order(branch: str) -> int:
        rest = branch[len(base):]
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


def free_branch(path, run, base: str, flight: dict) -> str:
    """Return the first of <base>, <base>-2... that no branch or MR uses."""
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
        name = base if number == 1 else f"{base}-{number}"
        if name not in taken:
            return name
    raise GitRefused(f"Aucun nom de branche libre pour {base}.")


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
                 allowed: set, extra: frozenset = frozenset()) -> dict:
    """Rebuild the branch of worktree without the commits of refs, from <start_sha>..HEAD.

    Left out: the commits whose subject carries a dropped ref, those of extra (SHAs), then
    the rows whose recorded commits (linked: ref -> SHAs) include a left-out commit, until
    nothing changes. Without any left-out commit, nothing is checked nor changed in git.
    Otherwise the commits kept after the first left-out one are replayed (replay) on a
    detached HEAD; a commit that conflicts, or becomes empty, drops its ref as well (when it
    is in allowed) and the replay starts again. The branch then moves to the new head, only
    if it has not moved meanwhile.

    Returns {"dropped": refs dropped (refs included), "rewritten": {old SHA: new SHA},
    "unattached": None, or why the worktree could not go back on the moved branch}.
    Raises Refused for a rewritten history or a merge in the range, uncommitted changes, a
    left-out commit already on a remote, or a commit without an allowed ref that does not
    replay; GitRefused for any other git failure. In both cases the branch is unchanged and
    the worktree is back on it.
    """
    commits = commits_in_range(worktree, run, start_sha)
    dropped = set(refs)
    removed = _left_out(commits, dropped, linked, extra)
    if not removed:
        return {"dropped": dropped, "rewritten": {}, "unattached": None}
    _linear(worktree, run, branch, start_sha, commits)
    _clean(worktree, run)
    old_head = _head(worktree, run)
    try:
        rewritten = _rebuild(worktree, run, branch, commits, dropped, linked, allowed, extra)
        _move(worktree, run, branch, old_head, "drop", "le retrait")
    except BaseException:
        _restore(worktree, run, branch)
        raise
    return {"dropped": dropped, "rewritten": rewritten,
            "unattached": _attach(worktree, run, branch)}


def check_drop(worktree: Path, run, *, branch: str, start_sha: str, refs: set, linked: dict,
               extra: frozenset = frozenset()) -> None:
    """Raise Refused, before anything changes, for what drop_commits would refuse in the
    history itself: a rewritten history or a merge in the range, or a first left-out commit
    already on a remote (later ones are checked by drop_commits as they come)."""
    commits = commits_in_range(worktree, run, start_sha)
    removed = _left_out(commits, set(refs), linked, extra)
    if not removed:
        return
    _linear(worktree, run, branch, start_sha, commits)
    _refuse_pushed(worktree, run, branch, next(c for c in commits if c["sha"] in removed))


def reorder_commits(worktree: Path, run, *, branch: str, start_sha: str, keep: Optional[str],
                    rank: Callable) -> dict:
    """Replay the commits of <start_sha>..HEAD that may move in the order of their rank.

    Never move: the commits up to keep (the head of a taken-over branch, None for none) and
    every commit a remote branch has (origin/<branch> fetched first), which form the start
    of the range. rank(commit) gives the place of a movable commit (its section), None for
    the place of the commit before it; commits of one rank keep their order. Nothing to
    move: no change and no check of the worktree. A conflict, or a branch whose content
    would change (its tree must stay the same), leaves the branch as it was.

    Returns {"reordered", "conflicts": [the SHA that does not replay], "rewritten": {old:
    new}, "unattached"}. Raises Refused for a rewritten history, a merge in the range or
    uncommitted changes, GitRefused for any other git failure; the branch is then unchanged
    and the worktree back on it.
    """
    commits = commits_in_range(worktree, run, start_sha)
    _linear(worktree, run, branch, start_sha, commits)
    fixed = _unmoved(worktree, run, branch, start_sha, commits, keep)
    movable = commits[fixed:]
    ranks, last = [], 0
    for commit in movable:
        found = rank(commit)
        last = last if found is None else found
        ranks.append(last)
    order = [movable[index] for index in sorted(range(len(movable)),
                                                key=lambda index: (ranks[index], index))]
    same = 0
    while same < len(movable) and movable[same] is order[same]:
        same += 1
    unchanged = {"reordered": False, "conflicts": [], "rewritten": {}, "unattached": None}
    if same == len(movable):
        return unchanged
    _clean(worktree, run)
    rest = order[same:]
    old_head = _head(worktree, run)
    old_tree = _tree(worktree, run, old_head)
    try:
        result = replay(worktree, run, movable[same]["parents"][0],
                        [(commit["sha"], None) for commit in rest])
        conflicts = result["conflicts"]
        if not conflicts and _tree(worktree, run, "HEAD") != old_tree:
            conflicts = (result["resolved"] or [rest[-1]["sha"]])[:1]
        if conflicts:
            _restore(worktree, run, branch)
            return {**unchanged, "conflicts": conflicts}
        _move(worktree, run, branch, old_head, "reorder", "le rangement")
    except BaseException:
        _restore(worktree, run, branch)
        raise
    return {"reordered": True, "conflicts": [], "rewritten": result["mapping"],
            "unattached": _attach(worktree, run, branch)}


def reword_commit(worktree: Path, run, *, branch: str, start_sha: str, sha: str,
                  message: str, keep: Optional[str] = None) -> dict:
    """Give commit sha of <start_sha>..HEAD the message message, then replay the commits after
    it: the content of every commit stays the same.

    Returns {"commit": the new SHA (sha itself when the message is already that one),
    "rewritten": {old: new}, "unattached"}. Raises GitRefused for a commit outside the range
    or any git failure, Refused for a commit at or before keep (the head of a taken-over
    branch, as for reorder_commits), a rewritten history, a merge in the range, uncommitted
    changes or a commit already on a remote; the branch is then unchanged and the worktree
    back on it.
    """
    commits = commits_in_range(worktree, run, start_sha)
    index = next((i for i, commit in enumerate(commits) if commit["sha"] == sha), None)
    if index is None:
        raise GitRefused(f"Le commit {sha[:12]} n'est pas dans {start_sha[:12]}..HEAD de la "
                         f"branche {branch} : rien n'a changé.")
    kept = next((i for i, commit in enumerate(commits) if commit["sha"] == keep), None)
    if kept is not None and index <= kept:
        raise Refused(f"Le commit {sha[:12]} ({_subject(commits[index])}) vient de la branche "
                      "reprise, avant cette passe : son message ne change pas. Rien n'a changé.")
    if commits[index]["message"].strip("\n") == message.strip("\n"):
        return {"commit": sha, "rewritten": {}, "unattached": None}
    _linear(worktree, run, branch, start_sha, commits)
    _clean(worktree, run)
    _refuse_pushed(worktree, run, branch, commits[index], "son message ne peut plus être réécrit")
    old_head = _head(worktree, run)
    old_tree = _tree(worktree, run, old_head)
    try:
        result = replay(worktree, run, commits[index]["parents"][0],
                        [(sha, message)] + [(commit["sha"], None) for commit in commits[index + 1:]])
        if result["conflicts"] or _tree(worktree, run, "HEAD") != old_tree:
            raise GitRefused(f"Le commit {sha[:12]} ne se rejoue pas à l'identique avec son "
                             "nouveau message : rien n'a changé.")
        _move(worktree, run, branch, old_head, "reword", "la réécriture")
    except BaseException:
        _restore(worktree, run, branch)
        raise
    return {"commit": result["mapping"][sha], "rewritten": result["mapping"],
            "unattached": _attach(worktree, run, branch)}


def save_leftovers(worktree: Path, run, folder: Path) -> Optional[dict]:
    """Save the uncommitted changes of worktree in folder/leftover-<N>.patch (the first free
    N), then reset the worktree to HEAD and clean it; None when it has none.

    The patch is binary-safe (git diff --binary) against HEAD, untracked files included;
    ignored files are neither saved nor removed. Returns {"path", "base": HEAD the patch
    applies to, "untracked": the untracked paths it holds}. Raises GitRefused when a git
    command fails (the worktree is reset only once the patch is written) and Refused when
    the worktree is still not clean afterwards.
    """
    entries = _status(worktree, run)
    if not entries:
        return None
    untracked = sorted(path for path, code in entries if code == "??")
    base = _head(worktree, run)
    _run_ok(worktree, run, "git add -A", "add", "-A")
    diff = fix_names.git(
        worktree, run, "diff", "--cached", "--binary", "--no-color", "--no-ext-diff",
        "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", "HEAD",
        binary=True,
    )
    if diff.returncode != 0:
        raise GitRefused(f"git diff a échoué dans {worktree} : "
                         f"{diff.stderr.decode('utf-8', 'replace').strip()}")
    path = _write_free(Path(folder), "leftover-{}.patch", diff.stdout)
    _run_ok(worktree, run, "git reset --hard", "reset", "-q", "--hard", "HEAD")
    _run_ok(worktree, run, "git clean", "clean", "-fdq")
    if _status(worktree, run):
        raise Refused(f"Le worktree {worktree} n'a pas pu être remis propre : retirez ses "
                      f"modifications à la main (elles sont sauvées dans {path}), puis relancez.")
    return {"path": path, "base": base, "untracked": untracked}


def replay(worktree: Path, run, onto: str, commits: list) -> dict:
    """Cherry-pick commits [(sha, message or None)] in that order on a detached onto.

    A message replaces the one of its commit (git commit --amend, author kept). A conflict
    limited to documentation files (fix_names.is_documentation) is settled by re-applying,
    on the current version of each file, the lines that the commit adds against its own
    parent, hunk by hunk at the place its neighbours give (_place_additions); a place that is
    not sure is a real conflict. Returns {"mapping": {old: new}, "conflicts": [the first
    commit that does not replay, or becomes empty], "resolved": [commits settled that way]};
    nothing is replayed after a conflict. The worktree ends clean, detached on the last
    replayed commit (the caller moves the branch or restores it). Raises Refused when the
    worktree has uncommitted changes (nothing is done), GitRefused for any other git
    failure (signing, identity, lock, filter), after putting the worktree back clean. Every
    merge is git's text merge (_merge_options): a merge driver of the repo (union, or one of
    its config) never settles a conflict.
    """
    _clean(worktree, run)
    merging = _merge_options(worktree, run)
    detached = fix_names.git(worktree, run, "checkout", "-q", "--detach", onto)
    if detached.returncode != 0:
        raise GitRefused(
            f"git checkout --detach a échoué dans {worktree} : {detached.stderr.strip()}"
        )
    mapping, resolved = {}, []
    try:
        for sha, message in commits:
            picked = fix_names.git(
                worktree, run, "-c", "rerere.enabled=false", *merging, "cherry-pick",
                "--allow-empty", sha,
            )
            if picked.returncode != 0:
                if not _conflicted(worktree, run):
                    raise GitRefused(
                        f"git cherry-pick {sha[:12]} a échoué dans {worktree} : "
                        f"{picked.stderr.strip()}"
                    )
                if not _resolve_documentation(worktree, run, sha):
                    _abort(worktree, run)
                    return {"mapping": mapping, "conflicts": [sha], "resolved": resolved}
                resolved.append(sha)
            if message is not None:
                _run_ok(worktree, run, f"git commit --amend ({sha[:12]})", "commit", "--amend",
                        "-q", "--no-verify", "--allow-empty", "--cleanup=whitespace", "-m",
                        message)
            mapping[sha] = _head(worktree, run)
    except BaseException:
        _abort(worktree, run)
        raise
    return {"mapping": mapping, "conflicts": [], "resolved": resolved}


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


def _left_out(commits: list, dropped: set, linked: dict, extra: frozenset = frozenset()) -> set:
    """Grow dropped with the rows whose recorded commits are left out; return the left-out SHAs
    (those whose subject carries a dropped ref, and those of extra)."""
    while True:
        removed = {commit["sha"] for commit in commits
                   if commit["ref"] in dropped or commit["sha"] in extra}
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
             allowed: set, extra: frozenset = frozenset()) -> dict:
    """Replay the kept commits until one pass goes through; return old -> new SHAs.

    Leaves the worktree on a detached HEAD: the caller moves the branch, or restores it.
    """
    removed = _left_out(commits, dropped, linked, extra)
    checked = len(commits)
    while True:
        index = next(i for i, commit in enumerate(commits) if commit["sha"] in removed)
        if index < checked:
            _refuse_pushed(worktree, run, branch, commits[index])
            checked = index
        kept = [commit for commit in commits[index:] if commit["sha"] not in removed]
        result = replay(worktree, run, commits[index]["parents"][0],
                        [(commit["sha"], None) for commit in kept])
        if not result["conflicts"]:
            return result["mapping"]
        failed = next(commit for commit in kept if commit["sha"] == result["conflicts"][0])
        if failed["ref"] not in allowed:
            raise Refused(
                f"Le commit {failed['sha'][:12]} ({_subject(failed)}) ne se rejoue pas sans les "
                "commits retirés et n'appartient à aucune ligne retirable : retirez-le à la "
                "main. Rien n'a changé."
            )
        dropped.add(failed["ref"])
        removed = _left_out(commits, dropped, linked, extra)


def _merge_options(worktree: Path, run) -> list:
    """Return the -c options that make every merge of a replay git's text merge.

    The built-in union driver and each driver of the config (merge.<name>.driver) run
    _TEXT_MERGE instead, and merge.default is emptied so that a path without a merge
    attribute gets git's text merge: a driver could otherwise settle a conflict by keeping
    the lines of a removed commit. An attribute naming a driver that the config lacks
    already gets git's text merge. The repo's attributes and config are never edited.
    """
    listing = fix_names.git(worktree, run, "config", "--name-only", "--get-regexp",
                            r"^merge\..+\.driver$")
    if listing.returncode not in (0, 1):
        raise GitRefused(f"git config a échoué dans {worktree} : {listing.stderr.strip()}")
    names = {"union"}
    for line in listing.stdout.splitlines() if listing.returncode == 0 else ():
        if line.startswith("merge.") and line.endswith(".driver"):
            names.add(line[len("merge."):-len(".driver")])
    unusable = sorted(name for name in names if not name or "=" in name)
    if unusable:
        raise GitRefused(f"Pilote de fusion au nom inutilisable dans la configuration git "
                         f"({', '.join(unusable)}) : renommez-le, puis relancez. Rien n'a changé.")
    options = ["-c", "merge.default="]
    for name in sorted(names):
        options += ["-c", f"merge.{name}.driver={_TEXT_MERGE}"]
    return options


def _linear(worktree: Path, run, branch: str, start_sha: str, commits: list) -> None:
    """Refuse a range that a replay cannot rebuild: start_sha left the branch, or a merge."""
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


def _clean(worktree: Path, run) -> None:
    """Refuse uncommitted changes in the worktree (untracked files included)."""
    status = fix_names.git(worktree, run, "status", "--porcelain=v1", "-uall")
    if status.returncode != 0 or status.stdout.strip():
        raise Refused(
            f"Le worktree {worktree} a des modifications non commitées : commitez-les ou "
            "retirez-les, puis relancez. Rien n'a changé."
        )


def _refuse_pushed(worktree: Path, run, branch: str, commit: dict,
                   consequence: str = "il ne peut plus être retiré de la branche") -> None:
    """Refuse a rewrite from commit when a remote branch has it (_remote_with)."""
    remote = _remote_with(worktree, run, branch, commit["sha"])
    if remote is not None:
        raise Refused(
            f"Le commit {commit['sha'][:12]} ({_subject(commit)}) est déjà sur {remote} : "
            f"{consequence}. Rien n'a changé."
        )


def _unmoved(worktree: Path, run, branch: str, start_sha: str, commits: list,
             keep: Optional[str]) -> int:
    """Return how many commits at the start of commits never move: those up to keep, and
    every one that a remote branch has (origin/<branch> fetched first; a failed fetch leaves
    the known remote refs to decide)."""
    fetch(worktree, run, f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    listing = fix_names.git(worktree, run, "rev-list", f"{start_sha}..HEAD", "--not", "--remotes")
    if listing.returncode != 0:
        raise GitRefused(f"git rev-list a échoué dans {worktree} : {listing.stderr.strip()}")
    unpushed = set(listing.stdout.split())
    fixed = 0
    for index, commit in enumerate(commits):
        if commit["sha"] not in unpushed or commit["sha"] == keep:
            fixed = index + 1
    return fixed


def _move(worktree: Path, run, branch: str, old_head: str, label: str, what: str) -> None:
    """Move the branch to HEAD, only if it is still at old_head."""
    moved = fix_names.git(
        worktree, run, "update-ref", "-m", f"cqa_fix {label}", f"refs/heads/{branch}",
        _head(worktree, run), old_head,
    )
    if moved.returncode != 0:
        raise Refused(f"La branche {branch} a bougé pendant {what} : rien n'a changé, relancez.")


def _attach(worktree: Path, run, branch: str) -> Optional[str]:
    """Put the worktree back on its moved branch; return why it could not, else None."""
    attached = fix_names.git(worktree, run, "checkout", "-q", branch)
    if attached.returncode == 0:
        return None
    return (
        f"La branche {branch} est reconstruite, mais le worktree {worktree} n'a pas pu y "
        f"revenir : {attached.stderr.strip()}. Lancez git checkout {branch} dans ce "
        "worktree, puis relancez."
    )


def _abort(worktree: Path, run) -> None:
    """Stop a cherry-pick in progress and leave the detached worktree clean, without the
    untracked files a stopped cherry-pick may write (replay starts from a clean worktree)."""
    fix_names.git(worktree, run, "cherry-pick", "--abort")
    fix_names.git(worktree, run, "reset", "-q", "--hard", "HEAD")
    fix_names.git(worktree, run, "clean", "-fdq")


def _resolve_documentation(worktree: Path, run, sha: str) -> bool:
    """Settle a stopped cherry-pick of sha whose conflicts are all in documentation files.

    Each conflicted file gets its current version (HEAD) plus the changes of sha against its
    own parent, placed by _place_additions; then the commit is made with the message and
    author of sha. Returns False, without committing, for a conflict out of documentation, a
    file that is a link or absent on either side, a place that is not sure, or a change that
    became empty. Raises GitRefused when git add or git commit fails.
    """
    listing = fix_names.git(worktree, run, "ls-files", "-u", "-z", binary=True)
    if listing.returncode != 0:
        return False
    modes = {}
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        info, _, raw = record.partition(b"\t")
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False
        modes.setdefault(path, set()).add(info.split(b" ", 1)[0])
    if not modes or any(not fix_names.is_documentation(path)
                        or not found <= {b"100644", b"100755"} for path, found in modes.items()):
        return False
    parent = rev(worktree, run, f"{sha}^1")
    if parent is None:
        return False
    merged = {}
    for path in modes:
        theirs, ours = _blob(worktree, run, sha, path), _blob(worktree, run, "HEAD", path)
        if theirs is None or ours is None:
            return False
        content = _place_additions(_blob(worktree, run, parent, path) or b"", theirs, ours)
        if content is None:
            return False
        merged[path] = content
    for path, content in merged.items():
        (Path(worktree) / path).write_bytes(content)
    _run_ok(worktree, run, "git add", "--literal-pathspecs", "add", "--", *merged)
    still = fix_names.git(worktree, run, "ls-files", "-u")
    if still.returncode != 0 or still.stdout.strip():
        return False
    if fix_names.git(worktree, run, "diff", "--cached", "--quiet", "HEAD").returncode == 0:
        return False
    _run_ok(worktree, run, f"git commit ({sha[:12]})", "commit", "-q", "--no-verify",
            "--cleanup=verbatim", "-C", sha)
    return True


def _place_additions(base: bytes, theirs: bytes, ours: bytes) -> Optional[bytes]:
    """Return ours with the changes of base -> theirs, or None when a place is not sure.

    Lines are aligned between base and ours: a hunk that removes lines needs them all in
    ours, in a row, and is applied there; a hunk that only adds lines goes between the
    nearest lines around it that ours still has, which must be next to each other in ours
    (lines of base that ours lost, such as those of a removed commit, do not count).
    Nothing else of base comes back.
    """
    old, new, current = _lines(base), _lines(theirs), _lines(ours)
    seen = {}
    for block in SequenceMatcher(None, old, current, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            seen[block.a + offset] = block.b + offset
    mapped = sorted(seen)
    edits = []
    for tag, first, last, start, end in SequenceMatcher(
            None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if last > first:
            places = [seen.get(index) for index in range(first, last)]
            if None in places or places != list(range(places[0], places[0] + len(places))):
                return None
            edits.append((places[0], places[0] + len(places), new[start:end]))
            continue
        index = bisect.bisect_left(mapped, first)
        before = seen[mapped[index - 1]] if index > 0 else -1
        after = seen[mapped[index]] if index < len(mapped) else len(current)
        if after != before + 1:
            return None
        edits.append((after, after, new[start:end]))
    for (_, previous_end, _), (following, _, _) in zip(edits, edits[1:]):
        if following < previous_end:
            return None
    for first, last, lines in reversed(edits):
        current[first:last] = lines
    return b"".join(current)


def _lines(data: bytes) -> list:
    """Split on "\\n" only, as git does, each line keeping its end."""
    parts = data.split(b"\n")
    return [part + b"\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _blob(worktree: Path, run, revision: str, path: str) -> Optional[bytes]:
    """Return the content of path in revision, None when it has none."""
    found = fix_names.git(worktree, run, "cat-file", "blob", f"{revision}:{path}", binary=True)
    return found.stdout if found.returncode == 0 else None


def _tree(worktree: Path, run, revision: str) -> str:
    tree = fix_names.git_out(worktree, run, "rev-parse", "--verify", "--quiet",
                             f"{revision}^{{tree}}")
    if tree is None:
        raise GitRefused(f"git rev-parse {revision}^{{tree}} a échoué dans {worktree}.")
    return tree


def _status(worktree: Path, run) -> list:
    """Return the (path, XY) entries of git status, untracked files included."""
    result = fix_names.git(worktree, run, "status", "--porcelain=v1", "-uall", "-z")
    if result.returncode != 0:
        raise GitRefused(f"git status a échoué dans {worktree} : {result.stderr.strip()}")
    fields = result.stdout.split("\0")
    entries, index = [], 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        if "R" in field[:2] or "C" in field[:2]:
            index += 1
        entries.append((field[3:], field[:2]))
    return entries


def _run_ok(worktree: Path, run, label: str, *args: str) -> None:
    """Run git in worktree; raise GitRefused with its message when it fails."""
    result = fix_names.git(worktree, run, *args)
    if result.returncode != 0:
        raise GitRefused(f"{label} a échoué dans {worktree} : {result.stderr.strip()}")


def _write_free(folder: Path, pattern: str, data: bytes) -> Path:
    """Write data to the first folder/pattern.format(N), N from 1, that does not exist."""
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(1, _MAX_SUFFIX):
        target = folder / pattern.format(number)
        try:
            with open(target, "xb") as handle:
                handle.write(data)
        except FileExistsError:
            continue
        return target
    raise GitRefused(f"Aucun nom libre pour {pattern.format('<N>')} dans {folder}.")


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
