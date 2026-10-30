"""Merge request texts of the fix mode: title, description, additions and the links file.

Everything here is pure: the caller reads the run files and git, and passes plain values.
Titles and descriptions are in French; no attribution line is ever written.
"""
import re
from datetime import date
from typing import Optional

from cqa import fix_names, fix_rows
from cqa.checklist import ref_label
from cqa.report import run_start

NOTION = "https://www.notion.so/"
SUITE = ("Après fusion et déploiement en préprod : passer les lignes en « Mis en place » dans "
         "Notion, puis lancer `/gm:checklist-quality-audit recette`.")
CI = "Pipeline CI de la MR : lint et tests du projet, s'il les lance."
SEMI_AUTO = "**Semi-auto : à valider avec soin.**"
NO_CHECKS = "Non lancé : contrôles de syntaxe et stylelint : aucun résultat pour cette branche"
PREPARED = ("MR préparée par le skill gm:checklist-quality-audit, en brouillon : à relire et "
            "ajuster avant fusion.")
HORS_SCOPE = "Hors-scope"
PARTIAL = "**Partiel.**"
FAILED_SECTION = "Section non aboutie : {reason}"
REORDER_CONFLICT = "Commits non rangés par section : conflit de rejeu"
_NO_REASON = "raison non précisée"
_NO_CATEGORY = "Sans catégorie"
_SHORT_SHA = 8
_HORS_SCOPE_LINE = re.compile(r"^## Hors-scope[ \t]*\r?$", re.MULTILINE)
_TOP_HEADING = re.compile(r"^#{1,2}[ \t]", re.MULTILINE)
_MR_URL = re.compile(r"https?://\S+?/-/merge_requests/([0-9]+)\b")
# The reason of a row skipped because the commit of another row, in an earlier pass, fixed it.
_COVERED_REASON = re.compile(r"^corrigé par le commit de CQ-([0-9]+)(?![0-9])")
COVERED = "couverte par le correctif de {ref}"


def title(count: int, sections: int) -> str:
    """Return the MR title: « Checklist PRE-MEL : correctifs (5 lignes, 2 sections) »."""
    rows = "ligne" if count <= 1 else "lignes"
    parts = "section" if sections <= 1 else "sections"
    return _clean(f"Checklist PRE-MEL : correctifs ({count} {rows}, {sections} {parts})")


def title_counts(found: dict, *, earlier: bool = True) -> tuple:
    """Return (rows, sections) for the title from the values of material: the fixed and covered
    rows, with the earlier ones of the branch for a new MR (earlier), and the sections that
    hold them."""
    items = list(found["fixed"]) + list(found["covered"])
    if earlier:
        items += list(found["earlier"])
    return len(items), len({item.get("section") for item in items if item.get("section")})


def notion_url(page_id) -> Optional[str]:
    """Return the Notion link of a page id, dashes removed, or None without an id."""
    if not isinstance(page_id, str) or not page_id.strip():
        return None
    return NOTION + page_id.strip().replace("-", "")


