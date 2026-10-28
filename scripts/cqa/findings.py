"""Findings written by the audit sub-agents: validation and verdict guardrails."""
import copy
import re
from collections import Counter
from typing import Iterable
from urllib.parse import unquote, urlsplit

from cqa.checklist import ref_label

VERDICTS = ("ko", "partial", "unknown", "ok_human", "ok", "na")
CONFIDENCES = ("high", "medium", "low")
REQUIRED_FINDING_KEYS = (
    "ref",
    "verdict",
    "confidence",
    "summary",
    "evidence",
    "todo",
    "human_check",
    "divergence",
    "needs_probes",
)
EVIDENCE_REQUIRED = {
    "file": ("repo", "path"),
    "page": ("url",),
    "tool": ("tool", "ref"),
    "http": ("url", "detail"),
}
PROBE_METHODS = ("GET", "HEAD")
MAX_SUMMARY_LENGTH = 600
MAX_FIELD_LENGTH = 400
PHP_FILE_REASON = "fichier PHP hors des routes de l'application"

_PHP_FILE = re.compile(r"\.(?:php\d?|phtml|phar)(?:$|/|\?)", re.IGNORECASE)
_PHP_ENTRY_POINTS = ("/index.php", "/wp-login.php")
_PHP_AJAX = "/wp-admin/admin-ajax.php"

_HUMAN_TOOL = "Intervention humaine"
_OPTIONAL_TEXTS = ("todo", "human_check", "divergence")
_RANK = {verdict: index for index, verdict in enumerate(VERDICTS)}


def worst(verdicts: Iterable[str]) -> str:
    """Return the worst of the verdicts, in the order of VERDICTS (ko worst, na best)."""
    verdicts = list(verdicts)
    if not verdicts:
        raise ValueError("aucun verdict à comparer")
    unknown = [verdict for verdict in verdicts if verdict not in _RANK]
    if unknown:
        raise ValueError(f"verdict inconnu : {unknown[0]}")
    return min(verdicts, key=_RANK.__getitem__)


def validate_document(doc: dict, lot_refs: set[int]) -> tuple[list[dict], list[str]]:
    """Return (valid findings, errors in French) for a findings/<lot>.json document.

    Every problem of the document is reported in one pass, because the sub-agent gets to fix
    its file once. A finding is returned (as a deep copy) only when it has no problem. A ref
    that is outside the lot or that appears in several findings is rejected with all its
    copies: which copy is right cannot be told, and the row then counts as not evaluated.
    A lot row that has no finding at all is reported as missing; a row whose finding is
    invalid is not, since the error of that finding already names it.
    """
    if not isinstance(doc, dict):
        return [], ['le document doit être un objet JSON {"lot": ..., "findings": [...]}']
    errors = []
    if "lot" not in doc:
        errors.append("champ « lot » manquant")
    elif not _is_text(doc["lot"]):
        errors.append("« lot » doit être un texte non vide")
    if "findings" not in doc:
        return [], errors + ["champ « findings » manquant"]
    items = doc["findings"]
    if not isinstance(items, list):
        return [], errors + ["« findings » doit être une liste"]

    counts = Counter(
        item["ref"] for item in items if isinstance(item, dict) and _is_int(item.get("ref"))
    )
    valid, seen, reported = [], set(), set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"constat n° {index} : doit être un objet JSON")
            continue
        ref = item.get("ref")
        in_lot = _is_int(ref) and ref in lot_refs
        label = ref_label(ref) if _is_int(ref) else f"n° {index}"
        problems = _finding_problems(item)
        if _is_int(ref) and not in_lot:
            problems.insert(0, "Réf hors du lot")
        errors.extend(f"constat {label} : {problem}" for problem in problems)
        duplicate = in_lot and counts[ref] > 1
        if duplicate and ref not in reported:
            reported.add(ref)
            errors.append(f"Réf en double : {ref_label(ref)} ({counts[ref]} constats)")
        if in_lot:
            seen.add(ref)
        if not problems and not duplicate:
            valid.append(copy.deepcopy(item))
    errors.extend(f"constat manquant : {ref_label(ref)}" for ref in sorted(lot_refs - seen))
    return valid, errors


