"""What the owner does on the phone with no link is kept and sent once when it is back (item 1523): a message made
with no link shows at once under the chat as "Waiting to send", survives the page being killed, goes once and in order
when the link returns (the daemon's queue is counted), is not sent twice when an answer is lost after the daemon took
it (the key the daemon dedupes by), can be taken back to be edited, is left to the owner when it waited more than a day,
and an answer to a question that was answered meanwhile says so. A real daemon in a throw-away home; Chromium with a
phone's screen sent offline with ``context.set_offline`` (Playwright's WebKit cannot be sent offline).
"""

import asyncio

import httpx
import pytest
import pytest_asyncio
from dock_scenario import QUEUED, Scene, read_login, scene
from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright, expect
from pwa_scene import keep_shell, open_chat, phone, until_kept

from dotagent.store.models import QueuedMessage

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_WORDS: str = "Then check the build log too."
# Make what waits in the outbox 25 hours old, as a phone that was left a day would hold it.
_MAKE_OLD: str = """async () => {
  const open = indexedDB.open('dot-cache');
  const db = await new Promise((resolve) => { open.onsuccess = () => resolve(open.result); });
  const tx = db.transaction('outbox', 'readwrite');
  const store = tx.objectStore('outbox');
  const read = store.getAll();
  const all = await new Promise((resolve) => { read.onsuccess = () => resolve(read.result); });
  for (const entry of all) { entry.madeAt -= 25 * 3600 * 1000; store.put(entry); }
  await new Promise((resolve) => { tx.oncomplete = () => resolve(); });
  db.close();
}"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login()


async def _queued(working: Scene) -> list[str]:
    """The words of the messages in the chat's queue, as the daemon holds them."""
    held: list[QueuedMessage] = await working.runtime.store.list_queued(working.run.thread_id)
    return [message.text for message in held]


async def _until_queued(working: Scene, count: int) -> None:
    """Wait until the daemon's queue of the chat holds ``count`` messages."""
    async with asyncio.timeout(20):
        while len(await _queued(working)) < count:  # noqa: ASYNC110
            await asyncio.sleep(0.2)


async def _settled(page: Page) -> None:
    """The page stands as it opened: the curtain is lifted and the run is shown at work, so that the link is lost under
    a page that has begun, not while it begins."""
    await expect(page.locator(".waiting")).to_have_count(0, timeout=20_000)
    await expect(page.locator(".capsule")).to_contain_text("Working", timeout=20_000)
    await expect(page.locator(".composer textarea").first).to_be_visible()


async def _send(page: Page, words: str) -> None:
    """Type ``words`` in the chat's composer and press its send button."""
    box = page.locator(".composer textarea").first
    await box.fill(words)
    # With the box empty, the send button of a run at work is its Stop: it is the send once the words are in.
    button = page.locator(".composer .send").first
    await expect(button).not_to_contain_text("Stop")
    await button.click()


async def _phone_at_work(
    playwright: object, working: Scene, workers: str = "allow"
) -> tuple[Browser, BrowserContext, Page]:
    browser: Browser = await playwright.chromium.launch(  # type: ignore[attr-defined]
        channel=working.runtime.config.browser.channel, headless=True
    )
    context: BrowserContext = await phone(browser, playwright, workers=workers)  # type: ignore[arg-type]
    page: Page = await open_chat(context, working.address, working.token, working.run.id)
    await expect(page.locator(".capsule")).to_contain_text("Working", timeout=20_000)
    await until_kept(page, chats=1, lists=2)
    return browser, context, page


async def test_a_message_made_with_no_link_waits_survives_the_page_and_goes_once_when_the_link_is_back(
    login_env: dict[str, str],
) -> None:
    async with scene(login_env, "work") as working, async_playwright() as playwright:
        browser, context, page = await _phone_at_work(playwright, working)
        try:
            await keep_shell(page)
            await _settled(page)
            before: list[str] = await _queued(working)
            assert before == [QUEUED]
            await context.set_offline(True)
            await _send(page, _WORDS)
            row = page.locator(".outbox-row")
            await expect(row).to_have_count(1)
            await expect(row).to_contain_text(_WORDS)
            await expect(row).to_contain_text("Waiting to send")
            await expect(row.get_by_role("button", name="Retry now")).to_be_visible()
            await expect(row.get_by_role("button", name="Edit")).to_be_visible()
            # The box is empty: the message is the outbox's now.
            await expect(page.locator(".composer textarea").first).to_have_value("")
            assert await _queued(working) == before

            # The page is killed and opened again with no link: the message is still waiting, once.
            await page.close()
            again: Page = await context.new_page()
            await again.goto(f"{working.address}/#/runs/{working.run.id}")
            row = again.locator(".outbox-row")
            await expect(row).to_have_count(1, timeout=20_000)
            await expect(row).to_contain_text(_WORDS)
            await expect(row).to_contain_text("Waiting to send")

            # The link is back: it goes once, in order, and the row is gone.
            await context.set_offline(False)
            await expect(row).to_have_count(0, timeout=20_000)
            await _until_queued(working, 2)
            await asyncio.sleep(1.5)
            assert await _queued(working) == [*before, _WORDS]
            await context.close()
        finally:
            await browser.close()


