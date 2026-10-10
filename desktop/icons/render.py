"""Render the icons of the desktop shell from the face of the web UI (``static/js/face.js``): the app icon
(``app.png``, 1024 px, for ``tauri icon``) and the menu bar icons, one per state (``tray-{state}.png``, 44 px, the
22 pt item at 2x). The face is drawn here with the same shapes and colours as ``faceSvg`` there; run it after a change
of either: ``uv run python desktop/icons/render.py`` from the repository, with the Playwright Chromium installed."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE: Path = Path(__file__).resolve().parent
TRAY_DIR: Path = HERE.parent / "src-tauri" / "icons"
FACE_FILL: str = "#ffcd3c"
INK: str = "#1a1a1a"
RING_OF_STATE: dict[str, str] = {"idle": INK, "working": INK, "waiting": "#8f5600"}
TRAY_PX: int = 44
APP_PX: int = 1024


def face_svg(state: str) -> str:
    ring: str = RING_OF_STATE[state]
    muted: str = ' stroke-opacity="0.35"' if state == "idle" else ""
    core: str = f'<circle cx="16" cy="16" r="{5.5 if state == "working" else 4.5}" fill="{INK}"/>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" data-state="{state}">'
        f'<circle cx="16" cy="16" r="16" fill="{FACE_FILL}"/>'
        f'<circle cx="16" cy="16" r="9" fill="none" stroke="{ring}"{muted} stroke-width="2"/>'
        f"{core}</svg>"
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
    await render(HERE / "app.png", face_svg("idle"), APP_PX)
    for state in RING_OF_STATE:
        await render(TRAY_DIR / f"tray-{state}.png", face_svg(state), TRAY_PX)


if __name__ == "__main__":
    TRAY_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
