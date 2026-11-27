"""The owner's iPhone through the Dot Next app (item 2051): ``phone_calendar_events`` reads its calendars,
``phone_calendar_add`` adds an event, ``phone_reminder_add`` adds a reminder and ``phone_location`` says where it is.

Each is an ask the daemon hands the app over the link of :mod:`dotagent.phone.asks`, which answers only while Dot Next
is open on the phone; iOS asks the owner for each permission the first time an ask needs it. The events are written by
others too (an invitation), so they go in an ``account_content`` fence and the run is tainted, as by the calendars of
:mod:`dotagent.tools.mail_calendar`. An add is a change in one of the owner's apps (``app_control``): it goes through
the policy as any other, so the run's approval mode decides whether it asks.
"""

import asyncio
import datetime as dt
import secrets
from collections.abc import Awaitable, Callable
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from dotagent.connectors.types import CalendarEvent
from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.phone.asks import (
    MOST_EVENTS,
    CalendarAddAsk,
    CalendarEventsAsk,
    CreatedAnswer,
    EventsAnswer,
    LocationAnswer,
    LocationAsk,
    PhoneAnswer,
    PhoneAsk,
    PhoneEvent,
    RefusedAnswer,
    ReminderAddAsk,
    new_ask_id,
)
from dotagent.policy.types import ActionInfo, ActionKind, Risk
from dotagent.scheduler.timing import resolve_zone
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import ToolContext
from dotagent.tools.mail_calendar import event_lines, fence_account, moment_of

PHONE_CALENDAR_EVENTS_TOOL: str = "phone_calendar_events"
PHONE_CALENDAR_ADD_TOOL: str = "phone_calendar_add"
PHONE_REMINDER_ADD_TOOL: str = "phone_reminder_add"
PHONE_LOCATION_TOOL: str = "phone_location"
PHONE_APP_TOOLS: frozenset[str] = frozenset(
    {PHONE_CALENDAR_EVENTS_TOOL, PHONE_CALENDAR_ADD_TOOL, PHONE_REMINDER_ADD_TOOL, PHONE_LOCATION_TOOL}
)
# How long an ask waits for the phone's answer: the first time, the owner answers iOS's permission question first.
_ANSWER_S: float = 75.0
_TIMEOUT_S: float = 90.0
_NONCE_BYTES: int = 8
_SOURCE: str = "the iPhone's calendars"
_TARGET: str = "iPhone"
_DAY_CHARS: int = len("2026-11-28")
_TIME_FIELD: str = "a day (2026-11-28) or a moment (2026-11-28T09:00), in the Mac's time zone unless it gives an offset"
# Where each permission is given back in iOS Settings, by what was refused.
_SETTINGS: dict[str, str] = {
    PHONE_CALENDAR_EVENTS_TOOL: "Calendars",
    PHONE_CALENDAR_ADD_TOOL: "Calendars",
    PHONE_REMINDER_ADD_TOOL: "Reminders",
    PHONE_LOCATION_TOOL: "Location",
}


def _is_day(text: str) -> bool:
    return len(text.strip()) == _DAY_CHARS


def _checked_time(name: str, value: str) -> None:
    try:
        moment_of(value, dt.UTC)
    except ValueError as error:
        raise ValueError(f"{name} is neither a day (2026-11-28) nor a moment (2026-11-28T09:00): {value!r}") from error


class CalendarEventsArgs(ToolArgs):
    start: str = Field(max_length=40, description=f"Where the time starts: {_TIME_FIELD}.")
    end: str = Field(
        max_length=40, description="Where it ends, not included: a day (2026-11-29 ends it with the 28th) or a moment."
    )
    query: str = Field(
        default="", max_length=200, description="Words the events must have, in their title, place or notes."
    )
    limit: int = Field(default=50, ge=1, le=MOST_EVENTS, description="How many events at most.")

    @model_validator(mode="after")
    def _times(self) -> Self:
        _checked_time("start", self.start)
        _checked_time("end", self.end)
        return self


class CalendarAddArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=300)
    start: str = Field(max_length=40, description=f"{_TIME_FIELD[0].upper()}{_TIME_FIELD[1:]}; a day makes it all day.")
    end: str = Field(
        default="",
        max_length=40,
        description="A moment for a timed event (empty: an hour after start), its last day for an all-day one (empty: "
        "the same day).",
    )
    location: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)
    calendar: str = Field(default="", max_length=200, description="A calendar's name; empty: the phone's default.")

    @model_validator(mode="after")
    def _times(self) -> Self:
        _checked_time("start", self.start)
        if self.end.strip():
            _checked_time("end", self.end)
            if _is_day(self.end) != _is_day(self.start):
                raise ValueError("start and end must both be days (all day) or both be moments")
        return self


class ReminderAddArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=300)
    due: str = Field(default="", max_length=40, description=f"When it is due: {_TIME_FIELD}; empty: none.")
    notes: str = Field(default="", max_length=2000)
    list: str = Field(default="", max_length=200, description="A reminders list's name; empty: the phone's default.")

    @model_validator(mode="after")
    def _due(self) -> Self:
        if self.due.strip():
            _checked_time("due", self.due)
        return self


class NoArgs(ToolArgs):
    pass


def _action(tool: str, kind: ActionKind, risk: Risk, summary: str) -> ActionInfo:
    return ActionInfo(tool=tool, kind=kind, risk=risk, summary=summary[:200], target=_TARGET, flags=())


def wire_moment(moment: dt.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def calendar_add_ask(args: CalendarAddArgs, zone: dt.tzinfo, ask_id: str) -> CalendarAddAsk:
    """The ask of ``args``, its times resolved in ``zone``: an all-day event's end is the day after its last."""
    start_text: str = args.start.strip()
    end_text: str = args.end.strip()
    if _is_day(start_text):
        first: dt.date = dt.date.fromisoformat(start_text)
        last: dt.date = dt.date.fromisoformat(end_text) if end_text else first
        if last < first:
            raise ToolInputError(
                f"{PHONE_CALENDAR_ADD_TOOL}: end ({args.end}) comes before start ({args.start})",
                {"start": args.start, "end": args.end},
            )
        start, end, all_day = first.isoformat(), (last + dt.timedelta(days=1)).isoformat(), True
    else:
        begins: dt.datetime = moment_of(start_text, zone)
        ends: dt.datetime = moment_of(end_text, zone) if end_text else begins + dt.timedelta(hours=1)
        if ends <= begins:
            raise ToolInputError(
                f"{PHONE_CALENDAR_ADD_TOOL}: end ({args.end}) must come after start ({args.start})",
                {"start": args.start, "end": args.end},
            )
        start, end, all_day = wire_moment(begins), wire_moment(ends), False
    return CalendarAddAsk(
        id=ask_id,
        kind="calendar_add",
        title=args.title.strip(),
        start=start,
        end=end,
        all_day=all_day,
        location=args.location.strip(),
        notes=args.notes,
        calendar=args.calendar.strip(),
    )


def reminder_add_ask(args: ReminderAddArgs, zone: dt.tzinfo, ask_id: str) -> ReminderAddAsk:
    due_text: str = args.due.strip()
    due: str | None = None
    if due_text:
        due = (
            dt.date.fromisoformat(due_text).isoformat() if _is_day(due_text) else wire_moment(moment_of(due_text, zone))
        )
    return ReminderAddAsk(
        id=ask_id, kind="reminder_add", title=args.title.strip(), due=due, notes=args.notes, list=args.list.strip()
    )


def as_calendar_event(event: PhoneEvent) -> CalendarEvent:
    """A phone's event as the calendars of the accounts hold one, so that both are written the same way."""
    start: dt.datetime | dt.date = (
        dt.date.fromisoformat(event.start) if event.all_day else dt.datetime.fromisoformat(event.start)
    )
    end: dt.datetime | dt.date = (
        dt.date.fromisoformat(event.end) if event.all_day else dt.datetime.fromisoformat(event.end)
    )
    return CalendarEvent(
        id=event.id,
        title=event.title,
        start=start,
        end=end,
        location=event.location,
        organizer=event.organizer,
        attendees=(),
        your_answer="",
        notes=event.notes,
        link="",
        cancelled=False,
    )


def events_text(answer: EventsAnswer, span: str, words: str, zone: dt.tzinfo, nonce: str) -> str:
    """What a run reads of the phone's events: a head line of Dot's, then the events fenced as content others wrote."""
    if not answer.events:
        more: str = " The time may hold more than are read at once: narrow it." if answer.more else ""
        return f"{_SOURCE}: no event{words} {span}.{more}"
    lines: list[str] = []
    for at, event in enumerate(answer.events, 1):
        lines.extend(event_lines(at, as_calendar_event(event), zone))
        if event.calendar:
            lines.append(f"   calendar: {' '.join(event.calendar.split())[:200]}")
    count: str = "1 event" if len(answer.events) == 1 else f"{len(answer.events)} events"
    cut: str = f" The time may hold more: narrow it, or raise limit (up to {MOST_EVENTS})." if answer.more else ""
    return "\n".join((f"{_SOURCE}: {count}{words} {span}.{cut}", fence_account(_SOURCE, "\n".join(lines), nonce)))


def location_text(answer: LocationAnswer, zone: dt.tzinfo) -> str:
    measured: str = dt.datetime.fromisoformat(answer.at).astimezone(zone).strftime("%a %d %b %Y %H:%M")
    where: str = f"{answer.latitude:.5f},{answer.longitude:.5f}"
    return (
        f"The iPhone is at {where} (latitude, longitude; within {answer.accuracy_m:.0f} m), measured {measured} "
        f"({getattr(zone, 'key', 'local time')}). Map: https://maps.apple.com/?ll={where}"
    )


class PhoneAskRefused(ToolExecutionError):
    """The phone did not do the ask: ``result`` is what the run reads (:func:`refusal_result`); the error's own words
    are Dot's, never the phone's."""

    def __init__(self, tool: str, answer: RefusedAnswer, result: ToolResult) -> None:
        super().__init__("the iPhone did not do it", {"tool": tool, "reason": answer.reason})
        self.result: ToolResult = result


def refusal_result(tool: str, answer: RefusedAnswer, nonce: str) -> ToolResult:
    """What a run reads of a refusal. A permission refused or restricted is said in Dot's own words, with where the
    owner gives it back. A failure carries the phone's words fenced, and taints the run: they may list the phone's
    calendars, and a shared one is named by someone else."""
    if answer.reason == "failed":
        said: str = fence_account("the iPhone's answer", answer.message, nonce)
        return mark_untrusted(fail(f"the iPhone could not do it; it said:\n{said}"))
    where: str = f"iOS Settings › Apps › Dot Next › {_SETTINGS[tool]}"
    why: str = "the owner has not allowed it" if answer.reason == "denied" else "the iPhone's restrictions forbid it"
    return fail(f"the iPhone refused: {why}. Only the owner can change that, in {where}.")


async def _asked(ctx: ToolContext, tool: str, ask: PhoneAsk) -> PhoneAnswer:
    answer: PhoneAnswer = await ctx.runtime.phone_link.ask(ask, _ANSWER_S)
    if isinstance(answer, RefusedAnswer):
        raise PhoneAskRefused(tool, answer, refusal_result(tool, answer, secrets.token_hex(_NONCE_BYTES)))
    return answer


def created_result(where: str, answer: CreatedAnswer, what: str, nonce: str) -> ToolResult:
    """What a run reads of an add: Dot's own words, and the calendar's or the list's name fenced, since a shared or
    subscribed one is named by someone else."""
    head: str = f"Added to the iPhone's {where} (id {answer.id}): {what}."
    if not answer.calendar:
        return ok(head)
    named: str = fence_account(f"the name of the iPhone's {where}", answer.calendar, nonce)
    return mark_untrusted(ok(f"{head} It went to:\n{named}"))


def refusals_read[A: ToolArgs](
    run: Callable[[A, ToolContext], Awaitable[ToolResult]],
) -> Callable[[A, ToolContext], Awaitable[ToolResult]]:
    """``run``, with a refusal of the phone's read by the run as its fenced result rather than as an error's words."""

    async def answered(args: A, ctx: ToolContext) -> ToolResult:
        try:
            return await run(args, ctx)
        except PhoneAskRefused as refused:
            return refused.result

    return answered


def build_phone_app_tools() -> tuple[RegisteredTool, ...]:
    def classify_events(args: CalendarEventsArgs, ctx: ToolContext) -> ActionInfo:
        return _action(
            PHONE_CALENDAR_EVENTS_TOOL, "read", "low", f"Read the iPhone's calendars from {args.start} to {args.end}"
        )

    async def run_events(args: CalendarEventsArgs, ctx: ToolContext) -> ToolResult:
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        start: dt.datetime = moment_of(args.start, zone)
        end: dt.datetime = moment_of(args.end, zone)
        if end <= start:
            raise ToolInputError(
                f"{PHONE_CALENDAR_EVENTS_TOOL}: end ({args.end}) must come after start ({args.start})",
                {"start": args.start, "end": args.end},
            )
        ask = CalendarEventsAsk(
            id=new_ask_id(),
            kind="calendar_events",
            start=wire_moment(start),
            end=wire_moment(end),
            query=args.query.strip(),
            limit=args.limit,
        )
        answer: PhoneAnswer = await _asked(ctx, PHONE_CALENDAR_EVENTS_TOOL, ask)
        if not isinstance(answer, EventsAnswer):
            raise ToolExecutionError("the iPhone answered with something else", {"answer": answer.kind})
        span: str = f"from {start:%a %d %b %Y %H:%M} to {end:%a %d %b %Y %H:%M} ({zone.key})"
        words: str = f" with {args.query.strip()!r}" if args.query.strip() else ""
        return mark_untrusted(ok(events_text(answer, span, words, zone, secrets.token_hex(_NONCE_BYTES))))

    def classify_add(args: CalendarAddArgs, ctx: ToolContext) -> ActionInfo:
        return _action(
            PHONE_CALENDAR_ADD_TOOL,
            "app_control",
            "medium",
            f"Add “{args.title}” to the iPhone's calendar at {args.start}",
        )

    async def run_add(args: CalendarAddArgs, ctx: ToolContext) -> ToolResult:
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        ask: CalendarAddAsk = calendar_add_ask(args, zone, new_ask_id())
        answer: PhoneAnswer = await _asked(ctx, PHONE_CALENDAR_ADD_TOOL, ask)
        if not isinstance(answer, CreatedAnswer):
            raise ToolExecutionError("the iPhone answered with something else", {"answer": answer.kind})
        span: str = f"{ask.start} to {ask.end}" + (" (all day, the end not included)" if ask.all_day else "")
        return created_result("calendar", answer, f"“{ask.title}”, {span}", secrets.token_hex(_NONCE_BYTES))

    def classify_reminder(args: ReminderAddArgs, ctx: ToolContext) -> ActionInfo:
        return _action(
            PHONE_REMINDER_ADD_TOOL, "app_control", "medium", f"Add the reminder “{args.title}” on the iPhone"
        )

    async def run_reminder(args: ReminderAddArgs, ctx: ToolContext) -> ToolResult:
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        ask: ReminderAddAsk = reminder_add_ask(args, zone, new_ask_id())
        answer: PhoneAnswer = await _asked(ctx, PHONE_REMINDER_ADD_TOOL, ask)
        if not isinstance(answer, CreatedAnswer):
            raise ToolExecutionError("the iPhone answered with something else", {"answer": answer.kind})
        due: str = f", due {ask.due}" if ask.due else ""
        return created_result("reminders", answer, f"“{ask.title}”{due}", secrets.token_hex(_NONCE_BYTES))

    def classify_location(args: NoArgs, ctx: ToolContext) -> ActionInfo:
        return _action(PHONE_LOCATION_TOOL, "read", "low", "Ask the iPhone where it is")

    async def run_location(args: NoArgs, ctx: ToolContext) -> ToolResult:
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        answer: PhoneAnswer = await _asked(ctx, PHONE_LOCATION_TOOL, LocationAsk(id=new_ask_id(), kind="location"))
        if not isinstance(answer, LocationAnswer):
            raise ToolExecutionError("the iPhone answered with something else", {"answer": answer.kind})
        return ok(location_text(answer, zone))

    reach: str = " Answers only while Dot Next is open on the owner's iPhone."
    return (
        make_tool(
            name=PHONE_CALENDAR_EVENTS_TOOL,
            description="The events of the calendars on the owner's iPhone between two days or moments: when, what, "
            "where, which calendar." + reach,
            args_model=CalendarEventsArgs,
            modes=ALL_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_events,
            run=refusals_read(run_events),
        ),
        make_tool(
            name=PHONE_CALENDAR_ADD_TOOL,
            description="Add an event to a calendar on the owner's iPhone." + reach,
            args_model=CalendarAddArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_add,
            run=refusals_read(run_add),
        ),
        make_tool(
            name=PHONE_REMINDER_ADD_TOOL,
            description="Add a reminder to the Reminders of the owner's iPhone." + reach,
            args_model=ReminderAddArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_reminder,
            run=refusals_read(run_reminder),
        ),
        make_tool(
            name=PHONE_LOCATION_TOOL,
            description="Where the owner's iPhone is now: latitude, longitude and accuracy." + reach,
            args_model=NoArgs,
            modes=ALL_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_location,
            run=refusals_read(run_location),
        ),
    )
