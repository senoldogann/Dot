"""When the nightly step runs (item 721, docs/harness.md): a time of day, or "off", set on the Self-improvement page and
kept in Dot's store, which wins over ``[harness] step_time`` of the settings file; with none set on the page, the
setting is read as it was. The page said "set a time of day in the settings file", and nothing on it could."""

from typing import Literal

from pydantic import Field

from dotagent.config import HarnessConfig
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

STEP_TIME_KEY: str = "harness:step_time"
STEP_OFF: str = "off"
# A time of day as the settings file takes it ("04:10", 24-hour), or "off".
STEP_TIME_PATTERN: str = r"^(?:off|(?:[01]\d|2[0-3]):[0-5]\d)$"


class StepTimeRequest(FrozenModel):
    """``PUT /api/harness/schedule``: when the nightly step runs, a time of day ("04:10") or "off"."""

    step_time: str = Field(pattern=STEP_TIME_PATTERN)


class StepSchedule(FrozenModel):
    """When the nightly step runs, and whether the page set it (``page``) or the settings file did (``settings``)."""

    step_time: str
    source: Literal["page", "settings"]


def schedule_from(kept: str | None, config: HarnessConfig) -> StepSchedule:
    """The schedule from what the store keeps (``None``: nothing, or a value no Dot wrote, which is passed over) and
    from the settings file."""
    if kept is not None and _valid(kept):
        return StepSchedule(step_time=kept, source="page")
    return StepSchedule(step_time=config.step_time, source="settings")


def _valid(value: str) -> bool:
    try:
        StepTimeRequest(step_time=value)
    except ValueError:
        return False
    return True


async def schedule_of(store: Store, config: HarnessConfig) -> StepSchedule:
    return schedule_from(await store.kv_get(STEP_TIME_KEY), config)


async def set_schedule(store: Store, config: HarnessConfig, step_time: str) -> StepSchedule:
    """Keep ``step_time`` (checked by :class:`StepTimeRequest`) as the page's choice, and give the schedule now."""
    await store.kv_set(STEP_TIME_KEY, StepTimeRequest(step_time=step_time).step_time)
    return await schedule_of(store, config)
