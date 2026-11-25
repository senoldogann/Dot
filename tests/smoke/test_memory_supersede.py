"""``memory_save`` with a subject end to end (item 1729): a newer note of the same subject replaces the older one, the
model is told so, a search and the notes a run starts with pass over the older one, the Memory page's list keeps it, and
nothing in the policy changed: a save is allowed as before and asks only once the run read outside content."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.verify import new_facts
from dotagent.config import AppConfig, load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.memory.service import REST_TAG, Remembered, prompt_memories
from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import MemoryRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

CITY: str = "The user lives in Oulu."
MOVED: str = "The user lives in Helsinki."


class _Recorder:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""


def _without_embeddings(config: AppConfig) -> AppConfig:
    return config.model_copy(update={"memory": config.memory.model_copy(update={"embed_enabled": False})})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    home: Path = tmp_path_factory.mktemp("home")
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(_without_embeddings(load_config(home / "config.toml")))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, PreparedCall]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="supersede-test",
        agent_id=None,
        thread_id="supersede-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    prepared: PreparedCall = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx), prepared


async def test_a_note_saved_with_the_subject_of_an_older_one_replaces_it_and_the_model_is_told(
    runtime: Runtime,
) -> None:
    first, _ = await _call(runtime, "memory_save", {"content": CITY, "subject": "User city"})
    assert not first.is_error, first.text
    assert "about user-city" in first.text, first.text
    assert "It replaced" not in first.text
    second, prepared = await _call(runtime, "memory_save", {"content": MOVED, "subject": "user-city"})
    assert not second.is_error, second.text
    assert "It replaced these older notes, which rest now" in second.text, second.text
    assert CITY in second.text and "Saved:" in second.text, second.text

    notes: list[MemoryRecord] = await runtime.store.list_memories(10, 0)
    old: MemoryRecord = next(note for note in notes if note.content == CITY)
    assert REST_TAG in old.tags and old.replaced_by is not None and old.subject == "user-city"

    found, _ = await _call(runtime, "memory_search", {"query": "which city does the user live in"})
    assert MOVED in found.text and CITY not in found.text, found.text
    start: Remembered = await prompt_memories(runtime.memory, "which city does the user live in", 4000, None)
    assert [note.content for note in start.records] == [MOVED]

    # The policy is as it was: a save is allowed, and asks only once the run read outside content.
    assert prepared.action.flags == ("persistence",)
    assert decide(prepared.action, "task", False, "aggressive", [], None).verdict == "allow"
    assert decide(prepared.action, "task", True, "aggressive", [], None).verdict == "ask"
