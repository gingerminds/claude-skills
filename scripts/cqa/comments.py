"""Comments the audit writes in Notion: the French texts, their rich_text form, their verdict."""
import ipaddress
import re
from datetime import date
from typing import Optional
from urllib.parse import quote, urlsplit

from cqa.checklist import (
    STATUS_IMPLEMENTED,
    STATUS_NA,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_STANDBY,
    STATUS_TODO,
    ref_label,
)
from cqa.git_tools import blob_url

VERDICT_LABELS = {
    "ok": "Ok / Recetté",
    "ok_human": "Mis en place, à valider",
    "partial": "Mise en place partielle",
    "ko": "À traiter",
    "na": "Non applicable",
    "unknown": "Non évaluable",
}
CONFIDENCE_LABELS = {"high": "haute", "medium": "moyenne", "low": "basse"}

MAX_COMMENT_LENGTH = 1500
MAX_TEXT_LENGTH = 2000
MR_LABEL = "MR :"
FIX_TITLE = "Correctif automatique"
FIX_PAGE_TITLE = "Correctifs automatiques"
NOT_DONE_LABEL = "Non traité :"
REMAINING_LABEL = "Reste à faire :"
LOCAL_BRANCH = "Branche locale : {name} (non poussée)"
PUSHED_BRANCH = "Branche poussée sans MR : {name}"
NO_REVIEW_OPTION_LINE = "Statut « En review » non posé : option absente de la copie."

_MAX_EVIDENCE = 5
_SKILL = "gm:checklist-quality-audit"
_EM_DASH = chr(0x2014)
_OPTIONAL_LINES = (
    ("todo", "À faire"),
    ("human_check", "À valider"),
    ("divergence", "Écart code / préprod"),
)
_STATUS_ORDER = (
    STATUS_TODO,
    STATUS_PARTIAL,
    STATUS_IMPLEMENTED,
    STATUS_OK,
    STATUS_NA,
    STATUS_STANDBY,
)
_BLOCKING_LABEL = "Lignes bloquantes non conformes"
_FOOTER_START = "Réf CQ-"

_VERDICT_BY_LABEL = {label: verdict for verdict, label in VERDICT_LABELS.items()}
_DATED_BY = r" · [0-9]{2}/[0-9]{2}/[0-9]{4} · lancé par \S.*"
_AUDIT_HEADER = re.compile(r"Audit PRE-MEL" + _DATED_BY)
_FIX_HEADER = re.compile(FIX_TITLE + _DATED_BY)
_RECETTE = "/gm:checklist-quality-audit recette"
_VERDICT_LINE = re.compile(r"\s*Verdict\s*:\s*(?P<label>.*?)\s*(?:\(confiance[^)]*\))?\s*")
_VERDICT_VALUE = re.compile(r"\s*(?P<label>.*?)\s*(?:\(confiance[^)]*\))?\s*")
_URL = re.compile(r"https?://[^\s<>\"']+")
_URL_FORBIDDEN = re.compile(r"[\s<>\"'`\\{}|^\x00-\x1f\x7f]")
_URL_TRAILING = ".,;:!?'\"`*>»”’…"
_URL_CLOSERS = {")": "(", "]": "[", "}": "{"}


def format_comment(
    *,
    finding: dict,
    row: dict,
    run_date: date,
    lead_option: str,
    repos: list[dict],
    plugin_version: str,
) -> str:
    """Return the Notion comment of a finding, at most MAX_COMMENT_LENGTH characters.

    The header, then one block per field (label alone on its line, content on the next one,
    a blank line between blocks), then the footer right after the last block. The blocks "À faire", "À valider" and
    "Écart code / préprod" appear only when their field is filled, and "Preuves" only when there is evidence. Beyond 5 pieces of evidence the rest
    becomes "et N autres preuves". A text still too long loses evidence from the end, then
    the summary is cut with an ellipsis, then the other lines if that is not enough. It never
    carries the "MR :" block of the v2 fix mode: the fix comment (format_fix_comment) does.
    """
    verdict = VERDICT_LABELS[finding["verdict"]]
    confidence = CONFIDENCE_LABELS[finding["confidence"]]
    return _compose(
        head=_header(run_date, lead_option),
        verdict=f"{verdict} (confiance {confidence})",
        summary=finding["summary"],
        evidence=[_evidence_line(item, repos) for item in finding.get("evidence") or []],
        extras=[(label, finding.get(key)) for key, label in _OPTIONAL_LINES],
        foot=_footer(row, repos, plugin_version),
    )


