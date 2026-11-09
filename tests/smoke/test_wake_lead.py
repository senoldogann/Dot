"""The run a work order that came back starts in the lead's chat at rest (item 1075): it starts untrusted when the
chat's newest run ended so, the order clean or not (the pre-push review: a lead that read a stranger's words after it
gave the order left them in the chat), and it starts trusted after a clean run; a chat with a run at work is not
started again. A real RunManager on the scripted model; no network."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import WORK_ORDER_SOURCE, RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Judged.", "success": True}),)
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[RunManager]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false\ngoal_mode = false\n\n"
        f'[models]\nmain = "{SCRIPTED_MODEL}"\nreview = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH] * 6))
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _lead(manager: RunManager, read_a_stranger: bool) -> RunRecord:
    """A lead's run that ended done, having read content third parties can author or not."""
    run: RunRecord = await manager.start(
        "Run the team.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(30):
        assert (await manager.wait(run.id)).status == "done"
    if read_a_stranger:
        await manager._runtime.store.add_event(run.id, "tool_result", {"tool": "web_fetch", "tainted": True})
    return run


async def _woken_tainted(manager: RunManager, lead: RunRecord) -> bool:
    woken: RunRecord | None = await manager.wake_lead(
        lead.thread_id, "Work order came back: judge it.", "The agent's answer.", False, ()
    )
    assert woken is not None and woken.source == WORK_ORDER_SOURCE
    queued = await manager._runtime.store.last_event(woken.id, "run_queued")
    assert queued is not None
    async with asyncio.timeout(30):
        await manager.wait(woken.id)
    return queued.data.get("tainted") is True


async def test_the_woken_run_is_untrusted_after_a_lead_that_read_a_stranger(manager: RunManager) -> None:
    assert await _woken_tainted(manager, await _lead(manager, True)) is True


async def test_the_woken_run_is_trusted_after_a_clean_lead(manager: RunManager) -> None:
    assert await _woken_tainted(manager, await _lead(manager, False)) is False
