"""The limits of a run: how many minutes a task or research run may take (its wall time, waits for the user
included) and how many tokens it and the runs it delegated to may use, as ``[agent] run_max_minutes`` and
``run_max_tokens`` set them (0: no limit). They are the limits of one run: a run that goes on from another (resumed,
or started by an answer) starts with a new clock and a new count.

The loop compares them with what the run spent between steps. At the first step past a limit the run is told so and
may only finish or close its plan, and its next reply ends it. Everything here is pure; the loop reads the clock and the
store, stores the note and ends the run.
"""

from dotagent.agent.continuity import RunLedger
from dotagent.agent.progress import stop_account
from dotagent.config import AgentConfig
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.endings import PREFIX_LIMIT
from dotagent.store.models import RunMode
from dotagent.tools.meta import FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL

LIMIT_EVENT: str = "limit_reached"
# What a run past its limit may still call: finish, and the plan tools, to close its plan in the same reply.
LIMIT_TOOLS: frozenset[str] = frozenset({FINISH_TOOL, UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL})
_SECONDS_PER_MINUTE: float = 60.0
_REPLY_CHARS: int = 1500


class RunLimits(FrozenModel):
    """How many minutes a run may take and how many tokens it may use; 0 is no limit."""

    max_minutes: float
    max_tokens: int


class LimitReached(FrozenModel):
    """A run past its limits: the seconds it had worked and the tokens it had used when that was seen."""

    seconds: float
    tokens: int
    limits: RunLimits


def limits_of(config: AgentConfig, mode: RunMode) -> RunLimits | None:
    """The limits of a run in ``mode``, or ``None`` when it has none: a chat turn ends with a reply and has no finish to
    end with, and both limits at 0 bound nothing."""
    if mode == "chat" or (config.run_max_minutes == 0 and config.run_max_tokens == 0):
        return None
    return RunLimits(max_minutes=config.run_max_minutes, max_tokens=config.run_max_tokens)


def limit_reached(limits: RunLimits, seconds: float, tokens: int) -> LimitReached | None:
    """What a run that has worked ``seconds`` and used ``tokens`` reached of ``limits``, or ``None`` while it is inside
    them. A limit is reached once the run has spent all of it."""
    over_time: bool = limits.max_minutes > 0 and seconds >= limits.max_minutes * _SECONDS_PER_MINUTE
    over_tokens: bool = limits.max_tokens > 0 and tokens >= limits.max_tokens
    if not (over_time or over_tokens):
        return None
    return LimitReached(seconds=seconds, tokens=tokens, limits=limits)


def limit_spent(reached: LimitReached) -> str:
    """What the run spent of each limit that is set, e.g. ``61.2 of 60 minutes and 512,000 of 500,000 tokens``."""
    limits: RunLimits = reached.limits
    parts: list[str] = []
    if limits.max_minutes > 0:
        parts.append(f"{reached.seconds / _SECONDS_PER_MINUTE:.1f} of {limits.max_minutes:g} minutes")
    if limits.max_tokens > 0:
        parts.append(f"{reached.tokens:,} of {limits.max_tokens:,} tokens")
    return " and ".join(parts)


def limit_note(reached: LimitReached) -> str:
    """What the model is told at the first step past a limit."""
    return (
        f"[system note] The limit of this run is reached: it has used {limit_spent(reached)}. Finish now, in this "
        "reply: call finish with what was reached (the numbers), what is missing and how to go on; success is true "
        "only if the goal or its targets are met and verified. To close the steps of your plan, call the plan tools "
        "in this reply before finish: a call after finish does not run. No other tool runs any more, and the run "
        "ends after this reply."
    )


def limit_result(reached: LimitReached, ledger: RunLedger, plan: str, reply: str) -> str:
    """What a run ends with when it did not finish after it was told that its limit is reached: the limit, the text of
    its last reply when it wrote one, then the account of :func:`dotagent.agent.progress.stop_account`."""
    told: str = f"{PREFIX_LIMIT} ({limit_spent(reached)}) and did not finish when it was told to."
    said: str = reply.strip()
    head: str = f"{told}\n\nIts last reply:\n{truncate_middle(said, _REPLY_CHARS)}" if said else told
    return stop_account(head, ledger, plan)


def limit_event_data(reached: LimitReached) -> JsonObject:
    """The data of the ``limit_reached`` event: what the run had spent, and its limits (0: none)."""
    return {
        "seconds": round(reached.seconds, 3),
        "tokens": reached.tokens,
        "max_minutes": reached.limits.max_minutes,
        "max_tokens": reached.limits.max_tokens,
    }
