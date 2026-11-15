"""Retry under an answer (item 1185), in a real Chrome and WebKit on a daemon of its own; the scripted model plays the
runs, and nothing reaches the network.

- **Retry sends the message again**: the chat goes back to before it, as Edit from here does, and the same words go as
  the chat's next run at once; the earlier turn stays, the answer retried leaves, the new answer stands in its place.
- **A send that fails after the chat went back** leaves the message in the box, as Edit from here does, and says why.
- **The first message of a chat** is sent again as a new chat's first, since the rewind leaves no run.
- **On a phone** Retry is a row of the message's sheet, as every button of the foot is.
"""

import asyncio
import time

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, Route, async_playwright, expect
from test_chat_calm import _OWNERS_PHONE, TOKEN, WAIT_S, _daemon, _done
from test_message_sheet import HOLD, HOLD_MS

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home
from dotagent.evals.types import ScriptedTurn
from dotagent.runtime import Runtime
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_FIRST: str = "Name three rivers of Turkey"
_SECOND: str = "Which one is the longest?"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _said(text: str) -> ScriptedTurn:
    return ScriptedTurn(text=text, calls=())


async def _chat(manager: RunManager, goal: str, thread: str | None) -> RunRecord:
    return await manager.start(
        goal, "chat", SCRIPTED_MODEL, thread, "test", None, None, "standard", None, "yolo", False, ()
    )


async def _page(browser: Browser, playwright, address: str, phone: bool) -> tuple[BrowserContext, Page]:
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    options = (
        {**playwright.devices[_OWNERS_PHONE], "reduced_motion": "reduce"}
        if phone
        else {"viewport": {"width": 1280, "height": 900}, "reduced_motion": "reduce"}
    )
    context: BrowserContext = await browser.new_context(**options)
    page: Page = await context.new_page()
    await page.goto(ui_url(host, port, TOKEN))
    return context, page


async def _newest(runtime: Runtime, thread: str | None, besides: set[str]) -> RunRecord:
    """The run started since `besides` were (in the chat `thread`, any chat when None), once it has ended done."""
    deadline: float = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        for run in await runtime.store.list_runs(50, thread):
            if run.id not in besides:
                return await _done(runtime, run)
        await asyncio.sleep(0.05)
    raise AssertionError(f"no new run in {WAIT_S} s")


async def test_retry_sends_the_message_again_in_place_of_its_answer(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        _said("Kızılırmak, Fırat and Dicle."),
        _said("The Fırat, at first."),
        _said("The Kızılırmak is the longest within Turkey."),
        _said("Sakarya, Yeşilırmak and Ceyhan."),
        _said("Kızılırmak, Fırat and Dicle, again."),
    ]
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, turns) as (runtime, manager, address), async_playwright() as playwright:
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                first: RunRecord = await _done(runtime, await _chat(manager, _FIRST, None))
                second: RunRecord = await _done(runtime, await _chat(manager, _SECOND, first.thread_id))
                context, page = await _page(chromium, playwright, address, False)
                await page.goto(f"{address}/#/runs/{second.id}")
                answer: Locator = page.locator("article.answer").last
                await expect(answer).to_contain_text("The Fırat, at first.")
                await answer.hover()
                await answer.get_by_role("button", name="Retry").click()
                dialog: Locator = page.locator("dialog[open]")
                await expect(dialog).to_contain_text("Retry this message?")
                await dialog.get_by_role("button", name="Retry", exact=True).click()
                # The same words went again as the chat's next run, and the page went to it.
                retried: RunRecord = await _newest(runtime, first.thread_id, {first.id, second.id})
                assert retried.goal == _SECOND
                await expect(page).to_have_url(f"{address}/#/runs/{retried.id}")
                answers: Locator = page.locator("article.answer")
                await expect(answers).to_have_count(2)
                await expect(answers.last).to_contain_text("The Kızılırmak is the longest within Turkey.")
                await expect(page.locator(".bubble.user")).to_have_text([_FIRST, _SECOND])
                await expect(page.get_by_text("The Fırat, at first.")).to_have_count(0)

                # The composer's choices are read before the chat goes back: when their read fails (the models, read
                # again once forgotten), the chat stays as it was.
                async def no_models(route: Route) -> None:
                    await route.fulfill(status=503, content_type="application/json", body='{"detail": "down"}')

                await page.route("**/api/models*", no_models)
                await page.evaluate("import('/js/state.js').then((state) => state.forgetModels())")
                answer = page.locator("article.answer").last
                await answer.hover()
                await answer.get_by_role("button", name="Retry").click()
                async with page.expect_response("**/api/models*"):
                    await page.locator("dialog[open]").get_by_role("button", name="Retry", exact=True).click()
                # The failure is told once the read failed, and only then is the store read.
                await expect(page.locator(".toast", has_text="down")).to_be_visible()
                await expect(page).to_have_url(f"{address}/#/runs/{retried.id}")
                kept: list[str] = [run.id for run in await runtime.store.list_runs(50, first.thread_id)]
                assert kept == [retried.id, first.id], kept
                await page.unroute("**/api/models*", no_models)

                # A send that fails after the chat went back leaves the message in the box of the run the chat ends
                # with now, as Edit from here does, and says why; nothing was sent.
                async def refuse(route: Route) -> None:
                    if route.request.method == "POST":
                        await route.fulfill(status=503, content_type="application/json", body='{"detail": "down"}')
                    else:
                        await route.fallback()

                await page.route("**/api/runs", refuse)
                answer = page.locator("article.answer").last
                await answer.hover()
                await answer.get_by_role("button", name="Retry").click()
                await page.locator("dialog[open]").get_by_role("button", name="Retry", exact=True).click()
                await expect(page).to_have_url(f"{address}/#/runs/{first.id}")
                await expect(page.locator(".dock textarea")).to_have_value(_SECOND)
                await expect(page.locator(".toast").last).to_be_visible()
                await page.unroute("**/api/runs", refuse)
                assert [run.id for run in await runtime.store.list_runs(50, first.thread_id)] == [first.id]
                await context.close()

                # The first message of a chat goes again as a new chat's first: on a phone, from the message's sheet.
                alone: RunRecord = await _done(runtime, await _chat(manager, "Name three other rivers", None))
                context, page = await _page(webkit, playwright, address, True)
                await page.goto(f"{address}/#/runs/{alone.id}")
                answer = page.locator("article.answer").last
                await expect(answer).to_contain_text("Sakarya")
                await page.evaluate(HOLD, ["article.answer .prose", HOLD_MS])
                sheet: Locator = page.locator("dialog.message-sheet[open]")
                await sheet.get_by_role("button", name="Retry").click()
                await page.locator("dialog[open]").get_by_role("button", name="Retry", exact=True).click()
                again: RunRecord = await _newest(runtime, None, {first.id, second.id, retried.id, alone.id})
                assert again.goal == "Name three other rivers"
                assert again.thread_id != alone.thread_id
                await expect(page.locator("article.answer").last).to_contain_text("again.")
                await context.close()
            finally:
                await chromium.close()
                await webkit.close()
