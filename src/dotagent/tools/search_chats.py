"""The search_chats tool (item 1740): find what was said in any chat of this Mac, without a model call.

``recall`` reads back the records a compaction named inside one run, and ``chats`` finds a chat by the words of its
title. This finds a thing by what was said: a full-text search (SQLite FTS5, kept current by the store's own triggers)
over every user's and assistant's message of the chats and every run's answer, in three modes after Hermes Agent's
session search, which found its way to a past conversation the same way (find, scroll, read):

- ``find``: the best hits for some words, each with its chat, when it was said, whose words they are and the text
  around the match;
- ``around``: the messages next to a hit, from the chat's id and the place the hit named;
- ``read``: one run, by its id: its goal, how it ended and its answer.

Every answer is bounded, so that a search never fills a run's context. What it quotes may carry what a page or a mail
said to the run that read it: the words of a chat whose runs read content third parties can author are fenced and the
result marked untrusted, so the run that reads it is tainted as that chat was (item 243), exactly as ``read_chat``
does; the secrets Dot holds are masked. Nothing here changes anything.
"""

import asyncio
import secrets
import time
from typing import Literal

from pydantic import Field

from dotagent.core.errors import NotFoundError, ToolInputError
from dotagent.core.text import redact_known, truncate_tail, unclosing
from dotagent.memory.words import search_terms
from dotagent.policy.types import ActionInfo
from dotagent.proactive import WATCHER_SOURCE
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import ChatHit, ChatMessage, RunRecord, ThreadRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.office import ago

SEARCH_CHATS_TOOL: str = "search_chats"
_NONCE_BYTES: int = 8
# How many hits one find returns, how long a hit's words and a chat's title are, how many messages each side of a hit
# ``around`` brings and how long each is, how much of a run ``read`` shows, and the whole answer's size.
MOST_HITS: int = 8
_HIT_CHARS: int = 260
_TITLE_CHARS: int = 80
_AROUND: int = 2
_MESSAGE_CHARS: int = 900
_GOAL_CHARS: int = 600
_RESULT_CHARS: int = 3000
_ANSWER_CHARS: int = 7000

type Mode = Literal["find", "around", "read"]


class SearchChatsArgs(ToolArgs):
    mode: Mode = Field(description="find: words to hit; around: messages by a hit; read: a run by id.")
    query: str = Field(default="", max_length=300, description="find: the words.")
    chat: str = Field(default="", description="around: the chat id of a hit.")
    seq: int = Field(default=-1, ge=-1, description="around: the place of a hit.")
    run: str = Field(default="", description="read: the run id.")


class _Words:
    """The lines of an answer, kept apart by whether they quote a chat that read content third parties can author:
    ``plain`` for the chats that did not, ``outside`` for the ones that did, which are fenced."""

    def __init__(self) -> None:
        self.plain: list[str] = []
        self.outside: list[str] = []

    def add(self, line: str, outside: bool) -> None:
        (self.outside if outside else self.plain).append(line)


def _one_line(text: str, chars: int) -> str:
    return truncate_tail(" ".join(text.split()), chars)


async def _is_outside(store: Store, thread: ThreadRecord) -> bool:
    """Whether the words of the chat ``thread`` may be a third party's: a run of it ever read content third parties can
    author (item 974's rule, as ``read_chat`` takes it), or it is a watcher's, named after what came in."""
    return thread.source == WATCHER_SOURCE or await store.chat_ever_tainted(thread.id)


def _who(role: str) -> str:
    return "the owner" if role == "user" else "Dot"


def _answer(words: _Words, head: str, held: tuple[str, ...]) -> ToolResult:
    """The result: what the plain chats said, then what the chats that read outside content said, fenced, the whole
    marked untrusted when any of it is fenced; masked of the secrets Dot holds and held to a size."""
    plain: str = redact_known("\n".join([head, *words.plain]), held)
    if not words.outside:
        return ok(truncate_tail(plain, _ANSWER_CHARS))
    nonce: str = secrets.token_hex(_NONCE_BYTES)
    fenced: str = unclosing(redact_known("\n".join(words.outside), held), "chat_search")
    text: str = (
        f"{plain}\nChats that read content third parties can author:\n"
        f'<chat_search nonce="{nonce}">\n{fenced}\n</chat_search nonce="{nonce}">'
    )
    return mark_untrusted(ok(truncate_tail(text, _ANSWER_CHARS)))


async def _thread_of(store: Store, thread_id: str) -> ThreadRecord | None:
    try:
        return await store.get_thread(thread_id)
    except NotFoundError:
        return None


async def _when(store: Store, hit: ChatHit) -> str:
    """When the words of ``hit`` were said, in words: the time of the run its message belongs to."""
    run: RunRecord | None = (
        await store.get_run(hit.ref) if hit.kind == "r" else await store.message_run(hit.thread_id, hit.seq)
    )
    return "at an unknown time" if run is None else ago(time.time() - run.created_at)


