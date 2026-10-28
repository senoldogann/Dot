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
"""

import asyncio
import contextlib
import math
import time
from collections.abc import Callable, Sequence

from Quartz import CGEventSourceSecondsSinceLastEventType, kCGAnyInputEventType, kCGEventSourceStateHIDSystemState

from dotagent.agent.manager import RunManager, thread_title
from dotagent.agent.resume import RESUME_SOURCE, cut_off_by
from dotagent.config import InitiativeConfig
from dotagent.core.errors import DotError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path
from dotagent.notify.owner import tell_owner
from dotagent.proactive import INITIATIVE_SOURCE, NOTICE_SOURCE, WATCHER_SOURCE
from dotagent.proactive.brief import BRIEF_CLOSE, BRIEF_OPEN, Quoted, record_parts
from dotagent.proactive.text import one_line, quoted
from dotagent.proactive.todos import InitiativeState, note_look, read_initiative
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
_RECENT: int = 15
_QUOTE_CHARS: int = 160
# The newest runs of a thing's chat read for one the owner started there since (a Resume).
_LATER_READ: int = 10
# How long no run of its own is tried after one could not be started (a model or a setting that refuses it).
_AFTER_A_FAILED_START_S: float = 600.0
_STATE_WORDS: dict[str, str] = {"open": "to do", "doing": "being done", "done": "done", "dropped": "let go"}
# The chats whose titles are not the owner's words: a watcher's carries a mail's subject, Dot's own a thing of its list.
_NOT_THE_OWNERS_CHATS: frozenset[str] = frozenset({WATCHER_SOURCE, NOTICE_SOURCE, INITIATIVE_SOURCE})
# How far back the run that looks for work reads the failed runs, when it never looked before.
_FIRST_LOOK_S: float = 7 * _DAY_S

_DO_GOAL: str = "On my own initiative: {title}"
_DO_OPENING: str = """You work on your own initiative for the owner (item 715): nobody asked for this in this chat and \
nobody watches it. It is a thing on your own list.

The work: {title}
Why it helps them: {why}

Do it as far as it goes without them. What only they can decide, leave as a question (ask_user) and go on with the \
rest. End with one or two sentences: what you did, and where the result is."""
_LOOK_GOAL: str = "On my own initiative: look for work that helps the owner"
_LOOK_OPENING: str = """You look for work to do for the owner on your own initiative (item 715). Read what you \
remember of them (memory_search: their projects, their wishes, what they asked to be done later), the record below, \
the chats they had lately, and their mail and calendar when an account is connected. Then put at most three things on \
your list with todo_add (in the tool group todos: turn it on with use_tools when it is off): each a concrete piece \
of work you can do alone, which they would thank you for, which is not on the list yet and was not done. Put nothing \
on it when nothing is worth it. Change nothing else. End with one sentence: what you put on the list, or that you \
found nothing.