def body(*, run_id: str, copy_url: Optional[str], fixed: list, checks, not_run: list,
         attention: list, waiting: list, left: list, earlier: list = (), covered: list = (),
         sections: list = (), reorder_conflicts: list = (), category: Optional[str] = None) -> str:
    """Return the description of a new MR; empty sections are left out.

    fixed: [{"ref", "title", "url", "semi_auto", "human_check", "summary", "change", "uat",
    "commits", "remaining", "section"}]; checks: the entries of checks.json (None when it is
    missing); not_run: [{"name", "command"}]; attention: remarks, {"section", "text"} or plain
    texts; waiting: [{"ref", "title", "text", "url"}]; left: [{"ref", "title", "reason", "url",
    "section"}]; earlier: [{"ref", "title"}], the fixes the branch carried before this pass;
    covered: [{"ref", "title", "by", "url", "section"}], the rows the commit of row "by" also
    fixes, listed after the fixes of their section. sections: [{"alias", "category", "state",
    "reason"}] in the order of the commits: « Correctifs », « Points d'attention » and
    « Hors-scope » give the rows of each under « ### <CATÉGORIE> » (an item of no listed
    section comes first, ungrouped); a failed section says why in « Hors-scope ».
    reorder_conflicts: the subjects of the commits a reorder left in place. category names the
    scope without sections. Raises ValueError for a run id that is no date.
    """
    started = run_start(run_id)
    names = [_name(section) for section in sections] or ([category] if category else [])
    scope = ""
    if names:
        scope = f", {'catégorie' if len(names) == 1 else 'catégories'} {', '.join(names)}"
    context = [f"Audit PRE-MEL du {started:%d/%m/%Y} (run {run_id}){scope}."]
    if copy_url:
        context.append(f"Copie Notion de la checklist : {copy_url}")
    context.append(PREPARED)
    if earlier:
        names = ", ".join(
            f"{ref_label(item['ref'])} ({item['title']})" if item.get("title")
            else ref_label(item["ref"]) for item in earlier
        )
        context.append(f"La branche porte aussi des correctifs d'une passe précédente : {names}.")
    parts = [("Contexte", "\n\n".join(context))]
    parts += _sections(2, fixed, covered, checks, not_run, attention, waiting, verify=True,
                       sections=sections, conflicts=reorder_conflicts)
    out = hors_scope(left, sections)
    if out:
        parts.append((HORS_SCOPE, out))
    parts.append(("Suite", SUITE))
    return _render(2, parts)


def addition(*, fixed: list, checks, not_run: list, attention: list, waiting: list, left: list,
             earlier: list = (), covered: list = (), sections: list = (),
             reorder_conflicts: list = ()) -> str:
    """Return the text of mr-addition.md: the new sub-sections, then the « ## Hors-scope » list.

    The checks are shown only with new fixed rows. Sections group the rows as in body.
    split_addition reads the file back.
    """
    parts = _sections(3, fixed, covered, checks, not_run, attention, waiting, verify=bool(fixed),
                      sections=sections, conflicts=reorder_conflicts)
    text = _render(3, parts).rstrip("\n") if parts else ""
    out = hors_scope(left, sections)
    if out:
        text = (text + "\n\n" if text else "") + f"## {HORS_SCOPE}\n\n{out}"
    return _clean(text + "\n") if text else ""


def same_description(first: str, second: str) -> bool:
    """Return True when two descriptions differ at most by line endings and trailing spaces."""
    def normal(text: str) -> str:
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return "\n".join(line.rstrip() for line in lines).strip()

    return normal(first) == normal(second)


def split_addition(text: str) -> tuple:
    """Return (addition, hors_scope) from the text of mr-addition.md."""
    found = _HORS_SCOPE_LINE.search(text)
    if found is None:
        return text.strip("\n"), ""
    return text[:found.start()].strip("\n"), text[found.end():].strip("\r\n")


def hors_scope(left: list, sections: list = ()) -> str:
    """Return the « Hors-scope » list: « - CQ-40 : <titre> : <raison> ([Notion](<url>)) ».

    The rows of each section come under « ### <CATÉGORIE> », in the order of sections, and a
    failed section first says « Section non aboutie : <raison> »; a row of no listed section
    comes first, ungrouped. Empty when there is nothing to list.
    """
    loose, groups = _grouped(left, sections)
    blocks = ["\n".join(_listed(item, item.get("reason")) for item in loose)] if loose else []
    for section, items in groups:
        failed = section.get("state") == "failed"
        if not items and not failed:
            continue
        block = [f"### {_name(section)}"]
        if failed:
            block.append(FAILED_SECTION.format(reason=section.get("reason") or _NO_REASON))
        if items:
            block.append("\n".join(_listed(item, item.get("reason")) for item in items))
        blocks.append("\n\n".join(block))
    return "\n\n".join(blocks)


