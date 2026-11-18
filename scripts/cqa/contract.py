"""The rules a findings file of a run is read under: those of 0.18.0, or the stricter ones of 0.18.1.

0.18.1 checks more of the evidence (a page or an http URL on a host of the target, an http URL a
probe requested, a tool whose result is ok), the keys a lot may write and its "lot" field, and adds
two guardrails (an na without evidence, a conforming report). A run started before 0.18.1
(context.json plugin_version, an absent or unreadable one counting as older) was audited under the
old rules: reading it again with the new ones would lose findings already validated. Its findings
files keep the old rules, unless validate checked them since (the file has its block in
findings/<lot>.checked.json): from then on they follow the new rules, whatever the run.
"""
import json
from pathlib import Path

from cqa import checked
from cqa.version import parse

STRICT_SINCE = (0, 18, 1)


def legacy_run(context) -> bool:
    """Whether the run was started before 0.18.1, by the plugin_version of its context.json."""
    found = parse(context.get("plugin_version")) if isinstance(context, dict) else None
    return found is None or found < STRICT_SINCE


def strict_file(run_dir: Path, context, lot_id: str, name: str) -> bool:
    """Whether findings/<name> of the lot follows the rules of 0.18.1."""
    return not legacy_run(context) or checked.has_block(run_dir, lot_id, name)


def run_context(run_dir: Path) -> dict:
    """Return context.json of the run, {} when it cannot be read (a legacy run, then)."""
    try:
        document = json.loads((Path(run_dir) / "context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}
