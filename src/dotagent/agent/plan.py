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


# What a step marked done must carry, named in the error a mark without it gets.
EVIDENCE_HINT: str = (
    "a step is done only with evidence: the tool result that shows it (the path and what was read back, the count or "
    "the line of output, what the page or the window showed), not the intention"
)
EVIDENCE_MAX_CHARS: int = 400


class PlanItem(FrozenModel):
    """One step: its text, its state and, for a step that was closed, the evidence the run recorded (what it observed
    that shows the step done, or why it failed or was skipped). Empty for an open step, and for the steps of the plans
    stored before evidence existed."""

    text: str
    status: StepStatus
    evidence: str = ""


class PlanPayload(FrozenModel):
    """The data of a ``plan`` event: the steps, and the targets of the run (the measurable outcome it works toward and
    how it is measured; empty when the run set none, as in the events stored before targets existed)."""

    steps: tuple[PlanItem, ...]
    targets: str = ""


# The plan of a run that has not recorded one.
EMPTY_PLAN: PlanPayload = PlanPayload(steps=(), targets="")


def plan_event_data(plan: PlanPayload) -> JsonObject:
    return {
        "steps": [{"text": step.text, "status": step.status, "evidence": step.evidence} for step in plan.steps],
        "targets": plan.targets,
    }


def plan_from_event(event: EventRecord) -> PlanPayload:
    """The plan a stored ``plan`` event holds. A malformed event is a store error, never an empty plan."""
    try:
        return PlanPayload.model_validate(event.data)
    except ValidationError as exc:
        raise StoreError(
            "a stored plan event is malformed",
            {"run_id": event.run_id, "event_id": event.id, "errors": exc.json(include_url=False)},
        ) from exc


def _checked_evidence(number: int, status: StepStatus, evidence: str) -> str:
    """``evidence`` for a step set to ``status``: a step marked done without any is refused."""
    if status == "done" and not evidence:
        raise ToolInputError(f"step {number} cannot be marked done without evidence: {EVIDENCE_HINT}", {"step": number})
    return evidence


def with_step_status(steps: Sequence[PlanItem], number: int, status: StepStatus, evidence: str) -> tuple[PlanItem, ...]:
    """The plan with step ``number`` (1-based) set to ``status`` and carrying ``evidence`` (empty clears what the step
    held: a step opened again has nothing shown yet)."""
    if not steps:
        raise ToolInputError("there is no plan yet; record one with update_plan first", {"step": number})
    if number < 1 or number > len(steps):
        raise ToolInputError(
            f"the plan has steps 1 to {len(steps)}; there is no step {number}", {"step": number, "steps": len(steps)}
        )
    checked: str = _checked_evidence(number, status, evidence)
    return tuple(
        step.model_copy(update={"status": status, "evidence": checked}) if index == number - 1 else step
        for index, step in enumerate(steps)
    )


def rewritten_steps(previous: Sequence[PlanItem], steps: Sequence[PlanItem]) -> tuple[PlanItem, ...]:
    """The steps of a rewritten plan. A step given as done keeps the evidence it carries; one given without takes the
    evidence of the step of the previous plan with the same text (the rewrite kept a step already shown done); one with
    neither is refused, so no plan ever holds a done step without evidence."""
    kept: dict[str, str] = {step.text.strip().casefold(): step.evidence for step in previous if step.evidence}
    return tuple(
        step.model_copy(
            update={
                "evidence": _checked_evidence(
                    number, step.status, step.evidence or kept.get(step.text.strip().casefold(), "")
                )
            }
        )
        for number, step in enumerate(steps, start=1)
    )


def open_steps(steps: Sequence[PlanItem]) -> tuple[int, ...]:
    """The 1-based numbers of the steps that are not closed yet."""
    return tuple(index + 1 for index, step in enumerate(steps) if step.status not in CLOSED_STATES)


def count_done(steps: Sequence[PlanItem]) -> int:
    return sum(1 for step in steps if step.status == "done")


def unevidenced_done(steps: Sequence[PlanItem]) -> int:
    """The steps marked done that carry no evidence: zero for every plan written since evidence is required, and the
    count a plan stored before that shows."""
    return sum(1 for step in steps if step.status == "done" and not step.evidence)


def _step_line(number: int, step: PlanItem) -> str:
    line: str = f"{number}. [{step.status}] {step.text}"
    return f"{line}\n   evidence: {step.evidence}" if step.evidence else line


def render_plan(steps: Sequence[PlanItem]) -> str:
    """``1. [done] text`` lines, as the model reads the plan back, each closed step followed by its evidence."""
    return "\n".join(_step_line(index + 1, step) for index, step in enumerate(steps))


def plan_lines(plan: PlanPayload) -> str:
    """The plan as the model reads it: the step lines of :func:`render_plan`, then the targets when the run set some."""
    steps: str = render_plan(plan.steps)
    return f"{steps}\nTargets: {plan.targets}" if plan.targets else steps
