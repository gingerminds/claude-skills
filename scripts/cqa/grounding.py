"""What the evidence of a finding claims that the run holds: files, tools, pages, probes, facts, documents.

validate_document checks the shape of a finding; Grounding checks the evidence of a valid one
against the run directory (and the repos of context.json) and says in French what the run does not
hold. Nothing here is written: cqa.rerun freezes the result at validation (cqa.checked).

A problem either sets the finding aside (a cited file or a tool summary that is missing, a document
that was never imported: the checks of 0.18.0, and the document) or drops only the piece of
evidence it is about (a page or an http URL off the target, an http URL no probe requested, a tool
whose result is not ok: the checks of 0.18.1; a fact of the server that facts/server.json does not
hold for the row: the check of 0.18.2). A dropped piece of evidence leaves the finding to the
guardrails with the evidence left: an ok without evidence becomes ok_human, an na unknown, a ko or
a partial stays. A problem of a tool, of a probe or of a fact (a tool that had not finished, a probe
whose tool had not run yet, a fact not noted yet) is marked retry: plan checks that piece of
evidence again (cqa.rerun).
"""
import json
import re
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from cqa import context_docs
from cqa.checklist import ref_label
from cqa.probes import target_hosts, url_host

GOOD_TOOL_RESULTS = ("ok", "not_applicable")
# The probe lists the plugin hands to the toolbox, with the tool that answers each one.
_PROBE_FILES = (("http", "probes.json"), ("http-followup", "probes-followup.json"))
_EVIDENCE_TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_FACTS_FILE = ("facts", "server.json")


