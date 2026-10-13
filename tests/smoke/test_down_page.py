"""The desktop shell's page for a daemon it cannot reach (``desktop/ui/down.html``, item 191) says what the shell tells
it in its fragment (``down_url`` in ``desktop/src-tauri/src/lib.rs``): a file the shell could not read stands for "Dot
is not running" and the advice to start it, a home without a token keeps them and says why, and a page told nothing is
the page it was."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.config import load_default_config

_DOWN: Path = Path(__file__).resolve().parents[2] / "desktop" / "ui" / "down.html"
_HOME: str = "/Users/someone/Library/Application Support/Dot"
_NOT_RUNNING: str = "Dot is not running"
_TITLE: str = "Dot could not read its config.toml"
_CONFIG: str = f"{_HOME}/config.toml: line 3, column 8: invalid string; expected `\"`, `'`"
_NO_TOKEN: str = f"{_HOME}/api-token: there is none yet; the daemon makes it the first time it starts"
# The paragraph and the command that tell how to start Dot, as the page shows them.
_ADVICE: str = ".start:visible"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest.mark.browser
async def test_the_down_page_says_what_kept_the_shell_from_the_daemon(tmp_path: Path) -> None:
    """Told a file the shell could not read, the page names it and what is wrong with it instead of "Dot is not running"
    and the advice to start Dot; told in a new fragment that there is no token yet, it says so above the advice; told
    nothing, it is the page it was. Before, the page said "Dot is not running" whatever kept the shell from the
    daemon."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(f"{_DOWN.as_uri()}#{urlencode({'title': _TITLE, 'detail': _CONFIG})}")
                await expect(page.locator("h1")).to_have_text(_TITLE)
                await expect(page.locator(".why")).to_have_text(_CONFIG)
                await expect(page.locator(_ADVICE)).to_have_count(0)
                await page.screenshot(path=str(tmp_path / f"down-config-{width}-{scheme}.png"))
                await page.evaluate("fragment => { location.hash = fragment; }", urlencode({"detail": _NO_TOKEN}))
                await expect(page.locator("h1")).to_have_text(_NOT_RUNNING)
                await expect(page.locator(".why")).to_have_text(_NO_TOKEN)
                await expect(page.locator(_ADVICE)).to_have_count(2)
                await page.screenshot(path=str(tmp_path / f"down-token-{width}-{scheme}.png"))
                await page.goto(_DOWN.as_uri())
                await expect(page.locator("h1")).to_have_text(_NOT_RUNNING)
                await expect(page.locator(".why")).to_be_hidden()
                await expect(page.locator(_ADVICE)).to_have_count(2)
                await page.close()
        finally:
            await browser.close()
