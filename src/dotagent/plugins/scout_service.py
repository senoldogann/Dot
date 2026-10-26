"""The plugin scout as the daemon runs it (item 711): its state in the store, the owner's recent goals it is fitted
to, GitHub's token from Customize › Connectors, and one night at a time, whether the scheduler starts it at
``[scout] time`` or the owner asks for one on Customize › Plugins."""

import asyncio
import time
import weakref
from datetime import date, datetime
from typing import TYPE_CHECKING

from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path, plugins_dir
from dotagent.plugins.gallery import download_archive
from dotagent.plugins.gallery_list import GALLERY
from dotagent.plugins.scout import ScoutDeps, ScoutStopped, run_night
from dotagent.plugins.scout_sources import GitHub
from dotagent.plugins.scout_types import SCOUT_KEY, ScoutState, bounded, merged, read_state, retired
from dotagent.proactive.github import GITHUB_TOKEN_NAME
from dotagent.secrets import optional_secret
from dotagent.server import UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import RunRecord

if TYPE_CHECKING:
    from dotagent.runtime import Runtime

log = get_logger(__name__)

# The runs whose goals are the owner's own words: those started on Dot's pages and by a message from their phone.
_OWNERS_SOURCES: frozenset[str] = frozenset({UI_SOURCE, "imessage"})
_GOALS: int = 20
_RUNS_READ: int = 300
_GOAL_CHARS: int = 300
# One night at a time, by the runtime's store (a runtime is a frozen dataclass, not one to key by): the scheduler's and
# the owner's "Look now" wait for each other.
_NIGHTS: "weakref.WeakKeyDictionary[Store, asyncio.Lock]" = weakref.WeakKeyDictionary()
# Each change of the stored state at a time: a night's end and the owner's Install, Skip or Revert read it, change it
# and write it back whole.
_STATES: "weakref.WeakKeyDictionary[Store, asyncio.Lock]" = weakref.WeakKeyDictionary()


async def load(store: Store) -> ScoutState:
    return read_state(await store.kv_get(SCOUT_KEY))


async def save(store: Store, state: ScoutState) -> None:
    await store.kv_set(SCOUT_KEY, bounded(state).model_dump_json())


async def owner_goals(store: Store) -> tuple[str, ...]:
    """The goals the owner gave Dot lately, newest first, each once: what the scout fits plugins to."""
    runs: list[RunRecord] = await store.list_runs(_RUNS_READ, None)
    goals: list[str] = []
    for run in runs:
        if run.parent_run_id is None and run.source in _OWNERS_SOURCES:
            goal: str = " ".join(run.goal.split())[:_GOAL_CHARS]
            if goal and goal not in goals:
                goals.append(goal)
        if len(goals) >= _GOALS:
            break
    return tuple(goals)


def _lock(runtime: "Runtime") -> asyncio.Lock:
    found: asyncio.Lock | None = _NIGHTS.get(runtime.store)
    if found is None:
        found = asyncio.Lock()
        _NIGHTS[runtime.store] = found
    return found


def state_lock(runtime: "Runtime") -> asyncio.Lock:
    """The lock a change of the stored state is made under (read, changed and written back)."""
    found: asyncio.Lock | None = _STATES.get(runtime.store)
    if found is None:
        found = asyncio.Lock()
        _STATES[runtime.store] = found
    return found


def _stop_engaged() -> bool:
    return failsafe_path().exists()


def running(runtime: "Runtime") -> bool:
    return _lock(runtime).locked()


async def night(runtime: "Runtime", now: float) -> ScoutState:
    """One night of the scout, stored once it ends over the state as it is then (what the owner decided during the night
    is kept, :func:`merged`); a second asked for while one runs waits for it and then runs. Refused while the emergency
    stop is engaged; one engaged during the night ends it, and nothing is stored."""
    if await asyncio.to_thread(_stop_engaged):
        raise ScoutStopped("the emergency stop is engaged: Dot does not look for plugins until it is released", {})
    async with _lock(runtime):
        config = runtime.config
        token: str | None = await asyncio.to_thread(optional_secret, GITHUB_TOKEN_NAME)
        github: GitHub = GitHub(token)
        try:
            deps: ScoutDeps = ScoutDeps(
                github=github,
                fetch=download_archive,
                router=runtime.router,
                model=config.models.review,
                review_timeout_s=config.scout.review_timeout_s,
                embedder=runtime.memory.embedder,
                goals=await owner_goals(runtime.store),
                plugins=plugins_dir(),
                gallery=GALLERY,
                today=datetime.fromtimestamp(now, runtime.schedule_zone).date(),
                now=now,
                max_leads=config.scout.leads_per_night,
                max_updates=config.scout.updates_per_night,
                deadline=time.monotonic() + config.scout.minutes * 60,
                stopped=_stop_engaged,
            )
            before: ScoutState = await load(runtime.store)
            after: ScoutState = await run_night(deps, before)
        finally:
            await github.aclose()
        async with state_lock(runtime):
            final: ScoutState = await asyncio.to_thread(
                retired, merged(before, after, await load(runtime.store)), plugins_dir()
            )
            await save(runtime.store, final)
        return final


def today_of(runtime: "Runtime") -> date:
    return datetime.now(runtime.schedule_zone).date()
