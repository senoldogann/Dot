"""The composer's picks (items 318 and 415) checked against what a run may use: each refusal in its own words, and the
MCP servers the owner opened to an agent's run whatever its tools."""

from dotagent.agent.picks import owner_server_groups, picks_problems
from dotagent.store.models import RunPicks


def _picks(*servers: str) -> RunPicks:
    return RunPicks(plugins=servers, skills=(), plan_first=False)


def test_a_picked_server_is_refused_for_what_keeps_it_out_not_always_as_not_connected() -> None:
    groups: frozenset[str] = frozenset({"core", "mcp__notes"})
    assert picks_problems(_picks("notes"), "task", groups, ("notes",), (), True) == []
    assert picks_problems(_picks("gone"), "task", groups, ("notes",), (), True) == [
        "the MCP server gone is not connected (the servers connected now: notes); Customize › Plugins says why"
    ]
    # Connected, but a chat may use none of its tools (they all act): the message goes as a task.
    assert picks_problems(_picks("writer"), "chat", groups, ("notes", "writer"), (), True) == [
        "the MCP server writer has no tool a chat run may use; send the message as a task"
    ]


def test_the_servers_open_to_an_agent_are_the_ones_the_owner_picked_now_or_earlier_in_the_chat() -> None:
    """The review's finding: a server the model called is no pick; only the owner's picks open one."""
    assert owner_server_groups(_picks("deepwiki"), frozenset({"rea-mcp"})) == frozenset(
        {"mcp__deepwiki", "mcp__rea-mcp"}
    )
    assert owner_server_groups(_picks(), frozenset()) == frozenset()
