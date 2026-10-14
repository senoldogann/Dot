"""The files the panel beside a chat shows (item 195): which places it shows files of, told by the file once its links
are followed; how it shows each, told by the file's first bytes; a CSV table split at its delimiter and cut at the cells
shown; a long text cut; and a PDF's pages drawn as pictures at the widths asked, in their proportions."""

import io
from pathlib import Path

import openpyxl
import pytest
from PIL import Image

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.office import SheetCell, WorkbookContent
from dotagent.previews import shown
from dotagent.previews.paths import located_file, opens_on_mac, shown_kind
from dotagent.previews.pdfpages import PageSize, page_png, page_sizes
from dotagent.previews.shown import TablePreview, TextPreview, shown_file


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _refusal(raw: str, home: Path, workspace: Path) -> str:
    with pytest.raises(InvalidValueError) as refused:
        located_file(raw, home, workspace)
    return refused.value.message


def test_the_panel_shows_files_of_the_workspace_and_of_the_home_folder_outside_hidden_folders_and_library(
    tmp_path: Path,
) -> None:
    home: Path = tmp_path / "home"
    workspace: Path = home / "Library" / "Application Support" / "Dot" / "workspace"
    plan: Path = _file(home / "Documents" / "plan.txt", b"plan")
    synced: Path = _file(home / "Library" / "CloudStorage" / "OneDrive-Personal" / "budget.xlsx", b"PK\x03\x04")
    icloud: Path = _file(home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "notes.txt", b"notes")
    made: Path = _file(workspace / "report.txt", b"report")
    _file(home / ".ssh" / "key.txt", b"key")
    _file(home / "Library" / "Preferences" / "settings.txt", b"settings")
    _file(home / "Library" / "Application Support" / "Dot" / "api-token", b"token")
    elsewhere: Path = _file(tmp_path / "elsewhere.txt", b"elsewhere")
    (home / "Documents" / "away.txt").symlink_to(elsewhere)

    for path in (plan, synced, icloud, made):
        assert located_file(str(path), home, workspace) == path.resolve()
    assert located_file("~/Documents/plan.txt", home, workspace) == plan.resolve()
    assert _refusal("~/.ssh/key.txt", home, workspace) == "the file is in a hidden folder"
    assert _refusal("~/Library/Preferences/settings.txt", home, workspace) == (
        "the file is among the files of an application"
    )
    assert _refusal("~/Library/Application Support/Dot/api-token", home, workspace) == (
        "the file is among the files of an application"
    )
    # A link is judged by where it leads.
    assert _refusal("~/Documents/away.txt", home, workspace) == "the file is outside your home folder"
    assert _refusal(str(elsewhere), home, workspace) == "the file is outside your home folder"
    assert _refusal("Documents/plan.txt", home, workspace) == "the path is neither absolute nor under ~"
    assert _refusal("~/Documents", home, workspace) == "the path is no file"
    with pytest.raises(NotFoundError):
        located_file("~/Documents/gone.txt", home, workspace)


def test_how_the_panel_shows_a_file_is_told_by_its_first_bytes(tmp_path: Path) -> None:
    package: bytes = b"PK\x03\x04" + bytes(20)
    assert shown_kind(tmp_path / "plan.docx", package) == "document"
    assert shown_kind(tmp_path / "budget.XLSM", package) == "workbook"
    assert shown_kind(tmp_path / "chart.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image"
    assert shown_kind(tmp_path / "scan", b"%PDF-1.7\n") == "pdf"
    assert shown_kind(tmp_path / "rows.CSV", b"a;b\n1;2\n") == "table"
    # A table of another encoding than UTF-8 is still a table.
    assert shown_kind(tmp_path / "rows.csv", "Päivä;Summa\n".encode("cp1252")) == "table"
    assert shown_kind(tmp_path / "page.html", b"<!doctype html><p>hi</p>") == "text"
    assert shown_kind(tmp_path / "Makefile", b"all:\n\techo hi\n") == "text"
    with pytest.raises(InvalidValueError) as old:
        shown_kind(tmp_path / "budget.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    assert old.value.message == "a .xls file is of the format of before 2007; save it as .xlsx"
    with pytest.raises(InvalidValueError) as binary:
        shown_kind(tmp_path / "tool.bin", b"\x00\x01\x02")
    assert binary.value.message == "the panel does not show a file of this kind"
    assert [opens_on_mac(Path(name)) for name in ("a.docx", "a.XLSX", "a.pdf", "a.csv", "a.sh", "a.html", "a.app")] == [
        True,
        True,
        True,
        True,
        False,
        False,
        False,
    ]


def test_a_table_is_split_at_its_delimiter_and_cut_at_the_cells_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows: Path = _file(tmp_path / "rows.csv", "﻿Item;Amount\nRent;1200,50\n;300\nFood;40\n".encode())
    table = shown_file(rows).preview
    assert isinstance(table, TablePreview)
    assert table.sheet.cells == (
        SheetCell(row=1, column=1, value="Item", formula=None),
        SheetCell(row=1, column=2, value="Amount", formula=None),
        SheetCell(row=2, column=1, value="Rent", formula=None),
        SheetCell(row=2, column=2, value="1200,50", formula=None),
        SheetCell(row=3, column=2, value="300", formula=None),
        SheetCell(row=4, column=1, value="Food", formula=None),
        SheetCell(row=4, column=2, value="40", formula=None),
    )
    assert table.sheet.cut_at_row is None
    monkeypatch.setattr(shown, "SHOWN_CELLS", 4)
    cut = shown_file(rows).preview
    assert isinstance(cut, TablePreview)
    assert (len(cut.sheet.cells), cut.sheet.cut_at_row) == (4, 3)

    monkeypatch.setattr(shown, "TEXT_BYTES", 8)
    text = shown_file(_file(tmp_path / "notes.txt", "Päivää kaikille".encode())).preview
    # Cut after eight bytes, the eighth of them the first of a character's two, which is left out.
    assert text == TextPreview(kind="text", text="Päivä", cut=True)


def test_a_workbook_shows_its_cells_with_their_formulas(tmp_path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = 2
    sheet["A2"] = "=A1*2"
    workbook.save(tmp_path / "sums.xlsx")
    file = shown_file(tmp_path / "sums.xlsx")
    assert file.opens
    assert isinstance(file.preview, WorkbookContent)
    assert file.preview.sheets[0].cells == (
        SheetCell(row=1, column=1, value="2", formula=None),
        SheetCell(row=2, column=1, value=None, formula="=A1*2"),
    )


def test_a_pdf_page_is_drawn_at_the_width_asked_in_its_proportions(tmp_path: Path) -> None:
    pages: list[Image.Image] = [Image.new("RGB", (612, 792), (255, 255, 255)), Image.new("RGB", (792, 612))]
    out: io.BytesIO = io.BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=72.0)
    pdf: Path = _file(tmp_path / "report.pdf", out.getvalue())
    assert page_sizes(pdf) == (PageSize(width=612.0, height=792.0), PageSize(width=792.0, height=612.0))
    drawn: Image.Image = Image.open(io.BytesIO(page_png(pdf, 2, 600)))
    assert (drawn.format, drawn.size) == ("PNG", (600, 464))
    with pytest.raises(InvalidValueError) as odd:
        page_png(pdf, 1, 601)
    assert odd.value.message == "a page is drawn at one of the widths of PAGE_WIDTHS"
    with pytest.raises(InvalidValueError) as missing:
        page_png(pdf, 3, 600)
    assert missing.value.message == "the PDF has no such page"
