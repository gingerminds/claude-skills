"""Selection of the checklist rows the fix mode may correct: one key per repo, in sections."""
from pathlib import Path
from typing import Optional

from cqa import fix_names
from cqa.checklist import fiche_filename, ref_label

OPEN_STATUSES = ("À traiter", "Mise en place partielle")
FIXABLE = {"Auto : détection + MR": False, "Semi-auto : MR à valider": True}
SILENT_EXCLUSIONS = ("phase", "standby", "techno")
UNEVALUATED_EXCLUSIONS = ("no_tools", "no_fiche")
UNEVALUATED = "non évaluée par l'audit : lancer d'abord une relance"
NOTHING_TO_FIX = "rien à corriger selon l'audit"
OUTSIDE_REPO = "hors du dépôt"
MINUTES_PER_ROW = 4
MINUTES_PER_SECTION = 10
FIRST_SECTION = "securite"
_NO_CATEGORY = "Sans catégorie"
_UNAVAILABLE = "not_gitlab"
_API_STATUSES = ("ok", "no_push")


def select(
    rows: list,
    findings: dict,
    excluded: list,
    repos: list,
    access: list,
    flights: dict,
    *,
    refs=(),
    without=(),
    run_dir: Optional[Path] = None,
) -> dict:
    """Return the candidate keys, the rows left out with the reason, and a time estimate.

    A key is a repo, in the order of context.json; its sections group its rows by commit
    alias (fix_names.section_alias), SÉCURITÉ first, then in the order of the copy. A section
    exists only when a row lands in it.

    findings is {ref: latest finding}; excluded the entries of lots.json; repos the repos of
    context.json; access the entries of doctor.json; flights {repo: in_flight}. refs are rows
    the lead adds (they skip the automation, outside-the-repo and closed MR checks), without
    rows the lead removes. A finding with in_repo false leaves its row to the devs. run_dir,
    when given, tells whether fiches/CQ-xxx.md exists.
    """
    repo_names = [repo["name"] for repo in repos]
    status_of = {entry["repo"]: entry["status"] for entry in access}
    for name in repo_names:
        if name not in status_of:
            raise ValueError(f"le repo « {name} » est absent de fix/doctor.json.")
    reasons = {entry["ref"]: entry["reason"] for entry in excluded}
    notes = _flight_notes(flights)
    by_ref = {row["ref"]: row for row in rows if isinstance(row.get("ref"), int)}
    forced = set()
    for ref in refs:
        if ref not in by_ref:
            notes.append(f"{ref_label(ref)} ne figure pas dans la copie : ignorée.")
        elif by_ref[ref].get("status") not in OPEN_STATUSES:
            notes.append(f"{ref_label(ref)} a le statut « {by_ref[ref].get('status')} » : ignorée.")
        elif reasons.get(ref) in SILENT_EXCLUSIONS:
            notes.append(f"{ref_label(ref)} exclue de ce run ({reasons[ref]}) : ignorée.")
        else:
            forced.add(ref)
    keys, left = {}, []
    for row in rows:
        ref = row.get("ref")
        if not isinstance(ref, int) or row.get("status") not in OPEN_STATUSES:
            continue
        if reasons.get(ref) in SILENT_EXCLUSIONS or ref in without:
            continue
        lead = ref in forced
        verdict = _verdict(row, findings.get(ref), reasons.get(ref), lead)
        if verdict is not None:
            left.append(_left(row, verdict, None))
            continue
        finding = findings[ref]
        if not lead and finding.get("in_repo") is False:
            left.append(_left(row, _outside(finding), None))
            continue
        targets, every_repo = _targets(finding, repo_names)
        category = row.get("category") or _NO_CATEGORY
        slug = fix_names.category_slug(category)
        alias = fix_names.section_alias(category)
        first_cause, placed = None, False
        for name in targets:
            cause = _blocked(name, status_of, flights, ref, slug, lead)
            if cause:
                first_cause = first_cause or cause
                continue
            key = keys.setdefault(name, {
                "key": name, "repo": name, "push_ready": status_of[name] == "ok", "sections": [],
            })
            section = next((item for item in key["sections"] if item["alias"] == alias), None)
            if section is None:
                section = {"category": category, "alias": alias, "rows": []}
                key["sections"].append(section)
            section["rows"].append(_row(row, finding, lead, every_repo, run_dir))
            placed = True
        if first_cause and not placed:
            left.append(_left(row, first_cause[0], first_cause[1]))
    ordered = [keys[name] for name in repo_names if name in keys]
    for key in ordered:
        key["sections"].sort(key=lambda section: section["alias"] != FIRST_SECTION)
    sections = [section for key in ordered for section in key["sections"]]
    count = sum(len(section["rows"]) for section in sections)
    return {
        "keys": ordered,
        "left": left,
        "flights": flights,
        "notes": notes,
        "estimate_min": count * MINUTES_PER_ROW + len(sections) * MINUTES_PER_SECTION,
    }


