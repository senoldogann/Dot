"""Where the seconds and the tokens of the model calls go: what a call is for, which run it belongs to and what it cost.

:class:`dotagent.llm.router.ModelRouter` measures every call it sends and hands it to the recorder it was built with.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject

log = get_logger(__name__)

# What a model call is for: a step of a run, the summary of a compaction, a judgement of the review model (an approval
# in auto mode, the goal check, the progress consultant's second opinion, the lessons, a rule drafted from the owner's
# words), a description of images for a model that cannot see (of a tool's result or of an attachment), a side
# question (/btw), or a question of the run to one of its helper models (the consult tool).
type CallPurpose = Literal[
    "step",
    "compaction",
    "review",
    "goal_check",
    "progress",
    "lessons",
    "playbook",
    "owner",
    "rule_draft",
    "extract",
    "vision",
    "attachment",
    "aside",
    # The recap of a run for its page (item 536), written by the fast model from the run's record.
    "recap",
    "consult",
    "memory",
    # The roles of the harness (item 390, docs/harness.md): the proposer, the composer, the blind selector and the
    # experience writer.
    "harness_propose",
    "harness_task",
    "harness_compose",
    "harness_select",
    "harness_experience",
    # A watcher's question of the fast model, whether one new mail or notification is one the owner wants to hear of
    # (item 571).
    "watch",
    # Whether a message sent to a chat at work is a request of its own (item 915).
    "busy",
    # The review of a plugin the scout found for the gallery (item 710).
    "scout",
    # Which free agents could help an agent at work (item 896), asked while the office is open.
    "offer",
]

CALL_EVENT: str = "model_call"
# What a call that a stop or its caller's timeout cut off records as its error.
CANCELLED: str = "cancelled"


class CallOrigin(FrozenModel):
    """Who makes a model call: the run it belongs to (``None`` outside any run) and what it is for."""

    run_id: str | None
    purpose: CallPurpose


class ModelCall(FrozenModel):
    """One model call as it was measured. ``model`` is the ``provider:model`` reference; ``error`` is the type of the
    error a failed call raised, :data:`CANCELLED` for a call that was cut off and ``None`` for a call that answered. A
    call that did not answer used no tokens that a provider reported. ``first_token_seconds`` is how long the first
    word of the reply took to come (item 404), ``None`` when none came: the prompt's processing, which a provider's
    cache of the prompt's start shortens."""

    run_id: str | None
    purpose: CallPurpose
    model: str
    session_key: str
    seconds: float
    first_token_seconds: float | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    error: str | None


CallRecorder = Callable[[ModelCall], Awaitable[None]]


def call_event_data(call: ModelCall) -> JsonObject:
    """The ``model_call`` event of a call: what it was for, on which model, its seconds and its tokens."""
    return {
        "purpose": call.purpose,
        "model": call.model,
        "seconds": round(call.seconds, 3),
        "first_token_seconds": None if call.first_token_seconds is None else round(call.first_token_seconds, 3),
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cached_tokens": call.cached_tokens,
        "error": call.error,
    }


async def log_model_call(call: ModelCall) -> None:
    """The recorder of a router that serves no run (the commands that list models or check keys): the call is logged."""
    log.info(CALL_EVENT, run_id=call.run_id, session_key=call.session_key, **call_event_data(call))
