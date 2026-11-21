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
``restart_when_idle_gave_up``, ``restart_when_idle_failed`` and ``restart_when_idle_crashed``. When it ends with no
restart it shows a notification of the Mac first (a failed one is ``restart_when_idle_not_told``), as nobody reads its
log.

A restart that waits is a flag in the store (:mod:`dotagent.restart_flag`, item 1310): since when, the run that asked
(the job's ``DOT_RESTART_ASKER``), the run it waits for now and whether the wait ran out. The daemon tells the owner at
thirty minutes which run holds it (:mod:`dotagent.restart_notice`), ``/api/status`` and ``dotx service status`` show the
flag. The run that asked is counted as any other: restarting while it works would cut it off at its last step. When
the two hours of runs at work are spent the job gives up as before, with its banner, and then goes on waiting, with
no time limit of its own, for the next time Dot is idle (no run works, none is blocked on the owner, no night's
learning is under way), restarts it then, once, and ends; nothing is stopped meanwhile, and schedules and channels
start their runs as ever.

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
from dotagent.core.notify import show_notification
from dotagent.core.paths import db_path, dot_home, ensure_layout, log_dir, token_path
from dotagent.core.runenv import RESTART_ASKER_MARKER
from dotagent.launchd import (
    RESTART_NIGHT_LIMIT_S,
    RESTART_POLL_S,
    RESTART_WAIT_LIMIT_S,
    launch_agent_path,
    release_restart_job,
    restart_job_path,
)
from dotagent.restart_flag import RestartFlag, clear_flag, write_flag
from dotagent.server.guard import read_token
from dotagent.server.links import health_url, status_url
from dotagent.server.views import HealthView
from dotagent.store.models import WORKING_STATUSES

log = get_logger(__name__)

# The database is read by a connection of its own while the daemon writes it; a read waits this long for a lock.
_DB_TIMEOUT_S: float = 5.0
# How long a question to the daemon about its night waits for the answer.
_NIGHT_TIMEOUT_S: float = 5.0
# How long the job goes on waiting for Dot to be idle after the wait for runs at work ran out (item 1310): a week, then
# it ends without restarting and takes its flag away. An idle moment comes within a day in any use of Dot.
RESTART_SECOND_CHANCE_S: float = 7 * 24 * 3600.0

# What tells the program the night's learning under way: the names, none when nothing of it is.
type NightLook = Callable[[], Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class Busy:
    """What the restart still waits for: the runs at work, and once none is, the night's learning under way."""

    runs: int
    night: tuple[str, ...]
    # The run at work the longest, the one the restart waits for (item 1310); "" when none works.
    holder: str = ""


class _DaemonNight(BaseModel):
    """The part of the daemon's ``GET /api/status`` this program reads (item 815); a daemon from before item 815 does
    not name its night's learning, which reads as none."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    night_work: tuple[str, ...] = ()


def working_run_ids(database: Path) -> tuple[str, ...]:
    """The ids of the runs at work (a status of ``WORKING_STATUSES``: a run blocked on the owner works too) in the
    database at ``database``, the longest at work first, read through a read-only connection that cannot change it.
    Raises :class:`ServiceError` when there is no database or it cannot be read: a program that cannot tell whether a
    run works must not restart the daemon."""
    if not database.is_file():
        raise ServiceError("there is no database of runs to look at", {"database": str(database)})
    statuses: list[str] = sorted(WORKING_STATUSES)
    uri: str = f"{database.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)) as connection:
            rows: list[object] = list(
                connection.execute(
                    f"SELECT id FROM runs WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at, id",
                    statuses,
                ).fetchall()
            )
    except sqlite3.Error as exc:
        raise ServiceError(
            "the database of runs cannot be read", {"database": str(database), "error": str(exc)}
        ) from exc
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 1 or not isinstance(row[0], str):
            raise ServiceError(
                "the database of runs answered with something other than an id",
                {"database": str(database), "row": repr(row)},
            )
        ids.append(row[0])
    return tuple(ids)


def count_working_runs(database: Path) -> int:
    """The number of runs at work in the database at ``database`` (:func:`working_run_ids`)."""
    return len(working_run_ids(database))


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
    """How the program asks the daemon of ``config`` about its night: at its address, with the API token its file holds
    at each question, so that a token made anew during the wait is the one asked with. A token that cannot be read is
    logged, and nothing of the night is known then."""
    url: str = status_url(config.server.host, config.server.port)

    async def look() -> tuple[str, ...]:
        try:
            token: str = await asyncio.to_thread(read_token, token_path())
        except (DotError, OSError) as exc:
            log.warning("restart_when_idle_night_unknown", url=url, error=str(exc))
            return ()
        return await night_work_of(url, token)

    return look


async def _no_night() -> tuple[str, ...]:
    return ()


async def _unnoted(holder: str) -> None:
    """What :func:`wait_until_idle` calls when the run it waits for changes, when nothing is to be kept."""


async def wait_until_idle(
    database: Path,
    poll_s: float,
    limit_s: float,
    night: NightLook = _no_night,
    night_limit_s: float = 0.0,
    holder_changed: Callable[[str], Awaitable[None]] = _unnoted,
) -> Busy:
    """Look at the runs of ``database`` every ``poll_s`` seconds and, once none works, at the night's learning ``night``
    names (item 815), until there is neither, or until runs have worked ``limit_s`` seconds of the wait or the night's
    learning has gone on ``night_limit_s``. ``holder_changed`` is told the id of the run at work the longest (``""``
    when none) at the first look and whenever it changes (item 1310). Returns what was under way at the end: nothing
    once Dot is idle."""
    runs_s: float = 0.0
    night_s: float = 0.0
    told: tuple[str, ...] = ()
    held_by: str | None = None
    while True:
        began: float = time.monotonic()
        working_ids: tuple[str, ...] = await asyncio.to_thread(working_run_ids, database)
        working: int = len(working_ids)
        holder: str = working_ids[0] if working_ids else ""
        learning: tuple[str, ...] = () if working else await night()
        if working == 0 and not learning:
            return Busy(0, ())
        if holder != held_by:
            held_by = holder
            await holder_changed(holder)
        if (working and runs_s >= limit_s) or (learning and night_s >= night_limit_s):
            return Busy(working, learning, holder)
        if learning and learning != told:
            log.info("restart_when_idle_waits_for_the_night", night_work=list(learning))
        told = learning
        await asyncio.sleep(poll_s)
        spent: float = time.monotonic() - began
        if working:
            runs_s += spent
        else:
            night_s += spent


class _Flag:
    """The flag of the restart that waits, kept in the store as it changes (item 1310). A flag that cannot be written
    is logged and the restart goes on: it is a note for the owner, and what it notes is not at stake."""

    def __init__(self, database: Path, asked_by: str, now: Callable[[], float] = time.time) -> None:
        self._database: Path = database
        self._flag: RestartFlag = RestartFlag(since=now(), asked_by=asked_by, waiting_for="", gave_up=False)

    async def keep(self, **change: str | bool) -> None:
        self._flag = self._flag.model_copy(update=change)
        try:
            await asyncio.to_thread(write_flag, self._database, self._flag)
        except ServiceError as exc:
            log.warning("restart_when_idle_flag_not_written", error=exc.message, context=exc.context)

    async def holder(self, run_id: str) -> None:
        await self.keep(waiting_for=run_id)

    async def clear(self) -> None:
        try:
            await asyncio.to_thread(clear_flag, self._database)
        except ServiceError as exc:
            log.warning("restart_when_idle_flag_not_removed", error=exc.message, context=exc.context)


async def wait_and_restart(database: Path, uid: int, asked_by: str = "") -> int:
    """Wait for the runs and the night's learning to end, then restart the daemon and wait for it to answer. Returns 0
    when it did, 1 when the time was up with runs at work or the night's learning under way and Dot was not idle
    afterwards either. After the time for runs at work is spent the owner is told (the banner), the flag says so, and
    the wait goes on for Dot to be idle (:data:`RESTART_SECOND_CHANCE_S`), when it restarts the daemon once
    (item 1310). ``asked_by`` is the run whose command asked for the restart; it is counted as any other."""
    began: float = time.monotonic()
    night: NightLook = night_look(load_user_config())
    flag: _Flag = _Flag(database, asked_by)
    log.info(
        "restart_when_idle_waiting",
        database=str(database),
        poll_s=RESTART_POLL_S,
        limit_s=RESTART_WAIT_LIMIT_S,
        night_limit_s=RESTART_NIGHT_LIMIT_S,
        runs_at_work=await asyncio.to_thread(count_working_runs, database),
        asked_by=asked_by,
    )
    await flag.keep()
    busy: Busy = await wait_until_idle(
        database, RESTART_POLL_S, RESTART_WAIT_LIMIT_S, night, RESTART_NIGHT_LIMIT_S, flag.holder
    )
    waited_s: float = round(time.monotonic() - began, 1)
    if busy.runs > 0 or busy.night:
        log.warning(
            "restart_when_idle_gave_up",
            waited_s=waited_s,
            runs_at_work=busy.runs,
            night_work=list(busy.night),
            holder=busy.holder,
        )
        await flag.keep(gave_up=True, waiting_for=busy.holder)
        await _tell_not_restarted(1)
        busy = await wait_until_idle(
            database, RESTART_POLL_S, RESTART_SECOND_CHANCE_S, night, RESTART_SECOND_CHANCE_S, flag.holder
        )
        waited_s = round(time.monotonic() - began, 1)
        if busy.runs > 0 or busy.night:
            log.warning("restart_when_idle_second_chance_ended", waited_s=waited_s, holder=busy.holder)
            await flag.clear()
            return 1
        log.info("restart_when_idle_idle_after_giving_up", waited_s=waited_s)
    log.info("restart_when_idle_idle", waited_s=waited_s)
    # The address is read now, not when the job was loaded: the configuration may have changed while it waited.
    config: AppConfig = load_user_config()
    health: HealthView = await restart_service(
        launch_agent_path(Path.home()), uid, health_url(config.server.host, config.server.port)
    )
    log.info("restart_when_idle_restarted", waited_s=waited_s, version=health.version)
    await flag.clear()
    return 0


async def _tell_not_restarted(code: int) -> None:
    """A banner of the Mac when the restart did not happen as asked: the job has no one else to tell, and nobody reads
    its log, so a daemon that went on with the old code (a merge that is not live, a wait that ran out at two hours)
    was found out by chance. At the two hours (``code`` 1) it says that Dot restarts by itself the next time it is
    idle."""
    message: str = (
        "Runs or the night's learning were still at work when the wait ran out, so the new code is not live yet. "
        "Dot restarts by itself the next time it is idle."
        if code == 1
        else "The restart failed. dotx.log says why."
    )
    try:
        await show_notification("Dot was not restarted yet" if code == 1 else "Dot was not restarted", message)
    except DotError as error:
        log.warning("restart_when_idle_not_told", error=error.message)


async def run(uid: int) -> int:
    """Carry out the restart, then unload this job: it ends here whatever happened, so that no job is left that
    ``dotx service status`` would call a restart that waits. Returns the exit code, which is 0 when the daemon was
    restarted, 1 when the time was up and Dot stayed busy a week after, 2 on an error of Dot's and 3 on any other (each
    logged, with its traceback for the last: once the job unloads itself nothing else would show it)."""
    plist: Path = restart_job_path(dot_home())
    code: int
    try:
        code = await wait_and_restart(db_path(), uid, os.environ.get(RESTART_ASKER_MARKER, ""))
    except DotError as exc:
        log.error("restart_when_idle_failed", error=exc.message, context=exc.context)
        code = 2
    except Exception as exc:
        log.exception("restart_when_idle_crashed", error=str(exc))
        code = 3
    try:
        # The job ends here: no restart waits after it, so the flag of one goes (item 1310).
        try:
            await asyncio.to_thread(clear_flag, db_path())
        except ServiceError as exc:
            log.warning("restart_when_idle_flag_not_removed", error=exc.message, context=exc.context)
        if code > 1:
            # Before the release: unloading the job ends this process, and nothing after it runs (the pre-push review).
            # A wait that ran out was told when it did (code 1), before it went on.
            await _tell_not_restarted(code)
    finally:
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
