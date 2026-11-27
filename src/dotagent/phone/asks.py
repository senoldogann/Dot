"""What Dot asks of the Dot Next app on the owner's iPhone, and what it answers (item 2051): the phone's calendars,
its reminders and where it is, read and written on the phone itself through EventKit and Core Location.

The daemon cannot open a connection to the phone; the phone reaches the daemon. So while Dot Next is on the screen it
holds a request open at ``GET /api/phone/asks/next`` (:mod:`dotagent.server.routes_phone_asks`), which answers the
next ask meant for it, or nothing after :data:`POLL_S`, and it asks again at once; the answer of an ask comes back at
``POST /api/phone/asks/{id}``. iOS lets an app's requests go only while it is in front, so the phone answers only
then: an ask made while no phone waits fails at once with :class:`PhoneUnreachable`, in words the run reads.

:class:`PhoneLink` is the daemon's one broker of the asks, in memory: which phones wait, the asks each has to take,
and the answers runs wait for. ``docs/phone.md`` holds the contract.
"""

import asyncio
import datetime as dt
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from dotagent.core.errors import InvalidValueError, NotFoundError, ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

# How long a phone's request waits for an ask before it answers nothing and the phone asks again.
POLL_S: float = 20.0
# A phone whose last request ended this long ago and that has not asked again is taken as gone (the app left the
# screen): the gap between two of its requests is a round trip.
PRESENT_S: float = 4.0
# How long an ask waits for a waiting phone to take it before the phone is taken as gone.
TAKE_S: float = 6.0
_ID_BYTES: int = 9
# The most an answer may list.
MOST_EVENTS: int = 100
_TEXT_MAX: int = 4_000
_DAY_CHARS: int = len("2026-11-28")


def moment(text: str) -> dt.datetime:
    """The moment ``text`` names, ISO 8601 with its offset; a ValueError for anything else (a day, a moment with no
    offset), so that an answer the run could not read is refused at the door with a 422."""
    found: dt.datetime = dt.datetime.fromisoformat(text)
    if found.utcoffset() is None:
        raise ValueError(f"a moment with no offset: {text!r}")
    return found


class CalendarEventsAsk(FrozenModel):
    """The events of the phone's calendars that overlap ``start`` to ``end`` (ISO 8601 moments with their offset),
    those with ``query`` in their title, place or notes when it is not empty, at most ``limit``."""

    id: str
    kind: Literal["calendar_events"]
    start: str
    end: str
    query: str
    limit: int


class CalendarAddAsk(FrozenModel):
    """An event added to the phone's default calendar for new events, or to the calendar named ``calendar`` when it is
    not empty. Timed: ``start`` and ``end`` are ISO 8601 moments with their offset. All day: they are days
    (``2026-11-28``), ``end`` the day after its last."""

    id: str
    kind: Literal["calendar_add"]
    title: str
    start: str
    end: str
    all_day: bool
    location: str
    notes: str
    calendar: str


class ReminderAddAsk(FrozenModel):
    """A reminder added to the phone's default list, or to the list named ``list`` when it is not empty. ``due`` is an
    ISO 8601 moment with its offset, a day (``2026-11-28``), or ``None`` for none."""

    id: str
    kind: Literal["reminder_add"]
    title: str
    due: str | None
    notes: str
    list: str


class LocationAsk(FrozenModel):
    """Where the phone is now."""

    id: str
    kind: Literal["location"]


type PhoneAsk = Annotated[
    CalendarEventsAsk | CalendarAddAsk | ReminderAddAsk | LocationAsk, Field(discriminator="kind")
]
type AskKind = Literal["calendar_events", "calendar_add", "reminder_add", "location"]