def format_excluded_comment(
    *,
    row: dict,
    verdict: str,
    detail: str,
    run_date: date,
    lead_option: str,
    repos: list[dict],
    plugin_version: str,
) -> str:
    """Return the Notion comment of a row the audit left out, with detail as its summary."""
    return _compose(
        head=_header(run_date, lead_option),
        verdict=VERDICT_LABELS[verdict],
        summary=detail,
        evidence=[],
        extras=[],
        foot=_footer(row, repos, plugin_version),
    )


def without_mr_block(text: str) -> str:
    """Return an audit comment without the "MR :" block the v2 fix mode put before its footer
    (see has_mr_block); the footer ("Réf CQ-...") then comes right after what is left. A text
    without that block is returned unchanged."""
    lines, foot = _split_footer(text)
    start = _v2_block_start(lines)
    if start is None:
        return text
    body = lines[:start]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join([*body, *foot])


def has_mr_block(text: str) -> bool:
    """Return True when a comment ends with the "MR :" block of the v2 fix mode: its last
    block before the footer is "MR :" followed by URL lines only. A "MR :" line anywhere
    else (in a finding, a todo) is not that block."""
    return _v2_block_start(_split_footer(text)[0]) is not None


def _split_footer(text: str) -> tuple:
    lines = text.rstrip().split("\n")
    foot = [lines.pop()] if lines and lines[-1].startswith(_FOOTER_START) else []
    return lines, foot


def _v2_block_start(lines: list):
    """Return the index of the line "MR :" that starts the last block of lines when that
    block is "MR :" and at least one URL line, nothing else; None otherwise."""
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    start = end
    while start and lines[start - 1].strip():
        start -= 1
    block = [line.strip() for line in lines[start:end]]
    if len(block) >= 2 and block[0] == MR_LABEL and all(_URL.fullmatch(item) for item in block[1:]):
        return start
    return None


def is_fix_comment(text: str) -> bool:
    """Return True for the fix comment of the skill: its first line is the header
    « Correctif automatique · <date> · lancé par <lead> »."""
    lines = text.strip().splitlines()
    return bool(lines) and _FIX_HEADER.fullmatch(lines[0].strip()) is not None


def is_audit_comment(text: str) -> bool:
    """Return True for the audit comment of the skill, never for its fix comment.

    It carries a known "Verdict : ..." line, or it ends with the footer ("Réf CQ-...") and
    holds an "MR :" line (the reduced comment of the v2 fix mode) or starts with the audit
    header (a reduced comment whose block was removed).
    """
    if is_fix_comment(text):
        return False
    if parse_verdict(text) is not None:
        return True
    lines = text.strip().splitlines()
    if not lines or not lines[-1].startswith(_FOOTER_START):
        return False
    return has_mr_block(text) or _AUDIT_HEADER.fullmatch(lines[0].strip()) is not None


def is_skill_comment(text: str) -> bool:
    """Return True when a comment the integration wrote is one of the skill's two comments:
    the audit comment or the fix comment."""
    return is_audit_comment(text) or is_fix_comment(text)


