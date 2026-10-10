"""A plan with dependencies in the real loop: the independent steps go to delegate in one turn, the delegated runs work
side by side and their reports close the steps with evidence, and the dependent step waits for them.

The runs are scripted: the parent on the stand-in of the ``ollama`` connector, each delegated run on a helper model of
its own provider, so that every run gets its own script whatever order the runs ask in.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.plan import PLAN_EVENT
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatProvider
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Report the prices and the reviews of the product, then write the summary."
PRICES_HELPER: str = "anthropic:scripted-prices"
REVIEWS_HELPER: str = "openai:scripted-reviews"
_STAND_IN_KEY: str = "stand-in-key-of-the-test"
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
# What each delegated run spends before it answers, so that the two runs can only overlap if they run side by side.
_WORK_S: float = 0.5


class Played(FrozenModel):
    """A run that is over, with its events and the runs it delegated to."""

    record: RunRecord
    events: tuple[EventRecord, ...]
    children: tuple[RunRecord, ...]

    def data(self, kind: str) -> tuple[JsonObject, ...]:
        return tuple(event.data for event in self.events if event.kind == kind)

    def results(self, tool: str) -> tuple[JsonObject, ...]:
        return tuple(data for data in self.data("tool_result") if data["tool"] == tool)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


def _config() -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    models: JsonObject = {"review": SCRIPTED_MODEL, "helpers": [PRICES_HELPER, REVIEWS_HELPER]}
    return config.model_copy(update={"models": config.models.model_copy(update=models)})


@asynccontextmanager
async def _session(
    login_env: dict[str, str], providers: Mapping[str, ChatProvider]
) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose providers are ``providers``, and its runs."""
    config: AppConfig = _config()
    keys: dict[str, str] = {
        config.providers.anthropic.api_key_env: _STAND_IN_KEY,
        config.providers.openai.api_key_env: _STAND_IN_KEY,
    }
    with scratch_home() as home, eval_environment(home, keys):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            for name, provider in providers.items():
                await runtime.router.register(name, provider)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _play(runtime: Runtime, manager: RunManager) -> Played:
    run: RunRecord = await manager.start(GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "yolo", False, ())
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await manager.wait(run.id)
    children: tuple[RunRecord, ...] = tuple(
        child for child in await runtime.store.list_runs(100, None) if child.parent_run_id == record.id
    )
    return Played(
        record=record, events=tuple(await runtime.store.list_events(record.id, 0, _MAX_EVENTS)), children=children
    )


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _call(tool: str, arguments: JsonObject) -> ScriptedCall:
    return ScriptedCall(tool=tool, arguments=arguments)


def _finish(answer: str) -> ScriptedTurn:
    return _turn(_call(FINISH_TOOL, {"answer": answer, "success": True}))


def _worker(name: str, report: str) -> ScriptedProvider:
    """A delegated run's model: it works for a moment, then reports."""
    return ScriptedProvider(name, [_turn(_call("shell", {"command": f"sleep {_WORK_S}"})), _finish(report)])


def _plan(steps: Sequence[JsonObject]) -> ScriptedCall:
    return _call(UPDATE_PLAN_TOOL, {"steps": list(steps)})


_PLAN: tuple[JsonObject, ...] = (
    {"text": "Fetch the prices", "status": "pending"},
    {"text": "Fetch the reviews", "status": "pending"},
    {"text": "Write the summary", "status": "pending", "depends_on": [1, 2]},
)


