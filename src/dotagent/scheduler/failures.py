"""Firings that fail (the code review's O3-O6): each schedule keeps how many of its firings failed in a row since one
started its run, and the last one's error (secrets masked), so a schedule that keeps failing is seen in ``dotx stats``
rather than tried again at every tick in silence. The record lives in the store's key/value table and is written in
one step no other write interleaves; a firing whose run starts clears its schedule's part of it.
"""

from pydantic import ValidationError

from dotagent.core.errors import StoreError
from dotagent.core.text import redact_secrets, truncate_tail
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

SCHEDULE_FAILURES_KEY: str = "schedule_failures"
_ERROR_CHARS: int = 300
# A one-time schedule stays due when its run does not start (item 351), and is tried again after a wait that doubles
# with each failure in a row, from a minute up to an hour; at every tick it made and dropped a chat and wrote the store
# again, some 5,760 times a day. The doubling stops at the cap.
_RETRY_FIRST_S: float = 60.0
_RETRY_MOST_S: float = 3600.0
_RETRY_DOUBLINGS: int = 6


class FiringFailure(FrozenModel):
    """One schedule's firings that failed in a row: how many, and when the last one failed and with what."""

    failures_in_row: int
    last_failure_at: float
    last_error: str


class FiringFailures(FrozenModel):
    """The record: the schedules whose last firing failed, by id."""

    schedules: dict[str, FiringFailure]


NO_FAILURES: FiringFailures = FiringFailures(schedules={})


def with_failure(failures: FiringFailures, schedule_id: str, at: float, error: str) -> FiringFailures:
    """``failures`` once a firing of ``schedule_id`` failed at ``at`` with ``error``: one more in a row."""
    before: FiringFailure | None = failures.schedules.get(schedule_id)
    after: FiringFailure = FiringFailure(
        failures_in_row=1 if before is None else before.failures_in_row + 1,
        last_failure_at=at,
        last_error=truncate_tail(redact_secrets(error), _ERROR_CHARS),
    )
    return FiringFailures(schedules={**failures.schedules, schedule_id: after})


def retry_after(failure: FiringFailure) -> float:
    """When a one-time schedule whose firings failed ``failure.failures_in_row`` times in a row is tried again: a
    minute after the first failure, twice as long after each next one, an hour at the most."""
    doublings: int = min(failure.failures_in_row - 1, _RETRY_DOUBLINGS)
    wait: float = min(_RETRY_FIRST_S * 2.0**doublings, _RETRY_MOST_S)
    return failure.last_failure_at + wait


def without_failure(failures: FiringFailures, schedule_id: str) -> FiringFailures:
    """``failures`` once a firing of ``schedule_id`` started its run."""
    return FiringFailures(schedules={key: value for key, value in failures.schedules.items() if key != schedule_id})


def parse_failures(raw: str | None) -> FiringFailures:
    """The record kept in the store, empty before the first failure. ``StoreError`` for one that cannot be read."""
    if raw is None:
        return NO_FAILURES
    try:
        return FiringFailures.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError(
            "the record of failed firings cannot be read", {"key": SCHEDULE_FAILURES_KEY, "error": str(exc)}
        ) from exc


async def record_firing_failure(store: Store, schedule_id: str, at: float, error: str) -> FiringFailure:
    """Count a firing of ``schedule_id`` that failed at ``at`` with ``error``; returns the schedule's failures now."""
    written: str = await store.kv_update(
        SCHEDULE_FAILURES_KEY,
        lambda raw: with_failure(parse_failures(raw), schedule_id, at, error).model_dump_json(),
    )
    return parse_failures(written).schedules[schedule_id]


async def firing_failure(store: Store, schedule_id: str) -> FiringFailure | None:
    """The firings of ``schedule_id`` that failed in a row, ``None`` when its last firing started its run."""
    return parse_failures(await store.kv_get(SCHEDULE_FAILURES_KEY)).schedules.get(schedule_id)


async def record_firing_started(store: Store, schedule_id: str) -> None:
    """Clear the failures of ``schedule_id``, whose firing started its run; nothing is written while it has none."""
    if schedule_id not in parse_failures(await store.kv_get(SCHEDULE_FAILURES_KEY)).schedules:
        return
    await store.kv_update(
        SCHEDULE_FAILURES_KEY, lambda raw: without_failure(parse_failures(raw), schedule_id).model_dump_json()
    )
