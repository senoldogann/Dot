"""Fan-out (item 927): the independent steps of a plan with a brief go to helpers at once, by the code. A real run in
the loop on the scripted model: its plan has two such steps and one of its own; the two run as helpers of the run, come
back marked done with their reports, and the run's own step stays its own. One ready step alone is the run's to do. No
model is called and nothing leaves the Mac."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.plan import PLAN_EVENT, PlanItem, PlanPayload, plan_from_event
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import AgentRecord, EventRecord, RunPicks, RunRecord
from dotagent.tools.fan_out import ready_steps
from dotagent.tools.meta import FINISH_TOOL, UPDATE_PLAN_TOOL

pytestmark = pytest.mark.asyncio

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


def _plan(steps: list[dict[str, object]]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=UPDATE_PLAN_TOOL, arguments={"steps": steps}),))


async def test_the_ready_steps_are_pending_with_a_brief_and_wait_for_nothing_open() -> None:
    steps: tuple[PlanItem, ...] = (
        PlanItem(text="a", status="pending"),
        PlanItem(text="b", status="pending", depends_on=(1,)),
        PlanItem(text="c", status="done", evidence="seen"),
        PlanItem(text="d", status="pending", depends_on=(3,)),
        PlanItem(text="e", status="in_progress"),
    )
    assert ready_steps(steps, {0: "x", 1: "x", 2: "x", 3: "x", 4: "x"}) == [0, 3]
    assert ready_steps(steps, {1: "x"}) == []


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\ngoal_mode = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _last_plan(runtime: Runtime, run_id: str) -> PlanPayload:
    event: EventRecord | None = await runtime.store.last_event(run_id, PLAN_EVENT)
    assert event is not None
    return plan_from_event(event)


async def test_two_independent_steps_with_a_brief_run_at_once_as_helpers_and_come_back_marked(
    runtime: Runtime,
) -> None:
    steps: list[dict[str, object]] = [
        {
            "text": "Count the lines of a.txt",
            "status": "pending",
            "brief": "Count the lines of a.txt; answer the number.",
        },
        {
            "text": "Count the lines of b.txt",
            "status": "pending",
            "brief": "Count the lines of b.txt; answer the number.",
        },
        {"text": "Add the two counts", "status": "pending", "depends_on": [1, 2]},
    ]
    # The plan's own step stays open, so the run's first finish is sent back once (an open plan), the second ends it.
    turns: list[ScriptedTurn] = [
        _plan(steps),
        _finish("12 lines."),
        _finish("30 lines."),
        _finish("42 in all."),
        _finish("42 in all."),
    ]
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Count the lines of a.txt and b.txt", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None,
            "yolo", False, (),
        )  # fmt: skip
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
        assert ended.status == "done", ended.result
        helpers: list[RunRecord] = [
            each for each in await runtime.store.list_runs(20, None) if each.parent_run_id == run.id
        ]
        assert sorted(helper.goal for helper in helpers) == [
            "Count the lines of a.txt; answer the number.",
            "Count the lines of b.txt; answer the number.",
        ]
        plan: PlanPayload = await _last_plan(runtime, run.id)
        assert [step.status for step in plan.steps] == ["done", "done", "pending"]
        assert all("Report:" in step.evidence for step in plan.steps[:2])
        # The plan's call carries its own limit, by which the watchdog reads the run as busy while its helpers work.
        [call] = [
            event
            for event in await runtime.store.events_of_kinds(run.id, ("tool_call",), 20)
            if event.data.get("tool") == UPDATE_PLAN_TOOL
        ]
        assert call.data["timeout_s"] == 4 * 3600.0
    finally:
        await manager.shutdown()


async def test_one_ready_step_alone_stays_the_runs_own(runtime: Runtime) -> None:
    steps: list[dict[str, object]] = [
        {"text": "Count the lines of a.txt", "status": "pending", "brief": "Count the lines of a.txt."},
        {"text": "Say the count", "status": "pending", "depends_on": [1]},
    ]
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_plan(steps), _finish("Done.")]))
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Count the lines of a.txt", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo",
            False, (),
        )  # fmt: skip
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)
        assert [each for each in await runtime.store.list_runs(20, None) if each.parent_run_id == run.id] == []
        assert [step.status for step in (await _last_plan(runtime, run.id)).steps] == ["pending", "pending"]
    finally:
        await manager.shutdown()


async def test_an_agent_whose_role_leaves_delegate_out_starts_no_helper(runtime: Runtime) -> None:
    """The pre-push review of item 927: the plan of an agent whose role does not give it ``delegate`` handed its steps
    to helpers all the same. Such a plan stays the run's own."""
    steps: list[dict[str, object]] = [
        {"text": "Count the lines of a.txt", "status": "pending", "brief": "Count the lines of a.txt."},
        {"text": "Count the lines of b.txt", "status": "pending", "brief": "Count the lines of b.txt."},
    ]
    await runtime.router.register(
        _PROVIDER, ScriptedProvider(_PROVIDER, [_plan(steps), _finish("Done."), _finish("Done.")])
    )
    agent: AgentRecord = await runtime.store.create_agent(
        "Counter", "Counts lines", "", "", None, None, ("read_file", UPDATE_PLAN_TOOL), None, None, ""
    )
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start_for_agent(
            agent, "Count the lines of a.txt and b.txt", "task", SCRIPTED_MODEL, None, "ui", None, "standard", None,
            "yolo", False, (), RunPicks(plugins=(), skills=(), plan_first=False),
        )  # fmt: skip
        assert run.agent_id == agent.id
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)
        assert [each for each in await runtime.store.list_runs(20, None) if each.parent_run_id == run.id] == []
        assert [step.status for step in (await _last_plan(runtime, run.id)).steps] == ["pending", "pending"]
    finally:
        await manager.shutdown()
