"""Reports of a run: the manifest of the toolbox results and the French synthesis of the audit."""
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from cqa.checklist import (
    STATUS_IMPLEMENTED,
    STATUS_NA,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_STANDBY,
    STATUS_TODO,
    ref_label,
)
from cqa import context_docs, deferred, docs_link, docs_verdicts
from cqa.lots import lot_document_refs
from cqa.comments import format_page_summary
from cqa.secrets import known_values_from_env, redact

_EM_DASH = chr(0x2014)
_RUN_ID = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})")
_STATUS_ORDER = (
    STATUS_TODO,
    STATUS_PARTIAL,
    STATUS_IMPLEMENTED,
    STATUS_OK,
    STATUS_NA,
    STATUS_STANDBY,
)
_TOOL_STATUS = {
    "ok": "ok",
    "failed": "en échec",
    "skipped": "ignoré",
    "not_applicable": "non applicable",
}
_NO_CATEGORY = "Sans catégorie"
_NO_RESULT = "result.json absent ou illisible"
_SET_ASIDE = ".prev"
_LAST_ATTEMPT = "result-last-attempt.json"
_UNKNOWN_STATUS = "statut inconnu"
_UNKNOWN_WITHOUT_TEXT = "voir le commentaire déjà présent dans Notion"
_CONSTAT = "Constat :"
_HUMAN_SECTION = "Statuts posés par un humain et contredits par l'audit"
_DOCUMENTS_SECTION = "Documents de contexte"
_MAX_UNLINKED = 30
_IDLE_LIMIT = timedelta(minutes=30)
_DEFERRED_KINDS = {
    "login": "connexion",
    "ssh_refused": "commande SSH refusée",
    "gap": "écart",
    "question": "question",
    "fix_question": "question de correctif",
}
_EMPTY = {
    "Décompte par statut": "Aucun.",
    _HUMAN_SECTION: "Aucun.",
    "Écarts relevés": "Aucun.",
    "Outils": "Aucun outil lancé.",
    "Outils non applicables": "Aucun.",
}


def run_start(run_id) -> datetime:
    """Return the start of a run from its id (AAAA-MM-JJ-HHMM), or raise ValueError."""
    found = _RUN_ID.fullmatch(run_id) if isinstance(run_id, str) else None
    try:
        if found:
            return datetime(*(int(part) for part in found.groups()))
    except ValueError:
        pass
    raise ValueError("context.json : « run_id » doit avoir la forme AAAA-MM-JJ-HHMM.")


def manifest(run_dir: Path) -> dict:
    """Return {"toolbox_version", "tools"}: the result.json of every tool folder, by tool name.

    A tool folder whose result.json is missing or unreadable is reported as failed, so that a
    crashed tool does not go unnoticed. toolbox_version is the first one the results carry. A
    folder the launcher set aside (<tool>.prev) is not a tool. A tool whose last attempt failed
    while its previous result was kept (result-last-attempt.json) carries it as "last_attempt".
    """
    tools_dir = Path(run_dir) / "tools"
    folders = sorted(tools_dir.iterdir()) if tools_dir.is_dir() else []
    results = [
        _tool_result(folder)
        for folder in folders
        if folder.is_dir() and not folder.name.endswith(_SET_ASIDE)
    ]
    versions = (item.get("toolbox_version") for item in results)
    return {"toolbox_version": next((item for item in versions if item), None), "tools": results}


