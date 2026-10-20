"""The cookie-banner handler of a page (``install_consent_handler``) lets no click error out to Playwright: a banner
that goes away under the click is the outcome it is after, so nothing is said of it; a click that fails with the banner
still there is logged with the page's address. Playwright prints an error a handler raises to stderr and counts the
use all the same. A real headless Chrome on a page of the test's own: nothing outside this Mac is reached."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright
from structlog.testing import capture_logs

from dotagent.browser import driver
from dotagent.browser.driver import install_consent_handler
from dotagent.config import load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

# The decline button is made again every 20 ms, so that a click on it never finds it still: it times out with "element
# was detached from the DOM, retrying", as TrustArc's banner did. ``closeAfterMs`` removes the banner too.
_PAGE: str = """<!doctype html><title>Banner</title>
<button id="go" onclick="document.title = 'went'">Go</button>
<div id="onetrust-banner-sdk"><span id="slot"></span></div>
<script>
  const slot = document.getElementById('slot');
  const again = () => { slot.innerHTML = '<button>Decline All</button>'; };
  again();
  setInterval(again, 20);
  const closeAfterMs = %s;
  if (closeAfterMs > 0) setTimeout(() => document.getElementById('onetrust-banner-sdk').remove(), closeAfterMs);
</script>"""
_CLICK_TIMEOUT_MS: float = 400.0
_PRINTED: str = "Error occurred in event listener"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            yield launched
        finally:
            await launched.close()


async def _click_go(browser: Browser, closes_after_ms: int) -> list[dict[str, object]]:
    """The log events of a click on Go, which the banner of ``_PAGE`` meets on its way."""
    page: Page = await browser.new_page()
    try:
        with capture_logs() as logs:
            await page.set_content(_PAGE % closes_after_ms)
            await install_consent_handler(page)
            await page.get_by_role("button", name="Go").click(timeout=15_000)
        assert await page.title() == "went"
    finally:
        await page.close()
    return logs


async def test_a_banner_that_goes_away_under_the_click_is_no_error(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(driver, "_CONSENT_CLICK_TIMEOUT_MS", _CLICK_TIMEOUT_MS)
    logs: list[dict[str, object]] = await _click_go(browser, 150)
    assert [entry for entry in logs if entry["event"] == "consent_decline_failed"] == []
    assert _PRINTED not in capfd.readouterr().err


async def test_a_click_that_fails_with_the_banner_still_there_is_logged_with_the_address(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(driver, "_CONSENT_CLICK_TIMEOUT_MS", _CLICK_TIMEOUT_MS)
    logs: list[dict[str, object]] = await _click_go(browser, 0)
    failed: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "consent_decline_failed"]
    assert failed and all(entry["url"] == "about:blank" and "Timeout" in str(entry["error"]) for entry in failed)
    assert _PRINTED not in capfd.readouterr().err
