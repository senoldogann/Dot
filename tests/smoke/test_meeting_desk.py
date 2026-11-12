"""The meeting desk (item 1138) in a real headless Chromium, on a stand-in for Google Meet's page served at Meet's own
address by the browser's routing, over a real store and approval broker: Dot asks to join, is let in, tells the meeting
on the owner's yes, keeps the captions and the chat as lines, posts only on the owner's yes (a no, or free mode, posts
nothing), makes the notes with no model of its own and leaves the summary in the chat; a no to the announcement leaves
at once, with nothing kept. No network and no model."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from meet_stand_in import PAGE
from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright

from dotagent.agent.approvals import ApprovalBroker
from dotagent.config import MeetingsConfig
from dotagent.core.errors import RunConflict
from dotagent.events import EventBus
from dotagent.meetings.desk import ANNOUNCEMENT, MeetingChrome, MeetingDesk, MeetingError
from dotagent.meetings.notes import MeetingNotes, NoteAction
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, MeetingLineRecord, MeetingRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

URL: str = "https://meet.google.com/abc-defg-hij"
OWNER: str = "Ada"
_WAIT_S: float = 15.0
_CONFIG: MeetingsConfig = MeetingsConfig(
    name="Dot", admit_minutes=1.0, most_hours=1.0, settle_s=0.3, notes_minutes=60.0, keep_days=7, headless=True
)


@dataclass
class Room:
    store: Store
    broker: ApprovalBroker
    run: RunRecord
    told: list[tuple[str, str]]
    pages: list[Page]
    desk: MeetingDesk
    # Whether a run works in the meeting's chat now: the chat then takes no line (RunConflict), as keep_notice does.
    working: list[bool]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def playwright() -> AsyncIterator[Playwright]:
    async with async_playwright() as running:
        yield running


NOTED: list[str] = []


async def _noter(lines: Sequence[MeetingLineRecord], earlier: MeetingNotes, meeting_id: str) -> MeetingNotes:
    """Notes made of the lines by code, as the note maker would give them back; each call is counted."""
    NOTED.append(meeting_id)
    return MeetingNotes(
        decisions=tuple(line.text for line in lines if "decide" in line.text),
        actions=(NoteAction(who="Bo", what="send the deck", when="Friday"),),
        questions=(),
        owner_named=(),
    )


@pytest_asyncio.fixture(loop_scope="module")
async def room(tmp_path: Path, playwright: Playwright) -> AsyncIterator[Room]:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    browser: Browser = await playwright.chromium.launch(headless=True)
    try:
        bus: EventBus = EventBus(store)
        broker: ApprovalBroker = ApprovalBroker(store, bus, _WAIT_S)
        thread: ThreadRecord = await store.create_thread("standup", "test")
        run: RunRecord = await store.create_run(
            thread.id,
            "Join my standup",
            "task",
            "ollama:none",
            None,
            "test",
            "medium",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        told: list[tuple[str, str]] = []
        pages: list[Page] = []

        async def launch() -> MeetingChrome:
            context: BrowserContext = await browser.new_context()

            async def stand_in(route: Route) -> None:
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE)

            await context.route("https://meet.google.com/**", stand_in)
            page: Page = await context.new_page()
            pages.append(page)
            return MeetingChrome(context=context, page=page, close=context.close)

        working: list[bool] = [False]

        async def notice(thread_id: str, goal: str, text: str) -> None:
            if working[0]:
                raise RunConflict("a run works in the chat", {"thread_id": thread_id})
            told.append((goal, text))

        desk: MeetingDesk = MeetingDesk(store, broker, _noter, _CONFIG, launch, lambda: OWNER, notice)
        yield Room(store=store, broker=broker, run=run, told=told, pages=pages, desk=desk, working=working)
        await desk.aclose()
    finally:
        await browser.close()
        await store.close()


async def _answer(room: Room, approve: bool) -> ApprovalRecord:
    """Answer the next question of the run as the owner does, and return it."""
    for _ in range(int(_WAIT_S * 10)):
        pending: list[ApprovalRecord] = [
            record for record in await room.store.list_approvals("pending", 10) if record.run_id == room.run.id
        ]
        if pending:
            await room.broker.resolve(pending[0].id, approve, "owner", "")
            return pending[0]
        await asyncio.sleep(0.1)
    raise AssertionError("no question came")


async def _lines(room: Room, meeting_id: str, count: int) -> list[MeetingLineRecord]:
    for _ in range(int(_WAIT_S * 10)):
        meeting: MeetingRecord = await room.store.get_meeting(meeting_id)
        if meeting.lines >= count:
            return await room.store.meeting_lines(meeting_id, 0, 100)
        await asyncio.sleep(0.1)
    raise AssertionError(f"fewer than {count} lines came")


async def test_a_meeting_is_told_kept_posted_in_on_a_yes_and_summed_up(room: Room) -> None:
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(room.desk.join(URL, room.run.thread_id, room.run.id))
    asked: ApprovalRecord = await _answer(room, True)
    assert ANNOUNCEMENT.format(owner=OWNER) in asked.summary
    meeting: MeetingRecord = await joining
    assert meeting.status == "in_call" and meeting.admitted_at is not None
    page: Page = room.pages[-1]
    assert await page.evaluate("window.guestName") == "Dot"

    await page.evaluate("standIn.caption(1, 'Bo', 'we decide to ship on Friday')")
    await page.evaluate("standIn.chat('Cy', 'deck link later')")
    lines: list[MeetingLineRecord] = await _lines(room, meeting.id, 2)
    said: set[tuple[str, str, str]] = {(line.kind, line.speaker, line.text) for line in lines}
    assert ("caption", "Bo", "we decide to ship on Friday") in said
    assert ("chat", "Cy", "deck link later") in said
    # Dot's own announcement is not a line of the meeting.
    assert not any(ANNOUNCEMENT.format(owner=OWNER) in line.text for line in lines)

    # A post the owner refuses is not typed; one the owner says yes to is, once.
    refused: asyncio.Task[str] = asyncio.create_task(room.desk.post(meeting.id, room.run.id, "We agree."))
    await _answer(room, False)
    assert (await refused).startswith("Not posted")
    approved: asyncio.Task[str] = asyncio.create_task(room.desk.post(meeting.id, room.run.id, "Ship it Friday."))
    await _answer(room, True)
    assert await approved == "Posted in the meeting's chat."
    shown: str = await page.evaluate("document.querySelector('[aria-live=polite]').innerText")
    assert "Ship it Friday." in shown and "We agree." not in shown

    # The run that asks Dot to leave works in the chat: Dot is out of the call at once, and the notes wait for the chat.
    room.working[0] = True
    left: MeetingRecord = await asyncio.wait_for(room.desk.leave(meeting.id, "the owner asked Dot to leave"), 10.0)
    assert left.status == "ended" and left.ended_at is not None
    assert room.desk.busy() is None
    await asyncio.sleep(1.0)
    assert room.told == []
    room.working[0] = False
    await _told(room, 1)
    goal, text = room.told[-1]
    assert goal == "The notes of the meeting"
    assert "we decide to ship on Friday" in text and "Bo: send the deck (Friday)" in text
    assert (await room.store.get_meeting(meeting.id)).summary == text


async def _told(room: Room, count: int) -> None:
    for _ in range(int(_WAIT_S * 10)):
        if len(room.told) >= count:
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the chat was not told")


async def test_the_emergency_stop_leaves_at_once_and_asks_no_model_again(room: Room) -> None:
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(room.desk.join(URL, room.run.thread_id, room.run.id))
    await _answer(room, True)
    meeting: MeetingRecord = await joining
    asked: int = len(NOTED)
    room.desk.stop_all("the emergency stop")
    await _told(room, 1)
    assert len(NOTED) == asked
    assert (await room.store.get_meeting(meeting.id)).status == "ended"
    assert "not made again" in room.told[-1][1]


async def test_free_mode_does_not_skip_the_question_of_a_post(room: Room) -> None:
    room.broker.switch_free(True)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(room.desk.join(URL, room.run.thread_id, room.run.id))
    await _answer(room, True)
    meeting: MeetingRecord = await joining
    posting: asyncio.Task[str] = asyncio.create_task(room.desk.post(meeting.id, room.run.id, "Hello."))
    asked: ApprovalRecord = await _answer(room, False)
    assert "Hello." in asked.summary
    assert (await posting).startswith("Not posted")
    await room.desk.leave(meeting.id, "the test ends")


async def test_a_no_to_the_announcement_leaves_with_nothing_kept(room: Room) -> None:
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(room.desk.join(URL, room.run.thread_id, room.run.id))
    await _answer(room, False)
    with pytest.raises(MeetingError, match="not told"):
        await joining
    meetings: list[MeetingRecord] = await room.store.meetings_of_thread(room.run.thread_id)
    assert meetings[0].status == "failed" and meetings[0].lines == 0
    assert room.desk.busy() is None


async def test_a_post_that_is_too_long_or_two_paragraphs_is_refused_before_any_question(room: Room) -> None:
    with pytest.raises(MeetingError, match="500 characters"):
        await room.desk.post("mtg_none", room.run.id, "x" * 501)
    with pytest.raises(MeetingError, match="one paragraph"):
        await room.desk.post("mtg_none", room.run.id, "one\n\ntwo")
    with pytest.raises(MeetingError, match="this is not one"):
        await room.desk.join("https://evil.example/abc-defg-hij", room.run.thread_id, room.run.id)
