"""Routes for what Dot keeps: rules for the policy, memories and schedules."""

import re
import time
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from dotagent.config import SchedulerConfig
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.memory.service import (
    MemoryHit,
    MemoryIndex,
    SavedMemory,
    forget_memory,
    recall,
    resting,
    save_memory,
    update_memory_note,
)
from dotagent.scheduler.plans import (
    ScheduleRequest,
    create_schedule,
    disable_schedule,
    edit_schedule,
    enable_schedule,
    remove_schedule,
    schedule_order,
    schedule_timing,
    schedule_zone,
)
from dotagent.scheduler.timing import format_local
from dotagent.server.context import ServerContext
from dotagent.server.views import (
    MemoriesQuery,
    MemoryPage,
    MemoryRequest,
    RuleEnabledRequest,
    RuleRequest,
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
    ScheduleView,
    SearchQuery,
)
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord, RuleRecord, ScheduleOptions, ScheduleRecord


def _checked_pattern(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise InvalidValueError(
            "the pattern is not a valid regular expression", {"pattern": pattern, "error": str(exc)}
        ) from exc
    return pattern


async def _rule(store: Store, rule_id: str) -> RuleRecord:
    for rule in await store.list_rules():
        if rule.id == rule_id:
            return rule
    raise NotFoundError("rule not found", {"rule_id": rule_id})


# How a ``datetime-local`` input writes a moment.
_INPUT_MOMENT: str = "%Y-%m-%dT%H:%M"


def _schedule_view(record: ScheduleRecord, zone: ZoneInfo) -> ScheduleView:
    return ScheduleView(
        record=record,
        when=schedule_timing(record, zone),
        next_local=None if record.next_run_at is None else format_local(record.next_run_at, zone),
        last_local=None if record.last_run_at is None else format_local(record.last_run_at, zone),
        run_at_input=None
        if record.run_at is None
        else datetime.fromtimestamp(record.run_at, tz=zone).strftime(_INPUT_MOMENT),
        zone=zone.key,
    )


def rules_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    @router.get("/rules")
    async def list_rules() -> list[RuleRecord]:
        return await store.list_rules()

    @router.post("/rules", status_code=201)
    async def add_rule(request: RuleRequest) -> RuleRecord:
        return await store.add_rule(
            request.behavior, request.tool_glob, _checked_pattern(request.match_regex), request.description
        )

    @router.patch("/rules/{rule_id}")
    async def set_rule_enabled(rule_id: str, request: RuleEnabledRequest) -> RuleRecord:
        await _rule(store, rule_id)
        await store.set_rule_enabled(rule_id, request.enabled)
        return await _rule(store, rule_id)

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str) -> RuleRecord:
        rule: RuleRecord = await _rule(store, rule_id)
        await store.delete_rule(rule_id)
        return rule

    return router


def memories_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    index: MemoryIndex = ctx.runtime.memory

    @router.get("/memories")
    async def list_memories(query: Annotated[MemoriesQuery, Query()]) -> MemoryPage:
        items: list[MemoryRecord] = await index.store.list_memories(query.limit, query.offset)
        now: float = time.time()
        return MemoryPage(
            total=await index.store.count_memories(),
            items=items,
            resting=[record.id for record in items if resting(record, now, index.config.rest_days)],
        )

    @router.get("/memories/search")
    async def search_memories(query: Annotated[SearchQuery, Query()]) -> list[MemoryHit]:
        return await recall(index, query.q, query.limit)

    @router.post("/memories", status_code=201)
    async def add_memory(request: MemoryRequest) -> SavedMemory:
        return await save_memory(index, request.content, request.tags, None)

    @router.patch("/memories/{memory_id}")
    async def edit_memory(memory_id: str, request: MemoryRequest) -> MemoryRecord:
        return await update_memory_note(index, memory_id, request.content, request.tags)

    @router.delete("/memories/{memory_id}")
    async def delete_memory(memory_id: str) -> MemoryRecord:
        return await forget_memory(index, memory_id)

    return router


def schedules_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store
    config: SchedulerConfig = ctx.runtime.config.scheduler
    zone: ZoneInfo = ctx.runtime.schedule_zone

    async def view_of(record: ScheduleRecord) -> ScheduleView:
        return _schedule_view(record, await schedule_zone(record.options, zone))

    @router.get("/schedules")
    async def list_schedules() -> list[ScheduleView]:
        return [await view_of(record) for record in sorted(await store.list_schedules(), key=schedule_order)]

    @router.get("/schedules/{schedule_id}")
    async def get_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await store.get_schedule(schedule_id))

    @router.post("/schedules", status_code=201)
    async def add_schedule(request: ScheduleCreateRequest) -> ScheduleView:
        plan: ScheduleRequest = ScheduleRequest(
            name=request.name,
            goal=request.goal,
            cron=request.cron,
            at=request.at,
            mode=request.mode,
            options=ScheduleOptions(
                model=request.model,
                effort=request.effort,
                approvals=request.approvals,
                goal_mode=request.goal_mode,
                timezone=request.timezone,
            ),
        )
        return await view_of(await create_schedule(store, config, zone, plan, time.time()))

    @router.patch("/schedules/{schedule_id}")
    async def change_schedule(schedule_id: str, request: ScheduleUpdateRequest) -> ScheduleView:
        plan: ScheduleRequest = ScheduleRequest(
            name=request.name,
            goal=request.goal,
            cron=request.cron,
            at=request.at,
            mode=request.mode,
            options=ScheduleOptions(
                model=request.model,
                effort=request.effort,
                approvals=request.approvals,
                goal_mode=request.goal_mode,
                timezone=request.timezone,
            ),
        )
        edited: ScheduleRecord = await edit_schedule(
            store, config, zone, schedule_id, plan, request.enabled, time.time()
        )
        return await view_of(edited)

    @router.post("/schedules/{schedule_id}/enable")
    async def switch_on_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await enable_schedule(store, config, zone, schedule_id, time.time()))

    @router.post("/schedules/{schedule_id}/disable")
    async def switch_off_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await disable_schedule(store, schedule_id))

    @router.delete("/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await remove_schedule(store, schedule_id))

    return router