class PhoneEvent(FrozenModel):
    """An event as the phone read it. Timed: ``start`` and ``end`` are ISO 8601 moments with their offset. All day:
    they are days, ``end`` the day after its last."""

    id: str = Field(max_length=500)
    title: str = Field(max_length=_TEXT_MAX)
    start: str = Field(max_length=40)
    end: str = Field(max_length=40)
    all_day: bool
    location: str = Field(max_length=_TEXT_MAX)
    notes: str = Field(max_length=_TEXT_MAX)
    calendar: str = Field(max_length=500)
    organizer: str = Field(max_length=500)

    @model_validator(mode="after")
    def _times(self) -> Self:
        if self.all_day:
            start, end = dt.date.fromisoformat(self.start), dt.date.fromisoformat(self.end)
            if len(self.start) != _DAY_CHARS or len(self.end) != _DAY_CHARS or end <= start:
                raise ValueError("an all-day event's start and end are days, the end after the start")
        elif moment(self.end) < moment(self.start):
            raise ValueError("an event ends before it starts")
        return self


class EventsAnswer(FrozenModel):
    """The answer of a :class:`CalendarEventsAsk`: the events, and whether the time holds more than are listed."""

    kind: Literal["events"]
    events: list[PhoneEvent] = Field(max_length=MOST_EVENTS)
    more: bool


class CreatedAnswer(FrozenModel):
    """The answer of an add: the new item's id on the phone and the calendar or list it went to."""

    kind: Literal["created"]
    id: str = Field(max_length=500)
    calendar: str = Field(max_length=500)


class LocationAnswer(FrozenModel):
    """Where the phone is: degrees, the radius of the uncertainty in metres, and when it was measured (an ISO 8601
    moment)."""

    kind: Literal["location"]
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    accuracy_m: float = Field(ge=0.0)
    at: str = Field(max_length=40)

    @model_validator(mode="after")
    def _at(self) -> Self:
        moment(self.at)
        return self


# ``denied``: the owner said no to the permission, or took it back in iOS Settings; ``restricted``: the phone's
# restrictions forbid it; ``failed``: the phone tried and could not (no calendar of that name, no location fix).
type RefusalReason = Literal["denied", "restricted", "failed"]


class RefusedAnswer(FrozenModel):
    """The phone did not do the ask, and why, in its words."""

    kind: Literal["refused"]
    reason: RefusalReason
    message: str = Field(max_length=1_000)


type PhoneAnswer = Annotated[EventsAnswer | CreatedAnswer | LocationAnswer | RefusedAnswer, Field(discriminator="kind")]


class AnswerBody(FrozenModel):
    """The body of ``POST /api/phone/asks/{id}``."""

    answer: PhoneAnswer


# The answer each kind of ask takes, besides a refusal.
_ANSWER_OF: dict[AskKind, str] = {
    "calendar_events": "events",
    "calendar_add": "created",
    "reminder_add": "created",
    "location": "location",
}


class PhoneUnreachable(ToolExecutionError):
    """No phone holds Dot Next open now, or the one that did left before it answered."""


def new_ask_id() -> str:
    return f"ask_{secrets.token_urlsafe(_ID_BYTES)}"


def fits(ask: PhoneAsk, answer: PhoneAnswer) -> bool:
    """Whether ``answer`` is one ``ask`` takes: its own kind of answer, or a refusal."""
    return answer.kind == "refused" or _ANSWER_OF[ask.kind] == answer.kind


@dataclass
class _Phone:
    """One phone as the link sees it: how many of its requests wait now, when its last one ended, and the asks it has
    to take, oldest first."""

    waiting: int
    left_at: float
    queue: list[PhoneAsk] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _Pending:
    phone: str
    ask: PhoneAsk
    taken: asyncio.Event
    answered: "asyncio.Future[PhoneAnswer]"


