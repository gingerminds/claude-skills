"""Synthesis of a fix pass for the lead, and the short message for the team channel.

Everything here is pure: the caller reads the run files and passes plain values. Texts are in
French and never hold a long dash.
"""
from typing import Optional

from cqa import fix_mr, fix_rows, fix_state
from cqa.checklist import ref_label

NO_REASON = "Raison non précisée"
NO_TITLE = "sans titre"
NO_MR = "Aucune MR créée."
NO_CHECK = "Aucune vérification en attente."
NOTHING_PREPARED = "Aucun correctif préparé."
EMPTY_BRANCH = " (aucun commit)"
LEFT_KEY = "- {repo} : {reason} (branche {branch}, worktree {worktree})."
NO_BRANCH = "aucune"
NO_WORKTREE = "aucun"
PARTIAL = "partielle, reste à faire : {remaining}"
SUITE = ("Relire et ajuster les MR, les fusionner et déployer en préprod, puis lancer "
         "`/gm:checklist-quality-audit recette` : elle reprend d'elle-même les lignes « En review ». "
         "Une ligne restée à un autre statut dans Notion est à passer d'abord en « Mis en place ».")
# The state of a row of a key left to the lead, by outcome (a reason follows when it has one).
ROW_STATES = {
    "pending": "non traitée", "fixed": "corrigée sur la branche", "skipped": "écartée",
    "dropped": "retirée", "question": "question sans réponse", "done_before": "déjà sur la branche",
}
_LEFT_OUTCOMES = ("skipped", "dropped", "question")
_RAN = ("passed", "failed")


def summary(*, project: str, state: dict, candidates: dict, checks: dict,
            replace_failed: list = (), in_review: Optional[list] = None) -> dict:
    """Return {"text", "team_message"}, both headed by the project name.

    state: fix/state.json; candidates: fix/candidates.json; checks: {key name: checks.json
    document or None}; replace_failed: refs whose skill comment could not be replaced;
    in_review: refs the Notion stage moved to « En review », counted only when given (None:
    no line). Partial rows are marked with what is left to do; a
    failed section (with its reason and its leftover patch) and a reorder conflict are named.
    A key left to the lead (fix_state.is_left) has its own section, with every row of the key
    and its state: no MR block, no branch line and no row of it anywhere else.
    """
    info = _titles(candidates)
    keys = state["keys"]
    parts = [("MR", _mr_section(keys, info))]
    notes = _attention_lines(keys)
    if notes:
        parts.append(("Points d'attention", "\n".join(notes)))
    left_keys = _left_keys(keys, info)
    if left_keys:
        parts.append(("Clés laissées au lead", "\n".join(_left_key_lines(left_keys))))
    left = _left(keys, candidates, info)
    if left:
        parts.append(("Laissées aux devs", left))
    parts.append(("Vérifications à faire par le relecteur", _checks_section(keys, checks)))
    local = _local_lines(state)
    if local:
        parts.append(("Branches restées en local", "\n".join(local)))
    notion = _notion_lines(replace_failed, in_review)
    if notion:
        parts.append(("Notion", "\n".join(notion)))
    parts.append(("Suite", SUITE))
    text = f"# {project} : synthèse de la passe de correction\n\n" + "\n\n".join(
        f"## {name}\n\n{body}" for name, body in parts) + "\n"
    return {"text": _clean(text), "team_message": _clean(_team_message(project, state)),
            "left_keys": left_keys}


_is_left = fix_state.is_left


def _left_keys(keys: dict, info: dict) -> list:
    """Return the keys left to the lead, in the order of the pass, each with its reason, the
    time it was left, its branch, its worktree and its rows ({"ref", "title", "state"})."""
    return [
        {"key": name, "reason": (key["left"].get("reason") or "").strip() or NO_REASON,
         "at": key["left"].get("at"), "branch": key.get("branch"), "worktree": key.get("worktree"),
         "rows": [_row_state(int(ref), row, info)
                  for ref, row in sorted(key["rows"].items(), key=lambda item: int(item[0]))
                  if isinstance(row, dict)]}
        for name, key in keys.items() if _is_left(key)
    ]


def _row_state(ref: int, row: dict, info: dict) -> dict:
    state = ROW_STATES.get(row.get("outcome")) or str(row.get("outcome"))
    reason = (row.get("reason") or "").strip()
    if reason and row.get("outcome") in ("skipped", "dropped"):
        state = f"{state} : {reason}"
    return {"ref": ref, "title": (info.get(ref) or {}).get("title") or NO_TITLE, "state": state}


def _left_key_lines(left_keys: list) -> list:
    lines = []
    for item in left_keys:
        lines.append(LEFT_KEY.format(repo=item["key"], reason=item["reason"],
                                     branch=item["branch"] or NO_BRANCH,
                                     worktree=item["worktree"] or NO_WORKTREE))
        lines.extend(f"  - {ref_label(row['ref'])} : {row['title']} : {row['state']}"
                     for row in item["rows"])
    return lines


def _clean(text: str) -> str:
    return text.replace("\u2014", "-")


