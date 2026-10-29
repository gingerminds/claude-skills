"""Checks of what the fix sub-agents hand back: the commits of a fix branch, the fixer's
reports and the reviewer's verdicts.

The guard judges only the commits of <start_sha>..HEAD: what a reused branch carried before
the pass (a reviewer's suggestion, a merge of develop) is never judged again; before a commit,
it judges the index and the planned message the same way. It names the labels of the secrets
it finds, never their values, and reads credentials as literal values only. Each problem has a
kind: "form" (the message: convention, section, length, attribution, ref) is fixed by
rewriting the message; "content" (paths, secrets, merge, rewritten history) drops the row.
"""
import fnmatch
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from cqa import fix_git, fix_names
from cqa.checklist import ref_label
from cqa.secrets import scan_text

THIRD_PARTY = "code tiers (third_party_paths)"
DEPENDENCY_DIR = "dossier de dépendances"
CORE = "cœur ou module contribué de la stack"
SUBMODULE = "sous-module git"
MINIFIED = "fichier minifié"
DEPENDENCIES = "dépendances : aucun ajout ni montée en v2.0"
SECRET = "secret détecté ({label})"
BAD_MESSAGE = "message de commit hors convention"
OUT_OF_KEY = "Réf hors de cette MR"
SECTION_MISMATCH = "section du commit différente de celle de la ligne"
SUBJECT_TOO_LONG = f"sujet de plus de {fix_names.SUBJECT_MAX} caractères"
MERGE = "commit de fusion"
ATTRIBUTION = "ligne d'attribution dans le message"
REWRITTEN = "historique réécrit : le départ de la passe n'est plus dans la branche"
OUT_OF_PASS = "commit hors de la passe {number} : {sha}"
UNREVIEWED_PASS = "la revue de la passe {number} n'est pas enregistrée"
OTHER_SECTION = "Réf d'une autre section ({alias})"
FORM = "form"
CONTENT = "content"

# Core and contributed code of each stack (repos[].stack of context.json). Patterns are
# matched segment by segment, case aside: "**" stands for any number of folders, and a
# pattern also covers everything below it. A stack missing here gets the generic rules only.
CORE_PATHS = {
    "wordpress": (
        "**/wp-admin", "**/wp-includes", "web/wp",
        "**/wp-activate.php", "**/wp-blog-header.php", "**/wp-comments-post.php",
        "**/wp-config-sample.php", "**/wp-cron.php", "**/wp-links-opml.php", "**/wp-load.php",
        "**/wp-login.php", "**/wp-mail.php", "**/wp-settings.php", "**/wp-signup.php",
        "**/wp-trackback.php", "**/xmlrpc.php",
    ),
    "drupal": (
        "core", "web/core", "docroot/core", "html/core",
        "**/contrib",
        "libraries", "web/libraries", "docroot/libraries", "html/libraries",
    ),
}
DEPENDENCY_DIRS = ("**/vendor", "**/node_modules")
DEPENDENCY_FILES = (
    "**/composer.json", "**/composer.lock", "**/package.json", "**/package-lock.json",
    "**/npm-shrinkwrap.json", "**/yarn.lock", "**/pnpm-lock.yaml",
)
SUBMODULE_FILES = ("**/.gitmodules",)
MINIFIED_FILES = ("**/*.min.*",)

REPORT_OUTCOMES = ("fixed", "skipped", "question")
VERDICTS = ("ok", "blocking")
MAX_TEXT = 600
MAX_REASON = 300
MAX_NOTE = 400
MAX_OPTION = 200
MAX_REMAINING = 300
MAX_ABANDON = 300
MIN_OPTIONS = 2
MAX_OPTIONS = 4
# The text corrections a review may give with an ok verdict, and their limits.
CORRECTION_LIMITS = {"change": MAX_TEXT, "uat": MAX_TEXT, "attention": MAX_NOTE,
                     "remaining": MAX_REMAINING}
