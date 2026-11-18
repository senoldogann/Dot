"""An eval measures a run on a program that runs the whole goal itself (the Claude plan, OpenCode) as it measures
Dot's own loop, and keeps the owner's Claude plan for their own work (item 1220)."""

from dotagent.core.types import JsonObject
from dotagent.evals.metrics import run_metrics, run_trace
from dotagent.evals.runner import PLAN_WINDOW_MOST, ended_by_plan_limit, plan_window_of, plan_window_spent
from dotagent.llm.calls import CALL_EVENT
from dotagent.repair.claude import LimitWindow
from dotagent.store.models import EventRecord, RunRecord

_RUN: RunRecord = RunRecord(
    id="run-1", thread_id="thread-1", goal="Write the memo.", mode="task", model="claude_code:sonnet", status="done",
    created_at=1.0, finished_at=2.0, result="Done.", steps=2, input_tokens=90_000, output_tokens=400,
    parent_run_id=None, source="eval", effort=None, speed="standard", once=None, approvals="ask", goal_mode=False,
    campaign_id=None, agent_id=None, rewindable=False,
)  # fmt: skip


def _event(index: int, kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=index, run_id="run-1", ts=float(index), kind=kind, data=data)


def _step(index: int, input_tokens: int, cached: int, seconds: float) -> EventRecord:
    """A step of a run on the Claude plan: its assistant event, which carries what a model call does in Dot's loop."""
    return _event(
        index,
        "assistant",
        {
            "step": index,
            "input_tokens": input_tokens,
            "output_tokens": 200,
            "cached_tokens": cached,
            "seconds": seconds,
        },
    )


def test_a_plan_run_is_measured_from_its_steps() -> None:
    events: tuple[EventRecord, ...] = (_step(1, 40_000, 30_000, 4.0), _step(2, 50_000, 45_000, 6.5))
    metrics = run_metrics(_RUN, events)
    assert (metrics.model_calls, metrics.cached_tokens, metrics.peak_input_tokens) == (2, 75_000, 50_000)
    assert metrics.model_seconds == 10.5
    assert [(step.kind, step.name, step.input_tokens) for step in run_trace(events)] == [
        ("model", "step", 40_000),
        ("model", "step", 50_000),
    ]


def test_a_run_of_dots_loop_is_measured_from_its_model_calls_alone() -> None:
    """Its assistant events would count each step twice."""
    call: JsonObject = {"purpose": "step", "input_tokens": 1000, "output_tokens": 10, "cached_tokens": 600}
    events: tuple[EventRecord, ...] = (
        _event(1, CALL_EVENT, {**call, "seconds": 1.0, "error": None}),
        _step(2, 1000, 600, 1.2),
    )
    metrics = run_metrics(_RUN, events)
    assert (metrics.model_calls, metrics.cached_tokens, metrics.model_seconds) == (1, 600, 1.0)


def _window(status: str, utilization: float | None) -> LimitWindow:
    return LimitWindow(kind="five_hour", status=status, utilization=utilization, resets_at=None)


def test_an_attempt_on_the_plan_stops_where_the_owners_share_of_the_window_begins() -> None:
    assert not plan_window_spent(_window("allowed", PLAN_WINDOW_MOST - 0.01))
    assert plan_window_spent(_window("allowed_warning", PLAN_WINDOW_MOST))
    assert plan_window_spent(_window("rejected", None))
    assert not plan_window_spent(_window("allowed", None))


def test_the_window_is_read_from_a_plan_runs_step_and_from_no_other() -> None:
    window: LimitWindow = LimitWindow(kind="seven_day", status="allowed", utilization=0.5, resets_at=9.0)
    # As claude_code/run.py writes it into a step's assistant event.
    assert plan_window_of({"plan_window": window.model_dump(mode="json")}) == window
    assert plan_window_of({"plan_window": None}) is None
    assert plan_window_of({}) is None


def test_a_run_the_plans_window_refused_is_told_from_its_end() -> None:
    def finished(error: JsonObject | None) -> tuple[EventRecord, ...]:
        return (_event(1, "run_started", {}), _event(2, "run_finished", {"status": "failed", "error": error}))

    assert ended_by_plan_limit(finished({"message": "limit", "context": {}, "kind": "plan_limit"}))
    assert not ended_by_plan_limit(finished({"message": "down", "context": {}, "kind": "passing"}))
    assert not ended_by_plan_limit(finished(None))
    assert not ended_by_plan_limit(())
