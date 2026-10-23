"""The pure parts of the step router: the state a step is judged on, the Choice over the models offered, the policy
over the answer and the event of a routed step."""

from dotagent.agent.loop import _Candidate, still_routable
from dotagent.agent.scaffold import Scaffold
from dotagent.agent.stepmodel import (
    StepChoice,
    StepModel,
    choose,
    last_result,
    route_question,
    route_state,
    routed_event,
    step_state,
)
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message, ModelInfo
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
    assert last_result(history, ()) == "file one\n[system note] keep going"
    assert last_result(history[:1], ()) == ""
    state = step_state("Goal " * 500, 3, history, 2, 1, ())
    assert state.step == 3 and state.failures == 2 and state.failures_in_a_row == 1
    assert len(state.goal) == 2000 and state.goal.endswith("…")
    assert route_state(state) == {
        "goal": state.goal,
        "step": 3,
        "last_result": "file one\n[system note] keep going",
        "failures": 2,
        "failures_in_a_row": 1,
    }


def test_what_the_judge_is_sent_has_its_secrets_masked_before_it_is_clipped() -> None:
    """Item 551: TypeSafe is a service outside the Mac, and the goal and the last results can quote a key."""
    held: str = "correct-horse-battery-staple"
    key: str = "sk-test-0123456789abcdefghij"
    history: list[Message] = [
        _message("assistant", "Reading."),
        _message("tool", f"{'x' * 1474} the key is {held}, and {key}"),
    ]
    state = step_state(f"Use {held} to fetch the report", 1, history, 0, 0, (held,))
    assert state.goal == "Use *** to fetch the report"
    assert held not in state.last_result and key not in state.last_result
    # A cut through the key would have left half of it: masked first, none of it is left.
    assert held[:12] not in state.last_result and key[:12] not in state.last_result


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


def _candidate(option: StepModel, label: str) -> _Candidate:
    info: ModelInfo = ModelInfo(
        id=label,
        vision=False,
        tools=True,
        thinking=False,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=False,
    )
    scaffold: Scaffold = Scaffold(
        model=label,
        window=None,
        chosen=None,
        context_tokens=48_000,
        tool_output_chars=12_000,
        tier="standard",
        reason="",
        reminder_every=None,
    )
    return _Candidate(option=option, ref=parse_model_ref(label), info=info, scaffold=scaffold)


def test_after_a_move_to_a_standby_model_a_step_goes_to_none_of_the_models_the_run_worked_on() -> None:
    # The fast model was the standby the run moved to, and the main one stopped answering: neither is a place for a
    # step any more (the outside audit of 2026-10-17, R3).
    fast: _Candidate = _candidate("fast", "ollama:fast")
    helper: _Candidate = _candidate("helper", "openai:helper")
    assert still_routable((fast, helper), frozenset({"openai:main", "ollama:fast"})) == (helper,)
    assert still_routable((fast, helper), frozenset({"openai:main"})) == (fast, helper)
