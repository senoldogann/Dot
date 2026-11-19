"""A real run keeps a pipeline's ledger (item 1227): on a scripted model, in the ask mode, it adds a client and begins
a mail in the record file of a throwaway workspace, the gate letting each write through as a write in the workspace;
the next run, asked to send the same mail, is refused by the ledger and says so."""

import asyncio
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.pipeline.ledger import parse
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

RECORD: str = "leads/client-pipeline.md"


def _turn(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _finish(answer: str) -> ScriptedTurn:
    return _turn(FINISH_TOOL, {"answer": answer, "success": True})


_REPLIES: Replies = Replies()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime() -> AsyncIterator[Runtime]:
    home: Path = Path(tempfile.mkdtemp())
    (home / "config.toml").write_text(
        f'[agent]\nplaybooks = false\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", tempfile.mkdtemp())
    patch.setenv("DOT_LOG_DIR", tempfile.mkdtemp())
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(PROVIDER, _REPLIES)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _play(runtime: Runtime, turns: list[ScriptedTurn]) -> RunRecord:
    _REPLIES.clear()
    _REPLIES.push(*turns)
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Write to the client", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
        )
        async with asyncio.timeout(60):
            return await manager.wait(run.id)
    finally:
        await manager.shutdown()


async def test_a_run_begins_a_mail_once_and_the_next_one_is_refused_the_same_send(runtime: Runtime) -> None:
    first: RunRecord = await _play(
        runtime,
        [
            _turn("pipeline_client", {"record": RECORD, "client": "a-oy", "name": "A Oy", "contact": "info@a.example"}),
            _turn("pipeline_begin", {"record": RECORD, "client": "a-oy", "action": "first-mail"}),
            _finish("Begun; the run stops here as if the Mac had slept."),
        ],
    )
    assert first.status == "done", first.result
    path: Path = runtime.workspace / RECORD
    text: str = await asyncio.to_thread(path.read_text, encoding="utf-8")
    assert [(action.key, action.state) for action in parse(text).actions] == [("a-oy/first-mail", "begun")]
    second: RunRecord = await _play(
        runtime,
        [
            _turn("pipeline_begin", {"record": RECORD, "client": "a-oy", "action": "first-mail"}),
            _finish("The ledger refused it: the first try's result is unseen."),
        ],
    )
    assert second.status == "done", second.result
    results: list[str] = [
        str(event.data.get("text", ""))
        for event in await runtime.store.list_events(second.id, 0, 200)
        if event.kind == "tool_result"
    ]
    assert any("nobody has seen what came of it" in result for result in results), results
    assert await asyncio.to_thread(path.read_text, encoding="utf-8") == text
    assert await asyncio.to_thread(path.with_name(f".{path.name}.dot-lock").exists)
