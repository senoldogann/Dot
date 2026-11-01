"""Dot's own initiative (item 715): while the Mac is idle, Dot does for the owner what is on its own list, one thing at
a time, each in a chat of its own; when nothing is on it, it looks for work now and then. An idea of makecindy/cindy's
companions (Apache-2.0), which wake on events and a list rather than on a bare timer: the code is Dot's own.

What holds:

- It is off until the owner switches it on (``proactive.todos.read_initiative``), and the emergency stop holds it.
- It starts nothing while one of the Mac's runs works, or while the owner used the keyboard, the mouse or the trackpad
  in the last ``[initiative] idle_minutes``; one of its runs at a time, ``gap_minutes`` apart, ``runs_per_day`` at most.
- Its goals are Dot's words: no reviewer takes them as the owner's instruction (``RunManager.start_own``), and a thing a
  run put on the list after reading content third parties can author is done by a run that starts tainted.
- A thing is tried ``attempts`` times at most; then it is let go and the owner is told. A run the owner stopped lets its
  thing go at once; one the daemon's stop or the emergency stop ended leaves it to do.
- A thing whose chat holds a question or a step left for the owner, or a run at work, is not started again in another
  chat; the run their answer starts in that chat goes on with it, and its end is the thing's (item 868).
"""

import asyncio
import contextlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from Quartz import CGEventSourceSecondsSinceLastEventType, kCGAnyInputEventType, kCGEventSourceStateHIDSystemState

from dotagent.agent.manager import RunManager, goes_on_from, thread_title
from dotagent.agent.resume import RESUME_SOURCE, cut_off_by
from dotagent.config import InitiativeConfig
from dotagent.core.errors import DotError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path
from dotagent.notify.owner import tell_owner
from dotagent.proactive import INITIATIVE_SOURCE, NOTICE_SOURCE, WATCHER_SOURCE
from dotagent.proactive.brief import BRIEF_CLOSE, BRIEF_OPEN, Quoted, record_parts
from dotagent.proactive.text import one_line, quoted
from dotagent.proactive.todos import (
    LET_GO_BY_ITS_RUN,
    OWNER_SOURCE,
    InitiativeState,
    note_look,
    read_initiative,
    todo_key,
)
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.endings import PREFIX_EMERGENCY_STOP, RESULT_SHUTDOWN
from dotagent.store.models import WORKING_STATUSES, RunMode, RunRecord, TodoRecord

log = get_logger(__name__)

LOOK_TITLE: str = "Looking for work to do for you"
_DAY_S: float = 86_400.0
_NOTE_CHARS: int = 300
_TELL_CHARS: int = 140
_LISTED: int = 30
# The most things a count of what Dot's own work came to reads (item 914).
_REVIEWED: int = 100
_RECENT: int = 15
_QUOTE_CHARS: int = 160
# The newest runs of a thing's chat read for one the owner started there since (a Resume).
_LATER_READ: int = 10
# How long no run of its own is tried after one could not be started (a model or a setting that refuses it).
_AFTER_A_FAILED_START_S: float = 600.0
# The page whose card lists the things (item 715), which a tell of a thing no run tried opens.
_LIST_ROUTE: str = "#/schedules"
_STATE_WORDS: dict[str, str] = {"open": "to do", "doing": "being done", "done": "done", "dropped": "let go"}


def _is_urgent(todo: TodoRecord) -> bool:
    """A thing the owner put on the list themselves, marked ``[URGENT]`` in its title or why: it may jump the idle and
    active checks (item 809). A thing a run put there, or one that came of content third parties can author (a page, a
    mail), is never urgent however it is marked: a page could else have Dot start runs on a Mac the owner works on."""
    return todo.source == OWNER_SOURCE and not todo.tainted and "[URGENT]" in f"{todo.title} {todo.why}".upper()


# The chats whose titles are not the owner's words: a watcher's carries a mail's subject, Dot's own a thing of its list.
_NOT_THE_OWNERS_CHATS: frozenset[str] = frozenset({WATCHER_SOURCE, NOTICE_SOURCE, INITIATIVE_SOURCE})
# How far back the run that looks for work reads the failed runs, when it never looked before.
_FIRST_LOOK_S: float = 7 * _DAY_S
# Monday is 0: the weekdays are those before Saturday (item 826).
_SATURDAY: int = 5

