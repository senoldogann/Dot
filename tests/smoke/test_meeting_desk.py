"""The meeting desk (item 1138) in a real headless Chromium, on a stand-in for Google Meet's page served at Meet's own
address by the browser's routing, over a real store and approval broker: Dot asks to join, is let in, tells the meeting
it is there, keeps the captions and the chat as lines, answers whoever names it with no question to the owner (item
1150), posts a run's words only on the owner's yes (a no, or free mode, posts nothing), makes the notes with no model of
its own and leaves the summary in the chat. No network and no model."""

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
from dotagent.meetings import desk as desk_module
from dotagent.meetings.desk import ANNOUNCEMENT, Launcher, MeetingChrome, MeetingDesk, MeetingError, Notice
from dotagent.meetings.notes import MeetingNotes, NoteAction
from dotagent.meetings.types import MeetingLine
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, MeetingLineRecord, MeetingRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

URL: str = "https://meet.google.com/abc-defg-hij"
OWNER: str = "Ada"
_WAIT_S: float = 15.0
_CONFIG: MeetingsConfig = MeetingsConfig(
    name="Dot",
    answers=True,
    admit_minutes=1.0,
    most_hours=1.0,
    settle_s=0.3,
    notes_minutes=60.0,
    keep_days=7,
    headless=True,
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
    launch: Launcher
    notice: Notice


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


ANSWERED: list[str] = []


async def _answerer(lines: Sequence[MeetingLineRecord], question: MeetingLine, meeting_id: str) -> str:
    """Answers as the answerer would give them back, by code: the date asked of, or nothing to a thanks."""
    ANSWERED.append(question.text)
    return "Friday, as Bo said." if "ship" in question.text else ""


async def _chat_shown(page: Page) -> str:
    shown: object = await page.evaluate("document.querySelector('[aria-live=polite]').innerText")
    return shown if isinstance(shown, str) else ""


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

        desk: MeetingDesk = MeetingDesk(store, broker, _noter, _answerer, _CONFIG, launch, lambda: OWNER)
        desk.tell_with(notice)
        yield Room(
            store=store,
            broker=broker,
            run=run,
            told=told,
            pages=pages,
            desk=desk,
            working=working,
            launch=launch,
            notice=notice,
        )
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
    meeting: MeetingRecord = await room.desk.join(URL, room.run.thread_id, room.run.id, None)
    assert meeting.status == "in_call" and meeting.admitted_at is not None
    page: Page = room.pages[-1]
    assert await page.evaluate("window.guestName") == "Dot"
    # The meeting is told Dot is there once it is let in, with no question to the owner (item 1150).
    assert ANNOUNCEMENT.format(owner=OWNER) in await _chat_shown(page)

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
    shown: str = await _chat_shown(page)
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
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(
        room.desk.join(URL, room.run.thread_id, room.run.id, None)
    )
    meeting: MeetingRecord = await joining
    asked: int = len(NOTED)
    room.desk.stop_all("the emergency stop")
    await _told(room, 1)
    assert len(NOTED) == asked
    assert (await room.store.get_meeting(meeting.id)).status == "ended"
    assert "not made again" in room.told[-1][1]


async def test_free_mode_does_not_skip_the_question_of_a_post(room: Room) -> None:
    room.broker.switch_free(True)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(
        room.desk.join(URL, room.run.thread_id, room.run.id, None)
    )
    meeting: MeetingRecord = await joining
    posting: asyncio.Task[str] = asyncio.create_task(room.desk.post(meeting.id, room.run.id, "Hello."))
    asked: ApprovalRecord = await _answer(room, False)
    assert "Hello." in asked.summary
    assert (await posting).startswith("Not posted")
    await room.desk.leave(meeting.id, "the test ends")


async def test_a_line_that_names_dot_is_answered_in_the_chat_without_asking_and_kept(room: Room) -> None:
    """Item 1150: whoever names Dot is answered in the chat by the answerer, with no question to the owner; a line that
    only speaks of Dot, Dot's own answer coming back and a question the answerer leaves empty are not answered."""
    meeting: MeetingRecord = await room.desk.join(URL, room.run.thread_id, room.run.id, None)
    page: Page = room.pages[-1]
    asked_before: int = len(await room.store.list_approvals("pending", 100))
    ANSWERED.clear()
    await page.evaluate("standIn.chat('Cy', 'Bo will tell Dot later')")
    await page.evaluate("standIn.chat('Cy', '@Dot when do we ship?')")
    for _ in range(int(_WAIT_S * 10)):
        if "Friday, as Bo said." in await _chat_shown(page):
            break
        await asyncio.sleep(0.1)
    assert "Friday, as Bo said." in await _chat_shown(page)
    await page.evaluate("standIn.caption(7, 'Bo', 'Dot, thanks')")
    lines: list[MeetingLineRecord] = await _lines(room, meeting.id, 4)
    for _ in range(int(_WAIT_S * 10)):
        if len(ANSWERED) >= 2:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    assert ANSWERED == ["@Dot when do we ship?", "Dot, thanks"]
    lines = await room.store.meeting_lines(meeting.id, 0, 100)
    assert ("dot", "Dot", "Friday, as Bo said.") in {(line.kind, line.speaker, line.text) for line in lines}
    # Nothing was asked of the owner, and the empty answer posted nothing.
    assert len(await room.store.list_approvals("pending", 100)) == asked_before
    assert (await _chat_shown(page)).count("Friday, as Bo said.") == 1
    await room.desk.leave(meeting.id, "the test ends")


async def test_a_join_that_is_not_the_owners_own_word_asks_them_first_in_every_mode(room: Room) -> None:
    """Item 1150: a link in a mail is no invitation. A join asked by a run that read outside content waits for the
    owner's yes, free mode too, and a no opens no browser and keeps no meeting in the call."""
    room.broker.switch_free(True)
    opened: int = len(room.pages)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(
        room.desk.join(URL, room.run.thread_id, room.run.id, "a task that read a mail asks for it")
    )
    asked: ApprovalRecord = await _answer(room, False)
    assert "Dot would join the meeting" in asked.summary and "a task that read a mail asks for it" in asked.summary
    with pytest.raises(MeetingError, match="did not say yes"):
        await joining
    assert len(room.pages) == opened and room.desk.busy() is None
    room.broker.switch_free(False)


async def test_questions_that_get_no_answer_still_count_against_the_minute(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-push review of item 1150: the cap counts questions put to the model, so a flood of questions whose
    answers are empty or refused does not call the model without end."""
    monkeypatch.setattr(desk_module, "_ANSWERS_A_MINUTE", 2)
    meeting: MeetingRecord = await room.desk.join(URL, room.run.thread_id, room.run.id, None)
    ANSWERED.clear()
    for sender in ("Ann", "Ben", "Cid"):
        await room.pages[-1].evaluate(f"standIn.chat('{sender}', 'Dot, thanks from {sender}')")
    await _lines(room, meeting.id, 3)
    for _ in range(int(_WAIT_S * 10)):
        if len(ANSWERED) >= 2:
            break
        await asyncio.sleep(0.1)
    # The third waits for the minute, so it is not put to the model now.
    await asyncio.sleep(1.5)
    assert len(ANSWERED) == 2
    await room.desk.leave(meeting.id, "the test ends")


async def test_a_desk_whose_answers_are_off_answers_nothing(room: Room) -> None:
    quiet: MeetingsConfig = _CONFIG.model_copy(update={"answers": False})
    desk: MeetingDesk = MeetingDesk(room.store, room.broker, _noter, _answerer, quiet, room.launch, lambda: OWNER)
    desk.tell_with(room.notice)
    meeting: MeetingRecord = await desk.join(URL, room.run.thread_id, room.run.id, None)
    ANSWERED.clear()
    await room.pages[-1].evaluate("standIn.chat('Cy', '@Dot when do we ship?')")
    await _lines(room, meeting.id, 1)
    await asyncio.sleep(1.0)
    assert ANSWERED == []
    await desk.leave(meeting.id, "the test ends")
    await desk.aclose()


async def test_a_post_that_is_too_long_or_two_paragraphs_is_refused_before_any_question(room: Room) -> None:
    with pytest.raises(MeetingError, match="500 characters"):
        await room.desk.post("mtg_none", room.run.id, "x" * 501)
    with pytest.raises(MeetingError, match="one line"):
        await room.desk.post("mtg_none", room.run.id, "one\n\ntwo")
    with pytest.raises(MeetingError, match="this is not one"):
        await room.desk.join("https://evil.example/abc-defg-hij", room.run.thread_id, room.run.id, None)


async def test_captions_turned_off_in_the_call_have_dot_leave_and_say_why(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desk_module, "_CAPTIONS_OFF_S", 0.5)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(
        room.desk.join(URL, room.run.thread_id, room.run.id, None)
    )
    meeting: MeetingRecord = await joining
    told: int = len(room.told)
    # The host turns the captions off: the stand-in's toggle, as Meet's.
    await room.pages[-1].click("button[aria-label='Turn off captions']")
    await _told(room, told + 1)
    ended: MeetingRecord = await room.store.get_meeting(meeting.id)
    assert ended.status == "ended" and "captions were turned off" in ended.note
    assert "captions were turned off" in room.told[-1][1]


async def test_a_desk_the_daemon_did_not_hand_its_chats_joins_nothing(room: Room) -> None:
    lone: MeetingDesk = MeetingDesk(room.store, room.broker, _noter, _answerer, _CONFIG, _no_chrome, lambda: OWNER)
    with pytest.raises(MeetingError, match="while its daemon runs"):
        await lone.join(URL, room.run.thread_id, room.run.id, None)


async def _no_chrome() -> MeetingChrome:
    raise AssertionError("no browser is opened for a desk that cannot tell its chats")


async def test_a_stop_while_the_notes_are_made_leaves_the_call_at_once(room: Room) -> None:
    """The note maker may take minutes on a long meeting; a stop does not wait for it (the pre-push review)."""
    started: asyncio.Event = asyncio.Event()

    async def slow(lines: Sequence[MeetingLineRecord], earlier: MeetingNotes, meeting_id: str) -> MeetingNotes:
        started.set()
        await asyncio.sleep(600)
        return earlier

    quick: MeetingsConfig = _CONFIG.model_copy(update={"notes_minutes": 0.01})
    desk: MeetingDesk = MeetingDesk(room.store, room.broker, slow, _answerer, quick, room.launch, lambda: OWNER)
    desk.tell_with(room.notice)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(desk.join(URL, room.run.thread_id, room.run.id, None))
    meeting: MeetingRecord = await joining
    await room.pages[-1].evaluate("standIn.caption(9, 'Bo', 'a long meeting')")
    await asyncio.wait_for(started.wait(), _WAIT_S)
    told: int = len(room.told)
    desk.stop_all("the emergency stop")
    for _ in range(100):
        if (await room.store.get_meeting(meeting.id)).status == "ended":
            break
        await asyncio.sleep(0.1)
    assert (await room.store.get_meeting(meeting.id)).status == "ended"
    # The note maker the stop cut leaves a summary all the same, of the notes as they stood.
    await _told(room, told + 1)
    assert "not made again" in room.told[-1][1]
    await desk.aclose()


async def test_leaving_asked_by_a_run_that_read_outside_content_waits_for_the_owner(room: Room) -> None:
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(
        room.desk.join(URL, room.run.thread_id, room.run.id, None)
    )
    meeting: MeetingRecord = await joining
    asked: asyncio.Task[bool] = asyncio.create_task(room.desk.leave_asked(meeting.id, room.run.id))
    await _answer(room, False)
    assert await asked is False
    assert room.desk.busy() == meeting.id
    await room.desk.leave(meeting.id, "the test ends")


async def test_a_call_whose_page_is_lost_is_summed_up_with_the_notes_it_had(room: Room) -> None:
    quick: MeetingsConfig = _CONFIG.model_copy(update={"notes_minutes": 0.01})
    desk: MeetingDesk = MeetingDesk(room.store, room.broker, _noter, _answerer, quick, room.launch, lambda: OWNER)
    desk.tell_with(room.notice)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(desk.join(URL, room.run.thread_id, room.run.id, None))
    meeting: MeetingRecord = await joining
    await room.pages[-1].evaluate("standIn.caption(5, 'Bo', 'we decide on the budget')")
    for _ in range(100):
        if (await room.store.get_meeting(meeting.id)).notes:
            break
        await asyncio.sleep(0.1)
    told: int = len(room.told)
    await room.pages[-1].close()
    await _told(room, told + 1)
    assert (await room.store.get_meeting(meeting.id)).status == "failed"
    assert "we decide on the budget" in room.told[-1][1]
    await desk.aclose()


async def test_a_stop_after_dot_left_cuts_the_note_maker_of_the_summary(room: Room) -> None:
    """The owner asks Dot to leave, the last notes are slow, and the emergency stop comes: no model is asked to the
    end, and the summary holds the notes as they stood (the pre-push review)."""
    started: asyncio.Event = asyncio.Event()

    async def slow(lines: Sequence[MeetingLineRecord], earlier: MeetingNotes, meeting_id: str) -> MeetingNotes:
        started.set()
        await asyncio.sleep(600)
        return earlier

    desk: MeetingDesk = MeetingDesk(room.store, room.broker, slow, _answerer, _CONFIG, room.launch, lambda: OWNER)
    desk.tell_with(room.notice)
    joining: asyncio.Task[MeetingRecord] = asyncio.create_task(desk.join(URL, room.run.thread_id, room.run.id, None))
    meeting: MeetingRecord = await joining
    told: int = len(room.told)
    await desk.leave(meeting.id, "the owner asked Dot to leave")
    await asyncio.wait_for(started.wait(), _WAIT_S)
    desk.stop_all("the emergency stop")
    await _told(room, told + 1)
    assert "not made again" in room.told[-1][1]
    await desk.aclose()
