"""Detection of the commands that check a project (install, build, lint, test).

The commands come from the project, never from the sub-agents: first its GitLab CI (jobs that
run on a clean checkout in a known image), then the manifests of the folders the CI does not
work in. The functions here are pure: nothing is executed and nothing is written. The lead
validates the list (cqa_fix.py commands) before the commands run in disposable containers.

A command is {"name", "dir" (relative to the repo, "" for its root), "image", "kind" (install,
build, lint or test), "command", "env", "source", "private"}. A job of the CI that uses a
variable which is not a literal, a service, a trigger, a deployment or docker, ssh, rsync, scp or
sudo is never retained: its commands could need a secret or reach beyond the worktree.
"""
import fnmatch
import json
import os
import posixpath
import re
import shlex
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

KINDS = ("install", "build", "lint", "test")
CI_FILE = ".gitlab-ci.yml"
WORKDIR = "/work"
DEFAULT_NODE = "node:22-alpine"
COMPOSER_IMAGE = "composer:2"
MAX_DEPTH = 4
SKIPPED_DIRS = {"node_modules", "vendor", ".git"}

_GLOBAL_KEYS = {"default", "include", "stages", "types", "variables", "workflow", "image",
                "services", "cache", "before_script", "after_script", "spec"}
_DEFAULT_STAGES = ("build", "test", "deploy")
_DEPLOY_WORDS = ("deploy", "release", "push", "publish", "pages")
_FORBIDDEN = re.compile(r"\b(?:docker|ssh|rsync|scp|sudo)\b", re.IGNORECASE)
# A reference to a variable: $X or ${X...}; a plain $X or ${X} is what _substitute replaces.
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")
_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_PROJECT_DIR = re.compile(r"\$\{CI_PROJECT_DIR\}|\$CI_PROJECT_DIR(?![A-Za-z0-9_])")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IMAGE = re.compile(r"[A-Za-z0-9._/:@-]+")
_COMMENT = re.compile(r"(?:^|\s)#")
_SHELL_META = re.compile(r"[;|&<>`()]")
_ARGUMENT = re.compile(r"[\w=.:/@+~${}\"'-]+")
_ECHO = re.compile(r"echo(?:\s+[^;|&<>`()]*)?")
_VERSION_FLAG = re.compile(
    r"(?:php|node|npm|npx|yarn|pnpm|composer|python3?)\s+(?:-v|-V|--version)")
_LISTING = re.compile(r"(?:pwd|ls(?:\s+[\w./-]+)*)")
_CD = re.compile(r"cd\s+([^;|&<>`()\s]+)")
_COMPOSER_INSTALL = re.compile(r"\bcomposer\s+(?:install|i)\b")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
_NODE_MAJOR = re.compile(r"\d+")
_SSH_HOST = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):")
# kind -> words of a job name or a stage; the first word found in the name wins, then the stage.
_KIND_WORDS = (
    ("build", ("build", "compile", "prebuild", "assets")),
    ("test", ("test", "phpunit")),
    ("lint", ("lint", "quality", "qa", "check", "syntax", "cs", "fixer", "pint", "phpstan",
              "eslint")),
)
_NODE_LOCKFILES = (("package-lock.json", "npm ci", "npm ci"),
                   ("yarn.lock", "yarn install --frozen-lockfile", "yarn install"),
                   ("pnpm-lock.yaml", "pnpm install --frozen-lockfile", "pnpm install"))
_NODE_SCRIPTS = (("build", "build"), ("production", "build"), ("lint", "lint"), ("test", "test"))
_COMPOSER_SCRIPTS = (("phpstan", "lint"), ("phpcs", "lint"), ("test", "test"))
COMPOSER_INSTALL = "composer install --no-interaction --no-progress --no-scripts"
MAX_INSTALL = 500
MAX_INSTALL_PACKAGES = 10
MAX_PACKAGE_NAME = 214
MAX_VERSION = 64
_NPM_NAME = r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*"
_COMPOSER_NAME = r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*"
# A version or a range of one token: no space, no "/", ":", "*", comparison sign or URL.
_VERSION = r"[\^~]?[A-Za-z0-9][A-Za-z0-9._+-]*"
_NPM_PACKAGE = re.compile(rf"({_NPM_NAME})(?:@({_VERSION}))?")
_COMPOSER_PACKAGE = re.compile(rf"({_COMPOSER_NAME})(?::({_VERSION}))?")
_NODE_LOCKS = {"npm": "package-lock.json", "yarn": "yarn.lock", "pnpm": "pnpm-lock.yaml"}


