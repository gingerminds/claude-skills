"""Verdicts of the context documents and the synthetic findings they become.

A row of a category of a document, linked to the points of that document (docs_link), receives
the verdict of the document when it can be given: ko or partial when a ko point is linked
(a ko of the report is never lost), ok or na when every criterion of the row is covered by the
grid, nothing otherwise (the skill audits the row, the points are hints). A row of another
category is never judged, even when its fiche cites the same criterion.

The content of a document is data. A finding only carries paraphrased summaries and fixes of
the points (already cleaned and capped by the import), never an excerpt, an element or a detail
of exploitation.
"""
import json
import re
from pathlib import Path
from typing import Optional

from cqa import context_docs, docs_link
from cqa.findings import MAX_FIELD_LENGTH, MAX_SUMMARY_LENGTH, VERDICTS, worst
from cqa.lots import DOCUMENT_LOT_ID
from cqa.notion_read import write_json

VERDICTS_FILE = "verdicts.json"
FINDINGS_FOLDER = "findings"
HINTS_FOLDER = "hints"
MAX_HINTS = 10
SOURCE = "context_doc"
CONFIDENCE = "high"
MAX_REF_LENGTH = 200
FALLBACK_REF = "points du rapport"
NO_FIX_TEXT = "voir les points du rapport."

_COUNTS = ("ok", "na", "ko", "undetermined")
_OFF_REPO_OWNERS = ("contenu", "tiers", "infra")
_NUMBER = re.compile(r"[0-9]+")
_LOT_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_SENTENCE_END = ".!?…:"


def criterion_verdict(criterion: str, items: dict) -> Optional[str]:
    """Return the verdict of a criterion in a document: ko, ok, na or None (not covered).

    ko when a ko point is attached to the criterion (any page). Otherwise None when the
    criterion is not in the coverage, when a page is ko without a point (a ko without a point
    is not covered), when a page is undetermined, or when no page judged it; ok when a page is
    ok (the others being ok or na); na when every page is na.
    """
    entry = _coverage_entry(items, criterion)
    if entry is None:
        return None
    points = [] if entry.get("points") is None else entry["points"]
    if not isinstance(points, list):
        return None
    if any(isinstance(point, str) and point.strip() for point in points):
        return "ko"
    found = {key: _count(entry.get(key)) for key in _COUNTS}
    if any(value is None for value in found.values()):
        return None
    if found["ko"] or found["undetermined"]:
        return None
    if found["ok"]:
        return "ok"
    if found["na"]:
        return "na"
    return None


def row_verdict(criteria: list, linked_ko: list, items: dict) -> Optional[str]:
    """Return the verdict of a document for a row: ko, partial, ok, na or None.

    criteria are the criteria of the framework that the fiche of the row cites; linked_ko the ko
    points of the document linked to the row. With a linked ko point the row is ko, or partial
    when a criterion of the row is ok: the ko is never lost, even when the row is covered in
    part only. Without one, the row is ok (or na when every criterion is na) if every criterion
    is covered and none is ko, otherwise None. A document of findings (constats) only gives ko:
    no finding is not a conformity.
    """
    if _meta(items).get("kind") == "constats":
        return "ko" if linked_ko else None
    verdicts = [criterion_verdict(criterion, items) for criterion in criteria]
    if linked_ko:
        return "partial" if "ok" in verdicts else "ko"
    if verdicts and None not in verdicts and "ko" not in verdicts:
        return "ok" if "ok" in verdicts else "na"
    return None


