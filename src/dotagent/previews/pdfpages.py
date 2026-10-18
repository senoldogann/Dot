"""The pages of a PDF as pictures for the panel beside a chat, drawn by Core Graphics: a page shows as it is in every
browser, a phone's too, where a PDF in a frame shows its first page alone or nothing. Core Graphics draws on any
thread, so a page is drawn off the event loop."""

import io
from pathlib import Path

from Foundation import NSURL
from PIL import Image
from Quartz import (
    CGBitmapContextCreate,
    CGBitmapContextGetBytesPerRow,
    CGBitmapContextGetData,
    CGColorSpaceCreateDeviceRGB,
    CGContextConcatCTM,
    CGContextDrawPDFPage,
    CGContextFillRect,
    CGContextScaleCTM,
    CGContextSetRGBFillColor,
    CGPDFDocumentCreateWithURL,
    CGPDFDocumentGetNumberOfPages,
    CGPDFDocumentGetPage,
    CGPDFDocumentIsUnlocked,
    CGPDFPageGetBoxRect,
    CGPDFPageGetDrawingTransform,
    CGPDFPageGetRotationAngle,
    CGRectMake,
    kCGImageAlphaPremultipliedLast,
    kCGPDFCropBox,
)

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel

# The widths a page is drawn at, in pixels: a few, so that the panel asks for the same pictures again.
PAGE_WIDTHS: tuple[int, ...] = (600, 900, 1200, 1800)


class PageSize(FrozenModel):
    """The size of a page as it shows, turned as the page says, in points."""

    width: float
    height: float


def _document(path: Path) -> object:
    document: object | None = CGPDFDocumentCreateWithURL(NSURL.fileURLWithPath_(str(path)))
    if document is None:
        raise InvalidValueError("the PDF cannot be read", {"path": str(path)})
    if not CGPDFDocumentIsUnlocked(document):
        raise InvalidValueError("the PDF is locked with a password", {"path": str(path)})
    return document


def _size(page: object) -> PageSize:
    box = CGPDFPageGetBoxRect(page, kCGPDFCropBox)
    turned: bool = CGPDFPageGetRotationAngle(page) % 180 != 0
    width, height = (box.size.height, box.size.width) if turned else (box.size.width, box.size.height)
    return PageSize(width=float(width), height=float(height))


def page_sizes(path: Path) -> tuple[PageSize, ...]:
    """The size of each page of the PDF at ``path``. ``InvalidValueError`` when it cannot be read or is locked."""
    document: object = _document(path)
    return tuple(
        _size(CGPDFDocumentGetPage(document, number))
        for number in range(1, CGPDFDocumentGetNumberOfPages(document) + 1)
    )


# The tallest page drawn, in pixels: at the widest of PAGE_WIDTHS a bitmap of about 150 MB at most.
MOST_PAGE_HEIGHT: int = 20_000


def page_png(path: Path, number: int, width: int) -> bytes:
    """Page ``number`` (from 1) of the PDF at ``path`` as a PNG ``width`` pixels wide, on white."""
    if width not in PAGE_WIDTHS:
        raise InvalidValueError("a page is drawn at one of the widths of PAGE_WIDTHS", {"width": width})
    document: object = _document(path)
    pages: int = CGPDFDocumentGetNumberOfPages(document)
    if not 1 <= number <= pages:
        raise InvalidValueError("the PDF has no such page", {"path": str(path), "page": number, "pages": pages})
    page: object = CGPDFDocumentGetPage(document, number)
    shown: PageSize = _size(page)
    height: int = max(1, round(width * shown.height / shown.width))
    if height > MOST_PAGE_HEIGHT:
        # A page far taller than wide would take gigabytes as a bitmap (item 386): it is refused, not drawn.
        raise InvalidValueError(
            "the page is too tall to draw",
            {"path": str(path), "page": number, "width": width, "height": height, "most": MOST_PAGE_HEIGHT},
        )
    context: object = CGBitmapContextCreate(
        None, width, height, 8, 0, CGColorSpaceCreateDeviceRGB(), kCGImageAlphaPremultipliedLast
    )
    CGContextSetRGBFillColor(context, 1.0, 1.0, 1.0, 1.0)
    CGContextFillRect(context, CGRectMake(0, 0, width, height))
    # The transform turns the crop box as the page says and fits it to a box of the page's own size (it never scales
    # a page up); that box is then scaled to the picture.
    CGContextScaleCTM(context, width / shown.width, height / shown.height)
    CGContextConcatCTM(
        context,
        CGPDFPageGetDrawingTransform(page, kCGPDFCropBox, CGRectMake(0, 0, shown.width, shown.height), 0, True),
    )
    CGContextDrawPDFPage(context, page)
    stride: int = CGBitmapContextGetBytesPerRow(context)
    pixels: bytes = bytes(CGBitmapContextGetData(context).as_buffer(stride * height))
    picture: Image.Image = Image.frombuffer("RGBA", (width, height), pixels, "raw", "RGBA", stride, 1)
    out: io.BytesIO = io.BytesIO()
    picture.convert("RGB").save(out, format="PNG")
    return out.getvalue()
