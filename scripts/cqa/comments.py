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

_VERDICT_BY_LABEL = {label: verdict for verdict, label in VERDICT_LABELS.items()}
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
    the summary is cut with an ellipsis, then the other lines if that is not enough.
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
    *, head: str, verdict: str, summary: str, evidence: list[str], extras: list[tuple], foot: str
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