# A pass whose diff is documentation only, this many added plus removed lines at most, is
# reviewed by the orchestrator itself.
LIGHT_REVIEW_LINES = 40

_GITLINK = "160000"
# Rows a report may no longer change: their state outcome and the error that names them.
_SETTLED = {
    "done_before": "déjà faite sur la branche avant cette passe (done_before), hors du rapport",
    "dropped": "retirée de la branche (dropped), hors du rapport",
}
# Trailers and footers that credit a tool: Co-Authored-By, Claude-Session, "Generated with".
_ATTRIBUTION = re.compile(r"\W*(?:co-authored-by\s*:|claude-session\s*:|generated with\b)",
                          re.IGNORECASE)
_SHORT_SHA = re.compile(r"[0-9a-f]{7,64}")
_ABANDON = re.compile(r"\s*abandon\s*:", re.IGNORECASE)
# An option that leaves the row as it is: the orchestrator already offers to leave it to a dev.
_AS_IS = ("ne rien", "rien faire", "laisser", "à un dev", "aux devs", "pas de changement",
          "ne pas corriger")
_DIFF = ("-U0", "--text", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
         "--src-prefix=a/", "--dst-prefix=b/")
_C_ESCAPE = re.compile(r'\\([0-7]{3}|[abtnvfr"\\])')
_C_ESCAPED = {"a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
              '"': '"', "\\": "\\"}


def path_reason(path: str, repo: dict, *, gitlink: bool = False) -> Optional[str]:
    """Return why a commit may not touch path in this repo, or None when it may."""
    parts = path.casefold().split("/")
    third_party = [item for item in repo.get("third_party_paths") or [] if isinstance(item, str)]
    if any(_under(parts, item.casefold().split("/")) for item in third_party if item):
        return THIRD_PARTY
    if _matches_any(DEPENDENCY_DIRS, parts):
        return DEPENDENCY_DIR
    if _matches_any(CORE_PATHS.get(repo.get("stack"), ()), parts):
        return CORE
    if gitlink or _matches_any(SUBMODULE_FILES, parts):
        return SUBMODULE
    if _matches_any(MINIFIED_FILES, parts):
        return MINIFIED
    if _matches_any(DEPENDENCY_FILES, parts):
        return DEPENDENCIES
    return None


def check_commits(worktree: Path, start_sha: str, rows: dict, repo: dict, known: list,
                  run) -> list:
    """Return the problems of the commits of <start_sha>..HEAD in worktree, oldest first.

    rows maps every ref of the key (done_before included) to the alias of its section (None
    when unknown); repo is the context.json entry (stack, third_party_paths); known holds the
    secret values of the session. Each problem is {"sha", "ref", "path", "reason", "kind"}:
    ref is the ref of the commit subject (the stray one for "Réf hors de cette MR"), path is
    None for a problem of the whole commit, kind is FORM or CONTENT. Only the added lines of
    the diffs, and the messages, are scanned for secrets.
    """
    worktree = Path(worktree)
    problems = []
    if not fix_git.is_ancestor(worktree, run, start_sha, "HEAD"):
        problems.append(_problem(start_sha, None, None, REWRITTEN, CONTENT))
    for commit in fix_git.commits_in_range(worktree, run, start_sha):
        sha, ref = commit["sha"], commit["ref"]
        if len(commit["parents"]) > 1:
            problems.append(_problem(sha, ref, None, MERGE, CONTENT))
            continue
        problems += _change_problems(
            sha, ref, commit["message"], rows, repo, known,
            _changed_paths(worktree, run, f"git diff-tree {sha[:12]}", "diff-tree", "-r", "-z",
                           "--raw", "--root", "--no-renames", "--no-commit-id", sha),
            _added_lines(worktree, run, f"git show {sha[:12]}", "-c", "core.quotePath=true",
                         "show", "--format=", "--no-show-signature", *_DIFF, sha),
        )
    return problems