class _Unreadable(Exception):
    """A job that cannot be analysed; the message is the reason given to the lead."""


def valid_image(image) -> bool:
    """True for an image reference the launcher accepts (cqa-toolbox.sh valid_image_ref)."""
    return (isinstance(image, str) and _IMAGE.fullmatch(image) is not None
            and not image.startswith("-") and ".." not in image)


def clean_dir(folder) -> Optional[str]:
    """Return a folder relative to the repo, normalised ("" for its root), or None when it
    leaves the repo or holds a character that does not suit a mount."""
    if not isinstance(folder, str) or any(char in folder for char in ":\\\0\n\r"):
        return None
    if folder.startswith("/"):
        return None
    cleaned = posixpath.normpath(folder or ".")
    if cleaned == ".":
        return ""
    if cleaned == ".." or cleaned.startswith("../"):
        return None
    return cleaned


def valid_command(item) -> bool:
    """True for a command of the shape above that the launcher can run."""
    if not isinstance(item, dict):
        return False
    texts = ("name", "dir", "image", "kind", "command", "source")
    env = item.get("env")
    return (
        all(isinstance(item.get(field), str) for field in texts)
        and item["kind"] in KINDS
        and item["name"].strip() != "" and item["command"].strip() != ""
        and "\0" not in item["command"]
        and valid_image(item["image"])
        and clean_dir(item["dir"]) == item["dir"]
        and isinstance(env, dict)
        and all(isinstance(key, str) and _NAME.fullmatch(key) and isinstance(value, str)
                and "\0" not in value for key, value in env.items())
        and isinstance(item.get("private"), bool)
    )


def allowed_install(command, manager_files) -> Optional[dict]:
    """Return what the orchestrator may run for the install a fixer asks for, or None.

    Only these forms, written with single spaces: npm install --save-dev|-D <p>[@v]..., npm
    install --save-exact <p>@<v>, npm install --package-lock-only, composer require --dev
    <vendor/p>[:v]..., composer update --lock, yarn add --dev <p>, pnpm add -D <p>. A package
    is a name of the public registry (no URL, path, git+ or scope other than @scope/), a version
    one token without space, "/", ":" or "*". Nothing else passes: no other option, no "--", no
    repeated option or package, no shell metacharacter, no capital outside a version, nothing
    but printable ASCII. manager_files are the names of the files of the folder: it must hold the
    manifest of the manager and no lock file of another node manager.

    The result is {"manager", "form" (dev, exact or lock), "packages" (as given, with their
    version), "names", "files" ([manifest, lock file]), "command"}. command is rebuilt from
    these parts, never the text given: the options of the manager are fixed here, and the
    scripts and plugins of what it installs are cut (--ignore-scripts, --no-scripts and
    --no-plugins).
    """
    if (not isinstance(command, str) or not 0 < len(command) <= MAX_INSTALL
            or not command.isascii() or not command.isprintable()):
        return None
    try:
        present = {name for name in manager_files if isinstance(name, str)}
    except TypeError:
        return None
    words = command.split(" ")
    if "" in words:
        return None
    parsed = _install_form(words)
    if parsed is None:
        return None
    manager, form, specs, argv = parsed
    names = []
    for spec in specs:
        found = (_COMPOSER_PACKAGE if manager == "composer" else _NPM_PACKAGE).fullmatch(spec)
        version = found.group(2) if found else None
        if (found is None or len(found.group(1)) > MAX_PACKAGE_NAME
                or len(version or "") > MAX_VERSION
                or (form == "exact" and version is None)
                or (manager in ("yarn", "pnpm") and version is not None)):
            return None
        names.append(found.group(1))
    if len(specs) > MAX_INSTALL_PACKAGES or len(set(names)) != len(names):
        return None
    manifest = "composer.json" if manager == "composer" else "package.json"
    lock = "composer.lock" if manager == "composer" else _NODE_LOCKS[manager]
    others = {name for each, name in _NODE_LOCKS.items() if each != manager}
    if manifest not in present or (manager != "composer" and others & present):
        return None
    return {"manager": manager, "form": form, "packages": specs, "names": names,
            "files": [manifest, lock], "command": shlex.join(argv + specs)}


