"""A schedule's firing whose saved reasoning level its model does not offer runs at the nearest one it does (item
882): on the owner's Mac schedules made in a chat kept that chat's ``medium``, which DeepSeek does not offer (low, high,
max), and every firing failed to start with "the model does not offer that reasoning effort"."""

import asyncio

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager, nearest_offered
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ModelInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 60.0


class _DeepSeekLevels(ScriptedProvider):
    """The scripted model, offering the levels DeepSeek does: low, high and max."""

    async def model_info(self, model: str) -> ModelInfo:
        info: ModelInfo = await super().model_info(model)
        return info.model_copy(update={"efforts": ("low", "high", "max")})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def test_the_nearest_level_is_the_higher_of_two_as_near_and_none_without_a_choice() -> None:
    assert nearest_offered("medium", ("low", "high", "max")) == "high"
    assert nearest_offered("xhigh", ("low", "high", "max")) == "max"
    assert nearest_offered("minimal", ("low", "high")) == "low"
    assert nearest_offered("medium", ("high",)) is None
    assert nearest_offered("on", ("low", "high")) is None


async def test_a_firing_at_a_level_its_model_does_not_offer_runs_at_the_nearest_and_a_chats_run_is_refused(
    login_env: dict[str, str],
) -> None:
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Checked.", "success": True}),)
    )
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        await runtime.router.register(_PROVIDER, _DeepSeekLevels(_PROVIDER, [finish]))
        manager: RunManager = RunManager(runtime)
        try:
            thread = await runtime.store.create_thread("Mail check", "schedule")
            run: RunRecord = await manager.start_firing(
                "Check the mail.",
                "Check the mail.",
                "task",
                SCRIPTED_MODEL,
                thread.id,
                "medium",
                "standard",
                "yolo",
                False,
                (),
            )
            assert run.effort == "high"
            async with asyncio.timeout(_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            # A level chosen for a chat's run is still refused at once: the owner sees why.
            with pytest.raises(ConfigError, match="does not offer that reasoning effort"):
                await manager.start(
                    "Check.",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    UI_SOURCE,
                    None,
                    "medium",
                    "standard",
                    None,
                    "yolo",
                    False,
                    (),
                )
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