def check_staged(worktree: Path, message: str, rows: dict, repo: dict, known: list,
                 run) -> list:
    """Return the problems of the next commit, before it is made: the index of worktree
    against HEAD, with message as its message. rows, repo and known as for check_commits;
    each problem has sha None and the ref of the planned subject. Nothing is written.
    """
    worktree = Path(worktree)
    ref = fix_names.ref_of_subject(message.split("\n", 1)[0])
    return _change_problems(
        None, ref, message, rows, repo, known,
        _changed_paths(worktree, run, "git diff --cached", "diff", "--cached", "-z", "--raw",
                       "--no-renames"),
        _added_lines(worktree, run, "git diff --cached", "-c", "core.quotePath=true", "diff",
                     "--cached", *_DIFF),
    )


def review_scope(worktree: Path, run, base: str) -> dict:
    """Return {"light", "lines", "files"} for the diff base..HEAD that the review of a pass reads.

    files lists its paths, sorted; lines counts its added plus removed lines. light is True
    when every file is documentation (fix_names.is_documentation), none binary, and lines is
    LIGHT_REVIEW_LINES at most: the orchestrator then writes the review itself.
    """
    result = fix_names.git(
        Path(worktree), run, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff",
        "--no-textconv", base, "HEAD",
    )
    if result.returncode != 0:
        raise fix_git.GitRefused(f"git diff {base[:12]}..HEAD a échoué : {result.stderr.strip()}")
    files, lines, binary = [], 0, False
    for record in result.stdout.split("\0"):
        fields = record.split("\t", 2)
        if len(fields) != 3:
            continue
        added, removed, path = fields
        files.append(path)
        if added.isdigit() and removed.isdigit():
            lines += int(added) + int(removed)
        else:
            binary = True
    files.sort()
    light = (not binary and lines <= LIGHT_REVIEW_LINES
             and all(fix_names.is_documentation(path) for path in files))
    return {"light": light, "lines": lines, "files": files}


def validate_report(document, *, key: str, number: int, rows: dict, commits: list,
                    pass_commits: Optional[set] = None, reviewed: Optional[set] = None,
                    sections: Optional[dict] = None) -> tuple:
    """Return (rows for the state, errors in French) for fix/<repo>/report-<number>.json.

    rows are the state rows of the pass's section ({"3": {...}}); sections, when given, maps
    every ref of the key ({"9": "qualite"}) so that a row of another section is named as
    such. commits are those of <start_sha>..HEAD
    (fix_git.commits_in_range). pass_commits, when given, holds the SHAs of
    bases[number]..HEAD: a commit a fixed row reports for the first time (not yet among its
    state commits) must be one of them, so that the review of the pass reads it. reviewed,
    when given, holds the passes whose review is recorded: a row already fixed in an earlier
    pass may be reported again only once that pass is reviewed, since a row fixed again
    belongs to the new pass, whose review does not read its earlier commits. Every problem
    of a row is reported in one pass, because the fixer rewrites its file once. The rows come
    back only when there is no error, each as {"ref", "outcome", "reason", "commits" (full
    SHAs), "attention", "remaining"}; a question keeps its text in reason, and remaining (what
    is left to do after a partial fix) is None but for a fixed row.
    """
    if not isinstance(document, dict):
        return [], ['le rapport doit être un objet JSON {"key", "pass", "rows"}']
    errors = _header_errors(document, key, number)
    items = document.get("rows")
    if not isinstance(items, list):
        return [], errors + ["« rows » doit être une liste"]
    known = {int(ref) for ref in rows}
    subjects = {commit["sha"]: commit["ref"] for commit in commits}
    entries, seen = [], Counter()
    for index, item in enumerate(items, start=1):
        ref = _row_ref(item, index, known, errors, sections)
        if ref is None:
            continue
        seen[ref] += 1
        current = rows[str(ref)]
        settled = _SETTLED.get(current.get("outcome"))
        if settled:
            errors.append(f"ligne {ref_label(ref)} : {settled}")
            continue
        problems, entry = _report_row(item, ref, known, subjects)
        if entry is not None and pass_commits is not None:
            recorded = set(current.get("commits") or [])
            problems += [
                OUT_OF_PASS.format(number=number, sha=sha[:12]) for sha in entry["commits"]
                if sha not in recorded and sha not in pass_commits
            ]
        earlier = current.get("pass")
        if (reviewed is not None and current.get("outcome") == "fixed" and _is_int(earlier)
                and earlier < number and earlier not in reviewed):
            problems.append(UNREVIEWED_PASS.format(number=earlier))
        errors.extend(f"ligne {ref_label(ref)} : {problem}" for problem in problems)
        if not problems:
            entries.append(entry)
    errors.extend(_duplicates(seen))
    pending = sorted(int(ref) for ref, row in rows.items() if row.get("outcome") == "pending")
    errors.extend(
        f"ligne absente du rapport : {ref_label(ref)}" for ref in pending if ref not in seen
    )
    return ([] if errors else entries), errors


