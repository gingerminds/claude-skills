"""Detect and mask secrets. Scans report labels, never values."""
import base64
import bisect
import fnmatch
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, quote_plus

MASK = "[secret masqué]"

_ENV_NAMES = ("NOTION_TOKEN", "GM_AUDIT_AUTH_PASS")
_AUTH_USER = "GM_AUDIT_AUTH_USER"
_AUTH_PASS = "GM_AUDIT_AUTH_PASS"
_MIN_ENV_LENGTH = 6

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    r"[\s\S]*?(?:(?P<end>-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----)|\Z)"
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
# runs of keywords. A name may close with "]" (['password'] = ...).
_SEPARATOR = r"""\\?["']?\]?[ \t]*(?:=>|[:=])"""
# Strong names: the password, secret and token words, and a name ending in pass (DB_PASS,
# smtp_pass, dbPass) unless a letter comes before it (compass, bypass).
_STRONG_NAMES = (
    r"password|passwd|pwd|secret|token|api[_-]?key|mot[_-]?de[_-]?passe"
    rf"|(?:(?<=[A-Za-z0-9][_.-])|(?-i:(?<=[a-z0-9])(?=P)))pass(?={_SEPARATOR})"
)
# Weak names say less: a bare pass, salt, auth, credentials, a key of a known kind (APP_KEY,
# stripe_key) or any upper-case name ending in _KEY. Their value has to look like a secret.
_WEAK_NAMES = (
    r"(?<![A-Za-z0-9_.-])pass|(?<![A-Za-z])(?:salt|auth|credentials?)"
    r"|(?-i:(?<=[a-z0-9])(?:Salt|Auth|Credentials?))"
    r"|(?<![A-Za-z])(?:app|encryption|signing|master|private|jwt|crypto|cipher|hmac|licen[sc]e"
    r"|access|session|stripe|webhook|service|client)[_-]?key"
    r"|(?-i:\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_KEY)"
)


def _assignment(names: str, suffix: str, minimum: int) -> re.Pattern:
    return re.compile(
        rf"""
        (?P<pre>
            (?P<kw>{names})(?P<suffix>{suffix})
            \\?["']?(?P<br>\])?[ \t]*(?:=>|[:=])[ \t]*
            (?:(?P<sp>[bBrRuUfF]{{1,2}})(?=\\?["']))?
            (?:\\?(?P<q>["']))?
        )
        (?!\[secret\ masqué\])
        (?P<val>(?(q)(?:(?!\\?(?P=q))[^\n]){{{minimum},}}|[^\s"'`]{{{minimum},}}))
        """,
        re.IGNORECASE | re.VERBOSE,
    )


_CREDENTIAL = _assignment(_STRONG_NAMES, r"[\w\-]{0,64}", 3)
_WEAK_CREDENTIAL = _assignment(_WEAK_NAMES, "", 8)
_ASSIGNMENTS = (_CREDENTIAL, _WEAK_CREDENTIAL)
_PASSWORD_WORDS = ("pass", "pwd", "mot")
# A value shorter than this after a name that is not a password word is not a secret.
_MIN_VALUE_LENGTH = 4
_FLAG_VALUES = frozenset({
    "true", "false", "yes", "no", "on", "off", "null", "none", "nil", "ok", "pass", "fail",
    "passed", "failed", "skipped",
})
# What follows a secret word in the name of a setting (password_policy, token_lifetime,
# password_hash): its value is a plain word, a number or a path, not a secret.
_DESCRIPTOR_WORDS = frozenset({
    "policy", "policies", "hash", "hashes", "hashing", "hashed", "algo", "algorithm",
    "algorithms", "length", "min", "max", "minimum", "maximum", "strength", "reset", "expiry",
    "expiration", "expire", "expires", "lifetime", "ttl", "type", "types", "field", "fields",
    "label", "labels", "required", "rule", "rules", "complexity", "history", "enabled",
    "disabled", "attempts", "timeout", "cost", "rounds", "format", "file", "path", "url",
    "endpoint", "name", "names", "mode", "scheme", "driver", "handler", "class", "provider",
    "validator", "encoder", "hasher", "storage", "encoding", "check", "count", "limit",
    "option", "options", "setting", "settings", "config",
})
# A value that only says a secret goes here: <mot de passe>, ***, xxxx.
_EMPTY_VALUE = re.compile(r"<[^<>\n]+>|\*+|x{3,}", re.IGNORECASE)
_PLAIN_VALUE = re.compile(
    r"(?:[A-Za-z][a-z0-9_.\-]*|\d+|/[\w./\-]*|[A-Za-z][A-Za-z0-9+.\-]*://[^\s@]*)[,;]?"
)
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")
_CODE_FREE_VALUE = re.compile(r"[\w+/=:@%.\-~!#^&*$]+")
_PATH_OR_URL = re.compile(r"(?:/|~/|\.{1,2}/)|\w+://")
_SECRET_SHAPE = re.compile(r"\d|[^\w\s.\-]|.[A-Z]")
# A placeholder in a URL, an option or a header: ${X}, $X, <x>, {x}, [x], %s, ***, xxx.
_PLACEHOLDER_VALUE = re.compile(
    r"\$\{[^{}\n]*\}|\$[A-Za-z_]\w*|<[^<>\n]*>|\{[^{}\n]*\}|\[[^\[\]\n]*\]|%\(?\w*\)?[sd]|\*+|x{3,}",
    re.IGNORECASE,
)

