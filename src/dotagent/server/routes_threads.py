"""Routes for the chats of the sidebar: every thread a person started a run on, deleting several at once, naming or
pinning one from its menu (item 130), keeping what its composer chose (item 199), the helpers its runs started
(item 227), and its terminal sessions (item 229)."""

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from dotagent.core.errors import ConfigError, NotFoundError
from dotagent.core.text import redact_known
from dotagent.llm.router import ModelRef, offers_fast_mode, parse_model_ref
from dotagent.memory.words import search_terms
from dotagent.notify.tell_ledger import mark_opened
from dotagent.secrets import known_secrets
from dotagent.server.context import ServerContext
from dotagent.server.stream import event_stream
from dotagent.server.thread_sync import (
    ThreadStreamQuery,
    ThreadsView,
    ThreadsViewQuery,
    ThreadView,
    ThreadViewQuery,
    check_cursors,
    follow_thread,
    follow_threads,
    parse_cursors,
    terminals_of,
    thread_view,
    threads_view,
)
from dotagent.server.views import (
    ChatSearchQuery,
    DeletedThreads,
    DeleteThreadsRequest,
    HelpersQuery,
    TerminalView,
    ThreadChange,
    ThreadsQuery,
)
from dotagent.store.db import Store
from dotagent.store.models import ChatHit, RunRecord, ThreadSummary


def threads_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    @router.get("/threads")
    async def list_threads(query: Annotated[ThreadsQuery, Query()]) -> list[ThreadSummary]:
        rows = await store.list_thread_summaries(query.limit, ctx.runtime.config.scheduler.same_chat)
        return [ctx.manager.as_shown_thread(row) for row in rows]

    @router.get("/threads/view")
    async def view_threads(query: Annotated[ThreadsViewQuery, Query()]) -> ThreadsView:
        """The chats with their revisions and asks (item 1686, :mod:`dotagent.server.thread_sync`)."""
        return await threads_view(ctx, query.limit)

    @router.get("/threads/search")
    async def search_threads(query: Annotated[ChatSearchQuery, Query()]) -> list[ChatHit]:
        """What the chats say that holds any of the words of ``q`` (item 1882, the phone's home search), best first, at
        most ``limit``: a message of a chat's history or a run's answer, with the words around the match (the index of
        item 1740). A secret Dot holds is masked in a snippet, as the ``search_chats`` tool masks it. Words that fold to
        nothing find nothing."""
        terms: tuple[str, ...] = search_terms(query.q)
        hits: list[ChatHit] = await store.search_chat_text(terms, query.limit)
        if not hits:
            return []
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        return [hit.model_copy(update={"snippet": redact_known(hit.snippet, held)}) for hit in hits]

    @router.get("/threads/stream")
    async def stream_threads() -> StreamingResponse:
        """The list's stream: the lifecycle kinds and every chat's ``thread_changed`` (item 1686)."""
        return event_stream(follow_threads(ctx.runtime.bus, ctx.stopping))

    @router.get("/threads/{thread_id}/view")
    async def view_thread(thread_id: str, query: Annotated[ThreadViewQuery, Query()]) -> ThreadView:
        """The chat's small state at its revision (item 1686); 404 for no chat."""
        return await thread_view(ctx, thread_id, query.tail, query.before)

    @router.get("/threads/{thread_id}/stream")
    async def stream_thread(thread_id: str, query: Annotated[ThreadStreamQuery, Query()]) -> StreamingResponse:
        """The chat's stream (item 1686): its runs' and helpers' events from the client's cursors, its revision's
        moves, and ``synchronized``; 404 for no chat or a named run that is not the chat's."""
        await store.get_thread(thread_id)
        cursors: dict[str, int] = parse_cursors(query.cursors)
        await check_cursors(store, thread_id, cursors)
        return event_stream(follow_thread(ctx, thread_id, cursors))

    @router.get("/threads/{thread_id}/helpers")
    async def list_helpers(thread_id: str, query: Annotated[HelpersQuery, Query()]) -> list[RunRecord]:
        """The runs the chat's runs started on their behalf, newest first (the side panel's Agents tab); 404 for no
        chat."""
        await store.get_thread(thread_id)
        return await store.list_thread_helpers(thread_id, query.limit)

    @router.get("/threads/{thread_id}/terminals")
    async def list_terminals(thread_id: str) -> list[TerminalView]:
        """The chat's terminal sessions, in the order they opened (item 229): the line over the composer shows those
        whose program runs. 404 for no chat."""
        await store.get_thread(thread_id)
        return terminals_of(ctx, thread_id)

    @router.delete("/threads/{thread_id}/terminals/{session_id}", status_code=204)
    async def close_terminal(thread_id: str, session_id: str) -> None:
        """End the program of one of the chat's terminal sessions and everything it started, as Stop on the line over
        the composer does (item 229); the chat is told nothing. 404 for no such session."""
        if session_id not in {session.id for session in ctx.runtime.terminals.sessions(thread_id)}:
            raise NotFoundError(
                "the chat has no such terminal session", {"thread_id": thread_id, "session": session_id}
            )
        await ctx.runtime.terminals.close(thread_id, session_id)

    @router.delete("/threads")
    async def delete_threads(request: DeleteThreadsRequest) -> DeletedThreads:
        """Delete chats with everything they hold. Nothing goes while one of them has a run under way: 409 names them.
        No run starts on them while they are deleted."""
        wanted: list[str] = list(dict.fromkeys(request.ids))
        with ctx.manager.deleting_threads(wanted):
            deleted: list[str] = await store.delete_threads(wanted)
        gone: set[str] = set(deleted)
        return DeletedThreads(deleted=deleted, missing=[thread_id for thread_id in wanted if thread_id not in gone])

    @router.patch("/threads/{thread_id}", status_code=204)
    async def change_thread(thread_id: str, change: ThreadChange) -> None:
        """Give a chat the name the owner typed, pin it (first among the pinned) or unpin it, mark it seen or unseen,
        or keep the choices its composer shows for its next follow-up; 404 for no chat. Fast mode is kept only with a
        model that has it (item 223)."""
        if change.choices is not None:
            ref: ModelRef = parse_model_ref(
                change.choices.model if change.choices.model is not None else ctx.manager.main_model()
            )
            if change.choices.speed == "fast" and not offers_fast_mode(ref):
                raise ConfigError("the model does not offer fast mode", {"model": ref.label})
        if change.title is not None:
            await store.rename_thread(thread_id, change.title)
        if change.pinned is not None:
            await store.pin_thread(thread_id, change.pinned)
        if change.seen is not None:
            await store.see_thread(thread_id, change.seen)
            if change.seen:
                # The chat was opened: a tell that opens it, told within the half hour, is answered (item 1743).
                await mark_opened(store, thread_id, time.time())
        if change.choices is not None:
            await store.set_chat_choices(thread_id, change.choices)

    return router
