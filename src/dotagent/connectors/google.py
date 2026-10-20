"""Gmail and Google Calendar (item 424): the messages a search finds, a message read whole, and the events of a time,
as :mod:`dotagent.connectors.types`. Google's answers are read here, at the edge, into the models below."""

import asyncio
import base64
import binascii
import datetime as dt
import html
import re
from email.utils import getaddresses
from urllib.parse import quote

from pydantic import Field

from dotagent.connectors.http import AccountApi, read_answer
from dotagent.connectors.types import Attachment, Attendee, CalendarEvent, MailMessage, MailSummary, ProviderAnswer
from dotagent.core.errors import AccountApiError
from dotagent.web.text import html_to_text

GMAIL: str = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_EVENTS: str = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_NAME: str = "Gmail"
CALENDAR_NAME: str = "Google Calendar"
# What a search with no words lists: the inbox, the newest first.
_INBOX: str = "in:inbox"
_SUMMARY_HEADERS: tuple[str, ...] = ("From", "To", "Subject")
_CHARSET: re.Pattern[str] = re.compile(r"charset\s*=\s*\"?([^\";\s]+)", re.IGNORECASE)


class _Header(ProviderAnswer):
    name: str
    value: str


class _Body(ProviderAnswer):
    size: int = 0
    data: str | None = None


class _Part(ProviderAnswer):
    mimeType: str = ""
    filename: str = ""
    headers: tuple[_Header, ...] = ()
    body: _Body = Field(default_factory=_Body)
    parts: tuple["_Part", ...] = ()


class _Message(ProviderAnswer):
    id: str
    labelIds: tuple[str, ...] = ()
    snippet: str = ""
    # When Gmail took the message in, in milliseconds since 1970.
    internalDate: str | None = None
    payload: _Part = Field(default_factory=_Part)


class _Reference(ProviderAnswer):
    id: str


class _MessageList(ProviderAnswer):
    messages: tuple[_Reference, ...] = ()


class _When(ProviderAnswer):
    dateTime: dt.datetime | None = None
    date: dt.date | None = None


class _Person(ProviderAnswer):
    email: str = ""
    displayName: str = ""
    responseStatus: str = ""
    is_you: bool = Field(default=False, alias="self")


class _Event(ProviderAnswer):
    id: str
    status: str = ""
    summary: str = ""
    description: str = ""
    location: str = ""
    htmlLink: str = ""
    start: _When
    end: _When
    organizer: _Person = Field(default_factory=_Person)
    attendees: tuple[_Person, ...] = ()


class _Events(ProviderAnswer):
    items: tuple[_Event, ...] = ()


def _header(part: _Part, name: str) -> str:
    return next((header.value for header in part.headers if header.name.casefold() == name.casefold()), "")


def people(value: str) -> tuple[str, ...]:
    """The addresses a header such as To names, each as ``Name <address>``, or the address alone."""
    return tuple(f"{name} <{address}>" if name else address for name, address in getaddresses([value]) if address)


def _text_of(part: _Part, data: str) -> str:
    """The text of a part: Gmail's base64url, its padding put back, in the part's charset (UTF-8 when it names none
    Python knows)."""
    try:
        raw: bytes = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError) as error:
        raise AccountApiError("Gmail gave a part of a message Dot could not decode", {"type": part.mimeType}) from error
    named: re.Match[str] | None = _CHARSET.search(_header(part, "Content-Type"))
    charset: str = named[1] if named is not None else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _texts(part: _Part) -> tuple[list[str], list[str]]:
    """The plain and the HTML texts of ``part`` and of the parts in it, in their order; files are left out."""
    plain: list[str] = []
    marked: list[str] = []
    if part.filename == "" and part.body.data is not None:
        if part.mimeType == "text/plain":
            plain.append(_text_of(part, part.body.data))
        elif part.mimeType == "text/html":
            marked.append(_text_of(part, part.body.data))
    for inner in part.parts:
        inner_plain, inner_marked = _texts(inner)
        plain.extend(inner_plain)
        marked.extend(inner_marked)
    return plain, marked


def _attachments(part: _Part) -> list[Attachment]:
    found: list[Attachment] = (
        [Attachment(name=part.filename, media_type=part.mimeType, size=part.body.size)] if part.filename else []
    )
    for inner in part.parts:
        found.extend(_attachments(inner))
    return found


def _summary(message: _Message) -> MailSummary:
    taken: str | None = message.internalDate
    return MailSummary(
        id=message.id,
        sender=", ".join(people(_header(message.payload, "From"))),
        to=people(_header(message.payload, "To")),
        subject=_header(message.payload, "Subject"),
        received=None if taken is None or not taken.isdigit() else dt.datetime.fromtimestamp(int(taken) / 1000, dt.UTC),
        # Gmail's snippet keeps HTML's entities (&#39;).
        snippet=html.unescape(message.snippet),
        unread="UNREAD" in message.labelIds,
    )


