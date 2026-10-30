"""What a fix pass writes to Notion (cqa_notion.py fix-plan): the rows that get a fix comment,
the texts of that comment, the status written back for a closed merge request, and the groups
of the page comment. Everything here is pure: the caller reads the run files and Notion.
"""
from typing import Optional

from cqa import fix_mr, fix_rows, fix_state
from cqa.checklist import ref_label

LEFT_BY_LEAD = "laissée aux devs par le lead"
NO_ANSWER = "sans réponse du lead"
NOT_HANDLED = "non traitée pendant la passe"
NO_REASON = "raison non précisée"
NO_REVIEW_OPTION = "option En review absente de la copie"
LEFT_KEY = "clé laissée au lead ({reason})"
EARLIER_FIX = "Correctif d'une passe précédente (voir la MR)."
# Groups of the rows not handled, in the order of the page comment.
GROUPS = (
    ("outside", "hors du dépôt"),
    ("skipped", "écartées"),
    ("dropped", "retirées"),
    ("lead", "laissées aux devs"),
    ("unanswered", "sans réponse"),
    ("left", "clé laissée au lead"),
)
_SHORT_SHA = 8


def published(key: dict) -> bool:
    """Return True when the commits of the key's pass went into its merge request: the key has
    an MR, was published and did not end with its branch kept local."""
    mr = key.get("mr")
    return (isinstance(mr, dict) and isinstance(mr.get("url"), str) and bool(mr["url"].strip())
            and key.get("step") in ("published", "done") and not key.get("kept_local"))


def pass_rows(state: dict, candidates: dict, reports: dict) -> dict:
    """Return {ref: row} for the rows the pass writes a fix comment to (design 4.6.1).

    Those are the rows of its keys, and the rows of fix/candidates.json left out as outside
    the repo (their reason starts with « hors du dépôt »). The rows already on the branch
    before the pass (done_before) count only when the pass created the merge request that
    ships them (a taken-over local branch, key « reused » other than « mr »): they get its
    link with « Correctif d'une passe précédente » (ruling P21); the rows of an MR of an
    earlier pass stay untouched. reports maps a key name to its [(N, report)].

    A row is {"mrs", "pushed", "unpushed", "branches", "done", "remaining", "uat", "commits",
    "not_done", "group", "in_mr", "fixed", "partial"}. A row fixed (or covered by the fix of
    another row) in a key lists the MR of a published key (in_mr), or the branch of another
    key: in pushed when publish pushed it (key « pushed ») but no MR followed, in unpushed
    ({"name", "iid"}) when the key took over an open MR whose new commits stayed local, in
    branches when it stayed local; with the texts of its fix. Otherwise not_done gives the
    reason and group its group of GROUPS. Every row of a key left to the lead
    (fix_state.is_left) is « clé laissée au lead (<raison>) ». A row of several keys is fixed
    when one of them fixed it. Branches are named « <branche> du repo <repo> » when several
    unpublished keys keep a branch with a fix.
    """
    keys = state.get("keys") or {}
    unpublished = [key for key in keys.values() if _branch_with_fixes(key)]
    found = {}
    for name, key in keys.items():
        rows = key.get("rows") or {}
        left = key["left"]["reason"] if fix_state.is_left(key) else None
        mr = key["mr"]["url"] if published(key) else None
        earlier = mr is not None and key.get("reused") != "mr"
        branch, unpushed = None, None
        if mr is None and key.get("branch") and not key.get("branch_deleted"):
            branch = key["branch"]
            if len(unpublished) > 1:
                branch = f"{branch} du repo {key['repo']}"
            open_mr = key.get("mr") if isinstance(key.get("mr"), dict) else {}
            if isinstance(open_mr.get("iid"), int) and not key.get("pushed"):
                branch, unpushed = None, {"name": branch, "iid": open_mr["iid"]}
        pushed = bool(key.get("pushed"))
        covered = fix_mr.covered_refs(rows)
        for ref_text, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if not isinstance(row, dict):
                continue
            ref, outcome = int(ref_text), row.get("outcome")
            if outcome == "done_before" and (left is not None or not earlier):
                continue
            entry = found.setdefault(ref, _empty())
            if left is not None:
                entry["reasons"].append((LEFT_KEY.format(reason=left), "left"))
            elif outcome == "done_before":
                _add_earlier(entry, ref, row, reports.get(name) or [], mr)
            elif outcome == "fixed" or ref in covered:
                _add_fix(entry, ref, row, rows, covered, reports.get(name) or [], mr, branch,
                         pushed, unpushed)
            else:
                entry["reasons"].append(_not_done(row))
    for item in candidates.get("left") or []:
        ref = item.get("ref") if isinstance(item, dict) else None
        reason = (item.get("reason") or "") if isinstance(item, dict) else ""
        if isinstance(ref, int) and ref not in found and reason.startswith(fix_rows.OUTSIDE_REPO):
            entry = found[ref] = _empty()
            entry["reasons"].append((reason, "outside"))
    return {ref: _settled(entry) for ref, entry in sorted(found.items())}


def status_before(row: dict, entries: list) -> Optional[str]:
    """Return the status of a row before the pass: the status_before of its entry of
    fix/mr-links.json for one of its merge requests, None when none has it."""
    for url in row["mrs"]:
        for entry in entries or []:
            if not isinstance(entry, dict) or entry.get("url") != url:
                continue
            value = entry.get("status_before")
            if isinstance(value, str) and value.strip():
                return value
    return None