def format_fix_comment(
    *,
    row: dict,
    run_date: date,
    lead_option: str,
    repos: list[dict],
    plugin_version: str,
    mrs: list[str],
    done: Optional[str],
    remaining: Optional[str],
    uat: Optional[str],
    commits: list[str],
    not_done: Optional[str],
    branches: Optional[list[str]] = None,
    pushed: Optional[list[str]] = None,
) -> str:
    """Return the fix comment of a row, at most MAX_COMMENT_LENGTH characters.

    The header « Correctif automatique · <date> · lancé par <lead> », then blocks laid out
    as in the audit comment: "MR :" (one URL per line), one « Branche poussée sans MR : <nom> »
    line per branch pushed without merge request (pushed) and one « Branche locale : <nom>
    (non poussée) » line per branch left local (branches), then "Fait :", "Reste à faire :",
    "À tester :", "Commits :" (short
    SHAs), then the footer of the audit comment. A row not put in a merge request (not_done)
    gets "Non traité :" and its reason instead of every other block. Empty blocks are left
    out and blank lines inside a text are dropped. A text too long loses the end of "À
    tester", then of "Fait", then of "Reste à faire".
    """
    head = f"{FIX_TITLE} · {run_date:%d/%m/%Y} · lancé par {lead_option}"
    foot = _footer(row, repos, plugin_version)
    reason = _block_text(not_done)
    if reason:
        return _finish(_fix_render(head, [[NOT_DONE_LABEL, reason]], foot))
    place = [[MR_LABEL, *mrs]] if mrs else []
    place.extend([PUSHED_BRANCH.format(name=name)] for name in pushed or [])
    place.extend([LOCAL_BRANCH.format(name=name)] for name in branches or [])
    fields = [["Fait :", _block_text(done)], [REMAINING_LABEL, _block_text(remaining)],
              ["À tester :", _block_text(uat)]]
    tail = [["Commits :", ", ".join(commits)]] if commits else []

    def render() -> str:
        return _fix_render(head, place + [field for field in fields if field[1]] + tail, foot)

    text = render()
    for index in (2, 0, 1):
        excess = len(text) - MAX_COMMENT_LENGTH
        if excess <= 0:
            break
        if fields[index][1]:
            fields[index][1] = _shorten(fields[index][1], excess)
            text = render()
    return _finish(text)


def parse_fix_comment(text: str) -> dict:
    """Return {"mrs", "remaining"} read in a fix comment: the URLs of its "MR :" block and
    the text of its "Reste à faire :" block (None without one)."""
    lines = text.strip().split("\n")
    if lines and lines[-1].startswith(_FOOTER_START):
        lines.pop()
    blocks, current = [], []
    for line in lines[1:]:
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    mrs, remaining = [], None
    for label, *content in blocks:
        if label == MR_LABEL:
            mrs.extend(item for item in content if _URL.fullmatch(item))
        elif label == REMAINING_LABEL:
            remaining = "\n".join(content) or None
    return {"mrs": mrs, "remaining": remaining}


def format_fix_page_comment(
    *,
    run_date: date,
    lead_option: str,
    mrs: list[str],
    branches: list[str],
    in_mr: list[int],
    local: list[int],
    partial: list[int],
    in_review: list[int],
    review_missing: bool,
    not_done: list[tuple],
    reverted=(),
    pushed=(),
    pushed_rows=(),
) -> str:
    """Return the comment of a fix pass for the copy page, laid out as the audit's one.

    « Correctifs automatiques · <date> · lancé par <lead> », one « MR : <url> » line per merge
    request, one « Branche poussée sans MR : <nom> » line per branch pushed without merge
    request (pushed) and one « Branche locale : <nom> (non poussée) » line per local branch,
    the rows put in a merge request (with the partial ones) and their « En review » status,
    the rows fixed on a branch pushed without merge request (pushed_rows) or left local
    (local), the closed merge requests whose rows get their status back if still « En
    review » (reverted), the rows not handled by reason (not_done: [(label, refs)], empty
    ones left out), then what comes next: one « Suite » per kind of branch, labelled when
    there are several. Over MAX_COMMENT_LENGTH the refs lists lose their last refs (« et N
    autres »), down to a bare count.
    """
    lines = [f"{FIX_PAGE_TITLE} · {run_date:%d/%m/%Y} · lancé par {lead_option}"]
    lines += [f"{MR_LABEL} {url}" for url in mrs]
    lines += [PUSHED_BRANCH.format(name=name) for name in pushed]
    lines += [LOCAL_BRANCH.format(name=name) for name in branches]
    if mrs:
        lines.append(f"Lignes mises en MR : {len(in_mr)}{_partial_part(in_mr, partial)}.")
        if review_missing:
            lines.append(NO_REVIEW_OPTION_LINE)
        elif in_review:
            lines.append(f"Statut « En review » : {_count_rows(len(in_review))}, sauf statut "
                         "changé depuis le lancement.")
    kinds = []
    if mrs:
        kinds.append(("MR", _mr_suite(len(mrs))))
    if pushed_rows:
        several = len(pushed) > 1
        label = "branches poussées sans MR" if several else "branche poussée sans MR"
        lines.append(f"Lignes corrigées sur {'les' if several else 'la'} {label} : "
                     f"{len(pushed_rows)}{_partial_part(pushed_rows, partial)} ; statuts inchangés.")
        kinds.append((label, _pushed_suite(several)))
    if local:
        several = len(branches) > 1
        label = "branches locales" if several else "branche locale"
        lines.append(f"Lignes corrigées sur {'les' if several else 'la'} {label} : "
                     f"{len(local)}{_partial_part(local, partial)} ; statuts inchangés.")
        kinds.append((label, _local_suite(several)))
    if reverted:
        names = ", ".join(ref_label(ref) for ref in reverted)
        if len(reverted) == 1:
            back = "remise à son statut d'avant la passe si elle était"
        else:
            back = "remises à leur statut d'avant la passe si elles étaient"
        lines.append(f"MR fermées sans fusion : {names} {back} encore « En review ».")
    groups = [(label, [ref_label(ref) for ref in refs]) for label, refs in not_done if refs]
    if len(kinds) == 1:
        suites = [f"Suite : {kinds[0][1]}"]
    else:
        suites = [f"Suite ({label}) : {suite}" for label, suite in kinds]
    for limit in range(max((len(refs) for _, refs in groups), default=0), -1, -1):
        listed = []
        if groups:
            listed.append("Non traitées :")
            listed += [f"- {label} : {_shown_refs(refs, limit)}" for label, refs in groups]
        text = "\n".join(lines + listed + suites)
        if len(text) <= MAX_COMMENT_LENGTH:
            break
    return _finish(text)


