"""Campaigns: the pure rules of a goal that Dot works on in sessions until its outcome is verified.

Every session is a run that starts in a thread of its own; what carries the campaign from one session to the next is
the brief, its opening message: the goal, the targets, the files attached to the goal, the owner's messages, the
newest strategy and progress, the end of the ledger, the questions left for the owner and what became of them, and how
the last session ended. The store keeps the campaign and its ledger; the manager starts the sessions and plans the next
one with :func:`next_session_at`.
"""

from collections.abc import Sequence
from typing import Literal

from dotagent.core.text import fold_search, truncate_tail
from dotagent.core.types import FrozenModel
from dotagent.store.models import ApprovalStatus, BrowserMode, CampaignEntry, CampaignRecord, RunRecord, RunStatus

# The live event, on the run of its session, of a campaign that ended by itself: the goal checker accepted its outcome,
# or it stopped (the owner's emergency stop ended a session, or it ran the sessions it may). What the owner does to a
# campaign sends none: they know it.
CAMPAIGN_ENDED_EVENT: str = "campaign_ended"
# How many entries of the ledger a brief shows, the newest last.
BRIEF_ENTRIES: int = 12
# How many questions left for the owner a brief shows, the newest last.
BRIEF_QUESTIONS: int = 8
_RESULT_CHARS: int = 600
_SECONDS_PER_MINUTE: float = 60.0
_SECONDS_PER_HOUR: float = 3600.0
_SECONDS_PER_DAY: float = 86400.0


class CampaignEnded(FrozenModel):
    """What ``campaign_ended`` carries: the campaign, how it ended and the note its ledger got for it."""

    campaign_id: str
    title: str
    status: Literal["achieved", "stopped"]
    note: str


class BriefQuestion(FrozenModel):
    """A question a session asked the owner: ``status`` is ``left`` until it is answered (``approved``, with the
    answer) or skipped (``denied``); ``expired`` when the session waited for it and ended without it."""

    summary: str
    asked_at: float
    status: ApprovalStatus
    answer: str


class BriefKept(FrozenModel):
    """A step a session kept for the owner when nobody answered it in time (item 484): a hand-off they do themselves
    (``handoff``) or a question for their yes; ``status`` is ``left`` while it waits, ``approved`` once they did it or
    allowed it (``used`` once a session did the allowed step, ``usable`` while the yes holds and is unused), ``denied``
    once they skipped it, ``expired`` once it lapsed; ``note`` is what they wrote with their answer."""

    summary: str
    kept_at: float
    handoff: bool
    status: ApprovalStatus
    note: str
    used: bool
    usable: bool


class LastSession(FrozenModel):
    """How the session before this one went. ``cut_off`` is true when the daemon stopped it before it ended."""

    number: int
    started_at: float
    finished_at: float | None
    status: RunStatus
    result: str
    cut_off: bool


class Brief(FrozenModel):
    """What the opening message of a session is made of. ``files`` are the paths of the files the owner attached to
    the goal; ``instructions`` are the messages the owner sent in the campaign's conversation after its goal;
    ``strategies`` are every strategy entry of the ledger, oldest first, and ``progress`` the newest progress entry,
    which the brief shows also when it is older than ``entries``; ``without_progress`` is how many sessions in a row
    wrote no ``progress`` entry, and ``patience`` how many of them the campaign accepts before it asks for a
    different route; ``browser`` is the Chrome the session works in."""

    goal: str
    browser: BrowserMode
    targets: str
    verification: str
    files: tuple[str, ...]
    session: int
    now: float
    instructions: tuple[str, ...]
    strategies: tuple[CampaignEntry, ...]
    progress: CampaignEntry | None
    entries: tuple[CampaignEntry, ...]
    questions: tuple[BriefQuestion, ...]
    kept: tuple[BriefKept, ...]
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
    """A measured value as it was written: a whole number without its point, any other in full."""
    return str(int(value)) if value.is_integer() else repr(value)


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
        case "expired":
            state = "not answered while the session waited for it"
        case "pending":
            state = "waiting for the owner's answer"
    return f"- “{question.summary}”: {state}"


def _kept_line(kept: BriefKept, now: float) -> str:
    """What a session reads of a step a session kept for the owner (item 484)."""
    said: str = "" if kept.note.strip() == "" else f" They wrote: {kept.note.strip()}"
    match kept.status:
        case "left":
            waits: str = "to do it themselves" if kept.handoff else "to allow it"
            state: str = f"waiting for the owner {waits} since {_ago(now - kept.kept_at)}; do not try it again"
        case "approved" if kept.handoff:
            state = f"the owner did it themselves; go on from there.{said}"
        case "approved" if kept.used:
            state = "allowed by the owner once, and done"
        case "approved" if kept.usable:
            state = f"allowed by the owner once: do it when you reach it, exactly as it was, once.{said}"
        case "approved":
            state = "allowed by the owner once, but the yes lapsed unused; do not try it again"
        case "denied":
            state = "skipped by the owner: do not try it again or another way round it"
        case _:
            state = "not answered in time, and lapsed; do not try it again"
    return f"- “{kept.summary}”: {state}"


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


