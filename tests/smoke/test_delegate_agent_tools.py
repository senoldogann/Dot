"""A helper of an agent's run keeps the agent's tools (item 553), in the real loop and store with a scripted model: the
owner bounds an agent to a few tools, and the run it delegates a part to is bound as its parent is, instead of getting
every tool of its mode; it carries the agent's id, its prompt holds the agent's instructions, and it stays a delegation
(its source, no slot of its own)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import NO_PICKS, AgentRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 60.0
GOAL: str = "Look at notes.txt and tell me what it says."
INSTRUCTIONS: str = "Only read; never act on the Mac."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# What the agent is bound to, and what a bound run has besides: finishing, asking the owner, turning tool groups on.
_BOUND: frozenset[str] = frozenset({"read_file", "delegate"})
# What every agent keeps (agent/loop.py's AGENT_CORE_TOOLS), ask_secret among them since item 857.
_CORE: frozenset[str] = frozenset({"finish", "ask_user", "ask_secret", "use_tools", "react"})


def _call(tool: str, **arguments: str | bool) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


class _Recording(ScriptedProvider):
    """The scripted model, keeping each request it was asked: the parent's first, then the helper's."""

    def __init__(self, name: str, turns: list[ScriptedTurn]) -> None:
        super().__init__(name, turns)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await super().complete(request, sink)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _session(login_env: dict[str, str], model: _Recording) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _offered(request: ChatRequest) -> frozenset[str]:
    return frozenset(spec.name for spec in request.tools)


async def test_the_helper_of_an_agents_run_has_the_tools_the_agent_is_bound_to_and_no_others(
    login_env: dict[str, str],
) -> None:
    model: _Recording = _Recording(
        _PROVIDER,
        [
            _call("delegate", goal="Read notes.txt and report the exact text."),
            _call("finish", answer="It says: hello.", success=True),
            _call("finish", answer="The notes say hello.", success=True),
        ],
    )
    async with _session(login_env, model) as (runtime, manager):
        agent: AgentRecord = await runtime.store.create_agent(
            "Reviewer", "Reads and reports", INSTRUCTIONS, "", None, None, sorted(_BOUND), None, None
        )
        run: RunRecord = await manager.start_for_agent(
            agent, GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, "standard", None, "yolo", False, (), NO_PICKS
        )
        async with asyncio.timeout(WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
        assert ended.status == "done", ended
        parent_request, helper_request = model.requests[0], model.requests[1]
        # The agent's own run is bound, and so is the run it delegated.
        assert _offered(parent_request) <= _BOUND | _CORE and {"read_file", "delegate"} <= _offered(parent_request)
        assert "read_file" in _offered(helper_request)
        assert _offered(helper_request) <= _BOUND | _CORE, sorted(_offered(helper_request) - _BOUND - _CORE)
        # A helper was sent no message of the owner's: it has nothing to react to (item 664).
        assert "react" in _offered(parent_request) and "react" not in _offered(helper_request)
        assert "shell" not in _offered(helper_request) and "write_file" not in _offered(helper_request)
        # It works as the agent's helper: the agent's id and instructions, a delegation's source.
        helpers: list[RunRecord] = [
            other for other in await runtime.store.list_runs(100, None) if other.parent_run_id == run.id
        ]
        assert [(helper.agent_id, helper.source) for helper in helpers] == [(agent.id, "delegate")]
        assert INSTRUCTIONS in helper_request.messages[0].content