def synthesis(run_dir: Path, *, now: Optional[datetime] = None) -> tuple[str, str]:
    """Return (synthese.md text, comment of the copy page) for a planned run.

    Both are in French, and the page comment fits the Notion limit. now is the end of the audit
    for its duration (the current time when omitted). Raises ValueError when a file of the run
    is missing or does not have the expected structure.
    """
    run_dir = Path(run_dir)
    context = _read(run_dir, "context.json", "le diagnostic")
    plan = _read(run_dir, "notion/plan.json", "cqa_plan.py plan")
    lots = _read(run_dir, "lots.json", "cqa_plan.py lots")
    rows = _read(run_dir, "rows.json", "cqa_notion.py rows")
    try:
        text = _synthesis(
            context, plan, lots, rows, manifest(run_dir), now or datetime.now(), run_dir
        )
        start = run_start(context["run_id"])
        comment = format_page_summary(
            summary=plan["summary"], run_date=start.date(), lead_option=context["lead"]["option"]
        )
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError(
            f"Synthèse impossible : un fichier du run est incomplet ou invalide ({error!r})."
        ) from None
    return text.replace(_EM_DASH, "-"), comment


def _synthesis(context, plan, lots, rows, tools, now, run_dir) -> str:
    start = run_start(context["run_id"])
    summary = plan["summary"]
    row_list = rows["rows"]
    titles = {row["ref"]: _line(row.get("title")) for row in row_list if row.get("ref") is not None}
    after = {state["ref"]: state["after"] for state in plan["rows"] if state.get("ref") is not None}

    header = [
        f"- Projet : {context['project']['name']}",
        f"- Run : {context['run_id']}, lancé par {context['lead']['option']}",
        _versions(context, tools),
        _duration_line(run_dir, start, now),
    ]
    sections = [
        ("Couverture", _coverage_lines(lots["coverage"], context.get("category_filter"))),
        ("Documents de contexte", _document_lines(run_dir, context, lots)),
        ("Décompte par statut", _status_lines(summary["by_status_after"])),
        ("Décompte par catégorie", _category_lines(plan["rows"], row_list)),
        (
            "Lignes bloquantes non conformes",
            [_ref_line(ref, titles, after.get(ref)) for ref in summary["blocking_non_conform"]],
        ),
        ("Lignes non évaluées", _unknown_lines(summary["unknown"], titles, lots, plan)),
        (
            _HUMAN_SECTION,
            [
                _ref_line(item["ref"], titles, _override_detail(item))
                for item in summary["human_overrides"]
            ],
        ),
        (
            "Lignes reportées à J0 / J+7",
            [_ref_line(ref, titles) for ref in summary["deferred"]],
        ),
        (
            "Lignes du modèle absentes de la copie",
            [_missing_line(item) for item in summary["missing_in_copy"]],
        ),
        ("Écarts relevés", _gap_lines(context)),
        ("Outils", _tool_lines(tools)),
        ("Outils non applicables", _not_applicable_lines(tools)),
    ]
    deferred = _deferred_lines(run_dir)
    if deferred:
        sections.append(("Décisions mises de côté", deferred))
    remarks = _remarks(run_dir)
    if remarks:
        sections.append(("Remarques", remarks))
    recette = plan.get("recette")
    if isinstance(recette, dict):
        position = [title for title, _ in sections].index("Écarts relevés")
        sections.insert(
            position, ("Recette", _recette_lines(recette, summary, titles, after))
        )
    lines = ["# Synthèse de l'audit PRE-MEL", "", *header, ""]
    for title, body in sections:
        if title == _DOCUMENTS_SECTION and not body:
            continue
        lines += [f"## {title}", ""]
        lines += body or [_EMPTY.get(title, "Aucune.")]
        lines.append("")
    return "\n".join(lines)


def _document_lines(run_dir: Path, context: dict, lots: dict) -> list:
    """Lines of the context documents section: what each holds and what became of its points.

    Empty without any document. Everything is read from the files of the run and tolerated
    when damaged: a document the lead never validated, or whose items are gone, is said so.
    """
    entries = context_docs.read_index(run_dir)["docs"]
    if not entries:
        return []
    links = docs_link.read_links(run_dir)
    verdicts = docs_verdicts.read_verdicts(run_dir)
    listed = lots.get("lots") if isinstance(lots, dict) else None
    judged = set()
    for lot in listed if isinstance(listed, list) else []:
        judged.update(lot_document_refs(lot))
    autonomous = context.get("mode") == "autonome"
    lines: list = []
    for entry in entries:
        lines += _document_entry(run_dir, entry, links, verdicts, judged, autonomous)
    return lines


