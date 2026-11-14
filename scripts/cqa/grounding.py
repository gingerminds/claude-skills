"""What the evidence of a finding claims that the run holds: files, tools, pages, probes, documents.

validate_document checks the shape of a finding; Grounding checks the evidence of a valid one
against the run directory (and the repos of context.json) and says in French what the run does not
hold. Nothing here is written: cqa.rerun freezes the result at validation (cqa.checked).

A problem either sets the finding aside (a cited file or a tool summary that is missing, a document
that was never imported: the checks of 0.18.0, and the document) or drops only the piece of
evidence it is about (a page or an http URL off the target, an http URL no probe requested, a tool
whose result is not ok: the checks of 0.18.1). A dropped piece of evidence leaves the finding to
the guardrails with the evidence left: an ok without evidence becomes ok_human, an na unknown, a ko
or a partial stays.
"""
import json
import re
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from cqa import context_docs
from cqa.probes import target_hosts, url_host

GOOD_TOOL_RESULTS = ("ok", "not_applicable")
# The probe lists the plugin hands to the toolbox, with the tool that answers each one.
_PROBE_FILES = (("http", "probes.json"), ("http-followup", "probes-followup.json"))
_EVIDENCE_TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class Problem(NamedTuple):
    """What the run does not hold for one piece of evidence (number counts from 1)."""

    number: int
    kind: str
    detail: str
    drops: bool
    label: str = ""

    @property
    def text(self) -> str:
        """The French text of the problem, as validate reports it after « constat CQ-<ref> : »."""
        verb = " écartée" if self.drops else ""
        return f"preuve n° {self.number} ({self.kind}){verb} : {self.detail}"


