"""What a watcher sees in one look (item 571): the new mail of an account, the events about to start in its calendar, or
the owner's new GitHub notifications, each as the items it has not handled yet, read through the same APIs the mail and
calendar tools use (:mod:`dotagent.connectors`) and :mod:`dotagent.proactive.github`.

A look reads and judges nothing: it gives the new items, the keys of everything it looked at (which a first look takes
as handled, so that no old mail is told), and when to look again. What is written by others stays in an item's ``body``,
which every reader fences."""

import asyncio
import datetime as dt
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from pydantic import Field

from dotagent.connectors import google, graph
from dotagent.connectors.http import AccountApi
from dotagent.connectors.types import CalendarEvent, EventList, MailList, MailSummary
from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel
from dotagent.oauth.accounts import Accounts
from dotagent.oauth.tokens import Account
from dotagent.proactive.github import GITHUB_TOKEN_NAME, Notification, Notifications, read_notifications
from dotagent.proactive.text import one_line
from dotagent.proactive.watchers import WatcherRecord
from dotagent.secrets import optional_secret

# Nothing that came more than this long ago is told: a daemon that was off for days comes back to no flood.
MAX_AGE_S: float = 86_400.0
# A look asks for what came from this long before the last one began: a message a provider shows late is still found,
# and what was handled is taken out by its key.
SLACK_S: float = 300.0
# The most messages a look reads, and a first look (which only needs to know they are there).
MAIL_LIMIT: int = 50
FIRST_MAIL_LIMIT: int = 10
# The calendar is looked at at least this often, and no oftener than the gap below however soon an event comes in.
CALENDAR_LOOK_S: float = 300.0
LEAST_GAP_S: float = 10.0
MOST_EVENTS: int = 50
# How long the lines of an item are: its title as it is told, and the lines of what a model or a run reads of it.
TITLE_CHARS: int = 140
_LINE_CHARS: int = 300
_PREVIEW_CHARS: int = 400
# What GitHub's notifications are told without the owner's words: a review asked of them, a mention (theirs or their
# team's), an assignment, and a check of their own that failed (``ci_activity`` whose title says it failed).
_DEFAULT_REASONS: frozenset[str] = frozenset({"review_requested", "mention", "team_mention", "assign"})
_REASON_WORDS: dict[str, str] = {
    "review_requested": "Review requested",
    "mention": "You were mentioned",
    "team_mention": "Your team was mentioned",
    "assign": "Assigned to you",
    "ci_activity": "A check failed",
    "author": "Activity on what you opened",
    "comment": "A comment on what you follow",
    "state_change": "A state change",
    "subscribed": "Activity on what you watch",
    "security_alert": "A security alert",
}


class Item(FrozenModel):
    """One thing a watcher may tell: ``key`` marks it as handled, ``title`` is the line the owner is told, ``source``
    says where it came from (a fence's ``source``), ``body`` is what a model or a run reads of it (written by others, so
    it is always fenced) and ``detail`` how a run can read more of it. ``order`` sorts items by when they came."""

    key: str
    title: str
    source: str
    body: str
    detail: str
    order: float


class Gathered(FrozenModel):
    """What a look found: the ``items`` it has not handled (oldest first), the ``listed`` keys of everything it looked
    at, the cursor to keep (``since``: when the look began, for mail; ``modified``: GitHub's), ``look_again_s``, how
    long until the next look, ``cut``, a word for the row when the provider had more than one look reads, and
    ``left_out``, how many of the things it looked at were read, declined or not what the watcher is for."""

    items: tuple[Item, ...]
    listed: tuple[str, ...]
    since: float | None = None
    modified: str | None = None
    look_again_s: float
    cut: str = ""
    left_out: int = Field(default=0, ge=0)


def _who(sender: str) -> str:
    """The name a sender goes by, else the address."""
    name, address = parseaddr(sender)
    return name or address or sender or "(no sender)"


def _api(accounts: Accounts, account: Account) -> AccountApi:
    return AccountApi(accounts, account.provider, account.address)


def _arrival(message: MailSummary, now: float) -> float:
    return now if message.received is None else message.received.timestamp()


