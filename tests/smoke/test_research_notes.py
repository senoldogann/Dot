"""A research run in the real loop keeps its notes file and a memory note (item 34): the scripted model writes the notes,
adds to them, saves a fact and finishes; the file holds both parts, the fact is in memory, and the run is done. The
file tools that change the world stay out of the run."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.notes import NOTES_TOOL, notes_path
from dotagent.tools.registry import tools_for_mode
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Find out what the catalogue sells and keep notes."
FIRST: str = "# Findings\n\n- The catalogue sells lamps (source: https://example.com/catalogue/)."
SECOND: str = "- Thirty products in all (source: https://example.com/catalogue/p30.html)."
FACT: str = "The user's catalogue research is about lamps."
_TIME_LIMIT_S: float = 60.0


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _call(tool: str, arguments: JsonObject) -> ScriptedCall:
    return ScriptedCall(tool=tool, arguments=arguments)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session() -> AsyncIterator[tuple[Runtime, RunManager]]:
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s)
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(
                "ollama",
                ScriptedProvider(
                    "ollama",
                    [
                        _turn(_call(NOTES_TOOL, {"content": FIRST})),
                        _turn(_call(NOTES_TOOL, {"content": SECOND, "append": True})),
                        _turn(_call("memory_save", {"content": FACT})),
                        _turn(
                            _call(
                                FINISH_TOOL,
                                {"answer": "The catalogue sells lamps; see the notes file.", "success": True},
                            )
                        ),
                    ],
                ),
            )
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_a_research_run_keeps_its_notes_file_and_a_memory_note(session: tuple[Runtime, RunManager]) -> None:
    runtime, manager = session
    offered: set[str] = {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
    assert {NOTES_TOOL, "memory_save", "web_crawl"} <= offered
    assert not {"write_file", "edit_file", "shell"} & offered
    run: RunRecord = await manager.start(
        GOAL, "research", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "yolo", False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await manager.wait(run.id)
    assert record.status == "done", record.result
    events: list[EventRecord] = await runtime.store.list_events(record.id, 0, 1000)
    results: list[JsonObject] = [event.data for event in events if event.kind == "tool_result"]
    assert [data["tool"] for data in results] == [NOTES_TOOL, NOTES_TOOL, "memory_save", FINISH_TOOL]
    assert not any(data.get("is_error") for data in results), results
    path: Path = notes_path(runtime.workspace, record.id)
    assert await asyncio.to_thread(path.read_text, "utf-8") == f"{FIRST}\n\n{SECOND}"
    memories: list[MemoryRecord] = await runtime.store.list_memories(10, 0)
    assert [memory.content for memory in memories] == [FACT]
