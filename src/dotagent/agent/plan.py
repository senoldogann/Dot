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
    """One step: its text, its state, for a step that was closed the evidence the run recorded (what it observed that
    shows the step done, or why it failed or was skipped; empty for an open step) and the 1-based numbers of the steps
    it waits for (``depends_on``; a step without any may start at once). The plans stored before evidence and
    dependencies existed have neither."""

    text: str
    status: StepStatus
    evidence: str = ""
    depends_on: tuple[int, ...] = ()


class PlanPayload(FrozenModel):
    """The data of a ``plan`` event: the steps, and the targets of the run (the measurable outcome it works toward and
    how it is measured; empty when the run set none, as in the events stored before targets existed)."""

    steps: tuple[PlanItem, ...]
    targets: str = ""


# The plan of a run that has not recorded one.
EMPTY_PLAN: PlanPayload = PlanPayload(steps=(), targets="")


def plan_event_data(plan: PlanPayload) -> JsonObject:
    return {
        "steps": [
            {"text": step.text, "status": step.status, "evidence": step.evidence, "depends_on": list(step.depends_on)}
            for step in plan.steps
        ],
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


def checked_step_number(steps: Sequence[PlanItem], number: int) -> int:
    """``number`` once it names a step of ``steps`` (1-based); a plan that is empty or has no such step is a tool input
    error."""
    if not steps:
        raise ToolInputError("there is no plan yet; record one with update_plan first", {"step": number})
    if number < 1 or number > len(steps):
        raise ToolInputError(
            f"the plan has steps 1 to {len(steps)}; there is no step {number}", {"step": number, "steps": len(steps)}
        )
    return number


def with_step_status(steps: Sequence[PlanItem], number: int, status: StepStatus, evidence: str) -> tuple[PlanItem, ...]:
    """The plan with step ``number`` (1-based) set to ``status`` and carrying ``evidence`` (empty clears what the step
    held: a step opened again has nothing shown yet)."""
    checked_step_number(steps, number)
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


def checked_dependencies(steps: Sequence[PlanItem]) -> tuple[PlanItem, ...]:
    """``steps`` once their dependencies are checked: every number names another step of the plan, and no step waits,
    through others, for itself. Anything else is a tool input error that names the step."""
    count: int = len(steps)
    for number, step in enumerate(steps, start=1):
        for wanted in step.depends_on:
            if wanted < 1 or wanted > count:
                raise ToolInputError(
                    f"step {number} depends on step {wanted}, and the plan has steps 1 to {count}",
                    {"step": number, "depends_on": wanted, "steps": count},
                )
            if wanted == number:
                raise ToolInputError(f"step {number} cannot depend on itself", {"step": number})
    for number in range(1, count + 1):
        if _reaches_itself(steps, number):
            raise ToolInputError(
                f"step {number} waits, through the steps it depends on, for itself; dependencies must not form a cycle",
                {"step": number},
            )
    return tuple(steps)


def _reaches_itself(steps: Sequence[PlanItem], start: int) -> bool:
    """Whether following ``depends_on`` from step ``start`` comes back to it."""
    seen: set[int] = set()
    frontier: list[int] = list(steps[start - 1].depends_on)
    while frontier:
        number: int = frontier.pop()
        if number == start:
            return True
        if number not in seen:
            seen.add(number)
            frontier.extend(steps[number - 1].depends_on)
    return False


def ready_steps(steps: Sequence[PlanItem]) -> tuple[int, ...]:
    """The 1-based numbers of the steps that may be worked on now: open, and waiting for no step that is still open or
    has failed. Steps that are ready together are independent of one another and can be delegated in one turn."""
    return tuple(
        number
        for number, step in enumerate(steps, start=1)
        if step.status not in CLOSED_STATES
        and all(steps[wanted - 1].status in ("done", "skipped") for wanted in step.depends_on)
    )


def held_steps(steps: Sequence[PlanItem]) -> tuple[int, ...]:
    """The 1-based numbers of the open steps that wait for a step that failed: they cannot start until that step is
    done again or skipped, or the plan is rewritten."""
    return tuple(
        number
        for number, step in enumerate(steps, start=1)
        if step.status not in CLOSED_STATES and any(steps[wanted - 1].status == "failed" for wanted in step.depends_on)
    )


def unevidenced_done(steps: Sequence[PlanItem]) -> int:
    """The steps marked done that carry no evidence: zero for every plan written since evidence is required, and the
    count a plan stored before that shows."""
    return sum(1 for step in steps if step.status == "done" and not step.evidence)


def _step_line(number: int, step: PlanItem) -> str:
    after: str = f" (after {', '.join(str(wanted) for wanted in step.depends_on)})" if step.depends_on else ""
    line: str = f"{number}. [{step.status}] {step.text}{after}"
    return f"{line}\n   evidence: {step.evidence}" if step.evidence else line


def render_plan(steps: Sequence[PlanItem]) -> str:
    """``1. [done] text (after 2, 3)`` lines, as the model reads the plan back, each closed step followed by its
    evidence."""
    return "\n".join(_step_line(index + 1, step) for index, step in enumerate(steps))


def readiness_lines(steps: Sequence[PlanItem]) -> str:
    """What may be worked on now, under a recorded plan: the ready steps (with the word that independent ones can go to
    delegate in one turn when there are several) and the steps held by a failed one; empty when no step is open."""
    ready: tuple[int, ...] = ready_steps(steps)
    held: tuple[int, ...] = held_steps(steps)
    lines: list[str] = []
    if len(ready) > 1:
        numbers: str = ", ".join(str(number) for number in ready)
        lines.append(f"Ready now: steps {numbers}; they are independent, so delegate can run them in one turn.")
    elif ready:
        lines.append(f"Ready now: step {ready[0]}.")
    if held:
        lines.append(f"Held by a failed step: {', '.join(str(number) for number in held)}.")
    return "\n".join(lines)


def plan_lines(plan: PlanPayload) -> str:
    """The plan as the model reads it: the step lines of :func:`render_plan`, then the targets when the run set some."""
    steps: str = render_plan(plan.steps)
    return f"{steps}\nTargets: {plan.targets}" if plan.targets else steps
