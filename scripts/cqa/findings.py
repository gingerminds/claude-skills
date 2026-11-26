"""Findings written by the audit sub-agents: validation and verdict guardrails."""
import copy
import json
import re
from collections import Counter
from typing import Iterable, Optional
from urllib.parse import unquote, unquote_plus, urlsplit

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
# The keys the sub-agents may write (the schema documents them): a finding of a lot file holds no
# other. "source", "guardrail" and the keys the reader of the findings sets are the code's own
# (see CODE_KEYS).
AGENT_FINDING_KEYS = REQUIRED_FINDING_KEYS + ("needs_scenarios", "tool_dates", "in_repo")
# Set by the reader of the findings (cqa.rerun): true on a finding read under the rules of a run
# started before 0.18.1 (cqa.contract), and the evidence it dropped, each as {"evidence": <the
# piece of evidence>, "problem": <French text>, "label": <short French reason>}.
LEGACY_KEY = "legacy_contract"
REFUSED_KEY = "refused_evidence"
CODE_KEYS = {
    "source": "cqa_plan.py l'écrit seul, d'après le verdict d'un document de contexte",
    "guardrail": "le script de plan le calcule seul",
    LEGACY_KEY: "le lecteur des constats l'écrit seul, d'après la version du run",
    REFUSED_KEY: "le lecteur des constats l'écrit seul, d'après le contrôle des preuves",
}
EVIDENCE_REQUIRED = {
    "file": ("repo", "path"),
    "page": ("url",),
    "tool": ("tool", "ref"),
    "http": ("url", "detail"),
    "document": ("doc", "ref"),
    "fact": ("topic",),
}
FACT_EVIDENCE = "fact"
CONTEXT_DOC_SOURCE = "context_doc"
PROBE_METHODS = ("GET", "HEAD")
# Scenario requests (needs_scenarios): data validated by whitelist and caps, never code. The step
# types, the keys and the caps copy STEP_TYPES, PRESS_KEYS and FOLLOWUP_LIMITS of the gm-audit
# mobile tool (src/mobile-scenarios.js): no test links the two repos, keep them in sync.
SCENARIO_PROFILES = ("portrait", "paysage")
SCENARIO_STEP_TYPES = ("tab", "press", "tap", "select", "state", "capture")
SCENARIO_PRESS_KEYS = (
    "Escape",
    "Tab",
    "Shift+Tab",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
)
MAX_SCENARIOS = 3
MAX_STEPS = 15
MAX_SELECTOR = 200
MAX_TAB_COUNT = 30
MAX_URL = 2000
# The URLs that act: the load of a scenario URL is a GET that no guard closes. These closed lists
# mirror ACTION_SEGMENTS, ACTION_VALUES and TOKEN_PARAMS of src/mobile-scenarios.js in the gm-audit
# toolbox (CONTRACT.md 1.4.0, section "mobile --scenarios", "URL d'action"); a list is never longer
# on one side only. Compared without case.
ACTION_SEGMENTS = (
    "logout",
    "signout",
    "sign-out",
    "deconnexion",
    "deconnecter",
    "delete",
    "remove",
    "trash",
    "publish",
    "unpublish",
    "unsubscribe",
    "desinscription",
    "cron",
    "clear-cache",
    "flush",
    "add-to-cart",
    "addtocart",
    "add_to_cart",
    "checkout",
    "purge",
    "disconnect",
    "logoff",
    "signoff",
    "sign-off",
)
ACTION_KEYS = ("action", "op", "do")
ACTION_VALUES = (
    "trash",
    "delete",
    "remove",
    "logout",
    "publish",
    "unpublish",
    "clear",
    "add",
    "purge",
    "flush",
    "signout",
    "disconnect",
    "unsubscribe",
    "add-to-cart",
    "addtocart",
    "checkout",
)
TOKEN_PARAMS = (
    "token",
    "_token",
    "csrf",
    "nonce",
    "_wpnonce",
    "form_token",
    "key",
    "signature",
    "sig",
    "access_token",
    "id_token",
    "refresh_token",
    "api_key",
    "apikey",
    "auth",
)
# The parameters that hold a route (Drupal 7 ?q=user/logout): read like a path.
ROUTE_PARAMS = ("q", "p", "route")
ACTION_URL_REASON = "URL d'action refusée"
UTF8_REASON = "texte non encodable en UTF-8 (demi-substitut Unicode isolé)"
MAX_SUMMARY_LENGTH = 600
MAX_FIELD_LENGTH = 400
PHP_FILE_REASON = "fichier PHP hors des routes de l'application"
DOCUMENT_ROW_PROBLEM = (
    "ligne jugée d'après un document de contexte, à retirer du fichier "
    "(seule une relance la juge à nouveau)"
)

