"""Agents (item 86): the store keeps them and their runs, the prompt opens in their voice, and the loop keeps their
tools to the ones they name."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.prompt import AgentBrief, build_system_prompt, collect_prompt_inputs
from dotagent.server.views import AgentRequest
from dotagent.store.db import Store
from dotagent.store.models import AgentLook, AgentRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL, USE_TOOLS_TOOL


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
            [],
            [],
            0,
            None,
            [],
            agent,
            (),
            (),
            None,
        )
    )


def test_the_prompt_opens_in_the_agents_voice_and_carries_the_owners_instructions() -> None:
    agent: AgentBrief = AgentBrief(
        name="Scout", role="Finds and checks sources", instructions="Prefer primary sources."
    )
    prompt: str = _prompt(agent)
    assert prompt.startswith("You are Scout, an agent the owner made: Finds and checks sources.")
    assert "Prefer primary sources." in prompt
    assert "You are Scout" not in _prompt(None)


def test_the_tools_an_agent_always_keeps_are_the_ones_that_end_ask_and_turn_groups_on() -> None:
    assert set(AGENT_CORE_TOOLS) == {FINISH_TOOL, ASK_USER_TOOL, USE_TOOLS_TOOL}


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


def test_a_request_with_a_look_outside_the_catalogue_is_refused() -> None:
    body: dict[str, object] = {
        "name": "Scout",
        "role": "",
        "instructions": "",
        "model": "",
        "effort": None,
        "approvals": None,
        "tools": [],
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
