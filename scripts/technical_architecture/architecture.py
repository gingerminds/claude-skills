#!/usr/bin/env python3
"""Tooling for the technical architecture documentation of a project.

The documentation lives in one folder (docs/architecture/ by default) holding
README.md, views/*.drawio.svg and tools/ (this script, dossier.css and
architecture.json). Each view is an SVG image that embeds its draw.io diagram.
This script extracts that diagram, re-renders the images with draw.io headless
(Docker), checks that views and flow matrix stay consistent and builds an
on-demand PDF dossier in build/ (never versioned).

Usage: python3 architecture.py [--racine <folder>] extract <view.drawio.svg> <out.drawio>
       python3 architecture.py [--racine <folder>] render <source.drawio> <view.drawio.svg> [--png <preview.png>]
       python3 architecture.py [--racine <folder>] refresh
       python3 architecture.py [--racine <folder>] check
       python3 architecture.py [--racine <folder>] pdf [--sans-points-attention] [--sortie <file.pdf>]
       python3 architecture.py version
       <command> | python3 architecture.py mask

--racine defaults to the folder above tools/, where this script is installed.
"""

import argparse
import base64
import binascii
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

TOOL_VERSION = "1.0.0"
CONFIG_NAME = "architecture.json"
ROLES = ("hebergeur", "agence", "client", "tiers")
PDF_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.pdf$")
PREFIX_RE = re.compile(r"^[A-Z]$")

DRAWIO_IMAGE = "rlespinasse/drawio-desktop-headless:v1.78.0"
PANDOC_IMAGE = "pandoc/core:3.14.0-alpine"
CHROME_IMAGE = "zenika/alpine-chrome:124"
# README sections appended to the PDF after the views; the last one can be left out.
ANNEX_SECTIONS = ("Légende", "Matrice des flux", "Points d'attention connus")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
VIEW_INDEX_RE = re.compile(r"^\| \[([^\]]+)\]\(views/([^)]+)\) \| ([^|]+) \|", re.M)

CARTOUCHE_RE = re.compile(r"État au \d{2}/\d{2}/\d{4}")
STATUS_RE = re.compile(
    r"^(constaté \(\d{2}/\d{2}/\d{4}\)|décidé|à confirmer|indiqué par .+ \(\d{2}/\d{2}/\d{4}\))$"
)
SECRET_RE = re.compile(
    r"(?i)(mot de passe|password|passwd|secret|token|api[_-]?key)[\"']?\s*[:=]\s*\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
)
EM_DASH = "\u2014"
MATRIX_HEADING = "## Matrice des flux"
# An <image> whose href is not a data: URI breaks once the SVG leaves draw.io.
EXTERNAL_IMAGE_RE = re.compile(r'<image\b[^>]*?\bhref="(?!data:)([^"]*)"')
MASK = "[masqué]"
# Values after a credential-like key, URL userinfo passwords, mysql -p<password>.
QUOTED_OR_WORD = r"(?:\"[^\"]*\"|'[^']*'|\S+)"
SECRET_VALUE_RE = re.compile(
    r"(?i)(\b[\w.-]*(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|authorization|credentials?|dsn"
    r"|(?:basic|digest)auth\.users)[\w.-]*[\"']?\s*[=:]\s*)((?:Bearer|Basic)\s+)?" + QUOTED_OR_WORD
)
# The password of a URL stops at the path, the query or the fragment.
URL_PASSWORD_RE = re.compile(r"(://[^/\s:@]*:)[^@\s/?#]+@")
MYSQL_CLIENT_RE = re.compile(r"\b(?:mysql|mysqldump|mysqladmin|mariadb|mariadb-dump)\b")
MYSQL_PASSWORD_RE = re.compile(r"(?<!\S)-p(?=\S)" + QUOTED_OR_WORD)
HTML_STYLE_RE = re.compile(r"(?:^|;)html=1(?:;|$)")
UNREADABLE_VIEW = (
    "aucun diagramme embarqué ou diagramme illisible (enregistrer en .drawio.svg avec une copie du diagramme)"
)
VIEW_ERRORS = (ValueError, ET.ParseError, zlib.error, binascii.Error, UnicodeDecodeError)


