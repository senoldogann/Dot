"""The program of ``dotx service restart --when-idle`` (item 441): wait until no run works, then restart the daemon.

A run that merged a change of Dot's own code wants the daemon restarted once it has ended; restarting at once would cut
the run itself off. ``dotx service restart --when-idle`` loads this program as a one-shot launchd job
(:func:`dotagent.launchd.restart_plist`) and returns. The program looks at Dot's database every
:data:`~dotagent.launchd.RESTART_POLL_S` seconds, read-only and with the statuses a run works in
(:data:`~dotagent.store.models.WORKING_STATUSES`); the run that asked still works, so it is counted until it ends. Once
no run works it restarts the daemon (:func:`dotagent.cli_daemon.restart_service`), waits for the health check, and
unloads its own job and deletes its property list whatever happened. After
:data:`~dotagent.launchd.RESTART_WAIT_LIMIT_S` seconds it gives up without restarting. It writes what it did to
``dotx.log``: ``restart_when_idle_waiting``, ``restart_when_idle_idle``, ``restart_when_idle_restarted``,
``restart_when_idle_gave_up`` and ``restart_when_idle_failed``.

``count_working_runs`` reads the database and ``wait_until_idle`` polls it; ``main`` is the job's entry point.
"""

import asyncio
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from dotagent.cli_daemon import restart_service
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import DotError, ServiceError
from dotagent.core.logs import configure_logging, get_logger
from dotagent.core.paths import db_path, dot_home, ensure_layout, log_dir
from dotagent.launchd import RESTART_POLL_S, RESTART_WAIT_LIMIT_S, release_restart_job, restart_job_path
from dotagent.server.links import health_url
from dotagent.server.views import HealthView
from dotagent.store.models import WORKING_STATUSES

log = get_logger(__name__)

# The database is read by a connection of its own while the daemon writes it; a read waits this long for a lock.
_DB_TIMEOUT_S: float = 5.0


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


async def wait_until_idle(database: Path, poll_s: float, limit_s: float) -> int:
    """Look at the runs of ``database`` every ``poll_s`` seconds until none works or ``limit_s`` seconds have passed.
    Returns 0 once none works, or the number of runs still at work when the time is up."""
    deadline: float = time.monotonic() + limit_s
    while True:
        working: int = await asyncio.to_thread(count_working_runs, database)
        if working == 0 or time.monotonic() >= deadline:
            return working
        await asyncio.sleep(poll_s)


async def wait_and_restart(database: Path, uid: int) -> int:
    """Wait for the runs to end, then restart the daemon and wait for it to answer. Returns 0 when it did, 1 when the
    time was up with runs still at work."""
    began: float = time.monotonic()
    log.info(
        "restart_when_idle_waiting",
        database=str(database),
        poll_s=RESTART_POLL_S,
        limit_s=RESTART_WAIT_LIMIT_S,
        runs_at_work=await asyncio.to_thread(count_working_runs, database),
    )
    working: int = await wait_until_idle(database, RESTART_POLL_S, RESTART_WAIT_LIMIT_S)
    waited_s: float = round(time.monotonic() - began, 1)
    if working > 0:
        log.warning("restart_when_idle_gave_up", waited_s=waited_s, runs_at_work=working)
        return 1
    log.info("restart_when_idle_idle", waited_s=waited_s)
    # The address is read now, not when the job was loaded: the configuration may have changed while it waited.
    config: AppConfig = load_user_config()
    health: HealthView = await restart_service(uid, health_url(config.server.host, config.server.port))
    log.info("restart_when_idle_restarted", waited_s=waited_s, version=health.version)
    return 0


async def run(uid: int) -> int:
    """Carry out the restart, then unload this job: it ends here whatever happened, so that no job is left that
    ``dotx service status`` would call a restart that waits. Returns the exit code, which is 0 when the daemon was
    restarted, 1 when the time was up and 2 on an error (logged)."""
    plist: Path = restart_job_path(dot_home())
    code: int
    try:
        code = await wait_and_restart(db_path(), uid)
    except DotError as exc:
        log.error("restart_when_idle_failed", error=exc.message, context=exc.context)
        code = 2
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
