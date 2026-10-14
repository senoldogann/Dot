"""The text of Word, Excel and PowerPoint files (Office Open XML: ``.docx``, ``.xlsx`` and ``.xlsm``, ``.pptx``) for the
model to read. A document reads as its paragraphs and tables in order, its headings and list items marked as Markdown
marks them. A workbook reads sheet by sheet, each cell after its address, a formula with the value Excel last worked
out for it. A presentation reads slide by slide: the text of its shapes, its tables, its charts' series and its notes.

The formats of before 2007 (``.doc``, ``.xls``, ``.ppt``) and files locked with a password are not read: they are not
the zip packages of Office Open XML, and the error says how to save one that is.
"""

import datetime as dt
import io
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import PurePath
from typing import Literal

import docx
import openpyxl
import pptx
from docx.document import Document as WordDocument
from docx.opc.exceptions import PackageNotFoundError as DocxPackageNotFoundError
from docx.table import Table as WordTable
from docx.text.paragraph import Paragraph
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.formula import ArrayFormula
from pptx.chart.chart import Chart
from pptx.chart.plot import _BasePlot
from pptx.exc import PackageNotFoundError as PptxPackageNotFoundError
from pptx.presentation import Presentation
from pptx.shapes.autoshape import Shape
from pptx.shapes.base import BaseShape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.group import GroupShape
from pptx.slide import Slide
from pptx.text.text import TextFrame

from dotagent.core.errors import InvalidValueError

type OfficeKind = Literal["document", "workbook", "presentation"]

OFFICE_SUFFIXES: dict[str, OfficeKind] = {
    ".docx": "document",
    ".xlsx": "workbook",
    ".xlsm": "workbook",
    ".pptx": "presentation",
}
OFFICE_MEDIA_TYPES: dict[OfficeKind, str] = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "workbook": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_NAMES: dict[OfficeKind, str] = {
    "document": "Word document",
    "workbook": "Excel workbook",
    "presentation": "presentation",
}
# The formats of before 2007, and the one to save each as.
_OLD_SUFFIXES: dict[str, str] = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}
_ZIP_MAGIC: bytes = b"PK\x03\x04"
# The first bytes of an OLE compound file: an Office file of before 2007, or one of Office Open XML locked with a
# password.
_OLE_MAGIC: bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# The cells of a workbook read at most; a sheet larger than that is read in part, and the text says where it stops.
MAX_CELLS: int = 100_000


def office_kind(name: str, head: bytes) -> OfficeKind | None:
    """What kind of Office file ``name`` is, by its suffix, when its first bytes are those of a zip package; ``None``
    for any other file."""
    kind: OfficeKind | None = OFFICE_SUFFIXES.get(PurePath(name).suffix.lower())
    return kind if kind is not None and head.startswith(_ZIP_MAGIC) else None


def unreadable_office_file(name: str, head: bytes) -> str | None:
    """Why ``name``, an Office file by its suffix, cannot be read, or ``None`` when it is no such file: one of before
    2007, or one locked with a password."""
    suffix: str = PurePath(name).suffix.lower()
    newer: str | None = _OLD_SUFFIXES.get(suffix)
    if newer is not None:
        return f"a {suffix} file is of the format of before 2007; save it as {newer}"
    if suffix in OFFICE_SUFFIXES and head.startswith(_OLE_MAGIC):
        return f"the {suffix} file is locked with a password; save a copy without one"
    return None


def names_office_file(name: str) -> bool:
    """Whether ``name`` has the suffix of a Word, Excel or PowerPoint file, of Office Open XML or of before 2007."""
    suffix: str = PurePath(name).suffix.lower()
    return suffix in OFFICE_SUFFIXES or suffix in _OLD_SUFFIXES


def office_text(kind: OfficeKind, data: bytes) -> str:
    """The text of an Office file of ``kind`` whose bytes are ``data``. ``InvalidValueError`` when it cannot be read:
    a package that is no zip or lacks a part, or a part whose XML is broken (the errors of lxml and of ElementTree are
    ``SyntaxError``s)."""
    try:
        match kind:
            case "document":
                return document_text(docx.Document(io.BytesIO(data)))
            case "workbook":
                with (
                    closing(openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)) as formulas,
                    closing(openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)) as values,
                ):
                    return workbook_text(_whole(formulas), _whole(values), MAX_CELLS)
            case "presentation":
                return presentation_text(pptx.Presentation(io.BytesIO(data)))
    except (
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        SyntaxError,
        DocxPackageNotFoundError,
        PptxPackageNotFoundError,
    ) as error:
        raise InvalidValueError(
            f"the {_NAMES[kind]} cannot be read: {error}", {"kind": kind, "error": type(error).__name__}
        ) from error