def apply_guardrails(finding: dict, row: dict, *, followup_done: bool) -> dict:
    """Return a copy of the finding with the verdict lowered where the rules require it.

    The copy carries "guardrail": the French reasons (joined with "; ") of the rules that
    changed the verdict, or None. Rules, in this order, each on the verdict left by the
    previous ones:
    1. ok without evidence, or with a confidence other than high, becomes ok_human;
    2. ok on a row that asks for "Intervention humaine" becomes ok_human;
    3. a filled divergence turns ok and ok_human into partial;
    4. needs_probes still filled after the follow-up cap the verdict at unknown: ko and partial
       are kept, ok_human, ok and na become unknown;
    5. an ok whose todo or human_check is filled becomes ok_human (contradictory finding);
    6. an na whose confidence is not high becomes unknown.
    A guardrail the sub-agent wrote itself is always overwritten.
    """
    result = copy.deepcopy(finding)
    verdict = result["verdict"]
    reasons = []
    if verdict == "ok" and not result.get("evidence"):
        verdict = "ok_human"
        reasons.append("aucune preuve : ok abaissé à ok_human")
    if verdict == "ok" and result.get("confidence") != "high":
        verdict = "ok_human"
        reasons.append("confiance non haute : ok abaissé à ok_human")
    if verdict == "ok" and _HUMAN_TOOL in (row.get("tools") or []):
        verdict = "ok_human"
        reasons.append("ligne avec intervention humaine : ok abaissé à ok_human")
    if verdict in ("ok", "ok_human") and _filled(result.get("divergence")):
        reasons.append(f"écart code / préprod : {verdict} abaissé à partial")
        verdict = "partial"
    if followup_done and result.get("needs_probes") and worst([verdict, "unknown"]) != verdict:
        reasons.append(f"sondes en attente après la relance : {verdict} abaissé à unknown")
        verdict = "unknown"
    if verdict == "ok" and (_filled(result.get("todo")) or _filled(result.get("human_check"))):
        verdict = "ok_human"
        reasons.append("à faire ou à valider renseigné : ok abaissé à ok_human")
    if verdict == "na" and result.get("confidence") != "high":
        verdict = "unknown"
        reasons.append("confiance non haute : na abaissé à unknown")
    result["verdict"] = verdict
    result["guardrail"] = "; ".join(reasons) if reasons else None
    return result


