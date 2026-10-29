"""Checks of the fixed files without the project environment: syntax and stylelint.

The checks run in the Docker toolbox (cqa-toolbox.sh check) on the worktree, never through the
project's own containers. A failed file maps back to the checklist rows whose commits touch it.
"""
import json
import re
import shutil
from pathlib import Path
from typing import Optional

from cqa import fix_guard, fix_names

NOT_RUN_WHY = "à lancer dans l'environnement du projet (shared/runner.md)"
TOOLBOX_DOWN = "boîte à outils indisponible"
UNSUPPORTED = "Nom de fichier"
STYLELINT = "stylelint (configuration du projet)"
CHECKLIST_RULES = ("selector-class-pattern", "max-nesting-depth")
STYLE_EXTENSIONS = (".css", ".scss", ".sass", ".less", ".vue")
MAX_DETAIL = 10
# check name -> (label, extensions); the tsv check column and the host fallback use the key
SYNTAX = {
    "php -l": ("Syntaxe PHP (php -l)", (".php",)),
    "node --check": ("Syntaxe JavaScript (node --check)", (".js", ".mjs", ".cjs")),
    "json": ("JSON", (".json",)),
    "yaml": ("YAML", (".yml", ".yaml")),
}
_MAKE_TARGETS = ("lint", "test", "phpcs")
_COMPOSER_SCRIPTS = ("test", "phpcs")
_NPM_SCRIPTS = ("lint", "stylelint")
_HUNK = re.compile(r"^@@ -[0-9,]+ \+([0-9]+)(?:,([0-9]+))? @@")


def project_commands(worktree: Path) -> list:
    """Return [{"name", "command"}]: the checks the project defines, to run in its environment."""
    worktree = Path(worktree)
    found = []
    makefile = _read(worktree / "Makefile")
    for target in _MAKE_TARGETS:
        if re.search(rf"^{target}[ \t]*:(?!=|:=)", makefile, re.MULTILINE):
            found.append({"name": f"Makefile ({target})", "command": f"make {target}"})
    scripts = _scripts(worktree / "composer.json")
    for name in _COMPOSER_SCRIPTS:
        if name in scripts:
            found.append({"name": f"composer.json ({name})", "command": f"composer {name}"})
    scripts = _scripts(worktree / "package.json")
    for name in _NPM_SCRIPTS:
        if name in scripts:
            found.append({"name": f"package.json ({name})", "command": f"npm run {name}"})
    if (worktree / ".husky" / "pre-commit").is_file():
        found.append({"name": "Hooks husky (pre-commit)", "command": "sh .husky/pre-commit"})
    if (worktree / "grumphp.yml").is_file() or (worktree / "grumphp.yml.dist").is_file():
        found.append({"name": "GrumPHP", "command": "vendor/bin/grumphp run"})
    if (worktree / "captainhook.json").is_file():
        found.append({"name": "CaptainHook (pre-commit)", "command": "vendor/bin/captainhook hook:pre-commit"})
    return found


def changed_files(worktree: Path, start_sha: str, run) -> list:
    """Return the files the key's commits add or modify, sorted."""
    result = fix_names.git(
        worktree, run, "diff", "--name-only", "--diff-filter=AM", "--no-renames", "-z",
        f"{start_sha}..HEAD",
    )
    if result.returncode != 0:
        raise ValueError(f"git diff a échoué : {result.stderr.strip()}")
    return sorted(path for path in result.stdout.split("\0") if path)


def added_lines(worktree: Path, start_sha: str, run) -> dict:
    """Return {path: {line numbers added by <start_sha>..HEAD}}."""
    result = fix_names.git(
        worktree, run, "-c", "core.quotePath=true", "diff", "-U0", "--no-color", "--no-ext-diff",
        "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/",
        f"{start_sha}..HEAD",
    )
    if result.returncode != 0:
        raise ValueError(f"git diff a échoué : {result.stderr.strip()}")
    added, path = {}, None
    for line in result.stdout.split("\n"):
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            path = fix_guard._diff_path(line[4:])
        elif path is not None and line.startswith("@@"):
            hunk = _HUNK.match(line)
            if hunk:
                first = int(hunk.group(1))
                count = 1 if hunk.group(2) is None else int(hunk.group(2))
                added.setdefault(path, set()).update(range(first, first + count))
    return added


