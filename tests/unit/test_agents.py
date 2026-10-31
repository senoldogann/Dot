"""Agents (item 86): the store keeps them and their runs, the prompt opens in their voice, and the loop keeps their
tools to the ones they name."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.prompt import AgentBrief, build_system_prompt, collect_prompt_inputs
from dotagent.core.errors import InvalidValueError
from dotagent.harness.components import NO_HARNESS
from dotagent.server.routes_agents import changed_agent_tools, new_agent_tools
from dotagent.server.views import AgentRequest
from dotagent.store.db import Store
from dotagent.store.models import AgentLook, AgentRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import ASK_SECRET_TOOL, ASK_USER_TOOL, FINISH_TOOL, REACT_TOOL, USE_TOOLS_TOOL


def _prompt(agent: AgentBrief | None) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            "ui",
            "aggressive",
            "ask",
            "wait",
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            [],
            [],
            0,
            None,
            [],
            agent,
            (),
            (),
            None,
            "full",
            NO_HARNESS,
        )
    )


def test_the_prompt_opens_in_the_agents_voice_and_carries_the_owners_instructions() -> None:
    agent: AgentBrief = AgentBrief(
        name="Scout",
        role="Finds and checks sources",
        instructions="Prefer primary sources.",
        made_by_dot=False,
        team="",
    )
    prompt: str = _prompt(agent)
    assert prompt.startswith("You are Scout, an agent the owner made: Finds and checks sources.")
    assert (
        "The owner's instructions to you, which you follow within the rules below:\nPrefer primary sources." in prompt
    )
    assert "You are Scout" not in _prompt(None)
    # Item 307: an agent a run made carries Dot's instructions, which a model wrote, not the owner's.
    made: str = _prompt(agent.model_copy(update={"made_by_dot": True}))
    assert made.startswith("You are Scout, an agent Dot made: Finds and checks sources.")
    assert "Dot's instructions to you, which you follow within the rules below:\nPrefer primary sources." in made
    assert "The owner's instructions" not in made


def test_the_prompt_says_what_the_agent_never_does_after_its_instructions() -> None:
    agent: AgentBrief = AgentBrief(
        name="Prospector",
        role="Finds prospects",
        instructions="Ten a day.",
        made_by_dot=False,
        team="",
        refuses="- Work inbound leads\n\n• Send a message itself\n",
    )
    prompt: str = _prompt(agent)
    never: int = prompt.index("What you never do, whoever asks")
    assert prompt.index("Ten a day.") < never
    assert "\n- Work inbound leads\n- Send a message itself" in prompt
    assert "What you never do" not in _prompt(agent.model_copy(update={"refuses": "  \n"}))


def test_the_tools_an_agent_always_keeps_are_the_ones_that_end_ask_react_and_turn_groups_on() -> None:
    # An agent reacts to the owner's message as Dot does (item 664).
    # And asks the owner for a secret on its card (item 857), as it asks them anything.
    assert set(AGENT_CORE_TOOLS) == {FINISH_TOOL, ASK_USER_TOOL, ASK_SECRET_TOOL, USE_TOOLS_TOOL, REACT_TOOL}


async def test_the_store_keeps_agents_and_their_runs_and_a_deleted_agents_runs_become_dots(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        agent: AgentRecord = await store.create_agent(
            "Scout", "Finds sources", "Be brief.", "", None, None, ["shell"], None, None
        )
        assert agent.look is None
        assert await store.list_agents() == [agent]
        assert await store.latest_agent_runs() == {}
        thread: ThreadRecord = await store.create_thread("t", "ui")
        first: RunRecord = await store.create_run(
            thread.id,
            "first",
            "task",
            "ollama:m",
            None,
            "ui",
            None,
            "standard",
            None,
            "ask",
            False,
            None,
            agent.id,
            None,
        )
        second: RunRecord = await store.create_run(
            thread.id,
            "second",
            "task",
            "ollama:m",
            None,
            "ui",
            None,
            "standard",
            None,
            "ask",
            False,
            None,
            agent.id,
            None,
        )
        dots: RunRecord = await store.create_run(
            thread.id, "dot", "task", "ollama:m", None, "ui", None, "standard", None, "ask", False, None, None, None
        )
        assert dots.agent_id is None
        assert [run.id for run in await store.agent_runs(agent.id, 10)] == [second.id, first.id]
        assert [run.id for run in await store.agent_runs(agent.id, 1)] == [second.id]
        assert {key: run.id for key, run in (await store.latest_agent_runs()).items()} == {agent.id: second.id}
        look: AgentLook = AgentLook(shape="droplet", expression="curious", colour="violet")
        changed: AgentRecord = await store.update_agent(
            agent.id, "Scout", "Checks sources", "Be brief.", "ollama:m", "high", "yolo", ["shell", "fetch"], look
        )
        assert changed.look == look
        assert changed.id == agent.id
        assert (changed.role, changed.model, changed.effort, changed.approvals) == (
            "Checks sources",
            "ollama:m",
            "high",
            "yolo",
        )
        assert changed.tools == ("shell", "fetch")
        assert changed.updated_at >= agent.updated_at
        assert await store.get_agent(agent.id) == changed
        # The look alone (item 102): an agent's in its columns, Dot's in the kv store.
        other: AgentLook = AgentLook(shape="cloud", expression="sleepy", colour="teal")
        assert (await store.set_agent_look(agent.id, other)).look == other
        assert await store.dot_look() is None
        await store.set_dot_look(look)
        assert await store.dot_look() == look
        await store.delete_agent(agent.id)
        assert await store.list_agents() == []
        assert (await store.get_run(first.id)).agent_id is None
        assert (await store.get_run(second.id)).agent_id is None
    finally:
        await store.close()


async def test_dots_newest_run_is_its_own_chats_and_not_an_agents_a_helpers_a_teams_or_a_campaigns(
    tmp_path: Path,
) -> None:
    """Item 633: Dot's card on the Agents page said "No chats yet" while the owner chatted with it every day."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")

    async def run(thread: ThreadRecord, parent: str | None, campaign: str | None, agent: str | None) -> RunRecord:
        return await store.create_run(
            thread.id,
            "goal",
            "task",
            "ollama:m",
            parent,
            "ui",
            None,
            "standard",
            None,
            "ask",
            False,
            campaign,
            agent,
            None,
        )

    try:
        assert await store.latest_dot_run() is None
        chat: ThreadRecord = await store.create_thread("With Dot", "ui")
        dots: RunRecord = await run(chat, None, None, None)
        assert await store.latest_dot_run() == dots
        # Runs after it that are not Dot's in a chat of its own leave it the newest.
        agent: AgentRecord = await store.create_agent("Scout", "", "", "", None, None, [], None, None)
        await run(await store.create_thread("With Scout", "ui"), None, None, agent.id)
        await run(chat, dots.id, None, None)
        team = await store.create_team("Crew", "", [agent.id], None)
        await run(await store.create_thread("With the crew", "ui", team.id), None, None, None)
        campaign = await store.create_campaign(
            "Long", "goal", chat.id, "ollama:m", "medium", "standard", None, "ask", [], None
        )
        await run(await store.create_thread("A session", "campaign"), None, campaign.id, None)
        assert await store.latest_dot_run() == dots
        # A newer one is the newest until a rewind takes it out of its chat.
        later: RunRecord = await run(await store.create_thread("Again", "ui"), None, None, None)
        assert await store.latest_dot_run() == later
        await store._db.execute("UPDATE runs SET rewound_at = ? WHERE id = ?", (later.created_at + 1.0, later.id))
        await store._db.commit()
        assert await store.latest_dot_run() == dots
        # A chat is whose its latest run is, as the chat list says: an agent's run after Dot's in the same chat makes it
        # the agent's, and Dot's card names the chat before it.
        mixed: ThreadRecord = await store.create_thread("Handed over", "ui")
        handed: RunRecord = await run(mixed, None, None, None)
        assert await store.latest_dot_run() == handed
        await run(mixed, None, None, agent.id)
        assert await store.latest_dot_run() == dots
    finally:
        await store.close()


