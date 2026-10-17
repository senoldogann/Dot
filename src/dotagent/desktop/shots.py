"""Screenshots of the main display, in the coordinates the model clicks in.

A screenshot shows the whole screen at one pixel per model unit, or a zoomed region of it at the display's own sharpness.
Windows of password managers are painted black before the picture leaves this module. Everything here blocks and runs on
the session's worker thread.
"""

from collections.abc import Sequence

from PIL import Image

from dotagent.config import DesktopConfig
from dotagent.core.errors import DesktopError
from dotagent.desktop import apps, capture, perms, safety, screen
from dotagent.desktop.types import DisplayInfo, Point, Rect, ScreenshotView, WindowInfo

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
    """The pixels of ``area`` (points) with password manager windows blacked out, and how many were."""
    perms.require_screen_recording()
    picture: Image.Image = capture.capture_native(area, config.capture_timeout_s)
    return capture.black_out(picture, area, safety.hidden_regions(windows))


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
    )


def _region_in_points(region: Rect, scale: float, main: DisplayInfo) -> Rect:
    """``region`` (model units, whole numbers) as points, checked to lie on the main display."""
    if region.width < MIN_REGION_UNITS or region.height < MIN_REGION_UNITS:
        raise DesktopError(
            f"the region is too small; each side needs at least {MIN_REGION_UNITS} units",
            {"width": region.width, "height": region.height},
        )
    area: Rect = Rect(region.x / scale, region.y / scale, region.width / scale, region.height / scale)
    bounds: Rect = main.bounds
    inside: bool = (
        area.x >= bounds.x
        and area.y >= bounds.y
        and area.x + area.width <= bounds.x + bounds.width + 0.5
        and area.y + area.height <= bounds.y + bounds.height + 0.5
    )
    if not inside:
        raise DesktopError(
            "the region leaves the screen; use coordinates from a full screenshot",
            {
                "region": f"({region.x:g}, {region.y:g}) {region.width:g}x{region.height:g}",
                "screen": f"{bounds.width * scale:g}x{bounds.height * scale:g}",
            },
        )
    return area


def take_region(config: DesktopConfig, region: Rect) -> ScreenshotView:
    """A zoomed look at ``region`` (model units): as sharp as the display delivers, at most ``screenshot_max_edge``."""
    main: DisplayInfo = screen.main_display()
    scale: float = screen.model_scale(main.bounds, config.screenshot_max_edge)
    whole: Rect = Rect(round(region.x), round(region.y), round(region.width), round(region.height))
    area: Rect = _region_in_points(whole, scale, main)
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
    )