async def test_the_independent_steps_run_side_by_side_and_their_reports_close_them(login_env: dict[str, str]) -> None:
    parent: ScriptedProvider = ScriptedProvider(
        "ollama",
        [
            _turn(
                _plan(_PLAN),
                _call(DELEGATE_TOOL, {"goal": "Fetch the prices and report them.", "model": PRICES_HELPER, "step": 1}),
                _call(
                    DELEGATE_TOOL, {"goal": "Fetch the reviews and report them.", "model": REVIEWS_HELPER, "step": 2}
                ),
            ),
            _turn(
                _call(MARK_PLAN_STEP_TOOL, {"step": 3, "status": "done", "evidence": "summary.md written, 12 lines"}),
                _call(
                    FINISH_TOOL,
                    {"answer": "Prices 12 and 14; reviews 4.2 of 5. The summary is in summary.md.", "success": True},
                ),
            ),
        ],
    )
    providers: dict[str, ChatProvider] = {
        "ollama": parent,
        "anthropic": _worker("anthropic", "Prices: 12 and 14."),
        "openai": _worker("openai", "Reviews: 4.2 of 5 over 30 reviews."),
    }
    async with _session(login_env, providers) as (runtime, manager):
        played: Played = await _play(runtime, manager)
    assert played.record.status == "done", played.record
    # The plan said which steps were ready, and the delegated runs' reports closed them with evidence.
    first: str = str(played.results(UPDATE_PLAN_TOOL)[0]["text"])
    assert "Ready now: steps 1, 2; they are independent, so delegate can run them in one turn." in first
    assert "3. [pending] Write the summary (after 1, 2)" in first
    plans: tuple[JsonObject, ...] = played.data(PLAN_EVENT)
    closed: list[JsonObject] = list(plans[1]["steps"])  # type: ignore[arg-type]
    assert [step["status"] for step in closed] == ["done", "done", "pending"]
    assert str(closed[0]["evidence"]).startswith("Delegated run ") and "Report: Prices: 12 and 14." in str(
        closed[0]["evidence"]
    )
    assert "Report: Reviews: 4.2 of 5 over 30 reviews." in str(closed[1]["evidence"])
    assert [step["status"] for step in plans[-1]["steps"]] == ["done", "done", "done"]  # type: ignore[index]
    results: tuple[JsonObject, ...] = played.results(DELEGATE_TOOL)
    assert [result["ok"] for result in results] == [True, True]
    assert all(
        "Plan step" in str(result["text"]) and "is marked done with this as its evidence." in str(result["text"])
        for result in results
    )
    # The two delegated runs overlapped: each started before the other ended, so the parent waited for the longer one,
    # not for both in a row.
    assert len(played.children) == 2
    assert {child.model for child in played.children} == {PRICES_HELPER, REVIEWS_HELPER}
    finished: list[float] = [child.finished_at for child in played.children if child.finished_at is not None]
    assert len(finished) == 2
    assert max(child.created_at for child in played.children) < min(finished)
    assert all(end - child.created_at >= _WORK_S for child, end in zip(played.children, finished, strict=True))


async def test_a_delegation_for_a_step_the_plan_does_not_have_starts_no_run(login_env: dict[str, str]) -> None:
    parent: ScriptedProvider = ScriptedProvider(
        "ollama",
        [
            _turn(_plan(_PLAN[:1]), _call(DELEGATE_TOOL, {"goal": "Fetch the reviews.", "step": 5})),
            # The open step is closed before the finish, else the loop asks once to close it.
            _turn(
                _call(MARK_PLAN_STEP_TOOL, {"step": 1, "status": "skipped", "evidence": "the delegation was refused"}),
                _call(FINISH_TOOL, {"answer": "Nothing was fetched.", "success": False}),
            ),
        ],
    )
    async with _session(login_env, {"ollama": parent}) as (runtime, manager):
        played: Played = await _play(runtime, manager)
    assert played.record.status == "failed", played.record
    result: JsonObject = played.results(DELEGATE_TOOL)[0]
    assert result["ok"] is False
    assert "the plan has steps 1 to 1; there is no step 5" in str(result["text"])
    assert played.children == ()
    # The refused call closed nothing: the step was still open when the run marked it itself.
    assert [step["status"] for step in played.data(PLAN_EVENT)[0]["steps"]] == ["pending"]  # type: ignore[index]
    assert [step["status"] for step in played.data(PLAN_EVENT)[-1]["steps"]] == ["skipped"]  # type: ignore[index]
