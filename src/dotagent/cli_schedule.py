"""``dotx schedule``: see, create, delete, switch on and switch off the schedules that the Dot daemon runs."""

import asyncio
import contextlib
import sys
import time
from collections.abc import AsyncIterator
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from dotagent.config import AppConfig, load_user_config
from dotagent.core.paths import db_path, screenshots_dir
from dotagent.core.types import FrozenModel
from dotagent.scheduler import DAEMON_NOTE
from dotagent.scheduler.plans import (
    UNSET_OPTIONS,
    ScheduleRequest,
    create_schedule,
    disable_schedule,
    enable_schedule,
    remove_schedule,
    render_schedule,
    render_schedule_entry,
    render_schedule_header,
    schedule_order,
    schedule_zone,
)
from dotagent.scheduler.timing import resolve_zone
from dotagent.store.db import Store
from dotagent.store.models import ScheduleRecord


class ScheduleListCommand(FrozenModel):
    name: Literal["schedule-list"]


class ScheduleAddCommand(FrozenModel):
    name: Literal["schedule-add"]
    title: str = Field(min_length=1, max_length=80, pattern=r"\S")
    cron: str | None
    at: str | None
    mode: Literal["task", "research"]
    goal: list[str] = Field(min_length=1)


class ScheduleDeleteCommand(FrozenModel):
    name: Literal["schedule-delete"]
    schedule_id: str


class ScheduleEnableCommand(FrozenModel):
    name: Literal["schedule-enable"]
    schedule_id: str


class ScheduleDisableCommand(FrozenModel):
    name: Literal["schedule-disable"]
    schedule_id: str


@contextlib.asynccontextmanager
async def store_alone() -> AsyncIterator[Store]:
    """The store alone, opened without the rest of the runtime and closed afterwards."""
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        yield store
    finally:
        await store.close()


async def local_zone(config: AppConfig) -> ZoneInfo:
    return await asyncio.to_thread(resolve_zone, config.scheduler.timezone)


async def cmd_schedule_list(command: ScheduleListCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        records: list[ScheduleRecord] = sorted(await store.list_schedules(), key=schedule_order)
    for record in records:
        sys.stdout.write(f"{render_schedule_entry(record, await schedule_zone(record.options, zone))}\n")
    sys.stderr.write(f"{render_schedule_header(len(records), zone)}\n")
    return 0


async def cmd_schedule_add(command: ScheduleAddCommand) -> int:
    config: AppConfig = load_user_config()
    zone: ZoneInfo = await local_zone(config)
    request: ScheduleRequest = ScheduleRequest(
        name=command.title,
        goal=" ".join(command.goal),
        cron=command.cron,
        at=command.at,
        mode=command.mode,
        options=UNSET_OPTIONS,
    )
    async with store_alone() as store:
        record: ScheduleRecord = await create_schedule(store, config.scheduler, zone, request, time.time())
    sys.stdout.write(f"Scheduled: {render_schedule(record, await schedule_zone(record.options, zone))}\n")
    sys.stderr.write(f"{DAEMON_NOTE}\n")
    return 0


async def cmd_schedule_delete(command: ScheduleDeleteCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        record: ScheduleRecord = await remove_schedule(store, command.schedule_id)
    sys.stdout.write(f"Deleted: {render_schedule(record, await schedule_zone(record.options, zone))}\n")
    return 0


async def cmd_schedule_enable(command: ScheduleEnableCommand) -> int:
    config: AppConfig = load_user_config()
    zone: ZoneInfo = await local_zone(config)
    async with store_alone() as store:
        record: ScheduleRecord = await enable_schedule(store, config.scheduler, zone, command.schedule_id, time.time())
    sys.stdout.write(f"Switched on: {render_schedule(record, await schedule_zone(record.options, zone))}\n")
    sys.stderr.write(f"{DAEMON_NOTE}\n")
    return 0


async def cmd_schedule_disable(command: ScheduleDisableCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        record: ScheduleRecord = await disable_schedule(store, command.schedule_id)
    sys.stdout.write(f"Switched off: {render_schedule(record, await schedule_zone(record.options, zone))}\n")
    return 0
