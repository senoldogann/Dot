"""Activity: everything in motion in one list (item 47). Three sections, read from the live runs, the approvals and
the schedules: what waits for the user, what works now, and what ended today. The sidebar shows the first two above
the chats; the Activity page shows all three."""

import time
from datetime import datetime

from dotagent.agent.manager import RunManager
from dotagent.server.views import ActivityRow, ActivityView
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, CampaignRecord, EventRecord, RunRecord, ScheduleRecord

# The channel of a run is that of its latest tool result; a run without one has worked in no channel yet.
_LAST_RESULT: int = 1
# A schedule that fires within this many seconds is listed among the working rows, with when.
_DUE_WITHIN_S: float = 3600.0
# How many of the newest runs are read for the ones that ended today.
_RECENT_RUNS: int = 60
_TITLE_CHARS: int = 120
_ENDED: frozenset[str] = frozenset({"done", "failed", "cancelled"})


def _title(text: str) -> str:
    first: str = text.strip().splitlines()[0] if text.strip() else "Untitled"
    return first if len(first) <= _TITLE_CHARS else f"{first[: _TITLE_CHARS - 1]}…"


def _start_of_today(now: float) -> float:
    today: datetime = datetime.fromtimestamp(now).astimezone()
    return today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


async def _channel(store: Store, run_id: str) -> str:
    results: list[EventRecord] = await store.events_of_kind(run_id, "tool_result", _LAST_RESULT)
    if not results:
        return ""
    channel: object = results[-1].data.get("channel")
    return channel if isinstance(channel, str) else ""


def _approval_row(approval: ApprovalRecord, run: RunRecord, kind: str, detail: str) -> ActivityRow:
    return ActivityRow(
        id=approval.id,
        section="waiting",
        kind=kind,
        title=_title(approval.summary),
        detail=detail,
        channel="",
        since=approval.created_at,
        href=f"#/runs/{run.id}",
        run_id=run.id,
        status=run.status,
    )


async def _run_row(store: Store, run: RunRecord, section: str, kind: str, detail: str, title: str) -> ActivityRow:
    return ActivityRow(
        id=run.id,
        section=section,
        kind=kind,
        title=title,
        detail=detail,
        channel=await _channel(store, run.id),
        since=run.created_at if run.finished_at is None else run.finished_at,
        href=f"#/runs/{run.id}",
        run_id=run.id,
        status=run.status,
    )


async def _working_row(store: Store, run: RunRecord) -> ActivityRow:
    """A run that works now: a session of a campaign, a run delegated by another, or a run of its own."""
    if run.campaign_id is not None:
        campaign: CampaignRecord = await store.get_campaign(run.campaign_id)
        return await _run_row(store, run, "working", "campaign", "a session of the campaign", _title(campaign.title))
    if run.parent_run_id is not None:
        return await _run_row(store, run, "working", "delegated", "delegated by another run", _title(run.goal))
    return await _run_row(store, run, "working", "run", "", _title(run.goal))


def _schedule_row(schedule: ScheduleRecord, now: float) -> ActivityRow:
    due: float = schedule.next_run_at if schedule.next_run_at is not None else now
    minutes: int = max(0, round((due - now) / 60))
    return ActivityRow(
        id=schedule.id,
        section="working",
        kind="schedule",
        title=_title(schedule.name),
        detail="fires now" if minutes == 0 else f"fires in {minutes} min",
        channel="",
        since=due,
        href="#/settings/schedules",
        run_id=None,
        status="scheduled",
    )


async def activity_view(store: Store, manager: RunManager, now: float) -> ActivityView:
    """The three sections at ``now``: waiting (the approvals and questions runs wait for, the questions runs left for
    the user, the runs the user holds),
    working (the live runs that wait for nothing, the schedules due within the hour), recent (the runs that ended
    today, newest first)."""
    waiting: list[ActivityRow] = []
    working: list[ActivityRow] = []
    live: dict[str, RunRecord] = {}
    for run_id in manager.active_run_ids():
        live[run_id] = await store.get_run(run_id)
    covered: set[str] = set()
    for approval in await store.list_approvals("pending", _RECENT_RUNS):
        run: RunRecord | None = live.get(approval.run_id)
        if run is None:
            continue
        kind: str = "question" if approval.severity == "question" else "approval"
        waiting.append(_approval_row(approval, run, kind, "waits for your answer"))
        covered.add(run.id)
    for question in await store.list_approvals("left", _RECENT_RUNS):
        asked: RunRecord = live.get(question.run_id) or await store.get_run(question.run_id)
        waiting.append(_approval_row(question, asked, "question", "left for you; answering carries the work on"))
    for run in live.values():
        if manager.held(run.id):
            waiting.append(await _run_row(store, run, "waiting", "takeover", "you have its browser", _title(run.goal)))
            covered.add(run.id)
    for run in live.values():
        if run.id not in covered:
            working.append(await _working_row(store, run))
    for schedule in await store.list_schedules():
        if schedule.enabled and schedule.next_run_at is not None and schedule.next_run_at - now <= _DUE_WITHIN_S:
            working.append(_schedule_row(schedule, now))
    recent: list[ActivityRow] = []
    today: float = _start_of_today(now)
    for run in await store.list_runs(_RECENT_RUNS, None):
        if run.status in _ENDED and run.finished_at is not None and run.finished_at >= today:
            recent.append(await _run_row(store, run, "recent", "run", "", _title(run.goal)))
    waiting.sort(key=lambda row: row.since)
    working.sort(key=lambda row: row.since)
    recent.sort(key=lambda row: row.since, reverse=True)
    return ActivityView(waiting=waiting, working=working, recent=recent, generated_at=now)


async def current_activity(store: Store, manager: RunManager) -> ActivityView:
    return await activity_view(store, manager, time.time())
