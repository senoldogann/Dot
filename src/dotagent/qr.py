"""QR codes of the addresses a phone opens in Settings › Phone (item 421), made by macOS's own Core Image generator as
PNG: a phone's camera opens them without anything typed."""

import Quartz
from AppKit import NSBitmapImageRep, NSPNGFileType
from Foundation import NSData

from dotagent.core.errors import InvalidValueError

# Each module of the code this many pixels wide: sharp on a Mac's screen, read at once by a phone held before it.
_MODULE_PX: int = 8
# Medium error correction: the code stays readable with a glare or a smudge on part of the screen.
_CORRECTION: str = "M"
# The transform is made here, once, on the thread that imports this module: PyObjC binds a Core Graphics function the
# first time the name is read, and two worker threads reading a name that is still unbound at once make the second one
# fail with a KeyError. Settings › Phone asks for two codes together, each on a worker thread.
_SCALE = Quartz.CGAffineTransformMakeScale(_MODULE_PX, _MODULE_PX)


def qr_png(text: str) -> bytes:
    """``text`` as a QR code: a PNG of dark modules on a light ground."""
    data: bytes = text.encode("utf-8")
    generator = Quartz.CIFilter.filterWithName_("CIQRCodeGenerator")
    generator.setValue_forKey_(NSData.dataWithBytes_length_(data, len(data)), "inputMessage")
    generator.setValue_forKey_(_CORRECTION, "inputCorrectionLevel")
    image = generator.outputImage()
    if image is None:
        raise InvalidValueError("macOS made no QR code of this text", {"chars": len(text)})
    scaled = image.imageByApplyingTransform_(_SCALE)
    picture = NSBitmapImageRep.alloc().initWithCIImage_(scaled)
    return bytes(picture.representationUsingType_properties_(NSPNGFileType, {}))
