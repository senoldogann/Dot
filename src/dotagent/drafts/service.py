"""What a draft goes through (item 314): a run writes it, the conversation shows it as a card, and the owner sends it,
with the recipients and words as they stand on the card then, or discards it. Nothing goes out without the owner's
press, whatever the run's approvals, a yolo run's too: a draft is how a run has the owner look first.

An email draft may also be in the owner's own Gmail or Outlook (item 2047), made through the account they signed in:
the owner opens it there too, and may send it there. Its Send on the card writes the card's words into that draft and
sends it through the account; its Discard deletes it from the mailbox. Without an account, Mail sends it."""

import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from pydantic import Field

from dotagent.channels.handles import names_a_handle, paired_handles
from dotagent.connectors import google, graph
from dotagent.connectors.http import AccountApi
from dotagent.connectors.types import MailboxDraft, OutgoingMail
from dotagent.core.errors import (
    AccountApiError,
    DesktopError,
    DotError,
    InvalidValueError,
    NotFoundError,
    RunConflict,
    SignInError,
)
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.desktop.script import run_osascript
from dotagent.drafts.scripts import SCRIPTS, send_calls
from dotagent.events import EventBus
from dotagent.oauth.accounts import Accounts
from dotagent.oauth.tokens import Account
from dotagent.store.db import Store
from dotagent.store.models import DraftChannel, DraftMailbox, DraftRecord

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
# One writer at a time of a draft in a mailbox (item 2047): a run writing it again, the owner's Send and Discard. The
# lock holds within one process: the daemon's runs and its routes, so such a run's write cannot land between Send's
# write of the card's words and the send itself. A run of `dotx chat` or `dotx resume`, which runs in the command's own
# process, is not held by it: there the store's check of the draft's status, made before the write, is all there is.
# A draft's lock is dropped once it is sent, discarded or gone, after which every write of it is a conflict.
_MAILBOX_LOCKS: dict[str, asyncio.Lock] = {}
# How many writers hold or wait for each draft's lock: asyncio releases a lock before it wakes the next waiter, so
# ``locked()`` alone cannot say that nobody waits.
_MAILBOX_USERS: dict[str, int] = {}
_DECIDED: frozenset[str] = frozenset({"sent", "discarded"})


PROVIDER_NAMES: dict[str, str] = {"google": "Gmail", "microsoft": "Outlook"}


@asynccontextmanager
async def _writing(store: Store, draft_id: str) -> AsyncIterator[None]:
    """Hold the draft's lock; once it is let go, a draft that is decided (or gone) drops its lock, whatever the
    holder did, a conflict too."""
    lock: asyncio.Lock = _MAILBOX_LOCKS.setdefault(draft_id, asyncio.Lock())
    _MAILBOX_USERS[draft_id] = _MAILBOX_USERS.get(draft_id, 0) + 1
    try:
        async with lock:
            yield
    finally:
        _MAILBOX_USERS[draft_id] -= 1
        if _MAILBOX_USERS[draft_id] == 0:
            del _MAILBOX_USERS[draft_id]
        try:
            decided: bool = (await store.get_draft(draft_id)).status in _DECIDED
        except NotFoundError:
            decided = True
        if decided and draft_id not in _MAILBOX_USERS:
            _MAILBOX_LOCKS.pop(draft_id, None)


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


async def paired_recipients(store: Store, channel: DraftChannel, to: Sequence[str]) -> list[str]:
    """The recipients of a message draft that are a number or an address paired with the iMessage channel (item 572):
    the owner's own conversation with themselves, which Dot reads as the owner's commands and answers in only through
    the channel. A message of a run written there, as a draft the owner sends or as one that goes out another way,
    would come back as the owner's untainted command. An email goes to Mail, not to that conversation."""
    if channel != "message":
        return []
    handles: tuple[str, ...] = await paired_handles(store)
    return [address.strip() for address in to if names_a_handle(address, handles)]


def paired_problem(named: Sequence[str]) -> str:
    """Why a draft that names ``named``, paired with the iMessage channel, is refused."""
    return (
        f"{', '.join(named)} is paired with Dot's iMessage channel: that is the owner's own conversation with "
        "themselves, where what is written is read as their commands, and Dot answers there only through the channel"
    )


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
    """Keep a draft of ``run_id`` and show it in the run's conversation; it waits for the owner. A message draft to a
    number or an address paired with the iMessage channel is refused."""
    paired: list[str] = await paired_recipients(store, channel, to)
    if paired:
        raise InvalidValueError(
            f"this draft cannot be made: {paired_problem(paired)}. Say what you wanted to tell the owner in your "
            "answer instead",
            {"channel": channel},
        )
    record: DraftRecord = await store.add_draft(
        run_id,
        channel,
        [address.strip() for address in to],
        [address.strip() for address in cc],
        subject.strip(),
        body,
        None,
    )
    await bus.emit(run_id, DRAFT_CREATED_EVENT, record.model_dump(mode="json"), True)
    return record


