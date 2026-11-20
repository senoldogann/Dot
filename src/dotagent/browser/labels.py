"""Ref marks on the screenshot a model is sent.

The picture kept for the owner is the viewport as it was taken. The one a model sees has a small box, holding the
element's ref from the latest aria snapshot, at the top-left of each interactive element that meets the viewport.
At most :data:`MARK_CAP` of them, in the order the snapshot lists them.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from dotagent.browser.snapshot import SnapshotLine, frame_origins

# How many refs one picture is asked to carry. Past this the first ones in the snapshot are marked and the rest are
# left to the snapshot's text: a page of hundreds of controls would otherwise be a field of badges.
MARK_CAP: int = 40
# Orange that JPEG quality 60 still leaves readable as itself, on the light controls a page usually draws.
_INK: tuple[int, int, int] = (255, 96, 0)
_TEXT: tuple[int, int, int] = (255, 255, 255)
_FONT_PX: int = 16
_PAD: int = 4

# What the model acts on by a ref. A heading, a paragraph or a box that only lays others out is not one of these, and
# a mark on it would spend the cap on something the model does not press.
_INTERACTIVE: frozenset[str] = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }
)


@dataclass(frozen=True, slots=True)
class _Badge:
    ref: str
    x: float
    y: float


def _in_view(x: float, y: float, width: float, height: float, view_width: int, view_height: int) -> bool:
    return x < view_width and x + width > 0 and y < view_height and y + height > 0


def _badges(lines: Sequence[SnapshotLine], view_width: int, view_height: int) -> tuple[_Badge, ...]:
    """The refs to mark, in snapshot order: interactive, in the viewport, at most :data:`MARK_CAP`."""
    found: list[_Badge] = []
    seen: set[str] = set()
    for line, origin in zip(lines, frame_origins(lines), strict=True):
        if len(found) >= MARK_CAP:
            break
        box = line.box
        ref: str | None = line.ref
        if (
            ref is None
            or ref in seen
            or line.role not in _INTERACTIVE
            or origin is None
            or box is None
            or box.width <= 0
            or box.height <= 0
        ):
            continue
        left: float = origin[0] + box.x
        top: float = origin[1] + box.y
        if not _in_view(left, top, box.width, box.height, view_width, view_height):
            continue
        seen.add(ref)
        found.append(_Badge(ref=ref, x=left, y=top))
    return tuple(found)


def _draw(
    painter: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    badge: _Badge,
    size: tuple[int, int],
) -> None:
    """A filled box holding ``badge``'s ref, its top-left on the element's, kept inside the picture."""
    text_box: tuple[float, float, float, float] = font.getbbox(badge.ref)
    width: int = round(text_box[2] - text_box[0]) + _PAD * 2
    height: int = round(text_box[3] - text_box[1]) + _PAD * 2
    x: int = min(max(0, round(badge.x)), max(0, size[0] - width))
    y: int = min(max(0, round(badge.y)), max(0, size[1] - height))
    painter.rectangle((x, y, x + width - 1, y + height - 1), fill=_INK)
    painter.text((x + _PAD - text_box[0], y + _PAD - text_box[1]), badge.ref, fill=_TEXT, font=font)


def mark_screenshot(data: bytes, lines: Sequence[SnapshotLine], width: int, height: int, quality: int) -> bytes:
    """``data`` with a ref badge on each interactive element of ``lines`` that meets the ``width`` by ``height``
    viewport, or empty when there is nothing to mark. ``data`` is not changed. A picture whose pixel size is not the
    viewport's is marked in the viewport's coordinates scaled to the picture, so a pixel of the mark still sits on the
    element."""
    badges: tuple[_Badge, ...] = _badges(lines, width, height)
    if not badges or width <= 0 or height <= 0:
        return b""
    with Image.open(io.BytesIO(data)) as opened:
        picture: Image.Image = opened.convert("RGB")
    scale_x: float = picture.width / width
    scale_y: float = picture.height / height
    scaled: tuple[_Badge, ...] = tuple(_Badge(badge.ref, badge.x * scale_x, badge.y * scale_y) for badge in badges)
    painter: ImageDraw.ImageDraw = ImageDraw.Draw(picture)
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.load_default(size=_FONT_PX)
    for badge in scaled:
        _draw(painter, font, badge, picture.size)
    buffer: io.BytesIO = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=quality, optimize=False)
    return buffer.getvalue()