def validate_review(document, *, key: str, number: int, rows: dict) -> tuple:
    """Return (blocking refs, errors in French) for fix/<repo>/review-<number>.json.

    Every row the fixer reported fixed in this pass needs a verdict, and only those rows may
    get one (a row fixed in another pass belongs to that pass's review); a blocking verdict
    needs notes, which tell the fixer what to change. An ok verdict may carry text corrections
    ({"change", "uat", "attention", "remaining"}, see review_corrections) instead of a
    correction pass for a text alone.
    """
    if not isinstance(document, dict):
        return [], ['la revue doit être un objet JSON {"key", "pass", "rows", "attention"}']
    errors = _header_errors(document, key, number)
    attention = document.get("attention")
    if not isinstance(attention, list):
        errors.append("« attention » doit être une liste de remarques (vide s'il n'y en a pas)")
    else:
        for index, note in enumerate(attention, start=1):
            problems = _text(note, "remarque", MAX_NOTE)
            errors.extend(f"« attention » n° {index} : {problem}" for problem in problems)
    items = document.get("rows")
    if not isinstance(items, list):
        return [], errors + ["« rows » doit être une liste"]
    known = {int(ref) for ref in rows}
    blocking, seen = [], Counter()
    for index, item in enumerate(items, start=1):
        ref = _row_ref(item, index, known, errors)
        if ref is None:
            continue
        seen[ref] += 1
        outcome = rows[str(ref)].get("outcome")
        if outcome != "fixed":
            errors.append(
                f"ligne {ref_label(ref)} : verdict sur une ligne non corrigée ({outcome})"
            )
            continue
        other = rows[str(ref)].get("pass")
        if other != number:
            errors.append(
                f"ligne {ref_label(ref)} : verdict sur une ligne d'une autre passe ({other})"
            )
            continue
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"ligne {ref_label(ref)} : « verdict » inconnu : {_shown(verdict)} "
                          "(attendu : ok ou blocking)")
            continue
        check = _text if verdict == "blocking" else _optional
        problems = check(item.get("notes"), "notes", MAX_TEXT)
        problems += _corrections(item.get("corrections"), verdict)
        errors.extend(f"ligne {ref_label(ref)} : {problem}" for problem in problems)
        if verdict == "blocking":
            blocking.append(ref)
    errors.extend(_duplicates(seen))
    fixed = sorted(
        int(ref) for ref, row in rows.items()
        if row.get("outcome") == "fixed" and row.get("pass") == number
    )
    errors.extend(
        f"ligne corrigée sans verdict : {ref_label(ref)}" for ref in fixed if ref not in seen
    )
    return ([] if errors else sorted(set(blocking))), errors


def review_corrections(document) -> dict:
    """Return {ref: {field: text}}, the text corrections of the ok verdicts of a valid review;
    a field left null is no correction."""
    found = {}
    for item in document.get("rows") or []:
        value = item.get("corrections") if item.get("verdict") == "ok" else None
        if isinstance(value, dict):
            texts = {name: value[name] for name in CORRECTION_LIMITS
                     if isinstance(value.get(name), str)}
            if texts:
                found[item["ref"]] = texts
    return found