_DO_GOAL: str = "On my own initiative: {title}"
_DO_OPENING: str = """You work on your own initiative for the owner (item 715): nobody asked for this in this chat and \
nobody watches it. It is a thing on your own list.

The work: {title}
Why it helps them: {why}

First check that the work is still to do, on the thing itself (the file, the page, the mail, the repository): the \
note it came from may be out of date (item 912). Done already: say where, change nothing, and end. Then do it as far \
as it goes without them. What only they can decide, leave as a question (ask_user) and go on with the rest. A thing \
that says no work you can do (empty, or a word with no task in it): say so in a sentence and end. Work the owner said \
not to do (a rule of theirs you remember or read in a chat): do none of it and ask nothing, let the thing go with \
todo_drop, their words as the reason, and end. When it is done, bring the note or plan its why names up to date (a \
line that says it is done, and where), so that it no longer reads as open; of a thing kept in several copies, change \
the one the others are made from. Dot keeps the list: as this run ends it marks the thing done, or to try again when \
the run fails; never change the list otherwise, nor Dot's database, yourself (item 821). End with one or two \
sentences: what you did, and where the result is."""
# The thing that counts what Dot's own work came to (item 914): Dot puts it on its list every [initiative]
# review_every_days, and it is done as any thing is, with an opening of its own. Without it nobody could say whether
# the work helped: the owner's question of 2026-11-01, after 64 runs in four days.
REVIEW_SOURCE: str = "review"
REVIEW_FILE: str = "own-work-review.md"
_REVIEW_TITLE: str = "Count what my own work since {since} came to"
_REVIEW_OPENING: str = """You count what your own work for the owner came to (item 914): nobody asked for it in this \
chat. The record below is what you did on your own since {since}: each thing done or let go, and what it cost.

For each thing done that something can come back from (a mail, an offer or an application sent, a page or a fix put \
live), look where the answer would be: the mail, the page itself, the account. Then write {file} in the workspace, \
the whole file anew: a line for each thing, what was done and what came of it with where you saw it, or "nothing \
yet"; the counts of the record; and at most three lines on what is worth more of your time and what to stop doing. \
Change nothing else and send nothing. End with two sentences for the owner: what the work brought, and the one change \
you would make.

{record}"""
_LOOK_GOAL: str = "On my own initiative: look for work that helps the owner"
_LOOK_OPENING: str = """You look for work to do for the owner on your own initiative (item 715). Read what you \
remember of them (memory_search: their projects, their wishes, what they asked to be done later, and what they said \
never to do or not to touch), the record below, what your last count of your own work found ({review_file} in the \
workspace, when it is there, item 914), \
the chats they had lately, and their mail and calendar when an account is connected. Then put at most three things on \
your list with todo_add (its tools are on from your first step, item 912): each a concrete piece \
of work you can do alone, which they would thank you for, which moves one of their objectives below forward when \
there is one (say which in its why, and leave out what a work order below already covers), which is not on the \
list yet and was not done (what came of each thing done is below: the same work in other words is done too; a note \
or plan that says a thing is open may be out of date, so check the thing itself first, item 911), which they \
did not say not to do, and which is not what was let go below for that reason. Put nothing \
on it when nothing is worth it; one you put on it by mistake, take back with todo_drop (item 821). Change nothing \
else. End with one sentence: what you put on the list, or that you found nothing.

{record}"""


def in_peak(now: float, hours: tuple[int, ...]) -> bool:
    """Whether ``now`` (seconds since the epoch) falls in ``hours`` (``[first, second)`` of a weekday, in UTC; item
    826): Ollama Cloud's dearer hours are 12 to 18 on weekdays, twice the price of the rest of the week."""
    if not hours:
        return False
    moment: datetime = datetime.fromtimestamp(now, UTC)
    return moment.weekday() < _SATURDAY and hours[0] <= moment.hour < hours[1]


def idle_seconds() -> float | None:
    """How long nobody touched the keyboard, the mouse or the trackpad, in seconds; ``None`` when macOS cannot say,
    which counts as not idle."""
    try:
        seconds: object = CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
        )
    except Exception:
        log.warning("idle_time_unknown", exc_info=True)
        return None
    return float(seconds) if isinstance(seconds, int | float) else None


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


