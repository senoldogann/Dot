"""Item 950: with a team, delegate and create_agent take the agents considered and why none fits; without that line
they are refused with the team listed, so that the run looks at it before it hands work to a helper or makes an agent
beside one that has the role."""

import pytest

from dotagent.core.errors import ToolInputError
from dotagent.store.models import AgentRecord
from dotagent.tools.delegate import checked_considered, team_listing


def _agent(name: str, role: str) -> AgentRecord:
    return AgentRecord.model_construct(id=f"agt_{name.lower()}", name=name, role=role)


_TEAM: list[AgentRecord] = [
    _agent("Research Desk", "Answers research questions from the public web with sources."),
    _agent("Growth Lead", "Plans the launch and runs the calendar."),
]


def test_with_no_team_nothing_is_asked() -> None:
    assert checked_considered("", [], "send it to them") is None


def test_with_a_team_the_line_is_asked_for_with_the_team_listed() -> None:
    for missing in ("", "   "):
        with pytest.raises(ToolInputError) as refused:
            checked_considered(missing, _TEAM, "send the part to the agent whose role fits it")
        said: str = refused.value.message
        assert "send the part to the agent whose role fits it" in said and "agents_considered" in said
        assert said.endswith(team_listing(_TEAM))
    assert checked_considered("  Research Desk answers questions;  this one runs code. ", _TEAM, "x") == (
        "Research Desk answers questions; this one runs code."
    )


def test_the_team_is_listed_a_line_each_by_name_and_role() -> None:
    long: AgentRecord = _agent("Writer", "word " * 60)
    listed: str = team_listing([*_TEAM, long])
    assert listed.splitlines()[:2] == [
        "- Research Desk: Answers research questions from the public web with sources.",
        "- Growth Lead: Plans the launch and runs the calendar.",
    ]
    assert len(listed.splitlines()[2]) <= len("- Writer: ") + 120
