"""The program of ``dotx service restart --when-idle`` (item 441): wait until no run works and no learning of the night
is under way (item 815), then restart the daemon.

A run that merged a change of Dot's own code wants the daemon restarted once it has ended; restarting at once would cut
the run itself off. ``dotx service restart --when-idle`` loads this program as a one-shot launchd job
(:func:`dotagent.launchd.restart_plist`) and returns. The program looks at Dot's database every
:data:`~dotagent.launchd.RESTART_POLL_S` seconds, read-only and with the statuses a run works in
(:data:`~dotagent.store.models.WORKING_STATUSES`); the run that asked still works, so it is counted until it ends. Once
no run works it asks the daemon (``GET /api/status``, with the API token) what of the night's learning is under way:
the reflection, the harness's step and the plugin scout's night are no runs, and each records nothing until its end,
so a restart would cut it off and lose the night. Once there is neither it restarts the daemon
(:func:`dotagent.cli_daemon.restart_service`), waits for the health check, and unloads its own job and deletes its
property list whatever happened. After :data:`~dotagent.launchd.RESTART_WAIT_LIMIT_S` seconds of runs at work, or
:data:`~dotagent.launchd.RESTART_NIGHT_LIMIT_S` of the night's learning, it gives up without restarting. It writes what
it did to ``dotx.log``: ``restart_when_idle_waiting``, ``restart_when_idle_waits_for_the_night``,
``restart_when_idle_night_unknown``, ``restart_when_idle_idle``, ``restart_when_idle_restarted``,
``restart_when_idle_gave_up``, ``restart_when_idle_failed`` and ``restart_when_idle_crashed``.

``count_working_runs`` reads the database, ``night_work_of`` asks the daemon, and ``wait_until_idle`` polls both;
``main`` is the job's entry point.
"""

import asyncio
import os
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.cli_daemon import restart_service
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import DotError, ServiceError
from dotagent.core.logs import configure_logging, get_logger
from dotagent.core.paths import db_path, dot_home, ensure_layout, log_dir, token_path
from dotagent.launchd import (
    RESTART_NIGHT_LIMIT_S,
    RESTART_POLL_S,
    RESTART_WAIT_LIMIT_S,
    launch_agent_path,
    release_restart_job,
    restart_job_path,
)
from dotagent.server.guard import read_token
from dotagent.server.links import health_url, status_url
from dotagent.server.views import HealthView
from dotagent.store.models import WORKING_STATUSES

log = get_logger(__name__)

# The database is read by a connection of its own while the daemon writes it; a read waits this long for a lock.
_DB_TIMEOUT_S: float = 5.0
# How long a question to the daemon about its night waits for the answer.
_NIGHT_TIMEOUT_S: float = 5.0