async def _send_through_application(claimed: DraftRecord, timeout_s: float) -> str | None:
    """Hand ``claimed`` to Mail or Messages; why it failed, None when it went."""
    # The recipients of a message the application took before one failed: a retry would send them the message again.
    reached: list[str] = []
    try:
        for arguments in send_calls(claimed):
            await run_osascript("AppleScript", SCRIPTS[claimed.channel], arguments, (), timeout_s)
            if claimed.channel == "message":
                reached.append(arguments[1])
    except DesktopError as failure:
        said: object = failure.context.get("stderr")
        error: str = f"{failure.message}: {said}" if isinstance(said, str) and said else failure.message
        if reached:
            sent: str = ", ".join(reached)
            error += f"; it was already sent to {sent}, so take them off the recipients before you send it again"
        log.warning("draft_not_sent", draft_id=claimed.id, channel=claimed.channel, error=error, reached=len(reached))
        return error
    return None


def mailbox_problem(failure: DotError, account: str) -> str:
    """What the owner reads of a call to their mailbox that failed: the provider's words, and for a 403, which is a
    sign-in made before Dot asked to write drafts (item 2047), how to give it that."""
    if failure.context.get("status") == 403:
        return f"{failure.message}. {drafts_advice(account)}"
    return failure.message


def drafts_advice(account: str) -> str:
    """How the owner lets Dot write drafts in ``account`` (item 2047)."""
    return (
        f"The sign-in of {account} does not let Dot write drafts: the owner presses Allow drafts and sending beside it "
        "in Settings › Connectors › Accounts, on the Mac, and says yes on the provider's page"
    )


async def _drafting_api(accounts: Accounts, address: str) -> AccountApi:
    """The calls of the account ``address``, whose sign-in must allow drafts (item 2047): a sign-in reads only."""
    account: Account = await accounts.account_of(address)
    if not account.drafts:
        raise SignInError(drafts_advice(account.address), {"provider": account.provider})
    return AccountApi(accounts, account.provider, account.address)


async def _changed_in_mailbox(api: AccountApi, mailbox: DraftMailbox) -> bool:
    """Whether the draft was changed in the mailbox (in Gmail or Outlook, by the owner) since Dot last wrote it."""
    if api.provider == "google":
        now: str = await google.gmail_draft_version(api, mailbox.remote_id)
    else:
        now = await graph.outlook_draft_version(api, mailbox.remote_id)
    return now != mailbox.version


def changed_problem(mailbox: DraftMailbox) -> str:
    name: str = PROVIDER_NAMES[mailbox.provider]
    return (
        f"it was changed in {name} after this card was shown, and Send here would write over that change: send it "
        f"from {name}, or discard this card"
    )


async def write_mailbox_draft(
    api: AccountApi, mail: OutgoingMail, reply_to: str | None, remote_id: str | None
) -> MailboxDraft:
    """``mail`` as a draft of the account's mailbox: a new one, in the thread of ``reply_to`` when it names a message,
    or the draft ``remote_id`` rewritten."""
    if api.provider == "google":
        if remote_id is None:
            return await google.create_gmail_draft(api, mail, reply_to)
        return await google.update_gmail_draft(api, remote_id, mail, reply_to)
    if remote_id is None:
        return await graph.create_outlook_draft(api, mail, reply_to)
    return await graph.update_outlook_draft(api, remote_id, mail)


class MailboxSend(FrozenModel):
    """What came of a Send through a mailbox: why it failed (None when it went), and the draft's place as it stands
    after it, whose version moved when the card's words were written before a send that failed."""

    error: str | None
    mailbox: DraftMailbox