def closed_target(entries: list) -> tuple:
    """Return (urls, status_before) of a row whose merge request was closed unmerged, from its
    entries in the newest fix/mr-links.json that links it: the URL of the newest entry of each
    repo, in the order of the file, and the status_before of the newest entry (None when it
    has none: no fallback to an older entry)."""
    newest = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            newest.pop(entry.get("repo"), None)
            newest[entry.get("repo")] = entry
    last = entries[-1] if entries and isinstance(entries[-1], dict) else {}
    before = last.get("status_before")
    urls = [entry["url"] for entry in newest.values()]
    return urls, before if isinstance(before, str) and before.strip() else None


def closed_reason(urls: list, before: Optional[str]) -> str:
    """Return the « Non traité » reason of a row whose merge request was closed unmerged. Its
    status goes back only if it is still « En review », so the text says so."""
    links = "\n".join(urls)
    if before:
        return (f"MR fermée sans fusion, statut remis à « {before} » s'il était encore "
                f"« En review » :\n{links}")
    return ("MR fermée sans fusion, statut laissé tel quel (statut d'avant la passe inconnu) :"
            f"\n{links}")


def not_done_groups(rows: dict) -> list:
    """Return [(label, refs)] of the rows not handled, in the order of GROUPS."""
    return [(label, sorted(ref for ref, row in rows.items() if row["group"] == group))
            for group, label in GROUPS]


def _empty() -> dict:
    return {"mrs": [], "pushed": [], "unpushed": [], "branches": [], "done": [], "remaining": [],
            "uat": [], "commits": [], "reasons": [], "in_mr": False, "fixed": False}


def _add_earlier(entry, ref, row, reports, mr) -> None:
    """A row fixed before the pass, shipped by the merge request the pass created: its link,
    the texts of its fix when a report of the key has them, else EARLIER_FIX."""
    texts = fix_mr.fixed_texts(ref, row, reports)
    entry["fixed"] = True
    entry["in_mr"] = True
    _extend(entry["mrs"], [mr])
    _extend(entry["done"], [texts["change"] or EARLIER_FIX])
    _extend(entry["uat"], [texts["uat"]])
    _extend(entry["commits"], [sha[:_SHORT_SHA] for sha in row.get("commits") or []
                               if isinstance(sha, str)])


def _branch_with_fixes(key: dict) -> bool:
    """Return True for an unpublished key whose branch is still there and carries a fix."""
    rows = key.get("rows") or {}
    fixed = any(isinstance(row, dict) and row.get("outcome") == "fixed" for row in rows.values())
    return (not published(key) and bool(key.get("branch")) and not key.get("branch_deleted")
            and (fixed or bool(fix_mr.covered_refs(rows))))


def _add_fix(entry, ref, row, rows, covered, reports, mr, branch, pushed=False,
             unpushed=None) -> None:
    by = covered.get(ref)
    source = row if by is None else (rows.get(str(by)) or {})
    texts = fix_mr.fixed_texts(ref if by is None else by, source, reports)
    done = texts["change"]
    if by is not None:
        cover = fix_mr.COVERED.format(ref=ref_label(by))
        done = f"{cover[:1].upper()}{cover[1:]}."
    remaining = fix_mr.remaining_of(row)
    entry["fixed"] = True
    if mr:
        entry["in_mr"] = True
        _extend(entry["mrs"], [mr])
    elif unpushed:
        if unpushed not in entry["unpushed"]:
            entry["unpushed"].append(unpushed)
    elif branch:
        _extend(entry["pushed" if pushed else "branches"], [branch])
    _extend(entry["done"], [done])
    _extend(entry["uat"], [texts["uat"]])
    _extend(entry["remaining"], [remaining])
    _extend(entry["commits"], [sha[:_SHORT_SHA] for sha in source.get("commits") or []
                               if isinstance(sha, str)])


def _extend(values: list, items: list) -> None:
    for item in items:
        if isinstance(item, str) and item.strip() and item not in values:
            values.append(item)


def _not_done(row: dict) -> tuple:
    """Return (reason, group) of a row of the pass that no merge request carries."""
    outcome = row.get("outcome")
    reason = (row.get("reason") or "").strip()
    if outcome == "question":
        return NO_ANSWER, "unanswered"
    if outcome == "pending":
        return NOT_HANDLED, "skipped"
    if outcome == "dropped":
        if reason == LEFT_BY_LEAD:
            return reason, "lead"
        if reason == NO_ANSWER:
            return reason, "unanswered"
        return reason or NO_REASON, "dropped"
    return reason or NO_REASON, "skipped"


def _settled(entry: dict) -> dict:
    fixed = entry["fixed"]
    reason, group = (None, None) if fixed else (entry["reasons"] or [(NO_REASON, "skipped")])[0]

    def joined(name):
        return ("\n".join(entry[name]) or None) if fixed else None

    return {
        "mrs": entry["mrs"], "pushed": entry["pushed"], "unpushed": entry["unpushed"],
        "branches": entry["branches"], "done": joined("done"),
        "remaining": joined("remaining"), "uat": joined("uat"),
        "commits": entry["commits"] if fixed else [], "not_done": reason, "group": group,
        "in_mr": entry["in_mr"], "fixed": fixed, "partial": fixed and bool(entry["remaining"]),
    }
