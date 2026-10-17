"""One run at work on a chat (item 188): the store refuses a run on a chat where another process drives a run at work,
and on a chat whose queued message another process is starting; a delegated run works beside its parent, a process
keeps its own runs apart itself, the process that claimed the message starts its run, and a chat whose run ended takes
the next. Two stores on one database stand for two processes."""

from pathlib import Path

import pytest

from dotagent.core.errors import RunConflict
from dotagent.store.db import Store
from dotagent.store.models import NO_PICKS, QueuedMessage, RunRecord, ThreadRecord

ACTIVE: str = "a run is already active on this thread"


async def _run(store: Store, thread: ThreadRecord, parent_run_id: str | None, owner: str) -> RunRecord:
    return await store.create_run(
        thread.id,
        "goal",
        "task",
        "ollama:a",
        parent_run_id,
        "test",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        owner,
    )


async def test_a_chat_takes_one_run_at_work_whichever_process_drives_it(tmp_path: Path) -> None:
    daemon: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    command: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await daemon.create_thread("t", "test")
        first: RunRecord = await _run(command, thread, None, "owner-b")
        with pytest.raises(RunConflict, match=ACTIVE) as refused:
            await _run(daemon, thread, None, "owner-a")
        assert refused.value.context["run_ids"] == [first.id]
        delegated: RunRecord = await _run(command, thread, first.id, "owner-b")
        # A process keeps its own runs on a chat apart itself (RunManager), its run whose end is still to be stored too.
        own: RunRecord = await _run(command, thread, None, "owner-b")
        for ended in (first, own):
            await command.end_run(ended.id, "done", "answer", {"status": "done", "result": "answer"})
        second: RunRecord = await _run(daemon, thread, None, "owner-a")
        assert {run.id for run in await daemon.list_runs(10, thread.id)} == {first.id, delegated.id, own.id, second.id}
    finally:
        await command.close()
        await daemon.close()


async def test_a_chat_whose_queued_message_another_process_starts_takes_that_run_only(tmp_path: Path) -> None:
    daemon: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    command: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await daemon.create_thread("t", "test")
        message: QueuedMessage = await daemon.add_queued(
            thread.id, "next", [], "task", None, None, "standard", None, NO_PICKS, None, None, None, "test"
        )
        assert await daemon.claim_queued(thread.id, "owner-a") == message
        with pytest.raises(RunConflict, match=ACTIVE):
            await _run(command, thread, None, "owner-b")
        started: RunRecord = await _run(daemon, thread, None, "owner-a")
        await daemon.drop_claimed(thread.id)
        with pytest.raises(RunConflict, match=ACTIVE) as refused:
            await _run(command, thread, None, "owner-b")
        assert refused.value.context["run_ids"] == [started.id]
    finally:
        await command.close()
        await daemon.close()