def gmail_summary(answer: object, url: str) -> MailSummary:
    """A message as Gmail's answer to ``url`` (its metadata) gives it."""
    return _summary(read_answer(_Message, answer, GMAIL_NAME, url))


def gmail_message(answer: object, url: str) -> MailMessage:
    """A message read whole, from Gmail's answer to ``url``: its plain text, else its HTML made text, else Gmail's
    snippet of it."""
    message: _Message = read_answer(_Message, answer, GMAIL_NAME, url)
    plain, marked = _texts(message.payload)
    text: str = (
        "\n\n".join(plain)
        if plain
        else "\n\n".join(html_to_text(page, "").text for page in marked)
        if marked
        else html.unescape(message.snippet)
    )
    return MailMessage(
        summary=_summary(message),
        cc=people(_header(message.payload, "Cc")),
        text=text.strip(),
        attachments=tuple(_attachments(message.payload)),
    )


def _person(person: _Person) -> str:
    return f"{person.displayName} <{person.email}>" if person.displayName and person.email else person.email


def _event(event: _Event) -> CalendarEvent:
    start: dt.datetime | dt.date | None = event.start.dateTime or event.start.date
    end: dt.datetime | dt.date | None = event.end.dateTime or event.end.date
    if start is None or end is None:
        raise AccountApiError("Google Calendar gave an event with no time", {"event": event.id})
    yours: str = next(
        (person.responseStatus for person in event.attendees if person.is_you),
        "organizer" if event.organizer.is_you else "",
    )
    # A description written in Google Calendar's editor is HTML.
    notes: str = html_to_text(event.description, "").text if "<" in event.description else event.description
    return CalendarEvent(
        id=event.id,
        title=event.summary or "(no title)",
        start=start,
        end=end,
        location=event.location,
        organizer=_person(event.organizer),
        attendees=tuple(
            Attendee(address=person.email, name=person.displayName, answer=person.responseStatus)
            for person in event.attendees
        ),
        your_answer=yours,
        notes=notes.strip(),
        link=event.htmlLink,
        cancelled=event.status == "cancelled",
    )


def google_events_of(answer: object, url: str) -> tuple[CalendarEvent, ...]:
    """The events of Google Calendar's answer to ``url``."""
    return tuple(_event(event) for event in read_answer(_Events, answer, CALENDAR_NAME, url).items)


async def search_gmail(api: AccountApi, query: str, limit: int) -> tuple[MailSummary, ...]:
    """The messages ``query`` finds (Gmail's search words; the inbox when empty), the newest first."""
    url: str = f"{GMAIL}/messages"
    listed: _MessageList = read_answer(
        _MessageList,
        await api.get(GMAIL_NAME, url, [("q", query.strip() or _INBOX), ("maxResults", str(limit))], {}),
        GMAIL_NAME,
        url,
    )
    params: list[tuple[str, str]] = [("format", "metadata"), *(("metadataHeaders", name) for name in _SUMMARY_HEADERS)]
    addresses: list[str] = [f"{GMAIL}/messages/{quote(reference.id, safe='')}" for reference in listed.messages]
    answers: list[object] = await asyncio.gather(
        *(api.get(GMAIL_NAME, address, params, {}) for address in addresses), return_exceptions=True
    )
    summaries: list[MailSummary] = []
    for address, answer in zip(addresses, answers, strict=True):
        # Every call has ended by now; the first that failed is the one raised, the others failed the same way.
        if isinstance(answer, BaseException):
            raise answer
        summaries.append(gmail_summary(answer, address))
    return tuple(summaries)


async def read_gmail(api: AccountApi, message_id: str) -> MailMessage:
    url: str = f"{GMAIL}/messages/{quote(message_id, safe='')}"
    return gmail_message(await api.get(GMAIL_NAME, url, [("format", "full")], {}), url)


async def google_events(
    api: AccountApi, start: dt.datetime, end: dt.datetime, query: str, limit: int
) -> tuple[CalendarEvent, ...]:
    """The events of the primary calendar between ``start`` and ``end``, each repeat on its own, in their order;
    ``query`` narrows them by Google's own search of their words when it is not empty."""
    params: list[tuple[str, str]] = [
        ("timeMin", start.isoformat()),
        ("timeMax", end.isoformat()),
        ("singleEvents", "true"),
        ("orderBy", "startTime"),
        ("maxResults", str(limit)),
    ]
    if query.strip():
        params.append(("q", query.strip()))
    return google_events_of(await api.get(CALENDAR_NAME, CALENDAR_EVENTS, params, {}), CALENDAR_EVENTS)
