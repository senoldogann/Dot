"""What the mail and the calendars of an account read as, whichever provider holds them (item 424)."""

import datetime as dt

from pydantic import BaseModel, ConfigDict

from dotagent.core.types import FrozenModel


class ProviderAnswer(BaseModel):
    """The base of a provider's answer as Dot reads it: what Dot does not read is left out."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class MailSummary(FrozenModel):
    """A message as a list of them shows it; ``received`` None when the provider gave no time."""

    id: str
    sender: str
    to: tuple[str, ...]
    subject: str
    received: dt.datetime | None
    snippet: str
    unread: bool


class Attachment(FrozenModel):
    name: str
    media_type: str
    size: int


class MailMessage(FrozenModel):
    """A message read whole: its summary, who else it went to, its text and the files it carries."""

    summary: MailSummary
    cc: tuple[str, ...]
    text: str
    attachments: tuple[Attachment, ...]


class Attendee(FrozenModel):
    address: str
    name: str
    # The attendee's answer in the provider's words (accepted, declined, tentative, needsAction, none…).
    answer: str


class CalendarEvent(FrozenModel):
    """An event: timed, with aware ``start`` and ``end``, or all day, with dates, ``end`` the day after its last;
    ``your_answer`` is the owner's answer to it in the provider's words (``organizer`` for their own), empty when the
    provider says none."""

    id: str
    title: str
    start: dt.datetime | dt.date
    end: dt.datetime | dt.date
    location: str
    organizer: str
    attendees: tuple[Attendee, ...]
    your_answer: str
    notes: str
    link: str
    cancelled: bool