# A PHP extension the server may run: not followed by a letter, digit, "_", "-" or "~", so
# x.php.jpg, x.php%20 or x.php;.jpg count, unless a backup suffix ends the path (x.php.bak).
_PHP_FILE = re.compile(
    r"\.(?:php\d*|pht|phtml|phar)(?![\w~-])"
    r"(?!\.(?:bak|old|orig|save|swp|swo|tmp|dist|backup)$)",
    re.IGNORECASE,
)
_PHP_ENTRY_POINTS = ("/index.php", "/wp-login.php")
_PHP_AJAX = "/wp-admin/admin-ajax.php"
_FRONT_CONTROLLER = "/index.php/"
_DOT_SEGMENT = re.compile(r"(?:^|/)\.\.?(?:/|$)")

_SCENARIO_ID = re.compile(r"[a-z0-9-]{1,40}")
_SCENARIO_KEYS = ("id", "url", "profile", "steps")
_STEP_KEYS = {
    "tab": ("count", "shift"),
    "press": ("key",),
    "tap": ("selector",),
    "select": ("selector", "value"),
    "state": ("selector",),
    "capture": (),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
# What the URL parser of the tool drops before it reads a URL.
_URL_DROPPED = re.compile(r"[\t\r\n]")
_URL_TRIMMED = "".join(chr(code) for code in range(0x21))
_PERCENT_BYTE = re.compile(rb"%([0-9a-fA-F]{2})")
_PATH_SEPARATORS = re.compile(r"[/\\]")

_TOOL_NAME = re.compile(r"[a-z0-9-]+")
_TOOL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z")

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


def validate_document(
    doc: dict,
    lot_refs: set[int],
    *,
    app_path: str = "",
    optional_refs: frozenset = frozenset(),
    forbidden_refs: frozenset = frozenset(),
    agent_file: bool = False,
    lot_id: Optional[str] = None,
) -> tuple[list[dict], list[str]]:
    """Return (valid findings, errors in French) for a findings/<lot>.json document.

    app_path is the path the application is served under, for the PHP rule of needs_probes and
    needs_scenarios (see requests_php_file). optional_refs are rows of the lot that the file need
    not give (the rows judged from a context document): one is never reported missing, but a
    finding the file gives for it is validated like any other. forbidden_refs are rows of the lot
    that the file must not give (the same rows, in a first-round or follow-up file: only a rerun
    judges them again): a finding for one is an error and is never returned.

    agent_file is true for a file a sub-agent writes (every findings/<lot>*.json but
    findings/context-docs.json, which the code writes): it holds no evidence of type document, no
    key of CODE_KEYS and no key other than AGENT_FINDING_KEYS, so a finding that breaks one is
    invalid. lot_id is the id of the lot the file is validated for: a "lot" field that
    holds another id refuses the whole file (no finding is returned).

    Every problem of the document is reported in one pass, because the sub-agent gets to fix
    its file once. A finding is returned (as a deep copy) only when it has no problem. A ref
    that is outside the lot or that appears in several findings is rejected with all its
    copies: which copy is right cannot be told, and the row then counts as not evaluated.
    A lot row that has no finding at all is reported as missing; a row whose finding is
    invalid is not, since the error of that finding already names it. A scenario id
    (needs_scenarios) that two findings of the lot hold is reported once, and invalidates all its
    holders.
    """
    if not isinstance(doc, dict):
        return [], ['le document doit être un objet JSON {"lot": ..., "findings": [...]}']
    errors = []
    other_lot = False
    if "lot" not in doc:
        errors.append("champ « lot » manquant")
    elif not _is_text(doc["lot"]):
        errors.append("« lot » doit être un texte non vide")
    elif lot_id is not None and doc["lot"] != lot_id:
        other_lot = True
        errors.append(
            f"« lot » vaut {_show(doc['lot'])} mais le fichier est validé pour le lot {_show(lot_id)}"
        )
    if "findings" not in doc:
        return [], errors + ["champ « findings » manquant"]
    items = doc["findings"]
    if not isinstance(items, list):
        return [], errors + ["« findings » doit être une liste"]

    counts = Counter(
        item["ref"] for item in items if isinstance(item, dict) and _is_int(item.get("ref"))
    )
    clashes = _scenario_id_clashes(items, lot_refs)
    valid, seen, reported, reported_ids = [], set(), set(), set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"constat n° {index} : doit être un objet JSON")
            continue
        ref = item.get("ref")
        in_lot = _is_int(ref) and ref in lot_refs
        label = ref_label(ref) if _is_int(ref) else f"n° {index}"
        problems = _finding_problems(item, app_path, agent_file)
        if _is_int(ref) and not in_lot:
            problems.insert(0, "Réf hors du lot")
        elif in_lot and ref in forbidden_refs:
            problems.append(DOCUMENT_ROW_PROBLEM)
        errors.extend(f"constat {label} : {problem}" for problem in problems)
        duplicate = in_lot and counts[ref] > 1
        if duplicate and ref not in reported:
            reported.add(ref)
            errors.append(f"Réf en double : {ref_label(ref)} ({counts[ref]} constats)")
        clashing = False
        for scenario_id in _requested_ids(item) if in_lot else ():
            if scenario_id in clashes:
                clashing = True
                if scenario_id not in reported_ids:
                    reported_ids.add(scenario_id)
                    holders = ", ".join(ref_label(holder) for holder in clashes[scenario_id])
                    errors.append(f"identifiant de scénario en double : {scenario_id} ({holders})")
        if in_lot:
            seen.add(ref)
        if not problems and not duplicate and not clashing:
            valid.append(copy.deepcopy(item))
    errors.extend(
        f"constat manquant : {ref_label(ref)}" for ref in sorted(lot_refs - seen - optional_refs)
    )
    return ([] if other_lot else valid), errors