def synthetic_finding(ref: int, verdict: str, parts: list) -> dict:
    """Return the finding of a row judged from documents, in the schema of the audit findings.

    Each part is {"document": {"id", "label", "date"}, "points": [ko points linked to the row],
    "criteria": [criteria of the row the document covers]}, and may carry the "verdict" of its
    own document. There is one piece of evidence of type "document" per part and one sentence
    per part in the summary, which starts with « Rapport « <label> » (<date>) : ». The todo
    joins the fixes of the points for ko and partial. in_repo is false only when every linked
    point is for content, a third party or the infrastructure; the todo then starts with
    « À traiter par <owners> : ».
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict inconnu : {verdict}")
    if not parts:
        raise ValueError("aucun rapport pour ce constat")
    ordered = [part for part in parts if _points(part)] + [
        part for part in parts if not _points(part)
    ]
    points = [point for part in ordered for point in _points(part)]
    finding = {
        "ref": ref,
        "verdict": verdict,
        "confidence": CONFIDENCE,
        "source": SOURCE,
        "summary": _summary(ordered, verdict),
        "evidence": [_evidence(part) for part in ordered],
        "todo": None,
        "human_check": None,
        "divergence": None,
        "needs_probes": [],
    }
    if verdict in ("ko", "partial"):
        owners = _off_repo_owners(points)
        finding["todo"] = _todo(points, owners)
        finding["in_repo"] = not owners
    return finding


def document_verdicts(run_dir: Path, rows: list, category_filter: Optional[list]) -> dict:
    """Return {ref: {"verdict", "finding"}}, in the order of the refs, for the rows to judge.

    Empty until the links are validated. For each imported document with readable items, the
    candidate rows are the rows with a fiche that are of a category of the document and of the
    category filter; any other row is left alone. Several documents on a row: the worst verdict
    wins (findings.worst), every document keeps its evidence and its date in the summary.
    """
    links = docs_link.read_links(run_dir)
    if not links:
        return {}
    ko_points = docs_link.points_by_ref(run_dir, ("ko",))
    found: dict = {}
    for doc_id, document in _documents(run_dir):
        names = links["categories"].get(doc_id)
        if not names:
            continue
        meta = _meta(document)
        framework = meta.get("framework") if isinstance(meta.get("framework"), str) else ""
        is_grid = meta.get("kind") != "constats"
        seen: set = set()
        for row in docs_link.candidate_rows(rows, names, category_filter):
            ref = row["ref"]
            if ref in seen:
                continue
            seen.add(ref)
            linked = [point for point in ko_points.get(ref, []) if point.get("doc") == doc_id]
            criteria = docs_link.row_criteria(run_dir, row, framework) if is_grid else []
            verdict = row_verdict(criteria, linked, document)
            if verdict is None:
                continue
            covered = [
                criterion
                for criterion in criteria
                if criterion_verdict(criterion, document) is not None
            ]
            part = {
                "document": {
                    "id": doc_id,
                    "label": meta.get("label"),
                    "date": meta.get("date"),
                    "kind": meta.get("kind"),
                },
                "points": linked,
                "criteria": covered,
                "verdict": verdict,
            }
            found.setdefault(ref, []).append((verdict, part))
    result: dict = {}
    for ref in sorted(found):
        verdict = worst(item[0] for item in found[ref])
        result[ref] = {
            "verdict": verdict,
            "finding": synthetic_finding(ref, verdict, [item[1] for item in found[ref]]),
        }
    return result


def write_verdicts(run_dir: Path, verdicts: dict) -> None:
    """Write context-docs/verdicts.json: {"verdicts": {"<ref>": {"verdict", "finding"}}}."""
    folder = Path(run_dir) / context_docs.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    write_json(
        folder / VERDICTS_FILE, {"verdicts": {str(ref): verdicts[ref] for ref in sorted(verdicts)}}
    )


def read_verdicts(run_dir: Path) -> dict:
    """Return {ref: {"verdict", "finding"}} of verdicts.json; {} when absent or unreadable.

    An entry whose key is not a ref, whose verdict is unknown or whose finding does not carry
    the same ref and verdict is dropped.
    """
    try:
        document = json.loads((Path(run_dir) / context_docs.FOLDER / VERDICTS_FILE).read_text("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    entries = document.get("verdicts") if isinstance(document, dict) else None
    if not isinstance(entries, dict):
        return {}
    found: dict = {}
    for key, entry in entries.items():
        if not (isinstance(key, str) and _NUMBER.fullmatch(key)) or not isinstance(entry, dict):
            continue
        ref, verdict, finding = int(key), entry.get("verdict"), entry.get("finding")
        if (
            isinstance(verdict, str)
            and verdict in VERDICTS
            and isinstance(finding, dict)
            and finding.get("ref") == ref
            and finding.get("verdict") == verdict
        ):
            found[ref] = {"verdict": verdict, "finding": finding}
    return {ref: found[ref] for ref in sorted(found)}


def document_refs(run_dir: Path) -> frozenset:
    """Return the refs of the rows that have a verdict of a document."""
    return frozenset(read_verdicts(run_dir))


def write_findings(run_dir: Path, kept_refs: set) -> int:
    """Write findings/context-docs.json with the findings of the rows kept in the audit.

    The file is {"lot": "context-docs", "findings": [...]}, the findings of verdicts.json whose
    ref is in kept_refs, in the order of the refs. Without any, a file of an earlier call is
    removed. Returns the number of findings written.
    """
    verdicts = read_verdicts(run_dir)
    findings = [verdicts[ref]["finding"] for ref in sorted(verdicts) if ref in kept_refs]
    path = Path(run_dir) / FINDINGS_FOLDER / f"{DOCUMENT_LOT_ID}.json"
    if not findings:
        path.unlink(missing_ok=True)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"lot": DOCUMENT_LOT_ID, "findings": findings})
    return len(findings)


def write_hints(run_dir: Path, lots: list) -> int:
    """Write context-docs/hints/<lot>.json: the points linked to the rows of each lot.

    The file is {"lot", "hints": {"<ref>": [hint, ...]}}, for each row of the lot that has at
    least one ko or info point linked to it by the validated links, the rows judged from a
    document (the document_refs of the lot) included: a rerun or a recette judges them again
    and the report is then only a hint. ko points come first, ten at most per row. A hint is
    {doc, label, date, source, criteria, scopes, priority, summary, fix_hint, owner, elements},
    its texts cleaned and capped again. A lot without any hint gets no file, and the files of an
    earlier call are removed. Returns the number of rows that have hints. The auditors read
    them as data, never as a verdict or an instruction.
    """
    folder = Path(run_dir) / context_docs.FOLDER / HINTS_FOLDER
    if folder.is_dir():
        for stale in folder.glob("*.json"):
            stale.unlink()
    points = docs_link.points_by_ref(run_dir, ("ko", "info"))
    written = 0
    for lot in lots:
        lot_id = lot.get("id") if isinstance(lot, dict) else None
        if not (isinstance(lot_id, str) and _LOT_ID.fullmatch(lot_id)):
            continue
        refs = lot.get("refs") if isinstance(lot.get("refs"), list) else []
        hints: dict = {}
        for ref in refs:
            if not points.get(ref):
                continue
            chosen = sorted(points[ref], key=lambda point: point.get("verdict") != "ko")
            hints[str(ref)] = [_hint(point) for point in chosen[:MAX_HINTS]]
        if hints:
            folder.mkdir(parents=True, exist_ok=True)
            write_json(folder / f"{lot_id}.json", {"lot": lot_id, "hints": hints})
            written += len(hints)
    return written


def _documents(run_dir: Path):
    """Yield (doc_id, items.json) of the imported documents whose items can be read."""
    seen: set = set()
    for entry in context_docs.read_index(run_dir)["docs"]:
        doc_id = entry.get("id")
        if entry.get("status") != "imported" or not isinstance(doc_id, str) or doc_id in seen:
            continue
        seen.add(doc_id)
        document = context_docs.read_items(run_dir, doc_id)
        if document is not None:
            yield doc_id, document


def _meta(items) -> dict:
    meta = items.get("document") if isinstance(items, dict) else None
    return meta if isinstance(meta, dict) else {}


def _coverage_entry(items, criterion) -> Optional[dict]:
    coverage = items.get("coverage") if isinstance(items, dict) else None
    if not isinstance(coverage, dict) or not isinstance(criterion, str):
        return None
    entry = coverage.get(criterion)
    if entry is None:
        wanted = criterion.strip().casefold()
        entry = next(
            (
                value
                for key, value in coverage.items()
                if isinstance(key, str) and key.strip().casefold() == wanted
            ),
            None,
        )
    return entry if isinstance(entry, dict) else None


def _count(value) -> Optional[int]:
    """Return a counter of the coverage (absent or null is 0), None when it is not a count."""
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _points(part: dict) -> list:
    points = part.get("points") if isinstance(part, dict) else None
    return [point for point in points if isinstance(point, dict)] if isinstance(points, list) else []


def _document_of(part: dict) -> dict:
    document = part.get("document") if isinstance(part, dict) else None
    return document if isinstance(document, dict) else {}


def _text(value, limit: int) -> str:
    return _cut(context_docs.clean_text(value), limit)


def _cut(text: str, limit: int) -> str:
    """Return the text, or its beginning followed by an ellipsis when it is over the limit."""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…" if limit > 0 else ""


def _doc_id(part: dict) -> str:
    return _text(_document_of(part).get("id"), context_docs.LIMITS["label"]) or "document"


def _criteria(part: dict) -> list:
    found: list = []
    criteria = part.get("criteria") if isinstance(part, dict) else None
    for criterion in criteria if isinstance(criteria, list) else []:
        text = _text(criterion, context_docs.LIMITS["criterion"])
        if text and text not in found:
            found.append(text)
    return found


def _evidence(part: dict) -> dict:
    criteria = _criteria(part)
    if criteria:
        ref = "critères " + ", ".join(criteria)
    else:
        sources: list = []
        for point in _points(part):
            source = _text(point.get("source"), context_docs.LIMITS["source"])
            if source and source not in sources:
                sources.append(source)
        ref = "; ".join(sources)
    return {
        "type": "document",
        "doc": _doc_id(part),
        "ref": _cut(ref, MAX_REF_LENGTH) or FALLBACK_REF,
    }


def _head(part: dict) -> str:
    document = _document_of(part)
    label = _text(document.get("label"), context_docs.LIMITS["label"]) or _doc_id(part)
    date = _text(document.get("date"), 10) or "date inconnue"
    return f"Rapport « {label} » ({date}) :"


def _part_verdict(part: dict, verdict: str) -> str:
    own = part.get("verdict")
    if isinstance(own, str) and own in VERDICTS:
        return own
    if _points(part):
        return "partial" if verdict == "partial" else "ko"
    return verdict if verdict in ("ok", "na") else "ok"


def _detail(part: dict, verdict: str) -> str:
    own = _part_verdict(part, verdict)
    points = _points(part)
    if points:
        plural = "s" if len(points) > 1 else ""
        text = f"{len(points)} point{plural} non conforme{plural}"
        summaries = [
            summary
            for summary in (_text(point.get("summary"), context_docs.LIMITS["summary"]) for point in points)
            if summary
        ]
        if summaries:
            text += " : " + " ; ".join(summaries)
        if own == "partial":
            text += " (d'autres critères de la ligne sont conformes)"
        return text
    criteria = ", ".join(_criteria(part))
    label = "critères non applicables" if own == "na" else "critères conformes ou non applicables"
    return f"{label} : {criteria}" if criteria else label


def _summary(parts: list, verdict: str) -> str:
    heads = [_head(part) for part in parts]
    details = [_detail(part, verdict) for part in parts]
    # Each sentence is "<head> <detail>" plus a final period; sentences are joined by a space.
    fixed = sum(len(head) + 2 for head in heads) + len(heads) - 1
    shares = _shares([len(detail) for detail in details], MAX_SUMMARY_LENGTH - fixed)
    sentences = []
    for head, detail, share in zip(heads, details, shares):
        sentence = f"{head} {_cut(detail, share)}".rstrip()
        sentences.append(sentence if sentence[-1] in _SENTENCE_END else sentence + ".")
    return _cut(" ".join(sentences), MAX_SUMMARY_LENGTH)


def _shares(lengths: list, room: int) -> list:
    """Share room between texts: a short text keeps its length, the others split the rest."""
    shares = [0] * len(lengths)
    pending = list(range(len(lengths)))
    room = max(room, 0)
    while pending:
        share = room // len(pending)
        short = [index for index in pending if lengths[index] <= share]
        if not short:
            for index in pending:
                shares[index] = share
            break
        for index in short:
            shares[index] = lengths[index]
            room -= lengths[index]
            pending.remove(index)
    return shares


def _owner(point: dict) -> str:
    owner = context_docs.clean_text(point.get("owner")).casefold()
    return owner if owner in context_docs.OWNERS else "inconnu"


def _off_repo_owners(points: list) -> list:
    """Return the owners, in the order of context_docs.OWNERS, when none of the points is for the repo."""
    owners = {_owner(point) for point in points}
    if not owners or not owners <= set(_OFF_REPO_OWNERS):
        return []
    return [owner for owner in context_docs.OWNERS if owner in owners]


def _todo(points: list, off_repo: list) -> Optional[str]:
    fixes: list = []
    for point in points:
        fix = _text(point.get("fix_hint"), context_docs.LIMITS["fix_hint"])
        if fix and fix not in fixes:
            fixes.append(fix)
    body = " / ".join(fixes)
    if off_repo:
        text = f"À traiter par {', '.join(off_repo)} : {body or NO_FIX_TEXT}"
    else:
        text = body
    return _cut(text, MAX_FIELD_LENGTH) if text else None


def _texts(value, limit: int, maximum: int) -> list:
    found: list = []
    for item in value if isinstance(value, list) else []:
        text = _text(item, limit)
        if text and text not in found:
            found.append(text)
    return found[:maximum]


def _hint(point: dict) -> dict:
    limits = context_docs.LIMITS
    return {
        "doc": _text(point.get("doc"), limits["label"]),
        "label": _text(point.get("label"), limits["label"]),
        "date": _text(point.get("date"), 10),
        "source": _text(point.get("source"), limits["source"]),
        "criteria": _texts(point.get("criteria"), limits["criterion"], context_docs.MAX_CRITERIA),
        "scopes": _texts(point.get("scopes"), limits["scope"], context_docs.MAX_SCOPES),
        "priority": _text(point.get("priority"), limits["priority"]),
        "summary": _text(point.get("summary"), limits["summary"]),
        "fix_hint": _text(point.get("fix_hint"), limits["fix_hint"]),
        "owner": _owner(point),
        "elements": _texts(point.get("elements"), limits["element"], context_docs.MAX_ELEMENTS),
    }
