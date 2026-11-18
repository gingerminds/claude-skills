"""Read context documents (xlsx, csv, docx, md, txt) with the standard library only.

A document is untrusted data. Nothing in it is executed or interpreted: formulas, macros, links
and XML entities are never followed, and a cell holds only the text stored in the file. The
sizes are capped (file, archive entries, archive member, archive total, cell, rows, output) and
a corrupted or oversized document raises DocumentError, with a message in French for the lead.
"""
import csv
import io
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Iterator, Optional

MAX_BYTES = 50 * 1024 * 1024
MAX_MEMBER = 100 * 1024 * 1024
MAX_TOTAL = 4 * MAX_MEMBER
MAX_CELL = 2000
MAX_ROWS = 20000
MAX_CHARS = 40000

MAX_ENTRIES = 5000
_MAX_COLUMNS = 16384
_MAX_SHEETS = 256
_MAX_STRINGS = 1000000
_MAX_LINE = 1024 * 1024

_FORMATS = {
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".docx": "docx",
    ".md": "text",
    ".txt": "text",
    ".pdf": "agent",
    ".png": "agent",
    ".jpg": "agent",
    ".jpeg": "agent",
}

_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    zlib.error,
    OSError,
    EOFError,
    NotImplementedError,
    RuntimeError,
    ValueError,
    OverflowError,
    struct.error,
)
# Keeps the line breaks of the text and nothing else (str.splitlines also cuts on \x0c, \x85...).
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+")
_CONTROL = {code: None for code in (*range(0, 32), *range(127, 160)) if code not in (9, 10)}
_CELL_REF = re.compile(r"([A-Za-z]{1,3})[0-9]*")
# What Excel itself refuses in a sheet name (so a real workbook never has it): the name is data
# that reaches the dispatch lines of the sub-agents, a forged one must not get through.
_MAX_SHEET_NAME = 31
_SHEET_NAME_FORBIDDEN = re.compile(r"[\\/?*\[\]:\x00-\x1f\x7f-\x9f]")
_XML_ENCODING = re.compile(rb"""^(?:\xef\xbb\xbf)?\s*<\?xml[^>]*?\sencoding\s*=\s*["']([^"']+)["']""")


class DocumentError(Exception):
    """A document that cannot be read (corrupted, too big, unsupported). Message in French."""


def format_of(path) -> str:
    """Return xlsx, csv, docx, text, agent (pdf and images, read by the agent) or unknown."""
    return _FORMATS.get(Path(path).suffix.lower(), "unknown")


def column_number(letters: str) -> int:
    """Return the number of a column from its letters ("A" is 1, "AB" is 28)."""
    if not isinstance(letters, str) or not re.fullmatch(r"[A-Za-z]{1,3}", letters):
        raise DocumentError("Référence de colonne invalide dans le document.")
    number = 0
    for char in letters.upper():
        number = number * 26 + ord(char) - 64
    if number > _MAX_COLUMNS:
        raise DocumentError("Référence de colonne hors limites dans le document.")
    return number


def list_sheets(path) -> list:
    """Return [{"name", "rows", "columns"}] of the document, in workbook order.

    A document without sheets (csv, docx, text) has one sheet whose name is None. rows is the
    number of the last row that holds something (at most MAX_ROWS), columns the widest row.
    """
    path = Path(path)
    kind = _readable(path)
    if kind != "xlsx":
        return [{"name": None, **_measure(_open_rows(path, None)[1])}]
    with _open_archive(path) as archive:
        sheets, shared = _xlsx_book(archive)
        strings = _shared_strings(archive, shared)
        return [
            {"name": name, **_measure(_xlsx_stream(archive.read_xml(member), strings))}
            for name, member in sheets
        ]


def iter_rows(path, sheet: Optional[str] = None) -> Iterator[tuple]:
    """Yield (row number from 1, cells as text) for the rows of a sheet, up to MAX_ROWS.

    The first sheet is read when sheet is not given. A row that holds nothing is yielded as an
    empty list when a later row holds something, so that numbers stay the ones of the document.
    Empty cells keep their column, trailing empty cells are dropped, a cell is cut at MAX_CELL.
    """
    return _normalize(_open_rows(Path(path), sheet)[1], {})


