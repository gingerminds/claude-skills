"""Row selection, coverage and lots of the audit."""
import re
import unicodedata
from collections import Counter
from typing import Optional

from cqa.checklist import STATUS_STANDBY

TOOLS = [
    "Code",
    "CLI local",
    "HTTP",
    "Navigateur",
    "Audit web",
    "Crawler",
    "SSH serveur",
    "BDD",
    "Comptes de test",
    "Service externe",
    "Intervention humaine",
]

TOOL_ACTIONS = {
    "Audit web": "docker login + pull de la boîte à outils",
    "Crawler": "boîte à outils 1.1 ou plus (docker pull)",
    "SSH serveur": "alias SSH vers la préprod",
    "BDD": "CLI de l'application accessible en SSH",
    "Navigateur": "Claude in Chrome connecté",
    "HTTP": "accès à la préprod (réseau de l'agence ou identifiants)",
    "CLI local": "runner du projet démarré",
    "Comptes de test": "comptes de test",
    "Service externe": "préprod publique (à J0)",
    "Code": "repos présents et à jour",
}

_HUMAN = "Intervention humaine"
_BROWSER = "Navigateur"
_SERVER_TOOLS = ("SSH serveur", "BDD")
_TOOLBOX_TOOLS = ("Audit web", "Crawler")
_DEFERRED_PHASES = frozenset({"MEL J0", "Post-MEL J+7"})
_MERGED_CATEGORIES = {"process-interne": "process-rgpd", "rgpd-legal": "process-rgpd"}
_BUCKETS = {"standby": "standby", "techno": "na", "phase": "deferred", "no_tools": "not_evaluable"}
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_TEST_ACCOUNTS = "Comptes de test"
_BACKOFFICE_SECTION = re.compile(r"^(back|bo$)", re.IGNORECASE)
# Matches tool names, not substrings ("syntaxe", "taxe").
_TOOLBOX_HINT = re.compile(
    r"gitleaks|testssl|lighthouse|\baxe\b|validateur nu|\bnu html|yellow ?lab|redocly"
    r"|schemathesis|\bcrawl",
    re.IGNORECASE,
)


def select_rows(
    rows: list[dict], technos: list[str], available_tools: list[str], phase_filter: Optional[str]
) -> tuple[list[dict], list[dict]]:
    """Split rows into the rows to audit and the rows excluded before any evaluation.

    The first rule that matches excludes a row: standby, no fiche, techno, phase, no tool.
    A row without phase never counts as deferred. With a phase_filter, only the rows that
    contain that phase are kept. "Intervention humaine" is not a tool: a row that asks for
    nothing else is kept. An excluded entry is {ref, page_id, reason, verdict, detail, tools};
    tools lets coverage count the rows a missing tool would bring back.
    """
    kept, excluded = [], []
    for row in rows:
        found = _exclusion(row, technos, set(available_tools), phase_filter)
        if found is None:
            kept.append(row)
            continue
        reason, verdict, detail = found
        excluded.append(
            {
                "ref": row.get("ref"),
                "page_id": row["page_id"],
                "reason": reason,
                "verdict": verdict,
                "detail": detail,
                "tools": list(row.get("tools") or []),
            }
        )
    return kept, excluded


def coverage(kept: list[dict], excluded: list[dict], available_tools: list[str]) -> dict:
    """Count the rows by what the lead's workstation lets the skill do, and the gains.

    total is every row received, including those without fiche, which belong to no other
    count. evaluable holds the rows whose required tools (human intervention aside) are all
    available; evaluable_human is the part of them that also asks for a human validation.
    partial holds the kept rows with a missing tool. not_evaluable, deferred, na and standby
    are the rows excluded for no_tools, phase, techno and standby.

    gains lists each unavailable tool with the number of rows, kept or excluded for no_tools,
    that require it, most rows first (ties in TOOLS order). Tools no row requires are left
    out, and human intervention never counts as a required tool.
    """
    available = set(available_tools)
    counts = {
        "total": len(kept) + len(excluded),
        "evaluable": 0,
        "evaluable_human": 0,
        "partial": 0,
        "not_evaluable": 0,
        "deferred": 0,
        "na": 0,
        "standby": 0,
    }
    needed = Counter()
    for row in kept:
        required = _required(row)
        if available.issuperset(required):
            counts["evaluable"] += 1
            if _HUMAN in (row.get("tools") or []):
                counts["evaluable_human"] += 1
        else:
            counts["partial"] += 1
        needed.update(set(required))
    for entry in excluded:
        bucket = _BUCKETS.get(entry["reason"])
        if bucket:
            counts[bucket] += 1
        if entry["reason"] == "no_tools":
            needed.update(set(_required(entry)))
    gains = [
        {"tool": tool, "action": TOOL_ACTIONS[tool], "rows": needed[tool]}
        for tool in TOOLS
        if tool not in available and needed[tool]
    ]
    gains.sort(key=lambda gain: -gain["rows"])
    counts["gains"] = gains
    return counts