def failed_refs(worktree: Path, start_sha: str, checks: list, run) -> list:
    """Return the sorted refs of the commits of <start_sha>..HEAD that touch a failed file."""
    failed = {path for check in checks if check["status"] == "failed" for path in check["files"]}
    if not failed:
        return []
    listing = fix_names.git(
        worktree, run, "log", "--no-merges", "--format=%H %s", f"{start_sha}..HEAD",
    )
    if listing.returncode != 0:
        raise ValueError(f"git log a échoué : {listing.stderr.strip()}")
    refs = set()
    for line in listing.stdout.split("\n"):
        sha, _, subject = line.partition(" ")
        ref = fix_names.ref_of_subject(subject)
        if ref is None:
            continue
        touched = fix_names.git(
            worktree, run, "diff-tree", "-r", "-z", "--name-only", "--no-commit-id", "--root",
            "--no-renames", sha,
        )
        if touched.returncode != 0:
            raise ValueError(f"git diff-tree {sha[:12]} a échoué : {touched.stderr.strip()}")
        if failed & set(touched.stdout.split("\0")):
            refs.add(ref)
    return sorted(refs)


def host_syntax(worktree: Path, files: list, run) -> list:
    """Return the syntax entries of php -l and node --check run by the host, when they exist.

    Entries are {"status", "check", "path", "message"}; a file whose tool is missing has none.
    """
    entries = []
    for check, command in (("php -l", ["php", "-l"]), ("node --check", ["node", "--check"])):
        binary = shutil.which(command[0])
        if binary is None:
            continue
        for path in files:
            if not path.endswith(SYNTAX[check][1]):
                continue
            result = run(
                [binary, *command[1:], str(Path(worktree) / path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            message = ""
            if result.returncode != 0:
                message = _first_error(f"{result.stdout}\n{result.stderr}", str(worktree))
            entries.append({
                "status": "ok" if result.returncode == 0 else "ko", "check": check, "path": path,
                "message": message,
            })
    return entries


def run_checks(run, launcher: Path, check_dir: Path, worktree: Path, repo: str, files: list,
               added: dict, third_party: Optional[list] = None) -> list:
    """Run the checks of the changed files and return [{"name", "status", "files", "detail"}].

    status is "passed", "failed" or "not_run". check_dir receives files.txt and context.json,
    then what the launcher writes (syntax.tsv, tools/stylelint/).
    """
    extensions = STYLE_EXTENSIONS + tuple(ext for _, exts in SYNTAX.values() for ext in exts)
    wanted = [path for path in files if path.endswith(extensions)]
    odd = [path for path in wanted if "\n" in path or "\t" in path]
    files = [path for path in files if "\n" not in path and "\t" not in path]
    unsupported = [_check(UNSUPPORTED, "not_run", odd, "nom de fichier non pris en charge")] if odd else []
    if not any(path.endswith(extensions) for path in files):
        return unsupported
    check_dir = Path(check_dir)
    check_dir.mkdir(parents=True, exist_ok=True)
    (check_dir / "files.txt").write_text("".join(f"{path}\n" for path in files), encoding="utf-8")
    context = {"repos": [{"name": repo, "third_party_paths": third_party or []}]}
    (check_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
    done = run(
        ["bash", str(launcher), "check", str(check_dir), str(worktree), repo],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tsv = check_dir / "syntax.tsv"
    toolbox = tsv.is_file()
    if toolbox:
        entries = _read_tsv(tsv)
        missing = "résultat absent de la boîte à outils"
    else:
        entries = host_syntax(worktree, files, run)
        missing = TOOLBOX_DOWN
    checks = []
    for check, (label, extensions) in SYNTAX.items():
        wanted = [path for path in files if path.endswith(extensions)]
        if wanted:
            checks.append(_syntax_check(label, wanted, [e for e in entries if e["check"] == check],
                                        missing))
    if any(path.endswith(STYLE_EXTENSIONS) for path in files):
        if toolbox and done.returncode == 0:
            checks.append(_stylelint_check(check_dir, repo, files, added))
        else:
            checks.append(_check(STYLELINT, "not_run", [], TOOLBOX_DOWN))
    return checks + unsupported


def _syntax_check(label: str, wanted: list, entries: list, missing: str) -> dict:
    by_path = {entry["path"]: entry for entry in entries}
    failed = [path for path in wanted if by_path.get(path, {}).get("status") == "ko"]
    if failed:
        detail = " ; ".join(f"{path} : {by_path[path]['message']}".rstrip(" :") for path in failed)
        return _check(label, "failed", failed, detail)
    passed = [path for path in wanted if by_path.get(path, {}).get("status") == "ok"]
    if len(passed) == len(wanted):
        return _check(label, "passed", passed, "")
    skipped = [by_path[path] for path in wanted if path in by_path and by_path[path]["status"] == "skip"]
    detail = skipped[0]["message"] if skipped else missing
    return _check(label, "not_run", [path for path in wanted if path not in passed], detail)


def _stylelint_check(check_dir: Path, repo: str, files: list, added: dict) -> dict:
    raw = Path(check_dir) / "tools" / "stylelint" / "raw" / repo
    report = _load_json(raw / "report.json")
    configs = [_load_json(path) for path in sorted(raw.glob("config-*.json"))]
    if not isinstance(report, list) or not configs:
        return _check(STYLELINT, "not_run", [], "stylelint n'a pas produit de résultat")
    applied = {}
    prefix = f"{repo}/"
    for config in configs:
        if isinstance(config, dict) and isinstance(config.get("files"), list):
            for name in config["files"]:
                applied[_unprefixed(str(name), prefix)] = config.get("applied")
    styled = [path for path in files if path.endswith(STYLE_EXTENSIONS) and path in applied]
    if not styled:
        return _check(STYLELINT, "not_run", [], "aucun fichier évalué par stylelint")
    counted = [path for path in styled if applied[path] != "checklist"]
    errors = [entry for entry in report if isinstance(entry, dict) and entry.get("severity") == "error"]
    if any(_unprefixed(str(entry.get("file")), prefix) not in applied for entry in errors):
        return _check(STYLELINT, "not_run", [], "format de rapport stylelint inattendu")
    if not counted:
        return _check(STYLELINT, "not_run", [], "configuration du projet non chargée")
    found = []
    for entry in errors:
        path = _unprefixed(str(entry.get("file")), prefix)
        if path not in counted or entry.get("line") not in added.get(path, ()):
            continue
        if applied[path] != "project" and entry.get("rule") in CHECKLIST_RULES:
            continue
        found.append((path, entry))
    if not found:
        return _check(STYLELINT, "passed", counted, "")
    shown = [f"{path}:{entry['line']} {entry.get('rule')} : {entry.get('text')}"
             for path, entry in found[:MAX_DETAIL]]
    more = len(found) - len(shown)
    detail = " ; ".join(shown) + (f" ; et {more} autre(s)" if more > 0 else "")
    return _check(STYLELINT, "failed", sorted({path for path, _ in found}), detail)


def _unprefixed(name: str, prefix: str) -> str:
    return name[len(prefix):] if name.startswith(prefix) else name


def _check(name: str, status: str, files: list, detail: str) -> dict:
    return {"name": name, "status": status, "files": files, "detail": detail}


def _read_tsv(path: Path) -> list:
    entries = []
    for line in _read(path).split("\n"):
        parts = line.split("\t", 3)
        if len(parts) == 4 and parts[0] in ("ok", "ko", "skip"):
            entries.append({"status": parts[0], "check": parts[1], "path": parts[2],
                            "message": parts[3]})
    return entries


def _first_error(text: str, worktree: str) -> str:
    lines = [line.strip() for line in text.replace(worktree + "/", "").split("\n") if line.strip()]
    for line in lines:
        if "error" in line.lower():
            return line[:300]
    return lines[0][:300] if lines else ""


def _scripts(path: Path) -> dict:
    document = _load_json(path)
    scripts = document.get("scripts") if isinstance(document, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