async def _listed(store: Store, clean: frozenset[str] = frozenset()) -> list[str]:
    """The list as the run that looks for work reads it: what is on it, and what was done or let go lately. The title
    of a thing a tainted run put on it is not quoted, only counted: it may hold what a mail or a page said (the code
    review of item 715), and ``proactive.todos.add_todo`` keeps it from being listed twice without reading it. Why a
    thing was let go is quoted only when the run that wrote it is in ``clean``, those that ended having read nothing
    third parties can author (the code review of item 878)."""
    kept: list[TodoRecord] = await store.list_todos(("open", "doing"), _LISTED)
    lately: list[TodoRecord] = [
        todo
        for todo in await store.recent_todos(time.time() - 30 * _DAY_S, _LISTED)
        if todo.status in ("done", "dropped")
    ]
    lines: list[str] = [
        f"- {_STATE_WORDS[todo.status]}{_when_ended(todo)}: {quoted(todo.title, _QUOTE_CHARS)}"
        f"{_why_let_go(todo, clean)}{_what_came(todo, clean)}"
        for todo in [*kept, *lately]
        if not todo.tainted
    ]
    unquoted: int = sum(1 for todo in [*kept, *lately] if todo.tainted)
    if unquoted:
        lines.append(f"- and {unquoted} more that came from content third parties wrote, not quoted here")
    return ["Your list:", *(lines or ["- nothing yet"])]


def _day(at: float) -> str:
    return datetime.fromtimestamp(at).strftime("%d.%m")


def _when_ended(todo: TodoRecord) -> str:
    """The day a thing was done or let go, after its state (item 911)."""
    if todo.status not in ("done", "dropped"):
        return ""
    return f" on {_day(todo.updated_at)}"


def _what_came(todo: TodoRecord, clean: frozenset[str]) -> str:
    """What came of a thing done, after its title (item 911): its run's first line, where the result is, so that the
    look does not put the same work on the list again in other words as if it were still open. Quoted only when that
    run is ``clean``, as why a thing was let go is."""
    if todo.status != "done" or not todo.note or todo.run_id is None or todo.run_id not in clean:
        return ""
    return f", what came of it: {quoted(todo.note, _QUOTE_CHARS)}"


def _why_let_go(todo: TodoRecord, clean: frozenset[str]) -> str:
    """Why a thing was let go, after its title, for the look (item 878): one its run let go because the owner said
    not to do it, or that the owner stopped, is not put on the list again in other words. The words are its last run's
    (its reason, or the first line of its answer): quoted only when that run is ``clean``, else not, as a title a
    tainted run wrote is not (the code review of item 878)."""
    if todo.status != "dropped" or not todo.note:
        return ""
    if todo.run_id is None or todo.run_id not in clean:
        return ", why not quoted: its run read content third parties wrote"
    return f", why: {quoted(todo.note, _QUOTE_CHARS)}"


async def _recent_chats(store: Store) -> list[str]:
    """The titles of the owner's newest chats: those a person started, whose titles are their words. The chats of the
    watchers (whose titles carry a mail's subject), their notices and Dot's own are left out (the code review of item
    715)."""
    chats = [chat for chat in await store.list_thread_summaries(_RECENT) if chat.source not in _NOT_THE_OWNERS_CHATS]
    return ["The owner's newest chats:", *(f"- {quoted(chat.title, _QUOTE_CHARS)}" for chat in chats)] if chats else []


@dataclass(frozen=True)
class _ThingsChat:
    """What the chat of a thing's last run holds (item 868): the runs that went on from it since, newest first; whether
    a run works there; whether a question or a step left for the owner waits."""

    later: tuple[RunRecord, ...]
    working: bool
    waiting: bool

    @property
    def holds(self) -> bool:
        """Whether no run may start the thing elsewhere: one works in its chat, the owner is asked there, or a run that
        went on from it ended and its end is not settled yet, which only :meth:`Initiative._next` may do (the third
        pre-push review of item 868: on a busy tick, an urgent thing whose answer's run the owner stopped started again
        in a new chat)."""
        return self.working or self.waiting or bool(self.later)


