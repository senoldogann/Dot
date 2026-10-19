"""What the panel beside a chat shows of a file (``ShownFile``): the pages of a PDF, an image, the content of a Word
document, an Excel workbook or a presentation (``dotagent.office``), a CSV or TSV table as a sheet, or text, which the
page always shows as text. A file of another kind, or one that cannot be read, is shown as why."""

import codecs
import csv
import io
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.office import DocumentContent, PresentationContent, Sheet, SheetCell, WorkbookContent, office_content
from dotagent.previews.paths import HEAD_BYTES, TABLE_DELIMITERS, ShownKind, opens_on_mac, shown_kind
from dotagent.previews.pdfpages import PAGE_WIDTHS, PageSize, page_sizes, pdf_text

# An Office file larger than this is not read for the panel: the reading would take long.
MAX_OFFICE_BYTES: int = 50 * 1024 * 1024
# The bytes of a text the panel shows at most.
TEXT_BYTES: int = 512 * 1024
# The cells of a workbook or a table the panel shows at most.
SHOWN_CELLS: int = 20_000
# The delimiters a CSV file's cells may be split by: a comma, or a semicolon where the comma marks decimals.
_CSV_DELIMITERS: str = ",;\t"
_SNIFF_CHARS: int = 16_384


class PdfPreview(FrozenModel):
    """A PDF: the size of each page, the widths in pixels a page is drawn at (``/api/files/page``), and its text of at
    most ``TEXT_BYTES`` characters for the panel's copy (item 403), ``cut`` when the PDF holds more; "" when it has
    none that reads."""

    kind: Literal["pdf"]
    pages: tuple[PageSize, ...]
    widths: tuple[int, ...]
    text: str
    cut: bool


class ImagePreview(FrozenModel):
    """An image, drawn from ``/api/files/image``."""

    kind: Literal["image"]


class TextPreview(FrozenModel):
    """A text, of at most ``TEXT_BYTES``; ``cut`` when the file holds more."""

    kind: Literal["text"]
    text: str
    cut: bool


class TablePreview(FrozenModel):
    """A CSV or TSV file as a sheet of at most ``max_cells`` cells."""

    kind: Literal["table"]
    sheet: Sheet
    max_cells: int


class UnshownPreview(FrozenModel):
    """A file the panel does not show, and why."""

    kind: Literal["unshown"]
    reason: str


type FilePreview = Annotated[
    PdfPreview
    | ImagePreview
    | TextPreview
    | TablePreview
    | DocumentContent
    | WorkbookContent
    | PresentationContent
    | UnshownPreview,
    Field(discriminator="kind"),
]


class ShownFile(FrozenModel):
    """A file for the panel: where it is (its links followed), its name, its size in bytes, when it changed (seconds
    since the epoch), whether it may open in its application on the Mac, and what the panel shows of it."""

    path: str
    name: str
    size: int
    modified: float
    opens: bool
    preview: FilePreview


def _encoding(head: bytes) -> str:
    """The encoding of a text by its byte order mark, else UTF-8."""
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return "utf-8"


def _text(path: Path, head: bytes) -> TextPreview:
    with path.open("rb") as file:
        data: bytes = file.read(TEXT_BYTES + 1)
    cut: bool = len(data) > TEXT_BYTES
    # A text cut in the middle of a character ends before it.
    decoder: codecs.IncrementalDecoder = codecs.getincrementaldecoder(_encoding(head))(errors="replace")
    return TextPreview(kind="text", text=decoder.decode(data[:TEXT_BYTES], final=not cut), cut=cut)


def _delimiter(path: Path, sample: str) -> str:
    """The delimiter of a table: a TSV file's tab, or the one a CSV file's first lines show, a comma when they show
    none (a table of one column)."""
    delimiter: str = TABLE_DELIMITERS[path.suffix.lower()]
    if delimiter != ",":
        return delimiter
    try:
        return csv.Sniffer().sniff(sample, delimiters=_CSV_DELIMITERS).delimiter
    except csv.Error:
        return delimiter


def _table(path: Path, head: bytes) -> TablePreview:
    cells: list[SheetCell] = []
    cut_at_row: int | None = None
    with path.open("rb") as raw, io.TextIOWrapper(raw, encoding=_encoding(head), errors="replace", newline="") as text:
        sample: str = text.read(_SNIFF_CHARS)
        text.seek(0)
        rows: Iterator[list[str]] = csv.reader(text, delimiter=_delimiter(path, sample))
        try:
            for row_number, fields in enumerate(rows, 1):
                row: list[SheetCell] = [
                    SheetCell(row=row_number, column=column, value=field, formula=None)
                    for column, field in enumerate(fields, 1)
                    if field != ""
                ]
                if len(cells) + len(row) > SHOWN_CELLS:
                    cut_at_row = row_number
                    break
                cells.extend(row)
        except csv.Error as error:
            raise InvalidValueError(f"the table cannot be read: {error}", {"path": str(path)}) from error
    sheet: Sheet = Sheet(title=path.name, state="visible", cells=tuple(cells), cut_at_row=cut_at_row)
    return TablePreview(kind="table", sheet=sheet, max_cells=SHOWN_CELLS)


def _office(path: Path, kind: Literal["document", "workbook", "presentation"], size: int) -> FilePreview:
    if size > MAX_OFFICE_BYTES:
        raise InvalidValueError(
            f"the file is larger than {MAX_OFFICE_BYTES // (1024 * 1024)} MB, too large to show here",
            {"path": str(path), "bytes": size},
        )
    return office_content(kind, path.read_bytes(), SHOWN_CELLS)


def _preview(path: Path, head: bytes, size: int) -> FilePreview:
    kind: ShownKind = shown_kind(path, head)
    match kind:
        case "pdf":
            text, cut = pdf_text(path, TEXT_BYTES)
            return PdfPreview(kind="pdf", pages=page_sizes(path), widths=PAGE_WIDTHS, text=text, cut=cut)
        case "image":
            return ImagePreview(kind="image")
        case "text":
            return _text(path, head)
        case "table":
            return _table(path, head)
        case "document" | "workbook" | "presentation":
            return _office(path, kind, size)


def shown_file(path: Path) -> ShownFile:
    """What the panel shows of the file at ``path``, one that ``located_file`` found: when it cannot be shown, why.
    Blocking file work: call it off the event loop."""
    status: os.stat_result = path.stat()
    with path.open("rb") as file:
        head: bytes = file.read(HEAD_BYTES)
    try:
        preview: FilePreview = _preview(path, head, status.st_size)
    except InvalidValueError as error:
        preview = UnshownPreview(kind="unshown", reason=error.message)
    return ShownFile(
        path=str(path),
        name=path.name,
        size=status.st_size,
        modified=status.st_mtime,
        opens=opens_on_mac(path),
        preview=preview,
    )
