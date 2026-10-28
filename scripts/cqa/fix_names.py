"""Names and git helper shared by the fix mode: branches, commit subjects, category slugs."""
import re
import unicodedata
from pathlib import Path
from typing import Optional

BRANCH_PREFIX = "feature/checklist-qa-fix-"
DRY_RUN_PREFIX = "dry-run/checklist-qa-fix-"
COMMIT_TYPES = ("fix", "feat", "refactor", "chore", "perf", "docs", "style")
SUBJECT = re.compile(r"^(fix|feat|refactor|chore|perf|docs|style)\(CQ-([0-9]+)\): \S")


def ref_of_subject(subject: str) -> Optional[int]:
    """Return the checklist ref of a commit subject such as "fix(CQ-107): x", else None."""
    found = SUBJECT.match(subject)
    return int(found.group(2)) if found else None


def category_slug(name: str) -> str:
    """Return the lowercase ASCII slug of a category name. Raises ValueError when empty."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        raise ValueError(f"nom de catégorie inutilisable : {name!r}")
    return slug


def branch_matches(branch: str, prefix: str, slug: str) -> bool:
    """Return True for "<prefix><slug>" and "<prefix><slug>-<n>"."""
    return re.fullmatch(re.escape(prefix + slug) + r"(?:-[0-9]+)?", branch) is not None


def git(path: Path, run, *args: str):
    """Run git in path with the hooks of the repo disabled; return the CompletedProcess."""
    return run(
        ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