class ConfigError(Exception):
    """Invalid or missing project configuration; the message is shown to the user."""


@dataclass(frozen=True)
class Config:
    """Project settings read from tools/architecture.json."""

    root: Path
    title: str
    pdf_name: str
    flow_prefixes: Tuple[Tuple[str, str], ...]
    owners: Dict[str, str]
    known_issues: bool
    repositories: Tuple[str, ...]
    tool_version: str

    @property
    def views_dir(self) -> Path:
        return self.root / "views"

    @property
    def readme(self) -> Path:
        return self.root / "README.md"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def css(self) -> Path:
        return self.root / "tools" / "dossier.css"

    @property
    def flow_id_re(self) -> Pattern[str]:
        letters = "".join(letter for letter, _ in self.flow_prefixes)
        # Never glued to a host name: srv-P01, web.P01 and P01-backup are not flow IDs.
        return re.compile(r"(?<![\w.-])[" + letters + r"]\d{2}(?![\w-])")

    def pdf_path(self, with_known_issues: bool) -> Path:
        """Where the dossier is written: always under build/."""
        if with_known_issues:
            return self.build_dir / self.pdf_name
        return self.build_dir / f"{Path(self.pdf_name).stem}-sans-points-attention.pdf"


def _required_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{CONFIG_NAME} : « {key} » manquant")
    return value.strip()


def load_config(root: Path) -> Config:
    """Read and validate root/tools/architecture.json."""
    path = root / "tools" / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{CONFIG_NAME} introuvable dans {root / 'tools'}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ConfigError(f"{CONFIG_NAME} : JSON invalide ({error})") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_NAME} : JSON invalide (objet attendu)")

    title = _required_text(data, "title")
    tool_version = _required_text(data, "tool_version")
    pdf_name = data.get("pdf")
    if not isinstance(pdf_name, str) or not PDF_NAME_RE.match(pdf_name):
        raise ConfigError(f"{CONFIG_NAME} : « pdf » doit être un nom de fichier en .pdf")

    prefixes = data.get("flow_prefixes")
    if (
        not isinstance(prefixes, dict)
        or not prefixes
        or not all(isinstance(key, str) and PREFIX_RE.match(key) for key in prefixes)
        or not all(isinstance(name, str) and name.strip() for name in prefixes.values())
    ):
        raise ConfigError(f"{CONFIG_NAME} : « flow_prefixes » attend des lettres majuscules uniques")

    owners = data.get("owners") or {}
    if not isinstance(owners, dict):
        raise ConfigError(f"{CONFIG_NAME} : « owners » attend un objet rôle → nom")
    for role, name in owners.items():
        if role not in ROLES:
            raise ConfigError(f"{CONFIG_NAME} : rôle inconnu « {role} » dans « owners »")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{CONFIG_NAME} : nom manquant pour le rôle « {role} » dans « owners »")

    known_issues = data.get("known_issues", True)
    if not isinstance(known_issues, bool):
        raise ConfigError(f"{CONFIG_NAME} : « known_issues » attend true ou false")
    repositories = data.get("repositories") or []
    if not isinstance(repositories, list) or not all(isinstance(item, str) for item in repositories):
        raise ConfigError(f"{CONFIG_NAME} : « repositories » attend une liste de chemins")

    return Config(
        root=root,
        title=title,
        pdf_name=pdf_name,
        flow_prefixes=tuple((letter, name.strip()) for letter, name in prefixes.items()),
        owners={role: name.strip() for role, name in owners.items()},
        known_issues=known_issues,
        repositories=tuple(repositories),
        tool_version=tool_version,
    )


def default_root() -> Path:
    """The documentation folder: the tool is installed in <root>/tools/."""
    return Path(__file__).resolve().parents[1]


