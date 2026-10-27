"""Detect and mask secrets. Scans report labels, never values."""
import os
import re
from collections.abc import Mapping
from pathlib import Path

MASK = "[secret masqué]"

_ENV_NAMES = ("NOTION_TOKEN", "GM_AUDIT_AUTH_PASS")
_MIN_ENV_LENGTH = 6

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    r"[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)"
)
_AWS = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_GITLAB = re.compile(r"\bgl(?:pat|dt|rt|ptt|cbt|oas|ft|imt|agent|soat)-[0-9A-Za-z_.\-]{20,}")
_GITHUB = re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{22,})")
_NOTION = re.compile(r"\b(?:ntn|secret)_[0-9A-Za-z]{40,}")
_JWT = re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}")

# redact keeps "pre" and masks "val". "q" is an opening quote: a quoted value runs to its
# closing quote, a bare one to the next space or quote. The lookahead skips masked values.
# The bounded name suffix keeps scans linear on long runs of keywords.
_CREDENTIAL = re.compile(
    r"""
    (?P<pre>
        (?:password|passwd|pwd|secret|token|api[_-]?key)[\w\-]{0,64}
        \\?["']?[ \t]*(?:=>|[:=])[ \t]*
        (?:\\?(?P<q>["']))?
    )
    (?!\[secret\ masqué\])
    (?P<val>(?(q)(?:(?!\\?(?P=q))[^\n]){4,}|[^\s"'`]{4,}))
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A bare credential needs 16 characters, one after a scheme (Basic, Bearer...) needs 4.
_AUTH_HEADER = re.compile(
    r"""
    (?P<pre>
        \bauthorization\\?["']?[ \t]*(?:=>|[:=])[ \t]*\\?["']?
        (?P<scheme>(?:basic|bearer|digest|token|negotiate|ntlm)[ \t]+)?
    )
    (?P<val>(?(scheme)[\w.~+/=\-]{4,}|[\w.~+/=\-]{16,}))
    """,
    re.IGNORECASE | re.VERBOSE,
)

_PATTERNS = (
    ("private_key", _PRIVATE_KEY),
    ("aws_key", _AWS),
    ("gitlab_token", _GITLAB),
    ("github_token", _GITHUB),
    ("notion_token", _NOTION),
    ("jwt", _JWT),
    ("credential_assignment", _CREDENTIAL),
    ("authorization_header", _AUTH_HEADER),
)


def scan_text(text: str, known_values: list[str]) -> list[str]:
    """Return the labels found in text, each once, in a fixed order."""
    labels = []
    if any(value in text for value in known_values if value):
        labels.append("known_value")
    labels.extend(label for label, pattern in _PATTERNS if pattern.search(text))
    return labels


def redact(text: str, known_values: list[str]) -> str:
    """Replace every secret found by MASK, keeping what precedes the value."""
    values = sorted({value for value in known_values if value}, key=len, reverse=True)
    if values:
        text = re.sub("|".join(re.escape(value) for value in values), lambda _: MASK, text)
    for _, pattern in _PATTERNS:
        text = pattern.sub(_mask, text)
    return text


def known_values_from_env(env: Mapping[str, str] = os.environ) -> list[str]:
    """Return the values of the secret environment variables that are long enough to match."""
    values = (env.get(name, "").strip() for name in _ENV_NAMES)
    return [value for value in values if len(value) >= _MIN_ENV_LENGTH]


def scan_tree(root: Path, known_values: list[str]) -> list[tuple[str, str]]:
    """Return (relative path, label) pairs for every file under root, sorted by path.

    A missing root or an unreadable file raises OSError instead of passing as clean.
    """
    root = Path(root)
    found = []
    for directory, _, names in os.walk(root, onerror=_raise):
        for name in names:
            path = Path(directory, name)
            if not path.is_file():
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
            relative = path.relative_to(root).as_posix()
            found.extend((relative, label) for label in scan_text(text, known_values))
    found.sort(key=lambda pair: pair[0])
    return found


def _mask(match: re.Match) -> str:
    return (match.groupdict().get("pre") or "") + MASK


def _raise(error: OSError) -> None:
    raise error
