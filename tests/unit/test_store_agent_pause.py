"""An agent's pause in the store (item 465): kept by the editor's Save and across a restart, cleared by resuming; the
agents of a store from before the column are not paused."""

import asyncio
import sqlite3
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import AgentRecord

_PAUSED_AT: float = 1_760_000_000.0


async def _agent(store: Store) -> AgentRecord:
    return await store.create_agent("Scout", "Reads the news", "", "", None, None, ("web_search",), None, None)


async def test_a_pause_is_kept_by_a_save_and_a_restart_and_cleared_by_resuming(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        scout: AgentRecord = await _agent(store)
        assert scout.paused_at is None
        assert (await store.set_agent_paused(scout.id, _PAUSED_AT)).paused_at == _PAUSED_AT
        saved: AgentRecord = await store.update_agent(
            scout.id, "Scout", "Reads the papers", "", "", None, None, ("web_search",), None
        )
        assert (saved.role, saved.paused_at) == ("Reads the papers", _PAUSED_AT)
    finally:
        await store.close()
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert (await store.get_agent(scout.id)).paused_at == _PAUSED_AT
        assert (await store.set_agent_paused(scout.id, None)).paused_at is None
    finally:
        await store.close()


def _forget_pauses(path: Path) -> None:
    """Make the database at `path` what a version from before item 465 left: agents with no pause column."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE agents DROP COLUMN paused_at")
        connection.commit()
    finally:
        connection.close()


async def test_the_agents_of_a_store_from_before_pauses_are_not_paused(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        before: AgentRecord = await _agent(store)
    finally:
        await store.close()
    await asyncio.to_thread(_forget_pauses, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await store.get_agent(before.id) == before
    finally:
        await store.close()