def _decode_diagram(payload: str) -> ET.Element:
    """Decode a compressed draw.io diagram (base64, raw deflate, URL encoding)."""
    raw = zlib.decompress(base64.b64decode(payload), -15)
    return ET.fromstring(urllib.parse.unquote(raw.decode("utf-8")))


def extract_diagram(view: Path) -> str:
    """Return the uncompressed <mxfile> XML embedded in a .drawio.svg file.

    Raises ValueError when the SVG carries no embedded diagram.
    """
    content = ET.parse(view).getroot().get("content")
    if not content:
        raise ValueError("aucun diagramme embarqué")
    mxfile = ET.fromstring(content)
    for diagram in mxfile.iter("diagram"):
        if diagram.find("mxGraphModel") is None and (diagram.text or "").strip():
            graph = _decode_diagram(diagram.text.strip())
            diagram.text = None
            diagram.append(graph)
    return ET.tostring(mxfile, encoding="unicode")


def _is_html(element: ET.Element) -> bool:
    """Whether a cell label is HTML (html=1 in its style, or in the style of a wrapped mxCell)."""
    style = element.get("style")
    if style is None:
        child = element.find("mxCell")
        style = child.get("style") if child is not None else None
    return bool(HTML_STYLE_RE.search(style or ""))


def view_text(mxfile_xml: str) -> str:
    """Return the visible text of every cell: plain labels as written, HTML labels stripped to one line."""
    labels = []
    for element in ET.fromstring(mxfile_xml).iter():
        for attribute in ("value", "label"):
            value = element.get(attribute)
            if not value:
                continue
            if _is_html(element):
                text = html.unescape(re.sub(r"<[^>]+>", " ", value)).replace("\xa0", " ")
                labels.append(re.sub(r"\s+", " ", text).strip())
            else:
                labels.append(value.replace("\xa0", " ").strip())
    return "\n".join(label for label in labels if label)