async def _inbox(api: AccountApi, floor: float | None, limit: int) -> MailList:
    """The newest messages of the inbox, from ``floor`` on where the provider can be asked that (Gmail), else all of
    the newest ``limit`` (Outlook, whose list is cut here)."""
    if api.provider == "google":
        after: str = "" if floor is None else f" after:{int(floor)}"
        return await google.search_gmail(api, f"in:inbox{after}", limit)
    return await graph.search_outlook(api, "", limit)


def _mail_item(message: MailSummary, account: Account, now: float) -> Item:
    name: str = google.GMAIL_NAME if account.provider == "google" else graph.MAIL_NAME
    subject: str = message.subject or "(no subject)"
    lines: list[str] = [
        f"From: {one_line(message.sender, _LINE_CHARS)}",
        f"To: {one_line(', '.join(message.to), _LINE_CHARS)}",
        f"Subject: {one_line(subject, _LINE_CHARS)}",
        f"First lines: {one_line(message.snippet, _PREVIEW_CHARS)}",
    ]
    return Item(
        key=message.id,
        title=one_line(f"{_who(message.sender)}: {subject}", TITLE_CHARS),
        source=f"{name} of {account.address}",
        body="\n".join(lines),
        detail=f"The message is {message.id} in the account {account.address}: read it whole with mail_read.",
        order=_arrival(message, now),
    )


async def look_mail(accounts: Accounts, watcher: WatcherRecord, now: float) -> Gathered:
    """The new unread mail of the watcher's account. The first look reads the newest few and takes them as handled; a
    later one asks from a little before the last look began and leaves out what was handled, what is read and what
    came more than a day ago."""
    account: Account = await accounts.account_of(watcher.account)
    state = watcher.state
    first: bool = not state.primed or state.since is None
    floor: float | None = None if first else max((state.since or now) - SLACK_S, now - MAX_AGE_S)
    found: MailList = await _inbox(_api(accounts, account), floor, FIRST_MAIL_LIMIT if first else MAIL_LIMIT)
    seen: set[str] = set(state.seen)
    fresh: list[MailSummary] = [
        message
        for message in found.messages
        if message.id not in seen and (floor is None or _arrival(message, now) >= floor)
    ]
    unread: list[MailSummary] = [message for message in fresh if message.unread]
    return Gathered(
        items=tuple(sorted((_mail_item(message, account, now) for message in unread), key=lambda item: item.order)),
        listed=tuple(message.id for message in found.messages),
        since=now,
        look_again_s=watcher.minutes * 60.0,
        cut="more mail than one look reads" if found.more and not first else "",
        left_out=len(fresh) - len(unread),
    )


def _declined(event: CalendarEvent) -> bool:
    return event.cancelled or event.your_answer == "declined"


def _event_item(event: CalendarEvent, start: dt.datetime, account: Account, zone: ZoneInfo, key: str) -> Item:
    name: str = google.CALENDAR_NAME if account.provider == "google" else graph.CALENDAR_NAME
    where: str = f", {one_line(event.location, 80)}" if event.location else ""
    local: dt.datetime = start.astimezone(zone)
    lines: list[str] = [
        f"Title: {one_line(event.title, _LINE_CHARS)}",
        f"Starts: {local:%a %d %b %Y %H:%M} ({zone.key})",
        *([f"Place: {one_line(event.location, _LINE_CHARS)}"] if event.location else []),
        *([f"Organizer: {one_line(event.organizer, _LINE_CHARS)}"] if event.organizer else []),
        f"Attendees: {len(event.attendees)}",
        *([f"Notes: {one_line(event.notes, _PREVIEW_CHARS)}"] if event.notes else []),
    ]
    return Item(
        key=key,
        title=one_line(f"{event.title} at {local:%H:%M}{where}", TITLE_CHARS),
        source=f"{name} of {account.address}",
        body="\n".join(lines),
        detail=f"The event is {event.id} in the calendar of {account.address}.",
        order=start.timestamp(),
    )