class PhoneLink:
    """The asks between the daemon's runs and the phones that hold Dot Next open (module docstring). One per runtime;
    ``now`` is a monotonic clock."""

    def __init__(self, now: Callable[[], float]) -> None:
        self._now: Callable[[], float] = now
        self._phones: dict[str, _Phone] = {}
        self._pending: dict[str, _Pending] = {}

    def present(self) -> str | None:
        """The phone an ask goes to: of those waiting now or gone less than :data:`PRESENT_S` ago, the one heard last;
        ``None`` when there is none."""
        now: float = self._now()
        heard: list[tuple[float, str]] = [
            (now if phone.waiting > 0 else phone.left_at, phone_id)
            for phone_id, phone in self._phones.items()
            if phone.waiting > 0 or now - phone.left_at < PRESENT_S
        ]
        return max(heard)[1] if heard else None

    async def next_ask(self, phone_id: str, wait_s: float, gone: Callable[[], Awaitable[bool]]) -> PhoneAsk | None:
        """The next ask for ``phone_id``, waiting up to ``wait_s`` for one; ``None`` when none came or ``gone`` says
        the phone's request was dropped (checked each second, so that a phone that left the screen takes nothing)."""
        phone: _Phone = self._phones.setdefault(phone_id, _Phone(waiting=0, left_at=self._now()))
        phone.waiting += 1
        try:
            async with asyncio.timeout(wait_s):
                while not phone.queue:
                    if await gone():
                        return None
                    phone.arrived.clear()
                    try:
                        async with asyncio.timeout(1.0):
                            await phone.arrived.wait()
                    except TimeoutError:
                        continue
                if await gone():
                    return None
                ask: PhoneAsk = phone.queue.pop(0)
                pending: _Pending | None = self._pending.get(ask.id)
                if pending is not None:
                    pending.taken.set()
                return ask
        except TimeoutError:
            return None
        finally:
            phone.waiting -= 1
            phone.left_at = self._now()

    def answer(self, phone_id: str, ask_id: str, answer: PhoneAnswer) -> None:
        """Give the run that waits on ``ask_id`` its answer. Raises :class:`NotFoundError` for an ask that is not
        ``phone_id``'s or no longer waits, and :class:`InvalidValueError` for an answer of another kind."""
        pending: _Pending | None = self._pending.get(ask_id)
        if pending is None or pending.phone != phone_id or pending.answered.done():
            raise NotFoundError("no such ask waits for this phone's answer", {"ask": ask_id})
        if not fits(pending.ask, answer):
            raise InvalidValueError(
                "the answer is not of the ask's kind",
                {"ask": ask_id, "ask_kind": pending.ask.kind, "answer_kind": answer.kind},
            )
        pending.answered.set_result(answer)

    async def ask(self, ask: PhoneAsk, answer_s: float) -> PhoneAnswer:
        """Hand ``ask`` to the phone present now and wait up to ``answer_s`` for its answer (the first time, the owner
        may have to say yes to iOS's permission). Raises :class:`PhoneUnreachable` when no phone holds Dot Next open,
        when the phone does not take the ask within :data:`TAKE_S`, or does not answer in time."""
        phone_id: str | None = self.present()
        if phone_id is None:
            raise PhoneUnreachable(
                "the iPhone cannot be reached now: Dot Next answers only while it is open on the owner's iPhone "
                "(iOS stops an app in the background). Ask the owner to open Dot Next and try again, or go on "
                "without it.",
                {"ask": ask.kind},
            )
        phone: _Phone = self._phones[phone_id]
        pending: _Pending = _Pending(phone_id, ask, asyncio.Event(), asyncio.get_running_loop().create_future())
        self._pending[ask.id] = pending
        phone.queue.append(ask)
        phone.arrived.set()
        try:
            try:
                async with asyncio.timeout(TAKE_S):
                    await pending.taken.wait()
            except TimeoutError as error:
                raise PhoneUnreachable(
                    "the iPhone did not take the request: Dot Next left the screen. Ask the owner to open it and try "
                    "again.",
                    {"ask": ask.kind, "phone": phone_id},
                ) from error
            try:
                async with asyncio.timeout(answer_s):
                    return await pending.answered
            except TimeoutError as error:
                raise PhoneUnreachable(
                    f"the iPhone took the request but did not answer in {answer_s:.0f} s: Dot Next may have left the "
                    "screen, or a permission question waits for the owner on it. Whether it was done is not known:"
                    " look before trying again.",
                    {"ask": ask.kind, "phone": phone_id},
                ) from error
        finally:
            del self._pending[ask.id]
            if ask in phone.queue:
                phone.queue.remove(ask)
            log.info("phone_ask_ended", kind=ask.kind, phone=phone_id)
