"""The work a call to a hook starts, once it passed: the bound schedule fires as by Run now, tainted
(:meth:`Scheduler.fire_by_hook`), or the bound agent's run starts in a chat of its own as a watcher's does, tainted
(:meth:`RunManager.start_hooked`). Neither takes a model, an approval mode or a mode from the hook or the call."""

import secrets

from dotagent.agent.manager import RunManager, thread_title
from dotagent.core.errors import DotError, RunConflict
from dotagent.core.types import JsonObject
from dotagent.hooks.model import Hook, call_words, hook_goal
from dotagent.proactive import WATCHER_SOURCE
from dotagent.proactive.watching import emergency_stop_engaged
from dotagent.runtime import Runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.store.models import AgentRecord, RunRecord, ThreadRecord

_NONCE_BYTES: int = 8


async def start_hook(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler | None, hook: Hook, body: JsonObject, now: float
) -> str:
    """Start the work of ``hook`` for a call that carried ``body``; the id of its run. A schedule's while no scheduler
    runs or that is switched off, an agent's that is paused or while the emergency stop is engaged, is a
    :class:`RunConflict`."""
    words: str = call_words(hook.name, hook.template, body, hook.fields, secrets.token_hex(_NONCE_BYTES))
    if hook.schedule_id is not None:
        if scheduler is None:
            raise RunConflict("schedules are not running right now", {"hook_id": hook.id})
        # A schedule the owner switched off starts nothing from a call either; Run now is theirs alone.
        if not (await runtime.store.get_schedule(hook.schedule_id)).enabled:
            raise RunConflict("the hook's scheduled task is switched off", {"hook_id": hook.id})
        run: RunRecord = await scheduler.fire_by_hook(hook.schedule_id, now, hook.name, words)
        return run.id
    if hook.agent_id is None:
        raise RunConflict("the hook is bound to nothing", {"hook_id": hook.id})
    agent: AgentRecord = await runtime.store.get_agent(hook.agent_id)
    if agent.paused_at is not None:
        raise RunConflict("the hook's agent is paused", {"hook_id": hook.id, "agent_id": agent.id})
    if await emergency_stop_engaged():
        raise RunConflict("no run starts while the emergency stop is engaged", {"hook_id": hook.id})
    thread: ThreadRecord = await runtime.store.create_thread(thread_title(f"{hook.name} · hook"), WATCHER_SOURCE)
    try:
        started: RunRecord = await manager.start_hooked(agent, hook_goal(hook.template), words, thread.id)
    except DotError:
        await runtime.store.delete_threads([thread.id])
        raise
    return started.id