def apply_guardrails(finding: dict, row: dict, *, followup_done: bool) -> dict:
    """Return a copy of the finding with the verdict lowered where the rules require it.

    The copy carries "guardrail": the French reasons (joined with "; ") of the rules that
    changed the verdict, or None. Rules, in this order, each on the verdict left by the
    previous ones:
    1. ok without evidence, or with a confidence other than high, becomes ok_human (a fact of the
       server, FACT_EVIDENCE, is not evidence for an ok);
    2. ok on a row that asks for "Intervention humaine" becomes ok_human;
    3. a filled divergence turns ok and ok_human into partial;
    4. needs_probes or needs_scenarios still filled after the follow-up cap the verdict at unknown:
       ko and partial are kept, ok_human, ok and na become unknown;
    5. an ok whose todo or human_check is filled becomes ok_human (contradictory finding);
    6. an na whose confidence is not high becomes unknown;
    7. an na without evidence becomes unknown;
    8. an ok built from a context document (source) becomes ok_human: a report may be older than
       the code.
    A finding read under the rules of a run started before 0.18.1 (LEGACY_KEY true, see
    cqa.contract) skips rules 7 and 8, which that run was not audited under. The evidence the reader
    dropped (REFUSED_KEY) is named first: the rules then judge the evidence left. A guardrail the
    sub-agent wrote itself is always overwritten.
    """
    result = copy.deepcopy(finding)
    verdict = result["verdict"]
    legacy = result.get(LEGACY_KEY) is True
    reasons = _refused_problems(result.get(REFUSED_KEY))
    evidence = result.get("evidence")
    if verdict == "ok" and not _supports_ok(evidence):
        verdict = "ok_human"
        reasons.append(
            "aucune preuve contrôlable (un fait relevé par SSH ne suffit pas pour un ok) : "
            "ok abaissé à ok_human"
            if evidence
            else "aucune preuve : ok abaissé à ok_human"
        )
    if verdict == "ok" and result.get("confidence") != "high":
        verdict = "ok_human"
        reasons.append("confiance non haute : ok abaissé à ok_human")
    if verdict == "ok" and _HUMAN_TOOL in (row.get("tools") or []):
        verdict = "ok_human"
        reasons.append("ligne avec intervention humaine : ok abaissé à ok_human")
    if verdict in ("ok", "ok_human") and _filled(result.get("divergence")):
        reasons.append(f"écart code / préprod : {verdict} abaissé à partial")
        verdict = "partial"
    pending = result.get("needs_probes") or result.get("needs_scenarios")
    if followup_done and pending and worst([verdict, "unknown"]) != verdict:
        reasons.append(
            f"sondes ou scénarios en attente après la relance : {verdict} abaissé à unknown"
        )
        verdict = "unknown"
    if verdict == "ok" and (_filled(result.get("todo")) or _filled(result.get("human_check"))):
        verdict = "ok_human"
        reasons.append("à faire ou à valider renseigné : ok abaissé à ok_human")
    if verdict == "na" and result.get("confidence") != "high":
        verdict = "unknown"
        reasons.append("confiance non haute : na abaissé à unknown")
    if not legacy and verdict == "na" and not result.get("evidence"):
        verdict = "unknown"
        reasons.append("aucune preuve : na abaissé à unknown")
    if not legacy and verdict == "ok" and result.get("source") == CONTEXT_DOC_SOURCE:
        verdict = "ok_human"
        reasons.append("conforme d'après un rapport tiers : ok abaissé à ok_human")
    result["verdict"] = verdict
    result["guardrail"] = "; ".join(reasons) if reasons else None
    return result


