"""Selection of the checklist rows the fix mode may correct, grouped by repo and category."""
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
MINUTES_PER_ROW = 4
MINUTES_PER_KEY = 10
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

    findings is {ref: latest finding}; excluded the entries of lots.json; repos the repos of
    context.json; access the entries of doctor.json; flights {repo: in_flight}. refs are rows
    the lead adds (they skip the automation and in-flight checks), without rows the lead
    removes. run_dir, when given, tells whether fiches/CQ-xxx.md exists.
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
        else:
            forced.add(ref)
    keys, left = {}, []
    for index, row in enumerate(rows):
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
        targets, every_repo = _targets(finding, repo_names)
        category = row.get("category") or _NO_CATEGORY
        slug = fix_names.category_slug(category)
        first_cause = None
        for name in targets:
            cause = _blocked(name, status_of, flights, ref, slug, lead)
            if cause:
                first_cause = first_cause or cause
                continue
            key = keys.setdefault((name, slug), {
                "key": f"{name}/{slug}", "repo": name, "category": category, "slug": slug,
                "push_ready": status_of[name] == "ok", "rows": [],
                "_order": (not slug.startswith("securite"), index, repo_names.index(name)),
            })
            key["rows"].append(_row(row, finding, lead, every_repo, run_dir))
        if first_cause and not any(ref == item["ref"] for key in keys.values() for item in key["rows"]):
            left.append(_left(row, first_cause[0], first_cause[1]))
    ordered = sorted(keys.values(), key=lambda key: key["_order"])
    for key in ordered:
        del key["_order"]
    count = sum(len(key["rows"]) for key in ordered)
    return {
        "keys": ordered,
        "left": left,
        "flights": flights,
        "notes": notes,
        "estimate_min": count * MINUTES_PER_ROW + len(ordered) * MINUTES_PER_KEY,
    }


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
    if lead:
        return None
    flight = flights.get(name) or {}
    for mr in flight.get("open") or []:
        if mr.get("refs") is None and fix_names.branch_matches(
            mr.get("branch") or "", fix_names.BRANCH_PREFIX, slug
        ):
            return f"MR !{mr.get('iid')} ouverte, Réf illisibles", mr.get("url")
        if ref in (mr.get("refs") or []):
            return f"déjà dans la MR !{mr.get('iid')}", mr.get("url")
    for mr in flight.get("merged") or []:
        if ref in (mr.get("refs") or []):
            return f"MR !{mr.get('iid')} déjà fusionnée", mr.get("url")
    for mr in flight.get("closed") or []:
        if ref in (mr.get("refs") or []):
            return f"MR !{mr.get('iid')} fermée sans fusion", mr.get("url")
    for branch in flight.get("branches") or []:
        if ref in (branch.get("refs") or []):
            return f"déjà sur la branche {branch.get('branch')}", None
    return None


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
