"""Checks of what the fix sub-agents hand back: the commits of a fix branch, the fixer's
reports and the reviewer's verdicts.

The guard judges only the commits of <start_sha>..HEAD: what a reused branch carried before
the pass (a reviewer's suggestion, a merge of develop) is never judged again; before a commit,
it judges the index and the planned message the same way. It names the labels of the secrets
it finds and their line, never their values, with the same strict rules as the audit: every
changed file is scanned whole, as committed (bytes, no newline translation), and a secret is
reported when one of its lines is added. gitleaks of the toolbox (fix_gitleaks), when the
caller gives it, reads the same content once per call; its findings ("gitleaks:<RuleID>")
are content, never waivable. Each problem has a kind: "form" (the message:
convention, section, length, attribution, ref) is fixed by rewriting the message; "content"
(paths, secrets, merge, rewritten history) drops the row; "suspect" is a heuristic secret
alert (HEURISTIC_SECRET_LABELS) that the lead may waive for one line of a file (waivable, see
cqa_fix.py waive-secret) or treat as content. A waiver is keyed on the content of the file
(its blob): it holds while the file is unchanged, whatever its commit.
"""
import bisect
import fnmatch
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from cqa import fix_git, fix_names, fix_state
from cqa.checklist import ref_label
from cqa.secrets import scan_lines, scan_spans

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
SUSPECT = "suspect"
# Secret labels found by a pattern on code, which a legitimate line can trigger (a password
# read from a request, a constant read from the environment): the lead may waive them. A
# known value, a private key or a provider token is never waived. The state checks the
# waivers it loads against the same set.
HEURISTIC_SECRET_LABELS = fix_state.WAIVABLE_LABELS

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
# An option that leaves the row as it is, in whole words: the orchestrator already offers to
# leave it to a dev. « Laisser un alt vide », « Ne rien afficher » or « Envoyer le formulaire
# au développeur » are real fixes.
_AS_IS = re.compile(
    r"\b(?:rien (?:faire|changer|modifier|toucher)"
    r"|pas de (?:changement|modification)s?"
    r"|ne pas (?:corriger|modifier|changer|toucher)"
    r"|(?:laisser|garder)(?: (?:la ligne|le code|le fichier|cela|ça|tout))?"
    r" (?:en l['\u2019]état|tel(?:le)?s? quel(?:le)?s?)"
    r"|(?:à une?|aux) (?:devs?|développeur(?:se)?s?))\b",
    re.IGNORECASE,
)
# No context at all, whatever diff.interHunkContext says: the hunk headers count added lines.
_DIFF = ("-U0", "--inter-hunk-context=0", "--text", "--no-color", "--no-ext-diff",
         "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")
# A hunk header of a patch without context: the added lines of the new file are +start,count.
_HUNK = re.compile(rb"@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@")
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
                  run, gitleaks=None) -> list:
    """Return the problems of the commits of <start_sha>..HEAD in worktree, oldest first.

    rows maps every ref of the key (done_before included) to the alias of its section (None
    when unknown); repo is the context.json entry (stack, third_party_paths); known holds the
    secret values of the session. Each problem is {"sha", "ref", "path", "line", "label",
    "reason", "kind", "waivable"}: ref is the ref of the commit subject (the stray one for
    "Réf hors de cette MR"), path is None for a problem of the whole commit; a secret has its
    label and its line (in the file, or in the message when path is None), other problems
    None; kind is FORM, CONTENT or SUSPECT, and only a suspect of a file is waivable. Every
    file a commit changes is scanned whole, as committed; a secret is reported, at the line
    where it starts, when one of its lines is added by the commit. The messages are scanned
    whole. gitleaks, when given, is called once with all that content joined
    (fix_gitleaks.Gitleaks) and its findings come last, as content problems.
    """
    worktree = Path(worktree)
    problems, segments = [], []
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
            _added_ranges(worktree, run, f"git show {sha[:12]}", "-c", "core.quotePath=true",
                          "show", "--format=", "--no-show-signature", *_DIFF, sha),
            lambda path, sha=sha: _blob_text(worktree, run, f"{sha}:{path}"),
            segments,
        )
    return problems + _gitleaks_problems(gitleaks, segments)


