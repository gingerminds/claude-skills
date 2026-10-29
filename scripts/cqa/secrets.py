"""Detect and mask secrets. Scans report labels, never values."""
import fnmatch
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
# The password glued to -p on the command line of a MySQL or MariaDB client (ps auxww). The
# scan of a line stops at the next client name, which keeps it linear.
_MYSQL_PASSWORD = re.compile(
    r"(?P<pre>\b(?:mysql|mariadb)[\w-]*\b(?:(?!\b(?:mysql|mariadb))[^\n])*?[ \t]-p)"
    r"(?!\[secret masqué\])(?P<val>'[^'\n]*'|\"[^\"\n]*\"|\S+)"
)
# The private key of a kubeconfig user (base64).
_KUBE_CLIENT_KEY = re.compile(r"(?P<pre>\bclient-key-data:[ \t]*)(?P<val>[A-Za-z0-9+/=]{8,})")

# redact keeps "pre" and masks "val". "q" is an opening quote, maybe after a string prefix "sp"
# (b'', r'', f''...): a quoted value runs to its closing quote, a bare one to the next space or
# quote. The lookahead skips masked values. The bounded name suffix keeps scans linear on long
# runs of keywords.
_CREDENTIAL = re.compile(
    r"""
    (?P<pre>
        (?P<kw>password|passwd|pwd|secret|token|api[_-]?key)[\w\-]{0,64}
        \\?["']?[ \t]*(?:=>|[:=])[ \t]*
        (?:(?P<sp>[bBrRuUfF]{1,2})(?=\\?["']))?
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

# A form field description (type=password, autocomplete=current-password), for a password
# keyword only. Another "=" or a "&" means a following pair, which may hold a secret.
_FORM_KEYWORDS = ("password", "passwd", "pwd")
_FORM_ATTRIBUTE = re.compile(r"(?:type|autocomplete|name|id)=[^=&]*", re.IGNORECASE)
# ${NAME}, or ${NAME:?message} whose message is an error text, never a value (unlike :- or :=).
_ENV_REFERENCE = re.compile(r"\$\{[A-Za-z_]\w*(?::?\?[^{}\n]*)?\}")
# A command substitution that reads its value ($(cat /run/secrets/x), $(openssl rand -hex 32)).
# In each piped command, beyond its name: one lowercase word at most (a subcommand), then only
# numbers, options (never -p<value> nor a credential name), lowercase absolute paths and $VARS.
_COMMAND_SUBSTITUTION = re.compile(r"\$\(([^()\n]*)\)")
_PRINTING_COMMANDS = frozenset({"echo", "printf", "command", "builtin", "exec"})
_COMMAND_WORD = re.compile(r"[a-z][a-z0-9_.-]*")
_SAFE_ARGUMENT = re.compile(
    r"""
    \d+
    | -(?!p.)[a-z0-9][a-z0-9-]*
    | --(?![a-z0-9-]*(?:pass|pwd|secret|token|key))[a-z][a-z0-9-]*(?:=(?:\d+|[a-z][a-z0-9_-]*))?
    | /[a-z0-9/._-]*
    | (?P<dq>"?)\$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*)(?P=dq)
    """,
    re.VERBOSE,
)
# A whole bare value that is a variable reference ($password, ${DB_PASSWORD}, $x->y(, $_ENV[,
# $a[0]), ended by punctuation. A call takes one $variable or Name::class argument at most.
# $$ (compose escape), $2y$ (hash) or $X&token=... are not.
_VARIABLE_REFERENCE = re.compile(
    r"""
    \$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*)
    (?:(?:\??->|::|\.)[A-Za-z_]\w*|\((?:\$[A-Za-z_]\w*|[A-Za-z_]\w*::class)?\)?|\[[\w$]*\]?)*
    [.,;)\]]*
    """,
    re.VERBOSE,
)
# An f-string that only formats names: placeholders that start with a name and hold no quote,
# joined by separators, maybe after an auth scheme (f'Bearer {token}'). Any other literal text
# may be the secret.
_SEPARATORS = r"[\s:/@.,;=_-]*"
_PLACEHOLDER = r"\{[A-Za-z_][^{}'\"\\\n]*\}"
_F_TEMPLATE = re.compile(
    rf"(?:(?:basic|bearer|digest|token)(?=[ \t]))?{_SEPARATORS}{_PLACEHOLDER}"
    rf"(?:{_SEPARATORS}{_PLACEHOLDER})*{_SEPARATORS}",
    re.IGNORECASE,
)
_CODE_PREFIX = re.compile(r"[A-Za-z_][\w\\]*(?:::|->)")
_CODE_CALL = re.compile(r"[A-Za-z_][\w.\\]*\(.*\)", re.DOTALL)
_QUOTED_LITERAL = re.compile(r"""(?:(?<!\w)|(?<=\b[bBrRuUfF])|(?<=\b[bBrRfF][bBrRfF]))(["'])(.*?)\1""")
_N_LITERAL = re.compile(r"""(?<![A-Za-z])N(["'])([^\s"']+)\1(?=[)\],;]|\s*$)""", re.MULTILINE)
_ACCESSOR_CALL = re.compile(
    r"\b(?:input|get|post|query|header|cookie|env|getenv|config|old|only|except|has|filled"
    r"|string|boolean|integer)\s*\(\s*\Z"
)
_HARMLESS_LITERAL = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+|[A-Z][A-Z0-9_]*")
# A value cut at a space before "(" or "[" is a call or index: its arguments stay masked.
_OPENING_BRACKET = re.compile(r"[ \t]*(?:\(|\[(?!secret masqué\]))")
# Literal mode (the guard of the fix mode): an unquoted value that starts as code (a call, a
# negated call or variable, a cast) is an expression, never a literal. A shell expansion
# ($(...), ${...}, :-) keeps the rules above, since its words may be the value.
_EXPRESSION_START = re.compile(
    r"(?:![ \t]*)?[A-Za-z_][\w.\\]*\(|![ \t]*\$[A-Za-z_]"
    r"|\((?:string|int|integer|bool|boolean|float|double|array|object)\)",
    re.IGNORECASE,
)
_SHELL_EXPANSION = re.compile(r"\$[({]|`|:-")


def _is_non_secret(match: re.Match, literal: bool = False) -> bool:
    """True when a credential match is a form field description or a pure code expression.

    With literal, an unquoted value is also no secret when it is an expression whose quoted
    literals are all harmless (see _is_expression).
    """
    value = match.group("val")
    if match.group("q"):
        f_string = "f" in (match.group("sp") or "").lower()
        return _is_reference(value) or (f_string and bool(_F_TEMPLATE.fullmatch(value)))
    if match.group("kw").lower() in _FORM_KEYWORDS and _FORM_ATTRIBUTE.fullmatch(value):
        return True
    text = match.string
    end = text.find("\n", match.end("val"))
    rest = text[match.start("val"):None if end < 0 else end]
    expression = rest.rstrip().rstrip(",;]").rstrip()
    if _is_reference(expression):
        return True
    is_code = (
        bool(_VARIABLE_REFERENCE.fullmatch(value))
        or bool(_CODE_PREFIX.match(value))
        or bool(_CODE_CALL.fullmatch(expression))
        or (value.startswith(":") and text[match.start("val") - 1:match.start("val")] == ":")
    )
    if not is_code and literal:
        is_code = _is_expression(value, rest)
    if not is_code or ":-" in rest:
        return False
    literals = list(_QUOTED_LITERAL.finditer(rest)) + list(_N_LITERAL.finditer(rest))
    return all(_is_harmless_literal(rest, literal) for literal in literals)


def _is_expression(value: str, rest: str) -> bool:
    """True when an unquoted value starts an expression (call, cast, negation) that holds no
    shell expansion and hides no other credential assignment in its first word."""
    return (
        bool(_EXPRESSION_START.match(rest))
        and not _SHELL_EXPANSION.search(rest)
        and _CREDENTIAL.search(value) is None
    )


def _is_reference(text: str) -> bool:
    """True when text is a whole environment reference or a command substitution that reads it."""
    return bool(_ENV_REFERENCE.fullmatch(text)) or _reads_its_value(text)


def _reads_its_value(text: str) -> bool:
    substitution = _COMMAND_SUBSTITUTION.fullmatch(text)
    if not substitution:
        return False
    for command in substitution.group(1).split("|"):
        name, *arguments = command.split() or [""]
        if name in _PRINTING_COMMANDS or not _COMMAND_WORD.fullmatch(name):
            return False
        words = [item for item in arguments if not _SAFE_ARGUMENT.fullmatch(item)]
        if len(words) > 1 or not all(_COMMAND_WORD.fullmatch(word) for word in words):
            return False
    return True


def _is_harmless_literal(line: str, literal: re.Match) -> bool:
    content = literal.group(2)
    if not content or _HARMLESS_LITERAL.fullmatch(content):
        return True
    if line[literal.end():].lstrip().startswith("=>"):
        return True
    before = line[:literal.start()]
    if before.rstrip().endswith("[") and line[literal.end():].lstrip().startswith("]"):
        return True
    return bool(_ACCESSOR_CALL.search(before))


_PATTERNS = (
    ("private_key", _PRIVATE_KEY),
    ("aws_key", _AWS),
    ("gitlab_token", _GITLAB),
    ("github_token", _GITHUB),
    ("notion_token", _NOTION),
    ("jwt", _JWT),
    ("credential_assignment", _CREDENTIAL),
    ("authorization_header", _AUTH_HEADER),
    ("mysql_password_option", _MYSQL_PASSWORD),
    ("kube_client_key", _KUBE_CLIENT_KEY),
)


def scan_text(text: str, known_values: list[str], *, literal_credentials: bool = False) -> list[str]:
    """Return the labels found in text, each once, in a fixed order.

    With literal_credentials (the guard of the fix mode only), credential_assignment flags a
    literal value only, a quoted string or a bare token: an expression on the right (a call,
    a cast, a negation) whose quoted literals are all harmless is no secret.
    """
    labels = []
    if any(value in text for value in known_values if value):
        labels.append("known_value")
    labels.extend(
        label for label, pattern in _PATTERNS if _search(pattern, text, literal_credentials)
    )
    return labels


def _search(pattern: re.Pattern, text: str, literal: bool = False) -> bool:
    return any(not _skipped(pattern, m, literal) for m in pattern.finditer(text))


def _skipped(pattern: re.Pattern, match: re.Match, literal: bool = False) -> bool:
    return pattern is _CREDENTIAL and _is_non_secret(match, literal)


def redact(text: str, known_values: list[str]) -> str:
    """Replace every secret found by MASK, keeping what precedes the value."""
    values = sorted({value for value in known_values if value}, key=len, reverse=True)
    if values:
        text = re.sub("|".join(re.escape(value) for value in values), lambda _: MASK, text)
    for _, pattern in _PATTERNS:
        if pattern is _CREDENTIAL:
            text = _redact_credentials(text)
        else:
            text = pattern.sub(_mask, text)
    return text


def known_values_from_env(env: Mapping[str, str] = os.environ) -> list[str]:
    """Return the values of the secret environment variables that are long enough to match."""
    values = (env.get(name, "").strip() for name in _ENV_NAMES)
    return [value for value in values if len(value) >= _MIN_ENV_LENGTH]


def scan_tree(
    root: Path,
    known_values: list[str],
    exclude: tuple[str, ...] = (),
    skip_dirs: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Return (relative path, label) pairs for every file under root, sorted by path.

    Paths whose first segment is in exclude are skipped, and so are the directories whose
    relative path matches a skip_dirs pattern segment by segment ("fix/*/*/wt").

    A missing root or an unreadable file raises OSError instead of passing as clean.
    """
    root = Path(root)
    found = []
    for directory, subdirs, names in os.walk(root, onerror=_raise):
        if skip_dirs:
            subdirs[:] = [
                name for name in subdirs
                if not _skipped_dir(Path(directory, name).relative_to(root).as_posix(), skip_dirs)
            ]
        for name in names:
            path = Path(directory, name)
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.split("/", 1)[0] in exclude:
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
            found.extend((relative, label) for label in scan_text(text, known_values))
    found.sort(key=lambda pair: pair[0])
    return found


def _skipped_dir(relative: str, patterns: tuple[str, ...]) -> bool:
    parts = relative.split("/")
    for pattern in patterns:
        wanted = pattern.split("/")
        if len(wanted) == len(parts) and all(
            fnmatch.fnmatchcase(part, glob) for part, glob in zip(parts, wanted)
        ):
            return True
    return False


def _redact_credentials(text: str) -> str:
    """Mask credential values; an unquoted call or index is masked to the end of line."""
    out = []
    position = 0
    for match in _CREDENTIAL.finditer(text):
        if match.start() < position or _is_non_secret(match):
            continue
        end = match.end("val")
        value = match.group("val")
        if not match.group("q") and (
            "(" in value or "[" in value or _OPENING_BRACKET.match(text, end)
        ):
            newline = text.find("\n", end)
            end = len(text) if newline < 0 else newline
        out.append(text[position:match.start()] + match.group("pre") + MASK)
        position = end
    return "".join(out) + text[position:]


def _mask(match: re.Match) -> str:
    return (match.groupdict().get("pre") or "") + MASK


def _raise(error: OSError) -> None:
    raise error
