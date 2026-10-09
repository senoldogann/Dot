"""Campaigns: the pure rules of a goal that Dot works on in sessions until its outcome is verified (item 12).

Every session is a run that starts in a thread of its own; what carries the campaign from one session to the next is
the brief, its opening message: the goal, the targets, the owner's messages, the newest strategy, the end of the
ledger, the questions left for the owner and what became of them, and how the last session ended. The store keeps
the campaign and its ledger; the manager starts the sessions and plans the next one with :func:`next_session_at`.
"""

from collections.abc import Sequence

from dotagent.core.text import truncate_tail
from dotagent.core.types import FrozenModel
from dotagent.store.models import ApprovalStatus, CampaignEntry, RunRecord, RunStatus

# How many entries of the ledger a brief shows, the newest last.
BRIEF_ENTRIES: int = 12
# How many questions left for the owner a brief shows, the newest last.
BRIEF_QUESTIONS: int = 8
_RESULT_CHARS: int = 600
_SECONDS_PER_MINUTE: float = 60.0
_SECONDS_PER_HOUR: float = 3600.0
_SECONDS_PER_DAY: float = 86400.0


class BriefQuestion(FrozenModel):
    """A question a session left for the owner: ``status`` is ``left`` until it is answered (``approved``, with the
    answer) or skipped (``denied``)."""

    summary: str
    asked_at: float
    status: ApprovalStatus
    answer: str


class LastSession(FrozenModel):
    """How the session before this one went. ``cut_off`` is true when the daemon stopped it before it ended."""

    number: int
    started_at: float
    finished_at: float | None
    status: RunStatus
    result: str
    cut_off: bool


class Brief(FrozenModel):
    """What the opening message of a session is made of. ``instructions`` are the messages the owner sent in the
    campaign's conversation after its goal; ``without_progress`` is how many sessions in a row wrote no ``progress``
    entry, and ``patience`` how many of them the campaign accepts before it asks for a new strategy."""

    goal: str
    targets: str
    verification: str
    session: int
    now: float
    instructions: tuple[str, ...]
    strategy: CampaignEntry | None
    entries: tuple[CampaignEntry, ...]
    questions: tuple[BriefQuestion, ...]
    last: LastSession | None
    without_progress: int
    patience: int


def _ago(seconds: float) -> str:
    """A rough duration for a person: minutes up to two hours, hours up to two days, then days."""
    if seconds < 2 * _SECONDS_PER_HOUR:
        return f"{max(0, round(seconds / _SECONDS_PER_MINUTE))} min"
    if seconds < 2 * _SECONDS_PER_DAY:
        return f"{round(seconds / _SECONDS_PER_HOUR)} h"
    return f"{round(seconds / _SECONDS_PER_DAY)} days"


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def entry_line(entry: CampaignEntry, now: float) -> str:
    """One entry of the ledger as a brief and the goal check show it."""
    numbers: str = ", ".join(f"{name} {_number(value)}" for name, value in entry.numbers.items())
    measured: str = f" ({numbers})" if numbers else ""
    return f"- [{entry.kind}, {_ago(now - entry.ts)} ago] {entry.text}{measured}"


def _question_line(question: BriefQuestion, now: float) -> str:
    match question.status:
        case "approved":
            state: str = f"answered: {question.answer}"
        case "denied":
            state = "skipped by the owner"
        case "left":
            state = f"unanswered since {_ago(now - question.asked_at)}"
        case _:
            state = question.status
    return f"- “{question.summary}”: {state}"


def _last_line(last: LastSession, now: float) -> str:
    if last.cut_off:
        ended: str = (
            f"Session {last.number} was cut off by a stop of the daemon "
            f"{_ago(now - (last.finished_at or last.started_at))} ago, before it ended: what it did is in its run and "
            "in the ledger; check what of it is done before you go on."
        )
    else:
        ended = f"Session {last.number} ended {_ago(now - (last.finished_at or last.started_at))} ago ({last.status})."
    said: str = last.result.strip()
    return f"{ended}\nIts answer: {truncate_tail(said, _RESULT_CHARS)}" if said and not last.cut_off else ended


def render_brief(brief: Brief) -> str:
    """The opening message of session ``brief.session``."""
    parts: list[str] = [f"This is session {brief.session} of a campaign. Its goal:\n{brief.goal}"]
    if brief.targets:
        verified: str = f"\nVerified by: {brief.verification}" if brief.verification else ""
        parts.append(f"Targets: {brief.targets}{verified}")
    else:
        parts.append("Targets: not set yet. Record them with campaign_targets before you finish.")
    if brief.instructions:
        parts.append(
            "What the owner wrote in the campaign's conversation since its goal (instructions):\n"
            + "\n".join(f"- {text}" for text in brief.instructions)
        )
    if brief.strategy is not None:
        parts.append(f"Strategy (the newest): {brief.strategy.text}")
    if brief.entries:
        parts.append(
            "Ledger (the newest entries, oldest first):\n"
            + "\n".join(entry_line(entry, brief.now) for entry in brief.entries)
        )
    else:
        parts.append("Ledger: empty.")
    if brief.questions:
        parts.append(
            "Questions left for the owner:\n"
            + "\n".join(_question_line(question, brief.now) for question in brief.questions)
        )
    if brief.last is not None:
        parts.append(_last_line(brief.last, brief.now))
    if brief.without_progress >= brief.patience:
        parts.append(
            f"No session wrote a progress entry for {brief.without_progress} sessions. Before anything else, write a "
            "new strategy entry with campaign_note (kind strategy) that tries another way, and follow it."
        )
    return "\n\n".join(parts)


def session_number(sessions: Sequence[RunRecord], run_id: str) -> int:
    """The number of the session ``run_id`` among the campaign's ``sessions``, oldest first, counting from 1."""
    return 1 + [session.id for session in sessions].index(run_id)


def sessions_without_progress(started: Sequence[float], last_progress_at: float | None) -> int:
    """How many of the sessions that started at ``started`` came after the newest ``progress`` entry (all of them when
    there is none)."""
    return sum(1 for at in started if last_progress_at is None or at > last_progress_at)


def next_session_at(
    now: float,
    planned: float | None,
    needs: str,
    questions: Sequence[ApprovalStatus],
    gap_minutes: float,
    wait_hours: float,
) -> float:
    """When the session after one that ended at ``now`` starts: when the session planned it (``planned``, with
    ``campaign_schedule`` or a rhythm), else, after a session that ended needing the owner with questions none of which
    is answered yet (``questions`` are their statuses), ``wait_hours`` later (an answer brings it forward), else
    ``gap_minutes`` later."""
    if planned is not None:
        return planned
    if needs.strip() and questions and all(status == "left" for status in questions):
        return now + wait_hours * _SECONDS_PER_HOUR
    return now + gap_minutes * _SECONDS_PER_MINUTE
