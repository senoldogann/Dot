"""A run makes a team of agents (item 864): the owner asked for a team and the run could only make its agents one by
one. On a scripted model and a real store: a run makes two agents, then the team of them and an agent the owner had,
which lists under the owner's teams in that order; a team of an agent nobody made, of one agent, or under a name a team
has already is refused, and nothing is made."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import AgentRecord, EventRecord, RunRecord, TeamRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.team import CREATE_TEAM_TOOL

pytestmark = pytest.mark.asyncio

RUN_WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_TURNS: list[ScriptedTurn] = [
    _call(
        "create_agent",
        name="Press Writer",
        role="Writes the press mails",
        instructions="Short and plain.",
        tools=["read_file"],
    ),
    _call(
        "create_agent",
        name="Launch Planner",
        role="Plans the launch days",
        instructions="One plan a day.",
        tools=["read_file"],
    ),
    _call(
        CREATE_TEAM_TOOL,
        name="Launch team",
        purpose="Gistsmith's launch",
        agents=["press writer", "Launch Planner", "Reviewer"],
    ),
    _call(CREATE_TEAM_TOOL, name="Launch team", agents=["Press Writer", "Reviewer"]),
    _call(CREATE_TEAM_TOOL, name="Ghosts", agents=["Press Writer", "Nobody Made Me"]),
    _call(CREATE_TEAM_TOOL, name="Alone", agents=["Press Writer", "press writer"]),
    _call(FINISH_TOOL, answer="The team is made.", success=True),
]


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _TURNS))
    try:
        yield built
    finally:
        await close_runtime(built)


async def test_a_run_makes_its_agents_then_the_team_of_them(runtime: Runtime) -> None:
    reviewer: AgentRecord = await runtime.store.create_agent(
        "Reviewer", "Reads the work before it goes", "", "", None, None, (), None, None
    )
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Bana bir takım oluştur",
            "task",
            SCRIPTED_MODEL,
            None,
            "ui",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        async with asyncio.timeout(RUN_WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
    finally:
        await manager.shutdown()
    agents: dict[str, AgentRecord] = {agent.name: agent for agent in await runtime.store.list_agents()}
    teams: list[TeamRecord] = await runtime.store.list_teams()
    assert [team.name for team in teams] == ["Launch team"]
    (team,) = teams
    assert team.purpose == "Gistsmith's launch"
    assert team.agents == (agents["Press Writer"].id, agents["Launch Planner"].id, reviewer.id)
    results: list[EventRecord] = [
        event
        for event in await runtime.store.list_events(run.id, 0, 500)
        if event.kind == "tool_result" and event.data.get("tool") == CREATE_TEAM_TOOL
    ]
    said: list[tuple[bool, str]] = [(bool(event.data.get("ok")), str(event.data.get("text", ""))) for event in results]
    assert said[0][0] and "Made the team Launch team: Press Writer, Launch Planner, Reviewer" in said[0][1]
    assert not said[1][0] and "a team has that name already" in said[1][1]
    assert not said[2][0] and "no agent has the name Nobody Made Me" in said[2][1]
    assert not said[3][0] and "a team has two agents at least" in said[3][1]