def merge_description(old: str, addition: str, hors_scope: str, today: date) -> str:
    """Return old with « ## Ajouts du <date> » and the new « ## Hors-scope » section.

    The additions go where « ## Hors-scope » stood, and that section is replaced (left out
    when the new list is empty); everything else of old is kept byte for byte. Without a
    « ## Hors-scope » heading, both go at the end.
    """
    added = []
    if addition.strip():
        added.append(f"## Ajouts du {today:%d/%m/%Y}\n\n{addition.strip(chr(10))}\n\n")
    if hors_scope.strip():
        added.append(f"## {HORS_SCOPE}\n\n{hors_scope.strip(chr(10))}\n\n")
    found = _HORS_SCOPE_LINE.search(old)
    if found is None:
        if not added:
            return old
        separator = "" if not old or old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")
        return old + separator + "".join(added).rstrip("\n") + "\n"
    following = _TOP_HEADING.search(old, found.end())
    tail = old[following.start():] if following else ""
    merged = old[:found.start()] + "".join(added)
    return merged + tail if tail else merged.rstrip("\n") + "\n"


def mr_from_output(text: str) -> Optional[dict]:
    """Return {"iid", "url"} of the last MR link printed by glab mr create, or None."""
    found = list(_MR_URL.finditer(text or ""))
    if not found:
        return None
    return {"iid": int(found[-1].group(1)), "url": found[-1].group(0)}


def merge_links(document: dict, repo: str, mr: dict, refs: list,
                details: Optional[dict] = None) -> dict:
    """Return the links file with the MR added to each ref, without a URL twice for a ref.

    An entry is {"repo", "url", "iid", "partial", "status_before"}, from details
    ({ref: link_details}). An entry already there for the URL takes the current partial and
    keeps its status_before, the status before the pass that first linked the row (it only
    fills a missing one). document is left untouched.
    """
    details = details or {}
    links = {ref: [dict(entry) for entry in entries]
             for ref, entries in document.get("links", {}).items()}
    for ref in refs:
        detail = details.get(int(ref)) or {}
        partial, status = bool(detail.get("partial")), detail.get("status_before")
        entries = links.setdefault(str(ref), [])
        found = next((entry for entry in entries if entry.get("url") == mr["url"]), None)
        if found is None:
            entries.append({"repo": repo, "url": mr["url"], "iid": mr["iid"], "partial": partial,
                            "status_before": status})
            continue
        found["partial"] = partial
        if found.get("status_before") is None:
            found["status_before"] = status
    return {"links": links}


def link_details(key: dict, candidates: dict, notion_rows: list, refs) -> dict:
    """Return {ref: {"partial", "status_before"}} for the links of refs.

    partial: the row is fixed with something left to do (remaining_of); status_before: the
    status of the copy read at launch, from the rows of fix/candidates.json, else rows.json,
    None when neither has it.
    """
    statuses = {}
    rows = [row for entry in candidates.get("keys") or [] for row in fix_rows.key_rows(entry)]
    for row in rows + [row for row in notion_rows if isinstance(row, dict)]:
        ref, status = row.get("ref"), row.get("status")
        if isinstance(ref, int) and isinstance(status, str) and status.strip():
            statuses.setdefault(ref, status)
    return {int(ref): {"partial": remaining_of(key["rows"].get(str(ref)) or {}) is not None,
                       "status_before": statuses.get(int(ref))} for ref in refs}


def remaining_of(row: dict) -> Optional[str]:
    """Return what is left to do after the partial fix of a fixed state row: the review's
    correction, else the report's remaining; None for a full fix or a row not fixed."""
    if row.get("outcome") != "fixed":
        return None
    value = _corrections(row).get("remaining") or row.get("remaining")
    return value.strip() if isinstance(value, str) and value.strip() else None


