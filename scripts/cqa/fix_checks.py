"""Checks of the fixed files without the project environment: syntax, stylelint, nginx, Varnish.

The checks run in the Docker toolbox (cqa-toolbox.sh check) on the worktree, never through the
project's own containers. A failed file maps back to the checklist rows whose commits touch it.
nginx and Varnish run on the worktree and on their folders at the start of the pass: only an
error the base does not have fails.

The commands of the project (install, build, lint, test) run one by one in disposable containers
(cqa-toolbox.sh exec) on the same worktree, which is put back as it was after each of them. Only
a failure the base does not have fails; an error of the environment is never a failure. The install
a fixer asks for runs alone (run_install), keeping the manifest and the lock file it changed.
"""
import json
import os
import re
import shlex
import shutil
from pathlib import Path, PurePosixPath
from typing import Optional

from cqa import fix_commands, fix_guard, fix_names
from cqa.secrets import known_values_from_env, scan_spans

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
OUTSIDE = "contrôle impossible hors de l'environnement du projet"
MISSING = "résultat absent de la boîte à outils"
INSTALL_IMPOSSIBLE = "installation des dépendances impossible"
PRIVATE_DEPENDENCIES = "dépendances privées : non transmises"
TIMED_OUT = "délai dépassé"
REFORMATTED = "fichiers reformatés par le linter"
MASKED_LINE = "ligne masquée : secret possible"
LAUNCHER_FAILED = "exécution impossible"
BASELINE_DIR = "baseline-wt"
INSTALL_TIMEOUT = 900
COMMAND_TIMEOUT = 600
TAIL_LINES = 10
_TAIL_WINDOW = 200
_MAX_LOG = 2_000_000
_CHUNK = 500
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TOKEN = re.compile(r"[\w./@+~-]+")
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
# Errors the offline image gives whatever the change: a module, a file, a certificate or a host
# of the project's environment is missing. They are not_run, never failed, with or without a base.
_ENVIRONMENT = {
    "nginx": re.compile(r"unknown directive|cannot load certificate|BIO_new_file|no such file|"
                        r"dlopen\(\)|load_module|host not found", re.IGNORECASE),
    "varnish": re.compile(r"could not (?:find|open|load) vmod|cannot read file|no such file|"
                          r"could not be resolved", re.IGNORECASE),
}
_MAIN_BLOCK = re.compile(r"^[ \t]*(?:events|http)[ \t]*(?:\{|$)", re.MULTILINE)
_NGINX_AT = re.compile(r" in (\S+):(\d+)\s*$")
_VCL_AT = re.compile(r"\('(.+?)' Line (\d+) Pos \d+\)")
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


def shell_command(item: dict) -> str:
    """Return the text sh -c runs for a command: its env exported first, so that it holds for
    every command of a chain joined by "&&". The values are quoted, never expanded."""
    env = item.get("env") or {}
    if not env:
        return item["command"]
    assigned = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
    return f"export {assigned}; {item['command']}"


def run_project_commands(run, launcher: Path, worktree: Path, commands: list, log_dir: Path,
                         pass_files: tuple = ()) -> list:
    """Run the commands of the project one after the other in worktree; return one result each.

    A result is {"name", "status", "files", "detail", "kind", "command", "log", "folder",
    "exact"}: status "ok", "failed", "not_run" or "already_broken". The commands run in the order
    given, each through cqa-toolbox.sh exec (network for an install only), and the worktree is
    put back as it was after each (restore_worktree). The full log of each is a file of log_dir.

    A failed or not run install makes the later commands of its folder not_run, as a command that
    needs private dependencies, which is never run. An error of the environment (a launcher that
    refuses, an exit 124) is not_run, never failed. A lint that exits 0 but rewrites tracked files
    is judged by them: failed on those the pass changed (exact), already_broken on the others.
    The detail of a failure is the end of its log, without secrets (log_tail).
    """
    worktree, log_dir = Path(worktree), Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    changed = set(pass_files)
    blocked, results = {}, []
    for index, item in enumerate(commands, 1):
        folder = fix_commands.start_dir(item)
        log = log_dir / f"{index:02d}-{_slug(item['name'])}.log"
        if folder in blocked:
            results.append(_result(item, folder, "not_run", blocked[folder]))
            continue
        if item["private"]:
            blocked[folder] = PRIVATE_DEPENDENCIES
            results.append(_result(item, folder, "not_run", PRIVATE_DEPENDENCIES))
            continue
        result = _execute(run, launcher, worktree, item, folder, log, changed)
        if item["kind"] == "install" and result["status"] in ("failed", "not_run"):
            blocked[folder] = INSTALL_IMPOSSIBLE
        results.append(result)
    return results


