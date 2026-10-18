"""The routes of Settings › System's Diagnostics (item 370) and Open source licences (item 371): what the daemon's
processes take, the room Dot's folders take, its logs and the end of one, and the licences of what Dot uses. Each is
read when it is asked for."""

import asyncio
import os
from typing import Annotated

from fastapi import APIRouter, Query

from dotagent.core.paths import log_dir
from dotagent.server.context import ServerContext
from dotagent.server.diagnostics import (
    MAX_TAIL_LINES,
    DiskView,
    LogsView,
    LogTail,
    ProcessesView,
    list_logs,
    measure_disk,
    read_processes,
    read_tail,
)
from dotagent.server.licences import LicencesView, read_licences


def about_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/diagnostics/processes")
    async def processes() -> ProcessesView:
        """The daemon's process and those it started, as they stand now."""
        return await read_processes(os.getpid())

    @router.get("/diagnostics/disk")
    async def disk() -> DiskView:
        """The room what Dot keeps takes on disk; it walks the folders, so it is asked for less often."""
        return await asyncio.to_thread(measure_disk)

    @router.get("/diagnostics/logs")
    async def logs() -> LogsView:
        return await asyncio.to_thread(list_logs, log_dir())

    @router.get("/diagnostics/logs/{name}")
    async def log_tail(name: str, lines: Annotated[int, Query(ge=1, le=MAX_TAIL_LINES)]) -> LogTail:
        """The last ``lines`` lines of the log ``name``, their secrets masked."""
        return await asyncio.to_thread(read_tail, log_dir(), name, lines)

    @router.get("/licences")
    async def licences() -> LicencesView:
        """The licences of the packages beside the daemon, of what comes with the pages, and of the tools Dot uses,
        those found on the PATH of the owner's shell."""
        return await asyncio.to_thread(read_licences, ctx.runtime.shell_env.get("PATH", os.defpath))

    return router