def _cells(line: str) -> List[str]:
    """Split a Markdown table line into stripped cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _matrix_table_rows(readme_text: str) -> List[Tuple[List[str], List[str]]]:
    """(header, cells) for every body row of the tables under the flow matrix heading."""
    rows: List[Tuple[List[str], List[str]]] = []
    in_matrix = False
    header: List[str] = []
    lines = readme_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## "):
            in_matrix = line.strip() == MATRIX_HEADING
            header = []
            continue
        if not in_matrix or not line.lstrip().startswith("|"):
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if re.match(r"^\s*\|\s*:?-{3,}", next_line):
            header = _cells(line)
            continue
        cells = _cells(line)
        if header and cells and not re.match(r"^:?-{3,}", cells[0]):
            rows.append((header, cells))
    return rows


def _id_rows(readme_text: str) -> List[Tuple[str, Dict[str, str]]]:
    """(first cell, row) for every matrix row with a non-empty first cell."""
    return [(cells[0], dict(zip(header, cells))) for header, cells in _matrix_table_rows(readme_text) if cells[0]]


def matrix_rows(readme_text: str, flow_id_re: Pattern[str]) -> List[Dict[str, str]]:
    """Return the flow rows of the tables under the flow matrix heading."""
    return [row for first, row in _id_rows(readme_text) if flow_id_re.fullmatch(first)]


def _text_errors(name: str, text: str) -> List[str]:
    """Errors shared by views and README: secrets and em dashes."""
    errors = []
    if SECRET_RE.search(text):
        errors.append(f"{name} : secret possible (mot de passe, jeton ou clé), à retirer")
    if EM_DASH in text:
        errors.append(f"{name} : tiret long interdit, le remplacer")
    return errors


def list_views(config: Config) -> List[Path]:
    """The views of the project, in file name order."""
    return sorted(config.views_dir.glob("*.drawio.svg")) if config.views_dir.is_dir() else []


def check(config: Config) -> List[str]:
    """Check views and flow matrix; return French error messages (empty when valid)."""
    errors: List[str] = []
    if config.tool_version != TOOL_VERSION:
        errors.append(
            f"{CONFIG_NAME} : tool_version {config.tool_version} ne correspond pas à l'outil {TOOL_VERSION}"
        )
    flow_id_re = config.flow_id_re
    view_ids: Dict[str, str] = {}
    views = list_views(config)
    if not views:
        errors.append("views : aucune vue (*.drawio.svg)")
    for view in views:
        try:
            diagram_xml = extract_diagram(view)
            pages = ET.fromstring(diagram_xml).findall("diagram")
            # draw.io exports the first page only: the other ones would be checked but never shown.
            text = view_text(ET.tostring(pages[0], encoding="unicode") if pages else diagram_xml)
            raw = view.read_text(encoding="utf-8")
        except VIEW_ERRORS:
            errors.append(f"{view.name} : {UNREADABLE_VIEW}")
            continue
        if len(pages) > 1:
            errors.append(f"{view.name} : plusieurs pages (une vue = une page)")
        if not CARTOUCHE_RE.search(text):
            errors.append(f"{view.name} : cartouche sans « État au JJ/MM/AAAA »")
        if "Légende" not in text:
            errors.append(f"{view.name} : « Légende » absente")
        for href in sorted(set(EXTERNAL_IMAGE_RE.findall(raw))):
            errors.append(f"{view.name} : image non embarquée ({href}), réenregistrer en incorporant les images")
        errors += _text_errors(view.name, text)
        for flow_id in flow_id_re.findall(text):
            view_ids.setdefault(flow_id, view.name)

    if not config.readme.is_file():
        return errors + [f"{config.readme.name} : introuvable"]
    readme_text = config.readme.read_text(encoding="utf-8")
    errors += _text_errors(config.readme.name, readme_text)
    letters = ", ".join(letter for letter, _ in config.flow_prefixes)
    matrix_ids = set()
    duplicates = set()
    for first, row in _id_rows(readme_text):
        if not flow_id_re.fullmatch(first):
            errors.append(f"{first} : identifiant invalide (une lettre parmi {letters} puis deux chiffres)")
            continue
        if first in matrix_ids:
            if first not in duplicates:
                errors.append(f"{first} : identifiant en double dans la matrice des flux")
                duplicates.add(first)
            continue
        matrix_ids.add(first)
        status = row.get("Statut", "")
        if not STATUS_RE.match(status):
            errors.append(
                f"{first} : statut « {status} » invalide "
                "(constaté (JJ/MM/AAAA), décidé, à confirmer, indiqué par … (JJ/MM/AAAA))"
            )

    for flow_id in sorted(set(view_ids) - matrix_ids):
        errors.append(f"{flow_id} : sur la vue {view_ids[flow_id]} mais absent de la matrice des flux")
    for flow_id in sorted(matrix_ids - set(view_ids)):
        errors.append(f"{flow_id} : dans la matrice des flux mais sur aucune vue")
    return errors


def mask_secrets(text: str) -> str:
    """Hide the usual forms of credentials in a command output (a safety net, not a guarantee)."""
    text = SECRET_VALUE_RE.sub(lambda m: m.group(1) + (m.group(2) or "") + MASK, text)
    text = URL_PASSWORD_RE.sub(lambda m: m.group(1) + MASK + "@", text)
    # -p<password> only on a MySQL or MariaDB client line: elsewhere -p is a port (docker run -p8080:80).
    return "".join(
        MYSQL_PASSWORD_RE.sub("-p" + MASK, line) if MYSQL_CLIENT_RE.search(line) else line
        for line in text.splitlines(keepends=True)
    )


def _docker(image: str, workdir: Path, args: Tuple[str, ...], error: str, options: Tuple[str, ...] = ()) -> None:
    """Run a tool image in Docker on files of workdir, as the current user; exit with a French error on failure."""
    command = [
        "docker", "run", "--rm", *options,
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp",
        "-v", f"{workdir}:/data", "-w", "/data",
        image, *args,
    ]
    print("+ " + shlex.join(command), flush=True)
    try:
        returncode = subprocess.run(command, stdout=subprocess.DEVNULL).returncode
    except FileNotFoundError:
        sys.exit("ERREUR : docker introuvable : installer Docker et le lancer")
    if returncode != 0:
        sys.exit(f"ERREUR : {error}")


def _drawio(workdir: Path, *args: str) -> None:
    """Run draw.io headless in Docker on files of workdir."""
    _docker(DRAWIO_IMAGE, workdir, args, "l'export draw.io a échoué", ("--shm-size=1g",))


def render(src: Path, view: Path, png: Optional[Path] = None) -> None:
    """Export a .drawio source to a .drawio.svg view (and an optional PNG preview)."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        shutil.copyfile(src, workdir / "source.drawio")
        _drawio(
            workdir, "--export", "--format", "svg", "--embed-diagram", "--embed-svg-images",
            "--theme", "light", "--output", "out.svg", "source.drawio",
        )
        shutil.copyfile(workdir / "out.svg", view)
        if png:
            _drawio(
                workdir, "--export", "--format", "png", "--scale", "2", "--theme", "light",
                "--output", "out.png", "source.drawio",
            )
            shutil.copyfile(workdir / "out.png", png)


