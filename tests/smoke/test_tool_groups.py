"""Tool groups in the real loop: a run whose tools are grouped starts with the core tools; a call of a tool whose group
is off does not run but turns the group on, and the next step offers the group's tools; use_tools turns a group on;
the next run of the thread starts with the groups the thread used. A run that offers every tool has no groups and no
use_tools.

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio
from collections.abc import Sequence

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, Tier, ToolSurface, load_default_config
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, ToolSpec
from dotagent.runtime import Runtime, _tool_groups, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.campaign import CAMPAIGN_TOOLS
from dotagent.tools.groups import group_of
from dotagent.tools.meta import FINISH_TOOL, USE_TOOLS_TOOL
from dotagent.tools.registry import build_registry, specs_for_mode
from dotagent.tools.schedule import build_schedule_tools
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Tell me which goals are scheduled to run later."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_LIST: ScriptedCall = ScriptedCall(tool="schedule_list", arguments={})
_SCHEDULE_TOOLS: tuple[str, ...] = ("schedule_create", "schedule_list", "schedule_delete")


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class Played(FrozenModel):
    """A run that is over: its record, the requests of its steps, its tool_groups events and its tool results."""

    record: RunRecord
    steps: tuple[ChatRequest, ...]
    groups: tuple[JsonObject, ...]
    results: tuple[JsonObject, ...]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


def _config(surface: ToolSurface, tiers: dict[str, Tier]) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={"agent": config.agent.model_copy(update={"tool_surface": surface, "tiers": tiers})}
    )


def _kind(events: Sequence[EventRecord], kind: str) -> tuple[JsonObject, ...]:
    return tuple(event.data for event in events if event.kind == kind)


async def _play(
    config: AppConfig, login_env: dict[str, str], runs: Sequence[Sequence[ScriptedTurn]]
) -> tuple[Played, ...]:
    """Each of ``runs`` as an unattended run of ``GOAL`` in one thread, one run after the other, in a throw-away home."""
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(_PROVIDER, [turn for turns in runs for turn in turns])
    )
    played: list[Played] = []
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                thread_id: str | None = None
                for _ in runs:
                    asked: int = len(model.requests)
                    run: RunRecord = await manager.start(
                        GOAL, "task", SCRIPTED_MODEL, thread_id, EVAL_SOURCE, None, None, "ask", False, ()
                    )
                    async with asyncio.timeout(_TIME_LIMIT_S):
                        record: RunRecord = await manager.wait(run.id)
                    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
                    played.append(
                        Played(
                            record=record,
                            steps=tuple(
                                request for request in model.requests[asked:] if request.session_key == record.thread_id
                            ),
                            groups=_kind(events, "tool_groups"),
                            results=_kind(events, "tool_result"),
                        )
                    )
                    thread_id = record.thread_id
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    return tuple(played)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _names(request: ChatRequest) -> list[str]:
    return [spec.name for spec in request.tools]


def _use(group: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=USE_TOOLS_TOOL, arguments={"group": group}))


DONE: ScriptedTurn = _turn(
    ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "No goal is scheduled to run later.", "success": True})
)


async def test_a_call_of_a_tool_whose_group_is_off_turns_the_group_on_and_the_next_step_offers_its_tools(
    login_env: dict[str, str],
) -> None:
    (played,) = await _play(
        _config("grouped", {}), login_env, [[_turn(_LIST), _turn(_LIST), _use("memory_admin"), DONE]]
    )
    assert played.record.status == "done"
    offered: list[list[str]] = [_names(step) for step in played.steps]
    assert len(offered) == 4
    # The run starts with the core tools only, use_tools among them.
    assert USE_TOOLS_TOOL in offered[0]
    assert {group_of(name) for name in offered[0]} == {"core"}
    # The call of a tool whose group was off did not run; its result says so, and the next step offers the group with
    # the same definitions as a run that offers every tool.
    assert played.results[0]["tool"] == "schedule_list"
    assert (played.results[0]["ok"], played.results[0]["risk"]) == (False, "safe")
    assert played.results[0]["summary"] == "Turn on the tool group schedule"
    assert played.steps[1].messages[-1].content == (
        "schedule_list did not run: it is in the tool group schedule, which was off. The group is on now: call "
        "schedule_list again."
    )
    assert [name for name in offered[1] if name not in offered[0]] == list(_SCHEDULE_TOOLS)
    definitions: dict[str, ToolSpec] = {spec.name: spec for spec in played.steps[1].tools}
    assert [definitions[tool.spec.name] for tool in build_schedule_tools()] == [
        tool.spec for tool in build_schedule_tools()
    ]
    assert played.results[1]["tool"] == "schedule_list"
    assert played.results[1]["ok"] is True
    # use_tools turns a group on as well; the groups that are on stay on.
    assert played.results[2]["ok"] is True
    assert played.results[2]["text"].startswith("The tool group memory_admin is on: memory_update, memory_forget.")
    assert offered[2] == offered[1]
    assert [name for name in offered[3] if name not in offered[2]] == ["memory_update", "memory_forget"]
    # Turning a group on changes the tool list only: the system prompt, which lists the groups, stays as it was.
    prompts: set[str] = {step.messages[0].content for step in played.steps}
    assert len(prompts) == 1
    assert "\n# Tool groups\n" in played.steps[0].messages[0].content
    assert "- schedule: goals that run later, once or on a cron expression (schedule_create, schedule_list, " in (
        played.steps[0].messages[0].content
    )
    off: list[str] = ["browser", "memory_admin", "schedule"]
    assert played.groups == (
        {"step": 0, "on": ["core"], "off": off, "turned_on": [], "reason": '[agent] tool_surface is "grouped"'},
        {
            "step": 1,
            "on": ["core", "schedule"],
            "off": ["browser", "memory_admin"],
            "turned_on": ["schedule"],
            "reason": "",
        },
        {
            "step": 3,
            "on": ["core", "memory_admin", "schedule"],
            "off": ["browser"],
            "turned_on": ["memory_admin"],
            "reason": "",
        },
    )


async def test_the_next_run_of_a_thread_starts_with_the_groups_the_thread_used(login_env: dict[str, str]) -> None:
    # "auto" groups the tools of a model of the weak tier.
    first, second = await _play(
        _config("auto", {SCRIPTED_MODEL: "weak"}), login_env, [[_turn(_LIST), _turn(_LIST), DONE], [DONE]]
    )
    assert (first.record.status, second.record.status) == ("done", "done")
    assert second.record.thread_id == first.record.thread_id
    reason: str = '[agent] tool_surface is "auto" and the model is in the weak tier'
    assert (first.groups[0]["on"], first.groups[0]["reason"]) == (["core"], reason)
    assert second.groups == (
        {"step": 0, "on": ["core", "schedule"], "off": ["browser", "memory_admin"], "turned_on": [], "reason": reason},
    )
    assert set(_SCHEDULE_TOOLS) <= set(_names(second.steps[0]))
    assert not any(name.startswith("browser_") for name in _names(second.steps[0]))


async def test_a_run_that_offers_every_tool_has_no_groups_and_answers_use_tools_as_unknown(
    login_env: dict[str, str],
) -> None:
    config: AppConfig = _config("full", {})
    (played,) = await _play(config, login_env, [[_use("schedule"), _turn(_LIST), DONE]])
    assert played.record.status == "done"
    assert played.groups == ()
    # Every tool of the mode at every step, as before there were groups (skill is left out: no skill is installed; so
    # are the campaign tools, which only a session of a campaign has).
    every: list[str] = [
        spec.name
        for spec in specs_for_mode(build_registry(_tool_groups(config)), "task")
        if spec.name not in {USE_TOOLS_TOOL, "skill", *CAMPAIGN_TOOLS}
    ]
    assert all(_names(step) == every for step in played.steps)
    assert all("# Tool groups" not in step.messages[0].content for step in played.steps)
    assert played.results[0]["ok"] is False
    assert played.results[0]["text"].startswith("Unknown tool 'use_tools'. Available tools: ")
    assert played.results[1]["ok"] is True
