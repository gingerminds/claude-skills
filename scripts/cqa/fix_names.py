"""Names and git helper shared by the fix mode: branches, commit subjects, category slugs."""
import re
import unicodedata
from pathlib import Path
from typing import Optional

BRANCH_PREFIX = "feature/checklist-qa-fix-"
DRY_RUN_PREFIX = "dry-run/checklist-qa-fix-"
BRANCH_BASE = "feature/checklist-qa-fix"
DRY_RUN_BASE = "dry-run/checklist-qa-fix"
SUBJECT_MAX = 72
SECTION_ALIASES = {
    "SÉCURITÉ": "securite",
    "QUALITÉ": "qualite",
    "ACCESSIBILITÉ": "accessibilite",
    "SEO / GEO": "seo",
    "ÉCO-CONCEPTION / WEBPERFORMANCE": "eco",
    "RGPD & LÉGAL": "rgpd",
    "PROCESS INTERNE": "process",
}
COMMIT_TYPES = ("fix", "feat", "refactor", "chore", "perf", "docs", "style")
# The subject the guard accepts for a new commit: type(<alias> CQ-<n>): summary.
SUBJECT = re.compile(r"^(fix|feat|refactor|chore|perf|docs|style)\(([a-z0-9]+) CQ-([0-9]+)\): \S")
# Documentation for the light review and the tolerant replay; never a file the site serves.
DOC_SUFFIXES = (".md", ".rst", ".adoc")


# Also reads the v2 subject type(CQ-<n>): summary, still found in the commits of a v2 MR.
_SUBJECT_PARTS = re.compile(
    r"^(?:fix|feat|refactor|chore|perf|docs|style)\((?:([a-z0-9]+) )?CQ-([0-9]+)\): \S"
)


def parse_subject(subject: str) -> Optional[tuple]:
    """Return (alias, ref) for "type(alias CQ-n): x", (None, ref) for "type(CQ-n): x", else None."""
    found = _SUBJECT_PARTS.match(subject)
    return (found.group(1), int(found.group(2))) if found else None


def ref_of_subject(subject: str) -> Optional[int]:
    """Return the checklist ref of a commit subject, in the 0.15 or the v2 form, else None."""
    parsed = parse_subject(subject)
    return parsed[1] if parsed else None


def is_documentation(path: str) -> bool:
    """Return True for a *.md, *.rst or *.adoc file (robots.txt, llms.txt... never are)."""
    return path.casefold().endswith(DOC_SUFFIXES)


def section_alias(category: str) -> str:
    """Return the commit alias of a category: the fixed one, else the first word of its slug."""
    if category in SECTION_ALIASES:
        return SECTION_ALIASES[category]
    slug = category_slug(category)
    for name, alias in SECTION_ALIASES.items():
        if category_slug(name) == slug:
            return alias
    return slug.split("-")[0]


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


def is_pass_branch(name: str, base: str) -> bool:
    """Return True for "<base>" and "<base>-<n>"."""
    return re.fullmatch(re.escape(base) + r"(?:-[0-9]+)?", name) is not None


def is_fix_branch(name: str) -> bool:
    """Return True for a pass branch or a v2 "<base>-<slug>[-n]" branch, never a dry-run one."""
    return name == BRANCH_BASE or name.startswith(BRANCH_PREFIX)


def git(path: Path, run, *args: str, binary: bool = False):
    """Run git in path with the hooks and the fsmonitor of the repo disabled.

    Returns the CompletedProcess, with bytes in stdout and stderr when binary is True (no
    newline translation: a "\\r" stays in its line).
    """
    options = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    return run(
        ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         *args],
        capture_output=True,
        **options,
    )


def git_out(path: Path, run, *args: str) -> Optional[str]:
    """Return the stripped output of a git command, or None when it fails or prints nothing."""
    result = git(path, run, *args)
    text = result.stdout.strip() if result.returncode == 0 else ""
    return text or None
