"""The text on a screenshot, read on this Mac by macOS's Vision framework (item 701), with where each line is.

A window that shows nothing to accessibility (a canvas, a game, an Electron app that builds no tree) can still be read
and clicked: each line of text comes with its box in the picture's own pixels, which the caller turns into the
coordinates ``desktop_act`` takes. Nothing leaves the Mac. Everything here blocks and runs on the session's worker
thread; the picture it is given is the one the model is shown, with what the screenshot hides already blacked out, so
nothing hidden there is read.
"""

import io
from dataclasses import dataclass

import Quartz
from Foundation import NSData
from PIL import Image

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger

_log = get_logger("dotagent.desktop.ocr")

# Lines Vision is less sure of than this are left out: on a screen they are mostly icons read as letters.
_MIN_CONFIDENCE: float = 0.3


@dataclass(frozen=True, slots=True)
class TextLine:
    """One line of text Vision read, with its box in the picture's pixels (origin top left)."""

    text: str
    x: float
    y: float
    width: float
    height: float


def _cg_image(picture: Image.Image) -> object:
    buffer: io.BytesIO = io.BytesIO()
    picture.convert("RGB").save(buffer, "PNG")
    data = NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))
    source = Quartz.CGImageSourceCreateWithData(data, None)
    image = None if source is None else Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise DesktopError("the screenshot could not be handed to Vision", {})
    return image


def read_lines(picture: Image.Image, languages: tuple[str, ...] = ()) -> tuple[TextLine, ...]:
    """The lines of text in ``picture``, top to bottom and then left to right, read at Vision's accurate level without
    its language correction (names, codes and addresses stay as they are written). ``languages`` are the ones Vision
    tries first; empty, it chooses."""
    # Vision is loaded by the first screenshot read, not by the daemon's start (item 729).
    import Vision

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    if languages:
        request.setRecognitionLanguages_(list(languages))
    else:
        request.setAutomaticallyDetectsLanguage_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(_cg_image(picture), {})
    done, error = handler.performRequests_error_([request], None)
    if not done:
        raise DesktopError("Vision could not read the screenshot", {"error": str(error)})
    width, height = picture.size
    lines: list[TextLine] = []
    for observation in request.results() or ():
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        best = candidates[0]
        text: str = str(best.string()).strip()
        if not text or best.confidence() < _MIN_CONFIDENCE:
            continue
        box = observation.boundingBox()
        # Vision's box is a share of the picture, its origin at the bottom left.
        left: float = box.origin.x * width
        top: float = (1.0 - box.origin.y - box.size.height) * height
        lines.append(TextLine(text=text, x=left, y=top, width=box.size.width * width, height=box.size.height * height))
    lines.sort(key=lambda line: (round(line.y / 8), line.x))
    _log.debug("ocr_read", lines=len(lines), width=width, height=height)
    return tuple(lines)