def _install_form(words: list) -> Optional[tuple]:
    """Return (manager, form, package words, fixed words of the command rebuilt) for the words
    of an allowed form, else None. The packages are not checked here."""
    manager, rest = words[0], words[1:]
    if manager == "npm" and rest[:1] == ["install"]:
        rest = rest[1:]
        if rest == ["--package-lock-only"]:
            return manager, "lock", [], ["npm", "install", "--package-lock-only", "--ignore-scripts"]
        if len(rest) > 1 and rest[0] in ("--save-dev", "-D"):
            return (manager, "dev", rest[1:],
                    ["npm", "install", "--save-dev", "--ignore-scripts"])
        if len(rest) == 2 and rest[0] == "--save-exact":
            return (manager, "exact", rest[1:],
                    ["npm", "install", "--save-exact", "--ignore-scripts"])
    elif manager == "composer":
        options = ["--no-scripts", "--no-plugins", "--no-interaction"]
        if len(rest) > 2 and rest[:2] == ["require", "--dev"]:
            return manager, "dev", rest[2:], ["composer", "require", "--dev"] + options
        if rest == ["update", "--lock"]:
            return manager, "lock", [], ["composer", "update", "--lock"] + options
    elif manager == "yarn" and len(rest) == 3 and rest[:2] == ["add", "--dev"]:
        return manager, "dev", rest[2:], ["yarn", "add", "--dev", "--ignore-scripts"]
    elif manager == "pnpm" and len(rest) == 3 and rest[:2] == ["add", "-D"]:
        return manager, "dev", rest[2:], ["pnpm", "add", "--save-dev", "--ignore-scripts"]
    return None


def install_target(manager: str, repo_dir: Path, folder: str, commands: list) -> dict:
    """Return {"image", "private"} for an install of manager in folder of the repo: those of the
    install command the lead validated for that folder (its work folder, after a leading cd), else
    the image the manifests give (the Node of .nvmrc or engines, composer:2); private is true when
    the dependencies of the folder need a credential that is never passed to a container."""
    pattern = re.compile(rf"(?<![\w-]){re.escape(manager)}(?![\w-])")
    for item in commands:
        if (isinstance(item, dict) and item.get("kind") == "install"
                and _start_dir(item) == folder and pattern.search(str(item.get("command")))):
            return {"image": item["image"], "private": bool(item.get("private"))}
    repo = Path(repo_dir)
    if manager == "composer":
        document = _read_json(repo / folder / "composer.json")
        return {"image": COMPOSER_IMAGE, "private": _composer_private(document, _auth_ignored(repo))}
    document = _read_json(repo / folder / "package.json")
    image, _ = _node_image(repo, folder, document if isinstance(document, dict) else {}, "")
    return {"image": image, "private": False}


def detect(repo_dir: Path, ci: Optional[dict], skip: tuple = ()) -> dict:
    """Return {"commands": [...], "notes": [...]} for a repo: the commands of its CI (ci is the
    .gitlab-ci.yml converted to a dict, None without one), then those of the manifests of the
    folders the CI retained nothing in. skip lists folders of third-party code (third_party_paths)."""
    commands, notes = ([], []) if ci is None else from_ci(ci)
    _mark_private(Path(repo_dir), commands)
    found, more = from_manifests(Path(repo_dir), covered_dirs(commands), skip=skip)
    return {"commands": commands + found, "notes": notes + more}


def from_ci(ci: dict) -> tuple:
    """Return (commands, notes) of the retained jobs of a .gitlab-ci.yml, in the order of its
    stages, then of its jobs. A note says why each other visible job was left out."""
    if not isinstance(ci, dict):
        return [], [f"{CI_FILE} : un mapping YAML est attendu."]
    notes = []
    if "include" in ci:
        notes.append(f"{CI_FILE} : « include » ignoré, seuls les jobs du fichier sont lus.")
    jobs = {name: job for name, job in ci.items()
            if isinstance(name, str) and name not in _GLOBAL_KEYS and isinstance(job, dict)}
    visible = [name for name in jobs if not name.startswith(".")]
    kept, left_out = {}, {}
    for index, name in enumerate(visible):
        try:
            job = _resolve(name, jobs)
            if "script" not in job and "trigger" not in job:
                continue
            kept[name] = _plan(name, job, ci, index)
        except _Unreadable as error:
            left_out[name] = str(error)
    _propagate_needs(kept, left_out)
    for name in visible:
        if name in left_out:
            notes.append(f"{CI_FILE} : job « {name} » écarté ({left_out[name]}).")
    stages = _stage_order(ci)
    commands = []
    for name in sorted(kept, key=lambda each: (_stage_rank(kept[each]["stage"], stages),
                                              kept[each]["index"])):
        commands.extend(_ci_commands(name, kept[name]))
    return commands, notes


