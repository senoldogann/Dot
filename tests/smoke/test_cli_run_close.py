"""A ``dotx run`` that works in its own process closes its run manager before its runtime (item 1284): the skill work a
run's end starts in the background, whose question to the review model may still be waiting, is cut while the store
is open, so its cut-off model call is recorded. Closed the other way round, the work was cut once the process ended,
into a closed store, and each such call was logged as ``model_call_not_recorded``.

No model service is used: the scripted model plays the run, and the skill writer's question waits until it is cut.
"""

import asyncio
from collections.abc import Sequence

import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.cli_run import close_here
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, open_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


class WaitingWriter:
    """The scripted model for the run's turns; the call after them, the skill writer's question, waits until it is
    cut, and says so."""

    def __init__(self, script: ScriptedProvider, turns: int) -> None:
        self._script: ScriptedProvider = script
        self._turns: int = turns
        self._served: int = 0
        self.asked: asyncio.Event = asyncio.Event()
        self.cut: bool = False

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._served += 1
        if self._served <= self._turns:
            return await self._script.complete(request, sink)
        self.asked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cut = True
            raise
        raise AssertionError("the skill writer's question was never cut")

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval run's configuration with learned skills on after three acting steps, lessons off, and the scripted
    model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True, "skill_min_steps": 3, "lessons": "off"}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


def _turns() -> Sequence[ScriptedTurn]:
    read: ScriptedCall = ScriptedCall(tool="read_file", arguments={"path": "notes.txt"})
    return [
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool="write_file", arguments={"path": "notes.txt", "content": "hi"}),)
        ),
        ScriptedTurn(text="", calls=(read,)),
        ScriptedTurn(text="", calls=(read,)),
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "It holds hi.", "success": True}),)
        ),
    ]


async def test_closing_a_dotx_run_cuts_the_skill_work_while_its_store_is_open(login_env: dict[str, str]) -> None:
    turns: Sequence[ScriptedTurn] = _turns()
    with scratch_home() as home, eval_environment(home, {}), capture_logs() as logs:
        runtime: Runtime = await open_runtime(_config(), login_env)
        model: WaitingWriter = WaitingWriter(ScriptedProvider(_PROVIDER, turns), len(turns))
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
        try:
            run: RunRecord = await manager.start(
                "Keep hi in notes.txt.", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask",
                False, (),
            )  # fmt: skip
            async with asyncio.timeout(_TIME_LIMIT_S):
                ended: RunRecord = await manager.wait(run.id)
                await model.asked.wait()
        finally:
            await close_here(manager, runtime)
    assert ended.status == "done"
    assert model.cut
    assert [entry for entry in logs if entry["event"] == "model_call_not_recorded"] == []
