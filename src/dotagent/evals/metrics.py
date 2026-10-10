"""Counting what a run did from the events it left."""

from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.plan import PLAN_EVENT, plan_from_event, unevidenced_done
from dotagent.core.errors import EvalError
from dotagent.evals.results import RunMetrics
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.types import invalid_arguments_text
from dotagent.store.models import EventRecord, RunRecord
from dotagent.store.stats import ACT_TOOLS

# A question nobody answered: one the run waited for (which the harness declines), or one it left and went on without.
_UNANSWERED_KINDS: frozenset[str] = frozenset({"approval_requested", QUESTION_LEFT_EVENT})


def _has_broken_arguments(event: EventRecord) -> bool:
    """Whether a ``tool_call`` event holds the stand-in a provider makes for arguments that were not valid JSON."""
    arguments: JsonValue = event.data.get("args")
    return isinstance(arguments, dict) and invalid_arguments_text(arguments) is not None


def _seconds(events: Sequence[EventRecord], kind: str) -> float:
    """The seconds the events of ``kind`` took in all."""
    total: float = 0.0
    for event in events:
        if event.kind != kind:
            continue
        seconds: JsonValue = event.data.get("seconds")
        if not isinstance(seconds, int | float):
            raise EvalError("an event lacks the seconds the harness counts", {"kind": kind, "event_id": event.id})
        total += seconds
    return total


def _batch_steps(event: EventRecord) -> int:
    """The steps a ``tool_result`` event of an act call recorded; 0 for an event recorded before they were counted."""
    steps: JsonValue = event.data.get("steps")
    return steps if isinstance(steps, int) else 0


def _cached_tokens(events: Sequence[EventRecord]) -> int:
    """The input tokens the provider served from its cache, over the model calls of the run."""
    total: int = 0
    for event in events:
        if event.kind != CALL_EVENT:
            continue
        cached: JsonValue = event.data.get("cached_tokens")
        if not isinstance(cached, int):
            raise EvalError("a model call lacks the cached tokens the harness counts", {"event_id": event.id})
        total += cached
    return total


def _unevidenced_steps(events: Sequence[EventRecord]) -> int:
    """The done steps without evidence in the run's last plan; 0 when it kept none."""
    plans: list[EventRecord] = [event for event in events if event.kind == PLAN_EVENT]
    return 0 if not plans else unevidenced_done(plan_from_event(plans[-1]).steps)


def _unproven_steps(events: Sequence[EventRecord]) -> int:
    """The done steps the goal checker could not bear out, over the run's goal checks."""
    total: int = 0
    for event in events:
        if event.kind != "goal_check":
            continue
        steps: JsonValue = event.data.get("unproven_steps")
        if not isinstance(steps, list):
            raise EvalError("a goal check lacks the unproven steps the harness counts", {"event_id": event.id})
        total += len(steps)
    return total


def run_metrics(run: RunRecord, events: Sequence[EventRecord]) -> RunMetrics:
    """The numbers of one run: its record gives steps and tokens, its events give the rest."""
    results: list[EventRecord] = [event for event in events if event.kind == "tool_result"]
    acts: list[EventRecord] = [event for event in results if event.data.get("tool") in ACT_TOOLS]
    return RunMetrics(
        steps=run.steps,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        tool_calls=sum(1 for event in events if event.kind == "tool_call"),
        tool_errors=sum(1 for event in results if event.data.get("ok") is False),
        invalid_arguments=sum(1 for event in events if event.kind == "tool_call" and _has_broken_arguments(event)),
        stalls=sum(1 for event in events if event.kind == "stall"),
        compactions=sum(1 for event in events if event.kind == "compacted"),
        unanswered_questions=sum(1 for event in events if event.kind in _UNANSWERED_KINDS),
        model_seconds=round(_seconds(events, CALL_EVENT), 3),
        tool_seconds=round(_seconds(events, "tool_result"), 3),
        acts=len(acts),
        single_acts=sum(1 for event in acts if _batch_steps(event) == 1),
        cached_tokens=_cached_tokens(events),
        unevidenced_steps=_unevidenced_steps(events),
        unproven_steps=_unproven_steps(events),
    )
