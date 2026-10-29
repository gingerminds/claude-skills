"""Synthesis of a fix pass for the lead, and the short message for the team channel.

Everything here is pure: the caller reads the run files and passes plain values. Texts are in
French and never hold a long dash.
"""
from typing import Optional

from cqa.checklist import ref_label

NO_REASON = "Raison non précisée"
NO_TITLE = "sans titre"
NO_MR = "Aucune MR créée."
NO_CHECK = "Aucune vérification en attente."
SUITE = ("Relire et ajuster les MR, les fusionner, déployer en préprod, passer les lignes en "
         "« Mis en place » dans Notion, puis lancer `/gm:checklist-quality-audit recette`.")
_LEFT_OUTCOMES = ("skipped", "dropped", "question")
_RAN = ("passed", "failed")


def summary(*, project: str, state: dict, candidates: dict, checks: dict,
            replace_failed: list = ()) -> dict:
    """Return {"text", "team_message"}.

    state: fix/state.json; candidates: fix/candidates.json; checks: {key name: checks.json
    document or None}; replace_failed: refs whose skill comment could not be replaced.
    """
    info = _titles(candidates)
    keys = state["keys"]
    parts = [("MR", _mr_section(keys, info))]
    left = _left(keys, candidates, info)
    if left:
        parts.append(("Laissées aux devs", left))
    parts.append(("Vérifications à faire par le relecteur", _checks_section(keys, checks)))
    local = _local_lines(state)
    if local:
        parts.append(("Branches restées en local", "\n".join(local)))
    if replace_failed:
        names = ", ".join(ref_label(ref) for ref in replace_failed)
        parts.append(("Notion", f"Commentaire du skill non remplacé, un second a été créé : {names}"))
    parts.append(("Suite", SUITE))
    text = "# Synthèse de la passe de correction\n\n" + "\n\n".join(
        f"## {name}\n\n{body}" for name, body in parts) + "\n"
    return {"text": _clean(text), "team_message": _clean(_team_message(project, state, local))}


def _clean(text: str) -> str:
    return text.replace("\u2014", "-")


def _titles(candidates: dict) -> dict:
    """Return {ref: candidate row}, the first row seen for each ref."""
    found = {}
    for entry in candidates.get("keys") or []:
        for row in (entry.get("rows") or []) if isinstance(entry, dict) else []:
            if isinstance(row, dict) and isinstance(row.get("ref"), int):
                found.setdefault(row["ref"], row)
    return found


def _line(ref: int, info: dict, suffix: str = "") -> str:
    title = (info.get(ref) or {}).get("title") or NO_TITLE
    return f"- {ref_label(ref)} : {title}{suffix}"


def _refs(key: dict, *outcomes: str) -> list:
    return sorted(int(ref) for ref, row in key["rows"].items() if row.get("outcome") in outcomes)


def _mr_section(keys: dict, info: dict) -> str:
    blocks = []
    for key in keys.values():
        mr = key.get("mr")
        if not mr:
            continue
        lines = [f"### {key['repo']} : {key.get('category') or 'sans catégorie'}", "",
                 f"Lien : {mr.get('url')}", ""]
        fixed = _refs(key, "fixed", "done_before")
        waiting = _refs(key, "pending", "question")
        lines.append("Lignes corrigées :")
        lines.extend(
            _line(ref, info, " (Semi-auto)" if (info.get(ref) or {}).get("semi_auto") else "")
            for ref in fixed
        ) if fixed else lines.append("- aucune")
        if waiting:
            lines += ["", "Lignes en attente :"]
            lines.extend(_line(ref, info) for ref in waiting)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else NO_MR


def _left(keys: dict, candidates: dict, info: dict) -> str:
    groups = {}
    for entry in candidates.get("left") or []:
        if isinstance(entry, dict) and isinstance(entry.get("ref"), int):
            groups.setdefault(entry.get("reason") or NO_REASON, []).append(
                f"- {ref_label(entry['ref'])} : {entry.get('title') or NO_TITLE}")
    for key in keys.values():
        for ref in _refs(key, *_LEFT_OUTCOMES):
            reason = key["rows"][str(ref)].get("reason") or NO_REASON
            groups.setdefault(reason, []).append(_line(ref, info))
    return "\n\n".join(f"{reason} :\n" + "\n".join(lines) for reason, lines in groups.items())


def _checks_section(keys: dict, checks: dict) -> str:
    seen, lines = set(), []
    for name, key in keys.items():
        document = checks.get(name) if key.get("mr") else None
        if not isinstance(document, dict):
            continue
        entries = [(item.get("name"), None) for item in document.get("checks") or []
                   if isinstance(item, dict) and item.get("status") not in _RAN]
        entries += [(item.get("name"), item.get("command")) for item in document.get("not_run") or []
                    if isinstance(item, dict)]
        for label, command in entries:
            if (label, command) not in seen:
                seen.add((label, command))
                lines.append(f"- {label} : `{command}`" if command else f"- {label}")
    return "\n".join(lines) if lines else NO_CHECK


def _local_lines(state: dict) -> list:
    everything = state.get("push") in ("local", "dry_run")
    return [
        f"- {key['repo']} : {key['branch']}"
        for key in state["keys"].values()
        if key.get("branch") and (everything or key.get("kept_local") or not key.get("push_ready"))
    ]


def _team_message(project: str, state: dict, local: list) -> str:
    urls = [key["mr"]["url"] for key in state["keys"].values() if key.get("mr")]
    if urls:
        return "\n".join([
            project,
            f"Correctifs automatiques de la checklist PRE-MEL : {len(urls)} MR en brouillon à relire.",
            *urls,
        ])
    return f"{project}\nCorrectifs préparés en branches locales : {len(local)} branches."
