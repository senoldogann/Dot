"""The files of the two eval tasks on Office files (item 196) and their checks.

``sales.xlsx`` has a month per row on its Data sheet and two totals on its Summary sheet, formulas that hold the results
Excel saved with them, as a workbook from Excel does. ``letter.docx`` names the company in bold, split over two italic
runs, in a table, in the closing line and in the header. The checks run as ``python -I -m dotagent.evals.office_tasks
<file> <path>`` in the workspace an attempt left: they read the file with the libraries Dot's own Python has, print what
is wrong and end with 1 when anything is, so a report shows why a check failed.
"""

import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal

from docx import Document
from docx.document import Document as DocumentObject
from docx.text.paragraph import Paragraph as DocxParagraph
from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook

from dotagent.evals.ooxml import Formula, Paragraph, Run, Sheet, Table, document, workbook

# The months of the Data sheet before the task: name, units and price.
SALES_MONTHS: tuple[tuple[str, int, float], ...] = (
    ("January", 100, 12.5),
    ("February", 80, 12.5),
    ("March", 120, 13.0),
    ("April", 90, 13.0),
    ("May", 150, 13.5),
    ("June", 110, 14.0),
    ("July", 130, 14.0),
)
AUGUST: tuple[str, int, float] = ("August", 120, 14.5)
# The cells of the Summary sheet that add the months up: the revenue and the units.
SUMMARY_TOTALS: tuple[str, ...] = ("B1", "B2")
OLD_NAME: str = "Northwind Traders"
NEW_NAME: str = "Northwind Partners"
# The word of the old name that the new one lacks: what is left of the old name anywhere holds it.
_OLD_WORD: str = "Traders"
# Where the old name is in the letter: the bold one, the italic one split over two runs, the table, the closing line
# and the header.
NAME_PLACES: int = 5
_LAST_MONTH_ROW: int = len(SALES_MONTHS) + 1
_GREETING: str = "Dear Ms. Larsen,"
_THANKS: str = "Thank you for your order with "
_QUESTIONS: str = "Questions about the delivery go to the customer care of "
# What a formula reads of a sheet: a cell (B9, $C$9), a range of cells (B2:B9, $B$2:$B$9) or a range of whole columns
# (B:C), with the sheet it is on before it (Data!B9, 'Data'!B2:B9). The name of a function (LOG10(, SUM() is none, and
# neither is a word that goes on after the letters and digits.
_REFERENCE: re.Pattern[str] = re.compile(
    r"(?<![\w$.'!])(?:(?P<sheet>'[^']+'|[A-Za-z_][\w.]*)!)?"
    r"(?:\$?[A-Z]{1,3}\$?(?P<first>\d+)(?::\$?[A-Z]{1,3}\$?(?P<last>\d+))?(?![\w(])|\$?[A-Z]{1,3}:\$?[A-Z]{1,3}(?![\w(]))"
)
_DATA_SHEET: str = "data"


def _plain(text: str) -> Run:
    return Run(text=text, bold=False, italic=False)


def sales_workbook() -> bytes:
    """``sales.xlsx`` as the task starts: the totals of the Summary sheet hold the results of the seven months."""
    data: tuple[tuple[str | int | float, ...], ...] = (("Month", "Units", "Price"), *SALES_MONTHS)
    revenue: float = sum(units * price for _, units, price in SALES_MONTHS)
    units: int = sum(units for _, units, _ in SALES_MONTHS)
    last: int = _LAST_MONTH_ROW
    summary: tuple[tuple[str | Formula, ...], ...] = (
        ("Revenue", Formula(text=f"SUMPRODUCT(Data!B2:B{last},Data!C2:C{last})", result=revenue)),
        ("Units", Formula(text=f"SUM(Data!B2:B{last})", result=units)),
    )
    return workbook([Sheet(name="Data", rows=data), Sheet(name="Summary", rows=summary)])


def letter_document() -> bytes:
    """``letter.docx`` as the task starts, the old name in each of :data:`NAME_PLACES` places."""
    body: tuple[Paragraph | Table, ...] = (
        Paragraph(runs=(_plain(_GREETING),)),
        Paragraph(
            runs=(
                _plain(_THANKS),
                Run(text=OLD_NAME, bold=True, italic=False),
                _plain(". Your goods leave our warehouse on Monday."),
            )
        ),
        Paragraph(
            runs=(
                _plain(_QUESTIONS),
                Run(text="Northwind ", bold=False, italic=True),
                Run(text="Traders", bold=False, italic=True),
                _plain(", which answers within a day."),
            )
        ),
        Table(rows=(("Supplier", OLD_NAME), ("Order", "4471"))),
        Paragraph(runs=(_plain("Kind regards,"),)),
        Paragraph(runs=(_plain(f"The team at {OLD_NAME}"),)),
    )
    return document(body, [Paragraph(runs=(_plain(f"{OLD_NAME} - order confirmation"),))])


