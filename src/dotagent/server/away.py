"""While you were away (items 466 and 1424): what Dot did on its own since a moment, the Home's card and the app's list.
One read gathers the store's counts and rows (:func:`read_away`); the pure builders shape them, mask the secrets of an
answer's line and order the runs with the most approvals answered for the owner.

What counts as done while the owner was away (:data:`AWAY_RULE`): the runs of a source Dot starts by itself and nobody
waits for (the sources of :data:`~dotagent.proactive.UNWATCHED_SOURCES`: a schedule's and a campaign's session, a
watcher's, Dot's own initiative, a reply's; and an agent's work and a command that ended in the background), and the
runs of an agent's own chat under a source that is not the owner's (the sources of a run they start and wait for, in
the app or with ``dotx run``, a review of theirs, an eval's task, an agent's welcome, and a run resumed after the one
they started). An agent's work (``team``) is always sent by a run, so it has a parent: it is listed, unless a run of
the owner's is above it (they wait for that run, and for the work in it). The simplest rule that lists no run the
owner watched: no record is kept of who was looking while a run worked, so a run they started is theirs, and the chat's
own unread dot says it ended."""

from dotagent.agent.manager import BACKGROUND_SOURCE
from dotagent.agent.prompt import OWNER_SOURCES
from dotagent.agent.resume import RESUME_SOURCE
from dotagent.agent.welcome import WELCOME_SOURCE
from dotagent.core.text import plain_line, redact_secrets
from dotagent.proactive import UNWATCHED_SOURCES
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server.views import AwayDecisionRun, AwayDecisions, AwayRun, AwayView
from dotagent.store.db import Store
from dotagent.store.models import WORKING_STATUSES, AutoDecisionCount, AwayRule, UnattendedCount, UnattendedRun
from dotagent.tools.team import TEAM_SOURCE

# The row of the store's key/value table that keeps when the owner last marked the card read (item 1356): Dot's, not a
# browser's, so that a phone and a Mac agree on what is news.
AWAY_READ_KEY: str = "away.read_at"
# Which runs the card lists (item 1424; before, a schedule's alone): every source of a run nobody watches, which
# ``UNWATCHED_SOURCES`` names in one place, with an agent's work and a command that ended in the background; and what
# ran in an agent's own chat under a source that is none of the owner's (the sources they start a run with, which the
# prompt names, and the two that go with their runs: an agent's welcome, which opens in front of them, and a run resumed
# after one they started). An agent's work is a run another run sent, so it is the one source allowed a parent.
AWAY_RULE: AwayRule = AwayRule(
    sources=tuple(sorted({*UNWATCHED_SOURCES, TEAM_SOURCE, BACKGROUND_SOURCE})),
    owners=tuple(sorted({*OWNER_SOURCES, WELCOME_SOURCE, RESUME_SOURCE})),
    delegated=(TEAM_SOURCE,),
)
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
    """What names a run of the card: the campaign's title, the schedule's name, the title of the chat for a run another
    source started (a reply, an agent's work, a command in the background: their goals are Dot's own notes), else its
    goal's first line (a firing from before item 257 names no schedule)."""
    if unattended.campaign_title is not None:
        return unattended.campaign_title
    if unattended.schedule_name is not None:
        return unattended.schedule_name
    if unattended.run.source != SCHEDULE_SOURCE and unattended.thread_title:
        return plain_line(unattended.thread_title, _ANSWER_CHARS)
    return plain_line(unattended.run.goal, _ANSWER_CHARS)


def away_run(unattended: UnattendedRun) -> AwayRun:
    run = unattended.run
    return AwayRun(
        run_id=run.id,
        title=title_of(unattended),
        source=run.source,
        campaign=run.campaign_id is not None,
        agent_id=run.agent_id,
        status=run.status,
        steps=run.steps,
        created_at=run.created_at,
        finished_at=run.finished_at,
        answer=answer_line(run.result),
    )


def _counted(counts: list[UnattendedCount], statuses: frozenset[str]) -> int:
    """The runs of the card (not the campaigns' sessions) that ended with one of ``statuses``."""
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
        working=_counted(counts, WORKING_STATUSES),
        campaign_sessions=sum(count.count for count in counts if count.campaign),
        runs=[away_run(run) for run in runs],
        decisions=_decisions(decisions),
        decision_runs=decision_runs(decisions),
        drafts=drafts,
    )


async def read_away(store: Store, since: float, now: float) -> AwayView:
    """What Dot did on its own from ``since`` (kept within the last week) to ``now``: the runs of :data:`AWAY_RULE`."""
    start: float = clamp_since(since, now)
    return away_view(
        start,
        now,
        await store.unattended_runs(AWAY_RULE, start, _RUNS),
        await store.unattended_counts(AWAY_RULE, start),
        await store.auto_decision_counts(AWAY_RULE, start),
        await store.unattended_drafts(AWAY_RULE, start),
    )


async def read_mark(store: Store) -> float | None:
    """When the owner last marked the card read on any device, or ``None`` when none did (or the row is not a time)."""
    stored: str | None = await store.kv_get(AWAY_READ_KEY)
    if stored is None:
        return None
    try:
        return float(stored)
    except ValueError:
        return None


async def mark_read(store: Store, until: float, now: float) -> float:
    """Mark the card read up to ``until`` (never back, nor past ``now``) and say where the mark stands. The mark is read
    and written in one write, so two devices marking at once cannot move it back."""
    moved: float = min(until, now)

    def later(stored: str | None) -> str:
        try:
            current: float | None = None if stored is None else float(stored)
        except ValueError:
            current = None
        return repr(moved if current is None else max(current, moved))

    return float(await store.kv_update(AWAY_READ_KEY, later))