def read_rows(
    path,
    sheet: Optional[str] = None,
    first: int = 1,
    last: Optional[int] = None,
    max_chars: int = MAX_CHARS,
) -> dict:
    """Return {"sheet", "first", "last", "total", "truncated", "rows"} for a range of rows.

    rows holds the cells of rows first..last, stopping before the row that would take the output
    over max_chars characters (the first row is cut instead, so that a read always progresses).
    last is the number of the last row returned, total the number of rows of the sheet.
    truncated is true when rows of the asked range were left out (output cap or MAX_ROWS).
    """
    if first < 1 or (last is not None and last < first) or max_chars < 1:
        raise DocumentError("Plage de lignes invalide.")
    name, raw = _open_rows(Path(path), sheet)
    state: dict = {}
    rows: list = []
    used = 0
    total = 0
    truncated = False
    for number, cells in _normalize(raw, state):
        total = number
        if number < first or (last is not None and number > last) or truncated:
            continue
        size = sum(len(cell) for cell in cells)
        if used + size > max_chars:
            truncated = True
            if not rows:
                rows.append(_fit(cells, max_chars))
            continue
        rows.append(cells)
        used += size
    if state.get("capped") and (last is None or last >= MAX_ROWS):
        truncated = True
    return {
        "sheet": name,
        "first": first,
        "last": first + len(rows) - 1,
        "total": total,
        "truncated": truncated,
        "rows": rows,
    }


def _readable(path: Path) -> str:
    kind = format_of(path)
    if kind == "agent":
        raise DocumentError(
            "Ce format (PDF ou image) est lu par l'agent avec son outil de lecture, pas par ce module."
        )
    if kind == "unknown":
        raise DocumentError(
            "Format de document non pris en charge (xlsx, csv, docx, md, txt, pdf, png, jpg)."
        )
    try:
        info = path.stat()
    except OSError as error:
        raise DocumentError("Document introuvable ou illisible.") from error
    if not path.is_file():
        raise DocumentError("Ce chemin n'est pas un fichier.")
    if info.st_size > MAX_BYTES:
        raise DocumentError(f"Document trop volumineux (limite : {MAX_BYTES / 1048576:g} Mo).")
    return kind


def _open_rows(path: Path, sheet: Optional[str]) -> tuple:
    """Return (sheet name, iterator of raw (number, cells)); the checks happen at once."""
    kind = _readable(path)
    if kind == "xlsx":
        return _xlsx_open(path, sheet)
    if sheet:
        raise DocumentError("Ce document n'a pas de feuilles nommées.")
    if kind == "csv":
        return None, _csv_rows(path)
    if kind == "docx":
        return None, _docx_rows(path)
    return None, _text_rows(path)


def _clean(text) -> str:
    text = text[:MAX_CELL] if isinstance(text, str) else ""
    return text.replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL)


def _tidy(cells) -> list:
    tidy = [_clean(cell) for cell in cells[:_MAX_COLUMNS]]
    while tidy and not tidy[-1].strip():
        tidy.pop()
    return tidy


def _normalize(raw, state: dict) -> Iterator[tuple]:
    """Clean the rows, fill the gaps between them and stop at MAX_ROWS (state["capped"])."""
    previous = 0
    released = 0
    for number, cells in raw:
        if number <= previous:
            raise DocumentError("Document corrompu : lignes dans le désordre.")
        previous = number
        cells = _tidy(cells)
        if not cells:
            continue
        if number > MAX_ROWS:
            state["capped"] = True
            return
        for gap in range(released + 1, number):
            yield gap, []
        yield number, cells
        released = number


def _measure(raw) -> dict:
    rows = columns = 0
    for number, cells in _normalize(raw, {}):
        rows = number
        columns = max(columns, len(cells))
    return {"rows": rows, "columns": columns}


def _fit(cells: list, limit: int) -> list:
    fitted = []
    for cell in cells:
        fitted.append(cell[:limit])
        limit -= len(fitted[-1])
    return fitted


class _Archive:
    """A zip package read through size limits: declared size, bounded read, total budget."""

    def __init__(self, archive):
        self._archive = archive
        self._left = MAX_TOTAL

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._archive.close()
        return False

    def has(self, name: str) -> bool:
        try:
            self._archive.getinfo(name)
        except KeyError:
            return False
        return True

    def read(self, name: str) -> bytes:
        try:
            info = self._archive.getinfo(name)
        except KeyError:
            raise DocumentError("Document invalide : une partie obligatoire est absente.") from None
        if info.file_size > MAX_MEMBER:
            raise _too_big()
        try:
            with self._archive.open(info) as stream:
                data = stream.read(MAX_MEMBER + 1)
        except _ZIP_ERRORS as error:
            raise DocumentError("Archive du document corrompue.") from error
        if len(data) > MAX_MEMBER:
            raise _too_big()
        self._left -= len(data)
        if self._left < 0:
            raise _too_big()
        return data

    def read_xml(self, name: str) -> bytes:
        data = self.read(name)
        _check_xml(data)
        return data


def _too_big() -> DocumentError:
    return DocumentError("Document trop volumineux une fois décompressé.")