def start_dir(item: dict) -> str:
    """Return the folder a command works in: its dir, then its leading cd chain ("" is the root)."""
    return _start_dir(item)


def covered_dirs(commands: list) -> set:
    """Return the folders the commands of the CI work in, once their leading cd is followed."""
    return {_start_dir(item) for item in commands
            if isinstance(item.get("source"), str) and item["source"].startswith(CI_FILE)}


def from_manifests(repo_dir: Path, covered: set, skip: tuple = ()) -> tuple:
    """Return (commands, notes) of the package.json and composer.json found in the folders of
    the repo (four levels deep, never node_modules, vendor or .git) that are not covered."""
    repo = Path(repo_dir)
    commands, notes = [], []
    auth_ignored = _auth_ignored(repo)
    for folder in _folders(repo):
        if folder in covered or any(_under(folder, path) for path in skip):
            continue
        found, noted = _node_commands(repo, folder)
        commands.extend(found)
        notes.extend(noted)
        found, noted = _composer_commands(repo, folder, auth_ignored)
        commands.extend(found)
        notes.extend(noted)
    return commands, notes


def _resolve(name: str, jobs: dict, trail: tuple = ()) -> dict:
    job = jobs[name]
    extends = job.get("extends")
    if extends is None:
        parents = []
    elif isinstance(extends, str):
        parents = [extends]
    elif isinstance(extends, list):
        parents = extends
    else:
        raise _Unreadable("extends non analysable")
    merged = {}
    for parent in parents:
        if not isinstance(parent, str) or parent not in jobs:
            raise _Unreadable(f"extends vers « {parent} » introuvable dans ce fichier")
        if parent == name or parent in trail:
            raise _Unreadable("extends circulaire")
        merged = _merge(merged, _resolve(parent, jobs, trail + (name,)))
    return _merge(merged, {key: value for key, value in job.items() if key != "extends"})


def _merge(base: dict, over: dict) -> dict:
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _plan(name: str, job: dict, ci: dict, index: int) -> dict:
    """Return what a retained job needs to become commands; raise _Unreadable with the reason
    when the job must be left out."""
    stage = job.get("stage") if isinstance(job.get("stage"), str) else "test"
    word = _deployment_word(name, stage)
    if word:
        raise _Unreadable(f"nom ou étape de déploiement ou de publication (« {word} »)")
    default = ci.get("default") if isinstance(ci.get("default"), dict) else {}
    if "trigger" in job:
        raise _Unreadable("trigger : pipeline déclenché ailleurs")
    services = job["services"] if "services" in job else default.get(
        "services", ci.get("services"))
    if services:
        raise _Unreadable("services : conteneurs annexes non démarrés")
    before = job["before_script"] if "before_script" in job else default.get(
        "before_script", ci.get("before_script"))
    script = _entries(job.get("script"))
    if not script:
        raise _Unreadable("script vide")
    entries = _entries(before) + script
    text = "\n".join(entries)
    found = _FORBIDDEN.search(text)
    if found:
        raise _Unreadable(f"mot « {found.group(0).lower()} » dans le script")
    variables = _literals(_merge_variables(ci.get("variables"), job.get("variables")))
    for match in _VARIABLE.finditer(text):
        used = match.group(1) or match.group(2)
        if used != "CI_PROJECT_DIR" and used not in variables:
            raise _Unreadable(f"variable non littérale ${used}")
    image = _image(job, default, ci, variables)
    kind = _content_kind(_atoms(entries)) or _word_kind(name) or _word_kind(stage)
    if kind is None:
        raise _Unreadable("type de job non reconnu : ni installation, ni build, ni lint, ni test")
    return {"stage": stage, "index": index, "image": image, "kind": kind, "env": variables,
            "entries": entries, "needs": _needs(job)}


