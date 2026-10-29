"""Merge request texts of the fix mode: title, description, additions and the links file.

Everything here is pure: the caller reads the run files and git, and passes plain values.
Titles and descriptions are in French; no attribution line is ever written.
"""
import re
from datetime import date
from typing import Optional

from cqa import fix_names
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
_NO_CATEGORY = "Sans catégorie"
_SHORT_SHA = 8
_HORS_SCOPE_LINE = re.compile(r"^## Hors-scope[ \t]*\r?$", re.MULTILINE)
_TOP_HEADING = re.compile(r"^#{1,2}[ \t]", re.MULTILINE)
_MR_URL = re.compile(r"https?://\S+?/-/merge_requests/([0-9]+)\b")


def title(category: str, count: int) -> str:
    """Return the MR title: « Checklist PRE-MEL : correctifs SÉCURITÉ (5 lignes) »."""
    word = "ligne" if count <= 1 else "lignes"
    return _clean(f"Checklist PRE-MEL : correctifs {category} ({count} {word})")


def notion_url(page_id) -> Optional[str]:
    """Return the Notion link of a page id, dashes removed, or None without an id."""
    if not isinstance(page_id, str) or not page_id.strip():
        return None
    return NOTION + page_id.strip().replace("-", "")


def body(*, run_id: str, category: str, copy_url: Optional[str], fixed: list, checks, not_run: list,
         attention: list, waiting: list, left: list, earlier: list = ()) -> str:
    """Return the description of a new MR; empty sections are left out.

    fixed: [{"ref", "title", "url", "semi_auto", "human_check", "summary", "change", "uat",
    "commits"}]; checks: the entries of checks.json (None when it is missing); not_run:
    [{"name", "command"}]; attention: remarks; waiting: [{"ref", "title", "text", "url"}];
    left: [{"ref", "title", "reason", "url"}]; earlier: [{"ref", "title"}], the fixes the
    branch carried before this pass. Raises ValueError for a run id that is no date.
    """
    started = run_start(run_id)
    context = [f"Audit PRE-MEL du {started:%d/%m/%Y} (run {run_id}), catégorie {category}."]
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
    parts += _sections(2, fixed, checks, not_run, attention, waiting, verify=True)
    if left:
        parts.append((HORS_SCOPE, hors_scope(left)))
    parts.append(("Suite", SUITE))
    return _render(2, parts)


def addition(*, fixed: list, checks, not_run: list, attention: list, waiting: list, left: list,
             earlier: list = ()) -> str:
    """Return the text of mr-addition.md: the new sub-sections, then the « ## Hors-scope » list.

    The checks are shown only with new fixed rows. split_addition reads the file back.
    """
    parts = _sections(3, fixed, checks, not_run, attention, waiting, verify=bool(fixed))
    text = _render(3, parts).rstrip("\n") if parts else ""
    if left:
        text = (text + "\n\n" if text else "") + f"## {HORS_SCOPE}\n\n{hors_scope(left)}"
    return text + "\n" if text else ""


def split_addition(text: str) -> tuple:
    """Return (addition, hors_scope) from the text of mr-addition.md."""
    found = _HORS_SCOPE_LINE.search(text)
    if found is None:
        return text.strip("\n"), ""
    return text[:found.start()].strip("\n"), text[found.end():].strip("\r\n")


def hors_scope(left: list) -> str:
    """Return the « Hors-scope » list: « - CQ-40 : <titre> : <raison> ([Notion](<url>)) »."""
    return "\n".join(_listed(item, item.get("reason")) for item in left)


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


def merge_links(document: dict, repo: str, mr: dict, refs: list) -> dict:
    """Return the links file with the MR added to each ref, without a URL twice for a ref."""
    links = {ref: list(entries) for ref, entries in document.get("links", {}).items()}
    for ref in refs:
        entries = links.setdefault(str(ref), [])
        if not any(entry.get("url") == mr["url"] for entry in entries):
            entries.append({"repo": repo, "url": mr["url"], "iid": mr["iid"]})
    return {"links": links}


