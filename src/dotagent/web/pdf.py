"""PDF text through PDFKit, the PDF reader of macOS."""

from typing import Protocol

from Foundation import NSData
from Quartz import PDFDocument

from dotagent.core.errors import WebError


class PdfDocumentLike(Protocol):
    def isLocked(self) -> bool: ...

    def pageCount(self) -> int: ...

    def string(self) -> str | None: ...


def pdf_to_text(data: bytes, url: str) -> str:
    """The text layer of a PDF. A scanned PDF has none, so the result can be empty."""
    opened: PdfDocumentLike | None = PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
    if opened is None:
        raise WebError("the PDF could not be opened", {"url": url, "bytes": len(data)})
    if opened.isLocked():
        raise WebError("the PDF is password protected", {"url": url, "pages": opened.pageCount()})
    return opened.string() or ""