{record}"""


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


async def _listed(store: Store) -> list[str]:
    """The list as the run that looks for work reads it: what is on it, and what was done or let go lately. The title
    of a thing a tainted run put on it is not quoted, only counted: it may hold what a mail or a page said (the code
    review of item 715), and ``proactive.todos.add_todo`` keeps it from being listed twice without reading it."""
    kept: list[TodoRecord] = await store.list_todos(("open", "doing"), _LISTED)
    lately: list[TodoRecord] = [
        todo
        for todo in await store.recent_todos(time.time() - 30 * _DAY_S, _LISTED)
        if todo.status in ("done", "dropped")
    ]
    lines: list[str] = [
        f"- {_STATE_WORDS[todo.status]}: {quoted(todo.title, _QUOTE_CHARS)}"
        for todo in [*kept, *lately]
        if not todo.tainted
    ]
    unquoted: int = sum(1 for todo in [*kept, *lately] if todo.tainted)
    if unquoted:
        lines.append(f"- and {unquoted} more that came from content third parties wrote, not quoted here")
    return ["Your list:", *(lines or ["- nothing yet"])]


async def _recent_chats(store: Store) -> list[str]:
    """The titles of the owner's newest chats: those a person started, whose titles are their words. The chats of the
    watchers (whose titles carry a mail's subject), their notices and Dot's own are left out (the code review of item
    715)."""
    chats = [chat for chat in await store.list_thread_summaries(_RECENT) if chat.source not in _NOT_THE_OWNERS_CHATS]
    return ["The owner's newest chats:", *(f"- {quoted(chat.title, _QUOTE_CHARS)}" for chat in chats)] if chats else []


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
        
        # Opportunistic Initiative: check for urgent tasks that bypass idle/active constraints.
        todo: TodoRecord | None = await self._next(config.attempts)
        is_urgent = todo is not None and ("[URGENT]" in todo.title.upper() or "[URGENT]" in todo.why.upper())
        
        if not is_urgent:
            if self._manager.active_run_ids() or self._manager.elsewhere_run_ids():
                return
            idle: float | None = await asyncio.to_thread(self._idle)
            if idle is None or idle < config.idle_minutes * 60.0:
                return
        
        if todo is not None:
            await self._do(todo)
        elif state.looked_at is None or now - state.looked_at >= config.look_every_hours * 3600.0:
            await self._look(state, now)

    async def _next(self, attempts: int) -> TodoRecord | None:
        """The oldest thing to do. One tried ``attempts`` times already (the limit was lowered since) is let go, so
        that old things cannot keep the newer ones out of the part of the list that is read; one whose chat goes on in
        a run the owner started there since (a Resume) is that run's: left while it works, done once it ended done
        (the code reviews of item 715)."""
        store: Store = self._runtime.store
        for todo in await store.list_todos(("open",), _LISTED):
            if todo.attempts >= attempts:
                await store.set_todo(
                    todo.id, "dropped", todo.attempts, todo.run_id, f"Let go after {todo.attempts} tries."
                )
                continue
            if await self._taken_over(todo):
                continue
            return todo
        return None

    async def _taken_over(self, todo: TodoRecord) -> bool:
        """Whether a resume of ``todo``'s last run (a later run of its chat that goes on from it) has it: one at work,
        or one that ended done, which marks the thing done. Another message of the owner's in that chat does not: its
        answer is no sign the thing was done (the code review of item 715)."""
        if todo.run_id is None:
            return False
        store: Store = self._runtime.store
        try:
            last: RunRecord = await store.get_run(todo.run_id)
        except NotFoundError:
            return False
        later: list[RunRecord] = [
            run
            for run in await store.list_runs(_LATER_READ, last.thread_id)
            if run.id != last.id and run.created_at >= last.created_at and run.source == RESUME_SOURCE
        ]
        if any(run.status in WORKING_STATUSES for run in later):
            return True
        finished: RunRecord | None = next((run for run in later if run.status == "done"), None)
        if finished is None:
            return False
        note: str = one_line(_first_line(finished.result), _NOTE_CHARS)
        await store.set_todo(todo.id, "done", todo.attempts, finished.id, note)
        return True

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
            run: RunRecord = await self._start(
                todo.title,
                _DO_GOAL.format(title=todo.title),
                _DO_OPENING.format(title=todo.title, why=todo.why or "(not said)"),
                "task",
                todo.tainted,
            )
        except DotError:
            # Its last run stays named (the review of item 790): an owner's resume of it is found by that id.
            await store.set_todo(todo.id, "open", todo.attempts, todo.run_id, todo.note)
            raise
        await store.set_todo(todo.id, "doing", todo.attempts, run.id, todo.note)
        log.info("initiative_started", todo_id=todo.id, run_id=run.id, tainted=todo.tainted)
        self._follow(run.id, todo.id)

    async def _look(self, state: InitiativeState, now: float) -> None:
        store: Store = self._runtime.store
        since: float = state.looked_at if state.looked_at is not None else now - _FIRST_LOOK_S
        parts: list[Quoted] = await record_parts(store, since, self._runtime.schedule_zone)
        lines: list[str] = [
            BRIEF_OPEN,
            *(line for part in parts for line in part.lines),
            *await _recent_chats(store),
            *await _listed(store),
            BRIEF_CLOSE,
        ]
        carried: tuple[str, ...] = tuple(dict.fromkeys(run_id for part in parts for run_id in part.run_ids))
        tainted: bool = await self._carries_taint(carried)
        run: RunRecord = await self._start(
            LOOK_TITLE, _LOOK_GOAL, _LOOK_OPENING.format(record="\n".join(lines)), "research", tainted
        )
        await note_look(store, now)
        log.info("initiative_looking", run_id=run.id, tainted=tainted)
        self._follow(run.id, None)

    async def _start(self, title: str, goal: str, opening: str, mode: RunMode, tainted: bool) -> RunRecord:
        """Start one of its runs in a new chat named ``title``. A start that fails takes its empty chat with it and
        holds the next for :data:`_AFTER_A_FAILED_START_S` (the code review of item 715: a start that kept failing made
        a chat at every tick)."""
        store: Store = self._runtime.store
        thread = await store.create_thread(thread_title(title), INITIATIVE_SOURCE)
        try:
            return await self._manager.start_own(goal, opening, mode, thread.id, tainted)
        except DotError:
            self._failed_at = time.monotonic()
            await store.delete_threads([thread.id])
            raise

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
        tried: int = todo.attempts + 1
        if tried < self._runtime.config.initiative.attempts:
            await store.set_todo(todo.id, "open", tried, ended.id, note)
            return
        await store.set_todo(todo.id, "dropped", tried, ended.id, note)
        await tell_owner(
            self._runtime.bus,
            f"Dot let go of a thing after {tried} tries",
            one_line(f"{todo.title}: {note}", _TELL_CHARS),
            f"#/runs/{ended.id}",
        )