def material(key: dict, candidates: dict, notion_rows: list, reports: list, reviews: list,
             checks, changed: list, branch_refs: set, *, since: int = 0, order=()) -> dict:
    """Return the values of body and addition for a key, from what the run holds.

    reports and reviews are [(N, document)]; checks is checks.json (None when missing);
    changed the files of <start_sha>..HEAD; branch_refs the refs of the commit subjects of
    origin/<base>..HEAD; order the SHAs of <start_sha>..HEAD, oldest first. Only fixed and
    covered rows, reports and reviews of passes after since count (the passes an earlier
    publish already described).

    The sections come in the order of their first commit (a section without any after the
    others, in the key's order), and the fixed rows of a section in the order of theirs. The
    text corrections of a review (rows[ref]["corrections"]) replace those of the report; the
    remarks of a failed section are left out, its reason and state go with it.
    """
    info = _row_info(key, candidates, notion_rows)
    rows = key["rows"]
    position = {sha: index for index, sha in enumerate(order)}
    fixed_refs = sorted(
        (int(ref) for ref, row in rows.items()
         if row.get("outcome") == "fixed" and (row.get("pass") or 0) > since),
        key=lambda ref: (_first_commit(rows[str(ref)], position), ref),
    )
    covered = _covered(rows, info)
    latest = _latest_fixed(reports)
    fixed = []
    for ref in fixed_refs:
        row = rows[str(ref)]
        about, report, corrected = info.get(ref, {}), latest.get(ref, {}), _corrections(row)
        fixed.append({
            "ref": ref, "title": about.get("title"), "url": notion_url(about.get("page_id")),
            "semi_auto": bool(about.get("semi_auto")), "human_check": about.get("human_check"),
            "summary": about.get("summary"),
            "change": corrected.get("change") or report.get("change"),
            "uat": corrected.get("uat") or report.get("uat"),
            "commits": [sha[:_SHORT_SHA] for sha in row.get("commits") or []],
            "remaining": remaining_of(row), "section": row.get("section"),
        })
    all_fixed = {int(ref) for ref, row in rows.items() if row.get("outcome") == "fixed"}
    titles = {item.get("ref"): item.get("title") for item in candidates.get("left") or []
              if isinstance(item, dict)}
    titles.update({ref: row.get("title") for ref, row in info.items() if row.get("title")})
    return {
        "fixed": fixed,
        "covered": [item for number, item in covered if number > since],
        "checks": _current_checks(checks, changed),
        "not_run": [{"name": item.get("name"), "command": item.get("command")}
                    for item in (checks or {}).get("not_run") or [] if isinstance(item, dict)],
        "attention": _attention(key, reports, reviews, since),
        "waiting": [
            {"ref": int(ref), "title": info.get(int(ref), {}).get("title"), "text": row.get("reason"),
             "url": notion_url(info.get(int(ref), {}).get("page_id"))}
            for ref, row in sorted(rows.items(), key=lambda item: int(item[0]))
            if row.get("outcome") == "question"
        ],
        "left": _left(key, candidates, info, branch_refs, {item["ref"] for _, item in covered}),
        "earlier": [{"ref": ref, "title": titles.get(ref),
                     "section": (rows.get(str(ref)) or {}).get("section")}
                    for ref in sorted(set(branch_refs) - all_fixed)],
        "sections": _ordered_sections(key, position),
        "reorder_conflicts": [subject for subject in key.get("reorder_conflicts") or []
                              if isinstance(subject, str) and subject.strip()],
    }


def _sections(level: int, fixed, covered, checks, not_run, attention, waiting, *,
              verify: bool, sections=(), conflicts=()) -> list:
    sub = "#" * (level + 1)
    parts = []
    if fixed or covered:
        parts.append(("Correctifs", _fixes(sub, fixed, covered, sections)))
    if verify:
        parts.append(("Vérification", "\n".join(f"- {line}" for line in _verification(checks, not_run))))
    notes = _notes(sub, attention, sections, conflicts)
    if notes:
        parts.append(("Points d'attention", notes))
    if waiting:
        parts.append(("En attente d'une décision",
                      "\n".join(_listed(item, item.get("text")) for item in waiting)))
    return parts


