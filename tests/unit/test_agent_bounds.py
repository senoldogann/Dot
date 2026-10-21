"""What a run of one of the owner's agents starts with (items 86 and 462): what its request or its schedule chose, else
the agent's own, else the configured ones; an approval mode chosen is kept as it is, yolo too, and never made the
stricter of the two."""

import pytest

from dotagent.agent.manager import AgentBounds, agent_bounds
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import AgentRecord


def _agent(model: str, approvals: ApprovalMode | None) -> AgentRecord:
    return AgentRecord(
        id="agt_scout",
        name="Scout",
        role="Reads the news",
        instructions="",
        model=model,
        effort="high",
        approvals=approvals,
        tools=("web_search",),
        created_at=0.0,
        updated_at=0.0,
        made_by=None,
        look=None,
    )


@pytest.mark.parametrize(
    ("chosen", "agents", "taken"),
    [
        # A yolo schedule of an agent that asks stays yolo.
        ("yolo", "ask", "yolo"),
        # A schedule left on Default takes its agent's yolo.
        (None, "yolo", "yolo"),
        # Neither chose: the configured mode, yolo when that is the one configured.
        (None, None, None),
        ("ask", "yolo", "ask"),
        ("supervised", None, "supervised"),
    ],
)
def test_the_approvals_are_the_ones_chosen_else_the_agents_else_the_configured_ones(
    chosen: ApprovalMode | None, agents: ApprovalMode | None, taken: ApprovalMode | None
) -> None:
    assert agent_bounds(_agent("", agents), None, None, chosen).approvals == taken


def test_the_model_and_the_level_are_the_ones_chosen_else_the_agents_and_an_empty_model_is_the_configured_one() -> None:
    assert agent_bounds(_agent("anthropic:claude-opus-5-5", None), None, None, None) == AgentBounds(
        model="anthropic:claude-opus-5-5", effort="high", approvals=None
    )
    assert agent_bounds(_agent("anthropic:claude-opus-5-5", None), "openai:gpt-5", "low", None) == AgentBounds(
        model="openai:gpt-5", effort="low", approvals=None
    )
    assert agent_bounds(_agent("", None), None, None, None).model is None
