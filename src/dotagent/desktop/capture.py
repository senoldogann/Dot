"""Screen capture through ScreenCaptureKit: the pixels of any rectangle of the desktop, then a JPEG of a chosen size.

Capturing and encoding are separate so that the output size can follow the density the display really delivers.
"""

import io
import platform
import threading
from collections.abc import Callable, Sequence
from typing import Protocol

import Quartz
import ScreenCaptureKit as SCK
from PIL import Image, ImageDraw

from dotagent.core.errors import DesktopError, DesktopPermissionError
from dotagent.desktop.types import Rect

_USER_DECLINED: int = -3801  # SCStreamErrorUserDeclined: Screen Recording is not granted
_BITS_PER_PIXEL: int = 32
# ScreenCaptureKit only has the capture of a rectangle from macOS 15.2 on.
_RECT_CAPTURE: bytes = b"captureImageInRect:completionHandler:"


class NSErrorLike(Protocol):
    def domain(self) -> str: ...

    def code(self) -> int: ...

    def localizedDescription(self) -> str: ...


type Completion = Callable[[object | None, NSErrorLike | None], None]


def _await_image(start: Callable[[Completion], None], timeout_s: float, what: str) -> object:
    """Run a completion-handler API from plain Python (no run loop needed) and wait for its answer."""
    done: threading.Event = threading.Event()
    box: list[tuple[object | None, NSErrorLike | None]] = []

    def completion(image: object | None, error: NSErrorLike | None) -> None:
        box.append((image, error))
        done.set()

    start(completion)
    if not done.wait(timeout_s):
        raise DesktopError(f"{what}: ScreenCaptureKit did not answer in time", {"api": what, "timeout_s": timeout_s})
    image, error = box[0]
    if error is not None:
        context: dict[str, str | int] = {
            "api": what,
            "domain": str(error.domain()),
            "code": int(error.code()),
            "description": str(error.localizedDescription()),
        }
        if int(error.code()) == _USER_DECLINED:
            raise DesktopPermissionError("macOS has not granted Screen Recording to the app that runs Dot", context)
        raise DesktopError(f"{what} failed", context)
    if image is None:
        raise DesktopError(f"{what} returned neither an image nor an error", {"api": what})
    return image


def _to_picture(cg_image: object) -> Image.Image:
    width: int = int(Quartz.CGImageGetWidth(cg_image))
    height: int = int(Quartz.CGImageGetHeight(cg_image))
    bits: int = int(Quartz.CGImageGetBitsPerPixel(cg_image))
    if bits != _BITS_PER_PIXEL:
        raise DesktopError("ScreenCaptureKit delivered an unexpected pixel format", {"bits_per_pixel": bits})
    stride: int = int(Quartz.CGImageGetBytesPerRow(cg_image))  # rows are padded: the stride must be passed explicitly
    data: bytes = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg_image)))
    return Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", stride, 1)


def rect_capture_supported() -> bool:
    """Whether this macOS can capture a rectangle of the screen; ScreenCaptureKit has that from macOS 15.2 on."""
    return bool(SCK.SCScreenshotManager.respondsToSelector_(_RECT_CAPTURE))


def capture_native(region: Rect, timeout_s: float) -> Image.Image:
    """The pixels of ``region`` (global points) at the display's own density; takes about 20 ms."""
    if not rect_capture_supported():
        raise DesktopError("screen capture needs macOS 15.2 or later", {"macos": platform.mac_ver()[0]})
    rectangle = Quartz.CGRectMake(region.x, region.y, region.width, region.height)

    def start(completion: Completion) -> None:
        SCK.SCScreenshotManager.captureImageInRect_completionHandler_(rectangle, completion)

    return _to_picture(_await_image(start, timeout_s, "captureImageInRect"))


def black_out(picture: Image.Image, area: Rect, hidden: Sequence[Rect]) -> tuple[Image.Image, int]:
    """``picture`` (the pixels of ``area``) with the parts that show ``hidden`` regions painted black, and how many of
    the regions were in the picture. The picture itself is left as it is."""
    inside: list[Rect] = [region for region in hidden if region.intersects(area)]
    if not inside:
        return picture, 0
    density: float = picture.width / area.width
    redacted: Image.Image = picture.copy()
    painter: ImageDraw.ImageDraw = ImageDraw.Draw(redacted)
    for region in inside:
        left: float = (region.x - area.x) * density
        top: float = (region.y - area.y) * density
        painter.rectangle((left, top, left + region.width * density, top + region.height * density), fill=(0, 0, 0))
    return redacted, len(inside)


def full_screen_size(main: Rect, scale: float) -> tuple[int, int]:
    """The output size of a whole-screen screenshot: one pixel per model unit."""
    return max(1, round(main.width * scale)), max(1, round(main.height * scale))


def zoom_size(region: Rect, native: tuple[int, int], max_edge: int) -> tuple[int, int]:
    """The output size of a zoomed screenshot: as sharp as the display delivers, at most ``max_edge`` on a side."""
    native_density: float = native[0] / region.width
    density: float = min(native_density, max_edge / max(region.width, region.height))
    return max(1, round(region.width * density)), max(1, round(region.height * density))


def render_jpeg(picture: Image.Image, width: int, height: int, quality: int) -> bytes:
    """``picture`` shrunk to ``width`` x ``height`` and encoded as JPEG."""
    fitted: Image.Image = picture
    if picture.size != (width, height):
        fitted = picture.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)
    buffer: io.BytesIO = io.BytesIO()
    fitted.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()
