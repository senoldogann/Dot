"""The screen as the window server sees it: displays, windows, the pointer, and whether the screen can be used."""

from collections.abc import Mapping, Sequence

import Quartz

from dotagent.core.errors import DesktopError
from dotagent.desktop.types import DisplayInfo, Point, Rect, ScreenState, WindowInfo

_MAX_DISPLAYS: int = 16
_SCREENSAVER_OWNER: str = "ScreenSaverEngine"
_LOCKED_KEY: str = "CGSSessionScreenIsLocked"
_ORDINARY_LAYER: int = 0  # application windows; overlays, the Dock and the menu bar sit on other layers


def displays() -> tuple[DisplayInfo, ...]:
    """The active displays in global points; secondary displays may have negative origins."""
    status, ids, _count = Quartz.CGGetActiveDisplayList(_MAX_DISPLAYS, None, None)
    if status != 0:
        raise DesktopError("cannot list the displays", {"status": int(status)})
    main_id: int = int(Quartz.CGMainDisplayID())
    result: list[DisplayInfo] = []
    for display_id in ids:
        bounds = Quartz.CGDisplayBounds(display_id)
        rect: Rect = Rect(
            float(bounds.origin.x), float(bounds.origin.y), float(bounds.size.width), float(bounds.size.height)
        )
        result.append(DisplayInfo(display_id=int(display_id), bounds=rect, main=int(display_id) == main_id))
    return tuple(result)


def main_display() -> DisplayInfo:
    """The display with the menu bar; it is the one every screenshot shows."""
    for display in displays():
        if display.main:
            return display
    raise DesktopError("no main display: the Mac has no active screen", {})


def pointer_location() -> Point:
    location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return Point(float(location.x), float(location.y))


def _number(raw: Mapping[str, object], key: str) -> float:
    value: object = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DesktopError("the window list holds an entry without a number", {"key": key, "value": repr(value)})
    return float(value)


def _window_of(raw: Mapping[str, object]) -> WindowInfo:
    bounds: object = raw.get("kCGWindowBounds")
    if not isinstance(bounds, Mapping):
        raise DesktopError("the window list holds an entry without bounds", {"entry": repr(raw)})
    return WindowInfo(
        window_id=int(_number(raw, "kCGWindowNumber")),
        pid=int(_number(raw, "kCGWindowOwnerPID")),
        owner=str(raw.get("kCGWindowOwnerName", "")),
        title=str(raw.get("kCGWindowName", "")),
        bounds=Rect(_number(bounds, "X"), _number(bounds, "Y"), _number(bounds, "Width"), _number(bounds, "Height")),
        layer=int(_number(raw, "kCGWindowLayer")),
        alpha=float(_number(raw, "kCGWindowAlpha")) if "kCGWindowAlpha" in raw else 1.0,
        on_screen=bool(raw.get("kCGWindowIsOnscreen", False)),
    )


def _windows(option: int) -> tuple[WindowInfo, ...]:
    raw = Quartz.CGWindowListCopyWindowInfo(option, Quartz.kCGNullWindowID)
    if raw is None:
        raise DesktopError("the window server returned no window list", {"option": option})
    return tuple(_window_of(entry) for entry in raw)


def windows_on_screen() -> tuple[WindowInfo, ...]:
    """Visible windows, frontmost first (about 0.2 ms)."""
    return _windows(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements)


def windows_everywhere() -> tuple[WindowInfo, ...]:
    """Every window including other Spaces, minimised and hidden ones (``on_screen`` tells them apart)."""
    return _windows(Quartz.kCGWindowListOptionAll)


def window_at(point: Point, windows: Sequence[WindowInfo]) -> WindowInfo | None:
    """The topmost ordinary window under ``point``; ``windows`` must be ordered frontmost first. Windows of other
    layers are ignored: several of them cover the whole screen and still let every click through."""
    for window in windows:
        if window.layer != _ORDINARY_LAYER or window.alpha <= 0.0:
            continue
        if window.bounds.width <= 1 or window.bounds.height <= 1:
            continue
        if window.bounds.contains(point):
            return window
    return None


def screen_state(windows: Sequence[WindowInfo]) -> ScreenState:
    """Whether the screen can be seen and used right now. Raises when there is no GUI login session at all."""
    session = Quartz.CGSessionCopyCurrentDictionary()
    if session is None:
        raise DesktopError("there is no GUI login session: the desktop cannot be used from here", {})
    if bool(session.get(_LOCKED_KEY, False)):
        return "locked"
    if bool(Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID())):
        return "display_asleep"
    if any(window.owner == _SCREENSAVER_OWNER for window in windows):
        return "screensaver"
    return "ready"


def model_scale(main: Rect, max_edge: int) -> float:
    """Model units per point: 1 unless the screen is larger than ``max_edge`` on its long side."""
    return min(1.0, max_edge / max(main.width, main.height))
