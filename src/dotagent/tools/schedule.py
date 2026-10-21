"""Schedule tools: run a goal later, or on a cron expression, without anyone being there.

A schedule is configuration that starts runs by itself, so creating and deleting one carries the ``persistence``
flag: the policy asks for approval once the run has read untrusted content. Switching a schedule on or off is for the
user (``dotx schedule``).
"""

import time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from dotagent.policy.types import ActionInfo, Risk
from dotagent.scheduler import DAEMON_NOTE
from dotagent.scheduler.plans import (
    ScheduleRequest,
    create_schedule,
    remove_schedule,
    render_schedule,
    render_schedule_entry,
    render_schedule_header,
    schedule_order,
    schedule_zone,
)
from dotagent.scheduler.timing import Timing, describe_timing, plan_timing
from dotagent.store.db import Store
from dotagent.store.models import ChatChoices, RunMode, RunRecord, ScheduleOptions, ScheduleRecord
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    make_live_tool,
    make_tool,
    ok,
)
from dotagent.tools.context import ToolContext

_TIMEOUT_S: float = 30.0
_SUMMARY_CHARS: int = 600


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _action(tool: str, summary: str, risk: Risk) -> ActionInfo:
    return ActionInfo(
        tool=tool,
        kind="schedule",
        risk=risk,
        summary=_clip(summary, _SUMMARY_CHARS),
        target="schedules",
        flags=("persistence",),
    )


class CreateArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=80, description="A short label, for example 'Weekday news digest'.")
    goal: str = Field(
        min_length=10,
        max_length=4000,
        description=(
            "The complete task for the future run. It goes on in this chat, with this chat's model, reasoning level, "
            "speed, approvals and goal mode, without anyone watching, so it must say everything it needs: what to do, "
            "where to look, how to report."
        ),
    )
    cron: str | None = Field(
        default=None,
        description=(
            "For a recurring schedule: five fields (minute hour day-of-month month day-of-week) in this Mac's local "
            "time, for example '0 9 * * 1-5' for 09:00 on weekdays. A schedule may not repeat more often than every "
            "5 minutes by default."
        ),
    )
    at: str | None = Field(
        default=None,
        description="For a single run: the local date and time, for example '2026-10-06T09:30'. Give cron or at.",
    )
    mode: Literal["task", "research"] | None = Field(
        default=None,
        description=(
            "task may act on the Mac and the web; research only reads and reports. Leave it out to keep this chat's."
        ),
    )


def schedule_mode(asked: Literal["task", "research"] | None, chat: RunMode) -> Literal["task", "research"]:
    """The mode of a schedule a chat makes: the one asked, else the chat's own; a chat that only talks schedules
    tasks, which is what a schedule's run can be."""
    if asked is not None:
        return asked
    return "research" if chat == "research" else "task"


def chat_options(run: RunRecord, choices: ChatChoices | None) -> ScheduleOptions:
    """How the runs of a schedule that the run ``run`` makes start (item 291): as the next follow-up of its chat starts,
    ``choices`` (those its first run started with, until the owner picked others), else as ``run`` started, its chat's
    runs being older than kept choices; and in that chat, as its agent (item 463), within that agent's bounds, or as
    Dot. They are the owner's choices, never the model's: the model names no agent."""
    if choices is None:
        return ScheduleOptions(
            model=run.model,
            effort=run.effort,
            speed=run.speed,
            approvals=run.approvals,
            goal_mode=run.goal_mode,
            timezone=None,
            history_runs=None,
            same_chat=True,
            stop_after_failures=None,
            notify=None,
            agent_id=run.agent_id,
        )
    return ScheduleOptions(
        model=choices.model,
        effort=choices.effort,
        speed=choices.speed,
        approvals=choices.approvals,
        goal_mode=choices.goal_mode,
        timezone=None,
        history_runs=None,
        same_chat=True,
        stop_after_failures=None,
        notify=None,
        agent_id=run.agent_id,
    )


def tool_timing(cron: str | None, at: str | None, ctx: ToolContext) -> Timing:
    """When the ``cron`` or the ``at`` a tool was given fires, in the Mac's time zone, held to the scheduler's
    ``min_interval_s``."""
    return plan_timing(cron, at, ctx.runtime.schedule_zone, ctx.runtime.config.scheduler.min_interval_s, time.time())


