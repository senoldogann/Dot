"""An eval of tool use, a step at a time (item 736): each case is a moment of a run that went wrong in the owner's own
runs (the text a page never showed waited for, a file edited with another agent's argument names, an address guessed),
told to the model as Dot tells a run (its system prompt and the schemas of its tools, captured from a run in a
throw-away home) with the steps before it, and judged by the first call that does the work: the right tool, with the
arguments that fit. A case costs one model call, or a few when the model plans first, so that the descriptions of the
tools can be climbed on the main model and trimmed with no regression, as another agent's maker did with its own (an
eval of real failures, then a hill climb per model, then a trim).

The cases are in :mod:`dotagent.evals.tool_cases`; `dotx eval tools` plays them."""

import re
from collections.abc import Sequence

from pydantic import Field, JsonValue

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import (
    ChatRequest,
    Message,
    ModelReply,
    ToolCall,
    assistant_message,
    tool_message,
)

# The calls that do no work of the case's own: planning, a reaction, a note of progress. The model is answered and
# asked again (at most :data:`EXTRA_TURNS` times) until it makes a call that does.
META_TOOLS: frozenset[str] = frozenset({"update_plan", "mark_plan_step", "react", "notify", "write_notes"})
EXTRA_TURNS: int = 2
# The call that ends a run with its answer.
FINISH: str = "finish"
# What a meta call is answered.
_META_RESULT: str = "OK."


class PriorStep(FrozenModel):
    """A step before the case's moment: the call the model made and what came back."""

    tool: str
    arguments: JsonObject
    result: str


class Expected(FrozenModel):
    """What passes: the first call that does work is one of ``tools``, with every argument of ``present`` given and each
    of ``match`` (an argument to a regular expression, searched in its text) matched; a call of ``forbid`` fails at
    once, and so does an answer with no call."""

    tools: tuple[str, ...] = Field(min_length=1)
    present: tuple[str, ...] = ()
    # The arguments one of ``tools`` needs besides (``shell`` for ten minutes of work: ``timeout_s``).
    present_for: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    match: dict[str, str] = Field(default_factory=dict)
    forbid: tuple[str, ...] = ()


class ToolCase(FrozenModel):
    """A moment of a run: its ``goal``, the steps before it, and what the next call should be. ``failure`` says which
    failure of the owner's runs it comes from."""

    id: str
    failure: str
    goal: str
    prior: tuple[PriorStep, ...] = ()
    expect: Expected


class CaseResult(FrozenModel):
    """How the model met a case: whether it passed, the call it made (``""`` for none) and its arguments, and why it
    failed."""

    case_id: str
    passed: bool
    tool: str
    arguments: JsonObject
    why: str
    input_tokens: int


def prior_messages(steps: Sequence[PriorStep]) -> list[Message]:
    """The messages of ``steps``: the model's call and its result, one after the other."""
    messages: list[Message] = []
    for number, step in enumerate(steps, start=1):
        call: ToolCall = ToolCall(id=f"prior_{number}", name=step.tool, arguments=step.arguments)
        messages.append(assistant_message("", None, (call,)))
        messages.append(tool_message(call, step.result, ()))
    return messages


def first_work(reply: ModelReply) -> ToolCall | None:
    """The reply's first call that is not a meta call (:data:`META_TOOLS`), or ``None``."""
    return next((call for call in reply.tool_calls if call.name not in META_TOOLS), None)


def _text(value: JsonValue) -> str:
    return value if isinstance(value, str) else str(value)


def judge(case: ToolCase, call: ToolCall | None) -> tuple[bool, str]:
    """Whether ``call`` passes ``case``, and why not."""
    expect: Expected = case.expect
    if call is None:
        # An answer with no call ends a task run as finish does (the loop takes it after its nudges): where finishing
        # fits, it passes.
        return (True, "") if FINISH in expect.tools else (False, "no call that does work")
    if call.name in expect.forbid:
        return False, f"called {call.name}, which this moment must not"
    if call.name not in expect.tools:
        return False, f"called {call.name}, not one of {', '.join(expect.tools)}"
    needed: tuple[str, ...] = (*expect.present, *expect.present_for.get(call.name, ()))
    missing: list[str] = [name for name in needed if name not in call.arguments]
    if missing:
        return False, f"left out {', '.join(missing)} (gave {', '.join(sorted(call.arguments)) or 'nothing'})"
    for name, pattern in expect.match.items():
        if name not in call.arguments or re.search(pattern, _text(call.arguments[name])) is None:
            return False, f"{name} does not match {pattern!r}"
    return True, ""


def case_request(base: ChatRequest, model: str, history: Sequence[Message]) -> ChatRequest:
    """``base`` (the request a run of the case's goal sent first, captured) asked of ``model`` with ``history`` after
    its own messages."""
    return base.model_copy(update={"model": model, "messages": (*base.messages, *history)})


def meta_answers(reply: ModelReply) -> list[Message]:
    """The model's meta calls and their plain answers, to ask it again."""
    calls: tuple[ToolCall, ...] = reply.tool_calls
    return [assistant_message(reply.text, None, calls), *(tool_message(call, _META_RESULT, ()) for call in calls)]


def pass_rate(results: Sequence[CaseResult]) -> float:
    return sum(1 for result in results if result.passed) / len(results) if results else 0.0
