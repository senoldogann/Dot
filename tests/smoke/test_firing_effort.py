"""A schedule's firing whose saved reasoning level its model does not offer runs at the nearest one it does (item
882): on the owner's Mac schedules made in a chat kept that chat's ``medium``, which DeepSeek does not offer (low, high,
max), and every firing failed to start with "the model does not offer that reasoning effort". The level is the one rule
of ``llm/traits.py`` that a run's own calls use (the lower of two as near, item 1307), and the schedule keeps it, with
one line on the run's timeline and none in the log at the firings after."""

import asyncio
import time

import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.helpers import SCHEDULE_EFFORT_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import Effort, ModelInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.scheduler.runner import Scheduler
from dotagent.server import UI_SOURCE
from dotagent.store.models import RunRecord, ScheduleOptions, ScheduleRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 60.0


class _DeepSeekLevels(ScriptedProvider):
    """The scripted model, offering the levels DeepSeek does: low, high and max."""

    async def model_info(self, model: str) -> ModelInfo:
        info: ModelInfo = await super().model_info(model)
        return info.model_copy(update={"efforts": ("low", "high", "max")})


class _LevelsByModel(ScriptedProvider):
    """The scripted provider, whose models offer the levels ``offered`` names (any other: none, no choice)."""

    def __init__(self, name: str, turns: list[ScriptedTurn], offered: dict[str, tuple[Effort, ...]]) -> None:
        super().__init__(name, turns)
        self._offered: dict[str, tuple[Effort, ...]] = offered

    async def model_info(self, model: str) -> ModelInfo:
        info: ModelInfo = await super().model_info(model)
        return info.model_copy(update={"efforts": self._offered.get(model, ())})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def test_the_nearest_level_is_the_lower_of_two_as_near_and_none_without_a_choice() -> None:
    assert effective_effort(("low", "high", "max"), "medium") == "low"
    assert effective_effort(("low", "high", "max"), "xhigh") == "high"
    assert effective_effort(("low", "high", "max"), "max") == "max"
    assert effective_effort(("low", "high"), "minimal") == "low"
    assert effective_effort(("high",), "medium") is None
    assert effective_effort((), "medium") is None


def test_a_plain_switch_takes_the_level_nearest_its_meaning() -> None:
    switch: tuple[Effort, ...] = ("off", "on")
    assert effective_effort(switch, "medium") == "on"
    assert effective_effort(switch, "high") == "on"
    assert effective_effort(switch, "max") == "on"
    assert effective_effort(switch, "low") == "on"
    assert effective_effort(switch, "minimal") == "off"
    assert effective_effort(switch, "off") == "off"
    assert effective_effort(switch, "on") == "on"


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
            assert run.effort == "low"
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


async def test_a_schedule_whose_level_its_model_does_not_offer_keeps_the_level_it_ran_at_and_says_so_once(
    login_env: dict[str, str],
) -> None:
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Checked.", "success": True}),)
    )
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        await runtime.router.register(_PROVIDER, _DeepSeekLevels(_PROVIDER, [finish, finish, finish]))
        manager: RunManager = RunManager(runtime)
        scheduler: Scheduler = Scheduler(runtime, manager)
        try:

            async def schedule(name: str, effort: Effort) -> ScheduleRecord:
                options: ScheduleOptions = UNSET_OPTIONS.model_copy(update={"model": SCRIPTED_MODEL, "effort": effort})
                request: ScheduleRequest = ScheduleRequest(
                    name=name, goal="Check the mail.", cron="0 9 * * *", at=None, mode="task", options=options
                )
                return await create_schedule(
                    runtime.store, runtime.config.scheduler, runtime.schedule_zone, request, time.time(), None
                )

            async def fire(record: ScheduleRecord) -> tuple[RunRecord, list[str]]:
                with capture_logs() as logged:
                    run: RunRecord = await scheduler.fire_by_hand(record.id, time.time())
                    async with asyncio.timeout(_WAIT_S):
                        await manager.wait(run.id)
                        await scheduler.drain()
                return run, [entry["event"] for entry in logged]

            wrong: ScheduleRecord = await schedule("medium on deepseek", "medium")
            first, first_log = await fire(wrong)
            assert first.effort == "low"
            assert (await runtime.store.get_schedule(wrong.id)).options.effort == "low"
            told = await runtime.store.events_of_kind(first.id, SCHEDULE_EFFORT_EVENT, 10)
            assert [(event.data["asked"], event.data["kept"]) for event in told] == [("medium", "low")]
            assert first_log.count("schedule_effort_corrected") == 1
            second, second_log = await fire(wrong)
            assert second.effort == "low"
            assert await runtime.store.events_of_kind(second.id, SCHEDULE_EFFORT_EVENT, 10) == []
            assert "schedule_effort_corrected" not in second_log and "firing_effort_not_offered" not in second_log
            # The normal case: a level the model offers runs as it was, nothing written, nothing said.
            fine: ScheduleRecord = await schedule("high on deepseek", "high")
            third, third_log = await fire(fine)
            assert third.effort == "high"
            assert (await runtime.store.get_schedule(fine.id)).options.effort == "high"
            assert await runtime.store.events_of_kind(third.id, SCHEDULE_EFFORT_EVENT, 10) == []
            assert "schedule_effort_corrected" not in third_log
        finally:
            await scheduler.stop()
            await manager.shutdown()
            await close_runtime(runtime)