def requests_php_file(url: str) -> bool:
    """Whether a GET of url would run a PHP file other than a public entry point.

    The path is percent-decoded first. Allowed: /index.php and /wp-login.php, with or without a
    query, and /wp-admin/admin-ajax.php without one. A backup name such as x.php.bak is not a
    PHP file. A URL that cannot be parsed is left to the other checks.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    path = unquote(parts.path)
    folded = path.casefold()
    if folded in _PHP_ENTRY_POINTS or (folded == _PHP_AJAX and not parts.query):
        return False
    return bool(_PHP_FILE.search(path))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _filled(value) -> bool:
    return value is not None and bool(str(value).strip())


def _show(value) -> str:
    text = f"« {value} »" if isinstance(value, str) else repr(value)
    return text if len(text) <= 60 else text[:59] + "…"


def _finding_problems(item: dict) -> list[str]:
    problems = []
    missing = [key for key in REQUIRED_FINDING_KEYS if key not in item]
    if missing:
        noun = "champ manquant" if len(missing) == 1 else "champs manquants"
        problems.append(f"{noun} : {', '.join(missing)}")
    if "ref" in item and not _is_int(item["ref"]):
        problems.append("« ref » doit être un entier")
    if "verdict" in item and not (isinstance(item["verdict"], str) and item["verdict"] in VERDICTS):
        problems.append(
            f"verdict {_show(item['verdict'])} inconnu (valeurs possibles : {', '.join(VERDICTS)})"
        )
    if "confidence" in item and not (
        isinstance(item["confidence"], str) and item["confidence"] in CONFIDENCES
    ):
        problems.append(
            f"confiance {_show(item['confidence'])} inconnue "
            f"(valeurs possibles : {', '.join(CONFIDENCES)})"
        )
    if "summary" in item:
        problems.extend(_summary_problems(item["summary"]))
    for key in _OPTIONAL_TEXTS:
        if key in item:
            problems.extend(_optional_text_problems(key, item[key]))
    if "evidence" in item:
        problems.extend(_evidence_problems(item["evidence"]))
    if "needs_probes" in item:
        problems.extend(_probe_problems(item["needs_probes"]))
    return problems


def _summary_problems(value) -> list[str]:
    if not isinstance(value, str):
        return ["« summary » doit être un texte"]
    if not value.strip():
        return ["« summary » ne doit pas être vide"]
    if len(value) > MAX_SUMMARY_LENGTH:
        return [f"« summary » dépasse {MAX_SUMMARY_LENGTH} caractères ({len(value)})"]
    return []


def _optional_text_problems(key: str, value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        return [f"« {key} » doit être un texte ou null"]
    if len(value) > MAX_FIELD_LENGTH:
        return [f"« {key} » dépasse {MAX_FIELD_LENGTH} caractères ({len(value)})"]
    return []


def _evidence_problems(evidence) -> list[str]:
    if not isinstance(evidence, list):
        return ["« evidence » doit être une liste"]
    problems = []
    kinds = ", ".join(EVIDENCE_REQUIRED)
    for index, item in enumerate(evidence, start=1):
        place = f"preuve n° {index}"
        if not isinstance(item, dict):
            problems.append(f"{place} : doit être un objet")
            continue
        kind = item.get("type")
        if "type" not in item:
            problems.append(f"{place} : type manquant ({kinds})")
            continue
        if not (isinstance(kind, str) and kind in EVIDENCE_REQUIRED):
            problems.append(f"{place} : type {_show(kind)} inconnu ({kinds})")
            continue
        place = f"{place} ({kind})"
        problems.extend(_required_text_problems(place, item, EVIDENCE_REQUIRED[kind]))
        problems.extend(_evidence_extras(place, kind, item))
    return problems


def _evidence_extras(place: str, kind: str, item: dict) -> list[str]:
    if kind == "file" and item.get("line") is not None:
        line = item["line"]
        if not _is_int(line) or line < 1:
            return [f"{place} : « line » doit être un entier positif"]
    if kind == "page" and item.get("detail") is not None and not isinstance(item["detail"], str):
        return [f"{place} : « detail » doit être un texte ou null"]
    return []


def _probe_problems(probes) -> list[str]:
    if not isinstance(probes, list):
        return ["« needs_probes » doit être une liste"]
    problems = []
    for index, probe in enumerate(probes, start=1):
        place = f"sonde n° {index}"
        if not isinstance(probe, dict):
            problems.append(f"{place} : doit être un objet")
            continue
        problems.extend(_required_text_problems(place, probe, ("url",)))
        if _is_text(probe.get("url")) and requests_php_file(probe["url"]):
            problems.append(
                f"{place} : {PHP_FILE_REASON} (seuls /index.php, /wp-login.php et "
                "/wp-admin/admin-ajax.php sans paramètre sont admis)"
            )
        if "method" not in probe:
            problems.append(f"{place} : « method » manquant")
        elif not (isinstance(probe["method"], str) and probe["method"] in PROBE_METHODS):
            problems.append(f"{place} : « method » doit valoir {' ou '.join(PROBE_METHODS)}")
    return problems


def _required_text_problems(place: str, item: dict, keys: Iterable[str]) -> list[str]:
    problems = []
    for key in keys:
        if key not in item:
            problems.append(f"{place} : « {key} » manquant")
        elif not _is_text(item[key]):
            problems.append(f"{place} : « {key} » doit être un texte non vide")
    return problems
