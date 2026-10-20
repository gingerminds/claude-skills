"""Team names: matching with the Notion "Assignée à" options and assignment proposal."""
import unicodedata
from typing import Optional

from cqa.checklist import STATUS_PARTIAL, STATUS_TODO

_KEPT_STATUSES = (STATUS_TODO, STATUS_PARTIAL)
_FULLSTACK = "fullstack"
_FRONT = "Front"
_BACK = "Back"
_UNKNOWN_VERDICT = "unknown"


def normalize(s: str) -> str:
    """Return s without accents, casefolded, with whitespace runs collapsed and trimmed."""
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def match_option(full_name: str, options: list[str]) -> Optional[str]:
    """Return the option of "Assignée à" that stands for a person, None when there is none.

    Options with the person's first name as their first word are searched. A single one
    without second word is the answer; otherwise the answer is the option made of the first
    name and the initial of the surname ("Julien L"). Accents and case are ignored.
    """
    words = normalize(full_name).split()
    if not words:
        return None
    first_name = words[0]
    split = [(option, normalize(option).split()) for option in options]
    candidates = [(option, parts) for option, parts in split if parts and parts[0] == first_name]
    if len(candidates) == 1 and len(candidates[0][1]) == 1:
        return candidates[0][0]
    if len(words) < 2:
        return None
    initial = words[1][0]
    for option, parts in candidates:
        if parts[1:] == [initial]:
            return option
    return None


def propose_assignment(
    rows: list[dict], team: list[dict], lead_option: str, *, include_unknown: bool = False
) -> list[dict]:
    """Propose an assignee for each row left to do, in the order of rows.

    Kept rows are "À traiter" and "Mise en place partielle", plus, with include_unknown, the
    rows whose "verdict" key is "unknown". Candidates depend on the row's Section: Front only
    takes the front and full-stack devs, Back only the back and full-stack devs, Front and
    Back (or no Section) the full-stack devs, else every dev. A dev without role is
    full-stack. The least loaded candidate wins, the order of team breaking ties. A row
    without candidate goes to the lead, which counts in the load of that option.
    """
    load = {}
    proposal = []
    for row in rows:
        if not _is_kept(row, include_unknown):
            continue
        options = [member["option"] for member in _candidates(row, team)]
        assignee = min(options, key=lambda option: load.get(option, 0)) if options else lead_option
        load[assignee] = load.get(assignee, 0) + 1
        proposal.append(
            {
                "ref": row["ref"],
                "page_id": row["page_id"],
                "title": row["title"],
                "sections": list(row.get("sections") or []),
                "assignee": assignee,
            }
        )
    return proposal


def _is_kept(row: dict, include_unknown: bool) -> bool:
    if row.get("status") in _KEPT_STATUSES:
        return True
    return include_unknown and row.get("verdict") == _UNKNOWN_VERDICT


def _role(member: dict) -> str:
    return member.get("role") or _FULLSTACK


def _candidates(row: dict, team: list[dict]) -> list[dict]:
    sections = set(row.get("sections") or [])
    front, back = _FRONT in sections, _BACK in sections
    if front and not back:
        roles = ("front", _FULLSTACK)
    elif back and not front:
        roles = ("back", _FULLSTACK)
    else:
        fullstack = [member for member in team if _role(member) == _FULLSTACK]
        return fullstack or list(team)
    return [member for member in team if _role(member) in roles]
