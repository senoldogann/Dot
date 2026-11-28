"""A message is in the chat the moment it is sent (item 1999, as DotNext's items 1919 and 1927 say it), on a
daemon in Chrome, wide and at a phone's width. The follow-up's request to start the chat's next run is held back by the
page's own route for a few seconds, as a busy Mac answers: meanwhile its bubble stands under the conversation with a
quiet "Pending" at its right edge, the agent's typing dots say "Starting…" under it, and the box is empty; once Dot took
it the page of the new run is open and nothing pending is left. A send Dot refuses takes its bubble away and gives the
words back to the box. The new run, which simply works, has no part in the capsule (item 2105): its work line says it.
In Turkish the words are "Bekliyor" and "Başlıyor…".

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio
import re
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page, Route, async_playwright, expect
from test_work_status import TOKEN, _served, _start

from dotagent.core.paths import ensure_layout
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


# The first run answers at once; the follow-up works in a command of a few seconds, so its capsule ticks.
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 4"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Again.", "success": True}),)),
)
# How long the page's route holds the request that starts the next run.
_HOLD_S: float = 2.5


async def _open(browser: Browser, port: int, address: str, run_id: str, width: int, language: str) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900 if width > 600 else 844}, reduced_motion="reduce"
    )
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.add_init_script(f"localStorage.setItem('dot.uiLanguage', '{language}')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".answer .prose").first).to_have_text("Done.")
    return page


async def _send(page: Page, words: str) -> None:
    box = page.locator(".dock textarea")
    await box.fill(words)
    await page.locator(".dock button.send").click()


@pytest.mark.parametrize(
    ("width", "language", "pending", "starting"),
    [(1440, "en", "Pending", "Starting…"), (390, "tr", "Bekliyor", "Başlıyor…")],
)
async def test_a_sent_message_is_pending_in_the_chat_until_dot_took_it(
    own_home: Path, tmp_path: Path, width: int, language: str, pending: str, starting: str
) -> None:
    async with _served(own_home, _TURNS) as (manager, address, port):
        run: RunRecord = await _start(manager)
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await _open(browser, port, address, run.id, width, language)

                async def held(route: Route) -> None:
                    if route.request.method == "POST":
                        await asyncio.sleep(_HOLD_S)
                    await route.continue_()

                await page.route("**/api/runs", held)
                await _send(page, "Count them again")
                shown = page.locator(".pending-send")
                await expect(shown.locator(".bubble.user")).to_have_text("Count them again", timeout=1500)
                await expect(shown.locator(".pending-note")).to_have_text(pending)
                await expect(shown.locator(".starting-line .work-status-words")).to_have_text(starting)
                await expect(shown.locator(".starting-line .typing-dots")).to_be_visible()
                await expect(page.locator(".dock textarea")).to_have_value("")
                # The bubble stands where the owner's messages do: its right edge on that of the message before it.
                bubble = await shown.locator(".bubble.user").bounding_box()
                before = await page.locator(".user-message .bubble.user").first.bounding_box()
                assert bubble is not None and before is not None
                assert abs((bubble["x"] + bubble["width"]) - (before["x"] + before["width"])) <= 1, (bubble, before)
                await page.screenshot(path=str(tmp_path / f"pending-{width}.png"))
                # Dot took it: the new run's page is open, its message drawn by the page, nothing pending left.
                await expect(page).not_to_have_url(re.compile(f"/runs/{run.id}$"), timeout=_HOLD_S * 1000 + 5000)
                await expect(page.locator(".pending-send")).to_have_count(0)
                await expect(page.locator(".user-message .bubble.user").last).to_have_text("Count them again")
                # A run that simply works says nothing over the composer, on a phone or a computer (item 2105).
                await expect(page.locator(".capsule .capsule-work:not([hidden])")).to_have_count(0)
                await expect(page.locator('.dockbar [data-segment="status"]:not([hidden])')).to_have_count(0)
                await expect(page.locator('.dockbar [data-segment="stop"]')).to_have_count(0)
                await page.close()
            finally:
                await browser.close()


async def test_a_refused_send_takes_its_bubble_away_and_gives_the_words_back(own_home: Path) -> None:
    async with _served(own_home, _TURNS) as (manager, address, port):
        run: RunRecord = await _start(manager)
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await _open(browser, port, address, run.id, 1440, "en")

                async def refused(route: Route) -> None:
                    if route.request.method != "POST":
                        await route.continue_()
                        return
                    await asyncio.sleep(1)
                    await route.fulfill(
                        status=400, content_type="application/json", body='{"detail": "That model is not set up."}'
                    )

                await page.route("**/api/runs", refused)
                await _send(page, "Count them again")
                await expect(page.locator(".pending-send")).to_have_count(1, timeout=1500)
                await expect(page.locator(".pending-send")).to_have_count(0, timeout=5000)
                await expect(page.locator(".dock textarea")).to_have_value("Count them again")
                await expect(page).to_have_url(re.compile(f"/runs/{run.id}$"))
                await page.close()
            finally:
                await browser.close()
