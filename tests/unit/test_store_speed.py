"""Fast mode in the store (item 223): a run, a chat's choices, a queued message and a campaign keep their speed, and
those of a store from before fast mode are at the standard speed, which is how they ran."""

import asyncio
import sqlite3
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import CampaignRecord, ChatChoices, QueuedMessage, RunRecord, ThreadRecord

OPUS: str = "anthropic:claude-opus-5-5"


def _forget_speed(path: Path) -> None:
    """Make the database at `path` what a version from before fast mode left: no speed anywhere."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        for table in ("runs", "queued_messages", "campaigns"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN speed")
        connection.execute("UPDATE threads SET choices = json_remove(choices, '$.speed') WHERE choices IS NOT NULL")
        connection.commit()
    finally:
        connection.close()


async def _speeds(store: Store, run: RunRecord, campaign: CampaignRecord) -> tuple[str, ...]:
    choices: ChatChoices | None = await store.chat_choices(run.thread_id)
    queued: list[QueuedMessage] = await store.list_queued(run.thread_id)
    assert choices is not None and len(queued) == 1
    return (
        (await store.get_run(run.id)).speed,
        choices.speed,
        queued[0].speed,
        (await store.get_campaign(campaign.id)).speed,
    )


async def test_a_store_keeps_the_speed_and_one_from_before_fast_mode_holds_the_standard_speed(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("Say hi.", "test")
        run: RunRecord = await store.create_run(
            thread.id, "Say hi.", "chat", OPUS, None, "test", "high", "fast", "ask", False, None, None, None
        )
        await store.finish_run(run.id, "done", "Done.")
        await store.set_chat_choices(
            thread.id, ChatChoices(mode="chat", model=OPUS, effort=None, speed="fast", approvals=None, goal_mode=None)
        )
        await store.add_queued(thread.id, "Again.", [], "chat", OPUS, None, "fast", None, None, None, "test")
        campaign: CampaignRecord = await store.create_campaign(
            "Keep at it", "Keep at it.", thread.id, OPUS, "high", "fast", "ask", [], "own"
        )
        assert await _speeds(store, run, campaign) == ("fast", "fast", "fast", "fast")
    finally:
        await store.close()
    await asyncio.to_thread(_forget_speed, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await _speeds(store, run, campaign) == ("standard", "standard", "standard", "standard")
    finally:
        await store.close()
