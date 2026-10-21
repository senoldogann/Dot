"""The icon of an application of this Mac, read from its bundle when the page asks for it (Customize › Plugins).

Apple's and Microsoft's artwork is theirs, not Dot's to ship: a tile of the page shows the icon of the application the
owner has installed, read from its bundle (``Contents/Info.plist`` names the ``.icns`` file in ``Contents/Resources``)
and drawn as a PNG, and the tile of an application that is not installed keeps its own mark."""

import io
import plistlib
from functools import lru_cache
from pathlib import Path
from xml.parsers.expat import ExpatError

from PIL import Image

from dotagent.core.errors import DesktopError
from dotagent.core.images import UNREADABLE_IMAGE
from dotagent.core.logs import get_logger

log = get_logger(__name__)

# The widest an icon is drawn, in pixels: twice what the largest tile shows, for a screen of 2x or more.
ICON_PX: int = 256
# The icons kept drawn; an application has one, so this is far more than the page lists.
_KEPT_ICONS: int = 32
_INFO_PLIST: str = "Contents/Info.plist"
_RESOURCES: str = "Contents/Resources"
_ICON_KEY: str = "CFBundleIconFile"
_ICNS: str = ".icns"


def icon_file(bundle: Path) -> Path | None:
    """The ``.icns`` file the ``Info.plist`` of the application ``bundle`` names as its icon; ``None`` when the bundle
    has no ``Info.plist``, names no icon file, names one that is not there (an icon kept only in an asset catalogue), or
    has an ``Info.plist`` that cannot be read (said in the log)."""
    plist: Path = bundle / _INFO_PLIST
    if not plist.is_file():
        return None
    try:
        info: object = plistlib.loads(plist.read_bytes())
    except (plistlib.InvalidFileException, ExpatError, ValueError) as error:
        log.warning("app_info_unreadable", bundle=str(bundle), error=str(error))
        return None
    name: object = info.get(_ICON_KEY) if isinstance(info, dict) else None
    # The name is a file of the bundle's Resources: a path of its own would reach outside them.
    if not isinstance(name, str) or name == "" or Path(name).name != name:
        return None
    found: Path = bundle / _RESOURCES / (name if name.endswith(_ICNS) else name + _ICNS)
    return found if found.is_file() else None


# What an icon file that is no picture Pillow can read raises (item 515), and the SyntaxError of an icns it cannot
# parse.
_UNREADABLE_ICON: tuple[type[Exception], ...] = (*UNREADABLE_IMAGE, SyntaxError)


@lru_cache(maxsize=_KEPT_ICONS)
def _drawn(icns: Path, modified_ns: int) -> bytes:
    try:
        with Image.open(icns) as source:
            picture: Image.Image = source.convert("RGBA")
    except _UNREADABLE_ICON as error:
        raise DesktopError(
            "the application's icon could not be read", {"icns": str(icns), "error": str(error)}
        ) from error
    picture.thumbnail((ICON_PX, ICON_PX), Image.Resampling.LANCZOS)
    out: io.BytesIO = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()


def icon_png(icns: Path) -> bytes:
    """The icon in ``icns``, its largest size, as a PNG at most ``ICON_PX`` wide; drawn once for each version of the
    file (its time of change), so an update of the application brings its new icon. ``DesktopError`` when the file is
    no icon."""
    return _drawn(icns, icns.stat().st_mtime_ns)
