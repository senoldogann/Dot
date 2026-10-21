"""Creating, changing, switching and describing schedules. The store holds all of their state."""

import asyncio
from typing import Literal
from zoneinfo import ZoneInfo

from dotagent.config import SchedulerConfig
from dotagent.core.errors import StoreError, ToolExecutionError, ToolInputError
from dotagent.core.types import FrozenModel
from dotagent.llm.router import parse_model_ref
from dotagent.scheduler import DAEMON_NOTE
from dotagent.scheduler.timing import (
    Timing,
    format_local,
    local_moment,
    named_zone,
    next_firing,
    plan_cron,
    plan_timing,
)
from dotagent.store.db import Store
from dotagent.store.models import ScheduleOptions, ScheduleRecord

_GOAL_CHARS: int = 160
# Options that leave every choice to the configuration at firing time.
UNSET_OPTIONS: ScheduleOptions = ScheduleOptions(
    model=None,
    effort=None,
    speed=None,
    approvals=None,
    goal_mode=None,
    timezone=None,
    history_runs=None,
    same_chat=None,
    stop_after_failures=None,
)


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


class ScheduleRequest(FrozenModel):
    """A schedule to create or the new state of one: ``goal`` runs in ``mode`` once at ``at``, or repeatedly on
    ``cron``, started with ``options``. ``at`` and ``cron`` are read in the schedule's own time zone."""

    name: str
    goal: str
    cron: str | None
    at: str | None
    mode: Literal["task", "research"]
    options: ScheduleOptions


async def schedule_zone(options: ScheduleOptions, fallback: ZoneInfo) -> ZoneInfo:
    """The zone a schedule's times are read in: its own, or ``fallback`` (the configured one) when it has none."""
    if options.timezone is None:
        return fallback
    return await asyncio.to_thread(named_zone, options.timezone)


def _check_options(options: ScheduleOptions) -> None:
    """Refuse options that could never start a run, so a mistake shows when the schedule is saved, not when it fires."""
    if options.model is not None:
        parse_model_ref(options.model)
    if options.timezone is not None and options.timezone.strip() == "":
        raise ToolInputError("a time zone cannot be blank; leave it out to use the configured one", {})


async def _ensure_room(store: Store, config: SchedulerConfig) -> None:
    switched_on: int = sum(1 for record in await store.list_schedules() if record.enabled)
    if switched_on >= config.max_schedules:
        raise ToolExecutionError(
            "too many schedules are switched on; delete or switch one off first",
            {"switched_on": switched_on, "limit": config.max_schedules},
        )


def _upcoming(record: ScheduleRecord, zone: ZoneInfo, now: float) -> float | None:
    """The next run that a schedule being switched on would wait for."""
    if record.cron is not None:
        return next_firing(record.cron, now, zone)
    if record.run_at is not None and record.run_at > now:
        return record.run_at
    return None


async def plan_schedule(
    store: Store, config: SchedulerConfig, fallback_zone: ZoneInfo, request: ScheduleRequest, now: float
) -> Timing:
    """How the new schedule ``request`` would fire, asked without saving it, or what saving it refuses: options that
    could never start a run, a time it cannot keep (a moment past, a gap under ``[scheduler] min_interval_s``), no room
    for one more switched on. A new chat's loop asks it before its run starts, so that a refusal starts nothing (item
    400)."""
    _check_options(request.options)
    zone: ZoneInfo = await schedule_zone(request.options, fallback_zone)
    timing: Timing = plan_timing(request.cron, request.at, zone, config.min_interval_s, now)
    await _ensure_room(store, config)
    return timing


async def create_schedule(
    store: Store,
    config: SchedulerConfig,
    fallback_zone: ZoneInfo,
    request: ScheduleRequest,
    now: float,
    thread_id: str | None,
) -> ScheduleRecord:
    """Save a new schedule, unless :func:`plan_schedule` refuses it; ``thread_id`` is the chat it goes on in, for a
    schedule made in a chat (item 291)."""
    timing: Timing = await plan_schedule(store, config, fallback_zone, request, now)
    return await store.add_schedule(
        request.name,
        timing.cron,
        timing.run_at,
        request.goal,
        request.mode,
        timing.first_run,
        request.options,
        thread_id,
    )


class _EditedTiming(FrozenModel):
    cron: str | None
    run_at: float | None
    upcoming: float | None


def _edited_timing(
    record: ScheduleRecord, request: ScheduleRequest, zone: ZoneInfo, config: SchedulerConfig, now: float
) -> _EditedTiming:
    """How an edited schedule fires. A one-time moment left as it was may lie in the past (the schedule then has no run
    left); a new one has to be in the future."""
    if request.cron is not None and request.at is not None:
        raise ToolInputError("give either cron or at, not both", {"cron": request.cron, "at": request.at})
    if request.cron is not None:
        planned: Timing = plan_cron(request.cron, zone, config.min_interval_s, now)
        return _EditedTiming(cron=planned.cron, run_at=None, upcoming=planned.first_run)
    if request.at is None:
        raise ToolInputError("give cron for a recurring schedule or at for a single run", {})
    moment: float = local_moment(request.at, zone)
    if moment != record.run_at and moment <= now:
        raise ToolInputError("'at' is not in the future", {"at": request.at, "now": format_local(now, zone)})
    return _EditedTiming(cron=None, run_at=moment, upcoming=moment if moment > now else None)