async def _find(args: SearchChatsArgs, ctx: ToolContext) -> ToolResult:
    store: Store = ctx.runtime.store
    terms: tuple[str, ...] = search_terms(args.query)
    if not terms:
        raise ToolInputError("find needs words to look for in query", {"query": args.query})
    hits: list[ChatHit] = await store.search_chat_text(terms, MOST_HITS)
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    if not hits:
        return ok(f"No chat says {' '.join(terms)!r}.")
    words: _Words = _Words()
    shown: int = 0
    for hit in hits:
        thread: ThreadRecord | None = await _thread_of(store, hit.thread_id)
        if thread is None:
            continue
        shown += 1
        said: str = f"the answer of {hit.ref}" if hit.kind == "r" else f"{_who(hit.ref)} said"
        place: str = f"read run={hit.ref}" if hit.kind == "r" else f"around chat={hit.thread_id} seq={hit.seq}"
        words.add(
            f"- {_one_line(thread.title, _TITLE_CHARS)} · {await _when(store, hit)} · {said} ({place}): "
            f"{_one_line(hit.snippet, _HIT_CHARS)}",
            await _is_outside(store, thread),
        )
    head: str = f"{shown} hits, the best first. Words in [brackets] are the match. around and read show more."
    return _answer(words, head, held)


async def _around(args: SearchChatsArgs, ctx: ToolContext) -> ToolResult:
    store: Store = ctx.runtime.store
    chat: str = args.chat.strip()
    if chat == "" or args.seq < 0:
        raise ToolInputError("around needs the chat's id and the place a hit named", {"chat": chat, "seq": args.seq})
    thread: ThreadRecord = await store.get_thread(chat)
    messages: list[ChatMessage] = await store.chat_messages_around(chat, args.seq, _AROUND + 1, _AROUND)
    if not messages:
        return ok(f"The chat {chat} has no message there any more.")
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    words: _Words = _Words()
    outside: bool = await _is_outside(store, thread)
    words.add(f"The chat {chat}: {_one_line(thread.title, _TITLE_CHARS)}", outside)
    for message in messages:
        mark: str = " <- the hit" if message.seq == args.seq else ""
        words.add(
            f"[{message.seq}] {_who(message.role)}{mark}: {truncate_tail(message.text.strip(), _MESSAGE_CHARS)}",
            outside,
        )
    return _answer(words, f"{len(messages)} messages around place {args.seq} of the chat {chat}.", held)


async def _read(args: SearchChatsArgs, ctx: ToolContext) -> ToolResult:
    store: Store = ctx.runtime.store
    run_id: str = args.run.strip()
    if run_id == "":
        raise ToolInputError("read needs the id of a run", {"run": run_id})
    try:
        run: RunRecord = await store.get_run(run_id)
    except NotFoundError as error:
        raise ToolInputError("no run has that id", {"run": run_id}) from error
    thread: ThreadRecord | None = await _thread_of(store, run.thread_id)
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    outside: bool = await store.run_ever_tainted(run.id) or (thread is not None and thread.source == WATCHER_SOURCE)
    words: _Words = _Words()
    title: str = "" if thread is None else f" ({_one_line(thread.title, _TITLE_CHARS)})"
    words.add(
        f"Run {run.id}: {run.status}, a {run.mode} run of {run.source}, {ago(time.time() - run.created_at)}, "
        f"chat {run.thread_id}{title}",
        outside,
    )
    words.add(f"Goal: {truncate_tail(run.goal.strip(), _GOAL_CHARS)}", outside)
    words.add(
        f"Answer: {truncate_tail(run.result.strip(), _RESULT_CHARS)}" if run.result.strip() else "No answer yet.",
        outside,
    )
    return _answer(words, "One run.", held)


def _search_chats_tool() -> RegisteredTool:
    def classify(args: SearchChatsArgs, _ctx: ToolContext) -> ActionInfo:
        shown: str = _one_line(args.query, 60) if args.mode == "find" else args.chat or args.run
        return ActionInfo(
            tool=SEARCH_CHATS_TOOL,
            kind="read",
            risk="safe",
            summary=f"Search the chats ({args.mode}) {shown}".strip(),
            target=shown,
            flags=(),
        )

    async def run(args: SearchChatsArgs, ctx: ToolContext) -> ToolResult:
        if args.mode == "find":
            return await _find(args, ctx)
        if args.mode == "around":
            return await _around(args, ctx)
        return await _read(args, ctx)

    return make_tool(
        name=SEARCH_CHATS_TOOL,
        description=(
            "Search what was said in every chat of this Mac and every run's answer (no model): find words, then "
            "around a hit or read a run."
        ),
        args_model=SearchChatsArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=15.0,
        classify=classify,
        run=run,
    )


def build_search_chats_tools() -> tuple[RegisteredTool, ...]:
    return (_search_chats_tool(),)
