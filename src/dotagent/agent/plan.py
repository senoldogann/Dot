"""The visible plan of a run: its steps and their states, and the targets it works toward, kept as ``plan`` events of
the run.

The latest ``plan`` event of a run is the plan: the UI paints it live, the agent loop pins it into every summary, and
``mark_plan_step`` changes one step of it. Every function here is pure; reading and writing the events is the caller's.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import ValidationError

from dotagent.core.errors import StoreError, ToolInputError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.models import EventRecord

PLAN_EVENT: str = "plan"

type StepStatus = Literal["pending", "in_progress", "done", "failed", "skipped"]

# A step in one of these states needs no more work.
CLOSED_STATES: frozenset[StepStatus] = frozenset({"done", "failed", "skipped"})


class PlanItem(FrozenModel):
    text: str
    status: StepStatus


class PlanPayload(FrozenModel):
    """The data of a ``plan`` event: the steps, and the targets of the run (the measurable outcome it works toward and
    how it is measured; empty when the run set none, as in the events stored before targets existed)."""

    steps: tuple[PlanItem, ...]
    targets: str = ""


# The plan of a run that has not recorded one.
EMPTY_PLAN: PlanPayload = PlanPayload(steps=(), targets="")


def plan_event_data(plan: PlanPayload) -> JsonObject:
    return {"steps": [{"text": step.text, "status": step.status} for step in plan.steps], "targets": plan.targets}


def plan_from_event(event: EventRecord) -> PlanPayload:
    """The plan a stored ``plan`` event holds. A malformed event is a store error, never an empty plan."""
    try:
        return PlanPayload.model_validate(event.data)
    except ValidationError as exc:
        raise StoreError(
            "a stored plan event is malformed",
            {"run_id": event.run_id, "event_id": event.id, "errors": exc.json(include_url=False)},
        ) from exc


def with_step_status(steps: Sequence[PlanItem], number: int, status: StepStatus) -> tuple[PlanItem, ...]:
    """The plan with step ``number`` (1-based) set to ``status``."""
    if not steps:
        raise ToolInputError("there is no plan yet; record one with update_plan first", {"step": number})
    if number < 1 or number > len(steps):
        raise ToolInputError(
            f"the plan has steps 1 to {len(steps)}; there is no step {number}", {"step": number, "steps": len(steps)}
        )
    return tuple(
        step.model_copy(update={"status": status}) if index == number - 1 else step for index, step in enumerate(steps)
    )


def open_steps(steps: Sequence[PlanItem]) -> tuple[int, ...]:
    """The 1-based numbers of the steps that are not closed yet."""
    return tuple(index + 1 for index, step in enumerate(steps) if step.status not in CLOSED_STATES)


def count_done(steps: Sequence[PlanItem]) -> int:
    return sum(1 for step in steps if step.status == "done")


def render_plan(steps: Sequence[PlanItem]) -> str:
    """``1. [done] text`` lines, as the model reads the plan back."""
    return "\n".join(f"{index + 1}. [{step.status}] {step.text}" for index, step in enumerate(steps))


def plan_lines(plan: PlanPayload) -> str:
    """The plan as the model reads it: the step lines of :func:`render_plan`, then the targets when the run set some."""
    steps: str = render_plan(plan.steps)
    return f"{steps}\nTargets: {plan.targets}" if plan.targets else steps