def key_rows(entry) -> list:
    """Return the rows of a key of fix/candidates.json, section after section."""
    sections = entry.get("sections") if isinstance(entry, dict) else None
    found = []
    for section in sections if isinstance(sections, list) else []:
        rows = section.get("rows") if isinstance(section, dict) else None
        if isinstance(rows, list):
            found.extend(row for row in rows if isinstance(row, dict))
    return found


def _flight_notes(flights: dict) -> list:
    notes = []
    for flight in flights.values():
        for note in flight.get("notes") or []:
            if note not in notes:
                notes.append(note)
    return notes


def _verdict(row: dict, finding, exclusion, lead: bool):
    """Return why the row is left out (None when it is a candidate)."""
    automation = row.get("automation")
    if not lead and automation not in FIXABLE:
        if not automation:
            return "Automatisation non renseignée"
        return automation if automation in ("Détection seule", "Manuel") else (
            f"Automatisation « {automation} » non corrigeable"
        )
    if exclusion in UNEVALUATED_EXCLUSIONS or not finding or finding.get("verdict") == "unknown":
        return UNEVALUATED
    if finding.get("verdict") not in ("ko", "partial"):
        return NOTHING_TO_FIX
    return None


def _outside(finding: dict) -> str:
    """Return the reason of a row whose fix lies outside the repos: « hors du dépôt : <todo> »."""
    todo = finding.get("todo")
    return f"{OUTSIDE_REPO} : {todo.strip()}" if isinstance(todo, str) and todo.strip() else OUTSIDE_REPO


def _left(row: dict, reason: str, url) -> dict:
    return {"ref": row["ref"], "title": row.get("title"), "category": row.get("category"),
            "reason": reason, "url": url}


def _targets(finding: dict, repo_names: list) -> tuple:
    cited = {
        item.get("repo") for item in finding.get("evidence") or []
        if isinstance(item, dict) and item.get("type") == "file"
    }
    named = [name for name in repo_names if name in cited]
    if named:
        return named, False
    return list(repo_names), len(repo_names) > 1


def _blocked(name: str, status_of: dict, flights: dict, ref: int, slug: str, lead: bool):
    """Return (reason, url) when the row cannot go in this repo, else None."""
    if status_of[name] == _UNAVAILABLE:
        return "repo hors GitLab", None
    flight = flights.get(name) or {}
    for mr in flight.get("open") or []:
        if mr.get("refs") is None and _may_carry(mr.get("branch") or "", slug):
            return f"MR !{mr.get('iid')} ouverte, Réf illisibles", mr.get("url")
        if ref in (mr.get("refs") or []):
            return f"déjà dans la MR !{mr.get('iid')}", mr.get("url")
    for mr in flight.get("merged") or []:
        if ref in (mr.get("refs") or []):
            return f"MR !{mr.get('iid')} déjà fusionnée", mr.get("url")
    for mr in [] if lead else flight.get("closed") or []:
        if ref in (mr.get("refs") or []):
            return f"MR !{mr.get('iid')} fermée sans fusion", mr.get("url")
    for branch in flight.get("branches") or []:
        if ref in (branch.get("refs") or []):
            return f"déjà sur la branche {branch.get('branch')}", None
    return None


def _may_carry(branch: str, slug: str) -> bool:
    """True when the branch may carry a row of the category: a pass branch carries every
    category, a v2 branch only its own."""
    return (fix_names.is_pass_branch(branch, fix_names.BRANCH_BASE)
            or fix_names.branch_matches(branch, fix_names.BRANCH_PREFIX, slug))


def _row(row: dict, finding: dict, lead: bool, every_repo: bool, run_dir) -> dict:
    ref = row["ref"]
    fiche = f"fiches/{fiche_filename(ref)}"
    return {
        "ref": ref,
        "page_id": row.get("page_id"),
        "title": row.get("title"),
        "category": row.get("category"),
        "automation": row.get("automation"),
        "semi_auto": FIXABLE.get(row.get("automation"), False),
        "assignees": row.get("assignees") or [],
        "verdict": finding.get("verdict"),
        "confidence": finding.get("confidence"),
        "summary": finding.get("summary"),
        "evidence": finding.get("evidence") or [],
        "todo": finding.get("todo"),
        "human_check": finding.get("human_check"),
        "fiche_path": fiche if run_dir is not None and (Path(run_dir) / fiche).is_file() else None,
        "every_repo": every_repo,
        "lead": lead,
    }