def _supports_ok(evidence) -> bool:
    """Whether some piece of the evidence can support an ok: any but a fact of the server."""
    if not isinstance(evidence, list):
        return bool(evidence)
    return any(not (isinstance(item, dict) and item.get("type") == FACT_EVIDENCE) for item in evidence)


def _refused_problems(refused) -> list:
    """Return the French texts of the evidence the reader dropped (REFUSED_KEY), [] when none."""
    if not isinstance(refused, list):
        return []
    return [
        entry["problem"]
        for entry in refused
        if isinstance(entry, dict) and isinstance(entry.get("problem"), str)
    ]


def requests_php_file(url: str, app_path: str = "") -> bool:
    """Whether a GET of url would run a PHP file other than a public entry point.

    The path is percent-decoded first. app_path is the path the application is served under
    ("" or "/" at the root, "/blog" for https://example.org/blog/). Allowed under it: /index.php,
    also followed by a route (/index.php/node/1), and /wp-login.php, with or without a query,
    and /wp-admin/admin-ajax.php without one; never through a "." or ".." segment. A backup
    name such as x.php.bak is not a PHP file. A URL that cannot be parsed is left to the other
    checks.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    path = unquote(parts.path)
    if _is_entry_point(path.casefold(), _app_prefix(app_path).casefold(), bool(parts.query)):
        return False
    return bool(_PHP_FILE.search(path))


def requests_action_url(url: str) -> bool:
    """Whether a GET of url would act: the mirror of the refusal of the gm-audit mobile tool.

    The path is read as the tool's URL parser reads it (tabs and line breaks dropped, a backslash
    taken for a slash), percent-decoded byte by byte (a malformed %XX stays as it is), put in
    lower case and cut on "/" and "\\", so %2f, %5c and %6c do not hide a word. Each segment
    loses its path parameters (what follows a ";": /logout;jsessionid=x is /logout) and acts
    when it is a whole word of ACTION_SEGMENTS: delete-pro-fr or trash-talk do not. The query
    is cut on "&" only, "+" is a space and %XX is decoded as UTF-8: it acts when a parameter is
    named as one of TOKEN_PARAMS, is named as one of ACTION_KEYS with a value of ACTION_VALUES,
    or is one of ROUTE_PARAMS and holds a route with a "/" one of whose segments is a word of
    ACTION_SEGMENTS (?q=user/logout, but not ?q=logout). The fragment is not read. Dot segments
    are not resolved, so the mirror is never laxer than the tool: it also refuses
    /logout/../page. Anything that cannot be parsed as a URL is left to the other checks.
    """
    if not isinstance(url, str):
        return False
    text = _URL_DROPPED.sub("", url).strip(_URL_TRIMMED).partition("#")[0]
    base, _, query = text.partition("?")
    try:
        path = urlsplit(base.replace("\\", "/")).path
    except ValueError:
        return False
    raw = path.encode("utf-8", "replace")
    decoded = _PERCENT_BYTE.sub(lambda match: bytes([int(match.group(1), 16)]), raw)
    decoded = decoded.decode("latin-1")
    if _has_action_segment(_PATH_SEPARATORS.split(decoded.lower()), path_parameters=True):
        return True
    return any(_is_action_parameter(pair) for pair in query.split("&"))


def _has_action_segment(segments: list, *, path_parameters: bool = False) -> bool:
    if path_parameters:
        segments = [segment.partition(";")[0] for segment in segments]
    return any(segment in ACTION_SEGMENTS for segment in segments)


def _is_action_parameter(pair: str) -> bool:
    name, _, value = pair.partition("=")
    key = unquote_plus(name).lower()
    text = unquote_plus(value).lower()
    if key in TOKEN_PARAMS:
        return True
    if key in ACTION_KEYS:
        return text in ACTION_VALUES
    return key in ROUTE_PARAMS and "/" in text and _has_action_segment(text.split("/"))


def _app_prefix(app_path: str) -> str:
    return unquote(app_path or "").rstrip("/")


def _is_entry_point(path: str, prefix: str, query: bool) -> bool:
    if not path.startswith(f"{prefix}/") or _DOT_SEGMENT.search(path):
        return False
    local = path[len(prefix):]
    if local in _PHP_ENTRY_POINTS or (local == _PHP_AJAX and not query):
        return True
    route = local[len(_FRONT_CONTROLLER):]
    return local.startswith(_FRONT_CONTROLLER) and not _PHP_FILE.search(route)


def is_utf8_encodable(value) -> bool:
    """Whether a text, or any text of a JSON value, can be written to a UTF-8 file.

    A lone surrogate ("\\ud800" in a JSON file) cannot: json.loads accepts it, a write fails.
    """
    try:
        if isinstance(value, str):
            value.encode("utf-8")
        else:
            json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (UnicodeEncodeError, TypeError, ValueError):
        return False
    return True


def utf16_length(text: str) -> int:
    """Length of text in UTF-16 code units, as JavaScript counts it (an emoji counts for two)."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _filled(value) -> bool:
    return value is not None and bool(str(value).strip())


