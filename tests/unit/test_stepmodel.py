"""The pure parts of the step router: the state a step is judged on, the Choice over the models offered, the policy
over the answer and the event of a routed step."""

from dotagent.agent.stepmodel import (
    StepChoice,
    choose,
    last_result,
    route_question,
    route_state,
    routed_event,
    step_state,
)
from dotagent.llm.types import Message
from dotagent.llm.typesafe import ChoiceAnswer


def _message(role: str, content: str) -> Message:
    return Message(
        role=role,  # type: ignore[arg-type]
        content=content,
        images=(),
        tool_calls=(),
        tool_call_id=None,
        tool_name=None,
        thinking=None,
        raw=(),
        raw_origin=None,
    )


def test_the_last_result_is_what_came_after_the_models_last_reply() -> None:
    history: list[Message] = [
        _message("user", "Do the thing."),
        _message("assistant", "Reading."),
        _message("tool", "file one"),
        _message("tool", ""),
        _message("user", "[system note] keep going"),
    ]
    assert last_result(history) == "file one\n[system note] keep going"
    assert last_result(history[:1]) == ""
    state = step_state("Goal " * 500, 3, history, 2, 1)
    assert state.step == 3 and state.failures == 2 and state.failures_in_a_row == 1
    assert len(state.goal) == 2000 and state.goal.endswith("…")
    assert route_state(state) == {
        "goal": state.goal,
        "step": 3,
        "last_result": "file one\n[system note] keep going",
        "failures": 2,
        "failures_in_a_row": 1,
    }


def test_the_choice_offers_the_models_the_run_can_use_and_main_is_always_among_them() -> None:
    assert list(route_question(["fast", "main"]).options) == ["fast", "main"]
    assert list(route_question(["helper", "main", "fast"]).options) == ["fast", "main", "helper"]
    question = route_question(["main"])
    assert list(question.options) == ["main"] and "`last_result`" in question.instructions


def test_another_model_takes_the_step_only_at_the_threshold_and_the_likelier_one_when_both_reach_it() -> None:
    def answer(**probabilities: float) -> dict[str, ChoiceAnswer]:
        best: str = max(probabilities, key=probabilities.__getitem__)
        return {"step_model": ChoiceAnswer(choice=best, probabilities=probabilities, confidence=probabilities[best])}

    assert choose(answer(fast=0.7, main=0.2, helper=0.1), 0.6).model == "fast"
    assert choose(answer(fast=0.5, main=0.4, helper=0.1), 0.6).model == "main"
    assert choose(answer(fast=0.62, main=0.0, helper=0.38), 0.6).model == "fast"
    assert choose(answer(fast=0.1, main=0.2, helper=0.7), 0.6).model == "helper"
    assert choose(answer(fast=0.3, main=0.7), 0.6) == StepChoice(
        model="main", probabilities={"fast": 0.3, "main": 0.7}, confidence=0.7
    )
    assert routed_event(4, "ollama:gpt-oss:20b-cloud", choose(answer(fast=0.81, main=0.19), 0.6)) == {
        "step": 4,
        "model": "ollama:gpt-oss:20b-cloud",
        "option": "fast",
        "probabilities": {"fast": 0.81, "main": 0.19},
        "confidence": 0.81,
    }