def _fix_render(head: str, blocks: list, foot: str) -> str:
    body = "\n\n".join("\n".join(block) for block in blocks)
    return f"{head}\n\n{body}\n{foot}" if body else f"{head}\n{foot}"


def _block_text(value) -> str:
    """Return a text for a block: stripped, without blank lines (a blank line ends a block)."""
    if not isinstance(value, str):
        return ""
    return "\n".join(line.rstrip() for line in value.strip().splitlines() if line.strip())


def _partial_part(refs: list, partial: list) -> str:
    count = len(set(refs) & set(partial))
    if not count:
        return ""
    return f", dont {count} {'partielle' if count == 1 else 'partielles'}"


def _count_rows(count: int) -> str:
    return f"{count} {'ligne' if count == 1 else 'lignes'}"


def _shown_refs(refs: list, limit: int) -> str:
    if not limit:
        return str(len(refs))
    shown = ", ".join(refs[:limit])
    return shown if limit >= len(refs) else f"{shown} {_others(len(refs) - limit)}"


def _mr_suite(count: int) -> str:
    mr, them = ("la MR", "la") if count == 1 else ("les MR", "les")
    return f"relire et tester {mr}, {them} fusionner, déployer en préprod, puis lancer {_RECETTE}."


def _pushed_suite(several: bool) -> str:
    mr, them = ("les MR", "les") if several else ("la MR", "la")
    return (f"ouvrir {mr}, {them} relire et {them} tester, {them} fusionner, déployer en "
            f"préprod, puis lancer {_RECETTE}.")


def _local_suite(several: bool) -> str:
    if several:
        start, them = "pousser les branches et ouvrir les MR", "les"
    else:
        start, them = "pousser la branche et ouvrir la MR", "la"
    return (f"{start}, {them} relire et {them} tester, {them} fusionner, déployer en préprod, "
            f"puis lancer {_RECETTE}.")


def format_page_summary(*, summary: dict, run_date: date, lead_option: str) -> str:
    """Return the comment for the copy page: counts and blocking refs of a plan summary.

    unknown, deferred and missing_in_copy may be a count or a list. Over MAX_COMMENT_LENGTH,
    the refs lists lose their last refs ("et N autres"), down to a bare count.
    """
    blocking = [_ref_text(ref) for ref in summary.get("blocking_non_conform") or []]
    overridden = [_ref_text(item["ref"]) for item in summary.get("human_overrides") or []]
    statuses = _status_lines(summary.get("by_status_after") or {})
    unknown = _count(summary.get("unknown"))
    deferred = _count(summary.get("deferred"))
    missing = _count(summary.get("missing_in_copy"))
    for limit in range(max(len(blocking), len(overridden)), -1, -1):
        lines = [_header(run_date, lead_option)]
        if statuses:
            lines += ["Décompte par statut :", *statuses]
        lines.append(_refs_line(_BLOCKING_LABEL, blocking, limit) or f"{_BLOCKING_LABEL} : aucune")
        optional = [
            _count_line("Lignes non évaluées", unknown),
            _refs_line("Statuts posés par un humain et contredits par l'audit", overridden, limit),
            _count_line("Lignes reportées à J0 / J+7", deferred),
            _count_line("Lignes du modèle absentes de la copie", missing),
        ]
        lines.extend(line for line in optional if line)
        text = "\n".join(lines)
        if len(text) <= MAX_COMMENT_LENGTH:
            break
    return _finish(text)


