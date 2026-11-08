"""A run ends once (item 1045): its end, its ``run_finished`` event and the scores of its notes are stored by the first
end alone; a later end (a run closed as its command's process seemed gone, then ended by that process) and a later
move between the working states leave it as it ended."""

from pathlib import Path

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, RunStatus, ThreadRecord


async def _run(store: Store, thread: ThreadRecord) -> RunRecord:
    return await store.create_run(
        thread.id, "goal", "task", "ollama:a", None, "test", "medium", "standard", None, "ask", False, None, None, "o"
    )


async def test_the_first_end_stands_and_is_the_only_one_told(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run: RunRecord = await _run(store, thread)
        first = await store.end_run(run.id, "failed", "its command died", {"status": "failed"})
        assert first is not None
        again = await store.end_run(run.id, "done", "answer", {"status": "done"})
        assert again is None
        ended: RunRecord = await store.get_run(run.id)
        assert (ended.status, ended.result) == ("failed", "its command died")
        assert not await store.finish_run(run.id, "cancelled", "stopped")
        finished = [event for event in await store.list_events(run.id, 0, 100) if event.kind == "run_finished"]
        assert len(finished) == 1, finished
    finally:
        await store.close()


async def test_a_run_moves_between_its_working_states_until_it_ends(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run: RunRecord = await _run(store, thread)
        assert await store.set_run_status(run.id, "running")
        assert await store.set_run_status(run.id, "blocked")
        assert await store.set_run_status(run.id, "running")
        await store.end_run(run.id, "cancelled", "stopped", {"status": "cancelled"})
        # An approval answered as the run was cancelled sets it running no more.
        assert not await store.set_run_status(run.id, "running")
        assert (await store.get_run(run.id)).status == "cancelled"
    finally:
        await store.close()


async def test_a_run_ended_while_queued_does_none_of_its_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run closed as it was about to start (a dead command's close, the daemon's start) starts no work: no
    ``run_started``, no call of its model, and the end it had is the only one it keeps (item 1045)."""
    provider: str = parse_model_ref(SCRIPTED_MODEL).provider
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        manager: RunManager = RunManager(runtime)
        try:
            # No reply scripted: a call of the model would end the run with an error of its own.
            await runtime.router.register(provider, ScriptedProvider(provider, []))
            store: Store = runtime.store
            moving = store.set_run_status

            async def closed_first(run_id: str, status: RunStatus) -> bool:
                if status == "running":
                    await store.end_run(run_id, "failed", "its command died", {"status": "failed"})
                return await moving(run_id, status)

            monkeypatch.setattr(store, "set_run_status", closed_first)
            run: RunRecord = await manager.start(
                "Write the note.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo",
                False, (),
            )  # fmt: skip
            ended: RunRecord = await manager.wait(run.id)
            kinds: list[str] = [event.kind for event in await store.list_events(run.id, 0, 10_000)]
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    assert (ended.status, ended.result) == ("failed", "its command died")
    assert "run_started" not in kinds, kinds
    assert kinds.count("run_finished") == 1, kinds