def _fixes(sub: str, fixed: list, covered: list, sections) -> str:
    """Return « Correctifs »: the rows of no listed section, then each section under its
    heading, its fixed rows a level below, then the rows their commits cover."""
    loose_fixed, fixed_groups = _grouped(fixed, sections)
    loose_covered, covered_groups = _grouped(covered, sections)
    blocks = [_fix(sub, row) for row in loose_fixed]
    if loose_covered:
        blocks.append(_covered_lines(loose_covered))
    for (section, rows), (_, others) in zip(fixed_groups, covered_groups):
        if not rows and not others:
            continue
        blocks.append(f"{sub} {_name(section)}")
        blocks += [_fix(sub + "#", row) for row in rows]
        if others:
            blocks.append(_covered_lines(others))
    return "\n\n".join(blocks)


def _covered_lines(items: list) -> str:
    return "\n".join(_listed(item, COVERED.format(ref=ref_label(item["by"]))) for item in items)


def _notes(sub: str, attention: list, sections, conflicts) -> str:
    """Return « Points d'attention »: the reorder conflict and the remarks of no listed
    section first, then those of each section under its heading."""
    notes = [note if isinstance(note, dict) else {"section": None, "text": note}
             for note in attention]
    loose, groups = _grouped(notes, sections)
    lines = []
    if conflicts:
        names = ", ".join(f"`{subject}`" for subject in conflicts)
        lines.append(f"- {REORDER_CONFLICT} ({names}).")
    lines += [f"- {note['text']}" for note in loose]
    blocks = ["\n".join(lines)] if lines else []
    for section, items in groups:
        if items:
            blocks.append(f"{sub} {_name(section)}\n\n"
                          + "\n".join(f"- {note['text']}" for note in items))
    return "\n\n".join(blocks)


def _grouped(items: list, sections) -> tuple:
    """Return (items of no listed section, [(section, its items)] in the order of sections)."""
    aliases = {section.get("alias") for section in sections}
    loose = [item for item in items if item.get("section") not in aliases]
    groups = [(section, [item for item in items if item.get("section") == section.get("alias")])
              for section in sections]
    return loose, groups


def _name(section: dict) -> str:
    return section.get("category") or section.get("alias") or _NO_CATEGORY


def _fix(heading: str, row: dict) -> str:
    lines = [f"{heading} {ref_label(row['ref'])} : {row.get('title') or 'sans titre'}"]
    if row.get("semi_auto"):
        check = row.get("human_check")
        lines.append(f"{SEMI_AUTO} À valider : {check}" if check else SEMI_AUTO)
    if row.get("remaining"):
        lines.append(f"{PARTIAL} Reste à faire : {row['remaining']}")
    fields = [
        ("Ligne Notion", row.get("url")),
        ("Constat de l'audit", row.get("summary")),
        ("Changement", row.get("change")),
        ("Commits", ", ".join(row.get("commits") or [])),
        ("UAT", row.get("uat")),
    ]
    lines.append("\n".join(f"- {label} : {value}" for label, value in fields if value))
    return "\n\n".join(lines)


def _verification(checks, not_run: list) -> list:
    lines = []
    if checks is None:
        lines.append(NO_CHECKS)
    for check in checks or []:
        files = f" ({', '.join(check['files'])})" if check.get("files") else ""
        detail = f" : {check['detail']}" if check.get("detail") else ""
        if check.get("status") == "passed":
            lines.append(f"✅ {check['name']}{files}")
        elif check.get("status") == "failed":
            lines.append(f"❌ Échec : {check['name']}{files}{detail}")
        else:
            lines.append(f"Non lancé : {check['name']}{files}{detail}")
    lines.extend(f"Non lancé : {item['name']} : `{item['command']}`" for item in not_run)
    lines.append(CI)
    return lines


def _listed(item: dict, text) -> str:
    line = f"- {ref_label(item['ref'])} : {item.get('title') or 'sans titre'}"
    if text:
        line += f" : {text}"
    if item.get("url"):
        line += f" ([Notion]({item['url']}))"
    return line