def _document_entry(
    run_dir, entry: dict, links: dict, verdicts: dict, judged: set, autonomous: bool
) -> list:
    doc_id = _plain(entry.get("id")) or "?"
    if entry.get("status") != "imported":
        state = "ignoré" if entry.get("status") == "ignored" else "non importé"
        note = _plain(entry.get("note"))
        return [f"- {doc_id} : {state}" + (f" ({note})" if note else "")]
    items = context_docs.read_items(run_dir, doc_id)
    if items is None:
        return [f"- {doc_id} : importé, mais ses points sont illisibles"]
    meta = items["document"]
    label = _plain(meta.get("label")) or doc_id
    date = _plain(meta.get("date")) or "date inconnue"
    kind = _plain(meta.get("kind"))
    lines = [f"- {doc_id} : « {label} » ({date})" + (f", {kind}" if kind else "")]
    points = [
        point for point in items["items"] if isinstance(point, dict) and isinstance(point.get("id"), str)
    ]
    ko = sum(1 for point in points if point.get("verdict") == "ko")
    info = sum(1 for point in points if point.get("verdict") == "info")
    found = [
        f"{ko} non conforme{'s' if ko > 1 else ''}" if ko else "",
        f"{info} remarque{'s' if info > 1 else ''}" if info else "",
    ]
    names = links.get("categories", {}).get(doc_id) if links else None
    if names is not None:
        lines.append(f"  - Catégories : {', '.join(_plain(name) for name in names) or 'aucune'}")
    lines.append(f"  - Points du document : {', '.join(item for item in found if item) or 'aucun'}")
    if names is None:
        lines.append("  - Rattachement non validé : aucun verdict repris de ce document")
        return lines
    attached = {
        point["id"]: links["links"][point["id"]]
        for point in points
        if links["links"].get(point["id"], {}).get("refs")
    }
    proposed = [point_id for point_id, link in attached.items() if link["how"] == "proposed"]
    lines.append(
        f"  - Rattachés à une ligne : {len(attached)} "
        f"(dont {len(proposed)} proposé{'s' if len(proposed) > 1 else ''} par le skill)"
    )
    loose = [point for point in points if point["id"] not in attached]
    lines.append(f"  - Sans ligne : {len(loose)}")
    for point in loose[:_MAX_UNLINKED]:
        source = _plain(point.get("source"))
        lines.append(
            f"    - {_plain(point['id'])} : {_plain(point.get('summary'))}"
            + (f" ({source})" if source else "")
        )
    if len(loose) > _MAX_UNLINKED:
        lines.append(f"    - et {len(loose) - _MAX_UNLINKED} autres")
    rows = [
        f"{ref_label(ref)} : {verdicts[ref]['verdict']}"
        for ref in sorted(judged)
        if ref in verdicts and doc_id in _evidence_docs(verdicts[ref]["finding"])
    ]
    lines.append(
        "  - Lignes jugées d'après le document : "
        + (f"{len(rows)} ({', '.join(rows)})" if rows else "aucune")
    )
    if autonomous and proposed:
        lines.append("  - Rattachements proposés par le skill, acceptés en mode autonome :")
        for point_id in proposed:
            refs = ", ".join(ref_label(ref) for ref in attached[point_id]["refs"])
            lines.append(f"    - {_plain(point_id)} : {refs}")
    return lines


def _evidence_docs(finding: dict) -> set:
    evidence = finding.get("evidence") if isinstance(finding, dict) else None
    return {
        item.get("doc")
        for item in (evidence if isinstance(evidence, list) else [])
        if isinstance(item, dict)
    }


def _plain(value) -> str:
    """A text of a document on one line, with the secrets of the environment redacted."""
    return redact(context_docs.clean_text(value), known_values_from_env())


