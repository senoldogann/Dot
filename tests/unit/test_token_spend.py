"""Where a window's tokens went (item 881), counted from a real database: the goals that spent the most, the chats
of one title together, and a goal started again in other chats whose runs keep failing marked to look at."""

import time
from pathlib import Path

from dotagent.cli_stats import render_spend
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, RunStatus
from dotagent.store.stats import TokenSpend, collect_spend


async def _run(store: Store, thread_id: str, status: RunStatus, tokens: int) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread_id, "goal", "task", "m:m", None, "test", "medium", "standard", None, "ask", False, None, None, None
    )
    await store.add_run_tokens(run.id, tokens, 0)
    await store.finish_run(run.id, status, "answer")
    return run


async def test_a_goal_started_again_in_other_chats_and_failing_is_marked_and_one_busy_chat_is_not(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    try:
        owners = await store.create_thread("Plan my week", "test")
        for _ in range(6):
            await _run(store, owners.id, "done", 50_000)
        for _ in range(3):
            # The same goal, started again in a chat of its own each time (the hiring CTF of 2026-10-30).
            again = await store.create_thread("Solve the hiring CTF", "test")
            await _run(store, again.id, "failed", 40_000)
        small = await store.create_thread("Tidy the downloads", "test")
        await _run(store, small.id, "done", 10_000)
    finally:
        await store.close()
    spend: TokenSpend = collect_spend(tmp_path / "dot.sqlite3", time.time() - 3600, 5)
    assert spend.total == 6 * 50_000 + 3 * 40_000 + 10_000
    assert [(chat.title, chat.chats, chat.runs, chat.failed) for chat in spend.chats] == [
        ("Plan my week", 1, 6, 0),
        ("Solve the hiring CTF", 3, 3, 3),
        ("Tidy the downloads", 1, 1, 0),
    ]
    lines: list[str] = render_spend(spend)
    marked: list[str] = [line for line in lines if "look whether it gets anywhere" in line]
    assert len(marked) == 1 and "3 chats, 3 runs, 3 failed  Solve the hiring CTF" in marked[0]
    assert collect_spend(tmp_path / "dot.sqlite3", time.time() + 60, 5) == TokenSpend(total=0, chats=())
    assert render_spend(TokenSpend(total=0, chats=())) == []


async def test_chats_of_a_short_title_or_not_the_owners_are_not_one_goal(tmp_path: Path) -> None:
    """The pre-push review of item 881: two failing chats titled "selam", or two watcher chats about mails of one
    subject, were marked as one goal started again and failing."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    try:
        for _ in range(2):
            hello = await store.create_thread("selam", "test")
            await _run(store, hello.id, "failed", 1_000)
            mail = await store.create_thread("Client mail · Invoice for October", "watcher")
            await _run(store, mail.id, "failed", 1_000)
    finally:
        await store.close()
    spend: TokenSpend = collect_spend(tmp_path / "dot.sqlite3", time.time() - 3600, 10)
    assert len(spend.chats) == 4
    assert all(chat.chats == 1 and not chat.retried for chat in spend.chats), spend.chats
