"""The team (item 87): the prompt lists the agents a run may message, an agent is found by name, a reply is rendered
as the tool's result, and the harness settings of the paper have their defaults. An agent a run makes gets no tool the
run lacks and no model but a helper or the run's own (item 307)."""

from pathlib import Path

import pytest

from dotagent.agent.prompt import AgentBrief, build_system_prompt, collect_prompt_inputs
from dotagent.agent.templates import team_of
from dotagent.config import load_default_config
from dotagent.core.errors import ToolInputError
from dotagent.harness.components import NO_HARNESS
from dotagent.store.models import AgentRecord, RunRecord
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.team import (
    CREATE_AGENT_TOOL,
    MESSAGE_AGENT_TOOL,
    agent_model,
    agent_tools,
    build_team_tools,
    find_agent,
    render_reply,
)

SCOUT: AgentRecord = AgentRecord(
    id="agt_1",
    name="Scout",
    role="Finds and checks sources",
    instructions="",
    model="",
    effort=None,
    approvals=None,
    tools=(),
    created_at=1.0,
    updated_at=1.0,
    made_by=None,
    look=None,
    paused_at=None,
)
TEAM: tuple[AgentBrief, ...] = (
    AgentBrief(name="Scout", role="Finds and checks sources", instructions="", made_by_dot=False, team=""),
)


def _prompt(tool_names: list[str], team: tuple[AgentBrief, ...]) -> str:
    return _prompt_for(None, tool_names, team)


def _prompt_for(agent: AgentBrief | None, tool_names: list[str], team: tuple[AgentBrief, ...]) -> str:
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
            tool_names,
            [],
            0,
            None,
            [],
            agent,
            team,
            (),
            None,
            "full",
            NO_HARNESS,
        )
    )


def test_the_prompt_lists_the_team_only_when_the_run_may_message_it() -> None:
    with_tool: str = _prompt([MESSAGE_AGENT_TOOL, "shell"], TEAM)
    assert "# Team" in with_tool and "  - Scout: Finds and checks sources" in with_tool
    assert "create_agent" not in with_tool
    assert "# Team" not in _prompt(["shell"], TEAM)
    assert "# Team" not in _prompt([MESSAGE_AGENT_TOOL], ())


def test_the_prompt_offers_to_make_a_teammate_when_the_run_may_and_says_so_without_any() -> None:
    alone: str = _prompt([MESSAGE_AGENT_TOOL, CREATE_AGENT_TOOL], ())
    assert "# Team" in alone and "no team yet" in alone and "make the teammates it needs with create_agent" in alone
    assert "  - Scout" not in alone
    together: str = _prompt([MESSAGE_AGENT_TOOL, CREATE_AGENT_TOOL], TEAM)
    assert "  - Scout: Finds and checks sources" in together and "Make a new teammate with create_agent" in together
    # Item 307: the team is not said to be the owner's, as Dot may have made it.
    assert "The owner made" not in together and "reuse one before you make one" in together
    # Without the message tool there is no team to speak of, create or not.
    assert "# Team" not in _prompt([CREATE_AGENT_TOOL], ())


def _brief(name: str, role: str, team: str) -> AgentBrief:
    return AgentBrief(name=name, role=role, instructions="", made_by_dot=False, team=team)


def test_an_agent_of_a_ready_team_lists_its_teammates_first_and_hands_them_their_parts() -> None:
    """Item 512: Competitor Watch's chat delegated the Growth team's work to helpers. Its run is told its team, names
    its teammates first, and hands a part an agent's role fits to that agent rather than to delegate."""
    watch: AgentBrief = _brief("Competitor Watch", "Watches the competitors", "Growth team")
    team: tuple[AgentBrief, ...] = (
        _brief("Talent Scout", "Finds people for an open role", ""),
        _brief("Paid Media", "Reads the ad results", "Growth team"),
        _brief("Reviewer", "Reviews a change", "Engineering team"),
    )
    prompt: str = _prompt_for(watch, [MESSAGE_AGENT_TOOL, "delegate"], team)
    assert "- You are in the Growth team: your teammates come first" in prompt
    assert "rather than delegating it: delegate is for a part no agent's role covers" in prompt
    listed: list[str] = [line for line in prompt.splitlines() if line.startswith("  - ")]
    assert listed[:3] == [
        "  - Paid Media (Growth team): Reads the ad results",
        "  - Talent Scout: Finds people for an open role",
        "  - Reviewer (Engineering team): Reviews a change",
    ]
    # Dot's own run is in no team: the list keeps its order, each agent with its team, and without delegate the line
    # says nothing of it.
    dots: str = _prompt([MESSAGE_AGENT_TOOL], team)
    assert "You are in the" not in dots and "rather than delegating" not in dots
    assert "  - Talent Scout: Finds people for an open role\n  - Paid Media (Growth team)" in dots