def _titles(candidates: dict) -> dict:
    """Return {ref: candidate row}, the first row seen for each ref."""
    found = {}
    for entry in candidates.get("keys") or []:
        for row in fix_rows.key_rows(entry):
            if isinstance(row.get("ref"), int):
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
        if not mr or _is_left(key):
            continue
        categories = ", ".join(section.get("category") or section.get("alias") or ""
                               for section in key.get("sections") or [])
        lines = [f"### {key['repo']} : {categories or 'sans catégorie'}", "",
                 f"Lien : {mr.get('url')}", ""]
        fixed = _refs(key, "fixed", "done_before")
        covered = fix_mr.covered_refs(key["rows"])
        waiting = _refs(key, "pending", "question")
        remaining = {ref: fix_mr.remaining_of(key["rows"][str(ref)]) for ref in fixed}
        partial = sum(1 for text in remaining.values() if text)
        count = f"Lignes mises en MR : {len(fixed) + len(covered)}"
        if partial:
            count += f", dont {partial} {'partielle' if partial == 1 else 'partielles'}"
        lines += [f"{count}.", "", "Lignes corrigées :"]
        for ref in fixed:
            suffix = " (Semi-auto)" if (info.get(ref) or {}).get("semi_auto") else ""
            if remaining[ref]:
                suffix += f" ({PARTIAL.format(remaining=remaining[ref])})"
            lines.append(_line(ref, info, suffix))
        lines.extend(
            _line(ref, info, f" ({fix_mr.COVERED.format(ref=ref_label(by))})")
            for ref, by in sorted(covered.items())
        )
        if not fixed and not covered:
            lines.append("- aucune")
        if waiting:
            lines += ["", "Lignes en attente :"]
            lines.extend(_line(ref, info) for ref in waiting)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else NO_MR


def _attention_lines(keys: dict) -> list:
    """Return the failed sections of every key, with their reason and the patch of the
    changes their fixer left uncommitted, then the commits a reorder left in place."""
    lines = []
    for key in keys.values():
        for section in key.get("sections") or []:
            if section.get("state") != "failed":
                continue
            name = section.get("category") or section.get("alias")
            line = f"- {key['repo']} : section {name} non aboutie : {_sentence(section.get('reason'))}"
            leftover = section.get("leftover")
            patch = leftover.get("patch") if isinstance(leftover, dict) else None
            if patch:
                line += f" Modifications non commitées sauvées dans {patch}."
            lines.append(line)
        conflicts = [subject for subject in key.get("reorder_conflicts") or []
                     if isinstance(subject, str) and subject.strip()]
        if conflicts:
            names = ", ".join(f"`{subject}`" for subject in conflicts)
            lines.append(f"- {key['repo']} : {fix_mr.REORDER_CONFLICT} ({names}).")
    return lines


def _sentence(text) -> str:
    text = text.strip() if isinstance(text, str) and text.strip() else NO_REASON
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _notion_lines(replace_failed, in_review) -> list:
    lines = []
    if in_review is not None:
        refs = sorted(set(in_review))
        listed = f" ({', '.join(ref_label(ref) for ref in refs)})" if refs else ""
        lines.append(f"Lignes passées « En review » : {len(refs)}{listed}.")
    if replace_failed:
        names = ", ".join(ref_label(ref) for ref in replace_failed)
        lines.append(f"Commentaire du skill non remplacé, un second a été créé : {names}")
    return lines


def _left(keys: dict, candidates: dict, info: dict) -> str:
    groups = {}
    for entry in candidates.get("left") or []:
        if isinstance(entry, dict) and isinstance(entry.get("ref"), int):
            groups.setdefault(entry.get("reason") or NO_REASON, []).append(
                f"- {ref_label(entry['ref'])} : {entry.get('title') or NO_TITLE}")
    for key in keys.values():
        if _is_left(key):
            continue
        covered = fix_mr.covered_refs(key["rows"])
        for ref in _refs(key, *_LEFT_OUTCOMES):
            if ref in covered:
                continue
            reason = key["rows"][str(ref)].get("reason") or NO_REASON
            groups.setdefault(reason, []).append(_line(ref, info))
    return "\n\n".join(f"{reason} :\n" + "\n".join(lines) for reason, lines in groups.items())


def _checks_section(keys: dict, checks: dict) -> str:
    seen, lines = set(), []
    for name, key in keys.items():
        document = checks.get(name) if key.get("mr") and not _is_left(key) else None
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


def _local_keys(state: dict) -> list:
    """Return [(key, empty)] for the branches left in the dev's repo; empty marks a branch
    that publish found without any commit (« Aucun commit sur la branche »). A key left to
    the lead is not a prepared fix: its own section names its branch."""
    everything = state.get("push") in ("local", "dry_run")
    found = []
    for key in state["keys"].values():
        if not key.get("branch") or key.get("branch_deleted") or _is_left(key):
            continue
        empty = bool(key.get("empty_branch")) and not key.get("mr")
        if everything or key.get("kept_local") or not key.get("push_ready") or empty:
            found.append((key, empty))
    return found


def _local_lines(state: dict) -> list:
    return [f"- {key['repo']} : {key['branch']}{EMPTY_BRANCH if empty else ''}"
            for key, empty in _local_keys(state)]


def _team_message(project: str, state: dict) -> str:
    """Return the message for the team: the project, the merge requests to review (or the
    local branches), then the count of the keys left to the lead, whose merge request, if
    any, is not to be reviewed."""
    urls = [key["mr"]["url"] for key in state["keys"].values()
            if key.get("mr") and not _is_left(key)]
    if urls:
        lines = [project,
                 f"Correctifs automatiques de la checklist PRE-MEL : {len(urls)} MR en brouillon à relire.",
                 *urls]
    else:
        count = sum(1 for _, empty in _local_keys(state) if not empty)
        if not count:
            lines = [project, NOTHING_PREPARED]
        else:
            word = "branche" if count == 1 else "branches"
            lines = [project, f"Correctifs préparés en branches locales : {count} {word}."]
    left = [name for name, key in state["keys"].items() if _is_left(key)]
    if left:
        plural = "s" if len(left) > 1 else ""
        lines.append(f"{len(left)} repo{plural} laissé{plural} au lead : {', '.join(left)}.")
    return "\n".join(lines)
