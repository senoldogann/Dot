"""A schedule that waits on the owner (item 1952): a firing whose goal check found the goal cannot be reached without
the owner ended done with what it needs from them (:func:`dotagent.agent.loop.owner_wait_end`). The firings after it
start with a note that that part is on hold, until the owner answers, so that a login wall they have to clear is not
met again by every run (Fiverr and TikTok, 17 failed runs and some 148 M tokens in a week). Nothing is stored for it:
the wait is read from the schedule's runs, so it cannot disagree with them."""

from dotagent.agent.manager import BACKGROUND_SOURCE
from dotagent.agent.resume import RESUME_SOURCE
from dotagent.proactive import NOTICE_SOURCE, UNWATCHED_SOURCES
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.history import OwnerWait
from dotagent.store.db import Store
from dotagent.store.endings import GOAL_CHECK_MARK, OWNER_WAIT_NOTE
from dotagent.store.models import EventRecord, RunRecord

# The most of the schedule's runs looked through, newest first, for the run that waits on the owner: far above what a
# schedule fires in the seven days a wait holds (a mail check every two hours is 84).
RUNS_LOOKED_AT: int = 300
# How long a wait on the owner holds: past it the part is tried again, since the owner may have cleared it themselves
# (logged in on their own) without telling Dot.
OWNER_WAIT_HOLDS_S: float = 7 * 86_400.0
_MARK: str = f"{GOAL_CHECK_MARK}{OWNER_WAIT_NOTE}"
# The runs in the chat that are not an answer of the owner's: Dot's own (a schedule's, a watcher's, its initiative, a
# reply's, a note's, a command's end) and the resumption of one.
_NOT_THE_OWNERS: frozenset[str] = UNWATCHED_SOURCES | {NOTICE_SOURCE, RESUME_SOURCE, BACKGROUND_SOURCE}


def waits_on_owner(run: RunRecord) -> bool:
    """Whether ``run`` ended done because its goal waits on the owner."""
    return run.status == "done" and _MARK in run.result


async def owner_wait_of(store: Store, schedule_id: str, now: float) -> OwnerWait | None:
    """The newest run of the schedule that waits on the owner, while it still does: ``None`` when none of its newest
    runs does, when the owner answered (a run of theirs, not the schedule's, came in the chat of that run after it) or
    when the wait is older than :data:`OWNER_WAIT_HOLDS_S`."""
    for run in await store.schedule_runs(schedule_id, SCHEDULE_SOURCE, RUNS_LOOKED_AT):
        if not waits_on_owner(run):
            continue
        ended: float = run.created_at if run.finished_at is None else run.finished_at
        if now - ended > OWNER_WAIT_HOLDS_S:
            return None
        newest: list[RunRecord] = await store.list_runs(1, run.thread_id)
        if newest and newest[0].id != run.id and newest[0].source not in _NOT_THE_OWNERS:
            return None
        finished: EventRecord | None = await store.last_event(run.id, "run_finished")
        needs: object = None if finished is None else finished.data.get("needs")
        return OwnerWait(run_id=run.id, needs=needs if isinstance(needs, str) else "", since=ended)
    return None
