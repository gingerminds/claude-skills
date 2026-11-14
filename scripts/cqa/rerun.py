"""Latest findings of a run and the rows a targeted rerun should evaluate."""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from cqa import checked, contract
from cqa.checklist import STATUS_IMPLEMENTED, ref_label
from cqa.findings import LEGACY_KEY, REFUSED_KEY, validate_document
from cqa.grounding import Grounding
from cqa.lots import DOCUMENT_LOT_ID, build_lots, lot_document_refs
from cqa.probes import app_path

_HUMAN = "Intervention humaine"
_MINUTES = {"code": 5, "server": 10, "browser": 15}
_DATE = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]+))?Z"
)
_TOOL_NAME = re.compile(r"[a-z0-9-]+")


_REFUSED_EVIDENCE = re.compile(r"constat CQ-[0-9]+ : preuve n° [0-9]+ \([a-z]+\) écartée : ")


def is_refused_evidence(error: str) -> bool:
    """Whether an error of read_grounded_findings names a piece of evidence dropped from a finding
    that is kept (cqa.grounding), not a finding set aside."""
    return bool(_REFUSED_EVIDENCE.match(error))


def _default_reader(run_dir: Path, lot: dict, name: str) -> Optional[tuple]:
    try:
        document = json.loads((run_dir / "findings" / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return [], [f"findings/{name} est illisible : {error}"]
    strict = contract.strict_file(run_dir, contract.run_context(run_dir), lot["id"], name)
    valid, errors = validate_document(
        document,
        set(lot["refs"]),
        optional_refs=frozenset(lot_document_refs(lot)),
        forbidden_refs=_document_refs_of_round(lot, name),
        agent_file=strict and _is_agent_file(lot),
        lot_id=lot["id"] if strict else None,
    )
    for item in valid:
        _mark_contract(item, strict)
    return valid, errors


def _mark_contract(item: dict, strict: bool) -> None:
    """Set the keys the reader owns on a finding it returns, whatever the file held."""
    item.pop(REFUSED_KEY, None)
    if strict:
        item.pop(LEGACY_KEY, None)
    else:
        item[LEGACY_KEY] = True


def _is_agent_file(lot: dict) -> bool:
    """Whether the findings files of the lot are written by a sub-agent: all but the documents'."""
    return lot.get("id") != DOCUMENT_LOT_ID


def _document_refs_of_round(lot: dict, name: str) -> frozenset:
    """Return the rows of the lot judged from a document that the file name must not give.

    A first-round or follow-up file never judges them (the report of the document does); a
    rerun file may.
    """
    if _RERUN_NAME.fullmatch(name):
        return frozenset()
    return frozenset(lot_document_refs(lot))


def read_grounded_findings(
    run_dir: Path, context: dict, lot: dict, name: str, *, freeze: Optional[str] = None
) -> Optional[tuple]:
    """Return (valid findings, errors) of findings/<name>, None when it does not exist.

    The valid findings are those of validate_document (as an agent file, but for the virtual lot
    of the documents), with the path the application is served under (context.json target), whose
    evidence also holds against the run (cqa.grounding): a cited file exists in a repo of
    context.json, a cited tool succeeded and has its summary, a page or an http request is on a
    host of the target and an http request is a probe the toolbox ran, a document is in
    context-docs/. A cited file, a tool summary or a document that is missing sets the finding
    aside; any other refused piece of evidence is only dropped from the finding, which carries it
    in REFUSED_KEY, and its error says « écartée » (is_refused_evidence). Every reader of the
    findings of a run that decides something (plan, rerun-candidates, recette, fix) uses it.

    The rules are those of the run (cqa.contract): a file of a run started before 0.18.1 that
    validate never checked keeps the checks of 0.18.0 (a cited file, a tool summary), is not read
    as an agent file, and its findings carry LEGACY_KEY (the guardrails then skip their rules 7
    and 8); such a file is never recorded. validate (freeze "refresh") always applies the rules of
    0.18.1, so a file it checked follows them since, whatever the run.

    The check is frozen when the file is validated (cqa.checked, findings/<lot>.checked.json):
    while the record of the file holds for its current content, the recorded problems stand in
    for the check against the run, which has moved since (a file deleted by a merged fix, a tool
    relaunched). A file, or a finding, that no record covers is checked against the run.
    freeze "refresh" checks every finding against the run and records the result (validate);
    "ensure" does the same only when no record covers the file (rerun-close); None never writes.
    """
    path = run_dir / "findings" / name
    try:
        data = path.read_bytes()
        document = json.loads(data.decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return [], [f"findings/{name} est illisible : {error}"]
    strict = freeze == "refresh" or contract.strict_file(run_dir, context, lot["id"], name)
    valid, errors = validate_document(
        document,
        set(lot["refs"]),
        app_path=app_path(context),
        optional_refs=frozenset(lot_document_refs(lot)),
        forbidden_refs=_document_refs_of_round(lot, name),
        agent_file=strict and _is_agent_file(lot),
        lot_id=lot["id"] if strict else None,
    )
    repos = context.get("repos") if isinstance(context.get("repos"), list) else []
    roots = {
        repo["name"]: Path(repo["path"])
        for repo in repos
        if isinstance(repo, dict) and isinstance(repo.get("name"), str)
        and isinstance(repo.get("path"), str)
    }
    grounding = Grounding(run_dir, context, roots, strict=strict)
    file_digest = checked.digest(data)
    recorded = None
    if strict and freeze != "refresh":
        recorded = checked.load(run_dir, lot["id"], name, file_digest)
    moment = checked.now()
    grounded, entries = [], []
    for item in valid:
        found = recorded.get(item["ref"]) if recorded is not None else None
        if found is None:
            found = _checked(grounding, item["evidence"])
        problems, refused = found["problems"], found["refused"]
        entries.append(checked.entry(item["ref"], item["evidence"], problems, moment, refused))
        label = ref_label(item["ref"])
        errors.extend(f"constat {label} : {text}" for text in problems)
        errors.extend(f"constat {label} : {refusal['problem']}" for refusal in refused)
        if not problems:
            grounded.append(_without_refused(item, refused, strict))
    if strict and entries and (freeze == "refresh" or (freeze == "ensure" and recorded is None)):
        checked.freeze(run_dir, lot["id"], name, file_digest, entries)
    return grounded, errors


def _checked(grounding: Grounding, evidence: list) -> dict:
    """Return {"problems", "refused"} of the evidence of a finding, checked against the run."""
    problems, refused = [], []
    for problem in grounding.check(evidence):
        if problem.drops:
            refused.append({"evidence": problem.number, "problem": problem.text, "label": problem.label})
        else:
            problems.append(problem.text)
    return {"problems": problems, "refused": refused}


def _without_refused(item: dict, refused: list, strict: bool) -> dict:
    """Return the finding without the evidence refused, which REFUSED_KEY then names."""
    _mark_contract(item, strict)
    evidence = item["evidence"]
    numbers = {refusal["evidence"] for refusal in refused if refusal["evidence"] <= len(evidence)}
    if not numbers:
        return item
    item[REFUSED_KEY] = [
        {"evidence": evidence[refusal["evidence"] - 1], "problem": refusal["problem"], "label": refusal["label"]}
        for refusal in refused
        if refusal["evidence"] in numbers
    ]
    item["evidence"] = [piece for number, piece in enumerate(evidence, start=1) if number not in numbers]
    return item


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


def document_lot(lots: list) -> Optional[dict]:
    """Return the virtual lot of the rows judged from context documents, None without any.

    It is {"id": "context-docs", "kind": "documents", "refs": [...]}: the document_refs of every
    lot, sorted. Its findings file is findings/context-docs.json (written by cqa_plan.py lots).
    """
    refs = sorted({ref for lot in lots for ref in lot_document_refs(lot)})
    if not refs:
        return None
    return {"id": DOCUMENT_LOT_ID, "kind": "documents", "refs": refs}


def latest_findings(
    run_dir: Path, lots: list[dict], read: Optional[Callable] = None
) -> dict[int, dict]:
    """Return {ref: finding}, the latest valid finding of each row.

    read(lot, name) gives (valid findings, errors) of a findings file, or None when it does
    not exist; it defaults to the structural validation of the document. A row a later file
    does not give, or gives invalid, keeps its previous finding. The virtual lot of the context
    documents (document_lot) is read before the others: a rerun file of the lot of the row
    prevails over the finding of its document. A first-round or follow-up file never does,
    whatever read gives: its findings for the document_refs of the lot are ignored.
    """
    reader = read or (lambda lot, name: _default_reader(run_dir, lot, name))
    latest = {}
    documents = document_lot(lots)
    for lot in ([documents] if documents else []) + list(lots):
        for name in finding_files(run_dir, lot["id"]):
            found = reader(lot, name)
            skipped = _document_refs_of_round(lot, name)
            for item in found[0] if found else []:
                if item["ref"] not in skipped:
                    latest[item["ref"]] = item
    return latest


_RERUN_NAME = re.compile(r".+-rerun-([0-9]{2})\.json")


def rerun_dates(run_dir: Path) -> dict:
    """Return {NN: date} of the closed reruns that recorded their date (rerun-close)."""
    dates = _read_object(Path(run_dir) / "reruns.json").get("dates")
    found = {}
    for key, value in (dates.items() if isinstance(dates, dict) else []):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                found[key] = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                continue
    return found


def comment_dates(run_dir: Path, sources: dict) -> dict:
    """Return {ref: date of its rerun} for the rows whose latest finding comes from a rerun
    (sources: {ref: name of the findings file of its latest finding}).

    Their comment then says when they were judged again; a rerun closed before its date was
    recorded keeps the date of the run.
    """
    dates = rerun_dates(run_dir)
    found = {}
    for ref, name in sources.items():
        match = _RERUN_NAME.fullmatch(name)
        if match and match.group(1) in dates:
            found[ref] = dates[match.group(1)]
    return found


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
    if not isinstance(lots_document.get("lots"), list):
        notes.append(
            "lots.json est introuvable ou illisible : aucune ligne à évaluer, "
            "lancer d'abord cqa_plan.py lots."
        )
    found: dict[int, dict] = {}

    def add(ref: int, lot_id: Optional[str], reason: str, detail: str) -> None:
        entry = found.setdefault(ref, {"ref": ref, "lot": lot_id, "pairs": []})
        if (reason, detail) not in entry["pairs"]:
            entry["pairs"].append((reason, detail))

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
            placed = _placement(rows.get(ref), lots, rows, run_dir)
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
            if ref not in lot_of:
                notes.append(
                    f"{ref_label(ref)} ne peut pas être relancée dans ce run : "
                    "elle n'appartient à aucun lot, il faut un nouveau run."
                )
        else:
            notes.append(f"{ref_label(ref)} ne figure dans aucune ligne du run : ignorée.")

    order = {"cause_levee": 0, "outil_relance": 1, "code_modifie": 2, "lead": 3}
    candidates = []
    for ref in sorted(found):
        entry = found[ref]
        pairs = sorted(entry.pop("pairs"), key=lambda pair: order[pair[0]])
        entry["reasons"] = list(dict.fromkeys(reason for reason, _ in pairs))
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


def recette_candidates(rows: list, plan: Optional[dict], applied: Optional[dict]) -> list:
    """Return the rows the devs fixed: "Mis en place" in the copy, but not set so by the skill.

    The last status the skill wrote is the "value" of the last status entry of the ref in the
    journal. Journal entries without a value (older runs) fall back to the plan: the "after" of
    the row when the status operation of that ref is in the journal, its "before" otherwise. A row the skill knows no
    status for (no plan, no journal, row absent from the plan) is a candidate: the lead may drop
    it. Each item is {"ref", "page_id", "status", "skill_last"}. Nothing here raises on a
    damaged document.
    """
    last_of = skill_last_of(plan, applied)
    found = []
    for row in rows:
        ref = row.get("ref")
        if not isinstance(ref, int) or row.get("status") != STATUS_IMPLEMENTED:
            continue
        last = last_of(ref)
        if last == STATUS_IMPLEMENTED:
            continue
        found.append(
            {"ref": ref, "page_id": row.get("page_id"), "status": row["status"], "skill_last": last}
        )
    return found


def skill_last_of(plan: Optional[dict], applied: Optional[dict]) -> Callable:
    """Return ref -> the last status the skill wrote for the row, None when unknown.

    See recette_candidates for the rule.
    """
    plan_rows = {
        state["ref"]: state
        for state in list_of(plan, "rows")
        if isinstance(state, dict) and isinstance(state.get("ref"), int)
    }
    written = {
        entry.get("key")
        for entry in list_of(applied, "applied")
        if isinstance(entry, dict)
    }
    values: dict = {}
    for entry in list_of(applied, "applied"):
        if (
            isinstance(entry, dict)
            and entry.get("kind") == "status"
            and isinstance(entry.get("value"), str)
        ):
            values[entry.get("ref")] = entry["value"]
    planned = {
        op.get("ref")
        for op in list_of(plan, "ops")
        if isinstance(op, dict) and op.get("kind") == "status" and op.get("key") in written
    }

    def last_of(ref):
        last = values.get(ref)
        state = plan_rows.get(ref)
        if last is None and state is not None:
            last = state.get("after") if ref in planned else state.get("before")
        return last

    return last_of


def list_of(document, key: str) -> list:
    value = document.get(key) if isinstance(document, dict) else None
    return value if isinstance(value, list) else []


def _placement(
    row: Optional[dict], lots: list, rows: dict, run_dir: Optional[Path] = None
) -> Optional[str]:
    """Return the id of the existing lot the lot builder gives a row, None when none fits.

    The builder reads the fiches of the run (run_dir) as it does when it writes lots.json.
    """
    if row is None:
        return None
    kept = [rows[ref] for lot in lots for ref in lot.get("refs") or [] if ref in rows]
    kept = [item for item in kept if item["ref"] != row["ref"]] + [row]
    for lot in build_lots(kept, run_dir=run_dir):
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
