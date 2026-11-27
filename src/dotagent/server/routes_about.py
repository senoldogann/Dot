"""The routes of Settings › System's Diagnostics (item 370) and Open source licences (item 371): what the daemon's
processes take, the room Dot's folders take, its logs and the end of one, the provider programs on the Mac with their
versions (item 1794), and the licences of what Dot uses. Each is read when it is asked for."""

import asyncio
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from dotagent.claude_code.versions import CLAUDE_VERSIONS, VersionVerdict, claude_version, parse_version, verdict_of
from dotagent.core.paths import claude_code_command, log_dir, opencode_command
from dotagent.core.types import FrozenModel
from dotagent.loop_watch import STALL_S, LoopStall
from dotagent.opencode.server import find_program
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
from dotagent.store.perf import PerfTable, PerfWindow


class StallsView(FrozenModel):
    stall_s: float
    stalls: list[LoopStall]


class ProviderProgram(FrozenModel):
    """A provider's program on the Mac (item 1794): its name, the command Dot starts, where it was found (``None``: not
    installed), the version it says, and how Dot fares on it: ``supported`` (measured), ``graceful`` (runs, older than
    measured), ``broken`` (a run on it is refused), ``unknown`` (Dot keeps no measure of it) or ``missing``."""

    name: str
    command: str
    path: str | None
    version: str | None
    support: Literal["supported", "graceful", "broken", "unknown", "missing"]
    why: str


class ProgramsView(FrozenModel):
    programs: list[ProviderProgram]


# OpenCode has no table of versions Dot was measured on, as Claude Code has (item 1673).
_OPENCODE_UNMEASURED: str = "Dot keeps no list of the OpenCode versions it was measured on"


async def _claude_program(command: str, env: dict[str, str]) -> ProviderProgram:
    program: Path | None = await asyncio.to_thread(find_program, command, env.get("PATH", ""))
    if program is None:
        return ProviderProgram(
            name="Claude Code", command=command, path=None, version=None, support="missing", why="not installed"
        )
    verdict: VersionVerdict = verdict_of(parse_version(await claude_version(program, env)), CLAUDE_VERSIONS)
    version: str | None = None if verdict.version is None else verdict.shown
    return ProviderProgram(
        name="Claude Code",
        command=command,
        path=str(program),
        version=version,
        support=verdict.support,
        why=verdict.why,
    )


async def _opencode_program(command: str, env: dict[str, str]) -> ProviderProgram:
    program: Path | None = await asyncio.to_thread(find_program, command, env.get("PATH", ""))
    if program is None:
        return ProviderProgram(
            name="OpenCode", command=command, path=None, version=None, support="missing", why="not installed"
        )
    # ``program --version`` the same way as Claude Code's: what it writes, or nothing within its time.
    found: tuple[int, int, int] | None = parse_version(await claude_version(program, env))
    version: str | None = None if found is None else ".".join(str(part) for part in found)
    return ProviderProgram(
        name="OpenCode",
        command=command,
        path=str(program),
        version=version,
        support="unknown",
        why=_OPENCODE_UNMEASURED,
    )


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

    @router.get("/diagnostics/stalls")
    async def stalls() -> StallsView:
        """The times the daemon's loop stopped answering for more than ``stall_s`` seconds, newest first, each with
        where it was (item 1672)."""
        return StallsView(stall_s=STALL_S, stalls=ctx.loop_watch.stalls())

    @router.get("/diagnostics/speed")
    async def speed(window: Annotated[PerfWindow, Query()]) -> PerfTable:
        """Where the runs' seconds went in the last ``window`` (24h or 7d), stage by stage: each row's count, p50, p95,
        p99, longest and how many took over 3 s (item 2003). Settings › Maintenance › Speed."""
        return await ctx.runtime.store.perf(window)

    @router.get("/diagnostics/programs")
    async def programs() -> ProgramsView:
        """The provider programs Dot starts (Claude Code, OpenCode): where each is on the login shell's PATH, the
        version it says and how Dot fares on it (item 1794). Each is asked ``--version`` once per request."""
        env: dict[str, str] = ctx.runtime.shell_env
        found: list[ProviderProgram] = list(
            await asyncio.gather(
                _claude_program(claude_code_command(ctx.runtime.config.providers.claude_code.command), env),
                _opencode_program(opencode_command(ctx.runtime.config.providers.opencode.command), env),
            )
        )
        return ProgramsView(programs=found)

    @router.get("/licences")
    async def licences() -> LicencesView:
        """The licences of the packages beside the daemon, of what comes with the pages, and of the tools Dot uses,
        those found on the PATH of the owner's shell."""
        return await asyncio.to_thread(read_licences, ctx.runtime.shell_env.get("PATH", os.defpath))

    return router
