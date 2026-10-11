"""Activity: everything in motion in one list (item 47). Five sections, read from the live runs, the approvals, the
schedules, the campaigns and the notes: what waits for the user, what works now, what ended today, what was learned
today (the journal and the lessons, item 72) and what is due later today. The sidebar shows the first two above the
chats; the Activity page shows the first three; the home's Today strip shows all five."""

import time
from datetime import datetime

from dotagent.agent.lessons import LESSON_TAG
from dotagent.agent.manager import RunManager
from dotagent.agent.reflection import Reflection, last_reflection, summary_line
from dotagent.memory.service import JOURNAL_TAG
from dotagent.server.views import ActivityRow, ActivityView
from dotagent.store.db import Store
from dotagent.store.models import (
    ApprovalRecord,
    CampaignRecord,
    EventRecord,
    MemoryRecord,
    RunRecord,
    ScheduleRecord,
)

# The channel of a run is that of its latest tool result; a run without one has worked in no channel yet.
_LAST_RESULT: int = 1
# A schedule that fires within this many seconds is listed among the working rows, with when.
_DUE_WITHIN_S: float = 3600.0
# How many of the newest runs are read for the ones that ended today.
_RECENT_RUNS: int = 60
_TITLE_CHARS: int = 120
_ENDED: frozenset[str] = frozenset({"done", "failed", "cancelled"})
# How many lesson notes are read for the ones written today, and how many journal notes for today's.
_LESSONS_READ: int = 50
_JOURNALS_READ: int = 3


def _title(text: str) -> str:
    first: str = text.strip().splitlines()[0] if text.strip() else "Untitled"
    return first if len(first) <= _TITLE_CHARS else f"{first[: _TITLE_CHARS - 1]}…"


def _start_of_today(now: float) -> float:
    today: datetime = datetime.fromtimestamp(now).astimezone()
    return today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _end_of_today(now: float) -> float:
    return _start_of_today(now) + 86400.0


def _clock(moment: float) -> str:
    """``14:30``, in this Mac's zone, as the day's rows say when something is due."""
    return datetime.fromtimestamp(moment).astimezone().strftime("%H:%M")


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
        minutes=None,
        tokens=None,
        steps=None,
        parent_run_id=None,
    )


async def _run_row(store: Store, run: RunRecord, section: str, kind: str, detail: str, title: str) -> ActivityRow:
    """A row for a run; one that ended carries its minutes and its tokens."""
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
        minutes=None if run.finished_at is None else (run.finished_at - run.created_at) / 60,
        tokens=None if run.finished_at is None else run.input_tokens + run.output_tokens,
        steps=run.steps,
        parent_run_id=run.parent_run_id,
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
        minutes=None,
        tokens=None,
        steps=None,
        parent_run_id=None,
    )


def _note_row(note: MemoryRecord, kind: str) -> ActivityRow:
    """A lesson or the journal written today: a lesson opens the run it came from, the journal the Memory page."""
    return ActivityRow(
        id=note.id,
        section="learned",
        kind=kind,
        title=_title(note.content),
        detail="",
        channel="",
        since=note.created_at,
        href=f"#/runs/{note.source_run}" if kind == "lesson" and note.source_run is not None else "#/memory",
        run_id=None,
        status="",
        minutes=None,
        tokens=None,
        steps=None,
        parent_run_id=None,
    )


def _due_schedule_row(schedule: ScheduleRecord, due: float) -> ActivityRow:
    return ActivityRow(
        id=f"due-{schedule.id}",
        section="due",
        kind="schedule",
        title=_title(schedule.name),
        detail=f"fires at {_clock(due)}",
        channel="",
        since=due,
        href="#/settings/schedules",
        run_id=None,
        status="scheduled",
        minutes=None,
        tokens=None,
        steps=None,
        parent_run_id=None,
    )


def _due_campaign_row(campaign: CampaignRecord, due: float) -> ActivityRow:
    return ActivityRow(
        id=f"due-{campaign.id}",
        section="due",
        kind="campaign",
        title=_title(campaign.title),
        detail=f"next session at {_clock(due)}",
        channel="",
        since=due,
        href=f"#/campaigns/{campaign.id}",
        run_id=None,
        status="scheduled",
        minutes=None,
        tokens=None,
        steps=None,
        parent_run_id=None,
    )


async def _learned_today(store: Store, today: float) -> list[ActivityRow]:
    """The journal note and the lessons written today, newest first."""
    rows: list[ActivityRow] = [
        _note_row(note, "journal")
        for note in await store.memories_with_tag(JOURNAL_TAG, _JOURNALS_READ)
        if note.created_at >= today
    ]
    rows.extend(
        _note_row(note, "lesson")
        for note in await store.memories_with_tag(LESSON_TAG, _LESSONS_READ)
        if note.created_at >= today
    )
    rows.sort(key=lambda row: row.since, reverse=True)
    return rows


async def _due_today(store: Store, now: float) -> list[ActivityRow]:
    """The schedules that fire and the campaigns whose next session starts before the day ends, soonest first."""
    end: float = _end_of_today(now)
    rows: list[ActivityRow] = [
        _due_schedule_row(schedule, schedule.next_run_at)
        for schedule in await store.list_schedules()
        if schedule.enabled and schedule.next_run_at is not None and schedule.next_run_at <= end
    ]
    rows.extend(
        _due_campaign_row(campaign, campaign.next_session_at)
        for campaign in await store.list_campaigns()
        if campaign.status == "active" and campaign.next_session_at is not None and campaign.next_session_at <= end
    )
    rows.sort(key=lambda row: row.since)
    return rows


def _reflection_row(reflection: Reflection) -> ActivityRow:
    """The reflection that ran today, among what ended: it opens the Memory page, where its journal and lessons are."""
    return ActivityRow(
        id="reflection",
        section="recent",
        kind="reflection",
        title=summary_line(reflection),
        detail="the nightly review of the day's runs",
        channel="",
        since=reflection.at,
        href="#/memory",
        run_id=None,
        status="done",
        minutes=None,
        tokens=None,
        steps=None,
        parent_run_id=None,
    )


async def activity_view(store: Store, manager: RunManager, now: float) -> ActivityView:
    """The three sections at ``now``: waiting (the approvals and questions runs wait for, the questions runs left for
    the user, the runs the user holds),
    working (the live runs that wait for nothing, the schedules due within the hour), recent (the runs that ended
    today and the reflection that ran today, newest first), learned_today (the journal and the lessons written today)
    and due (the schedules and campaign sessions due before the day ends)."""
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
    reflection: Reflection | None = await last_reflection(store)
    if reflection is not None and reflection.at >= today:
        recent.append(_reflection_row(reflection))
    waiting.sort(key=lambda row: row.since)
    working.sort(key=lambda row: row.since)
    recent.sort(key=lambda row: row.since, reverse=True)
    return ActivityView(
        waiting=waiting,
        working=working,
        recent=recent,
        learned_today=await _learned_today(store, today),
        due=await _due_today(store, now),
        generated_at=now,
    )


async def current_activity(store: Store, manager: RunManager) -> ActivityView:
    return await activity_view(store, manager, time.time())
