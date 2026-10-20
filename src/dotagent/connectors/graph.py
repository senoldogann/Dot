"""Outlook's mail and calendar through Microsoft Graph (item 424): the messages a search finds, a message read whole,
and the events of a time, as :mod:`dotagent.connectors.types`. Graph's answers are read here, at the edge, into the
models below. Graph is asked for the times of events in UTC and for the text of a message rather than its HTML."""

import datetime as dt
from urllib.parse import quote, urlsplit

from pydantic import Field

from dotagent.connectors.http import AccountApi, read_answer
from dotagent.connectors.types import (
    Attachment,
    Attendee,
    CalendarEvent,
    EventList,
    MailList,
    MailMessage,
    MailSummary,
    ProviderAnswer,
)
from dotagent.core.errors import AccountApiError
from dotagent.web.text import html_to_text

GRAPH: str = "https://graph.microsoft.com/v1.0/me"
MAIL_NAME: str = "Outlook"
CALENDAR_NAME: str = "Outlook Calendar"
_SUMMARY_FIELDS: str = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,isRead"
_MESSAGE_FIELDS: str = f"{_SUMMARY_FIELDS},ccRecipients,body"
_EVENT_FIELDS: str = (
    "id,subject,start,end,isAllDay,isCancelled,location,organizer,attendees,bodyPreview,webLink,responseStatus"
)
_UTC_TIMES: dict[str, str] = {"Prefer": 'outlook.timezone="UTC"'}
# Graph searches no events: with words to look for, the time is read a page of this many events at a time, up to this
# many pages, and the words looked for in each.
_PAGE_EVENTS: int = 100
_MOST_PAGES: int = 10
# Where a next page of Graph may be: the token goes with the request, so it goes nowhere else.
_GRAPH_HOST: str = "graph.microsoft.com"
_TEXT_BODY: dict[str, str] = {"Prefer": 'outlook.body-content-type="text"'}


class _Address(ProviderAnswer):
    name: str = ""
    address: str = ""


class _Recipient(ProviderAnswer):
    emailAddress: _Address = Field(default_factory=_Address)


class _Body(ProviderAnswer):
    contentType: str = "text"
    content: str = ""


class _Attachment(ProviderAnswer):
    name: str = ""
    contentType: str = ""
    size: int = 0


class _Message(ProviderAnswer):
    id: str
    subject: str | None = None
    sender: _Recipient | None = Field(default=None, alias="from")
    toRecipients: tuple[_Recipient, ...] = ()
    ccRecipients: tuple[_Recipient, ...] = ()
    receivedDateTime: dt.datetime | None = None
    bodyPreview: str = ""
    isRead: bool = True
    body: _Body | None = None
    attachments: tuple[_Attachment, ...] = ()


class _Messages(ProviderAnswer):
    value: tuple[_Message, ...] = ()
    # Graph's mark of a next page.
    next_link: str | None = Field(default=None, alias="@odata.nextLink")


class _When(ProviderAnswer):
    dateTime: str
    timeZone: str = "UTC"


class _Location(ProviderAnswer):
    displayName: str = ""


class _Status(ProviderAnswer):
    response: str = ""


class _Attendee(ProviderAnswer):
    emailAddress: _Address = Field(default_factory=_Address)
    status: _Status = Field(default_factory=_Status)


class _Event(ProviderAnswer):
    id: str
    subject: str | None = None
    start: _When
    end: _When
    isAllDay: bool = False
    isCancelled: bool = False
    location: _Location = Field(default_factory=_Location)
    organizer: _Recipient = Field(default_factory=_Recipient)
    attendees: tuple[_Attendee, ...] = ()
    bodyPreview: str = ""
    webLink: str = ""
    # The owner's own answer to the event.
    responseStatus: _Status = Field(default_factory=_Status)


class _Events(ProviderAnswer):
    value: tuple[_Event, ...] = ()
    next_link: str | None = Field(default=None, alias="@odata.nextLink")


def _person(recipient: _Recipient) -> str:
    named: _Address = recipient.emailAddress
    return f"{named.name} <{named.address}>" if named.name and named.name != named.address else named.address


def search_value(query: str) -> str:
    """Graph's ``$search`` for ``query``: in double quotes, a backslash or a double quote in it escaped."""
    return '"' + query.strip().replace("\\", "\\\\").replace('"', '\\"') + '"'


def _summary(message: _Message) -> MailSummary:
    return MailSummary(
        id=message.id,
        sender="" if message.sender is None else _person(message.sender),
        to=tuple(_person(recipient) for recipient in message.toRecipients),
        subject=message.subject or "",
        received=message.receivedDateTime,
        snippet=message.bodyPreview,
        unread=not message.isRead,
    )


def outlook_summaries_of(answer: object, url: str) -> MailList:
    """The messages of Graph's answer to ``url``, cut when it names a next page."""
    found: _Messages = read_answer(_Messages, answer, MAIL_NAME, url)
    return MailList(messages=tuple(_summary(message) for message in found.value), more=found.next_link is not None)


def outlook_message(answer: object, url: str) -> MailMessage:
    """A message read whole, from Graph's answer to ``url``: Graph gives its text when asked for it, and its HTML
    made text otherwise."""
    message: _Message = read_answer(_Message, answer, MAIL_NAME, url)
    body: _Body | None = message.body
    text: str = (
        ""
        if body is None
        else html_to_text(body.content, "").text
        if body.contentType.casefold() == "html"
        else body.content
    )
    return MailMessage(
        summary=_summary(message),
        cc=tuple(_person(recipient) for recipient in message.ccRecipients),
        text=text.strip(),
        attachments=tuple(
            Attachment(name=attached.name, media_type=attached.contentType, size=attached.size)
            for attached in message.attachments
        ),
    )