class Grounding:
    """The checks of the evidence of one findings file, against one run.

    strict false applies the checks of 0.18.0 only (a cited file exists in its repo, a cited tool
    has its summary): the rules of a findings file of a run started before 0.18.1 (cqa.contract).
    """

    def __init__(self, run_dir: Path, context: dict, roots: dict, *, strict: bool = True):
        self.run_dir = Path(run_dir)
        self.roots = roots
        self.strict = strict
        context = context if isinstance(context, dict) else {}
        self.hosts = target_hosts(context)
        self.page_hosts = self.hosts
        self._results: dict = {}
        self._probes: Optional[set] = None

    def check(self, evidence: list) -> list:
        """Return the Problems of the evidence of a valid finding, in the order of the evidence."""
        found = []
        for number, item in enumerate(evidence, start=1):
            found.extend(self.check_one(number, item))
        return found

    def check_one(self, number: int, item: dict) -> list:
        """Return the Problems of one piece of evidence (at most one)."""
        kind = item["type"]
        result = getattr(self, f"_{kind}_problem")(item)
        if result is None:
            return []
        detail, drops, label = result
        return [Problem(number, kind, detail, drops, label)]

    def problems(self, evidence: list) -> list:
        """Return the French texts of every problem of the evidence."""
        return [problem.text for problem in self.check(evidence)]

    def _file_problem(self, item: dict) -> Optional[tuple]:
        repo, path = item["repo"], item["path"]
        if repo not in self.roots:
            return _aside(f"repo {_quoted(repo)} absent de context.json (repos : {', '.join(self.roots)})")
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            return _aside(f"chemin {_quoted(path)} hors du repo {_quoted(repo)}")
        if relative.parts == ():
            return _aside(f"chemin {_quoted(path)} : la racine du repo {_quoted(repo)} n'est pas une preuve")
        candidate = self.roots[repo] / relative
        if not candidate.exists():
            return _aside(f"chemin {_quoted(path)} introuvable dans le repo {_quoted(repo)}")
        if not candidate.resolve().is_relative_to(self.roots[repo].resolve()):
            return _aside(f"chemin {_quoted(path)} hors du repo {_quoted(repo)}")
        if not candidate.is_file():
            return _aside(f"chemin {_quoted(path)} : pas un fichier dans le repo {_quoted(repo)} (dossier ?)")
        return None

    def _tool_problem(self, item: dict) -> Optional[tuple]:
        tool = item["tool"]
        if not _EVIDENCE_TOOL.fullmatch(tool):
            return _aside(f"nom d'outil {_quoted(tool)} invalide")
        result = self._result(tool) if self.strict else None
        failed = result is not None and result.get("status") not in GOOD_TOOL_RESULTS
        failure = (
            f"outil {_quoted(tool)} : son résultat est {_quoted(result.get('status'))}"
            f"{_reason(result)} dans tools/{tool}/result.json"
            if failed
            else ""
        )
        if not (self.run_dir / "tools" / tool / "summary.json").is_file():
            if failed:
                return _aside(f"{failure}, et tools/{tool}/summary.json est introuvable")
            return _aside(f"outil {_quoted(tool)} sans résumé (tools/{tool}/summary.json est introuvable)")
        if not self.strict:
            return None
        if failed:
            return (
                f"{failure}, une preuve exige un résultat ok ou not_applicable",
                True,
                f"résultat {_quoted(result.get('status'))} de l'outil",
            )
        if result is None:
            return (
                f"outil {_quoted(tool)} sans résultat lisible "
                f"(tools/{tool}/result.json est introuvable ou illisible)",
                True,
                "résultat de l'outil illisible",
            )
        return None

    def _page_problem(self, item: dict) -> Optional[tuple]:
        if not self.strict:
            return None
        return self._origin_problem(item["url"], self.page_hosts)

    def _http_problem(self, item: dict) -> Optional[tuple]:
        if not self.strict:
            return None
        url = item["url"]
        problem = self._origin_problem(url, self.hosts)
        if problem or _key(url) in self._probe_keys():
            return problem
        return (
            f"aucune sonde de la boîte à outils ne correspond à l'URL {_quoted(_shown(url))} "
            "(hôte, chemin et paramètres, probes.json ou probes-followup.json d'un outil http "
            "ou http-followup en résultat ok) : une requête faite à la main se dit dans « summary »",
            True,
            "aucune sonde de la boîte à outils",
        )

    def _document_problem(self, item: dict) -> Optional[tuple]:
        if not self.strict:
            return None
        doc = item["doc"]
        if context_docs.read_items(self.run_dir, doc) is None:
            return _aside(f"document {_quoted(doc)} introuvable dans context-docs/ du run (document importé)")
        return None

    def _origin_problem(self, url: str, hosts: list) -> Optional[tuple]:
        host = url_host(url)
        shown = _quoted(_shown(url))
        if host is None:
            return f"URL {shown} illisible : aucun hôte", True, "URL illisible"
        if not hosts:
            return (
                f"URL {shown} : context.json ne donne aucun hôte de cible "
                "(target.preprod_url ou target.prod_url)",
                True,
                "aucun hôte de cible",
            )
        if host not in hosts:
            return (
                f"URL {shown} sur l'hôte « {host} », hors de la cible "
                f"(hôtes attendus : {', '.join(hosts)})",
                True,
                "hôte hors de la cible",
            )
        return None

    def _result(self, tool: str) -> Optional[dict]:
        if tool not in self._results:
            self._results[tool] = _read_object(self.run_dir / "tools" / tool / "result.json") or None
        return self._results[tool]

    def _probe_keys(self) -> set:
        """Return the (host, path, query) of the probes that a toolbox tool answered."""
        if self._probes is None:
            self._probes = set()
            for tool, name in _PROBE_FILES:
                result = self._result(tool)
                if result is not None and result.get("status") == "ok":
                    self._probes |= _probe_keys(_read_object(self.run_dir / name))
        return self._probes


def _aside(detail: str) -> tuple:
    """A problem that sets the whole finding aside."""
    return detail, False, ""


def _probe_keys(document: dict) -> set:
    base = document.get("base_url")
    probes = document.get("probes")
    if not isinstance(base, str) or not isinstance(probes, list):
        return set()
    try:
        parts = urlsplit(base)
    except ValueError:
        return set()
    origin = f"{parts.scheme}://{parts.netloc}"
    keys = set()
    for probe in probes:
        path = probe.get("path") if isinstance(probe, dict) else None
        if not isinstance(path, str):
            continue
        key = _key(path if path.startswith(("http://", "https://")) else origin + path)
        if key:
            keys.add(key)
    return keys


def _key(url) -> Optional[tuple]:
    """Return (host, path, query) of a URL: the scheme, the port and the fragment do not count."""
    host = url_host(url)
    if host is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return host, parts.path or "/", parts.query


def _read_object(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return document if isinstance(document, dict) else {}


def _reason(result: dict) -> str:
    reason = result.get("reason")
    return f" ({_quoted(reason)})" if isinstance(reason, str) and reason else ""


def _shown(url: str) -> str:
    """Return the URL without its credentials, its query and its fragment, which may hold a secret."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme and host:
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{host}{port}{parts.path}"
    except ValueError:
        pass
    return url.partition("?")[0].partition("#")[0].rpartition("@")[2]


def _quoted(value) -> str:
    text = str(value)
    return f"« {text if len(text) <= 60 else text[:59] + '…'} »"
