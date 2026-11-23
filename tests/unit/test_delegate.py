"""The delegate tool: its run takes the parent's mode, which the model cannot choose (item 117), and in its result a
step the plan does not have is a note, not a refusal, and closes nothing."""

import pytest
from pydantic import ValidationError

from dotagent.agent.plan import PlanItem
from dotagent.agent.verify import CallFact, RunFacts
from dotagent.store.models import RunRecord, RunStatus
from dotagent.tools.base import ToolResult
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.delegate import (
    DelegateArgs,
    build_delegate_tools,
    closing_step,
    default_child_mode,
    delegated_evidence,
    opening_for,
    render_outcome,
    state_pack,
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


def test_a_report_without_a_verification_section_is_marked_as_a_claim() -> None:
    outcome: DelegateOutcome = DelegateOutcome(run=_run("done"), tainted=False)
    result: ToolResult = render_outcome(outcome, "abcd", None, None)
    assert "No Verification section was reported" in result.text


def test_a_report_with_a_verification_section_is_not_marked() -> None:
    run = _run("done").model_copy(update={"result": "Found 12 items.\n\nVerification: `uv run pytest` — 2 passed."})
    outcome: DelegateOutcome = DelegateOutcome(run=run, tainted=False)
    result: ToolResult = render_outcome(outcome, "abcd", None, None)
    assert "No Verification section was reported" not in result.text


def test_the_delegate_tool_asks_the_child_for_a_verification_section() -> None:
    goal: str = DelegateArgs.model_json_schema()["properties"]["goal"]["description"]
    assert "Verification" in goal
    assert "Verification" in build_delegate_tools()[0].spec.description


def test_a_delegated_run_starts_with_the_state_its_parent_already_measured() -> None:
    facts: RunFacts = RunFacts(
        written=("/work/report.md",),
        failures=(CallFact(tool="read_file", target="/work/missing.md", ok=False, result="no such file"),),
        resolved=(CallFact(tool="shell", target="uv run pytest -q", ok=False, result="1 failed, then 12 passed"),),
        checks=(CallFact(tool="shell", target="uv run pytest -q", ok=True, result="12 passed"),),
        named=(),
    )
    steps: tuple[PlanItem, ...] = (PlanItem(text="Write the report", status="done", evidence="12 sections"),)
    pack: str = state_pack(facts, steps)

    assert pack.startswith("STATE FROM THE RUN THAT HANDED THIS OVER")
    assert "ITS PLAN" in pack and "Write the report" in pack and "12 sections" in pack
    assert "FILES IT WROTE OR EDITED" in pack and "/work/report.md" in pack
    assert "CHECKS IT RAN" in pack and "12 passed" in pack
    assert "FAILED CALLS IT LEFT UNRESOLVED" in pack and "no such file" in pack
    assert "FAILED CALLS IT RESOLVED" in pack and "then 12 passed" in pack


def test_a_parent_with_nothing_measured_hands_over_no_state() -> None:
    assert state_pack(RunFacts(written=(), failures=(), resolved=(), checks=(), named=()), ()) == ""
    assert opening_for("Read the page", "") is None
    assert opening_for("Read the page", "STATE") == "Read the page\n\nSTATE"


def test_the_state_pack_keeps_only_what_fits_a_hand_over() -> None:
    facts: RunFacts = RunFacts(
        written=tuple(f"/work/file-{index}.md" for index in range(20)),
        failures=(),
        resolved=(),
        checks=tuple(
            CallFact(tool="shell", target=f"uv run pytest tests/{index}", ok=True, result="ok") for index in range(9)
        ),
        named=(),
    )
    pack: str = state_pack(facts, ())

    assert "/work/file-19.md" in pack and "/work/file-7.md" not in pack
    assert "tests/8" in pack and "tests/0" not in pack
    assert len(pack) <= 3000 + 100


def test_a_long_plan_does_not_crowd_out_the_checks_and_the_failures() -> None:
    steps: tuple[PlanItem, ...] = tuple(
        PlanItem(text=f"step {index} " + "x" * 300, status="done", evidence="y" * 300) for index in range(12)
    )
    facts: RunFacts = RunFacts(
        written=(),
        failures=(CallFact(tool="shell", target="uv run pytest tests/unit", ok=False, result="1 failed"),),
        resolved=(),
        checks=(CallFact(tool="shell", target="uv run ruff check", ok=True, result="all good"),),
        named=(),
    )
    pack: str = state_pack(facts, steps)

    assert "CHECKS IT RAN" in pack and "all good" in pack
    assert "FAILED CALLS IT LEFT UNRESOLVED" in pack and "1 failed" in pack
    assert len(pack) <= 3000 + 100
