"""An agent is one conversation, and each chat it is with is its own: the chat a message to an agent reaches is bound to
it (``threads.agent_id``), a message the owner sends there goes to that agent from any path, the newest of its chats is
its home chat, and a run of Dot's in it does not take it from the agent."""

import asyncio
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord

_OWNERS: tuple[str, ...] = ("ui", "cli")


async def _chat(
    store: Store, title: str, source: str, agent_id: str | None, bounded: str | None = None
) -> ThreadRecord:
    """A chat started by `source`, bound to `bounded` when given, with one run of `agent_id` (Dot's for `None`)."""
    thread: ThreadRecord = await store.create_thread(title, source, None, bounded)
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
    await asyncio.sleep(0.002)
    return thread


async def test_the_newest_chat_that_is_an_agents_is_its_home(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        home: ThreadRecord = await _chat(store, "Scout's first", "ui", "agt_scout", bounded="agt_scout")
        # Newer chats that are not Scout's, one of them with a run of Scout's: they are nobody's home but his.
        await _chat(store, "Dot's", "ui", None)
        await _chat(store, "Scout's unbound", "ui", "agt_scout")
        await _chat(store, "Writer's", "ui", "agt_writer", bounded="agt_writer")
        homes = await store.agent_home_chats(_OWNERS)
        assert homes["agt_scout"].id == home.id
        assert homes["agt_writer"].title == "Writer's"
    finally:
        await store.close()


async def test_a_new_chat_of_the_agents_takes_the_home_from_the_older_one(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        await _chat(store, "Scout's first", "ui", "agt_scout", bounded="agt_scout")
        newer: ThreadRecord = await _chat(store, "Scout's new chat", "ui", "agt_scout", bounded="agt_scout")
        homes = await store.agent_home_chats(_OWNERS)
        assert homes["agt_scout"].id == newer.id
    finally:
        await store.close()


async def test_a_bound_chat_stays_the_home_though_dots_run_comes_last_in_it(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        home: ThreadRecord = await _chat(store, "Scout's own", "ui", "agt_scout", bounded="agt_scout")
        # The owner asked Dot something in the agent's chat: the latest run there is Dot's.
        run: RunRecord = await store.create_run(
            home.id,
            "A question for Dot",
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
            None,
            None,
        )
        await store.finish_run(run.id, "done", "Done.")
        homes = await store.agent_home_chats(_OWNERS)
        assert homes["agt_scout"].id == home.id
    finally:
        await store.close()


async def test_binding_never_takes_a_teams_chat_or_another_agents_chat(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        other: ThreadRecord = await _chat(store, "Writer's own", "ui", "agt_writer", bounded="agt_writer")
        await store.bind_thread_agent(other.id, "agt_scout")
        assert (await store.get_thread(other.id)).agent_id == "agt_writer"
        team: ThreadRecord = await store.create_thread("A team's chat", "ui", "tem_one")
        await store.bind_thread_agent(team.id, "agt_scout")
        assert (await store.get_thread(team.id)).agent_id is None
    finally:
        await store.close()


async def test_a_chat_bound_to_an_agent_is_no_home_for_dot(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        await _chat(store, "Dot's", "ui", None)
        await _chat(store, "Scout's own", "ui", "agt_scout", bounded="agt_scout")
        found = await store.dot_home_chat(_OWNERS, False)
        assert found is not None
        assert found.title == "Dot's"
    finally:
        await store.close()


async def test_forgetting_an_agent_leaves_its_chats_as_plain_chats(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        agent = await store.create_agent("Scout", "watch", "Watch.", "ollama:m", None, None, (), None, None)
        chat: ThreadRecord = await _chat(store, "Scout's own", "ui", agent.id, bounded=agent.id)
        await store.delete_agent(agent.id)
        assert (await store.get_thread(chat.id)).agent_id is None
    finally:
        await store.close()