class Problem(NamedTuple):
    """What the run does not hold for one piece of evidence (number counts from 1)."""

    number: int
    kind: str
    detail: str
    drops: bool
    label: str = ""
    retry: bool = False

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
        self.page_hosts = target_hosts(context, pages=True)
        self._results: dict = {}
        self._probes: Optional[set] = None
        self._facts: Optional[tuple] = None

    def check(self, evidence: list, ref: Optional[int] = None) -> list:
        """Return the Problems of the evidence of a valid finding, in the order of the evidence.

        ref is the row of the finding: a fact of the server must cover it (without it, no fact holds).
        """
        found = []
        for number, item in enumerate(evidence, start=1):
            found.extend(self.check_one(number, item, ref))
        return found

    def check_one(self, number: int, item: dict, ref: Optional[int] = None) -> list:
        """Return the Problems of one piece of evidence (at most one)."""
        kind = item["type"]
        if kind == "fact":
            result = self._fact_problem(item, ref)
        else:
            result = getattr(self, f"_{kind}_problem")(item)
        if result is None:
            return []
        return [Problem(number, kind, *result)]

    def problems(self, evidence: list, ref: Optional[int] = None) -> list:
        """Return the French texts of every problem of the evidence."""
        return [problem.text for problem in self.check(evidence, ref)]

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
                return _aside(f"{failure}, et tools/{tool}/summary.json est introuvable", retry=True)
            return _aside(
                f"outil {_quoted(tool)} sans résumé (tools/{tool}/summary.json est introuvable)",
                retry=True,
            )
        if not self.strict:
            return None
        if failed:
            return _dropped(
                f"{failure}, une preuve exige un résultat ok ou not_applicable",
                f"résultat {_quoted(result.get('status'))} de l'outil",
                retry=True,
            )
        if result is None:
            return _dropped(
                f"outil {_quoted(tool)} sans résultat lisible "
                f"(tools/{tool}/result.json est introuvable ou illisible)",
                "résultat de l'outil illisible",
                retry=True,
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
        return _dropped(
            f"aucune sonde de la boîte à outils ne correspond à l'URL {_quoted(_shown(url))} "
            "(hôte, chemin et paramètres, probes.json ou probes-followup.json d'un outil http "
            "ou http-followup en résultat ok) : une requête faite à la main se dit dans « summary »",
            "aucune sonde de la boîte à outils",
            retry=True,
        )

    def _document_problem(self, item: dict) -> Optional[tuple]:
        if not self.strict:
            return None
        doc = item["doc"]
        if context_docs.read_items(self.run_dir, doc) is None:
            return _aside(f"document {_quoted(doc)} introuvable dans context-docs/ du run (document importé)")
        return None

    def _fact_problem(self, item: dict, ref: Optional[int]) -> Optional[tuple]:
        """Drop a fact that facts/server.json does not note for the row with a command that exited 0.

        The sub-agent writes the file: the check is of form (the fact exists, covers the row, notes
        a command that exited with 0 and a result), never of truth.
        """
        if not self.strict:
            return None
        topic = item["topic"]
        shown = _quoted(topic)
        if ref is None:
            return _dropped(
                f"fait {shown} : la ligne du constat n'est pas connue du contrôle",
                "ligne du constat inconnue",
            )
        facts, problem = self._server_facts()
        if problem is not None:
            return _dropped(*problem, retry=True)
        found = [fact for fact in facts if isinstance(fact, dict) and fact.get("topic") == topic]
        if not found:
            return _dropped(
                f"aucun fait du sujet {shown} dans facts/server.json "
                "(le « topic » doit être le même, à l'identique)",
                "aucun fait pour ce sujet",
                retry=True,
            )
        found = [fact for fact in found if _covers(fact.get("refs"), ref)]
        if not found:
            return _dropped(
                f"aucun fait du sujet {shown} ne couvre la ligne {ref_label(ref)} "
                "(« refs » de facts/server.json)",
                "fait qui ne couvre pas la ligne",
                retry=True,
            )
        found = [fact for fact in found if _is_zero(fact.get("exit"))]
        if not found:
            return _dropped(
                f"le fait du sujet {shown} n'a pas un « exit » égal à 0 "
                "(entier attendu) : la commande a échoué ou son code n'est pas noté",
                "commande en échec (exit différent de 0)",
                retry=True,
            )
        if not any(_filled(fact.get("command")) and _filled(fact.get("result")) for fact in found):
            return _dropped(
                f"le fait du sujet {shown} n'a pas à la fois une commande (« command ») "
                "et un résultat (« result ») non vides",
                "commande ou résultat vide",
                retry=True,
            )
        return None

    def _origin_problem(self, url: str, hosts: list) -> Optional[tuple]:
        host = url_host(url)
        shown = _quoted(_shown(url))
        if host is None:
            return _dropped(f"URL {shown} illisible : aucun hôte", "URL illisible")
        if not hosts:
            return _dropped(
                f"URL {shown} : context.json ne donne aucun hôte de cible "
                "(target.preprod_url ou target.prod_url)",
                "aucun hôte de cible",
            )
        if host not in hosts:
            return _dropped(
                f"URL {shown} sur l'hôte « {host} », hors de la cible "
                f"(hôtes attendus : {', '.join(hosts)})",
                "hôte hors de la cible",
            )
        return None

    def _result(self, tool: str) -> Optional[dict]:
        if tool not in self._results:
            self._results[tool] = _read_object(self.run_dir / "tools" / tool / "result.json") or None
        return self._results[tool]

    def _server_facts(self) -> tuple:
        """Return (facts, None) of facts/server.json, or (None, (detail, label)) when it holds none."""
        if self._facts is None:
            self._facts = self._read_facts()
        return self._facts

    def _read_facts(self) -> tuple:
        name = "/".join(_FACTS_FILE)
        path = self.run_dir.joinpath(*_FACTS_FILE)
        if not path.is_file():
            return None, (
                f"{name} est introuvable dans le run : noter d'abord le fait "
                "(lignes, sujet, commande, exit, résultat), puis le citer",
                f"{name} introuvable",
            )
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return None, (f"{name} est illisible (JSON invalide)", f"{name} illisible")
        facts = document.get("facts") if isinstance(document, dict) else None
        if not isinstance(facts, list):
            return None, (
                f"{name} doit être un objet avec une liste « facts »",
                f"{name} mal formé",
            )
        return facts, None

    def _probe_keys(self) -> set:
        """Return the (host, path, query) of the probes that a toolbox tool answered."""
        if self._probes is None:
            self._probes = set()
            for tool, name in _PROBE_FILES:
                result = self._result(tool)
                if result is not None and result.get("status") == "ok":
                    self._probes |= _probe_keys(_read_object(self.run_dir / name))
        return self._probes


def _aside(detail: str, *, retry: bool = False) -> tuple:
    """A problem that sets the whole finding aside: (detail, drops, label, retry) of a Problem."""
    return detail, False, "", retry


def _dropped(detail: str, label: str, *, retry: bool = False) -> tuple:
    """A problem that drops only its piece of evidence, label being its short French reason."""
    return detail, True, label, retry


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


def _covers(refs, ref: int) -> bool:
    """Whether the refs of a fact (a list of integers) hold the row."""
    return isinstance(refs, list) and any(
        isinstance(item, int) and not isinstance(item, bool) and item == ref for item in refs
    )


def _is_zero(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _filled(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