def _moment(when: _When, event: str) -> dt.datetime:
    """A time of an event, which Graph says in UTC when asked to, aware."""
    if when.timeZone.upper() != "UTC":
        raise AccountApiError(
            "Outlook Calendar said a time in another zone than UTC", {"event": event, "zone": when.timeZone}
        )
    try:
        said: dt.datetime = dt.datetime.fromisoformat(when.dateTime)
    except ValueError as error:
        raise AccountApiError("Outlook Calendar said a time Dot does not read", {"event": event}) from error
    return said.replace(tzinfo=dt.UTC) if said.tzinfo is None else said.astimezone(dt.UTC)


def _event(event: _Event) -> CalendarEvent:
    """An event; one all day keeps the days Graph says, which are the same in every zone."""
    start: dt.datetime = _moment(event.start, event.id)
    end: dt.datetime = _moment(event.end, event.id)
    return CalendarEvent(
        id=event.id,
        title=event.subject or "(no title)",
        start=start.date() if event.isAllDay else start,
        end=end.date() if event.isAllDay else end,
        location=event.location.displayName,
        organizer=_person(event.organizer),
        attendees=tuple(
            Attendee(
                address=attendee.emailAddress.address, name=attendee.emailAddress.name, answer=attendee.status.response
            )
            for attendee in event.attendees
        ),
        your_answer="" if event.responseStatus.response == "none" else event.responseStatus.response,
        notes=event.bodyPreview.strip(),
        link=event.webLink,
        cancelled=event.isCancelled,
    )


def graph_page(address: str) -> str:
    """``address``, a page of Graph about to be asked for with the account's token; an error for one that is not
    Graph's. Checked only for a page that is followed: a next page left alone only says the list was cut."""
    parts = urlsplit(address)
    if parts.scheme != "https" or parts.netloc != _GRAPH_HOST:
        raise AccountApiError("Outlook gave a next page that is not Graph's", {"host": parts.netloc})
    return address


def _event_page(answer: object, url: str) -> tuple[tuple[CalendarEvent, ...], str | None]:
    found: _Events = read_answer(_Events, answer, CALENDAR_NAME, url)
    return tuple(_event(event) for event in found.value), found.next_link


def outlook_events_of(answer: object, url: str) -> EventList:
    """The events of one page of Graph's answer to ``url``, cut when it names a next page."""
    events, link = _event_page(answer, url)
    return EventList(events=events, more=link is not None)


def _utc_text(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def has_words(event: CalendarEvent, query: str) -> bool:
    """Whether every word of ``query`` is in the event's title, place or notes, in any case."""
    said: str = " ".join((event.title, event.location, event.notes)).casefold()
    return all(word in said for word in query.casefold().split())


async def search_outlook(api: AccountApi, query: str, limit: int) -> MailList:
    """The messages ``query`` finds in every folder (Outlook's search words); the inbox, the newest first, when it is
    empty."""
    url: str
    params: list[tuple[str, str]]
    if query.strip():
        url = f"{GRAPH}/messages"
        params = [("$search", search_value(query)), ("$top", str(limit)), ("$select", _SUMMARY_FIELDS)]
    else:
        url = f"{GRAPH}/mailFolders/inbox/messages"
        params = [("$top", str(limit)), ("$select", _SUMMARY_FIELDS), ("$orderby", "receivedDateTime desc")]
    return outlook_summaries_of(await api.get(MAIL_NAME, url, params, {}), url)


async def read_outlook(api: AccountApi, message_id: str) -> MailMessage:
    url: str = f"{GRAPH}/messages/{quote(message_id, safe='')}"
    params: list[tuple[str, str]] = [
        ("$select", _MESSAGE_FIELDS),
        ("$expand", "attachments($select=name,contentType,size)"),
    ]
    return outlook_message(await api.get(MAIL_NAME, url, params, _TEXT_BODY), url)


async def outlook_events(api: AccountApi, start: dt.datetime, end: dt.datetime, query: str, limit: int) -> EventList:
    """The events of the calendar between ``start`` and ``end``, each repeat on its own, in their order. Graph
    searches no events: with ``query``, the time is read a page at a time and the events whose title, place or notes
    have its words are kept, until ``limit`` are found or :data:`_MOST_PAGES` pages are read; the list is cut when
    the time may hold more."""
    words: bool = query.strip() != ""
    address: str | None = f"{GRAPH}/calendarView"
    params: list[tuple[str, str]] = [
        ("startDateTime", _utc_text(start)),
        ("endDateTime", _utc_text(end)),
        ("$top", str(_PAGE_EVENTS if words else limit)),
        ("$orderby", "start/dateTime"),
        ("$select", _EVENT_FIELDS),
    ]
    kept: list[CalendarEvent] = []
    pages: int = 0
    while address is not None and pages < (_MOST_PAGES if words else 1) and len(kept) <= limit:
        events: tuple[CalendarEvent, ...]
        events, link = _event_page(await api.get(CALENDAR_NAME, graph_page(address), params, _UTC_TIMES), address)
        kept.extend(event for event in events if has_words(event, query))
        # A next page's address carries the query of the first.
        address, params, pages = link, [], pages + 1
    return EventList(events=tuple(kept[:limit]), more=len(kept) > limit or address is not None)
