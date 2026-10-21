"""The agent of a schedule in the store (item 462): kept with the schedule; deleting the agent switches its schedules
off, in the same write, and names no agent for them; a store from before item 462 has every schedule Dot's."""

import asyncio
import sqlite3
from pathlib import Path

from dotagent.store.db import AGENT_DELETED_STATUS, Store
from dotagent.store.models import AgentRecord, ScheduleOptions, ScheduleRecord


def _options(agent_id: str | None) -> ScheduleOptions:
    return ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals=None,
        goal_mode=None,
        timezone=None,
        history_runs=None,
        same_chat=None,
        stop_after_failures=None,
        notify=None,
        agent_id=agent_id,
    )


async def _agent(store: Store, name: str) -> AgentRecord:
    return await store.create_agent(name, "Reads the news", "", "", None, None, ("web_search",), None, None)


async def _schedule(store: Store, name: str, agent_id: str | None) -> ScheduleRecord:
    return await store.add_schedule(name, "0 9 * * *", None, "Look.", "task", 2_000_000_000.0, _options(agent_id), None)


async def test_deleting_an_agent_switches_its_schedules_off_and_leaves_the_others(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        scout: AgentRecord = await _agent(store, "Scout")
        keeper: AgentRecord = await _agent(store, "Keeper")
        scouts: ScheduleRecord = await _schedule(store, "news", scout.id)
        keepers: ScheduleRecord = await _schedule(store, "books", keeper.id)
        dots: ScheduleRecord = await _schedule(store, "mail", None)
        assert (await store.get_schedule(scouts.id)).options.agent_id == scout.id
        await store.delete_agent(scout.id)
        gone: ScheduleRecord = await store.get_schedule(scouts.id)
        assert (gone.enabled, gone.next_run_at, gone.options.agent_id, gone.last_status) == (
            False,
            None,
            None,
            AGENT_DELETED_STATUS,
        )
        assert await store.get_schedule(keepers.id) == keepers
        assert await store.get_schedule(dots.id) == dots
    finally:
        await store.close()


def _forget_agents(path: Path) -> None:
    """Make the database at `path` what a version from before item 462 left: schedules with no agent column."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE schedules DROP COLUMN agent_id")
        connection.commit()
    finally:
        connection.close()


async def test_a_store_from_before_agents_had_schedules_has_every_schedule_dots(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        before: ScheduleRecord = await _schedule(store, "mail", None)
    finally:
        await store.close()
    await asyncio.to_thread(_forget_agents, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await store.get_schedule(before.id) == before
    finally:
        await store.close()