def _recette_lines(recette: dict, summary: dict, titles: dict, after: dict) -> list:
    """Lines of the recette section from the "recette" key of the plan."""
    judged = [ref for ref in recette.get("judged") or [] if isinstance(ref, int)]
    unjudged = [ref for ref in recette.get("unjudged") or [] if isinstance(ref, int)]
    refs = sorted(set(judged) | set(unjudged))
    validated, waiting, refused, other, unknown, kept = [], [], [], [], [], []
    held = {
        item["ref"]: item
        for item in recette.get("kept") or []
        if isinstance(item, dict) and isinstance(item.get("ref"), int)
    }
    for ref in refs:
        status = after.get(ref)
        if ref in unjudged or ref in summary["unknown"]:
            unknown.append(_ref_line(ref, titles))
        elif ref in held:
            item = held[ref]
            kept.append(
                _ref_line(
                    ref,
                    titles,
                    f"constat de la recette : {item.get('verdict')}, "
                    f"statut conservé « {item.get('status')} »",
                )
            )
        elif status == STATUS_OK:
            validated.append(_ref_line(ref, titles))
        elif status == STATUS_IMPLEMENTED:
            waiting.append(_ref_line(ref, titles))
        elif status in (STATUS_TODO, STATUS_PARTIAL):
            refused.append(_ref_line(ref, titles, status))
        else:
            other.append(_ref_line(ref, titles, status or _UNKNOWN_STATUS))
    blocking = [_ref_line(ref, titles, after.get(ref)) for ref in summary["blocking_non_conform"]]
    groups = [
        (f"Validées ({STATUS_OK})", validated),
        ("Restées en validation humaine", waiting),
        ("Refusées", refused),
        ("Statut humain conservé", kept),
        ("Non évaluées", unknown),
        ("Bloquantes restantes", blocking),
    ]
    if other:
        groups.append(("Autres", other))
    lines = [f"{len(refs)} ligne(s) recettée(s).", ""]
    for title, body in groups:
        lines += [f"### {title}", ""]
        lines += body or ["Aucune."]
        lines.append("")
    return lines[:-1]


def _override_detail(item: dict) -> str:
    return f"« {item['before']} » posé par un humain, l'audit propose « {item['after']} »"


def _versions(context: dict, tools: dict) -> str:
    line = f"- Version du skill : {context['plugin_version']}"
    if tools["toolbox_version"]:
        line += f" (boîte à outils {tools['toolbox_version']})"
    return line


def _remarks(run_dir: Path) -> list:
    """Lines of remarques.md, the remarks the orchestrator writes by hand (empty when none)."""
    try:
        text = (run_dir / "remarques.md").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise ValueError(f"remarques.md est illisible : {error}") from None
    lines = redact(text, known_values_from_env()).splitlines()
    return [f"**{line.lstrip('#').strip()}**" if line.lstrip().startswith("#") else line for line in lines]


def _deferred_lines(run_dir: Path) -> list:
    """One bullet per entry of deferred.json, open or resolved."""
    lines = []
    for item in deferred.all_items(run_dir):
        kind = _DEFERRED_KINDS.get(item.get("kind"), item.get("kind") or "décision")
        refs = ", ".join(f"CQ-{ref}" for ref in item.get("refs") or [] if isinstance(ref, int))
        outcome = "traitée" if item.get("resolved") else "ouverte"
        where = f" ({refs})" if refs else ""
        lines.append(f"- {kind}{where} : {_line(redact(str(item.get('detail')), known_values_from_env()))} ({outcome})")
    return lines


