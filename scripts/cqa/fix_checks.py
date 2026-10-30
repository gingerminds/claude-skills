"""Checks of the fixed files without the project environment: syntax, stylelint, nginx, Varnish.

The checks run in the Docker toolbox (cqa-toolbox.sh check) on the worktree, never through the
project's own containers. A failed file maps back to the checklist rows whose commits touch it.
nginx and Varnish run on the worktree and on their folders at the start of the pass: only an
error the base does not have fails.
"""
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Optional

from cqa import fix_guard, fix_names

NOT_RUN_WHY = "à lancer dans l'environnement du projet (shared/runner.md)"
TOOLBOX_DOWN = "boîte à outils indisponible"
STYLELINT_FAILED = "stylelint a échoué dans la boîte à outils"
UNSUPPORTED = "Nom de fichier"
STYLELINT = "stylelint (configuration du projet)"
CHECKLIST_RULES = ("selector-class-pattern", "max-nesting-depth")
STYLE_EXTENSIONS = (".css", ".scss", ".sass", ".less", ".vue")
MAX_DETAIL = 10
SCSS = "Syntaxe SCSS (postcss-scss)"
ALREADY_BROKEN = "erreur déjà présente avant le correctif"
MISSING = "résultat absent de la boîte à outils"
# kind -> (label, check column of config.tsv)
CONFIG = {
    "nginx": ("Syntaxe nginx (nginx -t)", "nginx -t"),
    "varnish": ("Syntaxe Varnish (varnishd -C)", "varnishd -C"),
}
CONFIG_PLAN = "config-plan.tsv"
# Host names nginx and varnishd resolve at load time, mapped to the loopback by --add-host.
DUMMY_HOST = "example.test"
_NGINX_PASS = re.compile(r"\b(?:proxy|fastcgi|uwsgi|scgi|grpc|memcached)_pass\s+([^;\s]+)")
_NGINX_SERVER = re.compile(r"(?:^|[{;])\s*server\s+([^;\s{]+)", re.MULTILINE)
_NGINX_RESOLVER = re.compile(r"\bresolver\s+([^;]+);")
_VCL_HOST = re.compile(r"\.host\s*=\s*\"([^\"]*)\"")
_HOST = re.compile(r"(?=.*[A-Za-z_])[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?")
_MAX_HOSTS = 100
_MAX_FILES = 500
_MAX_BYTES = 1_000_000
_SKIPPED_DIRS = {".git", "node_modules", "vendor"}
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


def config_kind(path: str) -> Optional[str]:
    """Return "nginx" for a *.conf or *.conf.template under an nginx folder, "varnish" for a
    *.vcl, else None."""
    parts = path.split("/")
    if parts[-1].endswith(".vcl"):
        return "varnish"
    if "nginx" in parts[:-1] and parts[-1].endswith((".conf", ".conf.template")):
        return "nginx"
    return None


def config_plan(files: list) -> list:
    """Return [(kind, folder, path)] of the nginx and Varnish files among files.

    folder is what the check copies and the base keeps: the outermost nginx folder of an nginx
    file (copied over the image's /etc/nginx), the folder of a VCL file ("." at the root).
    """
    plan = []
    for path in files:
        kind = config_kind(path)
        if kind is None:
            continue
        parts = path.split("/")
        if kind == "nginx":
            folder = "/".join(parts[: parts.index("nginx") + 1])
        else:
            folder = "/".join(parts[:-1]) or "."
        plan.append((kind, folder, path))
    return plan


def write_config_plan(check_dir: Path, worktree: Path) -> list:
    """Write <check_dir>/config-plan.tsv from files.txt and return [(kind, host)]: the host names
    the nginx and Varnish files of the worktree and of <check_dir>/base resolve when loaded.

    Called by cqa-toolbox.sh check, which passes each host as --add-host <host>:127.0.0.1.
    """
    check_dir = Path(check_dir)
    files = [line for line in _read(check_dir / "files.txt").split("\n") if line]
    plan = config_plan(files)
    (check_dir / CONFIG_PLAN).write_text(
        "".join(f"{kind}\t{folder}\t{path}\n" for kind, folder, path in plan), encoding="utf-8")
    found = []
    for kind in CONFIG:
        folders = sorted({folder for each, folder, _ in plan if each == kind})
        if not folders:
            continue
        hosts = {DUMMY_HOST} if kind == "nginx" else set()
        for root in (Path(worktree), check_dir / "base"):
            for folder in folders:
                for path in _folder_files(root / folder, kind):
                    hosts.update(_hosts(kind, _read_small(path)))
        found.extend((kind, host) for host in sorted(hosts)[:_MAX_HOSTS])
    return found


