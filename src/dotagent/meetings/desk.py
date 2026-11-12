"""The meeting desk (item 1138, docs/meetings.md): Dot in the owner's Google Meet calls, watched by code with no model.

What holds, whatever a run or a meeting says:

- One meeting at a time, in a Chrome of its own (``<home>/meetings-profile``), where only Dot's own Google account may
  be signed in (``dotx browser meetings-login``): never the owner's profile, cookies or vault. Camera and microphone are
  fake devices, turned off before Dot asks to join.
- The tab stays on Meet: a navigation of it to another host leaves the meeting (Google's sign-in is told as such).
- Nothing is captured before the meeting is told: the fixed line :data:`ANNOUNCEMENT` is posted on the owner's yes, and
  a no, or no answer, leaves at once.
- Every post asks the owner, with its words whole, through :meth:`ApprovalBroker.request` itself: no mode, free mode
  included, and no "don't ask again" reaches it, since it is no decision of a run's gate.
- The meeting's words go to the store as lines and to the tool-less note maker (:mod:`dotagent.meetings.notes`), never
  to a run: a run reads them through ``meeting_read``, whose result is untrusted.
- It leaves when the meeting ends, when Dot is removed, after ``[meetings] most_hours``, at the emergency stop and when
  the daemon stops; a meeting a restart cut is marked failed. Its lines are deleted ``keep_days`` after it ended."""

import asyncio
import contextlib
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from dotagent.agent.approvals import ApprovalBroker, ApprovalOutcome
from dotagent.browser.keychain import keychain_switches, read_keychain_mode
from dotagent.config import BrowserConfig, MeetingsConfig
from dotagent.core.errors import DotError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.meetings.meet import MEET_HOST, MeetPage, MeetPageError, meeting_address
from dotagent.meetings.notes import NO_NOTES, MeetingNotes, notes_text
from dotagent.meetings.transcript import CaptionBook, fold_captions, new_chat
from dotagent.meetings.types import MeetingLine, MeetState, PageBatch
from dotagent.store.db import Store
from dotagent.store.models import MeetingLineRecord, MeetingRecord

log = get_logger(__name__)

MEETING_POST_TOOL: str = "meeting_post"
MEETING_LEAVE_TOOL: str = "meeting_leave"
# The line that tells the meeting Dot is there, posted on the owner's yes before anything is captured. Its one slot is
# the owner's name from Settings, never a model's words.
ANNOUNCEMENT: str = "Dot is taking notes for {owner}. Its notes stay on {owner}'s Mac."
# The most of a post, in characters, on its one line.
POST_CHARS: int = 500
# How often the page is read while Dot is in the call, and while it waits to be let in.
_READ_EVERY_S: float = 2.0
_WAIT_EVERY_S: float = 2.0
# Google's own sign-in, where a meeting that wants an account sends the tab: told, never followed.
_SIGN_IN_HOST: str = "accounts.google.com"
# The browser's flags of a meeting: fake camera and microphone, which Dot turns off anyway, no first-run screens, and
# the mark of automation left out, as Dot's own Chrome does.
_MEETING_ARGS: tuple[str, ...] = (
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
)
# How long the captions may stay off in the call (the host turned them off) before Dot leaves: it takes no notes
# another way.
_CAPTIONS_OFF_S: float = 60.0
# How long a page Dot does not know may stand while it waits to be let in.
_UNKNOWN_S: float = 30.0
# How long the summary waits for the chat to be free of a working run (the run that asked Dot to leave, the owner's
# next one); past it the summary stays on the meeting's record, which meeting_read gives.
_SUMMARY_WAIT_S: float = 3600.0
# How long leaving waits for Dot to be out of the call and its browser closed.
_LEAVE_WAIT_S: float = 30.0
# How often the lines of meetings over for ``keep_days`` are looked for.
_FORGET_EVERY_S: float = 6 * 3600.0


class MeetingError(DotError):
    """A meeting Dot could not join or act in, said for the owner: what happened and what to do."""