def check_staged(worktree: Path, message: str, rows: dict, repo: dict, known: list,
                 run, gitleaks=None) -> list:
    """Return the problems of the next commit, before it is made: the index of worktree
    against HEAD, with message as its message. rows, repo, known and gitleaks as for
    check_commits; each problem has the ref of the planned subject, and as sha the blob of
    its path in the index (None for the message or a deleted path), whose content is what
    the scan read. Nothing is written.
    """
    worktree = Path(worktree)
    ref = fix_names.ref_of_subject(message.split("\n", 1)[0])
    blobs = _staged_blobs(worktree, run)

    def read(path):
        blob = blobs.get(path)
        if blob is None:
            raise fix_git.GitRefused(f"l'index n'a pas de blob pour {path} (fusion en cours ?) : "
                                     "rien n'a été contrôlé.")
        return _blob_text(worktree, run, blob)

    segments = []
    problems = _change_problems(
        None, ref, message, rows, repo, known,
        _changed_paths(worktree, run, "git diff --cached", "diff", "--cached", "-z", "--raw",
                       "--no-renames"),
        _added_ranges(worktree, run, "git diff --cached", "-c", "core.quotePath=true", "diff",
                      "--cached", *_DIFF),
        read, segments,
    )
    problems += _gitleaks_problems(gitleaks, segments)
    for item in problems:
        if item["path"] is not None:
            item["sha"] = blobs.get(item["path"])
    return problems


def unwaived(problems: list, waivers: list, blob_of) -> list:
    """Return the problems that no waiver covers.

    A waiver {"blob", "path", "line", "label"} covers a waivable problem with the same path,
    line and label whose file has that content: blob_of(sha, path) gives the blob of the
    problem's path in its commit (the index blob itself for a staged problem), None when
    unknown, and a problem without a blob is never covered. The waiver thus holds whatever the
    commit (a drop replays it) and ends with any change of the file.
    """
    kept = []
    for item in problems:
        if item["waivable"]:
            blob = blob_of(item["sha"], item["path"])
            if blob is not None and any(
                (waiver.get("blob"), waiver.get("path"), waiver.get("line"), waiver.get("label"))
                == (blob, item["path"], item["line"], item["label"]) for waiver in waivers
            ):
                continue
        kept.append(item)
    return kept