def _show(value) -> str:
    text = f"« {value} »" if isinstance(value, str) else repr(value)
    text = text if len(text) <= 60 else text[:59] + "…"
    return text.encode("utf-8", "replace").decode("utf-8")


def _finding_problems(item: dict, app_path: str, agent_file: bool = False) -> list[str]:
    problems = []
    if agent_file:
        problems.extend(_agent_key_problems(item))
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
        problems.extend(_evidence_problems(item["evidence"], agent_file))
    if "needs_probes" in item:
        problems.extend(_probe_problems(item["needs_probes"], app_path))
    if "needs_scenarios" in item:
        problems.extend(_scenario_problems(item["needs_scenarios"], app_path))
    if "tool_dates" in item:
        problems.extend(_tool_dates_problems(item["tool_dates"]))
    if "in_repo" in item and not isinstance(item["in_repo"], bool):
        problems.append("« in_repo » doit être un booléen (true ou false)")
    return problems


def _agent_key_problems(item: dict) -> list[str]:
    problems = []
    admitted = ", ".join(AGENT_FINDING_KEYS)
    for key in item:
        if key in AGENT_FINDING_KEYS:
            continue
        if key in CODE_KEYS:
            problems.append(f"clé {_show(key)} refusée : {CODE_KEYS[key]}, un lot ne l'écrit jamais")
        else:
            problems.append(f"clé {_show(key)} non admise (clés admises : {admitted})")
    return problems


def _tool_dates_problems(value) -> list[str]:
    if not isinstance(value, dict):
        return ["« tool_dates » doit être un objet {outil: date}"]
    problems = []
    for name, date in value.items():
        if not _TOOL_NAME.fullmatch(name):
            problems.append(f"« tool_dates » : nom d'outil {_show(name)} invalide")
        elif not (isinstance(date, str) and _TOOL_DATE.fullmatch(date)):
            problems.append(
                f"« tool_dates » : date {_show(date)} invalide pour l'outil {_show(name)}"
            )
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


def _evidence_problems(evidence, agent_file: bool = False) -> list[str]:
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
        if agent_file and kind == "document":
            problems.append(
                f"{place} : cqa_plan.py écrit seul une preuve « document », un lot n'en écrit jamais"
            )
            continue
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