async def look_calendar(accounts: Accounts, watcher: WatcherRecord, now: float, zone: ZoneInfo) -> Gathered:
    """The events of the watcher's calendar that start within its lead (``minutes`` before) and were not told: timed
    ones only (an all-day event has no hour to tell before), none cancelled or declined, and those with the owner's
    words in their title, place or notes when they gave any. An event comes in again when its time moves. The next
    look is when the next event comes inside its lead, at most five minutes on."""
    account: Account = await accounts.account_of(watcher.account)
    api: AccountApi = _api(accounts, account)
    lead_s: float = watcher.minutes * 60.0
    start: dt.datetime = dt.datetime.fromtimestamp(now, tz=dt.UTC)
    end: dt.datetime = dt.datetime.fromtimestamp(now + lead_s + CALENDAR_LOOK_S, tz=dt.UTC)
    found: EventList = (
        await google.google_events(api, start, end, "", MOST_EVENTS)
        if account.provider == "google"
        else await graph.outlook_events(api, start, end, "", MOST_EVENTS)
    )
    seen: set[str] = set(watcher.state.seen)
    inside: list[Item] = []
    coming: list[float] = []
    for event in found.events:
        if _declined(event) or not isinstance(event.start, dt.datetime):
            continue
        if watcher.words != "" and not graph.has_words(event, watcher.words):
            continue
        starts: float = event.start.timestamp()
        key: str = f"{event.id}@{event.start.isoformat()}"
        if starts <= now or key in seen:
            continue
        if starts - now <= lead_s:
            inside.append(_event_item(event, event.start, account, zone, key))
        else:
            coming.append(starts - lead_s - now)
    soonest: float = min(coming) if coming else CALENDAR_LOOK_S
    return Gathered(
        items=tuple(sorted(inside, key=lambda item: item.order)),
        listed=tuple(item.key for item in inside),
        look_again_s=min(CALENDAR_LOOK_S, max(LEAST_GAP_S, soonest)),
        cut="more events than one look reads" if found.more else "",
    )


def _wanted_by_default(notification: Notification) -> bool:
    """Whether a notification is one the owner hears of without their own words: a review asked of them, a mention, an
    assignment, and a check that failed."""
    if notification.reason in _DEFAULT_REASONS:
        return True
    return notification.reason == "ci_activity" and "fail" in notification.title.casefold()


def _github_item(notification: Notification, key: str) -> Item:
    said: str = _REASON_WORDS.get(notification.reason, notification.reason.replace("_", " ").capitalize() or "Activity")
    lines: list[str] = [
        f"Repository: {one_line(notification.repository, _LINE_CHARS)}",
        f"Kind: {one_line(notification.kind, 60)}",
        f"Reason: {one_line(notification.reason, 60)}",
        f"Title: {one_line(notification.title, _LINE_CHARS)}",
        f"Updated: {notification.updated_at.isoformat()}",
    ]
    return Item(
        key=key,
        title=one_line(f"{said}: {notification.title} ({notification.repository})", TITLE_CHARS),
        source="GitHub notifications",
        body="\n".join(lines),
        detail="It is one of the owner's GitHub notifications; use the GitHub tools you have to read more of it.",
        order=notification.updated_at.timestamp(),
    )


async def look_github(accounts: Accounts, watcher: WatcherRecord, now: float) -> Gathered:
    """The owner's unread GitHub notifications that are new since the last look. Without the owner's words only the
    kinds in :func:`_wanted_by_default` are items; with them every new notification is, for a model to judge. The
    request is conditional and waits at least as long as GitHub asks. Raises a :class:`ConfigError` when no token is
    kept."""
    token: str | None = await asyncio.to_thread(optional_secret, GITHUB_TOKEN_NAME)
    if token is None:
        raise ConfigError(
            "GitHub is not set up: add it on Customize › Connectors with a personal access token",
            {"watcher": watcher.id},
        )
    answer: Notifications = await read_notifications(accounts, token, watcher.state.modified)
    seen: set[str] = set(watcher.state.seen)
    items: list[Item] = []
    listed: list[str] = []
    left_out: int = 0
    for notification in answer.notifications:
        key: str = f"{notification.id}@{notification.updated_at.isoformat()}"
        listed.append(key)
        if key in seen:
            continue
        if watcher.words == "" and not _wanted_by_default(notification):
            left_out += 1
            continue
        items.append(_github_item(notification, key))
    return Gathered(
        items=tuple(sorted(items, key=lambda item: item.order)),
        listed=tuple(listed),
        modified=answer.modified,
        look_again_s=max(watcher.minutes * 60.0, answer.poll_s or 0.0),
        left_out=left_out,
    )
