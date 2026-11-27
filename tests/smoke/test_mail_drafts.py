"""Drafts in the owner's own Gmail and Outlook (item 2047), end to end against stand-ins of Google's and Microsoft's
APIs (an ``httpx.MockTransport``) and a keychain held in memory: an account is signed in through the real sign-in, the
``mail_draft`` tool makes an answer in the thread of a message found, its card is the chat's draft, a second call
writes it again, and the owner's Send on the card writes the card's words into the mailbox's draft and sends it. Only
that press sends: the tool never does, and it is classified as a draft (nothing asked, nothing flagged). The owner's
Discard deletes the mailbox's draft; a sign-in made before Dot asked to write drafts is refused with how to give it."""

import base64
import email
import json
import time
from collections.abc import AsyncIterator
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio

from dotagent.connectors.types import OutgoingMail
from dotagent.core.errors import AccountApiError, RunConflict
from dotagent.core.types import JsonObject
from dotagent.drafts.service import (
    DRAFT_CREATED_EVENT,
    DraftWords,
    discard_draft,
    rewrite_mailbox_draft,
    send_draft,
)
from dotagent.events import EventBus
from dotagent.oauth.accounts import Accounts, CameBack
from dotagent.oauth.keychain import Keychain
from dotagent.oauth.providers import GOOGLE, MICROSOFT, ProviderId
from dotagent.oauth.tokens import Tokens
from dotagent.store.db import Store
from dotagent.store.models import DraftMailbox, DraftRecord, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.groups import ACCOUNTS_GROUP, group_of
from dotagent.tools.mail_drafts import MAIL_DRAFT_TOOL, build_mail_draft_tool

pytestmark = pytest.mark.asyncio

_GMAIL: str = "/gmail/v1/users/me"
_ORIGINAL_ID: str = "<CAB123@mail.example.com>"


class _MemoryKeychain:
    """The tokens of the accounts, held in memory: the test touches no keychain of the Mac."""

    def __init__(self) -> None:
        self.held: dict[str, Tokens] = {}

    async def save(self, provider: ProviderId, address: str, tokens: Tokens) -> None:
        self.held[f"{provider}:{address}"] = tokens

    async def read(self, provider: ProviderId, address: str) -> Tokens | None:
        return self.held.get(f"{provider}:{address}")

    async def forget(self, provider: ProviderId, address: str) -> None:
        self.held.pop(f"{provider}:{address}", None)


