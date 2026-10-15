"""A stop of the daemon, in the real loop, store and scheduler, on a scripted model in a throw-away home: a step it cuts
keeps the results of the calls that had ended, so only the calls it cut are left for a resumed run to look at, and the
stop does not wait for a reflection under way, which records nothing, so the next reflection reads the same runs again.
No model is called.
"""

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.reflection import Reflection, last_reflection, reflection_cron
from dotagent.agent.resume import InterruptedCall, interrupted_calls
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.scheduler.timing import next_firing
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_SHUTDOWN
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Build report.md from data.csv."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
# A stop that waits for nothing takes well under this.
_STOP_LIMIT_S: float = 10.0
# Runs until the test creates the file ``stop``, at most 30 seconds: the shell tool starts each command in a session of
# its own, so the end of the run does not reach it.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"
WRITE: ScriptedCall = ScriptedCall(tool="write_file", arguments={"path": "report.md", "content": "# Report"})
SHELL: ScriptedCall = ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND})
FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "report.md is written.", "success": True}),)
)


class Hanging:
    """The scripted model, which answers its first ``answers`` calls and leaves every later one without an answer;
    ``hanging`` is set once one waits."""

    def __init__(self, script: ScriptedProvider, answers: int) -> None:
        self._script: ScriptedProvider = script
        self._answers: int = answers
        self.hanging: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self._answers > 0:
            self._answers -= 1
            return await self._script.complete(request, sink)
        self.hanging.set()
        await asyncio.Event().wait()
        raise AssertionError("an event nobody sets was set")

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def _call_starts(subscription: Subscription, run_id: str, tool: str) -> None:
    """Wait until run ``run_id`` starts a call of ``tool``."""
    while True:
        event: EventRecord = await subscription.queue.get()
        if event.run_id == run_id and event.kind == "tool_call" and event.data.get("tool") == tool:
            return


def _let_the_command_end(workspace: Path) -> None:
    """The cut command goes on in a session of its own: let it end."""
    if workspace.is_dir():
        (workspace / "stop").touch()


async def test_a_step_a_stop_of_the_daemon_cuts_keeps_the_results_of_the_calls_that_ended(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            step: ScriptedTurn = ScriptedTurn(text="", calls=(WRITE, SHELL))
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [step]))
            manager: RunManager = RunManager(runtime)
            try:
                async with runtime.bus.subscribe(None) as subscription:
                    run: RunRecord = await manager.start(
                        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                    )
                    async with asyncio.timeout(_TIME_LIMIT_S):
                        await _call_starts(subscription, run.id, SHELL.tool)
            finally:
                # The daemon's way out cuts the run while the command runs, after the write has ended.
                await manager.shutdown()
                await asyncio.to_thread(_let_the_command_end, runtime.workspace)
            cut: RunRecord = await runtime.store.get_run(run.id)
            messages: list[Message] = await runtime.store.load_messages(cut.thread_id, 0)
            left: tuple[InterruptedCall, ...] = await interrupted_calls(runtime, cut)
        finally:
            await close_runtime(runtime)
    assert (cut.status, cut.result) == ("cancelled", RESULT_SHUTDOWN)
    results: dict[str | None, str] = {
        message.tool_name: message.content for message in messages if message.role == "tool"
    }
    # The write had ended: its result is in the thread, as a run that goes on there reads it.
    assert list(results) == [WRITE.tool]
    assert results[WRITE.tool].startswith("Wrote 8 bytes (1 lines) to ")
    # Only the command was cut, so only it is left for a resumed run to look at.
    assert [call.tool for call in left] == [SHELL.tool]


def _reflecting_config() -> AppConfig:
    """An eval configuration with the scripted model as the review model too, which describes the owner at night."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"models": config.models.model_copy(update={"review": SCRIPTED_MODEL})})


async def test_a_stop_of_the_daemon_does_not_wait_for_a_reflection_under_way(login_env: dict[str, str]) -> None:
    model: Hanging = Hanging(ScriptedProvider(_PROVIDER, [FINISH]), 1)
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_reflecting_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            scheduler: Scheduler = Scheduler(runtime, manager)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    await manager.wait(run.id)
                fire_at: float | None = next_firing(
                    reflection_cron(runtime.config.agent.reflection_time), time.time(), runtime.schedule_zone
                )
                assert fire_at is not None
                await scheduler.tick(fire_at + 1)
                async with asyncio.timeout(_TIME_LIMIT_S):
                    # The reflection reads the run and asks the review model to describe the owner, which hangs.
                    await model.hanging.wait()
            finally:
                # The daemon's way out (server/daemon.py): the scheduler stops, the runs end, the scheduler drains.
                started: float = time.monotonic()
                await scheduler.stop()
                await manager.shutdown()
                async with asyncio.timeout(_STOP_LIMIT_S):
                    await scheduler.drain()
                seconds: float = time.monotonic() - started
            reflection: Reflection | None = await last_reflection(runtime.store)
        finally:
            await close_runtime(runtime)
    assert seconds < _STOP_LIMIT_S
    # Nothing of it was recorded, so the next reflection reads the same runs again.
    assert reflection is None