# ----- documents ---------------------------------------------------------------------------------


def _cell_text(text: str) -> str:
    """A table cell's text on one line, with the pipes that would end its cell escaped."""
    return " ".join(text.split()).replace("|", "\\|")


def table_lines(rows: Sequence[Sequence[str]]) -> list[str]:
    """A table as Markdown: its first row as the head, every row as wide as the widest."""
    width: int = max((len(row) for row in rows), default=0)
    if width == 0:
        return []
    filled: list[list[str]] = [[_cell_text(cell) for cell in row] + [""] * (width - len(row)) for row in rows]
    head, *body = filled
    return [
        f"| {' | '.join(head)} |",
        f"|{'|'.join(['---'] * width)}|",
        *(f"| {' | '.join(row)} |" for row in body),
    ]


def _heading_level(style: str) -> int | None:
    """The level of a heading by its style's name: ``Title`` is 1, ``Heading 2`` is 2; ``None`` for no heading."""
    if style == "Title":
        return 1
    if style.startswith("Heading "):
        number: str = style.removeprefix("Heading ")
        return int(number) if number.isdigit() else None
    return None


def _paragraph_line(paragraph: Paragraph) -> str | None:
    text: str = paragraph.text.strip()
    if text == "":
        return None
    style: str = paragraph.style.name if paragraph.style is not None and paragraph.style.name is not None else ""
    level: int | None = _heading_level(style)
    if level is not None:
        return f"{'#' * min(level, 6)} {text}"
    if "List" in style:
        return f"- {text}"
    return text


def _word_rows(table: WordTable) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def document_text(document: WordDocument) -> str:
    """A Word document's paragraphs and tables in order, a blank line between them."""
    blocks: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            line: str | None = _paragraph_line(block)
            if line is not None:
                blocks.append(line)
        else:
            lines: list[str] = table_lines(_word_rows(block))
            if lines:
                blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ----- workbooks ---------------------------------------------------------------------------------


def cell_value(value: object) -> str:
    """A value of a cell as the text shows it: a whole number without a fraction, a date without a time of 00:00."""
    match value:
        case bool():
            return "TRUE" if value else "FALSE"
        case float() if value.is_integer():
            return str(int(value))
        case dt.datetime() if value.time() == dt.time():
            return value.date().isoformat()
        case dt.datetime() | dt.date() | dt.time():
            return value.isoformat()
        case str():
            return " ↵ ".join(value.splitlines())
        case _:
            return str(value)


