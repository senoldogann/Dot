"""The delegate tool: its run takes the parent's mode, which the model cannot choose (item 117), and in its result a step
the plan does not have is a note, not a refusal, and closes nothing."""

import pytest
from pydantic import ValidationError

from dotagent.agent.plan import PlanItem
from dotagent.store.models import RunRecord, RunStatus
from dotagent.tools.base import ToolResult
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.delegate import (
    DelegateArgs,
    closing_step,
    default_child_mode,
    delegated_evidence,
    render_outcome,
    unplanned_step_note,
)

STEPS: tuple[PlanItem, ...] = (
    PlanItem(text="Read the catalogue", status="pending", evidence="", depends_on=()),
    PlanItem(text="Write the report", status="pending", evidence="", depends_on=()),
)


def _run(status: RunStatus) -> RunRecord:
    return RunRecord(
        id="run_child",
        thread_id="thread_child",
        goal="Scan the catalogue",
        mode="task",
        model="ollama:scripted",
        status=status,
        created_at=1.0,
        finished_at=2.0,
        result="Found 12 items.",
        steps=4,
        input_tokens=0,
        output_tokens=0,
        parent_run_id="run_parent",
        source="delegate",
        effort=None,
        speed="standard",
        once=None,
        approvals="yolo",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
        rewindable=False,
    )


def test_a_step_the_plan_has_closes_and_one_it_does_not_have_is_a_note_that_closes_nothing() -> None:
    assert unplanned_step_note(STEPS, 2) is None
    assert unplanned_step_note(STEPS, 3) == (
        "Step 3 is not in the plan (the plan has 2 steps); the delegated run ran, nothing was marked."
    )
    assert unplanned_step_note((), 1) == (
        "Step 1 is not in the plan (the plan has 0 steps); the delegated run ran, nothing was marked."
    )
    outcome: DelegateOutcome = DelegateOutcome(run=_run("done"), tainted=False)
    planned: ToolResult = render_outcome(outcome, "abcd", 2, None)
    assert planned.text.startswith("Delegated run run_child on ollama:scripted ended as done after 4 steps.")
    assert planned.text.endswith("Plan step 2 is marked done with this as its evidence.")
    assert closing_step(2, planned) == 2
    assert delegated_evidence(planned).startswith("Delegated run run_child")
    unplanned: ToolResult = render_outcome(outcome, "abcd", None, unplanned_step_note(STEPS, 3))
    lines: list[str] = unplanned.text.split("\n")
    assert lines[0] == "Step 3 is not in the plan (the plan has 2 steps); the delegated run ran, nothing was marked."
    assert lines[1] == "Delegated run run_child on ollama:scripted ended as done after 4 steps."
    assert "Plan step" not in unplanned.text and not unplanned.is_error
    # The loop closes no step for it, and none for a call that named no step.
    assert closing_step(3, unplanned) is None
    assert closing_step(None, planned) is None


def test_a_delegated_run_takes_its_parents_mode_and_the_model_cannot_name_another() -> None:
    assert [default_child_mode(mode) for mode in ("task", "chat", "research")] == ["task", "task", "research"]
    assert "mode" not in DelegateArgs.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        DelegateArgs.model_validate({"goal": "Read the page", "mode": "research"})
