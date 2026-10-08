"""Counting what a run did from the events it left."""

from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.core.errors import EvalError
from dotagent.evals.results import RunMetrics
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.types import invalid_arguments_text
from dotagent.store.models import EventRecord, RunRecord


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


def run_metrics(run: RunRecord, events: Sequence[EventRecord]) -> RunMetrics:
    """The numbers of one run: its record gives steps and tokens, its events give the rest."""
    results: list[EventRecord] = [event for event in events if event.kind == "tool_result"]
    return RunMetrics(
        steps=run.steps,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        tool_calls=sum(1 for event in events if event.kind == "tool_call"),
        tool_errors=sum(1 for event in results if event.data.get("ok") is False),
        invalid_arguments=sum(1 for event in events if event.kind == "tool_call" and _has_broken_arguments(event)),
        stalls=sum(1 for event in events if event.kind == "stall"),
        compactions=sum(1 for event in events if event.kind == "compacted"),
        unanswered_questions=sum(1 for event in events if event.kind == "approval_requested"),
        model_seconds=round(_seconds(events, CALL_EVENT), 3),
        tool_seconds=round(_seconds(events, "tool_result"), 3),
    )
