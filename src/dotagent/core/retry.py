"""Async retry for external calls: warn on each failure, raise the last error when exhausted."""

import asyncio
import random
from collections.abc import Awaitable, Callable

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)


class RetryPolicy(FrozenModel):
    attempts: int
    base_delay_s: float
    max_delay_s: float


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    is_retryable: Callable[[Exception], bool],
    delay_hint_s: Callable[[Exception], float | None],
    policy: RetryPolicy,
    label: str,
) -> T:
    """Run ``operation`` up to ``policy.attempts`` times.

    Retryable failures are logged as warnings and followed by an exponential, jittered
    delay (or the server's own hint). Non-retryable errors and the error of the final
    attempt propagate unchanged.
    """
    for attempt in range(1, policy.attempts):
        try:
            return await operation()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            hint: float | None = delay_hint_s(exc)
            backoff: float = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt - 1)))
            delay: float = hint if hint is not None else backoff * (0.5 + random.random() / 2)
            log.warning(
                "retrying_after_failure",
                label=label,
                attempt=attempt,
                of=policy.attempts,
                delay_s=round(delay, 2),
                error_type=type(exc).__name__,
                error=str(exc)[:400],
            )
            await asyncio.sleep(delay)
    return await operation()