type Notice = Callable[[str, str, str], Awaitable[None]]
# The note maker: the notes of a meeting from its lines, its earlier notes and its id
# (:func:`dotagent.meetings.notes.make_notes`, with the daemon's router and fast model).
type Noter = Callable[[Sequence[MeetingLineRecord], MeetingNotes, str], Awaitable[MeetingNotes]]


async def untold(thread_id: str, goal: str, text: str) -> None:
    """The notice of a desk the daemon has not handed its run manager yet (:meth:`MeetingDesk.tell_with`)."""
    raise MeetingError("the meeting's chat cannot be told yet: the daemon is starting", {"thread_id": thread_id})


@dataclass
class _Live:
    """A meeting Dot is in: its record's id, its chat, the run that asked, its browser and page, and its watch."""

    meeting_id: str
    thread_id: str
    run_id: str
    chrome: "MeetingChrome"
    page: MeetPage
    stop: asyncio.Event
    why: list[str] = field(default_factory=list)
    posted: set[str] = field(default_factory=set)
    # Set once Dot is out of the call and its browser closed; the notes and the summary may still be on their way.
    left: asyncio.Event = field(default_factory=asyncio.Event)
    # Whether the summary is made of the notes as they stand, with no model asked again (the emergency stop).
    no_model: bool = False
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class MeetingChrome:
    """The Chrome a meeting runs in: its context, its one tab, and what closes both."""

    context: BrowserContext
    page: Page
    close: Callable[[], Awaitable[None]]


type Launcher = Callable[[], Awaitable[MeetingChrome]]


async def launch_meeting_chrome(profile: Path, browser: BrowserConfig, config: MeetingsConfig) -> MeetingChrome:
    """Dot's meeting Chrome on ``profile`` (only Dot's own Google account is signed in there), with fake camera and
    microphone, the keychain the profile's sign-in window used, and the page's own content rules set aside so that
    Dot's reading script runs on Meet's page."""
    await asyncio.to_thread(profile.mkdir, parents=True, exist_ok=True)
    playwright: Playwright = await async_playwright().start()
    try:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel=browser.channel,
            headless=config.headless,
            args=[*_MEETING_ARGS, *keychain_switches(await asyncio.to_thread(read_keychain_mode, profile))],
            viewport={"width": 1280, "height": 800},
            bypass_csp=True,
        )
    except PlaywrightError as error:
        await playwright.stop()
        raise MeetingError(
            "Dot's meeting browser did not start: is the meetings-login window still open?",
            {"profile": str(profile), "error": str(error)[:400]},
        ) from error
    page: Page = context.pages[0] if context.pages else await context.new_page()

    async def close() -> None:
        with contextlib.suppress(PlaywrightError):
            await context.close()
        await playwright.stop()

    return MeetingChrome(context=context, page=page, close=close)


