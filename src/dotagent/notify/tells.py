"""What the owner does with what Dot tells them (item 1743).

Dot tells the owner on its own, by push, on the desktop and on iMessage: the brief, the check-in, a watcher's finding,
what its initiative did and the end of a run. Until this item nothing recorded whether the owner looked: the caps on a
watcher (``tells_per_hour``) were set blind, and ProAgentBench found that the history of what a person did with an
earlier suggestion is what improves the timing of the next. A tell is recorded when it goes out (``tell_sent``), and
the owner's answers when they come (``tell_response``): the chat opened, a notification dismissed. What the owner did
next is read from what the store already holds, a message of theirs or a reaction in the chat of the run the tell
opens.

A tell is *acted on* when the owner wrote in that chat or reacted in it within :data:`TELL_WINDOW_S` of it, *opened*
when they opened the chat in that time, *dismissed* when they closed its notification without opening it, *ignored*
when the window ended with none of these, and *pending* while it runs. A tell whose route is not a chat (the schedules
page, the phone's settings) has no signal of opening: it is *unmeasured*, unless it was dismissed. Nothing here
changes what Dot tells; a later item may quiet a watcher whose tells are never opened.

Pure: the store layer reads the tells, the answers and the owner's acts, and this module only counts."""

from collections.abc import Mapping, Sequence
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.notify.tellkinds import TellKind

# The pseudo-run the tells' events are kept under: the events table is the store of a tell, which adds no table.
TELLS_RUN_ID: str = "_tells"
TELL_SENT_EVENT: str = "tell_sent"
TELL_RESPONSE_EVENT: str = "tell_response"
# How long after a tell an answer of the owner's counts as its answer: a later one is theirs, not the tell's.
TELL_WINDOW_S: float = 1800.0
# How far back the report reads.
TELL_DAYS: int = 30
_DAY_S: float = 86400.0
TELL_REPORT_S: float = TELL_DAYS * _DAY_S

type TellAnswer = Literal["opened", "dismissed"]
# Where an answer was seen: the page (a chat opened), the desktop notification, the phone's push.
type TellVia = Literal["app", "desktop", "push"]
type TellState = Literal["acted", "opened", "dismissed", "ignored", "pending", "unmeasured"]

TELL_KINDS: tuple[TellKind, ...] = (
    "brief",
    "checkin",
    "watcher",
    "initiative",
    "run_end",
    "schedule_end",
    "system",
)
_RUN_ROUTE: str = "#/runs/"


class TellSent(FrozenModel):
    """A tell that went out: when, which kind and about what (a watcher's id), and the route a tap opens."""

    tell_id: str
    ts: float
    kind: TellKind
    subject: str
    route: str


class TellResponse(FrozenModel):
    """What the owner did with a tell: ``opened`` its chat or ``dismissed`` its notification, seen by ``via``."""

    tell_id: str
    ts: float
    response: TellAnswer
    via: TellVia


class OwnerAct(FrozenModel):
    """Something the owner did in a chat: a message that started a run, or a reaction."""

    thread_id: str
    ts: float


class TellCounts(FrozenModel):
    """The tells of one kind and subject by what came of them. ``precision`` is the share of the tells that were opened
    or acted on among those whose window has ended and that have a signal (``None`` when there are none)."""

    sent: int
    acted: int
    opened: int
    dismissed: int
    ignored: int
    pending: int
    unmeasured: int
    precision: float | None


class TellRow(FrozenModel):
    """One line of the report: a kind, the subject it is about (empty for a kind with one) and its counts."""

    kind: TellKind
    subject: str
    counts: TellCounts


class TellReport(FrozenModel):
    """What the owner did with the tells of the last ``days`` days, per kind and subject, and the answer window."""

    days: int
    window_s: float
    rows: tuple[TellRow, ...]


def run_of_route(route: str) -> str | None:
    """The run a route opens (``#/runs/<id>``), ``None`` for a route that is not one chat's."""
    if not route.startswith(_RUN_ROUTE):
        return None
    run_id: str = route.removeprefix(_RUN_ROUTE)
    return run_id if run_id and "/" not in run_id else None


def _within(tell: TellSent, ts: float) -> bool:
    return tell.ts < ts <= tell.ts + TELL_WINDOW_S


def tell_state(
    tell: TellSent,
    responses: Sequence[TellResponse],
    thread_id: str | None,
    acts: Sequence[OwnerAct],
    now: float,
) -> TellState:
    """What came of ``tell``: ``responses`` are its answers, ``thread_id`` the chat its route opens (``None`` for a
    route that opens none) and ``acts`` what the owner did in their chats since."""
    if thread_id is not None and any(act.thread_id == thread_id and _within(tell, act.ts) for act in acts):
        return "acted"
    answers: set[TellAnswer] = {response.response for response in responses if _within(tell, response.ts)}
    if "opened" in answers:
        return "opened"
    if "dismissed" in answers:
        return "dismissed"
    if thread_id is None:
        return "unmeasured"
    return "pending" if now <= tell.ts + TELL_WINDOW_S else "ignored"


def counts_of(states: Sequence[TellState]) -> TellCounts:
    """The counts of ``states``, the states of the tells of one kind and subject."""
    tally: dict[TellState, int] = {
        state: states.count(state) for state in ("acted", "opened", "dismissed", "ignored", "pending", "unmeasured")
    }
    answered: int = tally["acted"] + tally["opened"] + tally["dismissed"] + tally["ignored"]
    return TellCounts(
        sent=len(states),
        acted=tally["acted"],
        opened=tally["opened"],
        dismissed=tally["dismissed"],
        ignored=tally["ignored"],
        pending=tally["pending"],
        unmeasured=tally["unmeasured"],
        precision=None if answered == 0 else (tally["acted"] + tally["opened"]) / answered,
    )


def tell_rows(
    tells: Sequence[TellSent],
    responses: Sequence[TellResponse],
    thread_of_run: Mapping[str, str],
    acts: Sequence[OwnerAct],
    now: float,
) -> tuple[TellRow, ...]:
    """The report's rows from ``tells``, their ``responses``, the chat of each run (``thread_of_run``, for the runs
    still there) and the owner's ``acts``: one row per kind and subject that sent a tell, the kinds in
    :data:`TELL_KINDS` order and the subjects by their id."""
    by_tell: dict[str, list[TellResponse]] = {}
    for response in responses:
        by_tell.setdefault(response.tell_id, []).append(response)
    grouped: dict[tuple[TellKind, str], list[TellState]] = {}
    for tell in tells:
        run_id: str | None = run_of_route(tell.route)
        thread_id: str | None = None if run_id is None else thread_of_run.get(run_id)
        state: TellState = tell_state(tell, by_tell.get(tell.tell_id, []), thread_id, acts, now)
        grouped.setdefault((tell.kind, tell.subject), []).append(state)
    order: dict[str, int] = {kind: index for index, kind in enumerate(TELL_KINDS)}
    return tuple(
        TellRow(kind=kind, subject=subject, counts=counts_of(states))
        for (kind, subject), states in sorted(grouped.items(), key=lambda item: (order[item[0][0]], item[0][1]))
    )