async def test_an_agents_schedule_is_judged_against_the_agents_model_not_the_main_one(
    login_env: dict[str, str],
) -> None:
    """The main model offers low, high and max and the agent's model low, medium and high (item 1307, review of the
    branch): a schedule of that agent at ``medium`` runs at ``medium`` and is not rewritten to the main model's ``low``;
    with a main model of no choice it keeps its level and is not cleared; a level the agent's model lacks is corrected
    against the agent's model, and the timeline names that model."""
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Checked.", "success": True}),)
    )
    agent_model: str = f"{_PROVIDER}:agents-own"
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        offered: dict[str, tuple[Effort, ...]] = {
            "scripted": ("low", "high", "max"),
            "agents-own": ("low", "medium", "high"),
        }
        provider: _LevelsByModel = _LevelsByModel(_PROVIDER, [finish] * 4, offered)
        await runtime.router.register(_PROVIDER, provider)
        manager: RunManager = RunManager(runtime)
        scheduler: Scheduler = Scheduler(runtime, manager)
        try:
            agent = await runtime.store.create_agent(
                "Mailer", "Reads the mail", "", agent_model, "low", None, (), None, None
            )

            async def schedule(name: str, effort: Effort) -> ScheduleRecord:
                options: ScheduleOptions = UNSET_OPTIONS.model_copy(update={"agent_id": agent.id, "effort": effort})
                request: ScheduleRequest = ScheduleRequest(
                    name=name, goal="Check the mail.", cron="0 9 * * *", at=None, mode="task", options=options
                )
                return await create_schedule(
                    runtime.store, runtime.config.scheduler, runtime.schedule_zone, request, time.time(), None
                )

            async def fire(record: ScheduleRecord) -> RunRecord:
                run: RunRecord = await scheduler.fire_by_hand(record.id, time.time())
                async with asyncio.timeout(_WAIT_S):
                    await manager.wait(run.id)
                    await scheduler.drain()
                return run

            # The main model does not offer medium; the agent's does: nothing is rewritten, nothing said.
            medium: ScheduleRecord = await schedule("agent at medium", "medium")
            run: RunRecord = await fire(medium)
            assert run.effort == "medium"
            assert (await runtime.store.get_schedule(medium.id)).options.effort == "medium"
            assert await runtime.store.events_of_kind(run.id, SCHEDULE_EFFORT_EVENT, 10) == []
            # A level the agent's model lacks is corrected against that model, which the timeline names.
            max_: ScheduleRecord = await schedule("agent at max", "max")
            run = await fire(max_)
            assert run.effort == "high"
            assert (await runtime.store.get_schedule(max_.id)).options.effort == "high"
            told = await runtime.store.events_of_kind(run.id, SCHEDULE_EFFORT_EVENT, 10)
            assert [(e.data["asked"], e.data["kept"], e.data["model"]) for e in told] == [("max", "high", agent_model)]
            # A main model with no choice does not clear the level of an agent whose model has one.
            offered["scripted"] = ()
            run = await fire(await schedule("agent at high", "high"))
            assert run.effort == "high"
        finally:
            await scheduler.stop()
            await manager.shutdown()
            await close_runtime(runtime)