def _problem(sha, ref, path, reason: str, kind: str) -> dict:
    return {"sha": sha, "ref": ref, "path": path, "reason": reason, "kind": kind}


def _change_problems(sha, ref, message: str, rows: dict, repo: dict, known: list, paths: list,
                     added: dict) -> list:
    """Return the problems of one change: its message, its paths, the secrets of its lines."""
    problems = [_problem(sha, ref, None, reason, kind)
                for reason, kind in _message_reasons(message, rows, known)]
    for path, gitlink in paths:
        reason = path_reason(path, repo, gitlink=gitlink)
        if reason:
            problems.append(_problem(sha, ref, path, reason, CONTENT))
    for path, lines in added.items():
        for label in scan_text("\n".join(lines), known, literal_credentials=True):
            problems.append(_problem(sha, ref, path, SECRET.format(label=label), CONTENT))
    return problems


def _message_reasons(message: str, rows: dict, known: list) -> list:
    """Return the (reason, kind) pairs of a commit message, convention first."""
    subject = message.split("\n", 1)[0]
    alias, ref = fix_names.parse_subject(subject) or (None, None)
    reasons = []
    if not fix_names.SUBJECT.match(subject):
        reasons.append((BAD_MESSAGE, FORM))
    if ref is not None and ref not in rows:
        reasons.append((OUT_OF_KEY, FORM))
    elif alias is not None and rows.get(ref) is not None and alias != rows[ref]:
        reasons.append((SECTION_MISMATCH, FORM))
    if len(subject) > fix_names.SUBJECT_MAX:
        reasons.append((SUBJECT_TOO_LONG, FORM))
    if any(_ATTRIBUTION.match(line) for line in message.split("\n")[1:]):
        reasons.append((ATTRIBUTION, FORM))
    reasons.extend((SECRET.format(label=label), CONTENT)
                   for label in scan_text(message, known, literal_credentials=True))
    return reasons


def _changed_paths(worktree: Path, run, label: str, *args: str) -> list:
    """Return (path, is a submodule) for every path a raw diff (-z) adds, changes or deletes."""
    result = fix_names.git(worktree, run, *args)
    if result.returncode != 0:
        raise fix_git.GitRefused(f"{label} a échoué : {result.stderr.strip()}")
    fields = result.stdout.split("\0")
    changes, index = [], 0
    while index < len(fields):
        meta = fields[index].split()
        index += 1
        if len(meta) < 5 or not meta[0].startswith(":"):
            continue
        count = 2 if meta[4][:1] in ("R", "C") else 1
        gitlink = _GITLINK in (meta[0][1:], meta[1])
        changes.extend((path, gitlink) for path in fields[index:index + count])
        index += count
    return changes


def _added_lines(worktree: Path, run, label: str, *args: str) -> dict:
    """Return {path: [added lines]} of a patch with no context, binary files included."""
    result = fix_names.git(worktree, run, *args)
    if result.returncode != 0:
        raise fix_git.GitRefused(f"{label} a échoué : {result.stderr.strip()}")
    added, path, in_hunk = {}, None, False
    for line in result.stdout.split("\n"):
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif not in_hunk:
            if line.startswith("+++ "):
                path = _diff_path(line[4:])
            elif line.startswith("@@"):
                in_hunk = True
        elif line.startswith("+"):
            added.setdefault(path, []).append(line[1:])
    return added


def _diff_path(text: str) -> Optional[str]:
    """Return the path of a "+++ b/<path>" header, unquoted; None for /dev/null.

    git ends the header with a tab when the path holds a space.
    """
    if text.endswith("\t"):
        text = text[:-1]
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        quoted = _C_ESCAPE.sub(
            lambda found: chr(int(found.group(1), 8)) if found.group(1)[0].isdigit()
            else _C_ESCAPED[found.group(1)],
            text[1:-1],
        )
        text = quoted.encode("latin-1", "replace").decode("utf-8", "replace")
    if text == "/dev/null":
        return None
    return text[2:] if text.startswith("b/") else text