def _column_letters(number: int) -> str:
    letters: str = ""
    while number > 0:
        number, rest = divmod(number - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _formula(value: object) -> str | None:
    """The formula a cell holds, or ``None`` for a value."""
    if isinstance(value, ArrayFormula):
        return value.text
    if isinstance(value, str) and value.startswith("="):
        return value
    return None


def _whole(workbook: Workbook) -> Workbook:
    """``workbook``, read only, with every sheet read to its last cell: a sheet read only stops where the size it
    records of itself ends, and a file written by another program can record one too small."""
    for sheet in workbook.worksheets:
        sheet.reset_dimensions()
    return workbook


def _cell(address: str, written: object, worked_out: object) -> str:
    formula: str | None = _formula(written)
    if formula is None:
        return f"{address}: {cell_value(written)}"
    if worked_out is None:
        return f"{address}: {formula} (not worked out yet)"
    return f"{address}: {formula} → {cell_value(worked_out)}"


def workbook_text(formulas: Workbook, values: Workbook, max_cells: int) -> str:
    """Every sheet of a workbook: a heading with its name, then a line for each row that holds anything, its cells
    after their addresses. ``formulas`` is the workbook read for what its cells hold, ``values`` for what Excel last
    worked out; at most ``max_cells`` cells are read."""
    sections: list[str] = []
    left: int = max_cells
    sheets: Sequence[object] = formulas.worksheets
    for number, (written, worked_out) in enumerate(zip(formulas.worksheets, values.worksheets, strict=True), 1):
        hidden: str = "" if written.sheet_state == "visible" else f", {written.sheet_state}"
        lines: list[str] = [f"## Sheet {number} of {len(sheets)}: {written.title}{hidden}"]
        rows: Iterator[tuple[tuple[object, ...], tuple[object, ...]]] = zip(
            written.iter_rows(values_only=True), worked_out.iter_rows(values_only=True), strict=False
        )
        for row_number, (held, shown) in enumerate(rows, 1):
            cells: list[str] = [
                _cell(
                    f"{_column_letters(column)}{row_number}", value, shown[column - 1] if column <= len(shown) else None
                )
                for column, value in enumerate(held, 1)
                if value is not None
            ]
            if not cells:
                continue
            if len(cells) > left:
                lines.append(
                    f"(the workbook holds more than {max_cells} cells; the rest from row {row_number} on is not read)"
                )
                sections.append("\n".join(lines))
                return "\n\n".join(sections)
            left -= len(cells)
            lines.append(" | ".join(cells))
        if len(lines) == 1:
            lines.append("(empty)")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


# ----- presentations -----------------------------------------------------------------------------

# The kinds of chart python-pptx reads, by the class of their plot. A scatter or bubble chart is read for its y values
# alone.
_PLOT_KINDS: dict[str, str] = {
    "AreaPlot": "area",
    "Area3DPlot": "3D area",
    "BarPlot": "bar",
    "BubblePlot": "bubble, y values only",
    "DoughnutPlot": "doughnut",
    "LinePlot": "line",
    "PiePlot": "pie",
    "RadarPlot": "radar",
    "XyPlot": "scatter, y values only",
}


def _point(category: str | None, value: object) -> str:
    shown: str = "(blank)" if value is None else cell_value(value)
    return shown if category is None else f"{category} {shown}"


def _chart_lines(chart: Chart) -> list[str]:
    """A chart as a line with its title and kind, then a line for each series with its values by category."""
    title: str = (
        chart.chart_title.text_frame.text.strip() if chart.has_title and chart.chart_title.has_text_frame else ""
    )
    named: str = f"Chart “{title}”" if title else "Chart"
    try:
        plots: list[_BasePlot] = list(chart.plots)
    except ValueError:
        # python-pptx reads no chart of another kind than those of _PLOT_KINDS.
        return [f"{named}: of a kind that is not read (3D bars or lines, stock or surface)"]
    kinds: str = ", ".join(dict.fromkeys(_PLOT_KINDS[type(plot).__name__] for plot in plots))
    lines: list[str] = [f"{named} ({kinds}):" if kinds else f"{named}:"]
    for plot in plots:
        categories: list[str] = [cell_value(category) for category in plot.categories]
        for series in plot.series:
            points: list[str] = [
                _point(categories[index] if index < len(categories) else None, value)
                for index, value in enumerate(series.values)
            ]
            lines.append(f"- {series.name}: {', '.join(points)}")
    return lines


def _shape_lines(shape: BaseShape) -> list[str]:
    """The lines of a shape: those of the shapes in a group, a table, a chart, or the paragraphs of its text, each
    indented by its level. A picture or a line has none."""
    if isinstance(shape, GroupShape):
        return [line for inner in shape.shapes for line in _shape_lines(inner)]
    if isinstance(shape, GraphicFrame) and shape.has_table:
        return table_lines([[cell.text for cell in row.cells] for row in shape.table.rows])
    if isinstance(shape, GraphicFrame) and shape.has_chart:
        return _chart_lines(shape.chart)
    if isinstance(shape, Shape):
        return [
            f"{'  ' * paragraph.level}{paragraph.text.strip()}"
            for paragraph in shape.text_frame.paragraphs
            if paragraph.text.strip()
        ]
    return []


def slide_text(slide: Slide, number: int) -> str:
    """A slide under a heading with its number and title: the lines of its other shapes, then its notes."""
    title_shape: Shape | None = slide.shapes.title
    title: str = title_shape.text_frame.text.strip() if title_shape is not None else ""
    lines: list[str] = [f"## Slide {number}" + (f": {title}" if title else "")]
    for shape in slide.shapes:
        if title_shape is not None and shape.shape_id == title_shape.shape_id:
            continue
        lines.extend(_shape_lines(shape))
    if slide.has_notes_slide:
        frame: TextFrame | None = slide.notes_slide.notes_text_frame
        notes: str = frame.text.strip() if frame is not None else ""
        if notes:
            lines.append(f"Notes: {notes}")
    return "\n".join(lines)


def presentation_text(presentation: Presentation) -> str:
    """Every slide of a presentation: a heading with its number and title, the text of its shapes, its tables and
    charts, and its notes."""
    return "\n\n".join(slide_text(slide, number) for number, slide in enumerate(presentation.slides, 1))
