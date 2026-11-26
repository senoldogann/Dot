"""A run's start reads what needs nothing else at once (item 1918): one after the other they took seconds before the
first model call (the model's profile, the skills' files, the notes' recall). The reads are made to wait for each other
here, so that reads made one after the other never end; no model is called."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent.loop import StartReads, start_reads
from dotagent.config import AppConfig, load_config
from dotagent.core.errors import StoreError
from dotagent.core.together import await_together
from dotagent.memory.service import MemoryIndex, prompt_memories, save_memory
from dotagent.oauth.tokens import Account
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord, RunRecord
from dotagent.store.stats import ModelProfile

NOTE: str = "The owner's invoices are sent on the first day of the month."
GOAL: str = "Send the invoices of this month."
MODEL: str = "ollama:a"
# The reads that wait for each other: the model's profile, the agents, when a model last answered in the chat, and the
# accounts signed in.
MEETING: int = 4


class _MeetingStore(Store):
    """A store whose reads of the profile, the agents and the chat's last call wait until all of them have begun."""

    meeting: asyncio.Barrier

    async def model_profile(self, model: str, runs: int) -> ModelProfile:
        await self.meeting.wait()
        return await super().model_profile(model, runs)

    async def list_agents(self) -> list[AgentRecord]:
        await self.meeting.wait()
        return await super().list_agents()

    async def thread_last_call_at(self, thread_id: str, run_id: str) -> float | None:
        await self.meeting.wait()
        return await super().thread_last_call_at(thread_id, run_id)


class _MeetingAccounts:
    def __init__(self, meeting: asyncio.Barrier) -> None:
        self._meeting: asyncio.Barrier = meeting

    async def accounts(self) -> list[Account]:
        await self._meeting.wait()
        return []


async def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[SimpleNamespace, _MeetingStore, RunRecord]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: _MeetingStore = await _MeetingStore.open(tmp_path / "dot.db", tmp_path / "images")  # type: ignore[assignment]  # open builds the subclass it is called on
    meeting: asyncio.Barrier = asyncio.Barrier(MEETING)
    store.meeting = meeting
    index: MemoryIndex = MemoryIndex(store=store, embedder=None, config=config.memory)
    await save_memory(index, NOTE, ["invoices"], None, None, None, False)
    thread = await store.create_thread("invoices", "ui")
    run: RunRecord = await store.create_run(
        thread.id, GOAL, "task", MODEL, None, "ui", "medium", "standard", None, "ask", False, None, None, None
    )
    runtime = SimpleNamespace(
        store=store,
        config=config,
        memory=index,
        accounts=_MeetingAccounts(meeting),
        plugins=SimpleNamespace(plugins=()),
    )
    return runtime, store, run


async def test_the_start_reads_run_at_once_and_give_what_each_read_gives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, store, run = await _setup(tmp_path, monkeypatch)
    try:
        # Read one after the other, the first read would wait for the others forever.
        async with asyncio.timeout(20):
            reads: StartReads = await start_reads(runtime, run, MODEL, 4000)  # type: ignore[arg-type]  # the parts the start reads
        alone = await prompt_memories(runtime.memory, GOAL, 4000, None)
    finally:
        await store.close()
    assert reads.query == GOAL
    assert [record.content for record in reads.recalled.records] == [record.content for record in alone.records]
    assert NOTE in {record.content for record in reads.recalled.records}
    assert (reads.profile.model, reads.profile.runs) == (MODEL, 0)
    assert (reads.agent_record, reads.agents, reads.signed_in, reads.last_called) == (None, (), (), None)


async def test_the_first_read_that_fails_is_raised_as_it_was_and_stops_the_others() -> None:
    stopped: asyncio.Event = asyncio.Event()

    async def fails() -> None:
        await asyncio.sleep(0)
        raise StoreError("the run history could not be read", {"model": MODEL})

    async def waits() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    failing: asyncio.Task[None] = asyncio.ensure_future(fails())
    waiting: asyncio.Task[None] = asyncio.ensure_future(waits())
    with pytest.raises(StoreError, match="the run history could not be read"):
        await await_together([waiting, failing])
    assert stopped.is_set() and waiting.cancelled()
