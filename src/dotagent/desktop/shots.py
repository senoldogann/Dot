"""Screenshots of the main display, in the coordinates the model clicks in.

A screenshot shows the whole screen at one pixel per model unit, or a zoomed region of it at the display's own
sharpness. Windows of password managers are painted black before the picture leaves this module, and Dot's own
while a secret of its vault may be on the screen. Everything here blocks
and runs on the session's worker thread.
"""

import math
from collections.abc import Sequence

from PIL import Image

from dotagent.config import DesktopConfig
from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.desktop import apps, capture, ocr, perms, safety, screen
from dotagent.desktop.types import DisplayInfo, Point, Rect, ScreenshotView, ScreenText, WindowInfo
from dotagent.vault import shown

_log = get_logger("dotagent.desktop.shots")

MIN_REGION_UNITS: int = 16
_NO_APP: str = "(none)"


def _frontmost_name(windows: Sequence[WindowInfo]) -> str:
    """The name of the application that has the keyboard focus."""
    pid: int | None = apps.frontmost_pid()
    if pid is None:
        return _NO_APP
    for window in windows:
        if window.pid == pid and window.layer == 0:
            return window.owner
    for app in apps.list_apps():
        if app.pid == pid:
            return app.name
    return f"pid {pid}"


def _pointer_units(scale: float) -> Point:
    pointer: Point = screen.pointer_location()
    return Point(pointer.x * scale, pointer.y * scale)


def _picture_of(config: DesktopConfig, area: Rect, windows: Sequence[WindowInfo]) -> tuple[Image.Image, int]:
    """The pixels of ``area`` (points) with password manager windows blacked out, and Dot's own while a secret of its
    vault may be on the screen (item 621), and how many were."""
    perms.require_screen_recording()
    picture: Image.Image = capture.capture_native(area, config.capture_timeout_s)
    hidden: tuple[Rect, ...] = safety.hidden_regions(windows, apps.bundle_ids(), own=shown.on_screen())
    return capture.black_out(picture, area, hidden)


def _text_of(config: DesktopConfig, picture: Image.Image, area: Rect) -> tuple[ScreenText, ...]:
    """The text Vision reads on ``picture``, which shows ``area`` (model units) with what is hidden blacked out
    already, at the centre of each line in model units (item 701); none when ``read_screen_text`` is off. A failure of
    Vision costs the text, not the screenshot."""
    if not config.read_screen_text:
        return ()
    try:
        lines: tuple[ocr.TextLine, ...] = ocr.read_lines(picture)
    except DesktopError as error:
        _log.warning("screen_text_unread", error=error.message)
        return ()
    across: float = area.width / picture.width
    down: float = area.height / picture.height
    return tuple(
        ScreenText(
            text=line.text,
            x=round(area.x + (line.x + line.width / 2) * across),
            y=round(area.y + (line.y + line.height / 2) * down),
        )
        for line in lines
    )


def take_screen(config: DesktopConfig) -> ScreenshotView:
    """The whole main display at one pixel per model unit."""
    main: DisplayInfo = screen.main_display()
    scale: float = screen.model_scale(main.bounds, config.screenshot_max_edge)
    windows: tuple[WindowInfo, ...] = screen.windows_on_screen()
    picture, redacted = _picture_of(config, main.bounds, windows)
    width, height = capture.full_screen_size(main.bounds, scale)
    pointer: Point = _pointer_units(scale)
    return ScreenshotView(
        image=capture.render_jpeg(picture, width, height, config.jpeg_quality),
        width=width,
        height=height,
        left=0,
        top=0,
        zoom=1.0,
        pointer_x=round(pointer.x),
        pointer_y=round(pointer.y),
        frontmost=_frontmost_name(windows),
        redacted=redacted,
        text_lines=_text_of(config, picture, Rect(0, 0, width, height)),
    )


def clip_region(region: Rect, screen_width: float, screen_height: float) -> Rect | None:
    """``region`` cut to the screen (0, 0, ``screen_width`` x ``screen_height``, model units) in whole units, the right
    and bottom edges rounded down so that they stay on it; ``None`` when nothing of it is on the screen."""
    left: int = max(0, math.ceil(region.x))
    top: int = max(0, math.ceil(region.y))
    right: int = min(math.floor(region.x + region.width), math.floor(screen_width))
    bottom: int = min(math.floor(region.y + region.height), math.floor(screen_height))
    if right <= left or bottom <= top:
        return None
    return Rect(left, top, right - left, bottom - top)


def _written(region: Rect) -> str:
    return f"({region.x:g}, {region.y:g}) {region.width:g}x{region.height:g}"


def _region_in_points(region: Rect, scale: float, main: DisplayInfo) -> tuple[Rect, Rect, str]:
    """``region`` (model units, whole numbers) as points, cut to the main display when it leaves it: the points, the
    region in units that is looked at, and a sentence that says what was cut (empty when nothing was). A region with
    nothing on the display is refused."""
    if region.width < MIN_REGION_UNITS or region.height < MIN_REGION_UNITS:
        raise DesktopError(
            f"the region is too small; each side needs at least {MIN_REGION_UNITS} units",
            {"width": region.width, "height": region.height},
        )
    bounds: Rect = main.bounds
    units: tuple[float, float] = (bounds.width * scale, bounds.height * scale)
    shown_region: Rect | None = clip_region(region, *units)
    if shown_region is None:
        raise DesktopError(
            "the region is not on the screen; use coordinates from a full screenshot",
            {"region": _written(region), "screen": f"{units[0]:g}x{units[1]:g}"},
        )
    if shown_region.width < MIN_REGION_UNITS or shown_region.height < MIN_REGION_UNITS:
        raise DesktopError(
            f"only a sliver of the region is on the screen (less than {MIN_REGION_UNITS} units on a side); "
            "use coordinates from a full screenshot",
            {"region": _written(region), "on_screen": _written(shown_region), "screen": f"{units[0]:g}x{units[1]:g}"},
        )
    area: Rect = Rect(
        shown_region.x / scale, shown_region.y / scale, shown_region.width / scale, shown_region.height / scale
    )
    note: str = (
        ""
        if shown_region == region
        else f"The region {_written(region)} left the screen ({units[0]:g}x{units[1]:g}); it was cut to "
        f"{_written(shown_region)}."
    )
    return area, shown_region, note


def take_region(config: DesktopConfig, region: Rect) -> ScreenshotView:
    """A zoomed look at ``region`` (model units): as sharp as the display delivers, at most ``screenshot_max_edge``."""
    main: DisplayInfo = screen.main_display()
    scale: float = screen.model_scale(main.bounds, config.screenshot_max_edge)
    whole: Rect = Rect(round(region.x), round(region.y), round(region.width), round(region.height))
    area, whole, clipped = _region_in_points(whole, scale, main)
    windows: tuple[WindowInfo, ...] = screen.windows_on_screen()
    picture, redacted = _picture_of(config, area, windows)
    width, height = capture.zoom_size(area, picture.size, config.screenshot_max_edge)
    pointer: Point = _pointer_units(scale)
    return ScreenshotView(
        image=capture.render_jpeg(picture, width, height, config.jpeg_quality),
        width=width,
        height=height,
        left=int(whole.x),
        top=int(whole.y),
        zoom=width / whole.width,
        pointer_x=round(pointer.x),
        pointer_y=round(pointer.y),
        frontmost=_frontmost_name(windows),
        redacted=redacted,
        text_lines=_text_of(config, picture, whole),
        clipped=clipped,
    )
