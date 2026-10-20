"""``mail_search``, ``mail_read`` and ``calendar_events`` (item 424): the owner's mail and calendars in the Google and
Microsoft accounts they signed in (:mod:`dotagent.oauth`), read through Gmail's, Google Calendar's and Microsoft
Graph's own APIs (:mod:`dotagent.connectors`).

They only read: nothing is sent, moved or marked read. What they return is written by others, since anyone can send a
mail or an invitation, so it goes in an ``account_content`` fence and the run is tainted, as by a web page."""

import asyncio
import datetime as dt
import secrets
from collections.abc import Sequence
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from dotagent.connectors import google, graph
from dotagent.connectors.http import AccountApi
from dotagent.connectors.types import CalendarEvent, MailMessage, MailSummary
from dotagent.core.errors import ToolInputError
from dotagent.core.text import truncate_middle, unclosing
from dotagent.oauth.providers import ProviderId
from dotagent.oauth.tokens import Account
from dotagent.policy.types import ActionInfo
from dotagent.scheduler.timing import resolve_zone
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext

MAIL_SEARCH_TOOL: str = "mail_search"
MAIL_READ_TOOL: str = "mail_read"
CALENDAR_EVENTS_TOOL: str = "calendar_events"
# A call may wait for the provider's "not now" twice before it gives up.
_TIMEOUT_S: float = 90.0
_NONCE_BYTES: int = 8
_SUMMARY_CHARS: int = 200
# The most of a message's text a read returns; the run's own limit on a result applies after.
_TEXT_CHARS: int = 30_000
_LINE_CHARS: int = 300
_NOTES_CHARS: int = 500
_LISTED_ATTENDEES: int = 8
_MAIL_NAMES: dict[ProviderId, str] = {"google": google.GMAIL_NAME, "microsoft": graph.MAIL_NAME}
_CALENDAR_NAMES: dict[ProviderId, str] = {"google": google.CALENDAR_NAME, "microsoft": graph.CALENDAR_NAME}
_ACCOUNT_FIELD: str = "The address of an account the owner signed in, as your instructions list them."


def fence_account(source: str, body: str, nonce: str) -> str:
    """Wrap what an account's mail or calendar holds, so that the prompt can say everything inside is data and only
    ``nonce`` closes it."""
    safe: str = unclosing(body, "account_content")
    quoted: str = '"' + source.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f'<account_content nonce="{nonce}" source={quoted}>\n{safe}\n</account_content nonce="{nonce}">'


def moment_of(text: str, zone: dt.tzinfo) -> dt.datetime:
    """The moment ``text`` names: a day (``2026-10-21``, its first moment in ``zone``) or a date and time
    (``2026-10-21T09:00``, in ``zone`` unless it says its offset); a ValueError for anything else."""
    said: str = text.strip()
    if len(said) == len("2026-10-21"):
        day: dt.date = dt.date.fromisoformat(said)
        return dt.datetime(day.year, day.month, day.day, tzinfo=zone)
    moment: dt.datetime = dt.datetime.fromisoformat(said)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)


def account_lines(accounts: Sequence[Account]) -> tuple[str, ...]:
    """The line that tells a run which accounts the owner signed in, for the tools here; none without one."""
    if not accounts:
        return ()
    named: str = "; ".join(
        f"{account.address} ({_MAIL_NAMES[account.provider]} and {_CALENDAR_NAMES[account.provider]})"
        for account in accounts
    )
    return (
        f"The user's accounts signed in, which {MAIL_SEARCH_TOOL}, {MAIL_READ_TOOL} and {CALENDAR_EVENTS_TOOL} "
        f"take as account: {named}.",
    )


