"""Render Dot's mark (item 114) into the files that carry it: the favicon of the web UI (``static/favicon.svg``), the
web app's icons (``static/icons/icon-192.png``, ``icon-512.png``), the app icon of the desktop shell (``app.png``, 1024
px, for ``npm run icons``) and its menu bar icons, one per state (``tray-{state}.png``, 44 px, the 22 pt item at 2x).

The mark is a yellow dot that looks: a disc with the two capsule eyes of the character's attentive rest pose
(``static/js/character``), a little larger than the engine draws them so they hold at 16 px. ``static/index.html`` sets
the same disc and eyes as the o of the wordmark; ``tests/unit/test_mark.py`` keeps the two alike. Run this after a
change of the mark, from the repository: ``uv run python desktop/icons/render.py`` (with the Playwright Chromium
installed), then ``npm run icons`` in ``desktop/``."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE: Path = Path(__file__).resolve().parent
REPO: Path = HERE.parents[1]
TRAY_DIR: Path = HERE.parent / "src-tauri" / "icons"
STATIC: Path = REPO / "src" / "dotagent" / "server" / "static"
YELLOW: str = "#ffcd3c"
INK: str = "#1c1b19"
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


def dot(eyes: tuple[str, str], fill: str) -> str:
    """The disc and its eyes about the origin, radius 100."""
    paths: str = "".join(f'<path d="{EYE}" transform="{matrix}" fill="{INK}"/>' for matrix in eyes)
    return f'<circle r="100" fill="{fill}"/>{paths}'


def mark_svg(eyes: tuple[str, str], pastille: bool) -> str:
    """The mark alone: the favicon, and the menu bar's icon in a state."""
    spot: str = f'<circle cx="74" cy="-74" r="24" fill="{PASTILLE}"/>' if pastille else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-100 -100 200 200">{dot(eyes, YELLOW)}{spot}</svg>'


def tile_svg(rounded: bool) -> str:
    """The mark lit as a ball on a charcoal tile: the macOS app icon (its rounded body inset on the 1024 grid, with
    the shadow the system draws no more) or, square and full bleed, the web app's icon, which the phone rounds."""
    lit: str = (
        '<radialGradient id="lit" cx="0.35" cy="0.3" r="0.8">'
        '<stop offset="0" stop-color="#ffe07a"/><stop offset="0.55" stop-color="#ffcd3c"/>'
        '<stop offset="1" stop-color="#f2b523"/></radialGradient>'
    )
    ground: str = (
        '<linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#3a3834"/><stop offset="1" stop-color="#1f1e1c"/></linearGradient>'
        '<radialGradient id="shade" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#000" stop-opacity="0.38"/><stop offset="1" stop-color="#000" stop-opacity="0"/>'
        "</radialGradient>"
        '<filter id="drop" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#000" flood-opacity="0.28"/></filter>'
    )
    if rounded:
        body: str = '<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#ground)" filter="url(#drop)"/>'
        scale: float = 2.55
    else:
        body = '<rect width="1024" height="1024" fill="url(#ground)"/>'
        scale = 3.1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><defs>{lit}{ground}</defs>{body}'
        f'<ellipse cx="512" cy="{512 + 112 * scale}" rx="{76 * scale}" ry="{13 * scale}" fill="url(#shade)"/>'
        f'<g transform="translate(512 {512 - 6 * scale}) scale({scale})">{dot(ATTENTIVE, "url(#lit)")}</g></svg>'
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
    states: dict[str, str] = {
        "idle": mark_svg(ATTENTIVE, False),
        "working": mark_svg(GLANCE, False),
        "waiting": mark_svg(ATTENTIVE, True),
    }
    for state, svg in states.items():
        await render(TRAY_DIR / f"tray-{state}.png", svg, TRAY_PX)


if __name__ == "__main__":
    TRAY_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