def _probe_problems(probes, app_path: str) -> list[str]:
    if not isinstance(probes, list):
        return ["« needs_probes » doit être une liste"]
    problems = []
    for index, probe in enumerate(probes, start=1):
        place = f"sonde n° {index}"
        if not isinstance(probe, dict):
            problems.append(f"{place} : doit être un objet")
            continue
        problems.extend(_required_text_problems(place, probe, ("url",)))
        problems.extend(_utf8_problems(place, probe, "url"))
        problems.extend(_php_file_problems(place, probe.get("url"), app_path))
        if "method" not in probe:
            problems.append(f"{place} : « method » manquant")
        elif not (isinstance(probe["method"], str) and probe["method"] in PROBE_METHODS):
            problems.append(f"{place} : « method » doit valoir {' ou '.join(PROBE_METHODS)}")
    return problems


def _php_file_problems(place: str, url, app_path: str) -> list[str]:
    if not (_is_text(url) and requests_php_file(url, app_path)):
        return []
    prefix = _app_prefix(app_path)
    return [
        f"{place} : {PHP_FILE_REASON} (seuls {prefix}/index.php et ses routes, "
        f"{prefix}/wp-login.php et {prefix}/wp-admin/admin-ajax.php sans paramètre sont admis)"
    ]


def _requested_ids(item: dict) -> list:
    """The valid ids of the scenarios a finding asks for, once each, in order."""
    scenarios = item.get("needs_scenarios")
    if not isinstance(scenarios, list):
        return []
    ids = []
    for scenario in scenarios[:MAX_SCENARIOS]:
        scenario_id = scenario.get("id") if isinstance(scenario, dict) else None
        if (
            isinstance(scenario_id, str)
            and _SCENARIO_ID.fullmatch(scenario_id)
            and scenario_id not in ids
        ):
            ids.append(scenario_id)
    return ids


def _scenario_id_clashes(items: list, lot_refs: set[int]) -> dict:
    """Map each scenario id held by findings of the lot with several refs to those refs, sorted."""
    holders = {}
    for item in items:
        if isinstance(item, dict) and _is_int(item.get("ref")) and item["ref"] in lot_refs:
            for scenario_id in _requested_ids(item):
                holders.setdefault(scenario_id, set()).add(item["ref"])
    return {key: sorted(refs) for key, refs in holders.items() if len(refs) > 1}


def _scenario_problems(scenarios, app_path: str) -> list[str]:
    """Problems of a needs_scenarios value: a request is data, checked against a whitelist.

    At most MAX_SCENARIOS scenarios and MAX_STEPS steps are examined: beyond, only the count is
    reported with the problems of the part that the caps allow.
    """
    if not isinstance(scenarios, list):
        return ["« needs_scenarios » doit être une liste"]
    problems = []
    if len(scenarios) > MAX_SCENARIOS:
        problems.append(
            f"« needs_scenarios » : {len(scenarios)} scénarios ({MAX_SCENARIOS} au plus)"
        )
    taken = {}
    for index, scenario in enumerate(scenarios[:MAX_SCENARIOS], start=1):
        problems.extend(_one_scenario_problems(index, scenario, app_path, taken))
    return problems


def _one_scenario_problems(index: int, scenario, app_path: str, taken: dict) -> list[str]:
    place = f"scénario n° {index}"
    if not isinstance(scenario, dict):
        return [f"{place} : doit être un objet"]
    allowed = ", ".join(_SCENARIO_KEYS)
    problems = [
        f"{place} : clé {_show(key)} non admise ({allowed})"
        for key in scenario
        if key not in _SCENARIO_KEYS
    ]
    scenario_id = scenario.get("id")
    if "id" not in scenario:
        problems.append(f"{place} : « id » manquant")
    elif not (isinstance(scenario_id, str) and _SCENARIO_ID.fullmatch(scenario_id)):
        problems.append(
            f"{place} : « id » doit correspondre à [a-z0-9-]{{1,40}} ({_show(scenario_id)})"
        )
    elif scenario_id in taken:
        shown, first = _show(scenario_id), taken[scenario_id]
        problems.append(f"{place} : « id » {shown} déjà pris par le scénario n° {first}")
    else:
        taken[scenario_id] = index
    problems.extend(_required_text_problems(place, scenario, ("url",)))
    problems.extend(_utf8_problems(place, scenario, "url"))
    url = scenario.get("url")
    if _is_text(url) and utf16_length(url) > MAX_URL:
        problems.append(f"{place} : « url » dépasse {MAX_URL} caractères ({utf16_length(url)})")
    problems.extend(_php_file_problems(place, url, app_path))
    if "profile" not in scenario:
        problems.append(f"{place} : « profile » manquant")
    elif not (isinstance(scenario["profile"], str) and scenario["profile"] in SCENARIO_PROFILES):
        problems.append(f"{place} : « profile » doit valoir {' ou '.join(SCENARIO_PROFILES)}")
    problems.extend(_steps_problems(place, scenario))
    return problems