def _duration_line(run_dir: Path, start: datetime, end: datetime) -> str:
    """Duration line of the header: the active time, or the elapsed time when dates are unusable."""
    active = _active_duration(run_dir, start, end)
    if active is not None:
        return f"- Durée active de l'audit : {_duration_text(active)}"
    if end < start:
        return "- Durée de l'audit : non mesurée"
    elapsed = _duration_text(int((end - start).total_seconds() // 60))
    return f"- Durée de l'audit : {elapsed} (durée écoulée, dates de fichiers indisponibles)"


def _active_duration(run_dir: Path, start: datetime, end: datetime) -> Optional[int]:
    """Active minutes: the gaps shorter than 30 minutes between file modification dates.

    Returns None when end is before start or no file date lies between start and end.
    """
    if end < start:
        return None
    dates = []
    for path in Path(run_dir).rglob("*"):
        try:
            if path.is_file():
                dates.append(datetime.fromtimestamp(path.stat().st_mtime))
        except OSError:
            continue
    dates = sorted(date for date in dates if start <= date <= end)
    if not dates:
        return None
    points = [start, *dates, end]
    active = sum(
        (later - earlier for earlier, later in zip(points, points[1:]) if later - earlier < _IDLE_LIMIT),
        timedelta(),
    )
    return int(active.total_seconds() // 60)


def _duration_text(minutes: int) -> str:
    if minutes < 1:
        return "moins d'une minute"
    hours, rest = divmod(minutes, 60)
    return f"{minutes} min" if not hours else f"{hours} h {rest:02d} min"


def _coverage_lines(coverage: dict, categories: Optional[list] = None) -> list:
    """Lines of the coverage section; the last one names the rows left out of the scope."""

    def count(key):
        return coverage.get(key, 0)

    lines = [
        f"- Lignes de la checklist : {count('total')}",
        f"- Évaluables : {count('evaluable')} "
        f"(dont {count('evaluable_human')} avec validation humaine)",
        f"- Partielles (un outil manque) : {count('partial')}",
        f"- Non évaluables (aucun outil disponible) : {count('not_evaluable')}",
        f"- Reportées à J0 / J+7 : {count('deferred')}",
        f"- Non applicables (techno) : {count('na')}",
        f"- En standby : {count('standby')}",
    ]
    if count("documents") > 0:
        lines.append(
            f"- Jugées d'après un document de contexte : {count('documents')} "
            "(comptées dans les évaluables)"
        )
    gains = coverage.get("gains") or []
    if gains:
        lines.append("- Outils à ajouter pour couvrir plus de lignes :")
        for gain in gains:
            noun = "ligne" if gain["rows"] == 1 else "lignes"
            lines.append(f"  - {gain['tool']} : {gain['rows']} {noun} ({gain['action']})")
    outside = count("out_of_scope")
    if outside > 0:
        names = ", ".join(_line(name) for name in categories or [])
        noun = "ligne" if outside == 1 else "lignes"
        detail = f" (catégories : {names})" if names else ""
        lines.append(f"- Hors périmètre : {outside} {noun}{detail}")
    return lines


def _status_lines(counts: dict) -> list:
    return [f"- {status} : {counts[status]}" for status in _ordered(counts) if counts[status]]


def _category_lines(states: list, rows: list) -> list:
    category_of = {row["page_id"]: row.get("category") or _NO_CATEGORY for row in rows}
    by_category = {}
    for state in states:
        if state.get("after"):
            category = category_of.get(state["page_id"], _NO_CATEGORY)
            by_category.setdefault(category, Counter())[state["after"]] += 1
    lines = []
    for category in sorted(by_category):
        counts = by_category[category]
        total = sum(counts.values())
        noun = "ligne" if total == 1 else "lignes"
        detail = ", ".join(f"{status} : {counts[status]}" for status in _ordered(counts))
        lines.append(f"- {category} : {total} {noun} ({detail})")
    return lines


def _ordered(counts) -> list:
    known = [status for status in _STATUS_ORDER if status in counts]
    return known + [status for status in counts if status not in _STATUS_ORDER]


def _unknown_lines(refs: list, titles: dict, lots: dict, plan: dict) -> list:
    reasons = {entry["ref"]: entry["detail"] for entry in lots["excluded"]}
    for op in plan["ops"]:
        if op["kind"] == "comment" and op["verdict"] == "unknown" and op["ref"] not in reasons:
            reasons[op["ref"]] = _constat(op["text"])
    return [_ref_line(ref, titles, reasons.get(ref) or _UNKNOWN_WITHOUT_TEXT) for ref in refs]


def _constat(text: str) -> Optional[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(_CONSTAT):
            continue
        inline = line[len(_CONSTAT) :].strip()
        if inline:
            return inline
        block = []
        for following in lines[index + 1 :]:
            # The footer follows the last block with no blank line.
            if not following.strip() or following.startswith("Réf "):
                break
            block.append(following.strip())
        return " ".join(block) or None
    return None


def _gap_lines(context: dict) -> list:
    lines = []
    for repo in context["repos"]:
        if repo.get("aligned") is False:
            deployed = (repo.get("deployed_sha") or "")[:7]
            audited = repo["audited_sha"][:7]
            if deployed:
                lines.append(
                    f"- Préprod en retard sur develop pour le repo « {repo['name']} » "
                    f"(déployé : {deployed}, audité : {audited})."
                )
            else:
                lines.append(
                    f"- Alignement de la préprod non confirmé pour le repo « {repo['name']} » "
                    f"(audité : {audited})."
                )
    return lines


def _tool_lines(tools: dict) -> list:
    lines = []
    for item in tools["tools"]:
        status = item.get("status")
        if status == "not_applicable":
            continue
        label = _TOOL_STATUS.get(status, status) if isinstance(status, str) and status else None
        label = label or _UNKNOWN_STATUS
        reason = _line(item.get("reason"))
        line = f"- {item['tool']} : {label}" + (f" ({reason})" if reason else "")
        lines.append(line + _last_attempt_text(item.get("last_attempt")))
    return lines


def _last_attempt_text(attempt) -> str:
    if not isinstance(attempt, dict):
        return ""
    status = attempt.get("status")
    label = _TOOL_STATUS.get(status, status) if isinstance(status, str) and status else None
    reason = _line(attempt.get("reason"))
    return (
        f", dernier essai {label or _UNKNOWN_STATUS}"
        + (f" ({reason})" if reason else "")
        + " : le résultat précédent est conservé"
    )


def _not_applicable_lines(tools: dict) -> list:
    lines = []
    for item in tools["tools"]:
        if item.get("status") == "not_applicable":
            reason = _line(item.get("reason"))
            lines.append(f"- {item['tool']}" + (f" ({reason})" if reason else ""))
    return lines


def _ref_line(ref, titles: dict, detail=None) -> str:
    label = ref_label(ref) if isinstance(ref, int) else str(ref)
    title = titles.get(ref)
    line = f"- {label} : {title}" if title else f"- {label}"
    return f"{line} ({detail})" if detail else line


def _missing_line(item) -> str:
    ref, title = item.get("ref"), _line(item.get("title"))
    return f"- {ref_label(ref)} : {title}" if isinstance(ref, int) else f"- {title}"


def _line(value) -> str:
    return " ".join(str(value).split()) if value else ""


def _tool_result(folder: Path) -> dict:
    try:
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = None
    if isinstance(result, dict):
        if not (isinstance(result.get("tool"), str) and result["tool"]):
            result = {**result, "tool": folder.name}
        attempt = _last_attempt(folder)
        return {**result, "last_attempt": attempt} if attempt else result
    return {"tool": folder.name, "status": "failed", "reason": _NO_RESULT}


def _last_attempt(folder: Path) -> Optional[dict]:
    """Return {"status", "reason", "started_at"} of the attempt that failed after a good result."""
    path = folder / _LAST_ATTEMPT
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    if not isinstance(document, dict):
        return {"status": "failed", "reason": _NO_RESULT, "started_at": None}
    return {key: document.get(key) for key in ("status", "reason", "started_at")}


def _read(run_dir: Path, relative: str, hint: str):
    path = run_dir / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{relative} est introuvable dans {run_dir} : lancer d'abord {hint}.")
    except (OSError, ValueError) as error:
        raise ValueError(f"{relative} est illisible : {error}")