def test_an_agent_is_in_the_ready_team_one_of_whose_members_has_its_name() -> None:
    growth = team_of("competitor watch ")
    assert growth is not None and growth.name == "Growth team"
    assert team_of("Talent Scout") is None and team_of("Scout") is None


def test_an_agent_is_found_by_its_name_whatever_the_case_or_by_its_id() -> None:
    assert find_agent([SCOUT], "scout ") == SCOUT
    assert find_agent([SCOUT], "agt_1") == SCOUT
    assert find_agent([SCOUT], "Pilot") is None


def _run(status: str, result: str) -> RunRecord:
    return RunRecord(
        id="run_2",
        thread_id="thr_2",
        goal="Find two sources",
        mode="task",
        model="scripted:m",
        status=status,  # type: ignore[arg-type]
        result=result,
        created_at=1.0,
        finished_at=2.0,
        steps=3,
        input_tokens=0,
        output_tokens=0,
        parent_run_id="run_1",
        source="team",
        effort=None,
        speed="standard",
        once=None,
        approvals="yolo",
        goal_mode=False,
        campaign_id=None,
        agent_id="agt_1",
        rewindable=False,
    )


def test_a_reply_names_the_agent_and_fences_what_an_untrusted_run_wrote() -> None:
    trusted = render_reply(SCOUT, DelegateOutcome(run=_run("done", "Two sources."), tainted=False), "abc")
    assert not trusted.is_error and trusted.text.startswith(
        "Scout (run run_2) ended as done after 3 steps.\nTwo sources."
    )
    tainted = render_reply(SCOUT, DelegateOutcome(run=_run("failed", ""), tainted=True), "abc")
    assert (
        tainted.is_error
        and tainted.untrusted
        and '<agent_reply nonce="abc" run="run_2">' in tainted.text
        and "(it replied nothing)" in tainted.text
    )


def test_the_harness_settings_of_the_paper_have_its_defaults() -> None:
    agent = load_default_config().agent
    assert (agent.context_elide_share, agent.context_summarise_share, agent.plan_each_turn) == (0.6, 0.85, True)


def test_an_agent_a_run_makes_gets_only_tools_the_run_has_and_with_none_named_its_makers_bound() -> None:
    """Item 307: an agent made in a run can do no more than the run that made it."""
    run_tools: frozenset[str] = frozenset({"shell", "read_file", "web_search"})
    assert agent_tools(["read_file", "web_search", "read_file"], run_tools, ()) == ("read_file", "web_search")
    with pytest.raises(ToolInputError) as refused:
        agent_tools(["read_file", "desktop_act"], run_tools, ())
    assert refused.value.message == "an agent gets only tools this run has, not desktop_act"
    # None named: Dot's run (no bound) gives every tool; an agent's bounded run gives its own bound.
    assert agent_tools([], run_tools, ()) == ()
    assert agent_tools([], run_tools, ("read_file",)) == ("read_file",)


def test_a_research_run_makes_no_agent_and_may_still_message_one() -> None:
    """Item 307: a run that only reads does not make a lasting teammate with tools to act."""
    modes: dict[str, frozenset[str]] = {tool.spec.name: frozenset(tool.modes) for tool in build_team_tools()}
    assert modes[CREATE_AGENT_TOOL] == {"task", "chat"} and "research" in modes[MESSAGE_AGENT_TOOL]


def test_an_agent_a_run_makes_runs_on_a_helper_model_or_the_runs_own_or_the_configured_one() -> None:
    allowed: list[str] = ["openai:gpt-5-mini", "anthropic:claude-sonnet-5-5"]
    assert agent_model("", allowed) == ""
    assert agent_model(" openai:gpt-5-mini ", allowed) == "openai:gpt-5-mini"
    with pytest.raises(ToolInputError) as refused:
        agent_model("anthropic:claude-opus-5-5", allowed)
    assert "(openai:gpt-5-mini, anthropic:claude-sonnet-5-5)" in refused.value.message
