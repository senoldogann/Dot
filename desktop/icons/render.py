"""Render Dot's mark (item 114) into the files that carry it: the favicon of the web UI (``static/favicon.svg``), the
web app's icons (``static/icons/icon-192.png``, ``icon-512.png``, the home screen's
``static/icons/touch-icon-{180,167,152,120}.png`` and the 180 px files at the root, items 410 and 550), the app icon of
the desktop shell (``app.png``, 1024 px, for ``npm run icons``) and its menu bar icons, one per state
(``tray-{state}.png``, 44 px, the 22 pt item at 2x).

On a phone (item 1069, the owner's choice of 2026-11-09) the icon is flat: the two eyes in white, alone, on a tile of
black, with no ball, light or gold, so that it reads as one plain sign on a home screen. The web app's icons, the home
screen's touch icons and the iPhone app's are drawn so (:func:`phone_tile_svg`), and the desktop app's on the macOS
tile (:func:`mac_tile_svg`); the favicon, the wordmark's o and the menu bar keep the ball, in the same black and white
(item 1081, the owner's ask that the new mark be everywhere).

The mark is a dot that looks (item 642, made flat by item 1081): a ball of black with the two capsule eyes of the
character's attentive rest pose (``static/js/character``) in white and a faint white hairline round it, so that it holds
on a light page, a dark one and a menu bar of either. The gradients keep their names and stops (one flat colour each,
the light's at nought), which every copy shares. The eyes are a
little larger than the engine draws them so they hold at 16 px. ``static/index.html`` and ``desktop/ui/down.html`` set
the same ball and eyes as the o of the wordmark; ``tests/unit/test_mark.py`` keeps them alike. Run this after a
change of the mark, from the repository: ``uv run python desktop/icons/render.py`` (with the Playwright Chromium
installed), then ``npm run icons`` in ``desktop/``."""

import asyncio
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

HERE: Path = Path(__file__).resolve().parent
REPO: Path = HERE.parents[1]
TRAY_DIR: Path = HERE.parent / "src-tauri" / "icons"
STATIC: Path = REPO / "src" / "dotagent" / "server" / "static"
# The ball's colour, the eyes' and the hairline's (item 1081: flat black and white; the names and the stops are item
# 642's, when the ball was obsidian lit at its upper left and the eyes champagne gold, so that every copy keeps them).
OBSIDIAN: tuple[tuple[str, str], ...] = (("0", "#000000"), ("0.32", "#000000"), ("1", "#000000"))
GOLD: tuple[tuple[str, str], ...] = (("0", "#ffffff"), ("0.45", "#ffffff"), ("1", "#ffffff"))
RIM: str = "#ffffff"
# The pastille of a state that waits for the owner: the character's notification blue.
PASTILLE: str = "#2496e8"
# One eye, a capsule 21 wide and 44 tall about its centre, in a disc of radius 100.
EYE: str = "M-10.5 -11.5a10.5 10.5 0 0 1 21 0v23a10.5 10.5 0 0 1 -21 0z"
# The attentive pose (the engine's frame at rest, its scale times 1.12): the eyes look at you, a little to the right.
ATTENTIVE: tuple[str, str] = (
    "matrix(1.098 -0.112 0.101 1.109 -17.3 -9.14)",
    "matrix(1.030 -0.045 0.101 1.109 37.32 -13.26)",
)
# The neutral pose (the character's signature glance, up and to the right): the menu bar's dot while Dot works.
GLANCE: tuple[str, str] = (
    "matrix(0.974 -0.370 0.504 0.941 21.42 -43.32)",
    "matrix(0.717 -0.067 0.504 0.941 62.98 -53.9)",
)
TRAY_PX: int = 44
APP_PX: int = 1024
WEB_PX: tuple[int, ...] = (192, 512)
# The sizes iOS asks for when the app is added to the home screen (items 410 and 550): the width of the touch icon
# under the icons folder, and the 180 px files at the root that it also tries by name.
TOUCH_PX: tuple[int, ...] = (120, 152, 167, 180)
ROOT_TOUCH: tuple[str, ...] = (
    "apple-touch-icon.png",
    "apple-touch-icon-precomposed.png",
    "apple-touch-icon-180x180.png",
    "apple-touch-icon-180x180-precomposed.png",
)


def _stops(stops: tuple[tuple[str, str], ...]) -> str:
    return "".join(f'<stop offset="{offset}" stop-color="{colour}"/>' for offset, colour in stops)


def defs() -> str:
    """The gradients of the mark: the obsidian of the ball, the gold of the eyes and the light on the ball."""
    return (
        f'<radialGradient id="mark-obsidian" cx="0.34" cy="0.28" r="0.8">{_stops(OBSIDIAN)}</radialGradient>'
        f'<linearGradient id="mark-gold" x1="0" y1="0" x2="0" y2="1">{_stops(GOLD)}</linearGradient>'
        '<radialGradient id="mark-shine" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#fff" '
        'stop-opacity="0"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
    )


