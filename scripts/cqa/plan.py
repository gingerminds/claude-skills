"""Notion write plan: the operations of a run, and the protection of statuses set by humans."""
import copy
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

from cqa.checklist import (
    STATUS_IMPLEMENTED,
    STATUS_NA,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_STANDBY,
    STATUS_TODO,
)
from cqa.comments import format_comment, format_excluded_comment
from cqa.findings import apply_guardrails

STATUS_FOR_VERDICT = {
    "ok": STATUS_OK,
    "ok_human": STATUS_IMPLEMENTED,
    "partial": STATUS_PARTIAL,
    "ko": STATUS_TODO,
    "na": STATUS_NA,
    "unknown": None,
}

_MISSING_FINDING = "constat invalide ou absent"
_NON_CONFORM = (STATUS_TODO, STATUS_PARTIAL)
_STATUS_ORDER = (
    STATUS_TODO,
    STATUS_PARTIAL,
    STATUS_IMPLEMENTED,
    STATUS_OK,
    STATUS_NA,
    STATUS_STANDBY,
)


def is_human_set(row: dict) -> bool:
    """Return True when the status of the row was set by a person.

    That is a status other than "À traiter" whose last edit is not the integration's. A missing
    value counts as human, so that a doubtful row is never overwritten silently.
    """
    return row.get("status") != STATUS_TODO and not row.get("last_edited_by_bot")


def operation_key(kind: str, page_id: str, after, text: str) -> str:
    """Return the key of an operation: "<kind>:<page_id>:<12 hex of sha1(after + text)>".

    after is hashed as is when it is a text, as JSON when it is a list, and as an empty text
    when it is None. The key is the same wherever and whenever the same operation is planned.
    """
    if after is None:
        shown = ""
    elif isinstance(after, str):
        shown = after
    else:
        shown = json.dumps(after, ensure_ascii=False)
    digest = hashlib.sha1((shown + text).encode("utf-8")).hexdigest()
    return f"{kind}:{page_id}:{digest[:12]}"


def load_history(cache_root: Path, slug: str, copy_ds_id: str, exclude_run: str) -> dict[int, str]:
    """Return, for each ref, the verdict of the last comment written in earlier runs of this copy.

    Runs are the folders of cache_root/slug, read in name order (a run id sorts by date) except
    exclude_run. A run counts when its plan, or else its context, names copy_ds_id. Only the
    "comment" entries of notion/applied.json are read; a file that cannot be read is skipped.
    """
    history = {}
    project_dir = Path(cache_root) / slug
    if not project_dir.is_dir():
        return history
    for run_dir in sorted(project_dir.iterdir(), key=lambda path: path.name):
        if run_dir.name == exclude_run or not run_dir.is_dir():
            continue
        applied = _read_json(run_dir / "notion" / "applied.json")
        if not isinstance(applied, dict) or _copy_of(run_dir) != copy_ds_id:
            continue
        entries = applied.get("applied")
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("kind") != "comment":
                continue
            ref, verdict = entry.get("ref"), entry.get("verdict")
            if isinstance(ref, int) and not isinstance(ref, bool) and isinstance(verdict, str):
                history[ref] = verdict
    return history


