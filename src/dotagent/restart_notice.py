"""Telling the owner which run a restart waits for (item 1310).

``dotx service restart --when-idle`` is a job of its own (:mod:`dotagent.restart_when_idle`) that shows one banner, at
the end of two hours. The daemon looks at the flag the job keeps in the store (:mod:`dotagent.restart_flag`) at each
tick of the scheduler and, once the restart has waited :data:`~dotagent.restart_flag.NOTICE_AFTER_S`, tells the owner
once, as Dot tells them anything (the phone, the desktop, the messaging apps), which run holds it. Nothing is stopped:
the run goes on, and the restart waits for it, or for the night's learning."""

import time

from dotagent.core.errors import NotFoundError
from dotagent.core.logs import get_logger
from dotagent.notify.owner import tell_owner
from dotagent.proactive.text import one_line
from dotagent.restart_flag import FLAG_KEY, NOTICE_AFTER_S, TOLD_KEY, RestartFlag, parse_flag, pending_for
from dotagent.runtime import Runtime
from dotagent.store.models import RunRecord

log = get_logger(__name__)

# When this process started: a restart asked for before it is the one that started it.
PROCESS_STARTED_AT: float = time.time()
NOTICE_TITLE: str = "Dot's restart is waiting"
_GOAL_CHARS: int = 80
_HOME_ROUTE: str = "#/"


async def pending_restart(runtime: Runtime, started_at: float) -> RestartFlag | None:
    """The restart that waits, as the store has it: ``None`` when none does, or when the daemon started after it was
    asked (that start was the restart)."""
    return pending_for(parse_flag(await runtime.store.kv_get(FLAG_KEY)), started_at)


async def _words(runtime: Runtime, flag: RestartFlag) -> tuple[str, str]:
    """What the notice says and the route it opens: the run the restart waits for, by its goal and its id, and that
    it is the run that asked when it is."""
    if flag.waiting_for == "":
        return (
            "It waits for the night's learning (the reflection, the plugin scout or the repair night) to end.",
            _HOME_ROUTE,
        )
    try:
        held: RunRecord = await runtime.store.get_run(flag.waiting_for)
        goal: str = one_line(held.goal, _GOAL_CHARS)
    except NotFoundError:
        goal = "a task"
    who: str = " (the task that asked for the restart)" if flag.waiting_for == flag.asked_by else ""
    return f"It waits for {goal}{who} to end: {flag.waiting_for}. Nothing is stopped.", f"#/runs/{flag.waiting_for}"


async def tell_if_due(runtime: Runtime, now: float, started_at: float = PROCESS_STARTED_AT) -> bool:
    """Tell the owner which run the restart waits for, once, when it has waited :data:`NOTICE_AFTER_S`. Returns whether
    it told. What was told is kept by the time the restart was asked for, so that a restart asked for again is told
    again, and one told is not told at the next tick."""
    flag: RestartFlag | None = await pending_restart(runtime, started_at)
    if flag is None or now - flag.since < NOTICE_AFTER_S:
        return False
    if await runtime.store.kv_get(TOLD_KEY) == repr(flag.since):
        return False
    text, route = await _words(runtime, flag)
    await tell_owner(
        runtime.bus,
        NOTICE_TITLE,
        f"A restart of Dot has waited for {int((now - flag.since) // 60)} minutes. {text}",
        route,
        "system",
        "",
    )
    await runtime.store.kv_set(TOLD_KEY, repr(flag.since))
    log.info("restart_waiting_told", since=flag.since, waiting_for=flag.waiting_for)
    return True