def refresh(views_dir: Path) -> None:
    """Re-render every view in place from its own embedded diagram, once all of them are known to embed one."""
    sources: List[Tuple[Path, str]] = []
    for view in sorted(views_dir.glob("*.drawio.svg")):
        try:
            sources.append((view, extract_diagram(view)))
        except VIEW_ERRORS:
            sys.exit(f"ERREUR : {view.name} : {UNREADABLE_VIEW}")
    for view, diagram in sources:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.drawio"
            source.write_text(diagram, encoding="utf-8")
            render(source, view)


def _section(readme_text: str, title: str) -> str:
    """Return a level-2 section of the README, heading included (empty when absent)."""
    match = re.search(rf"^## {re.escape(title)}\n.*?(?=^## |\Z)", readme_text, re.M | re.S)
    return match.group(0).strip() if match else ""


def _for_print(markdown: str) -> str:
    """Adapt a README section to the PDF: no status column or legend, no link to repository files."""
    out: List[str] = []
    table: List[str] = []

    def flush() -> None:
        if table:
            header = _cells(table[0])
            drop = header.index("Statut") if "Statut" in header else None
            for row in table:
                cells = _cells(row)
                if drop is not None and drop < len(cells):
                    del cells[drop]
                out.append("| " + " | ".join(cells) + " |")
            table.clear()

    for line in markdown.split("\n"):
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if not line.startswith("Statuts :"):
            out.append(line)
    flush()
    text = LINK_RE.sub(lambda m: "" if ".md" in m.group(2) else m.group(1), "\n".join(out))
    # Drop the separators left around a removed link.
    text = re.sub(r"\s*;\s*(?=\|)", " ", text)
    return re.sub(r"(?<=\|)\s*;\s*", " ", text)


def build_dossier_markdown(config: Config, with_known_issues: bool) -> str:
    """Pandoc Markdown of the PDF dossier: title page, one view per page, then the README annexes."""
    readme_text = config.readme.read_text(encoding="utf-8")
    index = {m.group(2): (m.group(1), m.group(3).strip()) for m in VIEW_INDEX_RE.finditer(readme_text)}
    parts = [f"::: {{.titre}}\n# {config.title}\n\nDossier généré le {date.today():%d/%m/%Y}\n:::"]
    for view in list_views(config):
        title, question = index.get(view.name, (view.name, ""))
        lead = f"*{question}*\n\n" if question else ""
        parts.append(f"::: {{.vue}}\n## {title}\n\n{lead}![{title}](views/{view.name})\n:::")
    keep_known_issues = with_known_issues and config.known_issues
    for title in ANNEX_SECTIONS if keep_known_issues else ANNEX_SECTIONS[:-1]:
        body = _for_print(_section(readme_text, title))
        if body:
            parts.append(f"::: {{.annexe}}\n{body}\n:::")
    return "\n\n".join(parts) + "\n"