class _Mailboxes:
    """Gmail's and Graph's drafts as far as the tool and the card need them; each call is recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, JsonObject | None]] = []
        # The status Graph answers a draft's creation with: 403 for a sign-in made before item 2047.
        self.graph_create_status: int = 201
        # The status Gmail answers a draft's send with: a 503 may come after it took the call.
        self.gmail_send_status: int = 200

    def _token(self, request: httpx.Request, mark: str) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": f"at-{mark}", "refresh_token": f"rt-{mark}", "expires_in": 3600}
        )

    def _record(self, request: httpx.Request) -> JsonObject | None:
        body: JsonObject | None = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.raw_path.split(b"?")[0].decode(), body))
        return body

    def _gmail(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer at-g"
        path: str = request.url.path
        if request.method == "GET" and path == f"{_GMAIL}/messages/m-1":
            assert request.url.params.get_list("metadataHeaders") == ["Message-ID", "References"]
            return httpx.Response(
                200,
                json={
                    "id": "m-1",
                    "threadId": "t-1",
                    "payload": {"headers": [{"name": "Message-Id", "value": _ORIGINAL_ID}]},
                },
            )
        self._record(request)
        if request.method == "POST" and path == f"{_GMAIL}/drafts":
            return httpx.Response(200, json={"id": "r-7", "message": {"id": "17a", "threadId": "t-1"}})
        if request.method == "PUT" and path == f"{_GMAIL}/drafts/r-7":
            return httpx.Response(200, json={"id": "r-7", "message": {"id": "17b", "threadId": "t-1"}})
        if request.method == "POST" and path == f"{_GMAIL}/drafts/send":
            if self.gmail_send_status == 503:
                return httpx.Response(503, json={"error": {"code": 503, "message": "Backend Error"}})
            return httpx.Response(200, json={"id": "17b", "threadId": "t-1", "labelIds": ["SENT"]})
        raise AssertionError(f"a Gmail call the stand-in does not take: {request.method} {path}")

    def _graph(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer at-m"
        self._record(request)
        raw: bytes = request.url.raw_path.split(b"?")[0]
        if request.method == "POST" and raw == b"/v1.0/me/messages/AAMk%2F1%3D/createReply":
            if self.graph_create_status == 403:
                return httpx.Response(
                    403, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}
                )
            return httpx.Response(201, json={"id": "D/1", "webLink": "https://outlook.live.com/owa/?ItemID=D1"})
        if request.method == "DELETE" and raw == b"/v1.0/me/messages/D%2F1":
            return httpx.Response(204)
        raise AssertionError(f"a Graph call the stand-in does not take: {request.method} {request.url}")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if url == GOOGLE.token_url:
            return self._token(request, "g")
        if url == MICROSOFT.token_url:
            return self._token(request, "m")
        if url == GOOGLE.profile_url:
            return httpx.Response(200, json={"email": "ada@gmail.com"})
        if url == MICROSOFT.profile_url:
            return httpx.Response(200, json={"mail": "ada@outlook.com"})
        if request.url.host == "gmail.googleapis.com":
            return self._gmail(request)
        if request.url.host == "graph.microsoft.com":
            return self._graph(request)
        raise AssertionError(f"a request no provider takes: {request.method} {url}")


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield opened
    finally:
        await opened.close()


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOT_GOOGLE_CLIENT_ID", "client-1")
    monkeypatch.setenv("DOT_GOOGLE_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("DOT_MS_CLIENT_ID", "client-ms")


async def _accounts(store: Store, mailboxes: _Mailboxes) -> Accounts:
    """One account of each provider, signed in through the sign-in itself."""
    accounts: Accounts = Accounts(store, cast(Keychain, _MemoryKeychain()), httpx.MockTransport(mailboxes))
    provider: ProviderId
    for provider in ("google", "microsoft"):
        link: str = await accounts.start(provider, 8765, time.time())
        came: CameBack | None = await accounts.come_back(
            parse_qs(urlsplit(link).query)["state"][0], "the-code", None, None, time.time()
        )
        assert came is not None and came.kind == "signed_in", came
    return accounts


async def _chat_run(store: Store) -> RunRecord:
    chat: ThreadRecord = await store.create_thread("Mail", "ui")
    return await store.create_run(
        chat.id,
        "Answer Bob",
        "task",
        "scripted/x",
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )


async def _call(store: Store, accounts: Accounts, run: RunRecord, arguments: JsonObject) -> ToolResult:
    ctx: ToolContext = cast(
        ToolContext,
        SimpleNamespace(
            runtime=SimpleNamespace(store=store, bus=EventBus(store), accounts=accounts),
            run_id=run.id,
            thread_id=run.thread_id,
        ),
    )
    prepared: PreparedCall = await build_mail_draft_tool().prepare(arguments, ctx)
    # A draft reaches no one: nothing for the policy to ask or refuse in any mode (docs/freedom.md).
    assert (prepared.action.kind, prepared.action.risk, prepared.action.flags) == ("notes_write", "safe", ())
    return await prepared.run(ctx)


def _thread_of(body: JsonObject | None) -> object:
    assert body is not None
    message: object = body["message"]
    assert isinstance(message, dict)
    return message.get("threadId")


def _raw_message(body: JsonObject | None) -> Message:
    assert body is not None
    message: object = body["message"]
    assert isinstance(message, dict)
    raw: object = message["raw"]
    assert isinstance(raw, str)
    return email.message_from_bytes(base64.urlsafe_b64decode(raw))


async def test_a_gmail_reply_draft_is_in_its_thread_rewritten_and_sent_only_by_the_owners_press(
    store: Store, clients: None
) -> None:
    assert group_of(MAIL_DRAFT_TOOL) == ACCOUNTS_GROUP
    mailboxes: _Mailboxes = _Mailboxes()
    accounts: Accounts = await _accounts(store, mailboxes)
    run: RunRecord = await _chat_run(store)
    made: ToolResult = await _call(
        store,
        accounts,
        run,
        {
            "account": "ADA@gmail.com",
            "to": ["bob@example.com"],
            "subject": "Re: Invoice 42",
            "body": "Paid today.",
            "reply_to": "m-1",
        },
    )
    assert (
        not made.is_error and "Drafts of Gmail of ada@gmail.com" in made.text and "Nothing has been sent" in made.text
    )
    method, path, body = mailboxes.calls[0]
    assert (method, path) == ("POST", f"{_GMAIL}/drafts")
    assert _thread_of(body) == "t-1"
    sent: Message = _raw_message(body)
    assert (sent["To"], sent["Subject"], sent["In-Reply-To"]) == ("bob@example.com", "Re: Invoice 42", _ORIGINAL_ID)
    assert sent.get_payload(decode=True) == b"Paid today.\r\n"
    [draft] = await store.thread_drafts(run.thread_id)
    assert draft.mailbox is not None and (draft.mailbox.provider, draft.mailbox.remote_id) == ("google", "r-7")
    assert draft.mailbox.link == "https://mail.google.com/mail/u/ada@gmail.com/#drafts?compose=17a"
    shown: list[EventRecord] = await store.events_of_kind(run.id, DRAFT_CREATED_EVENT, 5)
    assert [event.data["id"] for event in shown] == [draft.id]

    # The run writes it again, as the owner asked in the chat: the same draft of the mailbox, still not sent.
    again: ToolResult = await _call(
        store,
        accounts,
        run,
        {
            "account": "ada@gmail.com",
            "to": ["bob@example.com"],
            "subject": "Re: Invoice 42",
            "body": "Paid.",
            "draft": draft.id,
        },
    )
    assert not again.is_error
    assert [(call[0], call[1]) for call in mailboxes.calls] == [
        ("POST", f"{_GMAIL}/drafts"),
        ("PUT", f"{_GMAIL}/drafts/r-7"),
    ]
    rewritten: DraftRecord = await store.get_draft(draft.id)
    assert rewritten.body == "Paid." and rewritten.mailbox is not None and rewritten.mailbox.link.endswith("=17b")

    # The owner's Send on the card: the card's words go into the mailbox's draft, then it is sent, once.
    mailboxes.calls.clear()
    words: DraftWords = DraftWords(
        to=("bob@example.com",), cc=("cy@example.com",), subject="Re: Invoice 42", body="Paid, thanks."
    )
    settled: DraftRecord = await send_draft(store, EventBus(store), accounts, 30.0, draft.id, words)
    assert settled.status == "sent" and settled.error is None
    assert [(call[0], call[1]) for call in mailboxes.calls] == [
        ("PUT", f"{_GMAIL}/drafts/r-7"),
        ("POST", f"{_GMAIL}/drafts/send"),
    ]
    final: Message = _raw_message(mailboxes.calls[0][2])
    assert (final["Cc"], final["In-Reply-To"]) == ("cy@example.com", _ORIGINAL_ID)
    assert mailboxes.calls[1][2] == {"id": "r-7"}


async def test_an_outlook_draft_is_deleted_by_the_owners_discard_and_an_old_sign_in_is_told_to_consent(
    store: Store, clients: None
) -> None:
    mailboxes: _Mailboxes = _Mailboxes()
    accounts: Accounts = await _accounts(store, mailboxes)
    run: RunRecord = await _chat_run(store)
    arguments: JsonObject = {
        "account": "ada@outlook.com",
        "to": ["carl@example.com"],
        "subject": "RE: Invoice 43",
        "body": "Will pay Monday.",
        "reply_to": "AAMk/1=",
    }
    made: ToolResult = await _call(store, accounts, run, arguments)
    assert "Drafts of Outlook of ada@outlook.com" in made.text
    method, _, body = mailboxes.calls[0]
    assert method == "POST" and body == {
        "message": {
            "subject": "RE: Invoice 43",
            "body": {"contentType": "Text", "content": "Will pay Monday."},
            "toRecipients": [{"emailAddress": {"address": "carl@example.com"}}],
            "ccRecipients": [],
        }
    }
    [draft] = await store.thread_drafts(run.thread_id)
    assert draft.mailbox is not None and draft.mailbox.link == "https://outlook.live.com/owa/?ItemID=D1"
    discarded: DraftRecord = await discard_draft(store, EventBus(store), accounts, draft.id)
    assert discarded.status == "discarded" and mailboxes.calls[-1][:2] == ("DELETE", "/v1.0/me/messages/D%2F1")

    # A sign-in made before Dot asked to write drafts: Graph refuses, and the run reads how the owner gives it.
    mailboxes.graph_create_status = 403
    with pytest.raises(AccountApiError, match=r"sign in to it again in Settings › Connectors › Accounts"):
        await _call(store, accounts, run, arguments)
    assert await store.thread_drafts(run.thread_id) == []


async def test_a_send_answered_5xx_is_not_known_a_sent_draft_is_not_written_again_and_a_signed_out_one_is_dropped(
    store: Store, clients: None
) -> None:
    mailboxes: _Mailboxes = _Mailboxes()
    accounts: Accounts = await _accounts(store, mailboxes)
    run: RunRecord = await _chat_run(store)
    arguments: JsonObject = {"account": "ada@gmail.com", "to": ["bob@example.com"], "subject": "Hi", "body": "Hello."}
    await _call(store, accounts, run, arguments)
    [draft] = await store.thread_drafts(run.thread_id)
    words: DraftWords = DraftWords(to=draft.to, cc=(), subject=draft.subject, body=draft.body)

    # A 5xx on the send may come after Gmail sent it: the card says so, and nothing is sent again by itself.
    mailboxes.gmail_send_status = 503
    failed: DraftRecord = await send_draft(store, EventBus(store), accounts, 30.0, draft.id, words)
    assert failed.status == "failed" and failed.error is not None
    assert "whether it took the call is not known" in failed.error, failed.error
    assert [call[1] for call in mailboxes.calls].count(f"{_GMAIL}/drafts/send") == 1

    # Sent on a second press, it is no longer written again by a run: a conflict before any call to the mailbox.
    mailboxes.gmail_send_status = 200
    assert (await send_draft(store, EventBus(store), accounts, 30.0, draft.id, words)).status == "sent"
    before: int = len(mailboxes.calls)
    with pytest.raises(RunConflict, match="the draft is sent"):
        await rewrite_mailbox_draft(
            store,
            EventBus(store),
            accounts,
            draft.id,
            OutgoingMail(to=("bob@example.com",), cc=(), subject="Hi", body="Other words."),
        )
    assert len(mailboxes.calls) == before

    # A draft whose account was signed out since: Discard drops the card and says the draft stays in that mailbox.
    gone: DraftRecord = await store.add_draft(
        run.id,
        "mail",
        ["bob@example.com"],
        [],
        "Hi",
        "Hello.",
        DraftMailbox(provider="google", account="gone@gmail.com", remote_id="r-9", reply_to=None, link=""),
    )
    dropped: DraftRecord = await discard_draft(store, EventBus(store), accounts, gone.id)
    assert dropped.status == "discarded" and dropped.error is not None
    assert dropped.error.startswith("The draft stays in the Drafts of gone@gmail.com: no account"), dropped.error
