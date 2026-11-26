"""Conversation cards travel through the real daemon to desktop and phone browsers."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, TOKEN, own_home_of, served
from PIL import Image
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.core.types import JsonArray
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.cards import CARDS_EVENT, SHOW_CARDS_TOOL
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]
TITLE: str = "Visits by month"
UNTRUSTED_TEXT: str = '<img src="/missing" onerror="window.cardCodeRan = true">\nLiteral post text'
CHOICE: str = "Show the monthly totals"
CARDS: JsonArray = [
    {
        "kind": "chart",
        "title": TITLE,
        "chart_type": "bar",
        "x": ["Jan", "Feb", "Mar"],
        "series": [{"name": "Visits", "values": [4, 7, 9]}],
        "unit": "visits",
    },
    {"kind": "post_draft", "platform": "X", "account": "@example", "text": UNTRUSTED_TEXT},
    {"kind": "post_draft", "platform": "X", "account": "@example", "text": "Second draft"},
    {"kind": "post_draft", "platform": "X", "account": "@other", "text": "Third draft"},
    {"kind": "choice", "prompt": "Which report?", "options": [CHOICE, "Show the yearly total"]},
    {"kind": "image_gallery", "title": "Reference", "images": [{"path": "chart-preview.png", "alt": "Blue sample"}]},
]
FINISH: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(
        ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Here are the drafts and the chart.", "success": True}),
    ),
)


async def _done(runtime: Runtime, run_id: str) -> RunRecord:
    deadline: float = time.monotonic() + 30
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.finished_at is not None:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


async def _check_cards(page: Page, width: int, shots: Path) -> None:
    chart: Locator = page.get_by_role("img", name=f"{TITLE}, bar chart")
    await expect(chart).to_be_visible(timeout=90_000)
    await expect(chart.locator("rect")).to_have_count(3)
    await page.get_by_text("Chart data", exact=True).click()
    await expect(page.get_by_role("cell", name="9", exact=True)).to_be_visible()
    carousel: Locator = page.get_by_role("group", name="Post drafts")
    await expect(carousel.locator("article")).to_have_count(3)
    assert await carousel.evaluate("(node) => node.scrollWidth > node.clientWidth")
    assert await page.evaluate("() => document.documentElement.scrollWidth === window.innerWidth")
    first: Locator = carousel.locator("article").first
    assert await first.locator(".chat-draft-text").text_content() == UNTRUSTED_TEXT
    await expect(first.locator("img")).to_have_count(0)
    assert not await page.evaluate("() => window.cardCodeRan === true")
    await carousel.scroll_into_view_if_needed()
    await carousel.screenshot(path=shots / f"chat-draft-carousel-{width}.png")
    await expect(page.get_by_role("img", name="Blue sample")).to_be_visible()
    await expect(page.get_by_role("img", name="Blue sample")).to_have_js_property("naturalWidth", 16)

    await first.get_by_role("button", name="Edit", exact=True).click()
    await first.get_by_role("textbox", name="Post text").fill("Edited\npost.")
    await first.get_by_role("button", name="Save edit").click()
    await first.get_by_role("button", name="Prepare to publish").click()
    composer: Locator = page.locator('textarea[aria-label^="Message for"]').last
    value: str = await composer.input_value()
    assert json.loads(value.split("\n", 1)[1]) == {"platform": "X", "account": "@example", "text": "Edited\npost."}
    await first.get_by_role("button", name="Cancel draft").click()
    await expect(first.get_by_text("Draft cancelled in this view.")).to_be_visible()
    await composer.fill("")


async def test_the_daemon_shows_cards_at_desktop_and_phone_widths_and_replays_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home: Path = own_home_of(tmp_path, monkeypatch)
    async with served(home) as (client, port, runtime):
        image: Path = runtime.workspace / "chart-preview.png"
        Image.new("RGB", (16, 8), "#305fca").save(image)
        provider: str = parse_model_ref(SCRIPTED_MODEL).provider
        show: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool=SHOW_CARDS_TOOL, arguments={"cards": CARDS}),)
        )
        await runtime.router.register(provider, ScriptedProvider(provider, [show, FINISH, FINISH]))
        response: httpx.Response = await client.post(
            "/runs",
            headers=MAC,
            json={"goal": "Show cards", "model": SCRIPTED_MODEL, "approvals": "yolo", "goal_mode": False},
        )
        assert response.status_code == 201, response.text
        run_id: str = str(response.json()["id"])
        assert (await _done(runtime, run_id)).status == "done"
        events: list[EventRecord] = await runtime.store.events_of_kind(run_id, CARDS_EVENT, 10)
        assert len(events) == 1
        assert events[0].data["version"] == 1
        assert not await runtime.store.events_of_kind(run_id, "approval_requested", 10)

        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(reduced_motion="reduce")
                await page.add_init_script(f'localStorage.setItem("dot.token", {json.dumps(TOKEN)})')
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                address: str = f"http://127.0.0.1:{port}/#/runs/{run_id}"
                for width, scheme in ((1280, "light"), (390, "dark")):
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.emulate_media(color_scheme=scheme)
                    if width == 1280:
                        await page.goto(address, wait_until="commit")
                    else:
                        await page.reload(wait_until="commit")
                    await _check_cards(page, width, tmp_path)

                await page.get_by_role("button", name=CHOICE).click()
                await page.wait_for_url(lambda url: "#/runs/" in url and not url.endswith(run_id), wait_until="commit")
                next_id: str = page.url.rpartition("/")[2]
                assert (await _done(runtime, next_id)).goal == CHOICE
                await expect(page.get_by_role("img", name=f"{TITLE}, bar chart")).to_be_visible()
                await expect(page.get_by_role("group", name="Post drafts").locator("article")).to_have_count(3)
                assert not errors, errors
            finally:
                await browser.close()