def build_plan(
    *,
    rows: list[dict],
    findings: dict[int, dict],
    excluded: list[dict],
    context: dict,
    visible_verdicts: dict[int, list[str]],
    history: dict[int, str],
    run_date: date,
    plugin_version: str,
    followup_done: set[int],
    missing_in_copy: Optional[list] = None,
    recette_refs: frozenset = frozenset(),
    comment_dates: Optional[dict] = None,
) -> dict:
    """Return the plan.json document: the Notion writes the run proposes, and their summary.

    A kept row (not excluded, with a ref) goes through the guardrails and yields up to three
    operations, in this order: status (the target of its verdict when it differs from the
    current status), assign (the lead, on ok_human) and comment. A comment is written when the
    status changes, or when neither the visible comments of the integration nor the history of
    earlier runs already carry the verdict. A kept row without finding counts as unknown.
    Excluded rows: techno gets the status "Non applicable" and a comment, no_tools a comment,
    standby, phase and no_fiche nothing. A row without ref never gets an operation. A status
    operation carries human_override when the status it replaces was set by a person, except
    for the refs of recette_refs: the devs set their status on purpose, the recette may change it.

    Operations come by ref. Besides the keys of the exchange format the plan carries "rows",
    the status of every row before and after the run, which filter_plan needs to recompute
    the summary, and "plan_id", a digest of the plan that scopes the journal of the writes to it.
    missing_in_copy is the list of rows of the model absent from the copy. comment_dates maps
    the ref of a row judged again in a rerun to the date of that rerun, which its comment
    gives instead of run_date.
    """
    notion = context["notion"]
    common = {
        "run_date": run_date,
        "lead_option": context["lead"]["option"],
        "repos": context["repos"],
        "plugin_version": plugin_version,
    }
    by_ref = {row["ref"]: row for row in rows if row.get("ref") is not None}
    excluded_by_ref = {entry["ref"]: entry for entry in excluded if entry.get("ref") is not None}
    ops, unknown, deferred = [], [], []
    for ref in sorted(by_ref):
        row, entry = by_ref[ref], excluded_by_ref.get(ref)
        if entry is None:
            dated = (comment_dates or {}).get(ref)
            row_ops, verdict = _finding_operations(
                row,
                findings.get(ref),
                {**common, "run_date": dated} if dated else common,
                followup_done,
                visible_verdicts,
                history,
                ref in recette_refs,
            )
            if verdict == "unknown":
                unknown.append(ref)
        else:
            row_ops = _excluded_operations(row, entry, common, visible_verdicts, history)
            if entry["reason"] == "no_tools":
                unknown.append(ref)
            elif entry["reason"] == "phase":
                deferred.append(ref)
        ops.extend(row_ops)
    states = _row_states(rows, ops)
    plan = {
        "run_id": context["run_id"],
        "copy_data_source_id": notion["copy_data_source_id"],
        "copy_page_id": notion["copy_page_id"],
        "assignee_options": list(notion["assignee_options"]),
        "ops": ops,
        "summary": _summary(states, ops, unknown, deferred, missing_in_copy or []),
        "rows": states,
    }
    plan["plan_id"] = plan_digest(plan)
    return plan


def plan_digest(plan: dict) -> str:
    """Return the plan_id: the sha256 of the canonical JSON of the plan without its plan_id.

    The same plan rebuilt gets the same id (so a resume skips what the journal holds); any
    change of the rows, operations or recette scope gives another one.
    """
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def filter_plan(plan: dict, *, skip_human_overrides: bool) -> dict:
    """Return a copy of the plan without the pages whose status a person had set.

    With skip_human_overrides, every operation of a page that has a human_override operation
    is removed, comments and assignments included, so that the page is left as it is. The
    summary is recomputed: those pages keep their status from before the run.
    """
    result = copy.deepcopy(plan)
    if not skip_human_overrides:
        return result
    dropped = {op["page_id"] for op in plan["ops"] if op.get("human_override")}
    result["ops"] = [op for op in result["ops"] if op["page_id"] not in dropped]
    for state in result["rows"]:
        if state["page_id"] in dropped:
            state["after"] = state["before"]
    old = plan["summary"]
    result["summary"] = _summary(
        result["rows"], result["ops"], old["unknown"], old["deferred"], old["missing_in_copy"]
    )
    if "plan_id" in result:
        result["plan_id"] = plan_digest(result)
    return result