def pdf(config: Config, with_known_issues: bool, out: Optional[Path] = None) -> Path:
    """Build the PDF dossier: Markdown to HTML with pandoc, then HTML to PDF with headless Chromium."""
    if not config.css.is_file():
        raise ConfigError(f"dossier.css introuvable dans {config.css.parent}")
    target = out or config.pdf_path(with_known_issues)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        shutil.copytree(config.views_dir, workdir / "views")
        shutil.copyfile(config.css, workdir / "dossier.css")
        markdown = build_dossier_markdown(config, with_known_issues)
        (workdir / "dossier.md").write_text(markdown, encoding="utf-8")
        _docker(PANDOC_IMAGE, workdir, (
            "dossier.md", "--from", "markdown-implicit_figures", "--to", "html5", "--columns", "1000",
            "--standalone", "--css", "dossier.css",
            "--metadata", f"pagetitle={config.title}", "--metadata", "lang=fr", "--output", "dossier.html",
        ), "la conversion pandoc a échoué")
        _docker(CHROME_IMAGE, workdir, (
            "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
            "--print-to-pdf=/data/dossier.pdf", "file:///data/dossier.html",
        ), "l'impression PDF par Chromium a échoué", ("--shm-size=1g",))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(workdir / "dossier.pdf", target)
    print(f"Dossier PDF : {target}")
    return target


def _errors(messages: Sequence[str]) -> None:
    for message in messages:
        print(f"ERREUR : {message}", file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Architecture technique : vues draw.io, matrice des flux, dossier PDF")
    parser.add_argument("--racine", type=Path, help="dossier de la documentation (par défaut, celui qui contient tools/)")
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract", help="extrait le diagramme embarqué d'une vue")
    extract.add_argument("view", type=Path)
    extract.add_argument("out", type=Path)
    render_parser = commands.add_parser("render", help="génère une vue depuis une source .drawio")
    render_parser.add_argument("source", type=Path)
    render_parser.add_argument("view", type=Path)
    render_parser.add_argument("--png", type=Path, help="aperçu PNG à relire")
    commands.add_parser("refresh", help="régénère toutes les vues depuis leur diagramme embarqué")
    commands.add_parser("check", help="vérifie vues et matrice des flux")
    pdf_parser = commands.add_parser("pdf", help="génère le dossier PDF dans build/ (jamais versionné)")
    pdf_parser.add_argument("--sans-points-attention", action="store_true", help="retire l'annexe des points d'attention")
    pdf_parser.add_argument("--sortie", type=Path, help="fichier PDF produit (par défaut, dans build/)")
    commands.add_parser("version", help="affiche la version de l'outil")
    commands.add_parser("mask", help="recopie l'entrée standard en masquant les secrets usuels")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Command line entry point; returns the exit code."""
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(TOOL_VERSION)
        return 0
    if args.command == "mask":
        for line in sys.stdin:
            sys.stdout.write(mask_secrets(line))
        return 0
    if args.command == "extract":
        if not args.view.is_file():
            _errors([f"{args.view.name} : introuvable"])
            return 1
        try:
            diagram = extract_diagram(args.view)
        except VIEW_ERRORS:
            _errors([f"{args.view.name} : {UNREADABLE_VIEW}"])
            return 1
        args.out.write_text(diagram, encoding="utf-8")
        return 0
    if args.command == "render":
        render(args.source, args.view, args.png)
        return 0

    root = args.racine or default_root()
    try:
        config = load_config(root)
        if args.command == "refresh":
            refresh(config.views_dir)
            return 0
        errors = check(config)
        if errors:
            _errors(errors)
            return 1
        if args.command == "check":
            flows = len(matrix_rows(config.readme.read_text(encoding="utf-8"), config.flow_id_re))
            print(f"Schémas vérifiés : {len(list_views(config))} vue(s), {flows} flux.")
            return 0
        pdf(config, not args.sans_points_attention, args.sortie)
        return 0
    except ConfigError as error:
        _errors([str(error)])
        return 2


if __name__ == "__main__":
    sys.exit(main())