def _render(level: int, parts: list) -> str:
    heading = "#" * level
    return _clean("\n\n".join(f"{heading} {name}\n\n{text}" for name, text in parts) + "\n")


def _clean(text: str) -> str:
    return text.replace("\u2014", "-")


def _row_info(key: dict, candidates: dict, notion_rows: list) -> dict:
    """Return {ref: candidate row}: the key's rows first, then any key's, then rows.json."""
    info = {}
    entries = [entry for entry in candidates.get("keys") or [] if isinstance(entry, dict)]
    own = [entry for entry in entries if entry.get("repo") == key["repo"]]
    for entry in own + entries:
        for row in fix_rows.key_rows(entry):
            if isinstance(row.get("ref"), int):
                info.setdefault(row["ref"], row)
    for row in notion_rows:
        if isinstance(row, dict) and isinstance(row.get("ref"), int):
            info.setdefault(row["ref"], row)
    return info


def _latest_fixed(reports: list) -> dict:
    """Return {ref: report row} from the latest report where the row is fixed."""
    latest = {}
    for _, document in sorted(reports, key=lambda item: item[0]):
        for item in _items(document):
            if item.get("outcome") == "fixed" and isinstance(item.get("ref"), int):
                latest[item["ref"]] = item
    return latest


def _current_checks(checks, changed: list):
    """Return the checks of checks.json limited to the files the branch still changes."""
    if not isinstance(checks, dict):
        return None
    current, kept = set(changed), []
    for check in checks.get("checks") or []:
        if not isinstance(check, dict):
            continue
        files = [path for path in check.get("files") or [] if path in current]
        if check.get("files") and not files:
            continue
        kept.append({**check, "files": files})
    return kept


def _attention(key: dict, reports: list, reviews: list, since: int) -> list:
    """Return [{"section", "text"}]: the remarks of the reviews, in the section of their pass,
    then those of the fixed rows of the reports, in the section of the row, once each.

    The review of a pass of a failed section gives none; the remark of the pass that fixed a
    row is the review's correction when it has one.
    """
    rows = key["rows"]
    owners = key.get("pass_sections") if isinstance(key.get("pass_sections"), dict) else {}
    failed = {section.get("alias") for section in key.get("sections") or []
              if section.get("state") == "failed"}
    notes = []

    def add(section, text):
        note = {"section": section, "text": text}
        if note not in notes:
            notes.append(note)

    for number, document in sorted(reviews, key=lambda item: item[0]):
        section = owners.get(str(number))
        if number <= since or not isinstance(document, dict) or section in failed:
            continue
        for note in document.get("attention") or []:
            if isinstance(note, str) and note.strip():
                add(section, note.strip())
    for number, document in sorted(reports, key=lambda item: item[0]):
        if number <= since:
            continue
        for item in _items(document):
            ref = item.get("ref")
            row = rows.get(str(ref)) or {}
            if item.get("outcome") != "fixed" or row.get("outcome") != "fixed":
                continue
            note = item.get("attention")
            if number == row.get("pass"):
                note = _corrections(row).get("attention") or note
            if isinstance(note, str) and note.strip():
                add(row.get("section"), f"{ref_label(ref)} : {note.strip()}")
    return notes


def _corrections(row: dict) -> dict:
    """Return the text corrections of the review of a row ({} when none)."""
    value = row.get("corrections")
    if not isinstance(value, dict):
        return {}
    return {name: text for name, text in value.items() if isinstance(text, str) and text.strip()}


def _first_commit(row: dict, position: dict) -> int:
    """Return the place of the first commit of a row in the branch, after every commit when
    none of its commits is known."""
    return min((position[sha] for sha in row.get("commits") or [] if sha in position),
               default=len(position))