def export_base(worktree: Path, start_sha: str, plan: list, base: Path, run) -> None:
    """Write the folders of plan as they are at start_sha under base (only the VCL files of a
    Varnish folder). A folder absent at start_sha gives nothing: its files have no base."""
    kinds = {}
    for kind, folder, _ in plan:
        kinds.setdefault(folder, set()).add(kind)
    for folder, folder_kinds in sorted(kinds.items()):
        listing = fix_names.git(worktree, run, "ls-tree", "-r", "-l", "-z", start_sha, "--", folder)
        if listing.returncode != 0:
            raise ValueError(f"git ls-tree a échoué : {listing.stderr.strip()}")
        count = 0
        for entry in listing.stdout.split("\0"):
            meta, _, path = entry.partition("\t")
            fields = meta.split()
            if len(fields) != 4 or fields[0] not in ("100644", "100755") or fields[1] != "blob":
                continue
            if "nginx" not in folder_kinds and not path.endswith(".vcl"):
                continue
            parts = PurePosixPath(path).parts
            if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
                continue
            if not fields[3].isdigit() or int(fields[3]) > _MAX_BYTES or count >= _MAX_FILES:
                continue
            blob = fix_names.git(worktree, run, "cat-file", "blob", fields[2], binary=True)
            if blob.returncode != 0:
                raise ValueError(f"git cat-file {fields[2][:12]} a échoué.")
            target = Path(base, *parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.stdout)
            count += 1