def _propagate_needs(kept: dict, left_out: dict) -> None:
    """Leave out the retained jobs that need a job left out, until nothing changes."""
    changed = True
    while changed:
        changed = False
        for name in list(kept):
            for dependency in kept[name]["needs"]:
                if dependency in left_out:
                    del kept[name]
                    left_out[name] = f"dépend du job écarté « {dependency} »"
                    changed = True
                    break


def _needs(job: dict) -> list:
    """Return the names of the jobs a job needs; raise _Unreadable for a job of another project."""
    listed = []
    for key in ("needs", "dependencies"):
        value = job.get(key)
        for item in value if isinstance(value, list) else []:
            if isinstance(item, str):
                listed.append(item)
            elif isinstance(item, dict) and "project" in item:
                raise _Unreadable("needs vers un autre projet")
            elif isinstance(item, dict) and isinstance(item.get("job"), str):
                listed.append(item["job"])
    return listed


def _deployment_word(name: str, stage: str) -> Optional[str]:
    for text in (name.lower(), stage.lower()):
        for word in _DEPLOY_WORDS:
            if word in text:
                return word
    return None


def _entries(value) -> list:
    """Return the entries of a script as text, each stripped; raise _Unreadable otherwise."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise _Unreadable("script non analysable")
    return [item.strip() for item in items if item.strip()]


def _merge_variables(first, second) -> dict:
    merged = {}
    for value in (first, second):
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _literals(variables: dict) -> dict:
    """Return the variables whose value is plain text: no $ reference, no line break."""
    literals = {}
    for key, value in variables.items():
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, bool):
            value = "true" if value else "false"
        if isinstance(value, (int, float)):
            value = str(value)
        if (isinstance(key, str) and _NAME.fullmatch(key) and isinstance(value, str)
                and not any(char in value for char in "$\n\0")):
            literals[key] = value
    return literals


def _image(job: dict, default: dict, ci: dict, variables: dict) -> str:
    image = job["image"] if "image" in job else default.get("image", ci.get("image"))
    if isinstance(image, dict):
        image = image.get("name")
    if not isinstance(image, str) or not image.strip():
        raise _Unreadable("pas d'image")
    image = image.strip()
    for match in _VARIABLE.finditer(image):
        used = match.group(1) or match.group(2)
        if used not in variables:
            raise _Unreadable(f"image non résolue : ${used}")
    image = _substitute(image, variables)
    if not valid_image(image):
        raise _Unreadable(f"image non valide : {image}")
    return image


def _substitute(text: str, values: dict) -> str:
    return _REFERENCE.sub(
        lambda match: values.get(match.group(1) or match.group(2), match.group(0)), text)


def _atoms(entries: list) -> list:
    """Split the entries on && when they are simple lines; a multi-line entry stays whole."""
    atoms = []
    for entry in entries:
        if "\n" in entry or "&&" not in entry or entry.count('"') % 2 or entry.count("'") % 2:
            atoms.append(entry)
        else:
            atoms.extend(part.strip() for part in entry.split("&&") if part.strip())
    return atoms


def _is_install(atom: str) -> bool:
    if _SHELL_META.search(atom):
        return False
    tokens = atom.split()
    if len(tokens) < 2 and tokens != ["yarn"]:
        return False
    manager, rest = tokens[0], tokens[1:]
    if manager == "yarn":
        rest = rest[1:] if rest[:1] == ["install"] else rest
        return all(token.startswith("-") and _ARGUMENT.fullmatch(token) for token in rest)
    installs = {"npm": ("ci", "install", "i"), "pnpm": ("install", "i"),
                "composer": ("install", "i")}
    return (rest[:1] != [] and rest[0] in installs.get(manager, ())
            and all(_ARGUMENT.fullmatch(token) for token in rest[1:]))


def _is_neutral(atom: str) -> bool:
    return any(pattern.fullmatch(atom) for pattern in (_ECHO, _VERSION_FLAG, _LISTING, _CD))


def _content_kind(atoms: list) -> Optional[str]:
    """Return "install" for a script that only installs (and prints versions, changes folder)."""
    if atoms and any(_is_install(atom) for atom in atoms) and all(
            _is_install(atom) or _is_neutral(atom) for atom in atoms):
        return "install"
    return None


def _word_kind(text: str) -> Optional[str]:
    tokens = [token for token in _NOT_ALNUM.split(_CAMEL.sub("-", text).lower()) if token]
    for token in tokens:
        for kind, words in _KIND_WORDS:
            for word in words:
                if token == word or (len(word) >= 4 and token in (
                        word + "s", word + "er", word + "ing")):
                    return kind
    return None


def _stage_order(ci: dict) -> list:
    stages = ci.get("stages")
    listed = [item for item in stages if isinstance(item, str)] if isinstance(stages, list) \
        else list(_DEFAULT_STAGES)
    return [".pre"] + listed + [".post"]


def _stage_rank(stage: str, order: list) -> int:
    return order.index(stage) if stage in order else len(order)


def _ci_commands(name: str, plan: dict) -> list:
    atoms = _atoms([_group(entry) for entry in plan["entries"]])

    def command(label: str, kind: str, parts: list) -> dict:
        return {"name": label, "dir": "", "image": plan["image"], "kind": kind,
                "command": _PROJECT_DIR.sub(WORKDIR, " && ".join(parts)),
                "env": dict(plan["env"]), "source": f"{CI_FILE} ({name})", "private": False}

    if plan["kind"] == "install":
        return [command(name, "install", atoms)]
    split = _split_install(atoms)
    if split is None:
        return [command(name, plan["kind"], atoms)]
    head, rest = split
    return [command(f"{name} (install)", "install", head), command(name, plan["kind"], rest)]


def _group(entry: str) -> str:
    """Wrap an entry whose comment or line breaks could swallow the command joined after it."""
    if "\n" in entry or _COMMENT.search(entry):
        return "{ " + entry + "\n}"
    return entry


def _split_install(atoms: list) -> Optional[tuple]:
    """Return (the atoms up to the install, the folder changes then the rest), or None when
    the script does not start with an install followed by something else."""
    index = 0
    while index < len(atoms) and _CD.fullmatch(atoms[index]):
        index += 1
    changes, last = atoms[:index], None
    position = index
    while position < len(atoms) and (_is_install(atoms[position]) or _is_neutral(atoms[position])):
        if _is_install(atoms[position]):
            last = position
        position += 1
    if last is None or last + 1 >= len(atoms):
        return None
    return atoms[:last + 1], changes + atoms[last + 1:]


def _start_dir(item: dict) -> str:
    current = clean_dir(item.get("dir") or "") or ""
    env = item.get("env") if isinstance(item.get("env"), dict) else {}
    for part in str(item.get("command", "")).split(" && "):
        match = _CD.fullmatch(part.strip())
        if not match:
            break
        target = _substitute(match.group(1).strip("\"'"), env)
        if "$" in target:
            break
        if target.startswith("/"):
            if target != WORKDIR and not target.startswith(WORKDIR + "/"):
                break
            target, current = posixpath.relpath(target, WORKDIR), ""
        moved = posixpath.normpath(posixpath.join(current, target))
        moved = "" if moved == "." else moved
        if moved == ".." or moved.startswith("../"):
            break
        current = moved
    return current


def _mark_private(repo: Path, commands: list) -> None:
    """A composer install of the CI needs private packages when the project says so."""
    auth_ignored = _auth_ignored(repo)
    for item in commands:
        if item["kind"] == "install" and _COMPOSER_INSTALL.search(item["command"]):
            document = _read_json(repo / _start_dir(item) / "composer.json")
            if _composer_private(document, auth_ignored):
                item["private"] = True


def _folders(repo: Path) -> list:
    """Return the folders of the repo as relative paths (the root is ""), parents first."""
    found = []
    for directory, names, _ in os.walk(repo):
        relative = os.path.relpath(directory, repo)
        folder = "" if relative == "." else relative.replace(os.sep, "/")
        found.append(folder)
        depth = 0 if not folder else folder.count("/") + 1
        names[:] = [] if depth >= MAX_DEPTH else sorted(
            name for name in names if name not in SKIPPED_DIRS)
    return found


def _under(folder: str, path: str) -> bool:
    return folder == path or folder.startswith(path.rstrip("/") + "/")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _node_commands(repo: Path, folder: str) -> tuple:
    manifest = repo / folder / "package.json"
    if not manifest.is_file():
        return [], []
    label = f"{folder}/package.json" if folder else "package.json"
    document = _read_json(manifest)
    if not isinstance(document, dict):
        return [], [f"{label} illisible : aucune commande proposée."]
    lockfile = next((entry for entry in _NODE_LOCKFILES if (manifest.parent / entry[0]).is_file()),
                    None)
    if lockfile is None:
        return [], [f"{label} sans lockfile : aucune commande proposée."]
    image, notes = _node_image(repo, folder, document, label)
    scripts = document.get("scripts") if isinstance(document.get("scripts"), dict) else {}
    suffix = f" ({folder})" if folder else ""
    commands = [_manifest_command(f"{lockfile[2]}{suffix}", folder, image, "install", lockfile[1],
                                  label)]
    for script, kind in _NODE_SCRIPTS:
        value = scripts.get(script)
        if isinstance(value, str) and value.strip() and "no test specified" not in value:
            commands.append(_manifest_command(f"npm run {script}{suffix}", folder, image, kind,
                                              f"npm run {script}", label))
    return commands, notes


def _node_image(repo: Path, folder: str, document: dict, label: str) -> tuple:
    candidates = []
    for source in (repo / folder / ".nvmrc", repo / ".nvmrc"):
        try:
            lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines()]
        except OSError:
            continue
        candidates.append(next((line for line in lines if line), ""))
    engines = document.get("engines")
    node = engines.get("node") if isinstance(engines, dict) else None
    candidates.append(node if isinstance(node, str) else "")
    for text in candidates:
        found = _NODE_MAJOR.search(text)
        if found and 4 <= int(found.group(0)) <= 40:
            return f"node:{int(found.group(0))}-alpine", []
    return DEFAULT_NODE, [
        f"{label} : version de Node introuvable (.nvmrc, engines.node), {DEFAULT_NODE} par défaut."]


def _composer_commands(repo: Path, folder: str, auth_ignored: bool) -> tuple:
    manifest = repo / folder / "composer.json"
    if not manifest.is_file():
        return [], []
    label = f"{folder}/composer.json" if folder else "composer.json"
    document = _read_json(manifest)
    if not isinstance(document, dict):
        return [], [f"{label} illisible : aucune commande proposée."]
    private = _composer_private(document, auth_ignored)
    scripts = document.get("scripts") if isinstance(document.get("scripts"), dict) else {}
    suffix = f" ({folder})" if folder else ""
    commands = [_manifest_command(f"composer install{suffix}", folder, COMPOSER_IMAGE, "install",
                                  COMPOSER_INSTALL, label, private)]
    for script, kind in _COMPOSER_SCRIPTS:
        if script in scripts:
            commands.append(_manifest_command(f"composer {script}{suffix}", folder, COMPOSER_IMAGE,
                                              kind, f"composer {script}", label, private))
    return commands, []


def _manifest_command(name, folder, image, kind, command, source, private=False) -> dict:
    return {"name": name, "dir": folder, "image": image, "kind": kind, "command": command,
            "env": {}, "source": source, "private": private}


def _composer_private(document, auth_ignored: bool) -> bool:
    """True when the packages cannot all come from packagist without a credential."""
    if auth_ignored:
        return True
    repositories = document.get("repositories") if isinstance(document, dict) else None
    if isinstance(repositories, dict):
        repositories = list(repositories.values())
    for entry in repositories if isinstance(repositories, list) else []:
        for url in _urls(entry):
            host = _host(url)
            if host and host != "packagist.org" and not host.endswith(".packagist.org"):
                return True
    return False


def _urls(value) -> list:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_urls(item))
    return found


def _host(url: str) -> Optional[str]:
    if "://" in url:
        try:
            return (urlsplit(url).hostname or "").lower() or None
        except ValueError:
            return None
    match = _SSH_HOST.match(url)
    return match.group(1).lower() if match else None


def _auth_ignored(repo: Path) -> bool:
    """True when the .gitignore at the root of the repo ignores auth.json."""
    try:
        text = (repo / ".gitignore").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    ignored = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = (line[1:] if negated else line).lstrip("/")
        if pattern.startswith("**/"):
            pattern = pattern[3:]
        if fnmatch.fnmatchcase("auth.json", pattern):
            ignored = not negated
    return ignored
