"""Office files written from their XML (item 196), for eval tasks to start from: a workbook whose formulas hold the
results Excel saved with them, which no Python library writes, and a Word document whose runs carry their own
formatting. Every part is stored with one date, so a file is the same bytes whenever it is built. Pure."""

import io
import zipfile
from collections.abc import Sequence
from xml.sax.saxutils import escape

from dotagent.core.types import FrozenModel

_DATE: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0)
_XML: str = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_TYPES: str = "http://schemas.openxmlformats.org/package/2006/content-types"
_PACKAGE: str = "http://schemas.openxmlformats.org/package/2006/relationships"
_RELATIONS: str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SHEET_MAIN: str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_WORD_MAIN: str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_OFFICE_TYPE: str = "application/vnd.openxmlformats-officedocument"
# The one style every cell and run uses: Excel needs a stylesheet with its default font and fills.
_STYLES: str = (
    f'{_XML}<styleSheet xmlns="{_SHEET_MAIN}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font>'
    '</fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/>'
    '</fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1">'
    '<cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
)
# A table column's width in twentieths of a point, and the page of the document: A4 with margins of an inch.
_COLUMN_TWIPS: int = 3000
_SECTION: str = (
    '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
    'w:header="708" w:footer="708" w:gutter="0"/>'
)


class Formula(FrozenModel):
    """A cell with a formula, as Excel saves it: the formula without its "=", and the result Excel worked out."""

    text: str
    result: float


type Cell = str | int | float | Formula


class Sheet(FrozenModel):
    """A worksheet: its name, and its rows from the first, each a list of cells from column A."""

    name: str
    rows: tuple[tuple[Cell, ...], ...]


class Run(FrozenModel):
    """A stretch of text with one formatting."""

    text: str
    bold: bool
    italic: bool


class Paragraph(FrozenModel):
    runs: tuple[Run, ...]


class Table(FrozenModel):
    """A table of plain text, its rows of cells."""

    rows: tuple[tuple[str, ...], ...]


def _package(parts: Sequence[tuple[str, str]]) -> bytes:
    """A zip of ``parts`` (each a name and its XML), stored as they are and with one date."""
    buffer: io.BytesIO = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, text in parts:
            archive.writestr(zipfile.ZipInfo(name, date_time=_DATE), text)
    return buffer.getvalue()


def _content_types(overrides: Sequence[tuple[str, str]]) -> str:
    named: str = "".join(f'<Override PartName="/{part}" ContentType="{kind}"/>' for part, kind in overrides)
    return (
        f'{_XML}<Types xmlns="{_TYPES}"><Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>{named}</Types>'
    )


def _relationships(targets: Sequence[tuple[str, str]]) -> str:
    """Relationships to ``targets`` (each a kind of the office document relationships and a part), ``rId1`` on."""
    listed: str = "".join(
        f'<Relationship Id="rId{index}" Type="{_RELATIONS}/{kind}" Target="{target}"/>'
        for index, (kind, target) in enumerate(targets, start=1)
    )
    return f'{_XML}<Relationships xmlns="{_PACKAGE}">{listed}</Relationships>'


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _cell(reference: str, value: Cell) -> str:
    if isinstance(value, Formula):
        return f'<c r="{reference}"><f>{escape(value.text)}</f><v>{_number(value.result)}</v></c>'
    if isinstance(value, str):
        return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
    return f'<c r="{reference}"><v>{_number(value)}</v></c>'


def _worksheet(sheet: Sheet) -> str:
    rows: str = "".join(
        f'<row r="{number}">'
        + "".join(_cell(f"{chr(ord('A') + column)}{number}", value) for column, value in enumerate(row))
        + "</row>"
        for number, row in enumerate(sheet.rows, start=1)
    )
    return f'{_XML}<worksheet xmlns="{_SHEET_MAIN}"><sheetData>{rows}</sheetData></worksheet>'