def to_rich_text(text: str) -> list[dict]:
    """Return the rich_text array of a comment.

    Each URL is a text object of its own with a link, minus the punctuation or the closing
    bracket that merely follows it. The other text is cut into objects that stay within
    MAX_TEXT_LENGTH even counted in UTF-16 units. The objects hold the text unchanged.
    """
    objects = []
    start = 0
    for found in _URL.finditer(text):
        url = _trim_url(found.group())
        target = ascii_url(url) if is_linkable_url(url) else None
        if target is None or _units(url) > MAX_TEXT_LENGTH or len(target) > MAX_TEXT_LENGTH:
            continue
        objects.extend(_plain_objects(text[start : found.start()]))
        objects.append({"type": "text", "text": {"content": url, "link": {"url": target}}})
        start = found.start() + len(url)
    objects.extend(_plain_objects(text[start:]))
    return objects


def is_linkable_url(url: str) -> bool:
    """Return True when Notion can take url as a link: http(s), a real host, allowed characters."""
    if _URL_FORBIDDEN.search(url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if "@" in parts.netloc:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.split(".")
    if host != "localhost" and len(labels) < 2:
        return False
    return all(labels)


def ascii_url(url: str) -> Optional[str]:
    """Return url in pure ASCII (IDNA host, percent-encoded path, query and fragment), or None.

    Existing %XX escapes and reserved characters are kept as they are.
    """
    if url.isascii():
        return url
    try:
        parts = urlsplit(url)
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port
    except (ValueError, UnicodeError, AttributeError):
        return None
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return (
        f"{parts.scheme}://{netloc}{_encode(parts.path)}"
        + (f"?{_encode(parts.query)}" if parts.query else "")
        + (f"#{_encode(parts.fragment)}" if parts.fragment else "")
    )


def _encode(part: str) -> str:
    part = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", part)
    return quote(part, safe="/:?#[]@!$&'()*+,;=%~-._")


def parse_verdict(text: str) -> Optional[str]:
    """Return the verdict of a comment, or None.

    Two layouts: "Verdict : <label>" on one line (comments written before the block layout),
    or "Verdict :" alone with the label on the next line.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        found = _VERDICT_LINE.fullmatch(line)
        if not found:
            continue
        label = found.group("label")
        if label or not re.fullmatch(r"\s*Verdict\s*:\s*", line):
            return _VERDICT_BY_LABEL.get(label)
        following = lines[index + 1] if index + 1 < len(lines) else ""
        value = _VERDICT_VALUE.fullmatch(following)
        return _VERDICT_BY_LABEL.get(value.group("label")) if value else None
    return None


def _compose(
    *,
    head: str,
    verdict: str,
    summary: str,
    evidence: list[str],
    extras: list[tuple],
    foot: str,
) -> str:
    fields = [("Constat", _text(summary))]
    fields.extend((label, _text(value)) for label, value in extras)
    shown = min(len(evidence), _MAX_EVIDENCE)
    text = _render(head, verdict, fields, evidence, shown, foot)
    while len(text) > MAX_COMMENT_LENGTH and shown:
        shown -= 1
        text = _render(head, verdict, fields, evidence, shown, foot)
    for index, (label, value) in enumerate(fields):
        excess = len(text) - MAX_COMMENT_LENGTH
        if excess <= 0:
            break
        if value:
            fields[index] = (label, _shorten(value, excess))
            text = _render(head, verdict, fields, evidence, shown, foot)
    return _finish(text)


def _render(head, verdict, fields, evidence, shown, foot) -> str:
    (label, summary), *extras = fields
    blocks = [["Verdict :", verdict]]
    if summary:
        blocks.append([f"{label} :", summary])
    if evidence:
        lines = ["Preuves :"]
        lines.extend(f"- {line}" for line in evidence[:shown])
        if shown < len(evidence):
            lines.append(f"- {_others(len(evidence) - shown, 'preuve')}")
        blocks.append(lines)
    blocks.extend([f"{name} :", text] for name, text in extras if text)
    body = "\n\n".join("\n".join(block) for block in blocks)
    return f"{head}\n\n{body}\n{foot}"


def _finish(text: str) -> str:
    text = text.replace(_EM_DASH, "-")
    if len(text) <= MAX_COMMENT_LENGTH:
        return text
    return text[: MAX_COMMENT_LENGTH - 1].rstrip() + "…"


def _header(run_date: date, lead_option: str) -> str:
    return f"Audit PRE-MEL · {run_date:%d/%m/%Y} · lancé par {lead_option}"


def _footer(row: dict, repos: list[dict], plugin_version: str) -> str:
    parts = [f"Réf {ref_label(row['ref'])}"]
    if len(repos) == 1:
        parts.append(f"develop@{repos[0]['audited_sha'][:7]}")
    elif repos:
        parts.append(", ".join(f"{repo['name']}@{repo['audited_sha'][:7]}" for repo in repos))
    parts.append(f"{_SKILL} {plugin_version}")
    return " · ".join(parts)


def _evidence_line(item: dict, repos: list[dict]) -> str:
    kind = item["type"]
    if kind == "file":
        path, line = item["path"], item.get("line")
        place = path if line is None else f"{path}:{line}"
        repo = next((known for known in repos if known["name"] == item.get("repo")), None)
        if repo is None:
            return place
        return f"{place} ({blob_url(repo['web_url'], repo['audited_sha'], path, line)})"
    if kind in ("page", "http"):
        detail = _text(item.get("detail"))
        return f"{item['url']} : {detail}" if detail else item["url"]
    if kind == "tool":
        return f"{item['tool']} : {item['ref']}"
    raise ValueError(f"type de preuve inconnu : {kind}")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _others(count: int, noun: str = "") -> str:
    plural = "s" if count > 1 else ""
    text = f"et {count} autre{plural}"
    return f"{text} {noun}{plural}" if noun else text


def _shorten(value: str, excess: int) -> str:
    keep = max(len(value) - excess - 1, 0)
    return value[:keep].rstrip() + "…"


def _ref_text(ref) -> str:
    return ref if isinstance(ref, str) else ref_label(ref)


def _count(value) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return int(value or 0)


def _count_line(label: str, count: int) -> Optional[str]:
    return f"{label} : {count}" if count else None


def _refs_line(label: str, refs: list[str], limit: int) -> Optional[str]:
    if not refs:
        return None
    shown = refs[:limit]
    if not shown:
        return f"{label} : {len(refs)}"
    line = f"{label} ({len(refs)}) : {', '.join(shown)}"
    if len(shown) < len(refs):
        line += f" {_others(len(refs) - len(shown))}"
    return line


def _status_lines(counts: dict) -> list[str]:
    known = [status for status in _STATUS_ORDER if status in counts]
    ordered = known + [status for status in counts if status not in _STATUS_ORDER]
    return [f"- {status} : {counts[status]}" for status in ordered if counts[status]]


def _trim_url(url: str) -> str:
    while url:
        last = url[-1]
        unbalanced = last in _URL_CLOSERS and url.count(last) > url.count(_URL_CLOSERS[last])
        if last not in _URL_TRAILING and not unbalanced:
            break
        url = url[:-1]
    return url


def _width(char: str) -> int:
    return 2 if ord(char) > 0xFFFF else 1


def _units(text: str) -> int:
    return sum(_width(char) for char in text)


def _plain_objects(text: str) -> list[dict]:
    objects = []
    start = size = 0
    for index, char in enumerate(text):
        width = _width(char)
        if size + width > MAX_TEXT_LENGTH:
            objects.append({"type": "text", "text": {"content": text[start:index]}})
            start, size = index, 0
        size += width
    if start < len(text):
        objects.append({"type": "text", "text": {"content": text[start:]}})
    return objects
