"""A command a run's shell tool starts knows which run started it (item 1310): ``DOT_RUN_ID`` beside ``DOT_RUN_SHELL``,
which ``dotx service restart --when-idle`` hands to its job as the run that asked for the restart."""

import asyncio

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.runenv import RUN_ID_MARKER, RUN_SHELL_MARKER
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def test_a_shell_command_of_a_run_reads_the_id_of_the_run_that_started_it(login_env: dict[str, str]) -> None:
    command: str = f'printf "%s|%s" "${RUN_SHELL_MARKER}" "${RUN_ID_MARKER}"'
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": command}),)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Checked.", "success": True}),)),
    ]
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        manager: RunManager = RunManager(runtime)
        try:
            run: RunRecord = await manager.start(
                "Print the markers.",
                "task",
                SCRIPTED_MODEL,
                None,
                UI_SOURCE,
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            async with asyncio.timeout(_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            results = await runtime.store.events_of_kind(run.id, "tool_result", 10)
            assert any(f"1|{run.id}" in str(event.data) for event in results), [event.data for event in results]
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