async def edit_schedule(
    store: Store,
    config: SchedulerConfig,
    fallback_zone: ZoneInfo,
    schedule_id: str,
    request: ScheduleRequest,
    enabled: bool,
    now: float,
) -> ScheduleRecord:
    """Change everything about a schedule but its history. Switched on, it waits for its next firing under the new
    timing; a schedule switched on that has no firing left is refused."""
    record: ScheduleRecord = await store.get_schedule(schedule_id)
    _check_options(request.options)
    zone: ZoneInfo = await schedule_zone(request.options, fallback_zone)
    timing: _EditedTiming = _edited_timing(record, request, zone, config, now)
    if enabled and timing.upcoming is None:
        raise ToolExecutionError(
            "this schedule has no run left to wait for; give it a new time or leave it switched off",
            {"schedule_id": schedule_id},
        )
    if enabled and not record.enabled:
        await _ensure_room(store, config)
    return await store.update_schedule(
        schedule_id,
        request.name,
        timing.cron,
        timing.run_at,
        request.goal,
        request.mode,
        request.options,
        enabled,
        timing.upcoming if enabled else None,
    )


async def remove_schedule(store: Store, schedule_id: str) -> ScheduleRecord:
    """Delete a schedule; returns what was deleted. Its past threads stay."""
    record: ScheduleRecord = await store.get_schedule(schedule_id)
    await store.delete_schedule(schedule_id)
    return record


async def disable_schedule(store: Store, schedule_id: str) -> ScheduleRecord:
    await store.get_schedule(schedule_id)
    await store.set_schedule_enabled(schedule_id, False, None)
    return await store.get_schedule(schedule_id)


async def enable_schedule(
    store: Store, config: SchedulerConfig, fallback_zone: ZoneInfo, schedule_id: str, now: float
) -> ScheduleRecord:
    record: ScheduleRecord = await store.get_schedule(schedule_id)
    if record.enabled:
        return record
    await _ensure_room(store, config)
    upcoming: float | None = _upcoming(record, await schedule_zone(record.options, fallback_zone), now)
    if upcoming is None:
        raise ToolExecutionError(
            "this schedule has no run left to wait for; create a new one", {"schedule_id": schedule_id}
        )
    await store.set_schedule_enabled(schedule_id, True, upcoming)
    return await store.get_schedule(schedule_id)


def schedule_timing(record: ScheduleRecord, zone: ZoneInfo) -> str:
    """When a schedule fires: its cron expression, or the one time it runs."""
    if record.cron is not None:
        return f"cron {record.cron}"
    if record.run_at is not None:
        return f"once, {format_local(record.run_at, zone)}"
    raise StoreError("a schedule has neither a cron expression nor a time", {"schedule_id": record.id})


def options_text(record: ScheduleRecord) -> str:
    """The mode and the start options a schedule sets, for example ``task, approvals yolo, goal mode``."""
    options: ScheduleOptions = record.options
    parts: list[str] = [record.mode]
    if options.approvals is not None:
        parts.append(f"approvals {options.approvals}")
    if options.goal_mode is not None:
        parts.append("goal mode" if options.goal_mode else "no goal mode")
    if options.model is not None:
        parts.append(f"model {options.model}")
    if options.effort is not None:
        parts.append(f"reasoning {options.effort}")
    return ", ".join(parts)


def render_schedule(record: ScheduleRecord, zone: ZoneInfo) -> str:
    """One line: id, name, timing, mode and options, what is next and how the last run went. ``zone`` is the
    schedule's own zone (see :func:`schedule_zone`)."""
    upcoming: str = (
        f"next {format_local(record.next_run_at, zone)}"
        if record.enabled and record.next_run_at is not None
        else "switched off"
    )
    if record.last_run_at is not None:
        last: str = f"last {format_local(record.last_run_at, zone)}: {record.last_status}"
    else:
        last = f"last: {record.last_status}" if record.last_status != "" else "never ran"
    return (
        f'{record.id} "{record.name}" · {schedule_timing(record, zone)} ({zone.key}) · {options_text(record)} · '
        f"{upcoming} · {last}"
    )


def render_schedule_entry(record: ScheduleRecord, zone: ZoneInfo) -> str:
    """Two lines: the schedule as ``render_schedule`` shows it, and what it will do."""
    return f"- {render_schedule(record, zone)}\n  goal: {_clip(record.goal, _GOAL_CHARS)}"


def render_schedule_header(count: int, zone: ZoneInfo) -> str:
    """The line above a listing: how many schedules there are, the zone their times are in and the daemon note."""
    noun: str = "schedule" if count == 1 else "schedules"
    return f"{count} {noun}, times in {zone.key}. {DAEMON_NOTE}"


def schedule_order(record: ScheduleRecord) -> tuple[int, float]:
    """Sort key: switched-on schedules first, soonest first; then the others by age."""
    if record.enabled and record.next_run_at is not None:
        return 0, record.next_run_at
    return 1, record.created_at