async def _send_through_mailbox(accounts: Accounts, claimed: DraftRecord, mailbox: DraftMailbox) -> MailboxSend:
    """Write the card's words into the mailbox's draft, then send it through the account. A draft changed in the
    mailbox since Dot wrote it is not written over; a send whose answer was lost may have gone: the words say so."""
    written: DraftMailbox = mailbox
    mail: OutgoingMail = OutgoingMail(to=claimed.to, cc=claimed.cc, subject=claimed.subject, body=claimed.body)
    try:
        api: AccountApi = await _drafting_api(accounts, mailbox.account)
        # The owner's edit in the mailbox is not written over by the card's words.
        if await _changed_in_mailbox(api, mailbox):
            log.warning("draft_not_sent", draft_id=claimed.id, channel=claimed.channel, provider=mailbox.provider)
            return MailboxSend(error=changed_problem(mailbox), mailbox=mailbox)
        made: MailboxDraft = await write_mailbox_draft(api, mail, mailbox.reply_to, mailbox.remote_id)
        written = mailbox.model_copy(update={"version": made.version, "link": made.link})
        if api.provider == "google":
            await google.send_gmail_draft(api, mailbox.remote_id)
        else:
            await graph.send_outlook_draft(api, mailbox.remote_id)
    except (AccountApiError, SignInError) as failure:
        error: str = mailbox_problem(failure, mailbox.account)
        if failure.context.get("status") == 404:
            error += "; the draft is no longer in the mailbox: it was sent or deleted there"
        log.warning(
            "draft_not_sent",
            draft_id=claimed.id,
            channel=claimed.channel,
            provider=mailbox.provider,
            error_type=type(failure).__name__,
            status=failure.context.get("status"),
        )
        return MailboxSend(error=error, mailbox=written)
    return MailboxSend(error=None, mailbox=written)


async def create_mailbox_draft(
    store: Store,
    bus: EventBus,
    accounts: Accounts,
    run_id: str,
    address: str,
    mail: OutgoingMail,
    reply_to: str | None,
) -> DraftRecord:
    """An email draft of ``run_id`` in the mailbox of the account ``address`` (item 2047), an answer to the message
    ``reply_to`` in its thread when it names one, and its card in the run's conversation; it waits for the owner. A
    sign-in that may not write drafts is refused with how to give it that."""
    api: AccountApi = await _drafting_api(accounts, address)
    try:
        made: MailboxDraft = await write_mailbox_draft(api, mail, reply_to, None)
    except AccountApiError as failure:
        raise AccountApiError(mailbox_problem(failure, api.address), failure.context) from failure
    mailbox: DraftMailbox = DraftMailbox(
        provider=api.provider,
        account=api.address,
        remote_id=made.remote_id,
        version=made.version,
        reply_to=reply_to,
        link=made.link,
    )
    record: DraftRecord = await store.add_draft(run_id, "mail", mail.to, mail.cc, mail.subject, mail.body, mailbox)
    await bus.emit(run_id, DRAFT_CREATED_EVENT, record.model_dump(mode="json"), True)
    return record


async def rewrite_mailbox_draft(
    store: Store, bus: EventBus, accounts: Accounts, draft_id: str, mail: OutgoingMail
) -> DraftRecord:
    """The mailbox's draft ``draft_id``, which must still take an answer, with ``mail`` in place of its words, in the
    mailbox and on its card. It is read again under the draft's lock, so that a draft the owner sent or discarded
    meanwhile is a conflict before anything is written to the mailbox, and so is one the owner changed in the
    mailbox since Dot wrote it."""
    async with _writing(store, draft_id):
        draft: DraftRecord = await store.get_draft(draft_id)
        mailbox: DraftMailbox | None = draft.mailbox
        if mailbox is None:
            raise InvalidValueError(
                "this draft is not in a mailbox: write a new one", {"draft_id": draft.id, "channel": draft.channel}
            )
        if draft.status not in ("waiting", "failed"):
            raise RunConflict(f"the draft is {draft.status}, not waiting", {"draft_id": draft.id})
        api: AccountApi = await _drafting_api(accounts, mailbox.account)
        try:
            if await _changed_in_mailbox(api, mailbox):
                raise RunConflict(
                    f"the owner changed this draft in {PROVIDER_NAMES[mailbox.provider]} after it was written: do "
                    "not write it again; tell them what you would change",
                    {"draft_id": draft.id},
                )
            made: MailboxDraft = await write_mailbox_draft(api, mail, mailbox.reply_to, mailbox.remote_id)
        except AccountApiError as failure:
            raise AccountApiError(mailbox_problem(failure, mailbox.account), failure.context) from failure
        rewritten: DraftRecord = await store.rewrite_draft(
            draft.id,
            mail.to,
            mail.cc,
            mail.subject,
            mail.body,
            mailbox.model_copy(update={"link": made.link, "version": made.version}),
        )
    await bus.emit(rewritten.run_id, DRAFT_DECIDED_EVENT, rewritten.model_dump(mode="json"), True)
    return rewritten


