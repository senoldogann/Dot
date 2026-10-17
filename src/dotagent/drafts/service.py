"""What a draft goes through (item 314): a run writes it, the conversation shows it as a card, and the owner sends it,
with the recipients and words as they stand on the card then, or discards it. Nothing goes out without the owner's
press, whatever the run's approvals, a yolo run's too: a draft is how a run has the owner look first."""

import re
from collections.abc import Sequence

from pydantic import Field

from dotagent.core.errors import DesktopError, InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.desktop.script import run_osascript
from dotagent.drafts.scripts import SCRIPTS, send_arguments
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import DraftChannel, DraftRecord

log = get_logger(__name__)

DRAFT_CREATED_EVENT: str = "draft_created"
DRAFT_DECIDED_EVENT: str = "draft_decided"
# The most recipients a draft names in "to" and in "cc", and the longest subject and body it has.
MAX_RECIPIENTS: int = 20
MAX_SUBJECT_CHARS: int = 300
MAX_BODY_CHARS: int = 20_000
# Why a draft a stopped daemon was handing to its application failed: whether it went out is not known.
STOPPED_WHILE_SENDING: str = (
    "Dot stopped while it handed this draft over, so whether it went out is not known: look in Mail's Sent mailbox or "
    "in the conversation in Messages before you send it again"
)
_EMAIL: re.Pattern[str] = re.compile(r"[^@\s<>\",;:()]+@[^@\s<>\",;:()]+\.[^@\s<>\",;:()]+")
_PHONE: re.Pattern[str] = re.compile(r"\+?[0-9][0-9 ().\-]{4,30}")


class DraftWords(FrozenModel):
    """The recipients and the words of a draft as the owner sends it: what its card holds when Send is pressed."""

    to: tuple[str, ...] = Field(min_length=1, max_length=MAX_RECIPIENTS)
    cc: tuple[str, ...] = Field(max_length=MAX_RECIPIENTS)
    subject: str = Field(max_length=MAX_SUBJECT_CHARS)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)


def draft_problems(channel: DraftChannel, to: Sequence[str], cc: Sequence[str], subject: str, body: str) -> list[str]:
    """What keeps a draft from going out by ``channel``, in words for whoever wrote it; none when it can go."""
    problems: list[str] = []
    if not to:
        problems.append("it names no recipient")
    if body.strip() == "":
        problems.append("its body is empty")
    if channel == "mail":
        wrong: list[str] = [address for address in (*to, *cc) if _EMAIL.fullmatch(address.strip()) is None]
        if wrong:
            problems.append(f"these are not email addresses: {', '.join(wrong)}")
        if subject.strip() == "":
            problems.append("an email needs a subject")
        return problems
    unknown: list[str] = [
        address
        for address in to
        if _EMAIL.fullmatch(address.strip()) is None and _PHONE.fullmatch(address.strip()) is None
    ]
    if unknown:
        problems.append(f"these are neither phone numbers nor email addresses: {', '.join(unknown)}")
    if cc:
        problems.append("a message has no cc")
    if subject.strip() != "":
        problems.append("a message has no subject")
    return problems


async def create_draft(
    store: Store,
    bus: EventBus,
    run_id: str,
    channel: DraftChannel,
    to: Sequence[str],
    cc: Sequence[str],
    subject: str,
    body: str,
) -> DraftRecord:
    """Keep a draft of ``run_id`` and show it in the run's conversation; it waits for the owner."""
    record: DraftRecord = await store.add_draft(
        run_id, channel, [address.strip() for address in to], [address.strip() for address in cc], subject.strip(), body
    )
    await bus.emit(run_id, DRAFT_CREATED_EVENT, record.model_dump(mode="json"), True)
    return record


async def send_draft(store: Store, bus: EventBus, timeout_s: float, draft_id: str, words: DraftWords) -> DraftRecord:
    """The owner's press of Send: hand the draft, with ``words``, to its application. It is ``sent``, or ``failed``
    with what the application said, and the owner may send it again; a draft that no longer waits is a conflict, so
    that a second press, here or on another screen, sends nothing."""
    draft: DraftRecord = await store.get_draft(draft_id)
    to: tuple[str, ...] = tuple(address.strip() for address in words.to)
    cc: tuple[str, ...] = tuple(address.strip() for address in words.cc)
    problems: list[str] = draft_problems(draft.channel, to, cc, words.subject, words.body)
    if problems:
        raise InvalidValueError(f"the draft cannot be sent: {'; '.join(problems)}", {"draft_id": draft_id})
    claimed: DraftRecord = await store.claim_draft(draft_id, to, cc, words.subject.strip(), words.body)
    error: str | None = None
    try:
        await run_osascript("AppleScript", SCRIPTS[claimed.channel], send_arguments(claimed), (), timeout_s)
    except DesktopError as failure:
        said: object = failure.context.get("stderr")
        error = f"{failure.message}: {said}" if isinstance(said, str) and said else failure.message
        log.warning("draft_not_sent", draft_id=draft_id, channel=claimed.channel, error=error)
    settled: DraftRecord = await store.settle_draft(draft_id, error)
    if error is None:
        log.info("draft_sent", draft_id=draft_id, channel=settled.channel, recipients=len(to) + len(cc))
    await bus.emit(settled.run_id, DRAFT_DECIDED_EVENT, settled.model_dump(mode="json"), True)
    return settled


async def discard_draft(store: Store, bus: EventBus, draft_id: str) -> DraftRecord:
    """The owner's press of Discard: the draft is dropped and nothing goes out."""
    discarded: DraftRecord = await store.discard_draft(draft_id)
    await bus.emit(discarded.run_id, DRAFT_DECIDED_EVENT, discarded.model_dump(mode="json"), True)
    return discarded


async def fail_unsettled_drafts(store: Store, bus: EventBus) -> int:
    """At the daemon's start: the drafts a stopped daemon was handing over failed (:data:`STOPPED_WHILE_SENDING`).
    Returns how many."""
    failed: list[DraftRecord] = await store.fail_sending_drafts(STOPPED_WHILE_SENDING)
    for draft in failed:
        await bus.emit(draft.run_id, DRAFT_DECIDED_EVENT, draft.model_dump(mode="json"), True)
    return len(failed)