async def test_a_message_whose_answer_was_lost_after_the_daemon_took_it_is_one_message(
    login_env: dict[str, str],
) -> None:
    async with scene(login_env, "work") as working, async_playwright() as playwright:
        browser, context, page = await _phone_at_work(playwright, working, "block")
        try:
            before: list[str] = await _queued(working)
            taken: list[str] = []

            async def lose_the_answer(route: Route) -> None:
                if route.request.method != "POST" or taken:
                    await route.continue_()
                    return
                # The daemon takes the message; its answer never reaches the page.
                await route.fetch()
                taken.append(route.request.post_data or "")
                await route.abort("failed")

            await page.route("**/api/threads/*/queue", lose_the_answer)
            await _send(page, _WORDS)
            # The page was told nothing came of it, kept the message, and sent it again with the same key.
            await expect(page.locator(".outbox-row")).to_have_count(0, timeout=30_000)
            await asyncio.sleep(2.0)
            assert len(taken) == 1
            assert await _queued(working) == [*before, _WORDS]
            await context.close()
        finally:
            await browser.close()


async def test_a_message_taken_back_to_be_edited_is_not_sent_and_one_that_waited_a_day_is_left_to_the_owner(
    login_env: dict[str, str],
) -> None:
    async with scene(login_env, "work") as working, async_playwright() as playwright:
        browser, context, page = await _phone_at_work(playwright, working)
        try:
            await keep_shell(page)
            await _settled(page)
            before: list[str] = await _queued(working)
            await context.set_offline(True)
            await _send(page, "A first thought.")
            await expect(page.locator(".outbox-row")).to_have_count(1)
            # Edit takes it back into the box; nothing is sent when the link returns.
            await page.locator(".outbox-row").get_by_role("button", name="Edit").click()
            await expect(page.locator(".outbox-row")).to_have_count(0)
            await expect(page.locator(".composer textarea").first).to_have_value("A first thought.")

            # A message that waited more than a day: the daemon has forgotten its key, so it is the owner's to resend.
            await _send(page, "An old thought.")
            await expect(page.locator(".outbox-row")).to_have_count(1)
            await page.evaluate(_MAKE_OLD)
            await page.reload()
            await context.set_offline(False)
            row = page.locator(".outbox-row")
            await expect(row).to_contain_text("Not sent", timeout=20_000)
            await expect(row).to_contain_text("waited more than a day")
            await expect(row.get_by_role("button", name="Send again")).to_be_visible()
            await asyncio.sleep(2.0)
            assert await _queued(working) == before
            # Send again goes under a new key, once.
            await row.get_by_role("button", name="Send again").click()
            await expect(row).to_have_count(0, timeout=20_000)
            await _until_queued(working, len(before) + 1)
            await asyncio.sleep(1.5)
            assert await _queued(working) == [*before, "An old thought."]
            await context.close()
        finally:
            await browser.close()


async def test_an_answer_given_with_no_link_to_a_question_answered_meanwhile_says_so(login_env: dict[str, str]) -> None:
    async with scene(login_env, "ask") as asking, async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=asking.runtime.config.browser.channel, headless=True
        )
        try:
            context: BrowserContext = await phone(browser, playwright)
            page: Page = await open_chat(context, asking.address, asking.token, asking.run.id)
            card = page.locator(".approval").first
            await expect(card).to_be_visible(timeout=20_000)
            await context.set_offline(True)
            await card.get_by_role("button", name="Deny").click()
            await expect(page.locator(".outbox-note")).to_contain_text("Your answer is waiting to send")
            # Meanwhile it was answered on the Mac.
            pending = [a for a in await asking.runtime.store.list_approvals("pending", 20) if a.run_id == asking.run.id]
            async with httpx.AsyncClient(
                base_url=f"{asking.address}/api", headers={"Authorization": f"Bearer {asking.token}"}, timeout=30
            ) as client:
                decided = await client.post(f"/approvals/{pending[0].id}/decision", json={"approve": True, "note": ""})
                assert decided.status_code == 200, decided.text
            await context.set_offline(False)
            await expect(page.locator(".toast")).to_contain_text("was answered already", timeout=20_000)
            await context.close()
        finally:
            await browser.close()