def _finding_operations(
    row, finding, common, followup_done, visible_verdicts, history, recette=False
):
    ref = row["ref"]
    if finding is None:
        finding = _missing_finding(ref)
    finding = apply_guardrails(finding, row, followup_done=ref in followup_done)
    verdict = finding["verdict"]
    ops = _status_operation(row, verdict, STATUS_FOR_VERDICT[verdict], recette)
    status_changed = bool(ops)
    assignees = list(row.get("assignees") or [])
    lead = common["lead_option"]
    if verdict == "ok_human" and lead and lead not in assignees:
        ops.append(_operation("assign", row, verdict, assignees, assignees + [lead]))
    if _needs_comment(ref, verdict, status_changed, visible_verdicts, history):
        text = format_comment(finding=finding, row=row, **common)
        ops.append(_operation("comment", row, verdict, None, None, text))
    return ops, verdict


def _excluded_operations(row, entry, common, visible_verdicts, history):
    reason = entry["reason"]
    if reason not in ("techno", "no_tools"):
        return []
    verdict = entry["verdict"]
    ops = _status_operation(row, verdict, STATUS_NA) if reason == "techno" else []
    if _needs_comment(row["ref"], verdict, bool(ops), visible_verdicts, history):
        text = format_excluded_comment(row=row, verdict=verdict, detail=entry["detail"], **common)
        ops.append(_operation("comment", row, verdict, None, None, text))
    return ops


def _status_operation(row, verdict, target, recette=False):
    if target is None or target == row.get("status"):
        return []
    override = is_human_set(row) and not recette
    return [_operation("status", row, verdict, row.get("status"), target, human_override=override)]


def _needs_comment(ref, verdict, status_changed, visible_verdicts, history) -> bool:
    if status_changed:
        return True
    return verdict not in visible_verdicts.get(ref, ()) and history.get(ref) != verdict


def _operation(kind, row, verdict, before, after, text="", human_override=False) -> dict:
    return {
        "key": operation_key(kind, row["page_id"], after, text),
        "kind": kind,
        "page_id": row["page_id"],
        "ref": row["ref"],
        "verdict": verdict,
        "before": before,
        "after": after,
        "text": text,
        "human_override": human_override,
    }


def _missing_finding(ref: int) -> dict:
    return {
        "ref": ref,
        "verdict": "unknown",
        "confidence": "low",
        "summary": _MISSING_FINDING,
        "evidence": [],
        "todo": None,
        "human_check": None,
        "divergence": None,
        "needs_probes": [],
    }


def _row_states(rows, ops) -> list[dict]:
    written = {op["page_id"]: op["after"] for op in ops if op["kind"] == "status"}
    return [
        {
            "page_id": row["page_id"],
            "ref": row.get("ref"),
            "blocking": bool(row.get("blocking")),
            "before": row.get("status"),
            "after": written.get(row["page_id"], row.get("status")),
        }
        for row in rows
    ]


def _summary(states, ops, unknown, deferred, missing_in_copy) -> dict:
    counts = Counter(state["after"] for state in states if state["after"])
    ordered = [status for status in _STATUS_ORDER if status in counts]
    ordered += [status for status in counts if status not in _STATUS_ORDER]
    blocking = sorted(
        state["ref"]
        for state in states
        if state["blocking"] and state["ref"] is not None and state["after"] in _NON_CONFORM
    )
    overrides = [
        {"ref": op["ref"], "before": op["before"], "after": op["after"]}
        for op in ops
        if op["kind"] == "status" and op["human_override"]
    ]
    return {
        "by_status_after": {status: counts[status] for status in ordered},
        "blocking_non_conform": blocking,
        "unknown": list(unknown),
        "human_overrides": overrides,
        "deferred": list(deferred),
        "missing_in_copy": list(missing_in_copy),
    }


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _copy_of(run_dir: Path) -> Optional[str]:
    plan = _read_json(run_dir / "notion" / "plan.json")
    if isinstance(plan, dict) and plan.get("copy_data_source_id"):
        return plan["copy_data_source_id"]
    context = _read_json(run_dir / "context.json")
    notion = context.get("notion") if isinstance(context, dict) else None
    return notion.get("copy_data_source_id") if isinstance(notion, dict) else None