BROWSER_LINES: dict[BrowserMode, str] = {
    "attach": "Browser: the owner's own Chrome, signed in as they are (attach); their tabs are theirs to keep.",
    "own": "Browser: Dot's own Chrome (own), signed in to nothing unless a session signed in.",
}


def render_brief(brief: Brief) -> str:
    """The opening message of session ``brief.session``."""
    parts: list[str] = [
        f"This is session {brief.session} of a campaign. Its goal:\n{brief.goal}",
        BROWSER_LINES[brief.browser],
    ]
    if brief.targets:
        verified: str = f"\nVerified by: {brief.verification}" if brief.verification else ""
        parts.append(f"Targets: {brief.targets}{verified}")
    else:
        parts.append("Targets: not set yet. Record them with campaign_targets before you finish.")
    if brief.files:
        parts.append(
            "Files the owner attached to the goal (the first session got their content; read them at these paths when "
            "you need them):\n" + "\n".join(f"- {path}" for path in brief.files)
        )
    if brief.instructions:
        parts.append(
            "What the owner wrote in the campaign's conversation since its goal (instructions):\n"
            + "\n".join(f"- {text}" for text in brief.instructions)
        )
    if brief.strategies:
        parts.append(f"Strategy (the newest): {brief.strategies[-1].text}")
    shown: set[str] = {entry.id for entry in brief.entries}
    if brief.progress is not None and brief.progress.id not in shown:
        parts.append(f"Progress (the newest, before the entries below):\n{entry_line(brief.progress, brief.now)}")
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
    if brief.kept:
        parts.append(
            "Steps kept for the owner when nobody answered them (do none of them unless a line says you may):\n"
            + "\n".join(_kept_line(kept, brief.now) for kept in brief.kept)
        )
    if brief.last is not None:
        parts.append(_last_line(brief.last, brief.now))
    if brief.without_progress >= brief.patience:
        parts.append(_change_of_route(brief))
    return "\n\n".join(parts)


def routes_without_progress(
    strategies: Sequence[CampaignEntry], last_progress_at: float | None
) -> tuple[CampaignEntry, ...]:
    """The strategy entries written since the newest progress entry (all of them when there is none): the routes that
    brought nothing."""
    return tuple(entry for entry in strategies if last_progress_at is None or entry.ts > last_progress_at)


def route_refused(text: str, tried: Sequence[CampaignEntry]) -> bool:
    """Whether a strategy repeats one of the routes ``tried``, folded as search folds (case, accents and the Turkish
    i do not matter)."""
    folded: str = fold_search(text)
    return any(fold_search(entry.text) == folded for entry in tried)


def _change_of_route(brief: Brief) -> str:
    """What a brief says once the sessions without progress reach the patience: the routes tried since the newest
    progress, which are refused, and the call for a different one."""
    tried: tuple[CampaignEntry, ...] = routes_without_progress(
        brief.strategies, None if brief.progress is None else brief.progress.ts
    )
    sessions: str = "session" if brief.without_progress == 1 else "sessions"
    opening: str = f"No session wrote a progress entry for {brief.without_progress} {sessions}."
    if tried:
        listed: str = "\n".join(entry_line(entry, brief.now) for entry in tried)
        routes: str = (
            " The routes tried since the newest progress, oldest first, brought nothing and are refused "
            f"(campaign_note does not take them again):\n{listed}\n"
        )
    else:
        routes = " No strategy entry says which route they took.\n"
    return (
        f"{opening}{routes}"
        "Before anything else, take a different route: another channel (a site's API, a command or a skill instead of "
        "its pages), another source or another approach. Write it as a new strategy entry with campaign_note (kind "
        "strategy), then follow it."
    )


def campaign_browser(daemon_mode: BrowserMode, campaign: CampaignRecord) -> BrowserMode:
    """The Chrome the campaign's sessions work in: its own choice, or the daemon's (``daemon_mode``, read at each
    session) for a campaign started without one (item 579) or from before the choice."""
    return daemon_mode if campaign.browser is None else campaign.browser


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
    kept_open: bool,
    gap_minutes: float,
    wait_hours: float,
) -> float:
    """When the session after one that ended at ``now`` starts: when the session planned it (``planned``, with
    ``campaign_schedule`` or a rhythm), else, after a session that ended needing the owner with questions that it left
    for them, none answered or skipped yet (``questions`` are their statuses), or while a step kept for them in the
    campaign is still unanswered (``kept_open``, item 484), whatever the session said it needs, ``wait_hours`` later
    (an answer or a skip brings it forward), else ``gap_minutes`` later. A question that expired while the session
    waited for it can no longer be answered, so nothing waits for it."""
    if planned is not None:
        return planned
    if kept_open or (needs.strip() and questions and all(status == "left" for status in questions)):
        return now + wait_hours * _SECONDS_PER_HOUR
    return now + gap_minutes * _SECONDS_PER_MINUTE