async def send_draft(
    store: Store, bus: EventBus, accounts: Accounts, timeout_s: float, draft_id: str, words: DraftWords
) -> DraftRecord:
    """The owner's press of Send: hand the draft, with ``words``, to its application, or to the account whose mailbox
    holds it. It is ``sent``, or ``failed`` with what the application or the provider said, and the owner may send it
    again; a draft that no longer waits is a conflict, so that a second press, here or on another screen, sends
    nothing."""
    draft: DraftRecord = await store.get_draft(draft_id)
    to: tuple[str, ...] = tuple(address.strip() for address in words.to)
    cc: tuple[str, ...] = tuple(address.strip() for address in words.cc)
    problems: list[str] = draft_problems(draft.channel, to, cc, words.subject, words.body)
    # The owner may have put their own number on the card: what a run wrote is not sent into the conversation Dot reads
    # as their commands.
    paired: list[str] = await paired_recipients(store, draft.channel, to)
    if paired:
        problems.append(paired_problem(paired))
    if problems:
        raise InvalidValueError(f"the draft cannot be sent: {'; '.join(problems)}", {"draft_id": draft_id})
    async with _writing(store, draft_id):
        claimed: DraftRecord = await store.claim_draft(draft_id, to, cc, words.subject.strip(), words.body)
        error: str | None
        if claimed.mailbox is None:
            error = await _send_through_application(claimed, timeout_s)
        else:
            through: MailboxSend = await _send_through_mailbox(accounts, claimed, claimed.mailbox)
            error = through.error
            if through.mailbox != claimed.mailbox:
                # The card's words were written: a later Send or rewrite compares the mailbox with them.
                await store.set_draft_mailbox(draft_id, through.mailbox)
        settled: DraftRecord = await store.settle_draft(draft_id, error)
    if error is None:
        log.info("draft_sent", draft_id=draft_id, channel=settled.channel, recipients=len(to) + len(cc))
    await bus.emit(settled.run_id, DRAFT_DECIDED_EVENT, settled.model_dump(mode="json"), True)
    return settled


async def discard_draft(store: Store, bus: EventBus, accounts: Accounts, draft_id: str) -> DraftRecord:
    """The owner's press of Discard: the draft is dropped and nothing goes out. One in the owner's mailbox is deleted
    from it first; one already gone from it (sent or deleted there) is dropped all the same, and so is one the mailbox
    could not be asked to delete (the account signed out, the provider's refusal): the card says it stays there."""
    async with _writing(store, draft_id):
        draft: DraftRecord = await store.get_draft(draft_id)
        left: str | None = None
        if draft.mailbox is not None and draft.status in ("waiting", "failed"):
            left = await _delete_from_mailbox(accounts, draft.mailbox)
        discarded: DraftRecord = await store.discard_draft(draft_id, left)
    await bus.emit(discarded.run_id, DRAFT_DECIDED_EVENT, discarded.model_dump(mode="json"), True)
    return discarded


async def _delete_from_mailbox(accounts: Accounts, mailbox: DraftMailbox) -> str | None:
    """Delete the mailbox's draft; why it stays there, None when it is gone."""
    try:
        api: AccountApi = await _drafting_api(accounts, mailbox.account)
        if api.provider == "google":
            await google.delete_gmail_draft(api, mailbox.remote_id)
        else:
            await graph.delete_outlook_draft(api, mailbox.remote_id)
    except (AccountApiError, SignInError) as failure:
        if failure.context.get("status") == 404:
            return None
        log.warning(
            "mailbox_draft_kept",
            provider=mailbox.provider,
            error_type=type(failure).__name__,
            status=failure.context.get("status"),
        )
        # A 5xx or a lost answer may come after the provider deleted it.
        status: object = failure.context.get("status")
        unknown: bool = isinstance(failure, AccountApiError) and (
            "error" in failure.context or (isinstance(status, int) and status >= 500)
        )
        stays: str = "may stay" if unknown else "stays"
        return f"The draft {stays} in the Drafts of {mailbox.account}: {mailbox_problem(failure, mailbox.account)}"
    return None


async def fail_unsettled_drafts(store: Store, bus: EventBus) -> int:
    """At the daemon's start: the drafts a stopped daemon was handing over failed (:data:`STOPPED_WHILE_SENDING`).
    Returns how many."""
    failed: list[DraftRecord] = await store.fail_sending_drafts(STOPPED_WHILE_SENDING)
    for draft in failed:
        await bus.emit(draft.run_id, DRAFT_DECIDED_EVENT, draft.model_dump(mode="json"), True)
    return len(failed)
