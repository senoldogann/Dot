"""Item 1715: a run's start records why recall chose its notes, and its end which of them it echoed, through the real
loop on the scripted model with notes found by their words. No model is called and no embedding service is reached."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import db_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.memory.service import save_memory
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, MemoryRecord, RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Write the weekly report"
_FOLDER: str = "The weekly report goes into the archive folder under reports."
_ENGLISH: str = "The weekly report is written in British English."


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    roles: str = "".join(f'{role} = "{SCRIPTED_MODEL}"\n' for role in ("main", "fast", "review", "vision"))
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[models]\n{roles}\n[memory]\nembed_enabled = false\n\n"
        '[agent]\nlessons = "off"\n\n[skills]\nelsewhere = false\n\n[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


def _echoes(run_id: str) -> dict[str, int | None]:
    connection: sqlite3.Connection = sqlite3.connect(db_path())
    try:
        rows = connection.execute("SELECT memory_id, echoed FROM memory_uses WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        connection.close()
    return {str(memory_id): echoed for memory_id, echoed in rows}


async def test_a_run_records_why_its_notes_were_recalled_and_which_it_echoed(runtime: Runtime) -> None:
    folder: MemoryRecord = (await save_memory(runtime.memory, _FOLDER, [], None, None, None, False)).record
    english: MemoryRecord = (await save_memory(runtime.memory, _ENGLISH, [], None, None, None, False)).record
    await runtime.router.register(
        _PROVIDER,
        ScriptedProvider(_PROVIDER, [ScriptedTurn(text="Saved it in the archive folder under reports.", calls=())]),
    )
    manager: RunManager = RunManager(runtime)
    try:
        started: RunRecord = await manager.start(
            _GOAL, "chat", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", None, ()
        )
        async with asyncio.timeout(60.0):
            ended: RunRecord = await manager.wait(started.id)
    finally:
        await manager.shutdown()
    assert ended.status == "done", ended.result
    recalled: list[EventRecord] = await runtime.store.events_of_kind(ended.id, "memories", 5)
    assert recalled[0].data["tainted_left_out"] == 0
    # Found by their words alone: no closeness in meaning to record.
    assert recalled[0].data["similarities"] == {}
    assert await asyncio.to_thread(_echoes, ended.id) == {folder.id: 1, english.id: 0}