def reaches(formula: str, row: int) -> bool:
    """Whether ``formula`` reads a cell of ``row`` of the Data sheet: through a range of cells that holds it
    (``Data!B2:B9``), a range of whole columns (``Data!B:C``) or the cell itself (``Data!B9``). A reference with no
    sheet is of the sheet the formula is on, the Summary sheet, and one of another sheet is that sheet's: neither reads
    a month (item 558: ``=SUM(Data!B2:B8)+B9`` leaves August out)."""
    for reference in _REFERENCE.finditer(formula):
        sheet: str | None = reference.group("sheet")
        if sheet is None or sheet.strip("'").casefold() != _DATA_SHEET:
            continue
        first: str | None = reference.group("first")
        if first is None:
            return True
        last: str = reference.group("last") or first
        if min(int(first), int(last)) <= row <= max(int(first), int(last)):
            return True
    return False


def sales_problems(book: Workbook) -> list[str]:
    """What is wrong with the sales workbook after the task: the seven months as they were, August after them, and
    each total of the Summary sheet a formula that reads August's row."""
    data = book["Data"]
    summary = book["Summary"]
    rows: list[tuple[object, ...]] = [tuple(cell.value for cell in row) for row in data.iter_rows(min_row=2, max_col=3)]
    problems: list[str] = []
    if rows[: len(SALES_MONTHS)] != list(SALES_MONTHS):
        problems.append(f"the months on the Data sheet are no longer the seven of before: {rows[: len(SALES_MONTHS)]}")
    found: list[int] = [number for number, row in enumerate(rows, start=2) if row == AUGUST]
    if not found:
        return [*problems, f"no row of the Data sheet holds {AUGUST}"]
    for reference in SUMMARY_TOTALS:
        value: object = summary[reference].value
        if not (isinstance(value, str) and value.startswith("=")):
            problems.append(f"Summary!{reference} holds no formula but {value!r}")
        elif not reaches(value, found[0]):
            problems.append(f"Summary!{reference} ({value}) does not read row {found[0]} of the Data sheet")
    return problems


def _every_paragraph(letter: DocumentObject) -> Iterator[DocxParagraph]:
    """The paragraphs of the body, of its tables and of the headers."""
    yield from letter.paragraphs
    for table in letter.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for section in letter.sections:
        yield from section.header.paragraphs


def _formatting_problem(paragraphs: Sequence[DocxParagraph], start: str, kind: Literal["bold", "italic"]) -> str | None:
    """What is wrong with the formatting of the new name in the body paragraph that begins with ``start``: the name
    has to be ``kind`` and the words around it not."""
    paragraph: DocxParagraph | None = next((entry for entry in paragraphs if entry.text.startswith(start)), None)
    if paragraph is None or NEW_NAME not in paragraph.text:
        return f"no paragraph starts with {start!r} and holds {NEW_NAME!r}"
    at: int = paragraph.text.index(NEW_NAME)
    marks: list[bool] = [bool(getattr(run, kind)) for run in paragraph.runs for _ in run.text]
    inside: list[bool] = marks[at : at + len(NEW_NAME)]
    outside: list[bool] = [*marks[:at], *marks[at + len(NEW_NAME) :]]
    if all(inside) and not any(outside):
        return None
    return f"in {paragraph.text!r} the new name is not {kind} as the old one was, or the words around it became {kind}"


def letter_problems(letter: DocumentObject) -> list[str]:
    """What is wrong with the letter after the task: the old name anywhere, the new one not in each place the old one
    was, the formatting of the two places that had one, and the paragraphs of the body that did not hold it."""
    texts: list[str] = [paragraph.text for paragraph in _every_paragraph(letter)]
    problems: list[str] = [f"{text!r} still holds {_OLD_WORD!r}" for text in texts if _OLD_WORD in text]
    count: int = sum(text.count(NEW_NAME) for text in texts)
    if count != NAME_PLACES:
        problems.append(f"{NEW_NAME!r} is in {count} places, not {NAME_PLACES}")
    body: list[DocxParagraph] = list(letter.paragraphs)
    if not body or body[0].text != _GREETING:
        problems.append(f"the letter no longer starts with {_GREETING!r}")
    places: tuple[tuple[str, Literal["bold", "italic"]], ...] = ((_THANKS, "bold"), (_QUESTIONS, "italic"))
    for start, kind in places:
        problem: str | None = _formatting_problem(body, start, kind)
        if problem is not None:
            problems.append(problem)
    return problems


def main(arguments: Sequence[str]) -> int:
    """Check ``sales`` or ``letter`` at the path after it; print what is wrong and return 1 when anything is."""
    if len(arguments) != 2 or arguments[0] not in ("sales", "letter"):
        print("usage: python -m dotagent.evals.office_tasks sales|letter <path>")
        return 2
    path: Path = Path(arguments[1])
    problems: list[str] = (
        sales_problems(load_workbook(path)) if arguments[0] == "sales" else letter_problems(Document(str(path)))
    )
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
