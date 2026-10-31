"""What an agent's runs used (item 874): the store counts the tools its newest ended runs called, the page names
the ones none called once enough runs ended, and trimming keeps the rest."""

from pathlib import Path

import pytest

from dotagent.core.errors import InvalidValueError
from dotagent.server.routes_agents import agent_usage, trimmed_tools
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord, AgentToolUse, RunRecord
from dotagent.tools.team import MESSAGE_AGENT_TOOL


async def _ended_run(store: Store, agent: AgentRecord, tools: list[str], tokens: int) -> RunRecord:
    thread = await store.create_thread("A run", "ui")
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, "ui", None, "standard", None, "ask", False, None, agent.id, None
    )
    for tool in tools:
        await store.add_event(run.id, "tool_call", {"call_id": "c", "tool": tool, "args": {}})
    await store.add_run_tokens(run.id, tokens, 1)
    await store.finish_run(run.id, "done", "ok")
    return run


async def test_the_store_counts_the_tools_of_an_agents_newest_ended_runs_only(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        scout: AgentRecord = await store.create_agent(
            "Scout", "Reads", "", "", None, None, ("web_search", "read_file", "shell"), None, None
        )
        other: AgentRecord = await store.create_agent("Other", "", "", "", None, None, ("shell",), None, None)
        assert await store.agent_tool_use(scout.id, 20) == AgentToolUse(runs=0, tokens=0, newest=None, calls={})
        await _ended_run(store, scout, ["web_search", "web_search", "read_file"], 100)
        await _ended_run(store, scout, ["web_search"], 50)
        await _ended_run(store, other, ["shell"], 999)
        # A run still working is not counted: what it will call is not known yet.
        thread = await store.create_thread("Working", "ui")
        working = await store.create_run(
            thread.id,
            "goal",
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
            scout.id,
            None,
        )
        await store.add_event(working.id, "tool_call", {"call_id": "c", "tool": "shell", "args": {}})
        use: AgentToolUse = await store.agent_tool_use(scout.id, 20)
        assert (use.runs, use.tokens, use.calls) == (2, 152, {"web_search": 3, "read_file": 1})
        assert use.newest is not None
        # The window is the newest runs: the last one alone called web_search once.
        assert (await store.agent_tool_use(scout.id, 1)).calls == {"web_search": 1}
    finally:
        await store.close()


def _agent(tools: tuple[str, ...]) -> AgentRecord:
    return AgentRecord(
        id="agt_x",
        name="Scout",
        role="",
        instructions="",
        model="",
        effort=None,
        approvals=None,
        tools=tools,
        created_at=0.0,
        updated_at=0.0,
        made_by=None,
        look=None,
        paused_at=None,
    )


def test_the_unused_tools_are_named_once_three_runs_ended_and_never_the_ones_trimming_keeps() -> None:
    record: AgentRecord = _agent(("web_search", "read_file", "shell", "finish", MESSAGE_AGENT_TOOL))
    few = agent_usage(record, AgentToolUse(runs=2, tokens=10, newest=1.0, calls={"web_search": 4}), frozenset())
    assert (few.enough, few.unused) == (False, ())
    enough = agent_usage(record, AgentToolUse(runs=3, tokens=10, newest=1.0, calls={"web_search": 4}), frozenset())
    assert (enough.enough, enough.unused) == (True, ("read_file", "shell"))


def test_trimming_keeps_what_was_called_and_message_agent_and_refuses_what_would_change_nothing() -> None:
    known = frozenset({"web_search", "read_file", "shell", MESSAGE_AGENT_TOOL, "mcp__x__y"})
    named: AgentRecord = _agent(("web_search", "read_file", "shell", MESSAGE_AGENT_TOOL))
    use = AgentToolUse(runs=3, tokens=1, newest=1.0, calls={"web_search": 2})
    assert trimmed_tools(named, agent_usage(named, use, known), known) == ("web_search", MESSAGE_AGENT_TOOL)
    # One with every tool keeps the ones its runs called that are tools here, and message_agent.
    every: AgentRecord = _agent(())
    use_every = AgentToolUse(runs=4, tokens=1, newest=1.0, calls={"shell": 3, "finish": 4, "gone_tool": 1})
    assert trimmed_tools(every, agent_usage(every, use_every, known), known) == ("shell", MESSAGE_AGENT_TOOL)
    with pytest.raises(InvalidValueError):
        trimmed_tools(named, agent_usage(named, AgentToolUse(runs=2, tokens=1, newest=1.0, calls={}), known), known)
    # One with every action whose runs called only the ones every run has is not cut down to message_agent alone.
    only_core = agent_usage(
        every, AgentToolUse(runs=3, tokens=1, newest=1.0, calls={"finish": 3, "ask_user": 1}), known
    )
    assert only_core.own == ()
    with pytest.raises(InvalidValueError):
        trimmed_tools(every, only_core, known)
    # What it counts is what it keeps: a tool gone from this home and message_agent are not its own.
    assert agent_usage(every, use_every, known).own == ("shell",)
    talked = AgentToolUse(runs=3, tokens=1, newest=1.0, calls={MESSAGE_AGENT_TOOL: 2, "gone_tool": 1})
    with pytest.raises(InvalidValueError):
        trimmed_tools(every, agent_usage(every, talked, known), known)
    # One that names its actions and never called its one tool keeps message_agent alone, as Remove them says.
    lone: AgentRecord = _agent(("shell", MESSAGE_AGENT_TOOL))
    never = AgentToolUse(runs=3, tokens=1, newest=1.0, calls={"finish": 3})
    assert trimmed_tools(lone, agent_usage(lone, never, known), known) == (MESSAGE_AGENT_TOOL,)
    used_all = AgentToolUse(runs=3, tokens=1, newest=1.0, calls=dict.fromkeys(named.tools, 1))
    with pytest.raises(InvalidValueError):
        trimmed_tools(named, agent_usage(named, used_all, known), known)
