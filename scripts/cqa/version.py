import json
from pathlib import Path

_PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


def plugin_version() -> str:
    """Return the plugin version declared in .claude-plugin/plugin.json."""
    return json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