def test_a_request_with_a_look_outside_the_catalogue_is_refused() -> None:
    body: dict[str, object] = {
        "name": "Scout",
        "role": "",
        "instructions": "",
        "model": "",
        "effort": None,
        "approvals": None,
        "tools": None,
    }
    assert AgentRequest.model_validate({**body, "look": None}).look is None
    chosen: AgentRequest = AgentRequest.model_validate(
        {**body, "look": {"shape": "pebble", "expression": "proud", "colour": "pink"}}
    )
    assert chosen.look == AgentLook(shape="pebble", expression="proud", colour="pink")
    with pytest.raises(ValidationError):
        AgentRequest.model_validate({**body, "look": {"shape": "blob", "expression": "proud", "colour": "pink"}})
    with pytest.raises(ValidationError):
        AgentRequest.model_validate({**body, "look": {"shape": "pebble", "expression": "proud"}})


def test_tools_left_out_or_null_are_every_tool_and_an_empty_list_keeps_an_agents_own() -> None:
    """The code review's Y5: an editor whose tool list had not loaded sent none, which the server took for every tool,
    so a Save at that moment let a bounded agent use every tool."""
    body: dict[str, object] = {
        "name": "Scout",
        "role": "",
        "instructions": "",
        "model": "",
        "effort": None,
        "approvals": None,
    }
    assert AgentRequest.model_validate(body).tools is None
    assert AgentRequest.model_validate({**body, "tools": None}).tools is None
    assert AgentRequest.model_validate({**body, "tools": []}).tools == []
    with pytest.raises(ValidationError):
        AgentRequest.model_validate({**body, "tools": [], "every_tool": True})
    assert new_agent_tools(None) == ()
    assert new_agent_tools(["shell"]) == ("shell",)
    with pytest.raises(InvalidValueError, match="no actions to keep"):
        new_agent_tools([])
    bounded: tuple[str, ...] = ("shell", "skill")
    assert changed_agent_tools([], bounded) == bounded
    assert changed_agent_tools(["shell"], bounded) == ("shell",)
    assert changed_agent_tools(None, bounded) == ()