def _steps_problems(place: str, scenario: dict) -> list[str]:
    if "steps" not in scenario:
        return [f"{place} : « steps » manquant"]
    steps = scenario["steps"]
    if not isinstance(steps, list):
        return [f"{place} : « steps » doit être une liste"]
    if not steps:
        return [f"{place} : « steps » ne doit pas être vide"]
    problems = []
    if len(steps) > MAX_STEPS:
        problems.append(f"{place} : {len(steps)} étapes ({MAX_STEPS} au plus)")
    for index, step in enumerate(steps[:MAX_STEPS], start=1):
        problems.extend(_step_problems(f"{place} : étape n° {index}", step))
    return problems


def _step_problems(place: str, step) -> list[str]:
    if not isinstance(step, dict):
        return [f"{place} : doit être un objet"]
    kinds = ", ".join(SCENARIO_STEP_TYPES)
    if "type" not in step:
        return [f"{place} : « type » manquant ({kinds})"]
    kind = step["type"]
    if not (isinstance(kind, str) and kind in SCENARIO_STEP_TYPES):
        return [f"{place} : type {_show(kind)} inconnu ({kinds})"]
    keys = _STEP_KEYS[kind]
    admitted = f"clés admises : {', '.join(keys)}" if keys else "aucune clé admise"
    problems = [
        f"{place} : clé {_show(key)} non admise pour l'étape {kind} ({admitted})"
        for key in step
        if key != "type" and key not in keys
    ]
    if kind == "tab":
        if "count" in step and not (
            _is_int(step["count"]) and 1 <= step["count"] <= MAX_TAB_COUNT
        ):
            problems.append(f"{place} : « count » doit être un entier de 1 à {MAX_TAB_COUNT}")
        if "shift" in step and not isinstance(step["shift"], bool):
            problems.append(f"{place} : « shift » doit être un booléen (true ou false)")
    elif kind == "press":
        problems.extend(_press_key_problems(place, step))
    if "selector" in keys:
        problems.extend(_step_text_problems(place, step, "selector", blank=False))
    if "value" in keys:
        problems.extend(_step_text_problems(place, step, "value", blank=True))
    return problems


def _press_key_problems(place: str, step: dict) -> list[str]:
    if "key" not in step:
        return [f"{place} : « key » manquant"]
    if isinstance(step["key"], str) and step["key"] in SCENARIO_PRESS_KEYS:
        return []
    keys = f"{', '.join(SCENARIO_PRESS_KEYS[:-1])} ou {SCENARIO_PRESS_KEYS[-1]}"
    return [f"{place} : « key » doit valoir {keys}"]


def _step_text_problems(place: str, step: dict, key: str, *, blank: bool) -> list[str]:
    if key not in step:
        return [f"{place} : « {key} » manquant"]
    value = step[key]
    if not isinstance(value, str) or not (blank or value.strip()):
        return [f"{place} : « {key} » doit être un texte{'' if blank else ' non vide'}"]
    if len(value) > MAX_SELECTOR:
        return [f"{place} : « {key} » dépasse {MAX_SELECTOR} caractères ({len(value)})"]
    if _CONTROL.search(value):
        return [f"{place} : « {key} » contient un caractère de contrôle"]
    return _utf8_problems(place, step, key)


def _utf8_problems(place: str, item: dict, key: str) -> list[str]:
    value = item.get(key)
    if isinstance(value, str) and not is_utf8_encodable(value):
        return [
            f"{place} : « {key} » contient un demi-substitut Unicode isolé "
            "(texte non encodable en UTF-8)"
        ]
    return []


def _required_text_problems(place: str, item: dict, keys: Iterable[str]) -> list[str]:
    problems = []
    for key in keys:
        if key not in item:
            problems.append(f"{place} : « {key} » manquant")
        elif not _is_text(item[key]):
            problems.append(f"{place} : « {key} » doit être un texte non vide")
    return problems
