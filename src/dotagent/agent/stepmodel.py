"""A step's model chosen by a typed judgment (``[agent] step_router``): before each step of a run, one Choice of
TypeSafe's over the run's models from the step's state, so that a routine step goes to the fast model and a hard one
to a helper, while the run's own model takes the rest.

The state is the goal, the number of the step, what the last step brought (the tool results and notes since the model
last spoke), how many tool calls failed so far and how many steps in a row ended with a failure. The options are the
run's own model (``main``), ``[models] fast`` (``fast``) and the first available helper of ``[models] helpers``
(``helper``), each described by the kind of step it is for; a model that cannot take the run's tools, its window or
its images is not offered. Code owns the policy: a model other than the run's own takes the step only when its
probability reaches ``[agent] step_router_threshold``, and the run's model takes it otherwise. A step the routed model
cannot answer is asked again on the run's model. Every step that another model takes is a ``step_routed`` event.
"""

from collections.abc import Sequence
from typing import Literal

from dotagent.core.text import redact_known
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Message
from dotagent.llm.typesafe import ChoiceAnswer, ChoiceQuestion

STEP_ROUTED_EVENT: str = "step_routed"
STEP_ROUTE_FAILED_EVENT: str = "step_route_failed"
_LAST_RESULT_CHARS: int = 1500
_GOAL_CHARS: int = 2000

type StepModel = Literal["fast", "main", "helper"]

# What each option is for, as the judge reads it.
_OPTIONS: dict[StepModel, str] = {
    "fast": (
        "A small, fast model, for a routine next step whose action follows from the last result without judgment: "
        "the next file to read, the same working pattern on the next item, a straightforward tool call, a short "
        "confirmation."
    ),
    "main": (
        "The run's usual model, for a step that needs judgment: planning or changing the plan, reading an unfamiliar "
        "result, writing for the user, deciding between ways to go on, the first step of a task."
    ),
    "helper": (
        "A stronger and slower model, for a hard step: after repeated failures, when the results contradict the plan, "
        "when the task needs careful reasoning or expertise the usual model showed it lacks."
    ),
}
_QUESTION_ID: str = "step_model"
_INSTRUCTIONS: str = (
    "Which model should take the next step of this run? `goal` is the task, `step` the number of the step about to be "
    "taken, `last_result` what the previous step brought back (tool results and notes; empty before the first step), "
    "`failures` how many tool calls failed so far and `failures_in_a_row` how many steps in a row ended with a "
    "failure. Pick the option whose description fits the next step."
)


class StepState(FrozenModel):
    """The state the judge reads before a step."""

    goal: str
    step: int
    last_result: str
    failures: int
    failures_in_a_row: int


class StepChoice(FrozenModel):
    """What the judge said and what the policy made of it: the option that takes the step, the probability of each
    option and how concentrated they were."""

    model: StepModel
    probabilities: dict[str, float]
    confidence: float


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def last_result(history: Sequence[Message], known: Sequence[str]) -> str:
    """What the previous step brought back: the messages after the model's last reply (tool results, notes, what the
    user sent), oldest first, with the secrets ``known`` and keys of a known form masked; empty when the model has not
    spoken yet."""
    spoken: int = next((index for index in range(len(history) - 1, -1, -1) if history[index].role == "assistant"), -1)
    if spoken < 0:
        return ""
    parts: list[str] = [message.content for message in history[spoken + 1 :] if message.content]
    # Masked before it is clipped: a cut through a key would leave a half that no pattern knows.
    return _clip(redact_known("\n".join(parts), known), _LAST_RESULT_CHARS)


def step_state(
    goal: str, step: int, history: Sequence[Message], failures: int, failures_in_a_row: int, known: Sequence[str]
) -> StepState:
    """The state of ``step`` for the judge, a service outside the Mac: the goal and what the last step brought back have
    their secrets masked, those Dot holds (``known``, :func:`dotagent.secrets.known_secrets`) and keys of a known form
    (item 551)."""
    return StepState(
        goal=_clip(redact_known(goal, known), _GOAL_CHARS),
        step=step,
        last_result=last_result(history, known),
        failures=failures,
        failures_in_a_row=failures_in_a_row,
    )


def route_state(state: StepState) -> JsonObject:
    return {
        "goal": state.goal,
        "step": state.step,
        "last_result": state.last_result,
        "failures": state.failures,
        "failures_in_a_row": state.failures_in_a_row,
    }


def route_question(offered: Sequence[StepModel]) -> ChoiceQuestion:
    """The Choice over the models the run can take the step on; ``main`` is always among them."""
    return ChoiceQuestion(
        id=_QUESTION_ID,
        instructions=_INSTRUCTIONS,
        options={option: _OPTIONS[option] for option in ("fast", "main", "helper") if option in offered},
    )


def choose(answers: dict[str, ChoiceAnswer], threshold: float) -> StepChoice:
    """The policy over the judge's answer: ``fast`` or ``helper`` when its probability reaches ``threshold`` (the
    likelier of the two when both do), else ``main``."""
    answer: ChoiceAnswer = answers[_QUESTION_ID]
    others: list[tuple[float, StepModel]] = [
        (answer.probabilities[option], option)
        for option in ("fast", "helper")
        if option in answer.probabilities and answer.probabilities[option] >= threshold
    ]
    picked: StepModel = max(others)[1] if others else "main"
    return StepChoice(model=picked, probabilities=dict(answer.probabilities), confidence=answer.confidence)


def routed_event(step: int, model: str, choice: StepChoice) -> JsonObject:
    """The ``step_routed`` event: the step, the model that takes it and the judge's probabilities."""
    return {
        "step": step,
        "model": model,
        "option": choice.model,
        "probabilities": {option: round(value, 3) for option, value in choice.probabilities.items()},
        "confidence": round(choice.confidence, 3),
    }
