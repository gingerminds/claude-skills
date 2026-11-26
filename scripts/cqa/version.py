import json
import re
from pathlib import Path
from typing import Optional

_PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def plugin_version() -> str:
    """Return the plugin version declared in .claude-plugin/plugin.json."""
    return json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))["version"]


def parse(text) -> Optional[tuple]:
    """Return a version as a tuple of integers ("0.18.1" gives (0, 18, 1)), None when unreadable.

    Only dotted integers are read: a suffix, a prefix or spaces make the version unreadable.
    """
    if not isinstance(text, str) or not _VERSION.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))