def build_lots(kept: list[dict], max_size: int = 20) -> list[dict]:
    """Group the kept rows into lots, one per sub-agent, in dispatch order.

    A lot is {id, kind, needs_toolbox, needs_server_facts, needs_session, refs}, refs in
    ascending order.
    Rows are routed by the tools they ask for, whether those tools are available or not:
    browser lots (accessibility apart, no size limit), a server lot, then code lots of at
    most max_size rows (a category group is split by sub-category, then into balanced
    numbered chunks). Browser rows that need a session (test accounts, back-office section)
    go to their own "-session" lots, so the others run without a login. Rows that need the
    toolbox (a toolbox tool, or a tool named in tooling_hint) move to a sibling "<id>-outils"
    lot. Order: server, code, browser, browser "-session", then every sibling.
    """
    rows = sorted(kept, key=lambda row: row["ref"])
    server, code = [], []
    browser = {"accessibilite": [], "autres": [], "accessibilite-session": [], "autres-session": []}
    for row in rows:
        kind = _kind(row)
        if kind == "server":
            server.append(row)
        elif kind == "browser":
            key = "accessibilite" if _slug(row.get("category")) == "accessibilite" else "autres"
            browser[key + ("-session" if _needs_session(row) else "")].append(row)
        else:
            code.append(row)

    groups = [("server", "server", server)]
    groups += [(lot_id, "code", lot_rows) for lot_id, lot_rows in _code_groups(code, max_size)]
    groups += [(f"browser-{key}", "browser", browser[key]) for key in browser]

    plain, with_toolbox = [], []
    groups = [group for group in groups if group[2]]
    for lot_id, kind, lot_rows in groups:
        needing = [row for row in lot_rows if _needs_toolbox(row)]
        others = [row for row in lot_rows if not _needs_toolbox(row)]
        if others:
            plain.append(_lot(lot_id, kind, others, needs_toolbox=False))
        if needing:
            with_toolbox.append(_lot(f"{lot_id}-outils", kind, needing, needs_toolbox=True))
    return plain + with_toolbox


def lot_needs_session(lot: dict) -> bool:
    """Read needs_session of a lot entry; a lots.json of an older run has none: false."""
    return lot.get("needs_session") is True


def _exclusion(row, technos, available, phase_filter):
    if row.get("status") == STATUS_STANDBY:
        return "standby", "skip", "ligne en standby"
    if not row.get("fiche_path"):
        return "no_fiche", "skip", "fiche introuvable dans le modèle"
    row_technos = row.get("technos") or []
    if row_technos and not set(row_technos) & set(technos):
        project = " + ".join(technos) or "sans techno renseignée"
        return "techno", "na", f"tâche propre à {' + '.join(row_technos)}, projet {project}"
    phases = row.get("phases") or []
    if phase_filter:
        if phase_filter not in phases:
            return "phase", "skip", f"tâche hors de la phase demandée ({phase_filter})"
    elif phases and set(phases) <= _DEFERRED_PHASES:
        return "phase", "skip", f"tâche reportée à {' + '.join(phases)}"
    required = _required(row)
    if required and available.isdisjoint(required):
        return "no_tools", "unknown", f"outils indisponibles : {', '.join(required)}"
    return None


def _required(row):
    return [tool for tool in row.get("tools") or [] if tool != _HUMAN]


def _kind(row):
    tools = row.get("tools") or []
    if _BROWSER in tools:
        return "browser"
    if any(tool in tools for tool in _SERVER_TOOLS):
        return "server"
    return "code"


def _needs_toolbox(row):
    if any(tool in (row.get("tools") or []) for tool in _TOOLBOX_TOOLS):
        return True
    return bool(_TOOLBOX_HINT.search(row.get("tooling_hint") or ""))


def _needs_session(row):
    if _TEST_ACCOUNTS in (row.get("tools") or []):
        return True
    return any(
        _BACKOFFICE_SECTION.match((section or "").strip()) for section in row.get("sections") or []
    )


def _lot(lot_id, kind, rows, needs_toolbox):
    server_facts = kind == "browser" and any(
        tool in (row.get("tools") or []) for row in rows for tool in _SERVER_TOOLS
    )
    return {
        "id": lot_id,
        "kind": kind,
        "needs_toolbox": needs_toolbox,
        "needs_server_facts": server_facts,
        "needs_session": any(_needs_session(row) for row in rows) if kind == "browser" else False,
        "refs": [row["ref"] for row in rows],
    }


def _code_groups(rows, max_size):
    by_category = {}
    for row in rows:
        category = _slug(row.get("category")) or "sans-categorie"
        by_category.setdefault(_MERGED_CATEGORIES.get(category, category), []).append(row)
    groups = []
    for category, group in by_category.items():
        if len(group) <= max_size:
            groups.append((f"code-{category}", group))
            continue
        by_subcategory = {}
        for row in group:
            sub = _slug(row.get("subcategory")) or "sans-sous-categorie"
            by_subcategory.setdefault(sub, []).append(row)
        for sub, sub_rows in by_subcategory.items():
            lot_id = f"code-{category}-{sub}"
            if len(sub_rows) <= max_size:
                groups.append((lot_id, sub_rows))
                continue
            for number, chunk in enumerate(_balanced_chunks(sub_rows, max_size), 1):
                groups.append((f"{lot_id}-{number}", chunk))
    return groups


def _balanced_chunks(rows, max_size):
    count = -(-len(rows) // max_size)
    size, extra = divmod(len(rows), count)
    chunks, start = [], 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        chunks.append(rows[start:end])
        start = end
    return chunks


def _slug(text):
    decomposed = unicodedata.normalize("NFD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_SLUG.sub("-", plain.lower()).strip("-")
