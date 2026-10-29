"""gitleaks for the guard of the fix mode, through the toolbox launcher (cqa-toolbox.sh secrets).

One run per guard call, on the text the guard hands over (the changed files whole and the
commit messages, joined by fix_guard): the findings come back as (rule, first line, last
line), never a value. The input and the report live in a temporary folder of their own,
removed as soon as the report is read, so that neither the content of the branch nor a value
gitleaks matched stays on disk; only RuleID, StartLine and EndLine are read from the report.
"""
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

GITLEAKS_DOWN = "gitleaks indisponible : contrôle maison seul"
REPORT = "gitleaks.json"
INPUT = "input.txt"
_MAX_REASON = 300


class Gitleaks:
    """Callable text -> [(rule, first, last)], or None when gitleaks did not run (no Docker,
    no image, a failure): note then holds GITLEAKS_DOWN and reason the launcher's last line,
    for the lead (a path or a Docker message, never a value)."""

    def __init__(self, run, launcher: Path):
        self.run = run
        self.launcher = Path(launcher)
        self.note: Optional[str] = None
        self.reason: Optional[str] = None

    def __call__(self, text: str) -> Optional[list]:
        folder = Path(tempfile.mkdtemp(prefix="cqa-secrets-"))
        try:
            source, out_dir = folder / INPUT, folder / "out"
            out_dir.mkdir()
            source.write_bytes(text.encode("utf-8"))
            done = self.run(
                ["bash", str(self.launcher), "secrets", str(source), str(out_dir)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            findings = _findings(out_dir / REPORT) if done.returncode == 0 else None
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if findings is None:
            self.note = GITLEAKS_DOWN
            self.reason = (_last_line(done.stderr) if done.returncode != 0
                           else "rapport gitleaks illisible")
        return findings


def _findings(path: Path) -> Optional[list]:
    """Return [(rule, first, last)] of a gitleaks JSON report, None when it cannot be read.

    A report without finding may be an empty array or null. Any other shape, or an entry
    without a rule or valid lines, makes the whole report unreadable: a finding is never
    dropped quietly.
    """
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if report is None:
        return []
    if not isinstance(report, list):
        return None
    found = []
    for entry in report:
        if not isinstance(entry, dict):
            return None
        rule, first, last = entry.get("RuleID"), entry.get("StartLine"), entry.get("EndLine")
        if not isinstance(rule, str) or not rule or not _is_line(first) or not _is_line(last):
            return None
        found.append((rule, first, max(first, last)))
    return found


def _is_line(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _last_line(text) -> str:
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    return (lines[-1] if lines else "échec sans message")[:_MAX_REASON]
