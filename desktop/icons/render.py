"""Render Dot's mark (item 114) into the files that carry it: the favicon of the web UI (``static/favicon.svg``), the
web app's icons (``static/icons/icon-192.png``, ``icon-512.png``, the home screen's
``static/icons/touch-icon-{180,167,152,120}.png`` and the 180 px files at the root, items 410 and 550), the app icon of
the desktop shell (``app.png``, 1024 px, for ``npm run icons``) and its menu bar icons, one per state
(``tray-{state}.png``, 44 px, the 22 pt item at 2x).

The mark is a dot that looks (item 642, the owner's choice of 2026-10-25, "Obsidian"): a ball of obsidian, lit at its
upper left, with the two capsule eyes of the character's attentive rest pose (``static/js/character``) in champagne gold
and a hairline of gold round it, so that it holds on a light page, a dark one and a menu bar of either. The eyes are a
little larger than the engine draws them so they hold at 16 px. ``static/index.html`` and ``desktop/ui/down.html`` set
the same ball and eyes as the o of the wordmark; ``tests/unit/test_mark.py`` keeps them alike. Run this after a
change of the mark, from the repository: ``uv run python desktop/icons/render.py`` (with the Playwright Chromium
installed), then ``npm run icons`` in ``desktop/``."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE: Path = Path(__file__).resolve().parent
REPO: Path = HERE.parents[1]
TRAY_DIR: Path = HERE.parent / "src-tauri" / "icons"
STATIC: Path = REPO / "src" / "dotagent" / "server" / "static"
# The obsidian of the ball, from its lit upper left to its edge; the champagne gold of the eyes, top to bottom; the gold
# of the hairline round the ball and of its light.
OBSIDIAN: tuple[tuple[str, str], ...] = (("0", "#5a5a5e"), ("0.32", "#1c1c1f"), ("1", "#000000"))
GOLD: tuple[tuple[str, str], ...] = (("0", "#fff1c1"), ("0.45", "#e6c26b"), ("1", "#a8782b"))
RIM: str = "#e6c26b"
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
        'stop-opacity="0.75"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
    )


def dot(eyes: tuple[str, str], rim: float = 2.0) -> str:
    """The ball and its eyes about the origin, radius 100, its light and the hairline of gold round it (``rim`` wide:
    thicker where the mark is small, so the line still shows)."""
    paths: str = "".join(f'<path d="{EYE}" transform="{matrix}" fill="url(#mark-gold)"/>' for matrix in eyes)
    return (
        f'<circle r="100" fill="url(#mark-obsidian)"/>{paths}'
        '<ellipse cx="-38" cy="-50" rx="34" ry="18" transform="rotate(-32 -38 -50)" fill="url(#mark-shine)"/>'
        f'<circle r="{100 - rim / 2}" fill="none" stroke="{RIM}" stroke-opacity="0.55" stroke-width="{rim}"/>'
    )


def mark_svg(eyes: tuple[str, str], pastille: bool, rim: float = 2.0) -> str:
    """The mark alone: the favicon, and the menu bar's icon in a state."""
    spot: str = f'<circle cx="74" cy="-74" r="24" fill="{PASTILLE}"/>' if pastille else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-100 -100 200 200"><defs>{defs()}</defs>'
        f"{dot(eyes, rim)}{spot}</svg>"
    )


def tile_svg(rounded: bool, scale: float = 0.0) -> str:
    """The mark on a tile of night: the macOS app icon (its rounded body inset on the 1024 grid, with the shadow the
    system draws no more) or, square and full bleed, the web app's icon, which the phone rounds; ``scale`` a smaller
    mark for the maskable icon, which a phone may cut to a circle."""
    ground: str = (
        '<linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#232326"/><stop offset="1" stop-color="#050506"/></linearGradient>'
        '<radialGradient id="shade" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#000" stop-opacity="0.38"/><stop offset="1" stop-color="#000" stop-opacity="0"/>'
        "</radialGradient>"
        '<filter id="drop" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#000" flood-opacity="0.28"/></filter>'
    )
    if rounded:
        body: str = (
            '<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#ground)" filter="url(#drop)"/>'
            '<rect x="101" y="101" width="822" height="822" rx="184" fill="none" stroke="#fff" stroke-opacity="0.07"'
            ' stroke-width="2"/>'
        )
        scale = scale or 2.55
    else:
        body = '<rect width="1024" height="1024" fill="url(#ground)"/>'
        scale = scale or 3.1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><defs>{defs()}{ground}</defs>{body}'
        f'<ellipse cx="512" cy="{512 + 112 * scale}" rx="{76 * scale}" ry="{13 * scale}" fill="url(#shade)"/>'
        f'<g transform="translate(512 {512 - 6 * scale}) scale({scale})">{dot(ATTENTIVE)}</g></svg>'
    )


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
    await render(HERE / "app.png", tile_svg(True), APP_PX)
    for size in WEB_PX:
        await render(STATIC / "icons" / f"icon-{size}.png", tile_svg(False), size)
    # The maskable icon: the same tile, the mark well inside the circle a phone may cut it to (its safe zone).
    await render(STATIC / "icons" / "icon-maskable-512.png", tile_svg(False, scale=2.4), 512)
    await render(STATIC / "icons" / "icon-96.png", tile_svg(False), 96)
    for size in TOUCH_PX:
        await render(STATIC / "icons" / f"touch-icon-{size}.png", tile_svg(False), size)
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