def _under(parts: list, prefix: list) -> bool:
    return len(parts) >= len(prefix) and parts[:len(prefix)] == prefix


def _matches_any(patterns, parts: list) -> bool:
    return any(_matches(pattern.casefold().split("/"), parts) for pattern in patterns)


def _matches(pattern: list, parts: list) -> bool:
    """True when the pattern matches parts or one of its leading folders."""
    if not pattern:
        return True
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_matches(rest, parts[index:]) for index in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], head) and _matches(rest, parts[1:])


def _header_errors(document: dict, key: str, number: int) -> list:
    errors = []
    if document.get("key") != key:
        errors.append(f"« key » vaut {_shown(document.get('key'))} au lieu de « {key} »")
    if not _is_int(document.get("pass")) or document.get("pass") != number:
        errors.append(f"« pass » vaut {_shown(document.get('pass'))} au lieu de {number}")
    return errors


def _row_ref(item, index: int, known: set, errors: list,
             sections: Optional[dict] = None) -> Optional[int]:
    """Return the ref of a known row, or None after adding the error; sections names the
    section of a row of the key that is not known (another section)."""
    if not isinstance(item, dict):
        errors.append(f"ligne n° {index} : doit être un objet JSON")
        return None
    ref = item.get("ref")
    if not _is_int(ref):
        errors.append(f"ligne n° {index} : « ref » doit être un entier (107 pour CQ-107)")
        return None
    if ref not in known:
        alias = (sections or {}).get(str(ref))
        reason = OTHER_SECTION.format(alias=alias) if alias else "Réf hors de cette clé"
        errors.append(f"ligne {ref_label(ref)} : {reason}")
        return None
    return ref


def _report_row(item: dict, ref: int, known: set, subjects: dict) -> tuple:
    """Return (problems, state entry) for one row of a report.

    The entry is None only when the outcome is unknown: with any other problem it still
    comes back, so that the caller checks its commits too and reports everything at once.
    """
    outcome = item.get("outcome")
    if outcome not in REPORT_OUTCOMES:
        return [
            f"« outcome » inconnu : {_shown(outcome)} (attendu : fixed, skipped ou question)"
        ], None
    attention = item.get("attention")
    problems = _attention(attention, "attention")
    remaining = item.get("remaining")
    if remaining is not None and outcome != "fixed":
        problems.append("« remaining » ne sert qu'à une ligne corrigée (fixed)")
        remaining = None
    elif outcome == "fixed":
        problems += _optional(remaining, "remaining", MAX_REMAINING)
    covered = item.get("covered_by")
    if covered is not None and outcome != "fixed":
        problems.append("« covered_by » ne sert qu'à une ligne corrigée (fixed)")
    elif covered is not None and (not _is_int(covered) or covered not in known or covered == ref):
        problems.append("« covered_by » doit être la Réf d'une autre ligne de la clé")
        covered = None
    shas, reason = [], None
    if outcome == "fixed":
        problems += _text(item.get("change"), "change", MAX_TEXT)
        problems += _text(item.get("uat"), "uat", MAX_TEXT)
        shas, found = _commits(item.get("commits"), {ref, covered} - {None}, subjects)
        problems += found
    elif outcome == "skipped":
        reason = item.get("reason")
        problems += _text(reason, "reason", MAX_REASON)
    else:
        question = item.get("question")
        problems += _question(question)
        reason = question.get("text") if isinstance(question, dict) else None
    if outcome != "fixed" and ref in subjects.values():
        problems.append("commit sur la branche pour une ligne non corrigée")
    entry = {"ref": ref, "outcome": outcome, "reason": reason, "commits": shas,
             "attention": attention, "remaining": remaining}
    return problems, entry