class Initiative:
    """Starts the runs of Dot's own initiative from the scheduler's tick (:meth:`tick`) and follows each to its end."""

    def __init__(self, runtime: Runtime, manager: RunManager, idle: Callable[[], float | None] = idle_seconds) -> None:
        self._runtime: Runtime = runtime
        self._manager: RunManager = manager
        self._idle: Callable[[], float | None] = idle
        self._watch: asyncio.Task[None] | None = None
        # When its last run ended (``time.monotonic``), which the next waits ``gap_minutes`` after.
        self._ended_at: float = -math.inf
        # When a start of its runs last failed (``time.monotonic``): none is tried again for a while.
        self._failed_at: float = -math.inf
        self._recovered: bool = False

    def working(self) -> bool:
        """Whether one of its runs is under way."""
        return self._watch is not None and not self._watch.done()

    async def wait(self) -> None:
        """Wait until its run under way, if any, has ended and what it did is written down."""
        if self._watch is not None:
            await asyncio.gather(self._watch, return_exceptions=True)

    async def stop(self) -> None:
        """Stop following its run (the daemon stops; the run itself is stopped with every other)."""
        if self._watch is not None and not self._watch.done():
            self._watch.cancel()
            await asyncio.gather(self._watch, return_exceptions=True)

    async def tick(self, now: float) -> None:
        """Start the next of its runs when everything says it may (the module's docstring)."""
        store: Store = self._runtime.store
        if not self._recovered:
            await self._recover()
        state: InitiativeState = await read_initiative(store)
        if not state.enabled or self.working() or await asyncio.to_thread(failsafe_path().exists):
            return
        config: InitiativeConfig = self._runtime.config.initiative
        if time.monotonic() - self._ended_at < config.gap_minutes * 60.0:
            return
        if time.monotonic() - self._failed_at < _AFTER_A_FAILED_START_S:
            return
        if config.runs_per_day > 0 and await self._started_lately() >= config.runs_per_day:
            return
        # In a provider's dearer hours (item 826), only an urgent thing of the owner's starts, as while the Mac is busy.
        held: bool = in_peak(now, config.peak_hours_utc)

        # Opportunistic initiative: an urgent thing the owner put on the list may start even while another run is
        # going; anything else waits for an idle Mac and no other run. The list is read only after that decision, so a
        # busy tick cannot drop or finish a thing as a side effect.
        busy: bool = bool(self._manager.active_run_ids() or self._manager.elsewhere_run_ids())
        look_ready: bool = False
        if busy or held:
            todo: TodoRecord | None = await self._urgent_todo(config.attempts)
        else:
            idle: float | None = await asyncio.to_thread(self._idle)
            if idle is None:
                # macOS cannot say how long the Mac has been idle: nothing starts, urgent or not (the card says so).
                return
            if idle < config.idle_minutes * 60.0:
                todo = await self._urgent_todo(config.attempts)
            else:
                look_ready = True
                await self._review_if_due(now)
                todo = await self._next(config.attempts)

        if todo is not None:
            await self._do(todo)
        elif look_ready and (state.looked_at is None or now - state.looked_at >= config.look_every_hours * 3600.0):
            await self._look(state, now)

    async def _urgent_todo(self, attempts: int) -> TodoRecord | None:
        """The first thing marked ``[URGENT]`` in its title or why, if it has tries left and its chat does not hold it
        (:meth:`_chat_of`, item 868).

        This reads without changing the list; a busy tick must not let go of or finish anything."""
        store: Store = self._runtime.store
        for todo in await store.list_todos(("open",), _LISTED):
            if not _is_urgent(todo) or todo.attempts >= attempts:
                continue
            chat: _ThingsChat | None = await self._chat_of(todo)
            if chat is None or not chat.holds:
                return todo
        return None

    async def _next(self, attempts: int) -> TodoRecord | None:
        """The oldest thing to do, with urgent things first. One tried ``attempts`` times already (the limit was
        lowered since) is let go, so that old things cannot keep the newer ones out of the part of the list that is
        read. One whose chat holds it (:meth:`_chat_of`) is left: a run works there, or a question waits for the owner
        (item 868). One whose chat went on from its last run (a resume, the run an answer of the owner's started) is
        that run's: done once it ended done, and otherwise as its end leaves it (:meth:`_settle`; the code reviews of
        item 715, the pre-push reviews of item 868)."""
        store: Store = self._runtime.store
        todos: list[TodoRecord] = list(await store.list_todos(("open",), _LISTED))
        for listed in sorted(todos, key=lambda item: (not _is_urgent(item), item.created_at)):
            chat: _ThingsChat | None = await self._chat_of(listed)
            if chat is not None and (chat.working or chat.waiting):
                continue
            if chat is not None and chat.later:
                # Its end is the thing's, as one of its own runs' (the fourth pre-push review of item 868: done, it
                # is a try and the owner is told, as any done thing).
                finished: RunRecord | None = next((run for run in chat.later if run.status == "done"), None)
                await self._settle(listed.id, finished or chat.later[0])
            # As the end of a run that went on from it left it (a counted try, a newer last run).
            todo: TodoRecord = await store.get_todo(listed.id)
            if todo.status != "open":
                continue
            if todo.attempts >= attempts:
                # Tried as often as a lowered limit allows, or its last try left a question that is over now: let go,
                # and the owner told as after any last try (the fourth pre-push review of item 868).
                await self._let_go(todo, todo.attempts, todo.run_id, f"Let go after {todo.attempts} tries.")
                continue
            return todo
        return None

    async def _chat_of(self, todo: TodoRecord) -> _ThingsChat | None:
        """What the chat of ``todo``'s last run holds, read without changing anything; ``None`` when it names no run.

        A run at work there, or a question or a step its runs left for the owner, unanswered, holds the thing (item
        868): a new run in a chat of its own would do it twice. On 2026-10-31 a run left "may I put the fixes live?"
        and ended; the thing was tried again 15 minutes later in a new chat, and when the owner answered, the two runs
        put the same site live side by side. The later runs that go on from its last run (``goes_on_from``) are read
        too; another message of the owner's in that chat is no sign the thing was done (the code review of item
        715)."""
        if todo.run_id is None:
            return None
        store: Store = self._runtime.store
        try:
            last: RunRecord = await store.get_run(todo.run_id)
        except NotFoundError:
            return None
        runs: list[RunRecord] = await store.list_runs(_LATER_READ, last.thread_id)
        later: tuple[RunRecord, ...] = tuple(
            run
            for run in runs
            if run.id != last.id
            and run.created_at >= last.created_at
            and (run.source == RESUME_SOURCE or goes_on_from(run, last))
        )
        return _ThingsChat(
            later=later,
            working=any(run.status in WORKING_STATUSES for run in runs),
            waiting=await store.count_left_in_chat(last.thread_id) > 0,
        )

    async def _started_lately(self) -> int:
        """How many of its runs started in the last day, counted in the store, so that a restart of the daemon does not
        count from none (the code review of item 715)."""
        return await self._runtime.store.count_runs(INITIATIVE_SOURCE, time.time() - _DAY_S)

    async def _recover(self) -> None:
        """What a thing left as being done by a daemon that stopped becomes: a counted try when the daemon stopped
        without shutting down (a crash, a kill), so that a thing that brings it down is let go once it was tried
        ``[initiative] attempts`` times, as one that fails is; what any end makes of it otherwise (:meth:`_settle`): to
        do again, its try uncounted, after a shutdown or the emergency stop, let go when the owner stopped it, done when
        it ended done (the code reviews of item 715)."""
        store: Store = self._runtime.store
        for todo in await store.list_todos(("doing",), _LISTED):
            if todo.run_id is not None and todo.run_id in self._manager.active_run_ids():
                continue
            # Each thing on its own (item 790): one the store fails for is logged and looked at again at the next
            # daemon's start, and the others are not left as being done for good.
            try:
                await self._recover_one(todo)
            except DotError as error:
                log.error("initiative_not_recovered", todo_id=todo.id, error=error.message, context=error.context)
        # Set once the list was read: a store that failed to list it is asked again at the next tick.
        self._recovered = True

    async def _recover_one(self, todo: TodoRecord) -> None:
        store: Store = self._runtime.store
        ended: RunRecord | None = None
        if todo.run_id is not None:
            with contextlib.suppress(NotFoundError):
                ended = await store.get_run(todo.run_id)
        if ended is None:
            await store.set_todo(todo.id, "open", todo.attempts, todo.run_id, todo.note)
        elif cut_off_by(ended) == "daemon_restarted":
            await self._tried(todo, ended, "Dot stopped while it did this, without shutting down.")
        else:
            # Its run ended while the daemon was going down, before its end was written to the list: as any end.
            await self._settle(todo.id, ended)

    async def _do(self, todo: TodoRecord) -> None:
        store: Store = self._runtime.store
        # Written as being done before its run starts (item 790), which narrows the window in which a daemon that dies
        # leaves a run going and its thing open: what is left is the moment between the start and the line below. It
        # names no run until then: an earlier run's id, kept here, would be read by the recovery (:meth:`_recover`) as
        # how this try ended.
        await store.set_todo(todo.id, "doing", todo.attempts, None, todo.note)
        try:
            opening: str = (
                await self._review_opening(todo)
                if todo.source == REVIEW_SOURCE
                else _DO_OPENING.format(title=todo.title, why=todo.why or "(not said)")
            )
            run: RunRecord = await self._start(
                todo.title, _DO_GOAL.format(title=todo.title), opening, "task", todo.tainted
            )
        except BaseException:
            # Its last run stays named (the review of item 790): an owner's resume of it is found by that id. Whatever
            # kept the run from starting (a store, a bug, the daemon stopping), the thing is open again, not left
            # "doing" with no run until the next start of the daemon (the review of 2026-11-01).
            await store.set_todo(todo.id, "open", todo.attempts, todo.run_id, todo.note)
            raise
        await store.set_todo(todo.id, "doing", todo.attempts, run.id, todo.note)
        log.info("initiative_started", todo_id=todo.id, run_id=run.id, tainted=todo.tainted)
        self._follow(run.id, todo.id)

    async def _review_if_due(self, now: float) -> None:
        """Put the count of what Dot's own work came to on the list (item 914) once ``[initiative] review_every_days``
        went by since the last count was put on it, when a thing was done or let go since: nothing to count, no
        count."""
        days: float = self._runtime.config.initiative.review_every_days
        if days <= 0:
            return
        store: Store = self._runtime.store
        span: float = days * _DAY_S
        last: TodoRecord | None = await self._last_review()
        if last is not None and (last.status in ("open", "doing") or now - last.created_at < span):
            return
        since: float = now - span if last is None else max(last.created_at, now - span)
        # Something to count: a thing done or let go since, other than a count (the pre-push review of item 914: its
        # runs, and the looks, would else bring a count every week of a list where nothing was done).
        ended: list[TodoRecord] = await store.recent_todos(since, _REVIEWED)
        if not any(todo.status in ("done", "dropped") and todo.source != REVIEW_SOURCE for todo in ended):
            return
        title: str = _REVIEW_TITLE.format(since=_day(since))
        why: str = "Without a count, nobody can say whether this work helps, or which of it to do more of."
        await store.add_todo(title, todo_key(title), why, REVIEW_SOURCE, None, False)
        log.info("initiative_review_due", since=since)

    async def _last_review(self) -> TodoRecord | None:
        """The newest count put on the list, done, let go or to do."""
        store: Store = self._runtime.store
        kept: list[TodoRecord] = [
            *await store.list_todos(("open", "doing"), _LISTED),
            *await store.recent_todos(time.time() - 60 * _DAY_S, _REVIEWED),
        ]
        reviews: list[TodoRecord] = [todo for todo in kept if todo.source == REVIEW_SOURCE]
        return max(reviews, key=lambda todo: todo.created_at, default=None)

    async def _review_opening(self, todo: TodoRecord) -> str:
        """The opening of the count ``todo`` (item 914): what Dot's own runs did and spent since the count before it,
        each thing done or let go with what came of it, quoted only from runs that read nothing third parties wrote."""
        store: Store = self._runtime.store
        reviews: list[TodoRecord] = [
            review
            for review in await store.recent_todos(time.time() - 60 * _DAY_S, _REVIEWED)
            if review.source == REVIEW_SOURCE and review.id != todo.id and review.created_at < todo.created_at
        ]
        span: float = self._runtime.config.initiative.review_every_days * _DAY_S
        since: float = max((review.created_at for review in reviews), default=todo.created_at - span)
        made, failed, spent = await store.spent_by_source(INITIATIVE_SOURCE, since)
        clean: frozenset[str] = await self._clean_runs()
        ended: list[TodoRecord] = [
            thing
            for thing in await store.recent_todos(since, _REVIEWED)
            if thing.status in ("done", "dropped") and thing.source != REVIEW_SOURCE
        ]
        lines: list[str] = [
            BRIEF_OPEN,
            f"Since {_day(since)}: {made} runs of your own, {failed} of them failed, {spent / 1_000_000:.1f}M tokens.",
            *(
                f"- {_STATE_WORDS[thing.status]}{_when_ended(thing)}: {quoted(thing.title, _QUOTE_CHARS)}"
                f"{_why_let_go(thing, clean)}{_what_came(thing, clean)}"
                for thing in ended
                if not thing.tainted
            ),
        ]
        unquoted: int = sum(1 for thing in ended if thing.tainted)
        if unquoted:
            lines.append(f"- and {unquoted} more that came from content third parties wrote, not quoted here")
        lines.append(BRIEF_CLOSE)
        return _REVIEW_OPENING.format(since=_day(since), file=REVIEW_FILE, record="\n".join(lines))

    async def _look(self, state: InitiativeState, now: float) -> None:
        store: Store = self._runtime.store
        since: float = state.looked_at if state.looked_at is not None else now - _FIRST_LOOK_S
        parts: list[Quoted] = await record_parts(store, since, self._runtime.schedule_zone)
        lines: list[str] = [
            BRIEF_OPEN,
            *(line for part in parts for line in part.lines),
            *await _recent_chats(store),
            *await _listed(store, await self._clean_runs()),
            BRIEF_CLOSE,
        ]
        carried: tuple[str, ...] = tuple(dict.fromkeys(run_id for part in parts for run_id in part.run_ids))
        tainted: bool = await self._carries_taint(carried)
        # The look judges what is worth doing, on a model of its own when one is set (item 910); its things are done on
        # the configured one.
        model: str | None = self._runtime.config.initiative.look_model or None
        run: RunRecord = await self._start(
            LOOK_TITLE,
            _LOOK_GOAL,
            _LOOK_OPENING.format(record="\n".join(lines), review_file=REVIEW_FILE),
            "research",
            tainted,
            model,
        )
        await note_look(store, now)
        log.info("initiative_looking", run_id=run.id, tainted=tainted)
        self._follow(run.id, None)

    async def _start(
        self, title: str, goal: str, opening: str, mode: RunMode, tainted: bool, model: str | None = None
    ) -> RunRecord:
        """Start one of its runs in a new chat named ``title``. A start that fails takes its empty chat with it and
        holds the next for :data:`_AFTER_A_FAILED_START_S` (the code review of item 715: a start that kept failing made
        a chat at every tick)."""
        store: Store = self._runtime.store
        thread = await store.create_thread(thread_title(title), INITIATIVE_SOURCE)
        try:
            return await self._manager.start_own(goal, opening, mode, thread.id, tainted, model)
        except DotError:
            self._failed_at = time.monotonic()
            await store.delete_threads([thread.id])
            raise

    async def _clean_runs(self) -> frozenset[str]:
        """The last runs of the things done or let go lately that ended having read nothing third parties can author:
        their words of why, or of what came of the thing, may be quoted to the look (the code review of item 878, item
        911)."""
        let_go: list[TodoRecord] = [
            todo
            for todo in await self._runtime.store.recent_todos(time.time() - 30 * _DAY_S, _LISTED)
            if todo.status in ("done", "dropped") and todo.run_id is not None
        ]
        clean: set[str] = set()
        for todo in let_go:
            if todo.run_id is not None and not await self._manager.ended_tainted(todo.run_id):
                clean.add(todo.run_id)
        return frozenset(clean)

    async def _carries_taint(self, run_ids: Sequence[str]) -> bool:
        """Whether one of ``run_ids`` ended having read content third parties can author, whose words the record
        quotes."""
        for run_id in run_ids:
            if await self._manager.ended_tainted(run_id):
                return True
        return False

    def _follow(self, run_id: str, todo_id: str | None) -> None:
        self._watch = asyncio.create_task(self._watch_run(run_id, todo_id), name=f"initiative-{run_id}")

    async def _watch_run(self, run_id: str, todo_id: str | None) -> None:
        try:
            ended: RunRecord = await self._manager.wait(run_id)
            if todo_id is not None:
                await self._settle(todo_id, ended)
        except DotError as error:
            log.error("initiative_not_settled", run_id=run_id, error=error.message, context=error.context)
        except Exception:
            # The task is its own: what it could not write down is logged, and the thing is taken up again once the
            # daemon starts next (``_recover``).
            log.exception("initiative_not_settled", run_id=run_id)
        finally:
            self._ended_at = time.monotonic()

    async def _settle(self, todo_id: str, ended: RunRecord) -> None:
        """What the end of ``ended`` makes of its thing: done, to do again, or let go."""
        store: Store = self._runtime.store
        todo: TodoRecord = await store.get_todo(todo_id)
        note: str = one_line(_first_line(ended.result), _NOTE_CHARS)
        url: str = f"#/runs/{ended.id}"
        # Let go while its run worked (item 878): by that run, as the owner said not to do it, or by the owner on the
        # Schedules page. It stays let go, however the run ended, and is not tried again.
        if todo.status == "dropped":
            if todo.note.startswith(LET_GO_BY_ITS_RUN):
                await tell_owner(
                    self._runtime.bus,
                    "Dot let go of a thing you said not to do",
                    one_line(f"{todo.title}: {todo.note.removeprefix(LET_GO_BY_ITS_RUN)}", _TELL_CHARS),
                    url,
                )
            return
        if ended.status == "done":
            await store.set_todo(todo.id, "done", todo.attempts + 1, ended.id, note)
            await tell_owner(self._runtime.bus, "Dot did on its own", one_line(todo.title, _TELL_CHARS), url)
            return
        # The emergency stop ends a run in two words: the stop of every run, and the STOP file a run saw as it worked
        # ("Emergency stop: …"); either leaves the thing to do, as the docs say (item 790).
        if ended.status == "cancelled" and (
            ended.result == RESULT_SHUTDOWN or ended.result.startswith(PREFIX_EMERGENCY_STOP)
        ):
            await store.set_todo(todo.id, "open", todo.attempts, ended.id, todo.note)
            return
        if ended.status == "cancelled":
            await store.set_todo(todo.id, "dropped", todo.attempts + 1, ended.id, "You stopped it.")
            return
        await self._tried(todo, ended, note)

    async def _tried(self, todo: TodoRecord, ended: RunRecord, note: str) -> None:
        """Count a try of ``todo`` that ``ended`` without doing it: to do again, or let go and told once it was tried
        ``[initiative] attempts`` times."""
        store: Store = self._runtime.store
        config: InitiativeConfig = self._runtime.config.initiative
        tried: int = todo.attempts + 1
        waiting: bool = await store.count_left_in_chat(ended.thread_id) > 0
        # A try that spent more than [initiative] retry_under_tokens and did not do it is not made again the same way
        # (item 913): on the owner's Mac three tries of one application spent 31.6 million tokens on a puzzle its form
        # asked for, each one finding what the one before had.
        spent: int = ended.input_tokens + ended.output_tokens
        if not waiting and config.retry_under_tokens > 0 and spent > config.retry_under_tokens:
            millions: str = f"{spent / 1_000_000:.1f}"
            await self._let_go(
                todo, tried, ended.id, one_line(f"Spent {millions}M tokens without doing it: {note}", _NOTE_CHARS)
            )
            return
        # One that left the owner what only they decide (item 868) stays to do, its try counted, while it waits for
        # their answer; it is let go once it is not waiting and has no try left (:meth:`_next`).
        if tried < config.attempts or waiting:
            await store.set_todo(todo.id, "open", tried, ended.id, note)
            return
        await self._let_go(todo, tried, ended.id, note)

    async def _let_go(self, todo: TodoRecord, tried: int, run_id: str | None, note: str) -> None:
        """Let go of ``todo`` after ``tried`` tries, its last run ``run_id``, and tell the owner."""
        await self._runtime.store.set_todo(todo.id, "dropped", tried, run_id, note)
        await tell_owner(
            self._runtime.bus,
            f"Dot let go of a thing after {tried} {'try' if tried == 1 else 'tries'}",
            one_line(f"{todo.title}: {note}", _TELL_CHARS),
            f"#/runs/{run_id}" if run_id is not None else _LIST_ROUTE,
        )