def _create_tool() -> RegisteredTool:
    def classify(args: CreateArgs, ctx: ToolContext) -> ActionInfo:
        when: str = describe_timing(tool_timing(args.cron, args.at, ctx), ctx.runtime.schedule_zone)
        mode: str = "this chat's mode" if args.mode is None else args.mode
        return _action("schedule_create", f'Schedule "{args.name}" ({when}, {mode}): {args.goal}', "medium")

    async def run(args: CreateArgs, ctx: ToolContext) -> ToolResult:
        zone: ZoneInfo = ctx.runtime.schedule_zone
        store: Store = ctx.runtime.store
        # A run cannot choose how unattended runs start (model, approvals, goal mode): they start as the owner's chat
        # does, in that chat (item 291); a helper's schedule is its parent's chat's.
        current: RunRecord = await store.get_run(ctx.run_id)
        owner: RunRecord = current if current.parent_run_id is None else await store.get_run(current.parent_run_id)
        choices: ChatChoices | None = await store.chat_choices(owner.thread_id)
        request: ScheduleRequest = ScheduleRequest(
            name=args.name,
            goal=args.goal,
            cron=args.cron,
            at=args.at,
            mode=schedule_mode(args.mode, owner.mode if choices is None else choices.mode),
            options=chat_options(owner, choices),
        )
        record: ScheduleRecord = await create_schedule(
            store, ctx.runtime.config.scheduler, zone, request, time.time(), owner.thread_id
        )
        return ok(
            f"Scheduled: {render_schedule(record, zone)}\nEach run goes on in this chat, with its model, reasoning "
            f"level, speed, approvals and goal mode. {DAEMON_NOTE}"
        )

    return make_tool(
        name="schedule_create",
        description=(
            "Run a goal later, once or repeatedly, without the user being present. Use it when the user asks for "
            "something to happen at a time or on a rhythm ('every weekday at 9', 'tomorrow at 18:30'). Times are the "
            "Mac's local time. Each run goes on in this chat with this chat's settings. Check schedule_list first so "
            "you do not create a duplicate."
        ),
        args_model=CreateArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class ListArgs(ToolArgs):
    """No arguments."""


def _list_tool() -> RegisteredTool:
    def classify(args: ListArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool="schedule_list", kind="meta", risk="safe", summary="List schedules", target="schedules", flags=()
        )

    async def run(args: ListArgs, ctx: ToolContext) -> ToolResult:
        zone: ZoneInfo = ctx.runtime.schedule_zone
        records: list[ScheduleRecord] = sorted(await ctx.runtime.store.list_schedules(), key=schedule_order)
        if not records:
            return ok(f"No schedules. {DAEMON_NOTE}")
        lines: list[str] = [render_schedule_header(len(records), zone)]
        lines.extend([render_schedule_entry(record, await schedule_zone(record.options, zone)) for record in records])
        return ok("\n".join(lines))

    return make_tool(
        name="schedule_list",
        description="List the schedules: what runs when, what is next and how the last run went.",
        args_model=ListArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class DeleteArgs(ToolArgs):
    schedule_id: str = Field(min_length=4, description="The id as shown by schedule_list, for example sch_1a2b3c4d5e.")


def _delete_tool() -> RegisteredTool:
    async def classify(args: DeleteArgs, ctx: ToolContext) -> ActionInfo:
        record: ScheduleRecord = await ctx.runtime.store.get_schedule(args.schedule_id)
        return _action("schedule_delete", f'Delete schedule "{record.name}" ({record.id})', "low")

    async def run(args: DeleteArgs, ctx: ToolContext) -> ToolResult:
        record: ScheduleRecord = await remove_schedule(ctx.runtime.store, args.schedule_id)
        own_zone: ZoneInfo = await schedule_zone(record.options, ctx.runtime.schedule_zone)
        return ok(f"Deleted: {render_schedule(record, own_zone)}")

    return make_live_tool(
        name="schedule_delete",
        description="Delete a schedule by its id, for example because it is no longer wanted or to replace it.",
        args_model=DeleteArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_schedule_tools() -> tuple[RegisteredTool, ...]:
    return (_create_tool(), _list_tool(), _delete_tool())
