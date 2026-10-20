"""The runs of one run manager keep the pace of a site together (item 136, and its wiring in item 145): the gates of its
runs share the runtime's site locks, so two runs under way cannot both take the last public action of the day on a
site.

No network and no model service is used: the scripted model holds both runs at their first step.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, PaceConfig, load_default_config
from dotagent.core.errors import PolicyDenied
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Reply to the thread."
WAIT_S: float = 30.0
# How often the test looks for the question a post raised.
_POLL_S: float = 0.05
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUNS: int = 2
# A public post on a site whose pace allows one a day.
ONE_POST_A_DAY: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=1, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)
POST: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="Post the reply",
    target="https://social.example/thread",
    flags=("public_post",),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


class _HeldModel(ScriptedProvider):
    """The scripted model without a turn, whose every call waits until its run ends: the runs stay at their first step
    while the test uses their gates."""

    def __init__(self, runs: int) -> None:
        super().__init__(_PROVIDER, [])
        self._runs: int = runs
        self._calls: int = 0
        self.all_held: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._calls += 1
        if self._calls == self._runs:
            self.all_held.set()
        never: asyncio.Future[ModelReply] = asyncio.get_running_loop().create_future()
        return await never


@asynccontextmanager
async def _session(login_env: dict[str, str], model: ScriptedProvider) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose model is ``model`` and whose sites allow one public action a day, and its
    runs."""
    config: AppConfig = eval_config(load_default_config())
    paced: AppConfig = config.model_copy(update={"policy": config.policy.model_copy(update={"pace": ONE_POST_A_DAY})})
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(paced, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_two_runs_of_one_manager_cannot_both_take_the_last_public_action_of_the_day_on_a_site(
    login_env: dict[str, str],
) -> None:
    model: _HeldModel = _HeldModel(_RUNS)
    async with _session(login_env, model) as (runtime, manager):
        runs: list[RunRecord] = [
            await manager.start(
                GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
            )
            for _ in range(_RUNS)
        ]
        async with asyncio.timeout(WAIT_S):
            await model.all_held.wait()
        gates: list[RunGate] = [manager._handle(run.id).gate for run in runs]
        # A post waits for the user's yes in yolo too (item 429): the run that takes the last post of the day asks, and
        # the other is refused by the pace without a question.
        tasks: list[asyncio.Task[str]] = [asyncio.create_task(gate.authorize(POST)) for gate in gates]
        pending: list[ApprovalRecord] = []
        async with asyncio.timeout(WAIT_S):
            while not pending:
                pending = await runtime.store.list_approvals("pending", 10)
                await asyncio.sleep(0 if pending else _POLL_S)
        (asked,) = pending
        await manager.decide(asked.id, True, "test", "post it")
        outcomes: list[str | BaseException] = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), WAIT_S
        )
        assert sorted(outcome if isinstance(outcome, str) else type(outcome).__name__ for outcome in outcomes) == [
            PolicyDenied.__name__,
            "post it",
        ]
        assert len(await runtime.store.paced_times("social.example", "public", 0.0)) == 1