def _open_archive(path: Path) -> _Archive:
    try:
        archive = zipfile.ZipFile(path)
    except _ZIP_ERRORS as error:
        raise DocumentError("Archive du document illisible ou corrompue.") from error
    if len(archive.infolist()) > MAX_ENTRIES:
        archive.close()
        raise DocumentError("Document refusé : l'archive contient trop de fichiers.")
    return _Archive(archive)


def _check_xml(data: bytes) -> None:
    """Refuse what an office document never needs and an XML parser could be abused with."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data[:4]:
        raise DocumentError("Encodage XML non pris en charge (UTF-8 attendu).")
    declared = _XML_ENCODING.match(data[:300])
    if declared and declared.group(1).lower() not in (b"utf-8", b"utf8", b"us-ascii", b"ascii"):
        raise DocumentError("Encodage XML non pris en charge (UTF-8 attendu).")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise DocumentError("Document refusé : déclaration de type ou d'entités dans le XML.")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _complete(data: bytes, names) -> Iterator:
    """Yield the outermost elements whose local name is in names, each once it is complete.

    Everything read is dropped as soon as it is no longer needed, so that the memory stays flat
    whatever the size of the part.
    """
    stack: list = []
    inside = 0
    try:
        for event, element in ET.iterparse(io.BytesIO(data), events=("start", "end")):
            if event == "start":
                kept = inside > 0 or _local(element.tag) in names
                inside += kept
                stack.append((element, kept))
                continue
            _, kept = stack.pop()
            inside -= kept
            wanted = kept and inside == 0
            if wanted:
                yield element
            if (wanted or not kept) and stack:
                stack[-1][0].remove(element)
    except (ET.ParseError, ValueError) as error:
        raise DocumentError("Document corrompu : contenu XML invalide.") from error


def _rich_text(element) -> str:
    parts = []
    for child in element:
        name = _local(child.tag)
        if name == "t":
            parts.append(child.text or "")
        elif name == "r":
            parts.extend(grand.text or "" for grand in child if _local(grand.tag) == "t")
    return "".join(parts)


def _member_path(target: str) -> str:
    target = target.strip()
    joined = target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
    return posixpath.normpath(joined)


def _xlsx_book(archive: _Archive) -> tuple:
    """Return ([(sheet name, member)] in workbook order, member of the shared strings)."""
    relations = {}
    for relation in _complete(archive.read_xml("xl/_rels/workbook.xml.rels"), {"Relationship"}):
        if relation.get("TargetMode") != "External":
            relations[relation.get("Id")] = (relation.get("Type", ""), relation.get("Target", ""))
    sheets = []
    for sheet in _complete(archive.read_xml("xl/workbook.xml"), {"sheet"}):
        raw_name = sheet.get("name")
        name = _clean(raw_name)
        identifier = next(
            (value for key, value in sheet.attrib.items() if key.startswith("{") and _local(key) == "id"),
            None,
        )
        kind, target = relations.get(identifier, ("", ""))
        if not kind.endswith("/worksheet"):
            continue
        if not name:
            raise DocumentError("Classeur corrompu : feuille sans nom.")
        if len(raw_name) > _MAX_SHEET_NAME or _SHEET_NAME_FORBIDDEN.search(raw_name):
            raise DocumentError(
                "Classeur refusé : le nom d'une feuille n'est pas valide pour Excel "
                "(31 caractères au plus, ni caractère de contrôle, ni \\ / ? * [ ] :)."
            )
        if len(sheets) >= _MAX_SHEETS:
            raise DocumentError("Classeur trop volumineux : trop de feuilles.")
        sheets.append((name, _member_path(target)))
    if not sheets:
        raise DocumentError("Classeur sans feuille lisible.")
    shared = "xl/sharedStrings.xml"
    for kind, target in relations.values():
        if kind.endswith("/sharedStrings"):
            shared = _member_path(target)
    return sheets, shared


def _shared_strings(archive: _Archive, member: str) -> list:
    if not archive.has(member):
        return []
    strings: list = []
    for item in _complete(archive.read_xml(member), {"si"}):
        if len(strings) >= _MAX_STRINGS:
            raise DocumentError("Classeur trop volumineux : trop de chaînes partagées.")
        strings.append(_rich_text(item)[:MAX_CELL])
    return strings


def _xlsx_open(path: Path, sheet: Optional[str]) -> tuple:
    with _open_archive(path) as archive:
        sheets, shared = _xlsx_book(archive)
        if sheet:
            members = [member for name, member in sheets if name == sheet]
            if not members:
                raise DocumentError(f"Feuille introuvable dans le classeur : {sheet[:80]}.")
            name, member = sheet, members[0]
        else:
            name, member = sheets[0]
        strings = _shared_strings(archive, shared)
        data = archive.read_xml(member)
    return name, _xlsx_stream(data, strings)


def _xlsx_text(cell, strings: list) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return next((_rich_text(child) for child in cell if _local(child.tag) == "is"), "")
    value = next((child.text or "" for child in cell if _local(child.tag) == "v"), None)
    if value is None:
        return ""
    if kind == "s":
        try:
            index = int(value)
            if index < 0:
                raise IndexError
            return strings[index]
        except (ValueError, IndexError):
            raise DocumentError("Classeur corrompu : chaîne partagée introuvable.") from None
    if kind == "b":
        return "TRUE" if value.strip() == "1" else "FALSE"
    return value


def _xlsx_stream(data: bytes, strings: list) -> Iterator[tuple]:
    number = 0
    for row in _complete(data, {"row"}):
        try:
            number = int(row.get("r")) if row.get("r") else number + 1
        except ValueError:
            raise DocumentError("Classeur corrompu : numéro de ligne invalide.") from None
        cells: list = []
        column = 0
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            reference = cell.get("r")
            if reference:
                found = _CELL_REF.fullmatch(reference)
                if not found:
                    raise DocumentError("Classeur corrompu : référence de cellule invalide.")
                column = column_number(found.group(1))
            else:
                column += 1
            if column > _MAX_COLUMNS:
                raise DocumentError("Classeur corrompu : colonne hors limites.")
            text = _xlsx_text(cell, strings)
            if not text:
                continue
            cells.extend([""] * (column - len(cells)))
            cells[column - 1] = text
        yield number, cells


def _read_bytes(path: Path) -> bytes:
    try:
        with open(path, "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError as error:
        raise DocumentError("Document illisible.") from error
    if len(data) > MAX_BYTES:
        raise DocumentError(f"Document trop volumineux (limite : {MAX_BYTES / 1048576:g} Mo).")
    return data


def _read_text(path: Path) -> str:
    data = _read_bytes(path)
    if b"\x00" in data:
        raise DocumentError("Document binaire : un fichier texte était attendu.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _score(sample: str, delimiter: str) -> tuple:
    """Return how well a delimiter splits the sample: (records of the usual width, that width)."""
    try:
        reader = csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter)
        counts = [len(row) for row in reader if row][:50]
    except csv.Error:
        return (0, 0)
    if not counts:
        return (0, 0)
    usual = max(set(counts), key=lambda width: (counts.count(width), width))
    return (counts.count(usual) if usual > 1 else 0, usual)


def _delimiter(text: str) -> str:
    """Return the delimiter of a csv: the one of csv.Sniffer unless another splits the rows better.

    The sniffer alone takes a comma in the texts of a semicolon file for the delimiter.
    """
    sample = text[:16384]
    if len(text) > len(sample):
        sample = sample[: sample.rfind("\n") + 1] or sample
    try:
        best = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        best = ","
    best_score = _score(sample, best)
    for candidate in ",;\t":
        score = _score(sample, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best


def _csv_rows(path: Path) -> Iterator[tuple]:
    text = _read_text(path)
    delimiter = _delimiter(text)

    def lines():
        for found in _LINE.finditer(text):
            if len(found.group()) > _MAX_LINE:
                raise DocumentError("Document CSV illisible : ligne trop longue.")
            yield found.group()

    def rows():
        try:
            yield from enumerate(csv.reader(lines(), delimiter=delimiter), 1)
        except csv.Error as error:
            raise DocumentError("Document CSV illisible.") from error

    return rows()


def _text_rows(path: Path) -> Iterator[tuple]:
    text = _read_text(path)
    return (
        (number, [found.group().rstrip("\r\n")]) for number, found in enumerate(_LINE.finditer(text), 1)
    )


def _paragraph_text(paragraph) -> str:
    parts = []
    for run in paragraph.iter():
        if _local(run.tag) != "r":
            continue
        for child in run:
            name = _local(child.tag)
            if name == "t":
                parts.append(child.text or "")
            elif name == "tab":
                parts.append("\t")
            elif name in ("br", "cr"):
                parts.append("\n")
    return "".join(parts)


def _docx_rows(path: Path) -> Iterator[tuple]:
    with _open_archive(path) as archive:
        data = archive.read_xml("word/document.xml")
    return _docx_stream(data)


def _docx_stream(data: bytes) -> Iterator[tuple]:
    number = 0
    for block in _complete(data, {"p", "tbl"}):
        if _local(block.tag) == "p":
            number += 1
            yield number, [_paragraph_text(block)]
            continue
        for row in block:
            if _local(row.tag) != "tr":
                continue
            cells = [
                "\n".join(_paragraph_text(p) for p in cell.iter() if _local(p.tag) == "p")
                for cell in row
                if _local(cell.tag) == "tc"
            ]
            number += 1
            yield number, cells
