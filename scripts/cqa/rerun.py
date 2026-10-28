"""Latest findings of a run and the rows a targeted rerun should evaluate."""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from cqa.checklist import ref_label
from cqa.findings import validate_document
from cqa.lots import build_lots

_HUMAN = "Intervention humaine"
_MINUTES = {"code": 5, "server": 10, "browser": 15}
_DATE = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]+))?Z"
)
_TOOL_NAME = re.compile(r"[a-z0-9-]+")


def _default_reader(run_dir: Path, lot: dict, name: str) -> Optional[tuple]:
    try:
        document = json.loads((run_dir / "findings" / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return [], [f"findings/{name} est illisible : {error}"]
    return validate_document(document, set(lot["refs"]))


def finding_files(run_dir: Path, lot_id: str) -> list[str]:
    """Return the findings file names of a lot in reading order, the last one prevailing.

    The first round, the follow-up, then the reruns by ascending number.
    """
    pattern = re.compile(re.escape(lot_id) + r"-rerun-([0-9]+)\.json")
    numbered = []
    folder = run_dir / "findings"
    if folder.is_dir():
        for path in folder.iterdir():
            found = pattern.fullmatch(path.name)
            if found:
                numbered.append((int(found.group(1)), path.name))
    return [f"{lot_id}.json", f"{lot_id}-followup.json"] + [name for _, name in sorted(numbered)]


def latest_findings(
    run_dir: Path, lots: list[dict], read: Optional[Callable] = None
) -> dict[int, dict]:
    """Return {ref: finding}, the latest valid finding of each row.

    read(lot, name) gives (valid findings, errors) of a findings file, or None when it does
    not exist; it defaults to the structural validation of the document. A row a later file
    does not give, or gives invalid, keeps its previous finding.
    """
    reader = read or (lambda lot, name: _default_reader(run_dir, lot, name))
    latest = {}
    for lot in lots:
        for name in finding_files(run_dir, lot["id"]):
            found = reader(lot, name)
            for item in found[0] if found else []:
                latest[item["ref"]] = item
    return latest


def parse_instant(value) -> Optional[datetime]:
    """Return the instant of a UTC "...Z" date with or without fraction, None when invalid."""
    if not isinstance(value, str):
        return None
    found = _DATE.fullmatch(value)
    if not found:
        return None
    micro = (found.group(2) or "0")[:6].ljust(6, "0")
    try:
        return datetime.strptime(found.group(1), "%Y-%m-%dT%H:%M:%S").replace(
            microsecond=int(micro)
        )
    except ValueError:
        return None


def rerun_candidates(
    run_dir: Path, *, extra_refs: list[int], git_run=subprocess.run, read: Optional[Callable] = None
) -> dict:
    """Return the rows a targeted rerun should evaluate, with the reasons for each.

    The reasons are cause_levee (unknown finding, or a row excluded for missing tools that
    are now all available), outil_relance (a tool ran again after the finding),
    code_modifie (an evidence file changed since the audited commit) and lead (asked for).
    Nothing here raises on a bad date or an unreadable commit: the reason is skipped and a
    French note says why. git_run is called like subprocess.run.
    """
    context = _read_object(run_dir / "context.json")
    lots_document = _read_object(run_dir / "lots.json")
    lots = [lot for lot in lots_document.get("lots") or [] if isinstance(lot, dict)]
    excluded = [entry for entry in lots_document.get("excluded") or [] if isinstance(entry, dict)]
    latest = latest_findings(run_dir, lots, read)
    notes: list[str] = []
    found: dict[int, dict] = {}

    def add(ref: int, lot_id: Optional[str], reason: str, detail: str) -> None:
        entry = found.setdefault(ref, {"ref": ref, "lot": lot_id, "reasons": [], "details": []})
        entry["reasons"].append(reason)
        entry["details"].append(detail)

    lot_of = {ref: lot["id"] for lot in lots for ref in lot.get("refs") or []}
    available = set(context.get("available_tools") or [])

    for ref in sorted(lot_of):
        item = latest.get(ref)
        if item is None:
            add(ref, lot_of[ref], "cause_levee", "aucun constat valide")
        elif item["verdict"] == "unknown":
            add(ref, lot_of[ref], "cause_levee", "constat non évalué (unknown)")
    rows = {
        row["ref"]: row
        for row in _read_object(run_dir / "rows.json").get("rows") or []
        if isinstance(row, dict) and isinstance(row.get("ref"), int)
    }
    for entry in excluded:
        required = [tool for tool in entry.get("tools") or [] if tool != _HUMAN]
        if entry.get("reason") == "no_tools" and required and set(required) <= available:
            ref = entry["ref"]
            placed = _placement(rows.get(ref), lots, rows)
            if placed is None:
                notes.append(
                    f"{ref_label(ref)} ne peut pas être relancée dans ce run : "
                    "aucun lot existant ne lui convient, il faut un nouveau run."
                )
            lot_of[ref] = placed
            add(ref, placed, "cause_levee", f"outils désormais disponibles : {', '.join(required)}")

    tools = _tool_reasons(run_dir, latest, lot_of, add, notes)
    _code_reasons(context, latest, lot_of, git_run, add, notes)

    known = set(lot_of) | {entry.get("ref") for entry in excluded}
    for ref in sorted(set(extra_refs)):
        if ref in known:
            add(ref, lot_of.get(ref), "lead", "ligne demandée par le lead")
        else:
            notes.append(f"{ref_label(ref)} ne figure dans aucune ligne du run : ignorée.")

    order = {"cause_levee": 0, "outil_relance": 1, "code_modifie": 2, "lead": 3}
    candidates = []
    for ref in sorted(found):
        entry = found[ref]
        pairs = sorted(zip(entry["reasons"], entry["details"]), key=lambda p: order[p[0]])
        entry["reasons"] = [reason for reason, _ in pairs]
        entry["details"] = [detail for _, detail in pairs]
        candidates.append(entry)

    wanted = {entry["lot"] for entry in candidates if entry["lot"]}
    chosen = [lot for lot in lots if lot["id"] in wanted]
    return {
        "candidates": candidates,
        "lots": [lot["id"] for lot in chosen],
        "tools": sorted(tools),
        "estimate_min": sum(_MINUTES.get(lot.get("kind"), 5) for lot in chosen),
        "notes": notes,
    }


def _placement(row: Optional[dict], lots: list, rows: dict) -> Optional[str]:
    """Return the id of the existing lot the lot builder gives a row, None when none fits."""
    if row is None:
        return None
    kept = [rows[ref] for lot in lots for ref in lot.get("refs") or [] if ref in rows]
    kept = [item for item in kept if item["ref"] != row["ref"]] + [row]
    for lot in build_lots(kept):
        if row["ref"] in lot["refs"]:
            existing = {item.get("id") for item in lots}
            return lot["id"] if lot["id"] in existing else None
    return None


def _read_object(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _tool_reasons(run_dir, latest, lot_of, add, notes) -> set:
    started: dict = {}
    tools = set()
    for ref, item in sorted(latest.items()):
        dates = item.get("tool_dates")
        for tool, date in (dates.items() if isinstance(dates, dict) else []):
            if not isinstance(tool, str) or not _TOOL_NAME.fullmatch(tool):
                continue
            if tool not in started:
                started[tool] = _started_at(run_dir, tool)
            if started[tool] is None:
                continue
            ran = parse_instant(started[tool])
            if ran is None:
                _note_once(
                    notes,
                    f"La date de lancement de l'outil « {tool} » est illisible : "
                    "motif « outil_relance » ignoré pour cet outil.",
                )
                continue
            then = parse_instant(date)
            if then is None:
                _note_once(
                    notes,
                    f"La date de l'outil « {tool} » dans le constat {ref_label(ref)} est "
                    "illisible : motif « outil_relance » ignoré pour ce constat.",
                )
                continue
            if ran > then:
                tools.add(tool)
                add(
                    ref,
                    lot_of.get(ref),
                    "outil_relance",
                    f"outil « {tool} » relancé le {started[tool]}, après le constat ({date})",
                )
    return tools


def _started_at(run_dir: Path, tool: str) -> Optional[str]:
    result = _read_object(run_dir / "tools" / tool / "result.json")
    value = result.get("started_at")
    return value if isinstance(value, str) else None


def _note_once(notes: list, text: str) -> None:
    if text not in notes:
        notes.append(text)


def _code_reasons(context, latest, lot_of, git_run, add, notes) -> None:
    repos = {
        repo["name"]: repo
        for repo in context.get("repos") or []
        if isinstance(repo, dict) and isinstance(repo.get("name"), str)
    }
    changed: dict = {}
    for ref, item in sorted(latest.items()):
        for evidence in item.get("evidence") or []:
            if not isinstance(evidence, dict) or evidence.get("type") != "file":
                continue
            name, path = evidence.get("repo"), evidence.get("path")
            if name not in repos or not isinstance(path, str):
                continue
            if name not in changed:
                changed[name] = _changed_files(repos[name], git_run, notes)
            if path in changed[name]:
                add(
                    ref,
                    lot_of.get(ref),
                    "code_modifie",
                    f"{path} modifié depuis le commit audité du repo « {name} »",
                )


def _changed_files(repo: dict, git_run, notes: list) -> set:
    name, sha, path = repo["name"], repo.get("audited_sha"), repo.get("path")
    problem = (
        f"Le commit audité du repo « {name} » est introuvable ou illisible : "
        "motif « code_modifie » ignoré pour ce repo."
    )
    if not (isinstance(sha, str) and sha and isinstance(path, str) and path):
        notes.append(problem)
        return set()
    try:
        result = git_run(
            ["git", "-C", path, "diff", "--name-only", "--relative", f"{sha}..HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        notes.append(problem)
        return set()
    if result.returncode != 0:
        notes.append(problem)
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}
