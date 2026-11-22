"""Async retry for external calls: warn on each failure, raise the last error when exhausted."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)


# How far above a server's own hint the pause may reach, so that callers told the same second do not come back together.
_HINT_JITTER: float = 0.2


class RetryPolicy(FrozenModel):
    """``attempts`` tries with a pause of ``base_delay_s`` doubling up to ``max_delay_s`` between them. ``deadline_s``,
    when set, is the time after which no pause is begun: a pause that would end after it is not taken, no retry
    follows, and the error that came is raised (item 1306). A try that began in time is not cut short by it: its own
    timeout bounds it."""

    attempts: int
    base_delay_s: float
    max_delay_s: float
    deadline_s: float | None = None


def hinted_delay(hint_s: float, policy: RetryPolicy) -> float:
    """The pause for a server's ``hint_s``: within the policy's base and longest delays, a little longer by chance. A
    hint of 0 is the base delay, not an instant retry; one above the longest delay is the longest."""
    least: float = min(policy.base_delay_s, policy.max_delay_s)
    held: float = min(max(hint_s, least), policy.max_delay_s)
    return min(policy.max_delay_s, held * (1.0 + _HINT_JITTER * random.random()))


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    is_retryable: Callable[[Exception], bool],
    delay_hint_s: Callable[[Exception], float | None],
    policy: RetryPolicy,
    label: str,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``operation`` up to ``policy.attempts`` times.

    Retryable failures are logged as warnings and followed by an exponential, jittered
    delay (or the server's own hint, held within the policy's delays: :func:`hinted_delay`).
    Non-retryable errors and the error of the final attempt propagate unchanged, and so does
    the error of a try whose pause would end past ``policy.deadline_s`` (the next try is then not started; a try that
    did start is not cut short).
    """
    started: float = clock()
    for attempt in range(1, policy.attempts):
        try:
            return await operation()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            hint: float | None = delay_hint_s(exc)
            backoff: float = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt - 1)))
            delay: float = hinted_delay(hint, policy) if hint is not None else backoff * (0.5 + random.random() / 2)
            if policy.deadline_s is not None and clock() - started + delay > policy.deadline_s:
                log.warning(
                    "retry_deadline_reached",
                    label=label,
                    attempt=attempt,
                    deadline_s=policy.deadline_s,
                    error_type=type(exc).__name__,
                )
                raise
            log.warning(
                "retrying_after_failure",
                label=label,
                attempt=attempt,
                of=policy.attempts,
                delay_s=round(delay, 2),
                error_type=type(exc).__name__,
                error=str(exc)[:400],
            )
            await sleep(delay)
    return await operation()
