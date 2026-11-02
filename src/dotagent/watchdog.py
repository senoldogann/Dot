"""The watchdog (item 925): a look at Dot from outside it, every minute, which launchd starts as a job of its own.

launchd starts the daemon again when it exits; nothing caught a daemon that is alive and does not answer, nor a run
that works and has not made a step for long: the failure that is no crash, which the DeepSeek Harness super-agent
article puts an independent watchdog against. Each look, a process that ends, does three things:

- When the daemon's job is loaded with a process and its health check does not answer, the look counts it; after
  :data:`MISSES_BEFORE_RESTART` looks in a row it restarts the daemon (:func:`dotagent.cli_daemon.restart_service`)
  and shows a notification of the Mac, as the daemon that does not answer can tell nobody. A restart that waits for the
  runs to end (item 441) is left to do its work, and a job with no process is launchd's to start.
- When the daemon answers, a run that has worked for :func:`stuck_after_s` with no event (a model call is cut at
  ``[agent] model_call_timeout_s``, a command at ``[shell] max_timeout_s``) and is inside no call its own limit still
  allows (a helper's run, a wait, a question to the owner: the ``timeout_s`` of the call's event) is told to the owner
  once, through the daemon (``POST /api/runs/{id}/stuck``), as the daemon tells them anything: the push, the desktop,
  iMessage.
- What it saw is kept in ``watchdog.json`` in Dot's home, the misses and the runs told, and logged to ``dotx.log``:
  ``watchdog_unanswered``, ``watchdog_restarted``, ``watchdog_run_stuck``, ``watchdog_failed``.

The database is read through a read-only connection; the look changes nothing of Dot's but its own file.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx
from pydantic import ValidationError

from dotagent.cli_daemon import probe_daemon, restart_service
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import DotError, ServiceError
from dotagent.core.files import write_whole
from dotagent.core.logs import configure_logging, get_logger
from dotagent.core.notify import show_notification
from dotagent.core.paths import db_path, dot_home, ensure_layout, log_dir, token_path
from dotagent.core.types import FrozenModel
from dotagent.launchd import JobState, inspect_job, inspect_restart_job, launch_agent_path
from dotagent.server.guard import read_token
from dotagent.server.links import base_url, health_url
from dotagent.server.views import HealthView

log = get_logger(__name__)

# How many looks in a row the daemon may not answer before the watchdog restarts it: three minutes at one a minute,
# far past the seconds a start or a busy moment takes.
MISSES_BEFORE_RESTART: int = 3
# The least time a run works with no event before it is told, and the margin over the longest a step may take.
_STUCK_FLOOR_S: float = 30 * 60.0
_STUCK_MARGIN_S: float = 5 * 60.0
# How many runs told are kept, the newest: one is told once.
_TOLD_KEPT: int = 200
_STATE_FILE: str = "watchdog.json"
_DB_TIMEOUT_S: float = 5.0
_TELL_TIMEOUT_S: float = 10.0
_GOAL_CHARS: int = 80


class WatchdogState(FrozenModel):
    """What the looks before saw: how many in a row the daemon did not answer, and the runs told to be stuck."""

    misses: int
    told: tuple[str, ...]


class StuckRun(FrozenModel):
    run_id: str
    goal: str
    quiet_s: float


def stuck_after_s(config: AppConfig) -> float:
    """How long a run may work with no event before it counts as stuck: past the longest a model call and a command may
    take, and never under :data:`_STUCK_FLOOR_S`. A run inside a call counts by that call's own limit as well
    (:func:`stuck_runs`)."""
    longest: float = max(float(config.agent.model_call_timeout_s), float(config.shell.max_timeout_s))
    return max(_STUCK_FLOOR_S, longest + _STUCK_MARGIN_S)


def read_state(path: Path) -> WatchdogState:
    """The state the looks before kept; none when there is no file, or one that cannot be read (it is the watchdog's
    own memory of a minute, which a new look starts again)."""
    try:
        return WatchdogState.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return WatchdogState(misses=0, told=())
    except (OSError, ValidationError) as error:
        log.warning("watchdog_state_unread", path=str(path), error=str(error))
        return WatchdogState(misses=0, told=())


def write_state(path: Path, state: WatchdogState) -> None:
    write_whole(path, json.dumps(state.model_dump(mode="json")))


# A run's calls that have no result yet, and the latest moment one of them may still end by its own limit (item 925:
# the pre-push review; a helper's run, a wait or a question to the owner takes hours and writes no event meanwhile).
_OPEN_UNTIL: str = (
    "SELECT MAX(calls.ts + COALESCE(json_extract(calls.data, '$.timeout_s'), 0))"
    " FROM events AS calls WHERE calls.run_id = runs.id AND calls.kind = 'tool_call' AND NOT EXISTS ("
    "SELECT 1 FROM events AS results WHERE results.run_id = runs.id AND results.kind = 'tool_result'"
    " AND json_extract(results.data, '$.call_id') = json_extract(calls.data, '$.call_id'))"
)


def stuck_runs(database: Path, now: float, quiet_s: float) -> list[StuckRun]:
    """The runs that work (``running``: a run that waits for the owner is ``blocked``), have had no event for
    ``quiet_s`` seconds and are inside no call that its own limit still allows (``timeout_s`` of its ``tool_call``
    event, with :data:`_STUCK_MARGIN_S` over it), read through a read-only connection."""
    uri: str = f"{database.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)) as connection:
            rows: list[tuple[str, str, float, float | None]] = connection.execute(
                "SELECT runs.id, runs.goal, COALESCE(MAX(events.ts), runs.created_at) AS last,"
                f" ({_OPEN_UNTIL}) AS open_until"
                " FROM runs LEFT JOIN events ON events.run_id = runs.id"
                " WHERE runs.status = 'running' GROUP BY runs.id HAVING last < ?",
                (now - quiet_s,),
            ).fetchall()
    except sqlite3.Error as error:
        raise ServiceError(
            "the database of runs cannot be read", {"database": str(database), "error": str(error)}
        ) from error
    return [
        StuckRun(run_id=run_id, goal=" ".join(goal.split())[:_GOAL_CHARS], quiet_s=round(now - last, 1))
        for run_id, goal, last, open_until in rows
        if open_until is None or open_until + _STUCK_MARGIN_S < now
    ]


async def tell_stuck(base: str, token: str, run: StuckRun) -> None:
    """Have the daemon tell the owner that ``run`` works with no step (``POST /api/runs/{id}/stuck``)."""
    async with httpx.AsyncClient(timeout=_TELL_TIMEOUT_S, trust_env=False) as client:
        response: httpx.Response = await client.post(
            f"{base}/api/runs/{run.run_id}/stuck",
            headers={"Authorization": f"Bearer {token}"},
            json={"quiet_s": run.quiet_s},
        )
    if response.status_code >= 400:
        raise ServiceError(
            "the daemon did not take the stuck run",
            {"run_id": run.run_id, "status": response.status_code, "body": response.text[:300]},
        )


async def _answers(url: str) -> bool:
    """Whether the health check at ``url`` answers: one that times out or answers something else does not."""
    try:
        health: HealthView | None = await probe_daemon(url)
    except ServiceError as error:
        log.warning("watchdog_health_failed", url=url, error=error.message)
        return False
    return health is not None


async def look(uid: int, now: float) -> WatchdogState:
    """One look (the module's docstring); the state it leaves."""
    config: AppConfig = load_user_config()
    path: Path = dot_home() / _STATE_FILE
    state: WatchdogState = await asyncio.to_thread(read_state, path)
    job: JobState | None = await inspect_job(uid)
    if job is None or job.pid is None or await inspect_restart_job(uid) is not None:
        # No service, a service launchd is starting, or a restart that waits for the runs: nothing to do here.
        return WatchdogState(misses=0, told=state.told)
    url: str = health_url(config.server.host, config.server.port)
    if not await _answers(url):
        misses: int = state.misses + 1
        log.warning("watchdog_unanswered", url=url, misses=misses, pid=job.pid)
        if misses < MISSES_BEFORE_RESTART:
            return WatchdogState(misses=misses, told=state.told)
        try:
            health: HealthView = await restart_service(launch_agent_path(Path.home()), uid, url)
        except DotError:
            # The owner is told, and the looks count again from none (the pre-push review of item 925): the next try
            # is three looks on, not every minute; the error goes on to the log.
            await asyncio.to_thread(write_state, path, WatchdogState(misses=0, told=state.told))
            await show_notification(
                "Dot does not answer",
                f"Dot did not answer for {misses} minutes, and the watchdog could not restart it.",
            )
            raise
        log.warning("watchdog_restarted", misses=misses, version=health.version)
        await show_notification(
            "Dot was restarted", f"Dot did not answer for {misses} minutes, so the watchdog restarted it."
        )
        return WatchdogState(misses=0, told=state.told)
    told: list[str] = list(state.told)
    stuck: list[StuckRun] = await asyncio.to_thread(stuck_runs, db_path(), now, stuck_after_s(config))
    for run in stuck:
        if run.run_id in told:
            continue
        log.warning("watchdog_run_stuck", run_id=run.run_id, quiet_s=run.quiet_s)
        try:
            await tell_stuck(base_url(config.server.host, config.server.port), read_token(token_path()), run)
        except DotError:
            # The runs told before this one stay told (the pre-push review of item 925): kept, then the error goes
            # on, and the next look tells this one again.
            await asyncio.to_thread(write_state, path, WatchdogState(misses=0, told=tuple(told[-_TOLD_KEPT:])))
            raise
        told.append(run.run_id)
    return WatchdogState(misses=0, told=tuple(told[-_TOLD_KEPT:]))


async def run(uid: int) -> int:
    """One look, its state kept; 0 when it went through, 2 on an error of Dot's and 3 on any other (logged)."""
    path: Path = dot_home() / _STATE_FILE
    try:
        state: WatchdogState = await look(uid, time.time())
    except DotError as exc:
        log.error("watchdog_failed", error=exc.message, context=exc.context)
        return 2
    except Exception as exc:
        log.exception("watchdog_crashed", error=str(exc))
        return 3
    await asyncio.to_thread(write_state, path, state)
    return 0


def main() -> None:
    ensure_layout()
    configure_logging("info", False, log_dir() / "dotx.log")
    sys.exit(asyncio.run(run(os.getuid())))


if __name__ == "__main__":
    main()