def review_scope(worktree: Path, run, base: str) -> dict:
    """Return {"light", "lines", "files"} for the diff base..HEAD that the review of a pass reads.

    files lists its paths, sorted; lines counts its added plus removed lines. light is True
    when there is at least one file, every file is documentation (fix_names.is_documentation),
    none binary, and lines is LIGHT_REVIEW_LINES at most: the orchestrator then writes the
    review itself.
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
    light = (bool(files) and not binary and lines <= LIGHT_REVIEW_LINES
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
    return {"sha": sha, "ref": ref, "path": path, "line": None, "label": None,
            "reason": reason, "kind": kind, "waivable": False}


def _secret(sha, ref, path, label: str, line: int) -> dict:
    """A secret problem: a heuristic label is a suspect, waivable when it is in a file."""
    heuristic = label in HEURISTIC_SECRET_LABELS
    found = _problem(sha, ref, path, SECRET.format(label=label), SUSPECT if heuristic else CONTENT)
    found.update(line=line, label=label, waivable=heuristic and path is not None)
    return found


def _change_problems(sha, ref, message: str, rows: dict, repo: dict, known: list, paths: list,
                     added: dict, read, segments: list) -> list:
    """Return the problems of one change: its message, its paths, the secrets of its files.

    added maps every changed path to the lines it adds in the new file; read(path) gives the
    whole new content of a path (a gitlink has none and is a path problem already). A secret
    whose span meets an added line is reported at the line where it starts; on a line that
    holds a lone "\\r" it is never waivable, since an editor would show the lead another line
    than the one git and the scan read. Every text scanned (the message, then each file) is
    appended to segments as (sha, ref, path, added lines, text) for gitleaks.
    """
    problems = [_problem(sha, ref, None, reason, kind)
                for reason, kind in _message_reasons(message, rows)]
    problems += [_secret(sha, ref, None, label, line) for label, line in scan_lines(message, known)]
    segments.append((sha, ref, None, None, message))
    gitlinks = set()
    for path, gitlink in paths:
        reason = path_reason(path, repo, gitlink=gitlink)
        if reason:
            problems.append(_problem(sha, ref, path, reason, CONTENT))
        if gitlink:
            gitlinks.add(path)
    for path, lines in added.items():
        if not lines or path in gitlinks:
            continue
        text = read(path)
        segments.append((sha, ref, path, lines, text))
        rows_of = text.split("\n")
        for label, first, last in scan_spans(text, known):
            if lines.isdisjoint(range(first, last + 1)):
                continue
            found = _secret(sha, ref, path, label, first)
            if found["waivable"] and _lone_cr(rows_of, first, last):
                found["waivable"] = False
            problems.append(found)
    return problems


def _lone_cr(lines: list, first: int, last: int) -> bool:
    """True when a line of first..last (1-based, split on "\\n") holds a "\\r" that no "\\n"
    follows: git and the scan see one line where an editor shows two."""
    for number in range(first, min(last, len(lines)) + 1):
        line = lines[number - 1]
        if "\r" in line[:-1] or (line.endswith("\r") and number == len(lines)):
            return True
    return False


def _gitleaks_problems(gitleaks, segments: list) -> list:
    """Run gitleaks once on the segments joined and map its findings back to their segment.

    gitleaks(text) returns [(rule, first, last)] on the joined text, None when it did not
    run (the caller reports that). A finding of a file is kept when its span meets an added
    line of that file, one of a message always; each becomes a content problem
    "gitleaks:<rule>" at the line where it starts, in segment order, then by line and rule.
    """
    if gitleaks is None or not segments:
        return []
    texts = [text if text.endswith("\n") else text + "\n" for *_, text in segments]
    findings = gitleaks("".join(texts))
    if not findings:
        return []
    starts, total = [], 1
    for text in texts:
        starts.append(total)
        total += text.count("\n")
    found = set()
    for rule, first, last in findings:
        if not 1 <= first < total:
            # fix_gitleaks.Gitleaks refuses such a report; never skip one quietly here.
            raise ValueError(f"résultat gitleaks hors de l'entrée (ligne {first} sur {total - 1})")
        index = bisect.bisect_right(starts, first) - 1
        _, _, path, lines, _ = segments[index]
        end = (starts[index + 1] if index + 1 < len(starts) else total) - 1
        local_first = first - starts[index] + 1
        local_last = min(max(last, first), end) - starts[index] + 1
        if path is None or not lines.isdisjoint(range(local_first, local_last + 1)):
            found.add((index, local_first, rule))
    return [_secret(segments[index][0], segments[index][1], segments[index][2],
                    f"gitleaks:{rule}", line) for index, line, rule in sorted(found)]


def _message_reasons(message: str, rows: dict) -> list:
    """Return the (reason, kind) pairs of the form of a commit message, convention first."""
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
    return reasons


def _staged_blobs(worktree: Path, run) -> dict:
    """Return {path: blob SHA} of the index of worktree (stage 0)."""
    result = fix_names.git(worktree, run, "ls-files", "--stage", "-z")
    if result.returncode != 0:
        raise fix_git.GitRefused(f"git ls-files a échoué : {result.stderr.strip()}")
    blobs = {}
    for record in result.stdout.split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if path and len(fields) == 3 and fields[2] == "0":
            blobs[path] = fields[1]
    return blobs


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


def _added_ranges(worktree: Path, run, label: str, *args: str) -> dict:
    """Return {path: set of the lines added in the new file} of a patch with no context,
    binary files included, read as bytes so that a "\\r" never splits a line (the lines come
    from the hunk headers, as git counts them)."""
    result = fix_names.git(worktree, run, *args, binary=True)
    if result.returncode != 0:
        raise fix_git.GitRefused(f"{label} a échoué : {_stderr(result)}")
    added, path, in_hunk = {}, None, False
    for line in result.stdout.split(b"\n"):
        if line.startswith(b"diff --git "):
            path, in_hunk = None, False
        elif line.startswith(b"@@") and (in_hunk or path is not None):
            hunk = _HUNK.match(line)
            in_hunk = hunk is not None
            if hunk and path is not None:
                start = int(hunk.group(1))
                count = int(hunk.group(2)) if hunk.group(2) is not None else 1
                added.setdefault(path, set()).update(range(start, start + count))
        elif not in_hunk and line.startswith(b"+++ "):
            path = _diff_path(line[4:].decode("utf-8", "replace"))
    return added


def _blob_text(worktree: Path, run, spec: str) -> str:
    """Return the content of a blob (<sha>:<path>, or :<path> for the index) as the audit
    reads a file: bytes decoded as UTF-8, no newline translation."""
    result = fix_names.git(worktree, run, "cat-file", "blob", spec, binary=True)
    if result.returncode != 0:
        raise fix_git.GitRefused(f"git cat-file {spec} a échoué : {_stderr(result)}")
    return result.stdout.decode("utf-8", "replace")


def _stderr(result) -> str:
    text = result.stderr
    return (text.decode("utf-8", "replace") if isinstance(text, bytes) else text).strip()


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
            for option in options if _AS_IS.search(option)
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
