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
from cqa import deferred
from cqa.comments import format_page_summary

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
_TOOL_STATUS = {"ok": "ok", "failed": "en échec", "skipped": "ignoré"}
_NO_CATEGORY = "Sans catégorie"
_NO_RESULT = "result.json absent ou illisible"
_UNKNOWN_STATUS = "statut inconnu"
_UNKNOWN_WITHOUT_TEXT = "voir le commentaire déjà présent dans Notion"
_CONSTAT = "Constat : "
_HUMAN_SECTION = "Statuts posés par un humain et contredits par l'audit"
_IDLE_LIMIT = timedelta(minutes=30)
_DEFERRED_KINDS = {
    "login": "connexion",
    "ssh_refused": "commande SSH refusée",
    "gap": "écart",
    "question": "question",
}
_EMPTY = {
    "Décompte par statut": "Aucun.",
    _HUMAN_SECTION: "Aucun.",
    "Écarts relevés": "Aucun.",
    "Outils": "Aucun outil lancé.",
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
    crashed tool does not go unnoticed. toolbox_version is the first one the results carry.
    """
    tools_dir = Path(run_dir) / "tools"
    folders = sorted(tools_dir.iterdir()) if tools_dir.is_dir() else []
    results = [_tool_result(folder) for folder in folders if folder.is_dir()]
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
        f"- Durée active de l'audit : {_active_duration(run_dir, start, now)}",
    ]
    sections = [
        ("Couverture", _coverage_lines(lots["coverage"])),
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
        lines += [f"## {title}", ""]
        lines += body or [_EMPTY.get(title, "Aucune.")]
        lines.append("")
    return "\n".join(lines)


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
    return text.splitlines() if text else []


def _deferred_lines(run_dir: Path) -> list:
    """One bullet per entry of deferred.json, open or resolved."""
    lines = []
    for item in deferred.all_items(run_dir):
        kind = _DEFERRED_KINDS.get(item.get("kind"), item.get("kind") or "décision")
        refs = ", ".join(f"CQ-{ref}" for ref in item.get("refs") or [] if isinstance(ref, int))
        outcome = "traitée" if item.get("resolved") else "ouverte"
        where = f" ({refs})" if refs else ""
        lines.append(f"- {kind}{where} : {_line(item.get('detail'))} ({outcome})")
    return lines


def _active_duration(run_dir: Path, start: datetime, end: datetime) -> str:
    """Active time of the run: the gaps shorter than 30 minutes between file modification dates.

    Falls back to the elapsed time when no file of the run was modified between start and end.
    """
    if end < start:
        return _duration_text(0, measured=False)
    dates = []
    for path in Path(run_dir).rglob("*"):
        try:
            if path.is_file():
                dates.append(datetime.fromtimestamp(path.stat().st_mtime))
        except OSError:
            continue
    dates = sorted(date for date in dates if start <= date <= end)
    if not dates:
        return _duration_text(int((end - start).total_seconds() // 60))
    points = [start, *dates, end]
    active = sum(
        (later - earlier for earlier, later in zip(points, points[1:]) if later - earlier < _IDLE_LIMIT),
        timedelta(),
    )
    return _duration_text(int(active.total_seconds() // 60))


def _duration_text(minutes: int, measured: bool = True) -> str:
    if not measured:
        return "non mesurée"
    if minutes < 1:
        return "moins d'une minute"
    hours, rest = divmod(minutes, 60)
    return f"{minutes} min" if not hours else f"{hours} h {rest:02d} min"


def _coverage_lines(coverage: dict) -> list:
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
    gains = coverage.get("gains") or []
    if gains:
        lines.append("- Outils à ajouter pour couvrir plus de lignes :")
        for gain in gains:
            noun = "ligne" if gain["rows"] == 1 else "lignes"
            lines.append(f"  - {gain['tool']} : {gain['rows']} {noun} ({gain['action']})")
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
    for line in text.splitlines():
        if line.startswith(_CONSTAT):
            return line[len(_CONSTAT) :].strip() or None
    return None


def _gap_lines(context: dict) -> list:
    lines = []
    for repo in context["repos"]:
        if repo.get("aligned") is False:
            deployed = (repo.get("deployed_sha") or "")[:7]
            seen = f"déployé : {deployed}" if deployed else "commit déployé inconnu"
            lines.append(
                f"- Préprod en retard sur develop pour le repo « {repo['name']} » "
                f"({seen}, audité : {repo['audited_sha'][:7]})."
            )
    return lines


def _tool_lines(tools: dict) -> list:
    lines = []
    for item in tools["tools"]:
        status = item.get("status")
        label = _TOOL_STATUS.get(status, status) if isinstance(status, str) and status else None
        label = label or _UNKNOWN_STATUS
        reason = _line(item.get("reason"))
        lines.append(f"- {item['tool']} : {label}" + (f" ({reason})" if reason else ""))
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
        return result
    return {"tool": folder.name, "status": "failed", "reason": _NO_RESULT}


def _read(run_dir: Path, relative: str, hint: str):
    path = run_dir / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{relative} est introuvable dans {run_dir} : lancer d'abord {hint}.")
    except (OSError, ValueError) as error:
        raise ValueError(f"{relative} est illisible : {error}")
