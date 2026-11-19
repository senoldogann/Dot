"""An agent is one conversation (item 1233): the store names each agent's home chat, the newest chat of the owner's
whose latest run is the agent's, however many chats are newer, and leaves out the chats a schedule or another source
that is not the owner's started, and those whose latest run is another agent's or Dot's."""

import asyncio
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord

_OWNERS: tuple[str, ...] = ("ui", "cli")


async def _chat(store: Store, title: str, source: str, agent_id: str | None) -> ThreadRecord:
    """A chat started by `source`, with one run of `agent_id` (Dot's for `None`) that is over."""
    thread: ThreadRecord = await store.create_thread(title, source)
    run: RunRecord = await store.create_run(
        thread.id,
        f"{title} goal",
        "chat",
        "ollama:m",
        None,
        source,
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        agent_id,
        None,
    )
    await store.finish_run(run.id, "done", "Done.")
    # The chats' moments are seconds: each is a moment after the one before it.
    await asyncio.sleep(0.002)
    return thread


async def test_an_agents_home_chat_is_its_newest_of_the_owners_however_many_chats_are_newer(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        await _chat(store, "Scout's first", "ui", "agt_scout")
        home: ThreadRecord = await _chat(store, "Scout's second", "cli", "agt_scout")
        # Newer chats that are not the owner's with Scout: a schedule's, another agent's, Dot's, and many more of
        # Dot's than the sidebar lists.
        await _chat(store, "Scout's schedule", "schedule", "agt_scout")
        await _chat(store, "Scout's work for the lead", "work_order", "agt_scout")
        await _chat(store, "Writer's", "ui", "agt_writer")
        for number in range(205):
            await _chat(store, f"Dot's {number}", "ui", None)
        homes = await store.agent_home_chats(_OWNERS)
        assert homes["agt_scout"].id == home.id
        assert homes["agt_writer"].title == "Writer's"
        assert set(homes) == {"agt_scout", "agt_writer"}
        # One agent's alone.
        alone = await store.agent_home_chats(_OWNERS, agent_id="agt_scout")
        assert list(alone) == ["agt_scout"]
        assert alone["agt_scout"].id == home.id
        assert await store.agent_home_chats(_OWNERS, agent_id="agt_nobody") == {}
    finally:
        await store.close()


async def test_a_message_to_the_agent_in_an_older_chat_makes_that_chat_its_home(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        older: ThreadRecord = await _chat(store, "Older", "ui", "agt_scout")
        await _chat(store, "Newer", "ui", "agt_scout")
        run: RunRecord = await store.create_run(
            older.id,
            "Go on",
            "chat",
            "ollama:m",
            None,
            "ui",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            "agt_scout",
            None,
        )
        await store.finish_run(run.id, "done", "Done.")
        assert (await store.agent_home_chats(_OWNERS))["agt_scout"].id == older.id
    finally:
        await store.close()


async def test_no_source_names_no_home(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        await _chat(store, "Scout's", "ui", "agt_scout")
        assert await store.agent_home_chats(()) == {}
    finally:
        await store.close()
