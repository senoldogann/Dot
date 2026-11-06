"""Routes of Settings › Self-repair (items 919-921, :mod:`dotagent.repair`): the book of Dot's own problems and the
sessions Claude Code spent on them, a night started by hand, and how Claude Code works for it (item 933)."""

import asyncio

from fastapi import APIRouter

from dotagent.core.errors import RepairError, RunConflict
from dotagent.repair.claude import DEFAULT_WORKER, ClaudeAccount, WorkerChoice, claude_state, read_account
from dotagent.repair.night import (
    FAST_KEY,
    LIVE_KEY,
    SESSIONS_KEY,
    WORKER_KEY,
    read_fast,
    read_live,
    read_sessions,
    read_worker,
)
from dotagent.repair.problems import BOOK_KEY, read_book
from dotagent.server.context import ServerContext
from dotagent.server.views import RepairView

# How long the page waits for the reading of its pull requests' states before it opens with what was kept.
_SETTLE_WAIT_S: float = 8.0


def repair_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    async def view() -> RepairView:
        config = ctx.runtime.config.repair
        account: ClaudeAccount | None = await asyncio.to_thread(read_account, claude_state(ctx.runtime.shell_env))
        book = read_book(await ctx.runtime.store.kv_get(BOOK_KEY))
        sessions = read_sessions(await ctx.runtime.store.kv_get(SESSIONS_KEY))
        running: bool = ctx.scheduler is not None and ctx.scheduler.repairing()
        # A choice that cannot be read stops the nights; the page still opens, says so and takes a new one.
        lost: bool = False
        try:
            worker: WorkerChoice = await read_worker(ctx.runtime.store)
        except RepairError:
            worker, lost = DEFAULT_WORKER, True
        return RepairView(
            repo=config.repo,
            time=config.time,
            merge=config.merge,
            sessions_per_day=config.sessions_per_day,
            account=account,
            problems=sorted(book.problems, key=lambda problem: (problem.status != "open", -problem.count)),
            sessions=list(reversed(sessions)),
            running=running,
            worker=worker,
            worker_lost=lost,
            live=read_live(await ctx.runtime.store.kv_get(LIVE_KEY)) if running else None,
            fast_seen=read_fast(await ctx.runtime.store.kv_get(FAST_KEY)),
        )

    @router.get("/repair")
    async def repair() -> RepairView:
        return await view()

    @router.post("/repair/settle")
    async def repair_settle() -> RepairView:
        """The page as it opens, once where its pull requests stand on GitHub was read again (item 958), or a few
        seconds of it: a reading that takes longer goes on and the page shows it when it opens next. The page's polls
        read ``GET /api/repair``, which asks GitHub nothing."""
        if ctx.scheduler is not None and ctx.runtime.config.repair.repo:
            settling: asyncio.Task[None] | None = ctx.scheduler.settle_repairs()
            if settling is not None:
                await asyncio.wait({settling}, timeout=_SETTLE_WAIT_S)
        return await view()

    @router.post("/repair/now")
    async def repair_now() -> RepairView:
        """Start a night now. Refused (409) with no repository to repair, with no scheduler (a daemon started without
        one), or while a night is under way."""
        if not ctx.runtime.config.repair.repo:
            raise RunConflict("Self-repair is off: [repair] repo names no checkout of Dot's code", {})
        if ctx.scheduler is None or not ctx.scheduler.repair_now():
            raise RunConflict("a night of repairs is under way already, or this daemon schedules nothing", {})
        return await view()

    @router.post("/repair/stop")
    async def repair_stop() -> RepairView:
        """Stop the night under way (item 939): its session of Claude Code ends with what it started, and its problem
        stays as it was. 409 when none is under way."""
        if ctx.scheduler is None or not await ctx.scheduler.stop_repair():
            raise RunConflict("no night of repairs is under way", {})
        return await view()

    @router.put("/repair/worker")
    async def put_worker(request: WorkerChoice) -> RepairView:
        """Keep how Claude Code works for Dot: its model, its effort and fast mode. The next night's sessions and second
        looks take it; one under way keeps what it began with."""
        await ctx.runtime.store.kv_set(WORKER_KEY, request.model_dump_json())
        return await view()

    return router