def _ordered_sections(key: dict, position: dict) -> list:
    """Return the sections of the key in the order of their first commit (_first_commit of
    their fixed rows), a section without any after the others, in the key's order."""
    rows = key["rows"]

    def first(section: dict) -> int:
        return min((_first_commit(rows[str(ref)], position) for ref in section.get("refs") or []
                    if (rows.get(str(ref)) or {}).get("outcome") == "fixed"),
                   default=len(position))

    ordered = sorted(enumerate(key.get("sections") or []),
                     key=lambda item: (first(item[1]), item[0]))
    return [{"alias": section.get("alias"), "category": section.get("category"),
             "state": section.get("state"), "reason": section.get("reason")}
            for _, section in ordered]


def covered_refs(rows: dict) -> dict:
    """Return {ref: by} for the skipped rows whose reason says « corrigé par le commit de
    CQ-<by> », while CQ-<by> is still fixed in the key (or was already on the branch): their
    fix ships in the MR with CQ-<by>, so they are no « Hors-scope » row nor a row left.
    """
    found = {}
    for ref, row in rows.items():
        if not isinstance(row, dict) or row.get("outcome") != "skipped":
            continue
        match = _COVERED_REASON.match(row.get("reason") or "")
        if match is None:
            continue
        by = int(match.group(1))
        if (rows.get(str(by)) or {}).get("outcome") in ("fixed", "done_before"):
            found[int(ref)] = by
    return found


def _covered(rows: dict, info: dict) -> list:
    """Return [(pass, {"ref", "title", "by", "url", "section"})] for the rows of
    covered_refs, by ref."""
    found = []
    for ref, by in sorted(covered_refs(rows).items()):
        about = info.get(ref, {})
        found.append((rows[str(ref)].get("pass") or 0, {
            "ref": ref, "title": about.get("title"), "by": by,
            "url": notion_url(about.get("page_id")), "section": rows[str(ref)].get("section"),
        }))
    return found


def _left(key: dict, candidates: dict, info: dict, branch_refs: set,
          covered: set = frozenset()) -> list:
    """Return the open rows of the categories of the key's sections that this MR does not fix,
    sorted by ref, each with its section; covered holds the refs of the skipped rows another
    fix of the MR covers (_covered).

    Only the rows of this repo count: a left row of fix/candidates.json is left out when it is
    a row of another repo's key, or when its evidence names other repos only ("repos").
    """
    mr_url = (key.get("mr") or {}).get("url")
    aliases = {section.get("alias") for section in key.get("sections") or []}
    others = {row.get("ref") for entry in candidates.get("keys") or []
              if isinstance(entry, dict) and entry.get("repo") != key["repo"]
              for row in fix_rows.key_rows(entry)}
    found = {}
    for ref, row in key["rows"].items():
        if row.get("outcome") in ("skipped", "dropped") and int(ref) not in covered:
            found[int(ref)] = _out(int(ref), info, row.get("reason"), row.get("section"))
    for item in candidates.get("left") or []:
        ref = item.get("ref") if isinstance(item, dict) else None
        if not isinstance(ref, int) or ref in found or str(ref) in key["rows"] or ref in others:
            continue
        alias = _alias(item.get("category"))
        if alias not in aliases or ref in branch_refs:
            continue
        repos = item.get("repos")
        if isinstance(repos, list) and repos and key["repo"] not in repos:
            continue
        if mr_url and item.get("url") == mr_url:
            continue
        entry = _out(ref, info, item.get("reason"), alias)
        entry["title"] = item.get("title") or entry["title"]
        found[ref] = entry
    return [found[ref] for ref in sorted(found)]


def _out(ref: int, info: dict, reason, section) -> dict:
    about = info.get(ref, {})
    return {"ref": ref, "title": about.get("title"), "reason": reason,
            "url": notion_url(about.get("page_id")), "section": section}


def _alias(category) -> Optional[str]:
    try:
        return fix_names.section_alias(category or _NO_CATEGORY)
    except ValueError:
        return None


def _items(document) -> list:
    items = document.get("rows") if isinstance(document, dict) else None
    return [item for item in items or [] if isinstance(item, dict)]
