"""A schedule that waits on the owner (item 1952): a firing whose goal check found the goal cannot be reached without
the owner ended done with what it needs from them (:func:`dotagent.agent.loop.owner_wait_end`). The firings after it
start with a note that that part is on hold, until the owner answers, so that a login wall they have to clear is not
met again by every run (Fiverr and TikTok, 17 failed runs and some 148 M tokens in a week). Nothing is stored for it:
the wait is read from the schedule's runs, so it cannot disagree with them."""

from dotagent.channels.types import TITLES
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.history import OwnerWait
from dotagent.server import CLI_SOURCE, UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.endings import GOAL_CHECK_MARK, OWNER_WAIT_NOTE
from dotagent.store.models import EventRecord, RunRecord

# How long a wait on the owner holds: past it the part is tried again, since the owner may have cleared it themselves
# (logged in on their own) without telling Dot.
OWNER_WAIT_HOLDS_S: float = 7 * 86_400.0
_MARK: str = f"{GOAL_CHECK_MARK}{OWNER_WAIT_NOTE}"
# The runs in the chat that are an answer of the owner's: those they start in the app or with dotx, and in a channel
# (an allowlist: any other kind of run, now or later, is Dot's own).
_THE_OWNERS: frozenset[str] = frozenset({UI_SOURCE, CLI_SOURCE, *TITLES})


def waits_on_owner(run: RunRecord) -> bool:
    """Whether ``run`` ended done because its goal waits on the owner."""
    return run.status == "done" and _MARK in run.result


async def owner_wait_of(store: Store, schedule_id: str, now: float) -> OwnerWait | None:
    """The newest run of the schedule that waits on the owner and began in the last :data:`OWNER_WAIT_HOLDS_S`, while it
    still does: ``None`` when there is none, or when the owner answered (a run of theirs, one of :data:`_THE_OWNERS`,
    began in the chat of that run after it, whenever, however many firings came since)."""
    run: RunRecord | None = await store.schedule_run_marked(
        schedule_id, SCHEDULE_SOURCE, now - OWNER_WAIT_HOLDS_S, _MARK
    )
    if run is None:
        return None
    if await store.chat_has_run_after(run.thread_id, run.created_at, sorted(_THE_OWNERS)):
        return None
    ended: float = run.created_at if run.finished_at is None else run.finished_at
    finished: EventRecord | None = await store.last_event(run.id, "run_finished")
    needs: object = None if finished is None else finished.data.get("needs")
    return OwnerWait(run_id=run.id, needs=needs if isinstance(needs, str) else "", since=ended)
