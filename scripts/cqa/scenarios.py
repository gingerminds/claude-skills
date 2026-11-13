"""Scenarios of the audit: the interaction scenarios the toolbox plays on the target
(scenarios-followup.json), as the agents ask for them in needs_scenarios."""
from pathlib import Path
from urllib.parse import urlsplit

from cqa import probes
from cqa.findings import (
    ACTION_URL_REASON,
    MAX_URL,
    UTF8_REASON,
    is_utf8_encodable,
    requests_action_url,
    utf16_length,
    validate_document,
)

MAX_FOLLOWUP_SCENARIOS = 20
CAP_REASON = f"plafond de {MAX_FOLLOWUP_SCENARIOS} scénarios par suivi atteint"
LENGTH_REASON = f"URL de plus de {MAX_URL} caractères"
COLLISION_REASON = "nom de fichier du résultat en collision"


def followup_scenarios(run_dir: Path) -> dict:
    """Return the scenarios the findings of a run ask for, on the origin of the audit only.

    The result is {"base_url", "scenarios", "lots", "discarded"}. scenarios follows the toolbox
    format, {"id": "<lot>:<id of the agent>", "url", "profile", "steps"}, and holds at most
    MAX_FOLLOWUP_SCENARIOS of them; lots lists the lots with at least one scenario kept;
    discarded lists {lot, ref, id, url, reason} for each request kept out: its URL is not on the
    origin of the target (a scenario must never leave it), runs a PHP file outside the
    application routes (validate already refuses it, this is a second barrier), acts (see
    requests_action_url) or is unusable, or the cap is reached (a request kept out for another
    reason does not use a slot). The URL kept is the origin of the target plus the path and
    the query of the request, and the tool counts its length in UTF-16 units (an emoji counts
    for two). The tool names the result of a scenario <lot>-<id>-<profile> and refuses a second
    scenario of the same name: it is discarded here (COLLISION_REASON), so that no kept scenario
    shares its result file. Only the valid findings of findings/<lot>.json are read, never the
    follow-up files.
    """
    run_dir = Path(run_dir)
    context = probes._read_json(run_dir / "context.json", "le diagnostic")
    if not isinstance(context, dict):
        raise ValueError("context.json : un objet JSON est attendu.")
    base = probes.target_url(context)
    parts = urlsplit(base)
    origin = probes._origin(parts)
    root = f"{parts.scheme}://{parts.netloc}"
    lots = probes._lot_entries(probes._read_json(run_dir / "lots.json", "cqa_plan.py lots"))
    scenarios, discarded, kept_lots, names = [], [], [], set()
    for lot_id, ref, request in _requested(run_dir, lots, parts.path):
        if is_utf8_encodable(request):
            url, reason = _checked_url(request["url"], origin, root, parts.path)
        else:
            url, reason = None, UTF8_REASON
        name = _result_name(lot_id, request)
        if url is not None and name in names:
            url, reason = None, COLLISION_REASON
        if url is not None and len(scenarios) >= MAX_FOLLOWUP_SCENARIOS:
            url, reason = None, CAP_REASON
        if url is None:
            shown = probes._public_url(request["url"])
            shown_id = probes._printable(request["id"])
            discarded.append(
                {"lot": lot_id, "ref": ref, "id": shown_id, "url": shown, "reason": reason}
            )
            continue
        names.add(name)
        scenarios.append(
            {
                "id": f"{lot_id}:{request['id']}",
                "url": url,
                "profile": request["profile"],
                "steps": request["steps"],
            }
        )
        kept_lots.append(lot_id)
    return {
        "base_url": base,
        "scenarios": scenarios,
        "lots": list(dict.fromkeys(kept_lots)),
        "discarded": discarded,
    }


def pending_lots(run_dir: Path, lots: list, app_path: str = "") -> list:
    """Return the ids of the lots whose valid first-round findings still ask for scenarios.

    Only the path of the application (app_path) matters, for the validity of the findings: a
    lot is pending whatever becomes of its URLs.
    """
    pending = _requested(Path(run_dir), probes._lot_entries({"lots": lots}), app_path)
    return list(dict.fromkeys(lot_id for lot_id, _, _ in pending))


def _requested(run_dir: Path, lots: list, app_path: str):
    """Yield (lot id, ref, scenario) for each needs_scenarios entry of the valid first-round
    findings."""
    for lot in lots:
        document = probes._first_round(run_dir, lot["id"])
        valid, _ = (
            validate_document(document, set(lot["refs"]), app_path=app_path)
            if document
            else ([], [])
        )
        for item in valid:
            for request in item.get("needs_scenarios") or ():
                yield lot["id"], item["ref"], request


def _checked_url(url: str, origin: tuple, root: str, app_path: str) -> tuple:
    """Return (the URL to play, None), or (None, the reason the URL is refused)."""
    path, reason = probes._followup_path(url, origin, app_path)
    if path is None:
        return None, reason
    kept = root + path
    if utf16_length(kept) > MAX_URL:
        return None, LENGTH_REASON
    if requests_action_url(kept):
        return None, ACTION_URL_REASON
    return kept, None


def _result_name(lot_id: str, request: dict) -> str:
    """Return the name the tool gives the result of a scenario, without its extension."""
    return f"{lot_id}-{request['id']}-{request['profile']}"


def printable_discarded(discarded: list) -> list:
    """Return the discarded entries as they may be printed: a URL that carries a token or an
    action is cut before its query and fragment (the file of the run keeps the whole URL)."""
    shown = []
    for entry in discarded:
        url = entry["url"]
        if entry["reason"] == ACTION_URL_REASON or requests_action_url(url):
            url = url.partition("#")[0].partition("?")[0]
        shown.append({**entry, "url": url})
    return shown
