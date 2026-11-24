"""The ledger of tells (item 1743): the adapter between the bus, the store and the pure count of
:mod:`dotagent.notify.tells`.

Every way Dot tells the owner is an event on the bus: what Dot says on its own (``owner_told``), the end of a run and
the end of a schedule's run (the pushes of ``notify/push.py``). :func:`watch_tells` records each one that is told as a
``tell_sent`` event, under the same rules as the push, so that the ledger holds what the owner's phone was sent and
nothing else. The owner's answers are recorded when they come, by :func:`mark_opened` (the chat of a tell's run was
opened) and :func:`mark_dismissed` (a notification was closed unopened); :func:`tell_report` counts them."""

import asyncio

from dotagent.channels.types import TITLES
from dotagent.core.errors import DotError, NotFoundError
from dotagent.core.ids import new_id
from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject
from dotagent.events import SYSTEM_RUN_ID, Subscription
from dotagent.notify.owner import OWNER_TOLD_EVENT, OwnerTold
from dotagent.notify.push import PushMessage, notice_message, push_message
from dotagent.notify.tellkinds import TellKind
from dotagent.notify.tells import (
    TELL_REPORT_S,
    TELL_RESPONSE_EVENT,
    TELL_SENT_EVENT,
    TELL_WINDOW_S,
    OwnerAct,
    TellAnswer,
    TellReport,
    TellResponse,
    TellSent,
    TellVia,
    run_of_route,
    tell_rows,
)
from dotagent.scheduler import SCHEDULE_RUN_ENDED_EVENT, ScheduleRunEnded
from dotagent.server import CLI_SOURCE, UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

log = get_logger(__name__)
# The events a tell is recorded from: what Dot tells on its own, and the two ends the phone is told of.
TELL_SOURCE_KINDS: frozenset[str] = frozenset({OWNER_TOLD_EVENT, "run_finished", SCHEDULE_RUN_ENDED_EVENT})
_WAIT_S: float = 1.0
# The runs the owner's own messages start: in the app, with ``dotx run`` and in a channel.
_OWNER_SOURCES: tuple[str, ...] = (UI_SOURCE, CLI_SOURCE, *TITLES)


async def record_tell(store: Store, kind: TellKind, subject: str, route: str) -> None:
    """Keep that the owner was told, as the event ``tell_sent``."""
    body: JsonObject = {"tell_id": new_id("tell"), "kind": kind, "subject": subject, "route": route}
    await store.add_tell_event(TELL_SENT_EVENT, body)


async def _recorded_from(store: Store, event: EventRecord) -> None:
    """Record the tell ``event`` is, if it is one: an event of Dot's own is told as ``notice_message`` says, an end of a
    run as ``push_message`` says, so that nothing the phone is not sent is counted."""
    if event.kind == OWNER_TOLD_EVENT:
        owner: OwnerTold = OwnerTold.model_validate(event.data)
        await record_tell(store, owner.kind, owner.subject, owner.url)
        return
    if event.run_id == SYSTEM_RUN_ID:
        message: PushMessage | None = notice_message(event)
        if message is not None and event.kind == SCHEDULE_RUN_ENDED_EVENT:
            ended: ScheduleRunEnded = ScheduleRunEnded.model_validate(event.data)
            await record_tell(store, "schedule_end", ended.schedule_id, message.route)
        return
    message = push_message(event, await store.get_run(event.run_id))
    if message is not None and event.kind == "run_finished":
        await record_tell(store, "run_end", "", message.route)


async def watch_tells(store: Store, feed: Subscription, stopping: asyncio.Event) -> None:
    """Record every tell of the events of ``feed`` (:data:`TELL_SOURCE_KINDS`) until the daemon starts to stop. A tell
    that cannot be recorded (its run was deleted, the store failed) is logged and the next one still is."""
    while not stopping.is_set():
        try:
            event: EventRecord = await asyncio.wait_for(feed.queue.get(), _WAIT_S)
        except TimeoutError:
            continue
        try:
            await _recorded_from(store, event)
        except NotFoundError:
            # The run of the event is gone (its chat was deleted): there is nothing to open, and no tell to count.
            log.info("tell_run_gone", run_id=event.run_id, kind=event.kind)
        except DotError as error:
            log.warning("tell_not_recorded", run_id=event.run_id, kind=event.kind, error=str(error))


async def _newest_tell_of(store: Store, route: str, now: float) -> TellSent | None:
    """The newest tell of ``route`` that went out within the window."""
    return next((tell for tell in reversed(await store.tells_since(now - TELL_WINDOW_S)) if tell.route == route), None)


async def mark_opened(store: Store, thread_id: str, now: float) -> int:
    """The owner opened the chat ``thread_id``: every tell within the window whose route opens a run of it, and that
    has no ``opened`` yet, is answered as opened. Returns how many. It is the owner's act, not a model's, and it asks
    the store for the tells of the last half hour only."""
    tells: list[TellSent] = await store.tells_since(now - TELL_WINDOW_S)
    runs: list[str] = [run for run in (run_of_route(tell.route) for tell in tells) if run is not None]
    if not runs:
        return 0
    threads: dict[str, str] = await store.threads_of_runs(runs)
    answered: set[str] = {
        response.tell_id
        for response in await store.tell_responses_since(now - TELL_WINDOW_S)
        if response.response == "opened"
    }
    count: int = 0
    for tell in tells:
        run_id: str | None = run_of_route(tell.route)
        if run_id is None or threads.get(run_id) != thread_id or tell.tell_id in answered:
            continue
        await store.add_tell_event(TELL_RESPONSE_EVENT, {"tell_id": tell.tell_id, "response": "opened", "via": "app"})
        count += 1
    return count


async def mark_dismissed(store: Store, route: str, via: TellVia, now: float) -> bool:
    """A notification of the newest tell of ``route`` was closed unopened: answered as dismissed, once. ``False`` when
    no tell of that route went out within the window or it was answered."""
    tell: TellSent | None = await _newest_tell_of(store, route, now)
    if tell is None:
        return False
    answers: list[TellResponse] = [
        response for response in await store.tell_responses_since(tell.ts) if response.tell_id == tell.tell_id
    ]
    if answers:
        return False
    answer: TellAnswer = "dismissed"
    await store.add_tell_event(TELL_RESPONSE_EVENT, {"tell_id": tell.tell_id, "response": answer, "via": via})
    return True


async def tell_report(store: Store, now: float) -> TellReport:
    """What the owner did with the tells of the last :data:`TELL_DAYS` days, per kind and subject."""
    since: float = now - TELL_REPORT_S
    tells: list[TellSent] = await store.tells_since(since)
    responses: list[TellResponse] = await store.tell_responses_since(since)
    runs: list[str] = sorted({run for run in (run_of_route(tell.route) for tell in tells) if run is not None})
    acts: list[OwnerAct] = await store.owner_acts_since(since, _OWNER_SOURCES)
    return TellReport(
        days=int(TELL_REPORT_S // 86400),
        window_s=TELL_WINDOW_S,
        rows=tell_rows(tells, responses, await store.threads_of_runs(runs), acts, now),
    )