def _commits(value, refs: set, subjects: dict) -> tuple:
    """Return (full SHAs, problems) for the commits of a fixed row; each must carry one of refs."""
    if not isinstance(value, list) or not value:
        return [], ["une ligne corrigée cite au moins un commit de la branche (« commits »)"]
    shas, problems = [], []
    labels = " ou ".join(ref_label(ref) for ref in sorted(refs))
    for item in value:
        short = item.lower() if isinstance(item, str) else None
        if short is None or not _SHORT_SHA.fullmatch(short):
            problems.append(f"« commits » : {_shown(item)} n'est pas un SHA de commit")
            continue
        found = [sha for sha in subjects if sha.startswith(short)]
        if not found:
            problems.append(f"commit absent de la branche : {item}")
        elif len(found) > 1:
            problems.append(f"commit ambigu : {item}")
        elif subjects[found[0]] not in refs:
            problems.append(f"commit {item} : son sujet ne porte pas {labels}")
        elif found[0] not in shas:
            shas.append(found[0])
    return shas, problems


def _question(value) -> list:
    if not isinstance(value, dict):
        return ['« question » doit être un objet {"text", "options", "default"}']
    problems = _text(value.get("text"), "question.text", MAX_TEXT)
    options = value.get("options")
    if (
        not isinstance(options, list)
        or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS
        or any(_text(option, "option", MAX_OPTION) for option in options)
    ):
        problems.append(f"« question.options » : {MIN_OPTIONS} à {MAX_OPTIONS} options, chacune "
                        f"un texte non vide de {MAX_OPTION} caractères au plus")
    else:
        if len(set(options)) != len(options):
            problems.append("« question.options » : une option est en double")
        elif value.get("default") not in options:
            problems.append("« question.default » doit reprendre l'une des options")
        problems += [
            f"« question.options » : « {option} » laisse la ligne en l'état ; l'orchestrateur "
            "propose déjà de la laisser aux devs"
            for option in options if any(words in option.casefold() for words in _AS_IS)
        ]
    return problems


def _attention(value, name: str) -> list:
    """Check an attention text: MAX_NOTE at most, MAX_ABANDON for a row given up."""
    if isinstance(value, str) and _ABANDON.match(value) and len(value) > MAX_ABANDON:
        return [f"« {name} » : un abandon (« Abandon : ») fait {MAX_ABANDON} caractères au plus "
                f"({len(value)})"]
    return _optional(value, name, MAX_NOTE)


def _corrections(value, verdict) -> list:
    """Check the text corrections of a verdict: an ok one only, known fields, their limits."""
    if value is None:
        return []
    if verdict != "ok":
        return ["« corrections » ne sert qu'à un verdict ok"]
    if not isinstance(value, dict):
        return ['« corrections » doit être un objet {"change", "uat", "attention", "remaining"}']
    expected = ", ".join(CORRECTION_LIMITS)
    problems = [f"« corrections » : champ inconnu « {name} » (attendu : {expected})"
                for name in value if name not in CORRECTION_LIMITS]
    for name, limit in CORRECTION_LIMITS.items():
        if name == "attention":
            problems += _attention(value.get(name), f"corrections.{name}")
        else:
            problems += _optional(value.get(name), f"corrections.{name}", limit)
    return problems


def _text(value, name: str, limit: int) -> list:
    if not isinstance(value, str) or not value.strip():
        return [
            f"« {name} » est obligatoire : un texte en français de {limit} caractères au plus"
        ]
    if len(value) > limit:
        return [f"« {name} » dépasse {limit} caractères ({len(value)})"]
    return []


def _optional(value, name: str, limit: int) -> list:
    return [] if value is None else _text(value, name, limit)


def _duplicates(seen: Counter) -> list:
    return [f"Réf en double : {ref_label(ref)} ({count} lignes)"
            for ref, count in sorted(seen.items()) if count > 1]


def _shown(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
