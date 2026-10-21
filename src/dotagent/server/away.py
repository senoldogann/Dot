"""While you were away (item 466): what schedules and campaigns did since a moment, the Home's card. One read gathers
the store's counts and rows (:func:`read_away`); the pure builders shape them, mask the secrets of an answer's line and
order the runs with the most approvals answered for the owner."""

from dotagent.core.text import plain_line, redact_secrets
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server.views import AwayDecisionRun, AwayDecisions, AwayRun, AwayView
from dotagent.store.db import Store
from dotagent.store.models import AutoDecisionCount, UnattendedCount, UnattendedRun

# How far back the card reads at most: a week.
AWAY_WINDOW_S: float = 7 * 86_400.0
# The newest runs it lists, and the runs with the most approvals answered for the owner it names.
_RUNS: int = 50
_DECISION_RUNS: int = 10
# The first line of a run's answer, as the list shows it.
_ANSWER_CHARS: int = 200


def clamp_since(since: float, now: float) -> float:
    """``since`` within the last :data:`AWAY_WINDOW_S` before ``now``, and not after it."""
    return min(max(since, now - AWAY_WINDOW_S), now)


def answer_line(result: str) -> str:
    """The first line of an answer that has words, its secrets masked before it is cut, so no part of one is left."""
    first: str = next((line for line in result.splitlines() if line.strip() != ""), "")
    return plain_line(redact_secrets(first), _ANSWER_CHARS)


def title_of(unattended: UnattendedRun) -> str:
    """What names a run of a schedule or a campaign: the campaign's title, the schedule's name, else its goal's first
    line (a firing from before item 257 names no schedule)."""
    if unattended.campaign_title is not None:
        return unattended.campaign_title
    if unattended.schedule_name is not None:
        return unattended.schedule_name
    return plain_line(unattended.run.goal, _ANSWER_CHARS)


def away_run(unattended: UnattendedRun) -> AwayRun:
    run = unattended.run
    return AwayRun(
        run_id=run.id,
        title=title_of(unattended),
        campaign=run.campaign_id is not None,
        agent_id=run.agent_id,
        status=run.status,
        steps=run.steps,
        created_at=run.created_at,
        finished_at=run.finished_at,
        answer=answer_line(run.result),
    )


def _counted(counts: list[UnattendedCount], statuses: frozenset[str]) -> int:
    """The schedules' runs (not the campaigns' sessions) that ended with one of ``statuses``."""
    return sum(count.count for count in counts if not count.campaign and count.status in statuses)


def _decisions(counts: list[AutoDecisionCount]) -> AwayDecisions:
    def total(decided_by: str, status: str) -> int:
        return sum(count.count for count in counts if count.decided_by == decided_by and count.status == status)

    return AwayDecisions(
        reviewer_approved=total("reviewer", "approved"),
        reviewer_denied=total("reviewer", "denied"),
        yolo_approved=total("yolo", "approved"),
        yolo_denied=total("yolo", "denied"),
    )


def decision_runs(counts: list[AutoDecisionCount]) -> list[AwayDecisionRun]:
    """The runs with the most approvals answered for the owner, the most first (the newest of equal ones first)."""
    runs: dict[str, AwayDecisionRun] = {}
    started: dict[str, float] = {}
    for count in counts:
        run_id: str = count.run.run.id
        before: AwayDecisionRun = runs.get(
            run_id, AwayDecisionRun(run_id=run_id, title=title_of(count.run), reviewer=0, yolo=0, denied=0)
        )
        runs[run_id] = AwayDecisionRun(
            run_id=run_id,
            title=before.title,
            reviewer=before.reviewer + (count.count if count.decided_by == "reviewer" else 0),
            yolo=before.yolo + (count.count if count.decided_by == "yolo" else 0),
            denied=before.denied + (count.count if count.status == "denied" else 0),
        )
        started[run_id] = count.run.run.created_at
    ranked: list[AwayDecisionRun] = sorted(
        runs.values(), key=lambda run: (run.reviewer + run.yolo, started[run.run_id]), reverse=True
    )
    return ranked[:_DECISION_RUNS]


def away_view(
    since: float,
    until: float,
    runs: list[UnattendedRun],
    counts: list[UnattendedCount],
    decisions: list[AutoDecisionCount],
    drafts: int,
) -> AwayView:
    return AwayView(
        since=since,
        until=until,
        done=_counted(counts, frozenset({"done"})),
        failed=_counted(counts, frozenset({"failed"})),
        cancelled=_counted(counts, frozenset({"cancelled"})),
        working=_counted(counts, frozenset({"queued", "running", "blocked"})),
        campaign_sessions=sum(count.count for count in counts if count.campaign),
        runs=[away_run(run) for run in runs],
        decisions=_decisions(decisions),
        decision_runs=decision_runs(decisions),
        drafts=drafts,
    )


async def read_away(store: Store, since: float, now: float) -> AwayView:
    """What schedules and campaigns did from ``since`` (kept within the last week) to ``now``."""
    start: float = clamp_since(since, now)
    return away_view(
        start,
        now,
        await store.unattended_runs(SCHEDULE_SOURCE, start, _RUNS),
        await store.unattended_counts(SCHEDULE_SOURCE, start),
        await store.auto_decision_counts(SCHEDULE_SOURCE, start),
        await store.unattended_drafts(SCHEDULE_SOURCE, start),
    )
