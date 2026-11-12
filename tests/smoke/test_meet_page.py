"""``MeetPage`` drives a local stand-in for Google Meet's page in a real headless Chromium (item 1138):
join, captions rewritten and added, chat in and out, leave. No network, no model."""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from meet_stand_in import serve_stand_in
from playwright.async_api import Browser, Page, async_playwright

from dotagent.meetings.meet import MeetPage, MeetPageError, meeting_address
from dotagent.meetings.transcript import CaptionBook, fold_captions, new_chat
from dotagent.meetings.types import MeetingLine, PageBatch

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch(headless=True)
        yield chromium
        await chromium.close()


async def settle() -> None:
    # The page script reads the page 30 ms after a change.
    await asyncio.sleep(0.2)


async def test_a_meeting_is_joined_read_posted_in_and_left(browser: Browser) -> None:
    with serve_stand_in() as address:
        page: Page = await browser.new_page()
        meet: MeetPage = MeetPage(page)
        await meet.open(address)
        assert await meet.state() == "joining"

        assert await meet.prepare("Dot") == "waiting"
        assert await page.evaluate("window.guestName") == "Dot"
        for _ in range(40):
            if await meet.state() == "in_call":
                break
            await asyncio.sleep(0.1)
        assert await meet.state() == "in_call"

        assert not await meet.captions_on()
        await meet.turn_captions_on()
        assert await meet.captions_on()
        await meet.watch()
        await meet.watch()  # a second call installs nothing more

        book: CaptionBook = CaptionBook(open={}, changed_at={}, done={})
        lines: list[MeetingLine] = []

        async def fold(now: float) -> None:
            nonlocal book
            batch: PageBatch = await meet.drain()
            seen = batch.captions
            book, out = fold_captions(book, seen, now, 1.0)
            lines.extend(out)

        # Ana's block is rewritten three times; the first drain sees only what changed.
        for words in ("Hello", "Hello every", "Hello everyone, welcome"):
            await page.evaluate("([w]) => window.standIn.caption(1, 'Ana', w)", [words])
            await settle()
        await page.evaluate("() => window.standIn.caption(2, 'Bo', 'Thanks')")
        await settle()
        # The page's blocks are the whole picture each time, so a fold is given what is still on the page.
        batch: PageBatch = await meet.drain()
        assert [c.text for c in batch.captions] == ["Hello", "Hello every", "Hello everyone, welcome", "Thanks"]
        assert len({c.key for c in batch.captions}) == 2
        book, out = fold_captions(book, batch.captions, 10.0, 1.0)
        assert out == ()
        assert (await meet.drain()).captions == ()  # a drain empties the buffer
        # Nothing changed for 2 s: both blocks settle, once, with their last words.
        book, out = fold_captions(book, (), 12.0, 1.0)
        lines.extend(out)
        assert [(line.speaker, line.text) for line in lines] == [
            ("Ana", "Hello everyone, welcome"),
            ("Bo", "Thanks"),
        ]
        # A block that leaves the page before it settles is committed at once.
        await page.evaluate("() => window.standIn.caption(3, 'Cy', 'Bye')")
        await settle()
        batch = await meet.drain()
        book, out = fold_captions(book, batch.captions, 20.0, 5.0)
        assert out == ()
        book, out = fold_captions(book, (), 20.5, 5.0)
        assert [(line.speaker, line.text) for line in out] == [("Cy", "Bye")]

        # A chat message from someone comes once.
        await page.evaluate("() => window.standIn.chat('Dee', 'Please share the link')")
        await settle()
        first: PageBatch = await meet.drain()
        known, chat_lines = new_chat(frozenset(), first.chat)
        assert [(line.speaker, line.text) for line in chat_lines] == [("Dee", "Please share the link")]
        _, again = new_chat(known, first.chat)
        assert again == ()
        assert first.dropped == 0

        # Dot's own post shows in the chat region.
        await meet.post("hello")
        assert "hello" in await page.locator('div[aria-live="polite"]').inner_text()

        await meet.leave()
        assert await meet.state() == "ended"
        await page.close()


async def test_a_page_without_the_toggles_is_not_joined(browser: Browser) -> None:
    page: Page = await browser.new_page()
    await page.set_content("<button>Ask to join</button>")
    with pytest.raises(MeetPageError):
        await MeetPage(page).prepare("Dot")
    await page.close()


async def test_an_address_of_another_host_is_refused() -> None:
    assert meeting_address("https://meet.google.com/abc-defg-hij?authuser=1") == "https://meet.google.com/abc-defg-hij"
    with pytest.raises(MeetPageError):
        meeting_address("https://evil.example/abc-defg-hij")