# What tells the program the night's learning under way: the names, none when nothing of it is.
type NightLook = Callable[[], Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class Busy:
    """What the restart still waits for: the runs at work, and once none is, the night's learning under way."""

    runs: int
    night: tuple[str, ...]


class _DaemonNight(BaseModel):
    """The part of the daemon's ``GET /api/status`` this program reads (item 815); a daemon from before item 815 does
    not name its night's learning, which reads as none."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    night_work: tuple[str, ...] = ()


def count_working_runs(database: Path) -> int:
    """The number of runs at work (a status of ``WORKING_STATUSES``) in the database at ``database``, read through a
    read-only connection that cannot change it. Raises :class:`ServiceError` when there is no database or it cannot be
    read: a program that cannot tell whether a run works must not restart the daemon."""
    if not database.is_file():
        raise ServiceError("there is no database of runs to look at", {"database": str(database)})
    statuses: list[str] = sorted(WORKING_STATUSES)
    uri: str = f"{database.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)) as connection:
            row: object = connection.execute(
                f"SELECT COUNT(*) FROM runs WHERE status IN ({','.join('?' * len(statuses))})", statuses
            ).fetchone()
    except sqlite3.Error as exc:
        raise ServiceError(
            "the database of runs cannot be read", {"database": str(database), "error": str(exc)}
        ) from exc
    if not isinstance(row, tuple) or len(row) != 1 or not isinstance(row[0], int):
        raise ServiceError(
            "the database of runs answered with something other than a count",
            {"database": str(database), "row": repr(row)},
        )
    return row[0]


async def night_work_of(url: str, token: str) -> tuple[str, ...]:
    """The night's learning the daemon's ``GET /api/status`` at ``url`` names as under way (item 815). None is known
    when nothing listens there, when the daemon is from before item 815, or when its answer cannot be read (the last
    logged): what a restart cuts off then is a night of learning, which the next night does again, not a run's work, and
    a daemon that answers badly is the one a restart mends."""
    async with httpx.AsyncClient(timeout=_NIGHT_TIMEOUT_S, trust_env=False) as client:
        try:
            response: httpx.Response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return ()
        except httpx.HTTPError as exc:
            log.warning("restart_when_idle_night_unknown", url=url, error=repr(exc))
            return ()
    if response.status_code != httpx.codes.OK:
        log.warning("restart_when_idle_night_unknown", url=url, status=response.status_code)
        return ()
    try:
        return _DaemonNight.model_validate_json(response.content).night_work
    except ValidationError as exc:
        log.warning("restart_when_idle_night_unknown", url=url, error=str(exc))
        return ()


def night_look(config: AppConfig) -> NightLook:
    """How the program asks the daemon of ``config`` about its night: at its address, with the API token as the file
    holds it now. A token that cannot be read is logged, and nothing of the night is known then."""
    url: str = status_url(config.server.host, config.server.port)
    try:
        token: str = read_token(token_path())
    except (DotError, OSError) as exc:
        log.warning("restart_when_idle_night_unknown", url=url, error=str(exc))

        async def unknown() -> tuple[str, ...]:
            return ()

        return unknown

    async def look() -> tuple[str, ...]:
        return await night_work_of(url, token)

    return look


async def _no_night() -> tuple[str, ...]:
    return ()


async def wait_until_idle(
    database: Path, poll_s: float, limit_s: float, night: NightLook = _no_night, night_limit_s: float = 0.0
) -> Busy:
    """Look at the runs of ``database`` every ``poll_s`` seconds and, once none works, at the night's learning ``night``
    names (item 815), until there is neither, or until runs have worked ``limit_s`` seconds of the wait or the night's
    learning has gone on ``night_limit_s``. Returns what was under way at the end: nothing once Dot is idle."""
    runs_s: float = 0.0
    night_s: float = 0.0
    told: tuple[str, ...] = ()
    while True:
        began: float = time.monotonic()
        working: int = await asyncio.to_thread(count_working_runs, database)
        learning: tuple[str, ...] = () if working else await night()
        if working == 0 and not learning:
            return Busy(0, ())
        if (working and runs_s >= limit_s) or (learning and night_s >= night_limit_s):
            return Busy(working, learning)
        if learning and learning != told:
            log.info("restart_when_idle_waits_for_the_night", night_work=list(learning))
        told = learning
        await asyncio.sleep(poll_s)
        spent: float = time.monotonic() - began
        if working:
            runs_s += spent
        else:
            night_s += spent


async def wait_and_restart(database: Path, uid: int) -> int:
    """Wait for the runs and the night's learning to end, then restart the daemon and wait for it to answer. Returns 0
    when it did, 1 when the time was up with runs at work or the night's learning under way."""
    began: float = time.monotonic()
    night: NightLook = night_look(load_user_config())
    log.info(
        "restart_when_idle_waiting",
        database=str(database),
        poll_s=RESTART_POLL_S,
        limit_s=RESTART_WAIT_LIMIT_S,
        night_limit_s=RESTART_NIGHT_LIMIT_S,
        runs_at_work=await asyncio.to_thread(count_working_runs, database),
    )
    busy: Busy = await wait_until_idle(database, RESTART_POLL_S, RESTART_WAIT_LIMIT_S, night, RESTART_NIGHT_LIMIT_S)
    waited_s: float = round(time.monotonic() - began, 1)
    if busy.runs > 0 or busy.night:
        log.warning("restart_when_idle_gave_up", waited_s=waited_s, runs_at_work=busy.runs, night_work=list(busy.night))
        return 1
    log.info("restart_when_idle_idle", waited_s=waited_s)
    # The address is read now, not when the job was loaded: the configuration may have changed while it waited.
    config: AppConfig = load_user_config()
    health: HealthView = await restart_service(
        launch_agent_path(Path.home()), uid, health_url(config.server.host, config.server.port)
    )
    log.info("restart_when_idle_restarted", waited_s=waited_s, version=health.version)
    return 0


async def run(uid: int) -> int:
    """Carry out the restart, then unload this job: it ends here whatever happened, so that no job is left that
    ``dotx service status`` would call a restart that waits. Returns the exit code, which is 0 when the daemon was
    restarted, 1 when the time was up, 2 on an error of Dot's and 3 on any other (each logged, with its traceback for
    the last: once the job unloads itself nothing else would show it)."""
    plist: Path = restart_job_path(dot_home())
    code: int
    try:
        code = await wait_and_restart(db_path(), uid)
    except DotError as exc:
        log.error("restart_when_idle_failed", error=exc.message, context=exc.context)
        code = 2
    except Exception as exc:
        log.exception("restart_when_idle_crashed", error=str(exc))
        code = 3
    await release_restart_job(plist, uid)
    return code


def main() -> None:
    ensure_layout()
    configure_logging("info", False, log_dir() / "dotx.log")
    try:
        code: int = asyncio.run(run(os.getuid()))
    except DotError as exc:
        log.error("restart_when_idle_failed", error=exc.message, context=exc.context)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
