"""``read_file`` pages the text of a PDF by its lines, as it pages a text file, so no part of a long one is out of reach
(item 176)."""

from pathlib import Path

from dotagent.tools.base import ToolResult
from dotagent.tools.files import _read_blocking

BUDGET: int = 20_000
MAX_EDGE: int = 1568
QUALITY: int = 80


def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF whose text layer holds ``lines``, one under another."""
    shown: str = " T* ".join(f"({line}) Tj" for line in lines)
    stream: bytes = f"BT /F1 9 Tf 11 TL 36 770 Td {shown} ET".encode("ascii")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out: bytearray = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref: int = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_a_pdf_is_read_from_the_line_asked_for_with_the_line_to_go_on_from(tmp_path: Path) -> None:
    path: Path = tmp_path / "ledger.pdf"
    path.write_bytes(_pdf([f"Entry {number} of the ledger" for number in range(1, 61)]))
    result: ToolResult = _read_blocking(path, 21, 10, BUDGET, MAX_EDGE, QUALITY)
    assert not result.is_error, result.text
    shown: list[str] = result.text.splitlines()
    assert shown[0] == "    21\tEntry 21 of the ledger", result.text
    assert shown[-2:] == ["    30\tEntry 30 of the ledger", "[showing lines 21-30; pass offset=31 to continue]"]
    last: ToolResult = _read_blocking(path, 51, 10, BUDGET, MAX_EDGE, QUALITY)
    assert last.text.splitlines()[-1] == "[lines 51-60; end of file]", last.text