def material(key: dict, candidates: dict, notion_rows: list, reports: list, reviews: list,
             checks, changed: list, branch_refs: set, *, since: int = 0) -> dict:
    """Return the values of body and addition for a key, from what the run holds.

    reports and reviews are [(N, document)]; checks is checks.json (None when missing);
    changed the files of <start_sha>..HEAD; branch_refs the refs of the commit subjects of
    origin/<base>..HEAD. Only fixed rows, reports and reviews of passes after since count
    (the passes an earlier publish already described).
    """
    info = _row_info(key, candidates, notion_rows)
    rows = key["rows"]
    fixed_refs = sorted(
        int(ref) for ref, row in rows.items()
        if row.get("outcome") == "fixed" and (row.get("pass") or 0) > since
    )
    latest = _latest_fixed(reports)
    fixed = []
    for ref in fixed_refs:
        about, report = info.get(ref, {}), latest.get(ref, {})
        fixed.append({
            "ref": ref, "title": about.get("title"), "url": notion_url(about.get("page_id")),
            "semi_auto": bool(about.get("semi_auto")), "human_check": about.get("human_check"),
            "summary": about.get("summary"), "change": report.get("change"),
            "uat": report.get("uat"),
            "commits": [sha[:_SHORT_SHA] for sha in rows[str(ref)].get("commits") or []],
        })
    all_fixed = {int(ref) for ref, row in rows.items() if row.get("outcome") == "fixed"}
    titles = {item.get("ref"): item.get("title") for item in candidates.get("left") or []
              if isinstance(item, dict)}
    titles.update({ref: row.get("title") for ref, row in info.items() if row.get("title")})
    return {
        "fixed": fixed,
        "checks": _current_checks(checks, changed),
        "not_run": [{"name": item.get("name"), "command": item.get("command")}
                    for item in (checks or {}).get("not_run") or [] if isinstance(item, dict)],
        "attention": _attention(rows, reports, reviews, since),
        "waiting": [
            {"ref": int(ref), "title": info.get(int(ref), {}).get("title"), "text": row.get("reason"),
             "url": notion_url(info.get(int(ref), {}).get("page_id"))}
            for ref, row in sorted(rows.items(), key=lambda item: int(item[0]))
            if row.get("outcome") == "question"
        ],
        "left": _left(key, candidates, info, branch_refs),
        "earlier": [{"ref": ref, "title": titles.get(ref)}
                    for ref in sorted(set(branch_refs) - all_fixed)],
    }


def _sections(level: int, fixed, checks, not_run, attention, waiting, *, verify: bool) -> list:
    sub = "#" * (level + 1)
    parts = []
    if fixed:
        parts.append(("Correctifs", "\n\n".join(_fix(sub, row) for row in fixed)))
    if verify:
        parts.append(("Vérification", "\n".join(f"- {line}" for line in _verification(checks, not_run))))
    if attention:
        parts.append(("Points d'attention", "\n".join(f"- {note}" for note in attention)))
    if waiting:
        parts.append(("En attente d'une décision",
                      "\n".join(_listed(item, item.get("text")) for item in waiting)))
    return parts


def _fix(heading: str, row: dict) -> str:
    lines = [f"{heading} {ref_label(row['ref'])} : {row.get('title') or 'sans titre'}"]
    if row.get("semi_auto"):
        check = row.get("human_check")
        lines.append(f"{SEMI_AUTO} À valider : {check}" if check else SEMI_AUTO)
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
    own = [entry for entry in entries if entry.get("repo") == key["repo"]
           and entry.get("slug") == key["slug"]]
    for entry in own + entries:
        for row in entry.get("rows") or []:
            if isinstance(row, dict) and isinstance(row.get("ref"), int):
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


def _attention(rows: dict, reports: list, reviews: list, since: int) -> list:
    """Return the remarks of the reviews, then those of the fixed rows of the reports, once each."""
    notes = []

    def add(note):
        if note not in notes:
            notes.append(note)

    for number, document in sorted(reviews, key=lambda item: item[0]):
        if number > since and isinstance(document, dict):
            for note in document.get("attention") or []:
                if isinstance(note, str) and note.strip():
                    add(note.strip())
    for number, document in sorted(reports, key=lambda item: item[0]):
        if number <= since:
            continue
        for item in _items(document):
            ref, note = item.get("ref"), item.get("attention")
            if (item.get("outcome") == "fixed" and isinstance(note, str) and note.strip()
                    and (rows.get(str(ref)) or {}).get("outcome") == "fixed"):
                add(f"{ref_label(ref)} : {note.strip()}")
    return notes


def _left(key: dict, candidates: dict, info: dict, branch_refs: set) -> list:
    """Return the open rows of the key's category that this MR does not fix, sorted by ref."""
    mr_url = (key.get("mr") or {}).get("url")
    found = {}
    for ref, row in key["rows"].items():
        if row.get("outcome") in ("skipped", "dropped"):
            found[int(ref)] = _out(int(ref), info, row.get("reason"))
    for item in candidates.get("left") or []:
        ref = item.get("ref") if isinstance(item, dict) else None
        if not isinstance(ref, int) or ref in found or str(ref) in key["rows"]:
            continue
        if _slug(item.get("category")) != key["slug"] or ref in branch_refs:
            continue
        if mr_url and item.get("url") == mr_url:
            continue
        entry = _out(ref, info, item.get("reason"))
        entry["title"] = item.get("title") or entry["title"]
        found[ref] = entry
    return [found[ref] for ref in sorted(found)]


def _out(ref: int, info: dict, reason) -> dict:
    about = info.get(ref, {})
    return {"ref": ref, "title": about.get("title"), "reason": reason,
            "url": notion_url(about.get("page_id"))}


def _slug(category) -> Optional[str]:
    try:
        return fix_names.category_slug(category or _NO_CATEGORY)
    except ValueError:
        return None


def _items(document) -> list:
    items = document.get("rows") if isinstance(document, dict) else None
    return [item for item in items or [] if isinstance(item, dict)]