# Characters that would make the owner's card show other words than the meeting gets: controls (but the line break)
# and the marks that turn the direction of text.
_HIDDEN: re.Pattern[str] = re.compile("[\u0000-\u0009\u000b-\u001f\u007f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_LINE_BREAK: re.Pattern[str] = re.compile(r"[\r\n\u2028\u2029]")


def post_refused(text: str) -> str | None:
    """Why ``text`` is no post Dot types into a meeting, or ``None``: empty, longer than :data:`POST_CHARS`, more than
    one line (Enter sends it in Meet's chat), or holding a control or a mark that turns the direction of text, which
    would show the owner other words than the meeting gets."""
    words: str = text.strip()
    if words == "":
        return "a post needs words"
    if len(words) > POST_CHARS:
        return f"a post is {POST_CHARS} characters at most ({len(words)} given)"
    if _LINE_BREAK.search(words):
        return "a post is one line"
    if _HIDDEN.search(words):
        return "a post holds no control characters or marks that turn the direction of text"
    return None


def on_meet(url: str) -> bool:
    """Whether ``url`` is a page of Meet's own host (or the blank page a new tab starts on)."""
    if url in ("", "about:blank"):
        return True
    return urlsplit(url).hostname == MEET_HOST


class MeetingDesk:
    """The daemon's meetings: joins one, watches it with no model, keeps its lines and notes, posts only on the owner's
    yes, and leaves. ``noter`` writes the notes, ``launch`` opens the meeting's Chrome, ``owner`` gives the owner's name
    as Settings has it now; :meth:`tell_with` gives it the way to keep a line in a chat."""

    def __init__(
        self,
        store: Store,
        approvals: ApprovalBroker,
        noter: Noter,
        config: MeetingsConfig,
        launch: "Launcher",
        owner: Callable[[], str],
    ) -> None:
        self._store: Store = store
        self._approvals: ApprovalBroker = approvals
        self._noter: Noter = noter
        self._config: MeetingsConfig = config
        self._launch: Launcher = launch
        self._owner: Callable[[], str] = owner
        self._notice: Notice = untold
        self._live: _Live | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._forgetter: asyncio.Task[None] | None = None
        # Whether the daemon handed the desk its way to tell the chats (:meth:`tell_with`).
        self._told: bool = False
        # The watches of the meetings, kept until they told their summary, after Dot left the call.
        self._finishing: set[asyncio.Task[None]] = set()

    def tell_with(self, notice: Notice) -> None:
        """Keep the meetings' summaries in their chats with ``notice``, once the daemon's run manager is there: only
        then may Dot join a meeting (a ``dotx`` command has no chat to tell)."""
        self._notice = notice
        self._told = True

    # ------------------------------------------------------------------ the daemon's start and stop

    async def start(self) -> None:
        """Mark the meetings a restart cut as failed, and start forgetting the lines of meetings long over."""
        for meeting in await self._store.open_meetings():
            await self._store.set_meeting_status(meeting.id, "failed", "Dot restarted during the meeting")
        self._forgetter = asyncio.create_task(self._forget_forever(), name="meetings-forget")

    async def aclose(self) -> None:
        """Leave the meeting Dot is in, as the daemon stops: out of the call at once, the summary of the notes as they
        stand, and what is still on its way cut after :data:`_LEAVE_WAIT_S`."""
        await self.leave_all("Dot stopped")
        for task in list(self._finishing):
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(task, _LEAVE_WAIT_S)
        if self._forgetter is not None:
            self._forgetter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._forgetter

    async def _forget_forever(self) -> None:
        while True:
            try:
                gone: int = await self._store.forget_meeting_lines(time.time() - self._config.keep_days * 86400.0)
                if gone:
                    log.info("meeting_lines_forgotten", lines=gone)
            except DotError as error:
                log.warning("meeting_lines_not_forgotten", error=error.message, context=error.context)
            await asyncio.sleep(_FORGET_EVERY_S)

    # ------------------------------------------------------------------ joining

    def busy(self) -> str | None:
        """The id of the meeting Dot is in, or ``None``."""
        return None if self._live is None else self._live.meeting_id

    async def join(self, url: str, thread_id: str, run_id: str) -> MeetingRecord:
        """Join the Meet call at ``url`` for the run ``run_id`` of the chat ``thread_id``: ask to be let in, wait for
        it, tell the meeting Dot is there on the owner's yes, and watch it. Returns the meeting in the call; raises
        :class:`MeetingError`, with the meeting recorded as failed, when it cannot."""
        try:
            address: str = meeting_address(url)
        except MeetPageError as error:
            raise MeetingError(error.message, error.context) from error
        if not self._told:
            raise MeetingError("Dot joins meetings from its chats, while its daemon runs: ask there", {"url": address})
        async with self._lock:
            if self._live is not None:
                raise MeetingError(
                    "Dot is in a meeting already: leave it first (meeting_leave)", {"meeting_id": self._live.meeting_id}
                )
            meeting: MeetingRecord = await self._store.add_meeting(address, thread_id, run_id)
            try:
                live: _Live = await self._open(meeting)
            except DotError as error:
                await self._store.set_meeting_status(meeting.id, "failed", error.message)
                raise
            self._live = live
        try:
            await self._let_in(live, address)
            await self._announce(live)
            await live.page.turn_captions_on()
            if not await live.page.captions_on():
                raise MeetingError(
                    "the meeting's captions could not be turned on: its host may have turned them off, and Dot does "
                    "not take notes without them",
                    {"url": address},
                )
            await live.page.watch()
        except BaseException as error:
            note: str = error.message if isinstance(error, DotError) else "the join was stopped"
            await self._close(live, "failed", note)
            raise
        live.task = asyncio.create_task(self._watch(live), name=f"meeting-{meeting.id}")
        self._finishing.add(live.task)
        live.task.add_done_callback(self._finishing.discard)
        return await self._store.set_meeting_status(meeting.id, "in_call", "Dot is in the call and takes notes")

    async def _open(self, meeting: MeetingRecord) -> _Live:
        """The meeting's Chrome (:attr:`_launch`), with one tab that may only stay on Meet."""
        chrome: MeetingChrome = await self._launch()
        page: Page = chrome.page
        live: _Live = _Live(
            meeting_id=meeting.id,
            thread_id=meeting.thread_id,
            run_id=meeting.run_id,
            chrome=chrome,
            page=MeetPage(page),
            stop=asyncio.Event(),
        )

        def wandered(frame_url: str) -> None:
            if not on_meet(frame_url):
                live.why.append(
                    "the meeting asked for a Google sign-in: sign Dot's own account in with dotx browser meetings-login"
                    if urlsplit(frame_url).hostname == _SIGN_IN_HOST
                    else f"the meeting's tab went to another site ({truncate_middle(frame_url, 120)})"
                )
                live.stop.set()

        page.on("framenavigated", lambda frame: wandered(frame.url) if frame == page.main_frame else None)
        return live

    async def _let_in(self, live: _Live, address: str) -> None:
        """Ask to join, then wait to be let in for ``admit_minutes``; any other end is a :class:`MeetingError`."""
        await live.page.open(address)
        state: MeetState = await live.page.prepare(self._config.name or "Dot")
        await self._store.set_meeting_status(live.meeting_id, "waiting", "Dot asked to join and waits to be let in")
        deadline: float = time.monotonic() + self._config.admit_minutes * 60.0
        # A page Dot does not know is given a moment, as Meet draws its next screen; one that stays so is told.
        unknown_since: float | None = None
        while state != "in_call":
            if live.stop.is_set():
                raise MeetingError(_why(live, "the join was stopped"), {"url": address})
            if state in ("refused", "ended", "removed", "sign_in"):
                raise MeetingError(_STATE_WORDS[state], {"url": address, "state": state})
            if state == "unknown":
                unknown_since = unknown_since if unknown_since is not None else time.monotonic()
                if time.monotonic() - unknown_since > _UNKNOWN_S:
                    raise MeetingError(_STATE_WORDS[state], {"url": address, "state": state})
            else:
                unknown_since = None
            if time.monotonic() > deadline:
                raise MeetingError(f"nobody let Dot in within {self._config.admit_minutes:g} minutes", {"url": address})
            await asyncio.sleep(_WAIT_EVERY_S)
            state = await live.page.state()

    async def _announce(self, live: _Live) -> None:
        """Post :data:`ANNOUNCEMENT` on the owner's yes; a no, or no answer, is a :class:`MeetingError`."""
        line: str = ANNOUNCEMENT.format(owner=self._owner())
        outcome: ApprovalOutcome = await self._ask(live, line, "Tell the meeting that Dot takes notes")
        if outcome.status != "approved":
            raise MeetingError(
                "Dot left without taking notes: the meeting was not told Dot is there", {"answer": outcome.status}
            )
        if live.stop.is_set():
            raise MeetingError(_why(live, "the join was stopped"), {"answer": outcome.status})
        live.posted.add(line)
        await live.page.post(line)

    async def _ask(self, live: _Live, text: str, why: str) -> ApprovalOutcome:
        """The owner's answer to a post of ``text`` in the meeting, asked in the run that asked for the meeting."""
        return await self._approvals.request(
            live.run_id,
            MEETING_POST_TOOL,
            f"{why}. Dot would post in the meeting's chat: {text}",
            "high",
            {"meeting_id": live.meeting_id, "text": text},
        )

    # ------------------------------------------------------------------ posting and leaving

    async def post(self, meeting_id: str, run_id: str, text: str) -> str:
        """Post ``text`` in the meeting's chat once the owner said yes to its words, asked in the run ``run_id``;
        returns what happened, in words for the run."""
        refused: str | None = post_refused(text)
        if refused is not None:
            raise MeetingError(refused, {"chars": len(text)})
        live: _Live = self._the_live(meeting_id)
        outcome: ApprovalOutcome = await self._approvals.request(
            run_id,
            MEETING_POST_TOOL,
            f"Dot would post in the meeting's chat: {text.strip()}",
            "high",
            {"meeting_id": meeting_id, "text": text.strip()},
        )
        if outcome.status != "approved":
            note: str = f" ({outcome.note})" if outcome.note else ""
            return f"Not posted: the owner's answer was {outcome.status}{note}."
        if self._live is not live or live.stop.is_set():
            return "Not posted: Dot left the meeting before the owner's yes."
        live.posted.add(text.strip())
        await live.page.post(text.strip())
        return "Posted in the meeting's chat."

    async def leave_asked(self, meeting_id: str, run_id: str) -> bool:
        """Whether the owner says yes to leaving the meeting ``meeting_id``, asked in the run ``run_id``."""
        self._the_live(meeting_id)
        outcome: ApprovalOutcome = await self._approvals.request(
            run_id,
            MEETING_LEAVE_TOOL,
            "Dot would leave the meeting and stop taking notes",
            "high",
            {"meeting_id": meeting_id},
        )
        return outcome.status == "approved"

    async def leave(self, meeting_id: str, why: str) -> MeetingRecord:
        """Leave the meeting ``meeting_id`` for ``why``: returns once Dot is out of the call (:data:`_LEAVE_WAIT_S` at
        most). The last notes and the summary come after, to the chat, once no run works there."""
        live: _Live = self._the_live(meeting_id)
        live.why.append(why)
        live.stop.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(live.left.wait(), _LEAVE_WAIT_S)
        return await self._store.get_meeting(meeting_id)

    async def leave_all(self, why: str) -> None:
        """Leave the meeting Dot is in, if it is in one, at once (the emergency stop, the daemon's stop): no model is
        asked for the notes again, and the summary is made of them as they stand."""
        if self._live is not None:
            self._live.no_model = True
            await self.leave(self._live.meeting_id, why)

    def stop_all(self, why: str) -> None:
        """Have Dot leave the meeting it is in, if it is in one, without waiting (the emergency stop): its watch sees
        the stop within :data:`_READ_EVERY_S`, leaves the call and closes its browser, and asks no model again."""
        if self._live is not None:
            self._live.no_model = True
            self._live.why.append(why)
            self._live.stop.set()

    def _the_live(self, meeting_id: str) -> _Live:
        if self._live is None or self._live.meeting_id != meeting_id:
            raise MeetingError("Dot is not in that meeting now", {"meeting_id": meeting_id})
        return self._live

    # ------------------------------------------------------------------ watching

    async def _watch(self, live: _Live) -> None:
        """Read the page every :data:`_READ_EVERY_S` until the meeting is over for Dot: lines to the store, the notes
        again every ``notes_minutes`` while lines come, and the summary at the end. The notes are made beside the
        reading, never in its way: a stop is seen within :data:`_READ_EVERY_S` while the note maker works, and cuts it.
        Whatever ends the watch, Dot leaves the call and closes its browser."""
        notes: MeetingNotes = NO_NOTES
        making: asyncio.Task[tuple[MeetingNotes, str | None]] | None = None
        end_note: str = "the meeting ended"
        status: str = "ended"
        try:
            end_note, notes, making = await self._read_until_over(live)
            if making is not None and making.done() and not making.cancelled():
                notes = making.result()[0]
        except DotError as error:
            log.error("meeting_watch_failed", meeting_id=live.meeting_id, error=error.message, context=error.context)
            status, end_note = "failed", error.message
        except PlaywrightError as error:
            status, end_note = "failed", f"Dot lost the meeting's page ({str(error)[:200]})"
            log.error("meeting_watch_failed", meeting_id=live.meeting_id, error=end_note)
        except sqlite3.Error as error:
            status, end_note = "failed", f"Dot could not keep the meeting's lines ({error})"
            log.error("meeting_watch_failed", meeting_id=live.meeting_id, error=end_note)
        finally:
            if making is not None and not making.done():
                making.cancel()
            if not live.left.is_set():
                await self._close(live, status, end_note)
        if status == "ended":
            await self._sum_up(live, notes, end_note)
        else:
            await self._tell(live, "Dot left the meeting", f"Dot left the meeting: {end_note}")

    async def _read_until_over(
        self, live: _Live
    ) -> tuple[str, MeetingNotes, asyncio.Task[tuple[MeetingNotes, str | None]] | None]:
        """The reading of the call until it is over for Dot: why it ended, the notes as they stand, and the note maker
        still at work, if it is."""
        book: CaptionBook = CaptionBook()
        seen_chat: frozenset[str] = frozenset()
        notes: MeetingNotes = NO_NOTES
        making: asyncio.Task[tuple[MeetingNotes, str | None]] | None = None
        noted: int = 0
        next_notes: float = time.monotonic() + self._config.notes_minutes * 60.0
        ends: float = time.monotonic() + self._config.most_hours * 3600.0
        end_note: str = "the meeting ended"
        captions_off_since: float | None = None
        while not live.stop.is_set():
            if time.monotonic() > ends:
                end_note = f"Dot left after {self._config.most_hours:g} hours, its most"
                break
            state: MeetState = await live.page.state()
            if state in ("ended", "removed"):
                end_note = _STATE_WORDS[state]
                break
            if await live.page.captions_on():
                captions_off_since = None
            else:
                captions_off_since = captions_off_since if captions_off_since is not None else time.monotonic()
                if time.monotonic() - captions_off_since > _CAPTIONS_OFF_S:
                    end_note = "the meeting's captions were turned off, and Dot takes no notes without them"
                    break
            batch: PageBatch = await live.page.drain()
            if batch.dropped:
                log.warning("meeting_page_dropped", meeting_id=live.meeting_id, dropped=batch.dropped)
            book, captions = fold_captions(book, batch.captions, time.time(), self._config.settle_s)
            seen_chat, chat = new_chat(seen_chat, batch.chat)
            # Dot's own posts come back in the chat: they are not the meeting's words.
            theirs: tuple[MeetingLine, ...] = tuple(line for line in chat if line.text not in live.posted)
            total: int = await self._keep(live, (*captions, *theirs))
            if making is not None and making.done():
                notes = making.result()[0]
                making = None
            if making is None and total > noted and time.monotonic() >= next_notes:
                making = asyncio.create_task(self._noted(live, notes), name=f"meeting-notes-{live.meeting_id}")
                noted = total
                next_notes = time.monotonic() + self._config.notes_minutes * 60.0
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(live.stop.wait(), _READ_EVERY_S)
        # What stood on the page last is a line too.
        _, rest = fold_captions(book, (), time.time() + self._config.settle_s, self._config.settle_s)
        await self._keep(live, rest)
        if live.why:
            end_note = live.why[-1]
        return end_note, notes, making

    async def _keep(self, live: _Live, lines: tuple[MeetingLine, ...]) -> int:
        return await self._store.add_meeting_lines(
            live.meeting_id, [(line.kind, line.speaker, line.text, line.at) for line in lines]
        )

    async def _noted(self, live: _Live, earlier: MeetingNotes) -> tuple[MeetingNotes, str | None]:
        """The notes made again from the meeting's lines, and ``None``; a note maker that fails leaves the earlier ones,
        with what went wrong, logged."""
        meeting: MeetingRecord = await self._store.get_meeting(live.meeting_id)
        lines = await self._store.meeting_lines(live.meeting_id, 0, meeting.lines)
        try:
            notes: MeetingNotes = await self._noter(lines, earlier, live.meeting_id)
        except DotError as error:
            log.warning("meeting_notes_failed", meeting_id=live.meeting_id, error=error.message, context=error.context)
            return earlier, error.message
        await self._store.set_meeting_notes(live.meeting_id, notes.model_dump_json())
        return notes, None

    async def _sum_up(self, live: _Live, notes: MeetingNotes, end_note: str) -> None:
        """The notes made once more from every line (as they stand, after the emergency stop), and left in the chat as
        the meeting's summary, which says when they could not be made again."""
        final: MeetingNotes = notes
        unmade: str | None = "Dot was stopped, so they were not made again" if live.no_model else None
        if not live.no_model:
            final, unmade = await self._noted(live, notes)
        meeting: MeetingRecord = await self._store.get_meeting(live.meeting_id)
        stale: str = "" if unmade is None else f"These are the notes as they last stood: {unmade}.\n\n"
        text: str = (
            f"Dot left the meeting ({end_note}). {meeting.lines} lines were kept for "
            f"{self._config.keep_days} days.\n\n{stale}{notes_text(final, self._owner())}"
        )
        await self._store.set_meeting_summary(live.meeting_id, text)
        await self._tell(live, "The notes of the meeting", text)

    async def _tell(self, live: _Live, goal: str, text: str) -> None:
        """Keep ``text`` in the meeting's chat, waiting while a run works there; past :data:`_SUMMARY_WAIT_S` it is
        logged instead, and stays on the meeting's record."""
        deadline: float = time.monotonic() + _SUMMARY_WAIT_S
        while True:
            try:
                await self._notice(live.thread_id, goal, text)
                return
            except RunConflict as error:
                # A run works in the chat: the line waits for it to end.
                if time.monotonic() > deadline:
                    log.warning("meeting_summary_not_told", meeting_id=live.meeting_id, error=error.message)
                    return
            except DotError as error:
                # Anything else will not pass by waiting (the chat was deleted): the summary stays on the record.
                log.warning(
                    "meeting_summary_not_told", meeting_id=live.meeting_id, error=error.message, context=error.context
                )
                return
            await asyncio.sleep(5.0)

    async def _close(self, live: _Live, status: str, note: str) -> None:
        """Leave the call and close the meeting's browser; the meeting is ``ended`` or ``failed`` with ``note``, and
        whoever waits for Dot to be out of the call is told."""
        try:
            with contextlib.suppress(PlaywrightError, DotError):
                await live.page.leave()
            with contextlib.suppress(PlaywrightError):
                await live.chrome.close()
            if self._live is live:
                self._live = None
            await self._store.set_meeting_status(live.meeting_id, "ended" if status == "ended" else "failed", note)
        finally:
            live.left.set()


def _why(live: _Live, otherwise: str) -> str:
    return live.why[-1] if live.why else otherwise


_STATE_WORDS: dict[MeetState, str] = {
    "joining": "Dot is asking to join",
    "waiting": "Dot waits to be let in",
    "in_call": "Dot is in the call",
    "refused": "the meeting did not let Dot in",
    "ended": "the meeting ended",
    "removed": "Dot was removed from the meeting",
    "sign_in": "the meeting asked for a Google sign-in: sign Dot's own account in with dotx browser meetings-login",
    "unknown": "Meet showed a page Dot does not know; Meet may have changed, and Dot did not go on",
}