def run_checks(run, launcher: Path, check_dir: Path, worktree: Path, repo: str, files: list,
               added: dict, third_party: Optional[list] = None,
               start_sha: Optional[str] = None) -> list:
    """Run the checks of the changed files and return [{"name", "status", "files", "detail"}].

    status is "passed", "failed" or "not_run". check_dir receives files.txt, context.json and,
    with start_sha, base/ (the nginx and Varnish folders at start_sha), then what the launcher
    writes (syntax.tsv, config.tsv, tools/stylelint/).
    """
    extensions = STYLE_EXTENSIONS + tuple(ext for _, exts in SYNTAX.values() for ext in exts)
    wanted = [path for path in files if path.endswith(extensions) or config_kind(path)]
    odd = [path for path in wanted if "\n" in path or "\t" in path]
    files = [path for path in files if "\n" not in path and "\t" not in path]
    unsupported = [_check(UNSUPPORTED, "not_run", odd, "nom de fichier non pris en charge")] if odd else []
    plan = config_plan(files)
    if not plan and not any(path.endswith(extensions) for path in files):
        return unsupported
    check_dir = Path(check_dir)
    check_dir.mkdir(parents=True, exist_ok=True)
    (check_dir / "files.txt").write_text("".join(f"{path}\n" for path in files), encoding="utf-8")
    context = {"repos": [{"name": repo, "third_party_paths": third_party or []}]}
    (check_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
    if plan and start_sha:
        export_base(worktree, start_sha, plan, check_dir / "base", run)
    done = run(
        ["bash", str(launcher), "check", str(check_dir), str(worktree), repo],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tsv = check_dir / "syntax.tsv"
    toolbox = tsv.is_file()
    if toolbox:
        entries = _read_tsv(tsv)
        missing = MISSING
    else:
        entries = host_syntax(worktree, files, run)
        missing = TOOLBOX_DOWN
    checks = []
    for check, (label, extensions) in SYNTAX.items():
        wanted = [path for path in files if path.endswith(extensions)]
        if wanted:
            checks.append(_syntax_check(label, wanted, [e for e in entries if e["check"] == check],
                                        missing))
    linted = []
    if any(path.endswith(STYLE_EXTENSIONS) for path in files):
        if toolbox and done.returncode == 0:
            stylelint, linted = _stylelint_check(check_dir, repo, files, added)
            checks.append(stylelint)
        else:
            # syntax.tsv is there only when the syntax ran: then the stylelint container failed
            why = STYLELINT_FAILED if toolbox else TOOLBOX_DOWN
            checks.append(_check(STYLELINT, "not_run", [], why))
    scss = [path for path in files if path.endswith(".scss") and path not in linted]
    if scss:
        checks.append(_syntax_check(SCSS, scss, [e for e in entries if e["check"] == "scss"], missing))
    if plan:
        config = check_dir / "config.tsv"
        head, base = _read_config(config)
        why = MISSING if config.is_file() else TOOLBOX_DOWN
        for kind, (label, name) in CONFIG.items():
            wanted = [path for each, _, path in plan if each == kind]
            if wanted:
                checks.append(_config_check(label, wanted, [e for e in head if e["check"] == name],
                                            [e for e in base if e["check"] == name], why))
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


def _config_check(label: str, wanted: list, head: list, base: list, missing: str) -> dict:
    """An nginx or Varnish check: a file fails only on an error its base does not have."""
    now = {entry["path"]: entry for entry in head}
    before = {entry["path"]: entry for entry in base}
    failed, known, passed = [], [], []
    for path in wanted:
        entry = now.get(path, {})
        if entry.get("status") == "ok":
            passed.append(path)
        elif entry.get("status") == "ko":
            old = before.get(path, {})
            if old.get("status") == "ko" and _error_key(old["message"]) == _error_key(entry["message"]):
                known.append(path)
            else:
                failed.append(path)
    if failed:
        detail = " ; ".join(f"{path} : {now[path]['message']}".rstrip(" :") for path in failed)
        return _check(label, "failed", failed, detail)
    if len(passed) == len(wanted):
        return _check(label, "passed", passed, "")
    rest = [path for path in wanted if path not in passed]
    if known:
        detail = f"{ALREADY_BROKEN} (" + " ; ".join(
            f"{path} : {now[path]['message']}".rstrip(" :") for path in known) + ")"
    else:
        skipped = [now[path] for path in rest if now.get(path, {}).get("status") == "skip"]
        detail = skipped[0]["message"] if skipped else missing
    return _check(label, "not_run", rest, detail)


def _error_key(message: str) -> str:
    """The message without its line and column numbers, which move when lines are added."""
    text = re.sub(r"(?::\d+)+", "", message)
    text = re.sub(r"\b(?:[Ll]ine|[Pp]os)\s+\d+", "", text)
    return " ".join(text.split())


def _read_config(path: Path) -> tuple:
    """Return the (head, base) entries of config.tsv: side, status, check, path, message."""
    sides = {"head": [], "base": []}
    for line in _read(path).split("\n"):
        parts = line.split("\t", 4)
        if len(parts) == 5 and parts[0] in sides and parts[1] in ("ok", "ko", "skip"):
            sides[parts[0]].append({"status": parts[1], "check": parts[2], "path": parts[3],
                                    "message": parts[4]})
    return sides["head"], sides["base"]


def _folder_files(folder: Path, kind: str) -> list:
    """Return the regular files of folder (only *.vcl for Varnish), bounded, without symlinks."""
    found = []
    if not folder.is_dir() or folder.is_symlink():
        return found
    for current, dirs, names in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if name not in _SKIPPED_DIRS)
        for name in sorted(names):
            path = Path(current) / name
            if kind == "varnish" and not name.endswith(".vcl"):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            found.append(path)
            if len(found) >= _MAX_FILES:
                return found
    return found


def _hosts(kind: str, text: str) -> set:
    """Return the host names an nginx or VCL text resolves at load time."""
    if kind == "varnish":
        return {host for host in (_host_part(value) for value in _VCL_HOST.findall(text)) if host}
    text = "\n".join(line.split("#", 1)[0] for line in text.split("\n"))
    values = _NGINX_PASS.findall(text) + _NGINX_SERVER.findall(text)
    for resolver in _NGINX_RESOLVER.findall(text):
        values.extend(token for token in resolver.split() if "=" not in token)
    return {host for host in (_host_part(value) for value in values) if host}


def _host_part(value: str) -> Optional[str]:
    """Return the host of "[scheme://]host[:port][/path]", or None when it is not a plain name."""
    value = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", value.strip())
    host = re.split(r"[:/]", value, maxsplit=1)[0]
    if host in ("unix", "localhost") or not _HOST.fullmatch(host):
        return None
    return host


def _read_small(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ""
    except OSError:
        return ""
    return _read(path)


def _stylelint_check(check_dir: Path, repo: str, files: list, added: dict) -> tuple:
    """Return (check, the changed style files stylelint evaluated with the project's config)."""
    raw = Path(check_dir) / "tools" / "stylelint" / "raw" / repo
    report = _load_json(raw / "report.json")
    configs = [_load_json(path) for path in sorted(raw.glob("config-*.json"))]
    if not isinstance(report, list) or not configs:
        return _check(STYLELINT, "not_run", [], "stylelint n'a pas produit de résultat"), []
    applied = {}
    prefix = f"{repo}/"
    for config in configs:
        if isinstance(config, dict) and isinstance(config.get("files"), list):
            for name in config["files"]:
                applied[_unprefixed(str(name), prefix)] = config.get("applied")
    styled = [path for path in files if path.endswith(STYLE_EXTENSIONS) and path in applied]
    if not styled:
        return _check(STYLELINT, "not_run", [], "aucun fichier évalué par stylelint"), []
    counted = [path for path in styled if applied[path] != "checklist"]
    errors = [entry for entry in report if isinstance(entry, dict) and entry.get("severity") == "error"]
    if any(_unprefixed(str(entry.get("file")), prefix) not in applied for entry in errors):
        return _check(STYLELINT, "not_run", [], "format de rapport stylelint inattendu"), []
    if not counted:
        return _check(STYLELINT, "not_run", [], "configuration du projet non chargée"), []
    found = []
    for entry in errors:
        path = _unprefixed(str(entry.get("file")), prefix)
        if path not in counted or entry.get("line") not in added.get(path, ()):
            continue
        if applied[path] != "project" and entry.get("rule") in CHECKLIST_RULES:
            continue
        found.append((path, entry))
    if not found:
        return _check(STYLELINT, "passed", counted, ""), counted
    shown = [f"{path}:{entry['line']} {entry.get('rule')} : {entry.get('text')}"
             for path, entry in found[:MAX_DETAIL]]
    more = len(found) - len(shown)
    detail = " ; ".join(shown) + (f" ; et {more} autre(s)" if more > 0 else "")
    return _check(STYLELINT, "failed", sorted({path for path, _ in found}), detail), counted


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