def _clip(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters: a line break in what others wrote cannot fake a line of the list."""
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _counted(count: int, thing: str) -> str:
    return f"1 {thing}" if count == 1 else f"{count} {thing}s"


def _moment_text(moment: dt.datetime, zone: dt.tzinfo) -> str:
    return moment.astimezone(zone).strftime("%a %d %b %Y %H:%M")


def _size_text(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1048576:.1f} MB"


def summary_lines(index: int, summary: MailSummary, zone: dt.tzinfo) -> list[str]:
    when: str = "" if summary.received is None else f" · {_moment_text(summary.received, zone)}"
    return [
        f"{index}. id {summary.id}{when}{' · unread' if summary.unread else ''}",
        f"   From: {_clip(summary.sender, _LINE_CHARS) or '(no sender)'}",
        f"   To: {_clip(', '.join(summary.to), _LINE_CHARS) or '(no recipient)'}",
        f"   Subject: {_clip(summary.subject, _LINE_CHARS) or '(no subject)'}",
        f"   {_clip(summary.snippet, _LINE_CHARS)}",
    ]


def message_text(message: MailMessage, zone: dt.tzinfo) -> str:
    summary: MailSummary = message.summary
    files: str = "; ".join(
        f"{_clip(attached.name, 120)} ({attached.media_type}, {_size_text(attached.size)})"
        for attached in message.attachments
    )
    lines: list[str] = [
        f"From: {_clip(summary.sender, _LINE_CHARS)}",
        f"To: {_clip(', '.join(summary.to), _LINE_CHARS)}",
        *([f"Cc: {_clip(', '.join(message.cc), _LINE_CHARS)}"] if message.cc else []),
        *([f"Date: {_moment_text(summary.received, zone)}"] if summary.received is not None else []),
        f"Subject: {_clip(summary.subject, _LINE_CHARS)}",
        *([f"Attachments: {files}"] if files else []),
        "",
        truncate_middle(message.text, _TEXT_CHARS) or "(no text)",
    ]
    return "\n".join(lines)


def span_text(event: CalendarEvent, zone: dt.tzinfo) -> str:
    """When the event is, in ``zone``: its day and hours, or its days for one all day."""
    if isinstance(event.start, dt.datetime) and isinstance(event.end, dt.datetime):
        start: dt.datetime = event.start.astimezone(zone)
        end: dt.datetime = event.end.astimezone(zone)
        if start.date() == end.date():
            return f"{start:%a %d %b %Y %H:%M}–{end:%H:%M}"
        return f"{start:%a %d %b %Y %H:%M} – {end:%a %d %b %Y %H:%M}"
    first: dt.date = event.start.date() if isinstance(event.start, dt.datetime) else event.start
    after: dt.date = event.end.date() if isinstance(event.end, dt.datetime) else event.end
    last: dt.date = after - dt.timedelta(days=1)
    return f"{first:%a %d %b %Y}, all day" if last <= first else f"{first:%a %d %b %Y} – {last:%a %d %b %Y}, all day"


def event_lines(index: int, event: CalendarEvent, zone: dt.tzinfo) -> list[str]:
    details: list[str] = [f"id {event.id}"]
    if event.location:
        details.append(f"place: {_clip(event.location, _LINE_CHARS)}")
    if event.organizer:
        details.append(f"organizer: {_clip(event.organizer, _LINE_CHARS)}")
    if event.your_answer:
        details.append(f"you: {event.your_answer}")
    lines: list[str] = [
        f"{index}. {span_text(event, zone)} · {_clip(event.title, _LINE_CHARS)}"
        + (" · cancelled" if event.cancelled else ""),
        "   " + " · ".join(details),
    ]
    if event.attendees:
        shown: str = ", ".join(
            _clip(attendee.name or attendee.address, 80) + (f" ({attendee.answer})" if attendee.answer else "")
            for attendee in event.attendees[:_LISTED_ATTENDEES]
        )
        more: int = len(event.attendees) - _LISTED_ATTENDEES
        lines.append(f"   attendees ({len(event.attendees)}): {shown}" + (f" and {more} more" if more > 0 else ""))
    if event.notes:
        lines.append(f"   notes: {_clip(event.notes, _NOTES_CHARS)}")
    if event.link:
        lines.append(f"   link: {event.link}")
    return lines


class MailSearchArgs(ToolArgs):
    account: str = Field(min_length=3, max_length=320, description=_ACCOUNT_FIELD)
    query: str = Field(
        default="",
        max_length=500,
        description=(
            "Gmail: what Gmail's search box takes, such as from:ada is:unread newer_than:7d subject:invoice "
            "has:attachment. Outlook: words, or from:, subject:, received>=2026-10-01. Empty: the newest messages "
            "of the inbox."
        ),
    )
    limit: int = Field(default=10, ge=1, le=25, description="How many messages at most.")


class MailReadArgs(ToolArgs):
    account: str = Field(min_length=3, max_length=320, description=_ACCOUNT_FIELD)
    message_id: str = Field(min_length=1, max_length=1000, description="The id mail_search gave the message.")


class CalendarEventsArgs(ToolArgs):
    account: str = Field(min_length=3, max_length=320, description=_ACCOUNT_FIELD)
    start: str = Field(
        description="Where the time starts: a day (2026-10-21) or a moment (2026-10-21T09:00), in the Mac's time "
        "zone unless it gives an offset."
    )
    end: str = Field(description="Where it ends, not included: a day (2026-10-22 ends it with 21 October) or a moment.")
    query: str = Field(
        default="", max_length=200, description="Words the events must have, in their title, place or notes."
    )
    limit: int = Field(default=50, ge=1, le=100, description="How many events at most.")

    @model_validator(mode="after")
    def _times(self) -> Self:
        for name, value in (("start", self.start), ("end", self.end)):
            try:
                moment_of(value, dt.UTC)
            except ValueError as error:
                raise ValueError(
                    f"{name} is neither a day (2026-10-21) nor a moment (2026-10-21T09:00): {value!r}"
                ) from error
        return self


def _action(tool: str, summary: str, account: str) -> ActionInfo:
    """What a call is to the policy: a read of what others wrote, through the provider's API, as of a web page."""
    return ActionInfo(
        tool=tool,
        kind="network",
        risk="low",
        summary=_clip(summary, _SUMMARY_CHARS),
        target=f"account:{account.strip().casefold()}",
        flags=(),
    )


async def _api(ctx: ToolContext, address: str) -> AccountApi:
    account: Account = await ctx.runtime.accounts.account_of(address)
    return AccountApi(ctx.runtime.accounts, account.provider, account.address)


async def _search(api: AccountApi, query: str, limit: int) -> tuple[MailSummary, ...]:
    if api.provider == "google":
        return await google.search_gmail(api, query, limit)
    return await graph.search_outlook(api, query, limit)


async def _read(api: AccountApi, message_id: str) -> MailMessage:
    if api.provider == "google":
        return await google.read_gmail(api, message_id)
    return await graph.read_outlook(api, message_id)


async def _events(
    api: AccountApi, start: dt.datetime, end: dt.datetime, query: str, limit: int
) -> tuple[CalendarEvent, ...]:
    if api.provider == "google":
        return await google.google_events(api, start, end, query, limit)
    return await graph.outlook_events(api, start, end, query, limit)


def _fenced(head: str, source: str, body: str) -> ToolResult:
    return mark_untrusted(ok("\n".join((head, fence_account(source, body, secrets.token_hex(_NONCE_BYTES))))))


def build_mail_calendar_tools() -> tuple[RegisteredTool, ...]:
    def classify_search(args: MailSearchArgs, ctx: ToolContext) -> ActionInfo:
        words: str = f" for {args.query.strip()!r}" if args.query.strip() else ", the newest of the inbox"
        return _action(MAIL_SEARCH_TOOL, f"Search the mail of {args.account}{words}", args.account)

    async def run_search(args: MailSearchArgs, ctx: ToolContext) -> ToolResult:
        api: AccountApi = await _api(ctx, args.account)
        found: tuple[MailSummary, ...] = await _search(api, args.query, args.limit)
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        source: str = f"{_MAIL_NAMES[api.provider]} of {api.address}"
        what: str = f"for {args.query.strip()!r}" if args.query.strip() else "in the inbox"
        if not found:
            return ok(f"{source}: no message {what}.")
        body: str = "\n".join(line for at, summary in enumerate(found, 1) for line in summary_lines(at, summary, zone))
        return _fenced(
            f"{source}: {_counted(len(found), 'message')} {what}, times in {zone.key}. Read one whole with "
            f"{MAIL_READ_TOOL} and its id.",
            source,
            body,
        )

    def classify_read(args: MailReadArgs, ctx: ToolContext) -> ActionInfo:
        return _action(MAIL_READ_TOOL, f"Read the message {args.message_id} of {args.account}", args.account)

    async def run_read(args: MailReadArgs, ctx: ToolContext) -> ToolResult:
        api: AccountApi = await _api(ctx, args.account)
        message: MailMessage = await _read(api, args.message_id)
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        source: str = f"{_MAIL_NAMES[api.provider]} of {api.address}"
        return _fenced(
            f"{source}: the message {message.summary.id}, times in {zone.key}. Nothing was marked read.",
            source,
            message_text(message, zone),
        )

    def classify_events(args: CalendarEventsArgs, ctx: ToolContext) -> ActionInfo:
        return _action(
            CALENDAR_EVENTS_TOOL, f"Read the calendar of {args.account} from {args.start} to {args.end}", args.account
        )

    async def run_events(args: CalendarEventsArgs, ctx: ToolContext) -> ToolResult:
        api: AccountApi = await _api(ctx, args.account)
        zone: ZoneInfo = await asyncio.to_thread(resolve_zone, "")
        start: dt.datetime = moment_of(args.start, zone)
        end: dt.datetime = moment_of(args.end, zone)
        if end <= start:
            raise ToolInputError(
                f"{CALENDAR_EVENTS_TOOL}: end ({args.end}) must come after start ({args.start})",
                {"start": args.start, "end": args.end},
            )
        found: tuple[CalendarEvent, ...] = await _events(api, start, end, args.query, args.limit)
        source: str = f"{_CALENDAR_NAMES[api.provider]} of {api.address}"
        span: str = f"from {_moment_text(start, zone)} to {_moment_text(end, zone)} ({zone.key})"
        words: str = f" with {args.query.strip()!r}" if args.query.strip() else ""
        if not found:
            return ok(f"{source}: no event{words} {span}.")
        body: str = "\n".join(line for at, event in enumerate(found, 1) for line in event_lines(at, event, zone))
        return _fenced(f"{source}: {_counted(len(found), 'event')}{words} {span}.", source, body)

    return (
        make_tool(
            name=MAIL_SEARCH_TOOL,
            description=(
                "Search the owner's mail in an account they signed in (Gmail or Outlook): who wrote, to whom, when, "
                "the subject and the first words of each message. Only reads."
            ),
            args_model=MailSearchArgs,
            modes=ALL_MODES,
            parallel_safe=True,
            timeout_s=_TIMEOUT_S,
            classify=classify_search,
            run=run_search,
        ),
        make_tool(
            name=MAIL_READ_TOOL,
            description=(
                "Read one message of the owner's mail whole, by the id mail_search gave: its people, date, text and "
                "the files it carries. Only reads: it is not marked read."
            ),
            args_model=MailReadArgs,
            modes=ALL_MODES,
            parallel_safe=True,
            timeout_s=_TIMEOUT_S,
            classify=classify_read,
            run=run_read,
        ),
        make_tool(
            name=CALENDAR_EVENTS_TOOL,
            description=(
                "The events of the owner's calendar in an account they signed in (Google Calendar or Outlook) between "
                "two days or moments: when, what, where, who, and the owner's answer. Only reads."
            ),
            args_model=CalendarEventsArgs,
            modes=ALL_MODES,
            parallel_safe=True,
            timeout_s=_TIMEOUT_S,
            classify=classify_events,
            run=run_events,
        ),
    )
