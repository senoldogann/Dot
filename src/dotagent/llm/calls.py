"""Where the seconds and the tokens of the model calls go: what a call is for, which run it belongs to and what it cost.

:class:`dotagent.llm.router.ModelRouter` measures every call it sends and hands it to the recorder it was built with.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject

log = get_logger(__name__)

# What a model call is for: a step of a run, the summary of a compaction, a judgement of the review model (an approval
# in auto mode, the goal check, the progress consultant's second opinion, the lessons), a description of images for a
# model that cannot see (of a tool's result or of an attachment), or a side question (/btw).
type CallPurpose = Literal[
    "step", "compaction", "review", "goal_check", "progress", "lessons", "vision", "attachment", "aside"
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
    call that did not answer used no tokens that a provider reported."""

    run_id: str | None
    purpose: CallPurpose
    model: str
    session_key: str
    seconds: float
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
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cached_tokens": call.cached_tokens,
        "error": call.error,
    }


async def log_model_call(call: ModelCall) -> None:
    """The recorder of a router that serves no run (the commands that list models or check keys): the call is logged."""
    log.info(CALL_EVENT, run_id=call.run_id, session_key=call.session_key, **call_event_data(call))
