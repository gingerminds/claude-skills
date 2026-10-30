"""What a fix pass writes to Notion (cqa_notion.py fix-plan): the rows that get a fix comment,
the texts of that comment, the status written back for a closed merge request, and the groups
of the page comment. Everything here is pure: the caller reads the run files and Notion.
"""
from typing import Optional

from cqa import fix_mr, fix_rows
from cqa.checklist import ref_label

LEFT_BY_LEAD = "laissée aux devs par le lead"
NO_ANSWER = "sans réponse du lead"
NOT_HANDLED = "non traitée pendant la passe"
NO_REASON = "raison non précisée"
NO_REVIEW_OPTION = "option En review absente de la copie"
# Groups of the rows not handled, in the order of the page comment.
GROUPS = (
    ("outside", "hors du dépôt"),
    ("skipped", "écartées"),
    ("dropped", "retirées"),
    ("lead", "laissées aux devs"),
    ("unanswered", "sans réponse"),
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

    Those are the rows of its keys, but the ones already on the branch before it
    (done_before), and the rows of fix/candidates.json left out as outside the repo (their
    reason starts with « hors du dépôt »). reports maps a key name to its [(N, report)].

    A row is {"mrs", "branches", "done", "remaining", "uat", "commits", "not_done", "group",
    "in_mr", "fixed", "partial"}. A row fixed (or covered by the fix of another row) in a key
    lists the MR of a published key (in_mr) or the local branch of another one, with the
    texts of its fix; otherwise not_done gives the reason and group its group of GROUPS. A
    row of several keys is fixed when one of them fixed it. Local branches are named
    « <branche> du repo <repo> » when several keys keep one.
    """
    keys = state.get("keys") or {}
    local_keys = [key for key in keys.values() if not published(key) and key.get("branch")]
    found = {}
    for name, key in keys.items():
        rows = key.get("rows") or {}
        mr = key["mr"]["url"] if published(key) else None
        branch = None
        if mr is None and key.get("branch") and not key.get("branch_deleted"):
            branch = key["branch"]
            if len(local_keys) > 1:
                branch = f"{branch} du repo {key['repo']}"
        covered = fix_mr.covered_refs(rows)
        for ref_text, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if not isinstance(row, dict) or row.get("outcome") == "done_before":
                continue
            ref, outcome = int(ref_text), row.get("outcome")
            entry = found.setdefault(ref, _empty())
            if outcome == "fixed" or ref in covered:
                _add_fix(entry, ref, row, rows, covered, reports.get(name) or [], mr, branch)
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
            value = entry.get("status_before") if isinstance(entry, dict) else None
            if entry.get("url") == url and isinstance(value, str) and value.strip():
                return value
    return None


def closed_target(entries: list) -> tuple:
    """Return (url, status_before) of the newest link of a row (the last entry): the merge
    request to name and the status to write back; status_before falls back to an older entry,
    None when no entry has it."""
    url = entries[-1].get("url")
    for entry in reversed(entries):
        value = entry.get("status_before")
        if isinstance(value, str) and value.strip():
            return url, value
    return url, None


def closed_reason(url: str, before: Optional[str]) -> str:
    """Return the « Non traité » reason of a row whose merge request was closed unmerged."""
    if before:
        return f"MR fermée sans fusion, statut remis à « {before} » :\n{url}"
    return f"MR fermée sans fusion :\n{url}"


def not_done_groups(rows: dict) -> list:
    """Return [(label, refs)] of the rows not handled, in the order of GROUPS."""
    return [(label, sorted(ref for ref, row in rows.items() if row["group"] == group))
            for group, label in GROUPS]


def _empty() -> dict:
    return {"mrs": [], "branches": [], "done": [], "remaining": [], "uat": [], "commits": [],
            "reasons": [], "in_mr": False, "fixed": False}


def _add_fix(entry, ref, row, rows, covered, reports, mr, branch) -> None:
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
    elif branch:
        _extend(entry["branches"], [branch])
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
        "mrs": entry["mrs"], "branches": entry["branches"], "done": joined("done"),
        "remaining": joined("remaining"), "uat": joined("uat"),
        "commits": entry["commits"] if fixed else [], "not_done": reason, "group": group,
        "in_mr": entry["in_mr"], "fixed": fixed, "partial": fixed and bool(entry["remaining"]),
    }