# A secret that is written after a space: "SetEnv DB_PASSWORD value", "fastcgi_param NAME value;",
# "--password value", "redis-cli -a value", "curl -u user:value", "sshpass -p value". The name
# ends with the keyword, so --password-file, --no-password and --password-stdin are not matched.
_COMMAND_CREDENTIAL = re.compile(
    r"""
    (?P<pre>
        (?:
            (?-i:\b(?:SetEnv|setenv|fastcgi_param|uwsgi_param|scgi_param|ENV|ARG))[ \t]+
            ["']?[\w.\-]*(?:password|passwd|pwd|pass|secret|token|api[_-]?key|salt|key)["']?[ \t]+
          | (?<![\w-])--?(?!no-|ask-)[\w-]*?(?:password|passwd|pwd|secret|token|api[_-]?key)
            [ \t]+
          | (?<![\w-])--passphrase[ \t]+
          | \bredis-cli\b(?:(?!\bredis-cli\b)[^\n|;&])*?[ \t](?:-a|--pass)[ \t]+
          | \bsshpass\b(?:(?!\bsshpass\b)[^\n|;&])*?[ \t]-p[ \t]*
          | \b(?:curl|wget)\b(?:(?!\b(?:curl|wget)\b)[^\n|;&])*?[ \t]
            (?:-u[ \t]+|--user(?:[ \t]+|=))["']?[^\s:"']+:
        )
        (?:(?P<q>["']))?
    )
    (?!\[secret\ masqué\])
    (?!-)
    (?P<val>(?(q)(?:(?!(?P=q))[^\n])+|[^\s"'`;|]{3,}))
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A user and a password in a URL, up to the last "@" of the authority.
_URL_CREDENTIALS = re.compile(
    r"""
    (?P<pre>\b[A-Za-z][A-Za-z0-9+.\-]{1,15}://[^\s/@:"'`<>]*:)
    (?P<val>[^\s/"'`<>]+)
    (?=@[^\s@/"'`<>])
    """,
    re.VERBOSE,
)

# The request header of cookies: session ids of the visitor. Set-Cookie stays (its attributes
# are evidence for the audit).
_COOKIE_HEADER = re.compile(
    r"""
    (?P<pre>(?<![\w-])cookie\\?["']?[ \t]*:[ \t]*\\?["']?)
    (?!\[secret\ masqué\])
    (?P<val>[\w.%\-]+=[^\n"'`\\]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_REFERENCE_COOKIE = re.compile(r"[\w.%\-]+=\$\{?\w+\}?")

# A key of an obvious form, with no keyword near it.
_PROVIDER_KEY = re.compile(
    r"\b(?:(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{10,}|whsec_[0-9A-Za-z]{16,}"
    r"|xox[abprs]-[0-9A-Za-z\-]{10,}|AIza[0-9A-Za-z_\-]{35}"
    r"|SG\.[0-9A-Za-z_\-]{16,}\.[0-9A-Za-z_\-]{16,}|npm_[0-9A-Za-z]{36})"
)

# A PHP constant whose name says it holds a credential (DB_PASSWORD, API_TOKEN...) or a
# WordPress salt, defined with a non-empty literal: define( 'DB_PASSWORD', '...' ). A value
# that is an expression (getenv( 'X' ), $password, A_CONSTANT) is not matched.
_DEFINE = re.compile(
    r"""
    (?P<pre>
        (?<![\w$>:])define\s*\(\s*
        (?P<nq>["'])
        (?:\w*(?:password|passwd|pwd|secret|token|api_?key)\w*
           |(?:secure_)?auth_(?:key|salt)|logged_in_(?:key|salt)|nonce_(?:key|salt))
        (?P=nq)\s*,\s*
        (?P<q>["'])
    )
    (?!\[secret\ masqué\])
    (?P<val>(?:(?!(?P=q))[^\n])+)
    (?=(?P=q))
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


def _is_non_secret(match: re.Match) -> bool:
    """True when a credential match is a setting, a form field description or a pure code expression."""
    value = match.group("val")
    keyword = match.group("kw").lower()
    if _EMPTY_VALUE.fullmatch(value):
        return True
    if match.re is _WEAK_CREDENTIAL:
        if not _looks_like_secret(value):
            return True
    elif _is_setting(match.group("suffix"), value):
        return True
    elif keyword.startswith(_PASSWORD_WORDS):
        if not re.search(r"\w", value):
            return True
        if keyword == "pass" and not _is_pass_secret(match.group("kw"), value):
            return True
    elif len(value) < _MIN_VALUE_LENGTH:
        return True
    if match.group("br") and not match.group("q") and not _is_word_secret(value):
        return True
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
    if not is_code or ":-" in rest:
        return False
    literals = list(_QUOTED_LITERAL.finditer(rest)) + list(_N_LITERAL.finditer(rest))
    return all(_is_harmless_literal(rest, literal) for literal in literals)


def _is_setting(suffix: str, value: str) -> bool:
    """True for password_policy: strong, token_lifetime: 3600, password_file: /run/x."""
    words = _WORD.findall(suffix)
    return bool(words) and all(word.lower() in _DESCRIPTOR_WORDS for word in words) and bool(
        _PLAIN_VALUE.fullmatch(value)
    )


def _looks_like_secret(value: str) -> bool:
    """True for a value that has the shape of a secret: 8 characters or more, not a path or a
    URL, without spaces or brackets, with a digit, a symbol or an upper-case letter inside (not
    only a word)."""
    return len(value) >= 8 and not _PATH_OR_URL.match(value) and _is_word_secret(value)


def _is_word_secret(value: str) -> bool:
    return bool(_CODE_FREE_VALUE.fullmatch(value) and _SECRET_SHAPE.search(value))


def _is_pass_secret(name: str, value: str) -> bool:
    """A value after a name ending in pass: DB_PASS takes any word, smtp_pass a secret shape."""
    if value.lower() in _FLAG_VALUES or not _CODE_FREE_VALUE.fullmatch(value):
        return False
    return name.isupper() or bool(_SECRET_SHAPE.search(value))


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
    ("credential_assignment", _WEAK_CREDENTIAL),
    ("credential_assignment", _COMMAND_CREDENTIAL),
    ("provider_key", _PROVIDER_KEY),
    ("url_credentials", _URL_CREDENTIALS),
    ("credential_define", _DEFINE),
    ("authorization_header", _AUTH_HEADER),
    ("cookie_header", _COOKIE_HEADER),
    ("mysql_password_option", _MYSQL_PASSWORD),
    ("kube_client_key", _KUBE_CLIENT_KEY),
)


def scan_text(text: str, known_values: list[str]) -> list[str]:
    """Return the labels found in text, each once, in a fixed order."""
    labels = []
    if any(value in text for value in known_values if value):
        labels.append("known_value")
    for label, pattern in _PATTERNS:
        if label not in labels and _search(pattern, text):
            labels.append(label)
    return labels


def scan_lines(text: str, known_values: list[str]) -> list[tuple[str, int]]:
    """Return the (label, line) pairs found in text, line counted from 1 where a match starts.

    The labels are those of scan_text; each pair comes once, by line, then in the order of
    scan_text. A value is never returned.
    """
    found = []
    for label, first, _ in scan_spans(text, known_values):
        if (label, first) not in found:
            found.append((label, first))
    return found


def scan_spans(text: str, known_values: list[str]) -> list[tuple[str, int, int]]:
    """Return the (label, first line, last line) of every match in text, lines counted from 1
    on "\\n" only (a "\\r" is part of its line, as git counts it).

    A define( or a private key written over several lines spans them; a private key without
    its END line spans its first line only (the match runs to the end of the text). Each
    triple comes once, by first line, then in the order of scan_text, then by last line. A
    value is never returned.
    """
    starts = [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]
    found = set()
    for value in {value for value in known_values if value}:
        found.update(("known_value", *_span_of(starts, match))
                     for match in re.finditer(re.escape(value), text))
    for label, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if _skipped(pattern, match):
                continue
            first, last = _span_of(starts, match)
            if pattern is _PRIVATE_KEY and match.group("end") is None:
                last = first
            found.add((label, first, last))
    order = ["known_value"] + [label for label, _ in _PATTERNS]
    return sorted(found, key=lambda item: (item[1], order.index(item[0]), item[2]))


def _span_of(starts: list, match: re.Match) -> tuple[int, int]:
    first = _line_of(starts, match.start())
    return first, max(first, _line_of(starts, match.end() - 1))


def _line_of(starts: list, offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def _search(pattern: re.Pattern, text: str) -> bool:
    return any(not _skipped(pattern, m) for m in pattern.finditer(text))


def _skipped(pattern: re.Pattern, match: re.Match) -> bool:
    if pattern in _ASSIGNMENTS:
        return _is_non_secret(match)
    if pattern is _COMMAND_CREDENTIAL:
        return _is_placeholder(match.group("val"))
    if pattern is _URL_CREDENTIALS:
        return bool(_PLACEHOLDER_VALUE.fullmatch(match.group("val")))
    if pattern is _COOKIE_HEADER:
        return bool(_REFERENCE_COOKIE.fullmatch(match.group("val")))
    return False


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_VALUE.fullmatch(value) or _VARIABLE_REFERENCE.fullmatch(value)
                or _is_reference(value))


def redact(text: str, known_values: list[str]) -> str:
    """Replace every secret found by MASK, keeping what precedes the value."""
    values = sorted({value for value in known_values if value}, key=len, reverse=True)
    if values:
        text = re.sub("|".join(re.escape(value) for value in values), lambda _: MASK, text)
    for _, pattern in _PATTERNS:
        if pattern in _ASSIGNMENTS:
            text = _redact_credentials(text, pattern)
        else:
            text = pattern.sub(lambda match, pattern=pattern: _mask(match, pattern), text)
    return text


def known_values_from_env(env: Mapping[str, str] = os.environ) -> list[str]:
    """Return the values of the secret environment variables that are long enough to match.

    The Basic Auth password comes with the forms it takes on the wire: the Basic credentials in
    base64 (with the user) and the password percent-encoded in a URL or a form.
    """
    values = [value for value in (env.get(name, "").strip() for name in _ENV_NAMES)
              if len(value) >= _MIN_ENV_LENGTH]
    password = env.get(_AUTH_PASS, "").strip()
    user = env.get(_AUTH_USER, "").strip()
    forms = [quote(password, safe=""), quote_plus(password)] if password else []
    if password and user:
        forms.append(base64.b64encode(f"{user}:{password}".encode()).decode("ascii"))
    for form in forms:
        if len(form) >= _MIN_ENV_LENGTH and form not in values:
            values.append(form)
    return values


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


def _redact_credentials(text: str, pattern: re.Pattern) -> str:
    """Mask credential values; an unquoted call or index is masked to the end of line."""
    out = []
    position = 0
    for match in pattern.finditer(text):
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


def _mask(match: re.Match, pattern: re.Pattern) -> str:
    if _skipped(pattern, match):
        return match.group(0)
    return (match.groupdict().get("pre") or "") + MASK


def _raise(error: OSError) -> None:
    raise error