def _execute(run, launcher: Path, worktree: Path, item: dict, folder: str, log: Path,
             changed: set) -> dict:
    log.unlink(missing_ok=True)
    try:
        done = _launch(run, launcher, worktree, item, log)
    except OSError as error:
        return _result(item, folder, "not_run", f"{LAUNCHER_FAILED} : {error}")
    finally:
        modified = restore_worktree(worktree, run)
    failure = _failure(item, folder, done, log)
    if failure is not None:
        return failure
    if item["kind"] == "lint" and modified:
        inside = [path for path in modified if path in changed]
        if inside:
            return _result(item, folder, "failed", f"{REFORMATTED} : {listed(inside)}",
                           files=inside, log=log, exact=True)
        return _result(item, folder, "already_broken",
                       f"{REFORMATTED}, hors des fichiers de la passe : {listed(modified)}",
                       log=log)
    return _result(item, folder, "ok", log=log)


def _launch(run, launcher: Path, worktree: Path, item: dict, log: Path):
    """Run the command in a disposable container (cqa-toolbox.sh exec): the network is open for an
    install only, and the timeout follows the kind."""
    install = item["kind"] == "install"
    return run(
        ["bash", str(launcher), "exec", str(worktree), str(log), item["image"], item["dir"],
         "on" if install else "off", str(INSTALL_TIMEOUT if install else COMMAND_TIMEOUT), "--",
         shell_command(item)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _failure(item: dict, folder: str, done, log: Path) -> Optional[dict]:
    """Return the result of a command that did not exit 0 (None when it did): a timeout and a
    refusal of the launcher are not_run, any other code is a failure with the end of its log."""
    code = done.returncode
    if code == 0:
        return None
    if code == 124:
        return _result(item, folder, "not_run", TIMED_OUT, log=log)
    if code == 2 and not log.exists():
        # A refusal of the launcher (image absent, Docker down) comes before the log is made.
        lines = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
        return _result(item, folder, "not_run", lines[-1][:300] if lines else LAUNCHER_FAILED)
    return _result(item, folder, "failed", log_tail(_read_log(log)), log=log)


def run_install(run, launcher: Path, worktree: Path, item: dict, log: Path,
                allowed: tuple) -> dict:
    """Run one install command (kind install) in worktree and keep what it changed in allowed.

    Returns the result of run_project_commands for it (status ok, failed or not_run) with two more
    fields: changed, the files of allowed it modified or created, and stray, every other file of
    the worktree it touched (modified, deleted or untracked and not ignored; ignored files, such as
    node_modules, are left). The changes of allowed stay in the worktree, uncommitted, only when
    the command succeeded, touched nothing else and changed something: in every other case, and
    when the run is interrupted, the worktree is put back as it was (restore_worktree).
    """
    worktree = Path(worktree)
    folder = fix_commands.start_dir(item)
    log.unlink(missing_ok=True)
    changed, stray, keep = [], [], False
    try:
        try:
            done = _launch(run, launcher, worktree, item, log)
        except OSError as error:
            result = _result(item, folder, "not_run", f"{LAUNCHER_FAILED} : {error}")
        else:
            result = _failure(item, folder, done, log) or _result(item, folder, "ok", log=log)
        dirty = dirty_files(worktree, run)
        changed = [path for path in dirty if path in allowed]
        stray = [path for path in dirty if path not in allowed]
        keep = result["status"] == "ok" and bool(changed) and not stray
    finally:
        if not keep:
            restore_worktree(worktree, run)
    return {**result, "changed": changed, "stray": stray}


def _result(item: dict, folder: str, status: str, detail: str = "", files=(), log=None,
            exact: bool = False) -> dict:
    return {"name": item["name"], "status": status, "files": list(files), "detail": detail,
            "kind": item["kind"], "command": item["command"],
            "log": str(log) if log is not None else "", "folder": folder, "exact": exact}


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:40] or "command"


def listed(paths: list) -> str:
    shown = ", ".join(paths[:MAX_DETAIL])
    more = len(paths) - MAX_DETAIL
    return shown + (f" et {more} autre(s)" if more > 0 else "")


def _read_log(path: Path) -> str:
    """Return the end of a log (at most _MAX_LOG bytes) without colors and progress rewrites."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _MAX_LOG:
                handle.seek(size - _MAX_LOG)
            data = handle.read()
    except OSError:
        return ""
    text = _ANSI.sub("", data.decode("utf-8", errors="replace")).replace("\r\n", "\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


def log_tail(text: str) -> str:
    """Return the last TAIL_LINES non-empty lines of a log. A line the secret scan flags is
    replaced by MASKED_LINE, and so are the lines of a private key from its first line on."""
    lines = [line.rstrip() for line in text.split("\n") if line.strip()][-_TAIL_WINDOW:]
    hidden = set()
    for label, first, last in scan_spans("\n".join(lines), known_values_from_env()):
        hidden.update(range(first, (len(lines) if label == "private_key" else last) + 1))
    shown = [MASKED_LINE if number in hidden else line[:300]
             for number, line in enumerate(lines, 1)]
    return "\n".join(shown[-TAIL_LINES:])


def cited_files(text: str, files: list, folder: str = "") -> list:
    """Return the files a log names: by their path under the repo, under /work/ (the worktree
    as the container sees it), or relative to folder, where the command worked."""
    tokens = set()
    for token in _TOKEN.findall(text):
        token = token.rstrip(".")
        for prefix in ("/work/", "./"):
            if token.startswith(prefix):
                token = token[len(prefix):]
        tokens.add(token)
    found = []
    for path in files:
        relative = path[len(folder) + 1:] if folder and path.startswith(folder + "/") else None
        if path in tokens or (relative is not None and relative in tokens):
            found.append(path)
    return found


def project_checks(results: list, base: Optional[list], pass_files: list,
                   log_prefix: str = "") -> list:
    """Return the entries of checks.json for the results of run_project_commands.

    status is "passed", "failed" or "not_run", as for the other checks. A failure the base has
    too (a result of base failed or already_broken, by name) is not attributed to the pass: its
    entry is not_run, with already_broken true and ALREADY_BROKEN in its detail. Any other failure
    is attributed to the files of the pass its log names, else to those under its folder, else to
    all of them (failed_refs maps them to the rows). A failure by files (exact) keeps its own.
    """
    broken = {item["name"] for item in base or [] if item["status"] in ("failed", "already_broken")}
    changed = list(pass_files)
    checks = []
    for item in results:
        log = f"{log_prefix}/{Path(item['log']).name}".lstrip("/") if item["log"] else ""
        status, files, detail, known = item["status"], [], item["detail"], False
        if status == "ok":
            status, detail = "passed", ""
        elif status == "failed" and not item["exact"] and item["name"] in broken:
            known = True
        elif status == "failed":
            files = item["files"] if item["exact"] else _attributed(item, changed)
        elif status == "already_broken":
            known = True
        if known:
            last = next((line for line in reversed(detail.split("\n")) if line.strip()), "")
            status, detail = "not_run", f"{ALREADY_BROKEN} ({last})" if last else ALREADY_BROKEN
        entry = {"name": item["name"], "status": status, "files": files, "detail": detail,
                 "kind": item["kind"], "command": item["command"]}
        if log:
            entry["log"] = log
        if known:
            entry["already_broken"] = True
        checks.append(entry)
    return checks


def _attributed(item: dict, changed: list) -> list:
    cited = cited_files(_read_log(Path(item["log"])) if item["log"] else "", changed, item["folder"])
    if cited:
        return cited
    folder = item["folder"]
    under = [path for path in changed if not folder or path == folder or path.startswith(folder + "/")]
    return under or changed


def measure_base(run, launcher: Path, worktree: Path, start_sha: str, commands: list,
                 check_dir: Path) -> list:
    """Run the commands on a worktree of start_sha detached under check_dir, removed after;
    return their results (run_project_commands). Raises ValueError when it cannot be made."""
    check_dir = Path(check_dir)
    base = check_dir / BASELINE_DIR
    shutil.rmtree(base, ignore_errors=True)
    fix_names.git(worktree, run, "worktree", "prune")
    added = fix_names.git(worktree, run, "worktree", "add", "--detach", str(base), start_sha)
    if added.returncode != 0:
        raise ValueError(f"État de départ non mesuré : git worktree add a échoué : "
                         f"{added.stderr.strip()}")
    try:
        return run_project_commands(run, launcher, base, commands, check_dir / "baseline-logs")
    finally:
        fix_names.git(worktree, run, "worktree", "remove", "--force", str(base))
        shutil.rmtree(base, ignore_errors=True)
        fix_names.git(worktree, run, "worktree", "prune")


def dirty_files(worktree: Path, run) -> list:
    """Return the sorted files of the worktree that differ from HEAD: modified, deleted or
    untracked, never the ignored ones."""
    return sorted(path for path, _ in _status(worktree, run))


def restore_worktree(worktree: Path, run, keep: tuple = ()) -> list:
    """Put the worktree back as it was: restore the tracked files that were modified (git checkout
    -- .), except keep, and delete the untracked files that are not ignored (and the folders they
    leave empty), except keep. Ignored files stay: node_modules and vendor serve the next
    command. Return the sorted tracked files that were modified, those kept included."""
    worktree = Path(worktree)
    kept = set(keep)
    entries = _status(worktree, run)
    modified = sorted(path for path, code in entries if code != "??")
    if modified:
        if not kept & set(modified):
            _checkout(worktree, run, ["."])
        else:
            literal = [f":(literal){path}" for path in modified if path not in kept]
            for start in range(0, len(literal), _CHUNK):
                _checkout(worktree, run, literal[start:start + _CHUNK])
    for path, code in entries:
        if code == "??" and path not in kept:
            _remove(worktree, path)
    return modified


def _checkout(worktree: Path, run, paths: list) -> None:
    result = fix_names.git(worktree, run, "checkout", "--", *paths)
    if result.returncode != 0:
        raise ValueError(f"git checkout a échoué dans {worktree} : {result.stderr.strip()}")


def _remove(worktree: Path, path: str) -> None:
    posix = PurePosixPath(path)
    if not posix.parts or posix.is_absolute() or ".." in posix.parts:
        return
    target = worktree.joinpath(*posix.parts)
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        parent = target.parent
        while parent != worktree and worktree in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    except OSError as error:
        raise ValueError(f"{path} n'a pas pu être retiré du worktree : {error}")


def _status(worktree: Path, run) -> list:
    """Return the (path, XY) entries of git status, untracked files included, ignored ones not."""
    result = fix_names.git(worktree, run, "status", "--porcelain=v1", "-uall", "-z")
    if result.returncode != 0:
        raise ValueError(f"git status a échoué dans {worktree} : {result.stderr.strip()}")
    fields = result.stdout.split("\0")
    entries, index = [], 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        if "R" in field[:2] or "C" in field[:2]:
            index += 1
        entries.append((field[3:], field[:2]))
    return entries


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
    """Return "nginx" for a *.conf or *.conf.template under an nginx folder or any nginx.conf,
    "varnish" for a *.vcl, else None."""
    parts = path.split("/")
    if parts[-1].endswith(".vcl"):
        return "varnish"
    if parts[-1] == "nginx.conf" or (
            "nginx" in parts[:-1] and parts[-1].endswith((".conf", ".conf.template"))):
        return "nginx"
    return None


def config_plan(files: list) -> list:
    """Return [(kind, folder, path)] of the nginx and Varnish files among files.

    folder is what the check copies and the base keeps: the outermost nginx folder of an nginx
    file, copied whole over the image's /etc/nginx; the folder of an nginx.conf outside such a
    folder, of which only that file is copied; the folder of a VCL file. "." is the root.
    """
    plan = []
    for path in files:
        kind = config_kind(path)
        if kind is None:
            continue
        parts = path.split("/")
        if kind == "nginx" and "nginx" in parts[:-1]:
            folder = "/".join(parts[: parts.index("nginx") + 1])
        else:
            folder = "/".join(parts[:-1]) or "."
        plan.append((kind, folder, path))
    return plan


def whole_folder(kind: str, folder: str) -> bool:
    """True when the check copies all of folder: an nginx folder, not the folder of a lone
    nginx.conf (its last segment is never "nginx")."""
    return kind == "nginx" and folder.split("/")[-1] == "nginx"


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
        entries = sorted({(folder, path) for each, folder, path in plan if each == kind})
        if not entries:
            continue
        hosts = {DUMMY_HOST} if kind == "nginx" else set()
        for root in (Path(worktree), check_dir / "base"):
            for folder, path in entries:
                lone = root / path
                if kind == "varnish" or whole_folder(kind, folder):
                    scanned = _folder_files(root / folder, kind)
                else:
                    scanned = [lone] if lone.is_file() and not lone.is_symlink() else []
                for scanned_path in scanned:
                    hosts.update(_hosts(kind, _read_small(scanned_path)))
        found.extend((kind, host) for host in sorted(hosts)[:_MAX_HOSTS])
    return found


def export_base(worktree: Path, start_sha: str, plan: list, base: Path, run) -> None:
    """Write what the check copies, as it is at start_sha, under base: an nginx folder, a lone
    nginx.conf, the VCL files of a Varnish folder. A file absent at start_sha has no base."""
    queries = set()
    for kind, folder, path in plan:
        if kind == "varnish":
            queries.add((folder, ".vcl"))
        elif whole_folder(kind, folder):
            queries.add((folder, ""))
        else:
            queries.add((path, ""))
    written = set()
    for pathspec, suffix in sorted(queries):
        listing = fix_names.git(worktree, run, "ls-tree", "-r", "-l", "-z", start_sha, "--", pathspec)
        if listing.returncode != 0:
            raise ValueError(f"git ls-tree a échoué : {listing.stderr.strip()}")
        count = 0
        for entry in listing.stdout.split("\0"):
            meta, _, path = entry.partition("\t")
            fields = meta.split()
            if len(fields) != 4 or fields[0] not in ("100644", "100755") or fields[1] != "blob":
                continue
            if not path.endswith(suffix) or path in written:
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
            written.add(path)
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
        roots = (Path(worktree), check_dir / "base")
        for kind, (_, name) in CONFIG.items():
            wanted = [path for each, _, path in plan if each == kind]
            if wanted:
                checks.append(_config_check(kind, wanted, [e for e in head if e["check"] == name],
                                            [e for e in base if e["check"] == name], why, roots))
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


def _config_check(kind: str, wanted: list, head: list, base: list, missing: str,
                  roots: tuple) -> dict:
    """An nginx or Varnish check of wanted. An error of the environment (_outside) is not_run;
    another one fails unless its base has the same error. roots: (worktree, base folder)."""
    label = CONFIG[kind][0]
    now = {entry["path"]: entry for entry in head}
    before = {entry["path"]: entry for entry in base}
    failed, known, outside, passed = [], [], [], []
    for path in wanted:
        entry = now.get(path, {})
        if entry.get("status") == "ok":
            passed.append(path)
        elif entry.get("status") == "ko":
            old = before.get(path, {})
            if _outside(kind, entry["message"], Path(roots[0]) / path):
                outside.append(path)
            elif old.get("status") == "ko" and (_error_key(kind, old["message"], roots[1])
                                                == _error_key(kind, entry["message"], roots[0])):
                known.append(path)
            else:
                failed.append(path)
    if failed:
        detail = " ; ".join(f"{path} : {now[path]['message']}".rstrip(" :") for path in failed)
        return _check(label, "failed", failed, detail)
    if len(passed) == len(wanted):
        return _check(label, "passed", passed, "")
    rest = [path for path in wanted if path not in passed]
    reasons = []
    if known:
        reasons.append(f"{ALREADY_BROKEN} (" + " ; ".join(
            f"{path} : {now[path]['message']}".rstrip(" :") for path in known) + ")")
    reasons.extend(f"{OUTSIDE} : {now[path]['message']}" for path in outside)
    if not reasons:
        skipped = [now[path] for path in rest if now.get(path, {}).get("status") == "skip"]
        reasons.append(skipped[0]["message"] if skipped else missing)
    return _check(label, "not_run", rest, " ; ".join(reasons))


def _outside(kind: str, message: str, source: Path) -> bool:
    """True when the error comes from the offline image, not from the change: see _ENVIRONMENT,
    and for nginx a directive still misplaced after the last wrapper (the file was checked out of
    the context the project includes it in; a main file is never wrapped)."""
    if _ENVIRONMENT[kind].search(message):
        return True
    return (kind == "nginx" and "is not allowed here" in message
            and not _MAIN_BLOCK.search(_read_small(source)))


def _error_key(kind: str, message: str, root: Path) -> str:
    """The message without the line and column numbers of its location, which move when lines
    are added, plus the trimmed source line it points at under root, so that the same text on
    another line of code is another error."""
    text = re.sub(r"( in \S+):\d+(?=\s|$)", r"\1", message)
    text = re.sub(r"\b(?:Line|Pos)\s+\d+", "", text)
    return " ".join(text.split()) + "\n" + _source_line(kind, message, Path(root))


def _source_line(kind: str, message: str, root: Path) -> str:
    """Return the trimmed line the message points at under root ("" when unknown)."""
    found = (_VCL_AT if kind == "varnish" else _NGINX_AT).findall(message)
    if not found:
        return ""
    path, number = found[-1]
    parts = PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
        return ""
    for candidate in (Path(root, *parts), Path(root, *parts[:-1], parts[-1] + ".template")):
        if candidate.is_file() and not candidate.is_symlink():
            lines = _read_small(candidate).split("\n")
            index = int(number) - 1
            return lines[index].strip() if 0 <= index < len(lines) else ""
    return ""


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