def dot(eyes: tuple[str, str], rim: float = 2.0) -> str:
    """The ball and its eyes about the origin, radius 100, its light and the hairline of gold round it (``rim`` wide:
    thicker where the mark is small, so the line still shows)."""
    paths: str = "".join(f'<path d="{EYE}" transform="{matrix}" fill="url(#mark-gold)"/>' for matrix in eyes)
    return (
        f'<circle r="100" fill="url(#mark-obsidian)"/>{paths}'
        '<ellipse cx="-38" cy="-50" rx="34" ry="18" transform="rotate(-32 -38 -50)" fill="url(#mark-shine)"/>'
        f'<circle r="{100 - rim / 2}" fill="none" stroke="{RIM}" stroke-opacity="0.3" stroke-width="{rim}"/>'
    )


def mark_svg(eyes: tuple[str, str], pastille: bool, rim: float = 2.0) -> str:
    """The mark alone: the favicon, and the menu bar's icon in a state."""
    spot: str = f'<circle cx="74" cy="-74" r="24" fill="{PASTILLE}"/>' if pastille else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-100 -100 200 200"><defs>{defs()}</defs>'
        f"{dot(eyes, rim)}{spot}</svg>"
    )


# The eyes' middle in the attentive pose, which a phone's tile draws about the tile's middle.
EYES_MIDDLE: tuple[float, float] = (10.0, -11.0)


def phone_tile_svg(scale: float) -> str:
    """The icon of a phone (item 1069): the eyes of the attentive pose in white, ``scale`` times their size in the disc
    of radius 100, about the middle of a square tile of black, full bleed (the phone rounds it)."""
    eyes: str = "".join(f'<path d="{EYE}" transform="{matrix}" fill="#fff"/>' for matrix in ATTENTIVE)
    x, y = EYES_MIDDLE
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#000"/>'
        f'<g transform="translate(512 512) scale({scale}) translate({-x} {-y})">{eyes}</g></svg>'
    )


IOS_ICON: Path = HERE.parents[1] / "ios" / "Dot" / "Assets.xcassets" / "AppIcon.appiconset" / "icon-1024.png"

# The eyes on a phone's tile, and on the maskable one, which a phone may cut to a circle: well inside its safe zone.
PHONE_SCALE: float = 5.2
MASKABLE_SCALE: float = 4.4


def mac_tile_svg() -> str:
    """The desktop app's icon (item 1081): the phone's flat eyes on the macOS tile, a black body inset on the 1024
    grid with the rounding of the system's icons."""
    eyes: str = "".join(f'<path d="{EYE}" transform="{matrix}" fill="#fff"/>' for matrix in ATTENTIVE)
    x, y = EYES_MIDDLE
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
        '<rect x="100" y="100" width="824" height="824" rx="185" fill="#000"/>'
        f'<g transform="translate(512 512) scale({MAC_SCALE}) translate({-x} {-y})">{eyes}</g></svg>'
    )


# The eyes on the desktop app's tile: the phone's, in the proportion of its smaller body.
MAC_SCALE: float = PHONE_SCALE * 824 / 1024


def _page(svg: str, size: int) -> str:
    return (
        "<!doctype html><html><head><style>html,body{margin:0;background:transparent}"
        f"svg{{display:block;width:{size}px;height:{size}px}}</style></head><body>{svg}</body></html>"
    )


async def render(target: Path, svg: str, size: int) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
            await page.set_content(_page(svg, size))
            await page.screenshot(
                path=str(target), omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size}
            )
        finally:
            await browser.close()


async def main() -> None:
    (STATIC / "favicon.svg").write_text(mark_svg(ATTENTIVE, False) + "\n", encoding="utf-8")
    await render(HERE / "app.png", mac_tile_svg(), APP_PX)
    # The web app's and the home screen's icons are a phone's (item 1069).
    for size in WEB_PX:
        await render(STATIC / "icons" / f"icon-{size}.png", phone_tile_svg(PHONE_SCALE), size)
    await render(STATIC / "icons" / "icon-maskable-512.png", phone_tile_svg(MASKABLE_SCALE), 512)
    await render(STATIC / "icons" / "icon-96.png", phone_tile_svg(PHONE_SCALE), 96)
    for size in TOUCH_PX:
        await render(STATIC / "icons" / f"touch-icon-{size}.png", phone_tile_svg(PHONE_SCALE), size)
    # The iPhone app's icon is the same tile (item 1081), opaque, as Xcode wants an app icon.
    await render(IOS_ICON, phone_tile_svg(PHONE_SCALE), APP_PX)
    with Image.open(IOS_ICON) as drawn:
        drawn.convert("RGB").save(IOS_ICON)
    # The 180 px files at the root, which iOS tries by name when a page names no icon: the same artwork.
    tile: bytes = (STATIC / "icons" / "touch-icon-180.png").read_bytes()
    for name in ROOT_TOUCH:
        (STATIC / name).write_bytes(tile)
    # In the menu bar the hairline of gold is wider: it is what keeps the ball from a dark bar at 22 points.
    states: dict[str, str] = {
        "idle": mark_svg(ATTENTIVE, False, rim=9.0),
        "working": mark_svg(GLANCE, False, rim=9.0),
        "waiting": mark_svg(ATTENTIVE, True, rim=9.0),
    }
    for state, svg in states.items():
        await render(TRAY_DIR / f"tray-{state}.png", svg, TRAY_PX)


if __name__ == "__main__":
    TRAY_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