def workbook(sheets: Sequence[Sheet]) -> bytes:
    """An .xlsx of ``sheets`` (at most 26 columns each), its strings written in its cells."""
    paths: list[str] = [f"worksheets/sheet{index}.xml" for index in range(1, len(sheets) + 1)]
    listed: str = "".join(
        f'<sheet name="{escape(sheet.name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet in enumerate(sheets, start=1)
    )
    sheet_type: str = f"{_OFFICE_TYPE}.spreadsheetml.worksheet+xml"
    return _package(
        [
            (
                "[Content_Types].xml",
                _content_types(
                    [
                        ("xl/workbook.xml", f"{_OFFICE_TYPE}.spreadsheetml.sheet.main+xml"),
                        ("xl/styles.xml", f"{_OFFICE_TYPE}.spreadsheetml.styles+xml"),
                        *((f"xl/{path}", sheet_type) for path in paths),
                    ]
                ),
            ),
            ("_rels/.rels", _relationships([("officeDocument", "xl/workbook.xml")])),
            (
                "xl/workbook.xml",
                f'{_XML}<workbook xmlns="{_SHEET_MAIN}" xmlns:r="{_RELATIONS}"><sheets>{listed}</sheets></workbook>',
            ),
            (
                "xl/_rels/workbook.xml.rels",
                _relationships([*(("worksheet", path) for path in paths), ("styles", "styles.xml")]),
            ),
            ("xl/styles.xml", _STYLES),
            *((f"xl/{path}", _worksheet(sheet)) for path, sheet in zip(paths, sheets, strict=True)),
        ]
    )


def _run(run: Run) -> str:
    marks: str = ("<w:b/>" if run.bold else "") + ("<w:i/>" if run.italic else "")
    formatting: str = f"<w:rPr>{marks}</w:rPr>" if marks else ""
    return f'<w:r>{formatting}<w:t xml:space="preserve">{escape(run.text)}</w:t></w:r>'


def _paragraph(paragraph: Paragraph) -> str:
    return "<w:p>" + "".join(_run(run) for run in paragraph.runs) + "</w:p>"


def _table(table: Table) -> str:
    columns: int = max(len(row) for row in table.rows)
    grid: str = "".join(f'<w:gridCol w:w="{_COLUMN_TWIPS}"/>' for _ in range(columns))
    rows: str = "".join(
        "<w:tr>"
        + "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="{_COLUMN_TWIPS}" w:type="dxa"/></w:tcPr>'
            f"{_paragraph(Paragraph(runs=(Run(text=text, bold=False, italic=False),)))}</w:tc>"
            for text in row
        )
        + "</w:tr>"
        for row in table.rows
    )
    return f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{rows}</w:tbl>'


def document(body: Sequence[Paragraph | Table], header: Sequence[Paragraph]) -> bytes:
    """A .docx of ``body``, the paragraphs and tables of its one section in their order, with ``header`` at the top of
    every page."""
    blocks: str = "".join(_paragraph(block) if isinstance(block, Paragraph) else _table(block) for block in body)
    top: str = "".join(_paragraph(paragraph) for paragraph in header)
    namespaces: str = f'xmlns:w="{_WORD_MAIN}" xmlns:r="{_RELATIONS}"'
    return _package(
        [
            (
                "[Content_Types].xml",
                _content_types(
                    [
                        ("word/document.xml", f"{_OFFICE_TYPE}.wordprocessingml.document.main+xml"),
                        ("word/header1.xml", f"{_OFFICE_TYPE}.wordprocessingml.header+xml"),
                    ]
                ),
            ),
            ("_rels/.rels", _relationships([("officeDocument", "word/document.xml")])),
            (
                "word/document.xml",
                f"{_XML}<w:document {namespaces}><w:body>{blocks}<w:sectPr>"
                f'<w:headerReference w:type="default" r:id="rId1"/>{_SECTION}</w:sectPr></w:body></w:document>',
            ),
            ("word/_rels/document.xml.rels", _relationships([("header", "header1.xml")])),
            ("word/header1.xml", f"{_XML}<w:hdr {namespaces}>{top}</w:hdr>"),
        ]
    )
