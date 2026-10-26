"""The Dot daemon: one process that serves the API and the UI, runs the agent and fires the schedules."""

import asyncio
import contextlib
import fcntl
import functools
import os
import signal
import socket
import time
from collections.abc import Iterator
from contextlib import AsyncExitStack
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from dotagent.agent.loop import warm_help_vectors
from dotagent.agent.manager import RunManager
from dotagent.agent.reactions import Reactions
from dotagent.agent.snapshots import stored_taker
from dotagent.channels.service import Channels
from dotagent.config import AppConfig
from dotagent.core.errors import ConfigError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import db_path, dot_home, phones_path, pid_path, running_mark_path, token_path
from dotagent.desktop.flight import FlightOverlay
from dotagent.desktop.show_dot import dot_shower
from dotagent.events import DAEMON_RESTARTED_EVENT, SYSTEM_RUN_ID, DaemonRestarted, Subscription
from dotagent.notify.push import PUSH_KINDS, watch_pushes
from dotagent.proactive.watching import watch_loop
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.guard import read_or_create_token, require_loopback
from dotagent.server.links import base_url, ui_url
from dotagent.server.phones import PairedPhones
from dotagent.store.backup import keep_backups
from dotagent.store.models import RunRecord

log = get_logger(__name__)

# Open connections get this long to finish when the daemon stops. Event streams end as soon as it starts to stop (see
# ``DotServer.shutdown``); this is the limit for a client that does anything else.
_GRACE_S: int = 3
_PRIVATE_FILE_MODE: int = 0o600
_PID_BYTES: int = 32
_STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)
# The exit code of a daemon asked to start again (item 409): not a clean exit, so the service's launchd job, which
# starts the daemon again only after one, does so (sysexits' EX_TEMPFAIL).
RESTART_CODE: int = 75


class DotServer(uvicorn.Server):
    """uvicorn's server without its own signal handling: the daemon decides what a signal means, so that runs and
    services are closed in order before the process ends. It sets ``stopping`` when it starts to stop, which tells the
    event streams, the only responses that would not end by themselves, to end."""

    def __init__(self, config: uvicorn.Config, stopping: asyncio.Event) -> None:
        super().__init__(config)
        self._stopping: asyncio.Event = stopping

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._stopping.set()
        await super().shutdown(sockets=sockets)


def serving_config(app: FastAPI) -> uvicorn.Config:
    """How the daemon serves. The HTTP parser and the event loop are named, not left to uvicorn's ``auto``: Dot's
    dependencies bring uvicorn's extras (``uvicorn[standard]``, item 583), so ``auto`` would choose httptools and
    uvloop, which are installed now, for the daemon that holds the owner's API token. It serves with h11 and asyncio, as
    it did before they came."""
    return uvicorn.Config(
        app,
        http="h11",
        loop="asyncio",
        log_config=None,
        access_log=False,
        lifespan="off",
        ws="none",
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=_GRACE_S,
    )


def bind_listener(host: str, port: int) -> socket.socket:
    """Open the listening socket before any service starts, so an address that is taken is reported at once."""
    family: socket.AddressFamily = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener: socket.socket = socket.socket(family, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
    except OSError as exc:
        listener.close()
        raise ConfigError(
            "the server cannot listen on its address",
            {
                "host": host,
                "port": port,
                "error": str(exc),
                "fix": "a daemon of Dot that is still stopping holds the port for a few seconds: start again; if "
                "another program holds it, change [server] port in the configuration",
            },
        ) from exc
    return listener


@contextlib.contextmanager
def instance_lock(path: Path) -> Iterator[None]:
    """Hold the daemon's pid file for as long as the daemon runs; a second daemon is refused."""
    descriptor: int = os.open(path, os.O_RDWR | os.O_CREAT, _PRIVATE_FILE_MODE)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder: str = os.pread(descriptor, _PID_BYTES, 0).decode("ascii", errors="replace").strip()
            raise RunConflict("another Dot daemon is already running", {"pid_file": str(path), "pid": holder}) from exc
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode("ascii"), 0)
        yield
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def running_mark(path: Path) -> Iterator[bool]:
    """Leave a mark at ``path`` for as long as the daemon runs, and remove it only when the daemon ends without an
    error. Yields whether the daemon before this one left its mark: it did not end cleanly (it was killed, it crashed,
    or it ended with an error, such as a scheduler loop that failed)."""
    left: bool = path.exists()
    path.touch(_PRIVATE_FILE_MODE)
    yield left
    path.unlink()


def _end_with_scheduler(server: DotServer, scheduler_loop: asyncio.Task[None]) -> None:
    """Schedules must not silently stop firing: when the scheduler loop ends by itself (its ticks failed
    ``FAILED_TICKS_TO_END`` times in a row, see ``scheduler/runner.py``), so does the daemon."""
    if scheduler_loop.cancelled():
        return
    log.error("scheduler_loop_ended", error=repr(scheduler_loop.exception()))
    server.should_exit = True


def _log_push_end(pusher: asyncio.Task[None]) -> None:
    """Pushes that stop before the daemon does are logged, so the phone's silence has a cause on record."""
    if pusher.cancelled() or pusher.exception() is None:
        return
    log.error("push_watch_ended", error=repr(pusher.exception()))


def _log_watch_end(watcher: asyncio.Task[None]) -> None:
    """A watch of dead dotx commands that stops before the daemon does is logged: their runs stay active until the
    daemon starts again."""
    if watcher.cancelled() or watcher.exception() is None:
        return
    log.error("command_watch_ended", error=repr(watcher.exception()))


def _log_warm_end(task: "asyncio.Task[None]") -> None:
    """The warming of the help vectors ended: quietly, or with the error it raised, which is not the daemon's end."""
    if task.cancelled():
        return
    error: BaseException | None = task.exception()
    if error is not None:
        log.warning("help_vectors_warming_failed", error_type=type(error).__name__, error=str(error))


def _log_backups_end(backups: asyncio.Task[None]) -> None:
    """The copies of the database that stop before the daemon does are logged: none is made until it starts again."""
    if backups.cancelled() or backups.exception() is None:
        return
    log.error("backups_ended", error=repr(backups.exception()))


def _log_watchers_end(watching: asyncio.Task[None]) -> None:
    """The loop of the watchers (item 571) that stops before the daemon does is logged: no watcher looks until the
    daemon starts again."""
    if watching.cancelled() or watching.exception() is None:
        return
    log.error("watchers_ended", error=repr(watching.exception()))


def _log_relay_end(relay: asyncio.Task[None]) -> None:
    """A relay of the runs of dotx commands that stops before the daemon does is logged: the pages see those runs only
    as they are stored, and their chats' queues wait, until the daemon starts again."""
    if relay.cancelled() or relay.exception() is None:
        return
    log.error("relay_ended", error=repr(relay.exception()))


async def _stop_server_on(stop: asyncio.Event, server: DotServer) -> None:
    """The first stop request ends the server gracefully, a second one forces it."""
    await stop.wait()
    log.info("stop_requested")
    server.should_exit = True
    stop.clear()
    await stop.wait()
    log.warning("stop_forced")
    server.force_exit = True


async def _cancelled(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.wait({task})


async def run_daemon(config: AppConfig) -> int:
    """Serve until SIGINT or SIGTERM, or until a restart is asked (``POST /api/system/restart``, item 409): then
    ``RESTART_CODE`` is returned, so that the service's launchd job starts the daemon again; 0 otherwise.

    Raises when the address is taken, another daemon runs, or the scheduler loop failed. On the way out the scheduler
    stops, the runs that are going on are cancelled, the schedules that finished are written and the services close.
    A start that finds the mark of a daemon that did not end cleanly (:func:`running_mark`) tells the owner, with the
    number of runs it left active (``daemon_restarted``).
    """
    host: str = config.server.host
    require_loopback(host)
    token: str = await asyncio.to_thread(read_or_create_token, token_path())
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    stop: asyncio.Event = asyncio.Event()
    restart_wanted: bool = False

    def restart() -> None:
        nonlocal restart_wanted
        restart_wanted = True
        stop.set()

    with instance_lock(pid_path()), running_mark(running_mark_path()) as unclean:
        async with AsyncExitStack() as cleanup:
            for number in _STOP_SIGNALS:
                loop.add_signal_handler(number, stop.set)
                cleanup.callback(loop.remove_signal_handler, number)
            listener: socket.socket = bind_listener(host, config.server.port)
            cleanup.callback(listener.close)
            port: int = listener.getsockname()[1]
            runtime: Runtime = await build_runtime(config)
            cleanup.push_async_callback(close_runtime, runtime)
            # A phone reaches the Mac away from home while it is kept awake on power (item 271).
            if config.server.keep_awake:
                await runtime.awake.start()
            manager: RunManager = RunManager(runtime)
            scheduler: Scheduler = Scheduler(runtime, manager)
            cleanup.push_async_callback(scheduler.drain)
            cleanup.push_async_callback(manager.shutdown)
            cleanup.push_async_callback(scheduler.stop)
            stopping: asyncio.Event = asyncio.Event()
            # The phone hears what the desktop notifications say, from before the start's own work (the restart below,
            # the firings the scheduler's first tick finds missed) until the runs are stopped on the way out.
            feed: Subscription = await cleanup.enter_async_context(runtime.bus.subscribe_kinds(PUSH_KINDS))
            pusher: asyncio.Task[None] = asyncio.create_task(
                watch_pushes(runtime.store, feed, runtime.push_keys, stopping), name="push"
            )
            pusher.add_done_callback(_log_push_end)
            cleanup.push_async_callback(_cancelled, pusher)
            # The skills' embeddings are made now, in the background, so that the first run finds them (item 404).
            warming: asyncio.Task[None] = asyncio.create_task(warm_help_vectors(runtime), name="help-vectors")
            warming.add_done_callback(_log_warm_end)
            cleanup.push_async_callback(_cancelled, warming)
            # The Command key is watched for SnapShots while they are on (item 405); the watch ends with the daemon.
            # Dot's own pages are looked for at its addresses: this Mac's and the names it is reached by (item 414).
            addresses: list[str] = [
                base_url(host, port),
                f"http://localhost:{port}",
                *(f"https://{name}" for name in config.server.reach),
            ]
            await runtime.snapshots.start(
                stored_taker(config.desktop, runtime.workspace),
                dot_shower(addresses, ui_url(host, port, token)),
                FlightOverlay(),
                runtime.desktop.pointer,
            )
            cleanup.push_async_callback(runtime.snapshots.stop)
            recovered: int = await manager.recover()
            if unclean:
                log.warning("daemon_restarted_after_unclean_end", runs_cut_off=recovered)
                await runtime.bus.emit(
                    SYSTEM_RUN_ID, DAEMON_RESTARTED_EVENT, DaemonRestarted(runs_cut_off=recovered).model_dump(), False
                )
            # A dotx command that dies leaves its run active: that run is closed while the daemon runs.
            watcher: asyncio.Task[None] = asyncio.create_task(manager.watch_commands(), name="dead-commands")
            watcher.add_done_callback(_log_watch_end)
            cleanup.push_async_callback(_cancelled, watcher)
            # The runs of dotx commands work on the pages as this daemon's own do, and their chats' queues move here.
            relay: asyncio.Task[None] = asyncio.create_task(manager.relay_elsewhere(), name="relay")
            relay.add_done_callback(_log_relay_end)
            cleanup.push_async_callback(_cancelled, relay)
            # A copy of the database every [store] backup_every_hours hours, kept where [store] backup_dir says (item
            # 526); the first at once when none is younger.
            if config.store.backup_every_hours > 0:
                backups: asyncio.Task[None] = asyncio.create_task(keep_backups(config.store, db_path()), name="backups")
                backups.add_done_callback(_log_backups_end)
                cleanup.push_async_callback(_cancelled, backups)
            # Before the scheduler and the server start: a resumed run takes its place before any new run, and no new
            # message reaches its thread before it.
            resumed: tuple[RunRecord, ...] = await manager.resume_safe() if config.agent.resume == "safe" else ()
            # A session is never resumed as a run: a campaign whose session the daemon cut off goes on with a new one.
            campaigns: int = await manager.resume_campaigns()
            # The owner's messaging apps (item 572): a message of theirs reaches its chat only after the runs the last
            # start cut off are resumed, and the channels stop before the manager does.
            channels: Channels = Channels(runtime, manager, host, port)
            await channels.start()
            cleanup.push_async_callback(channels.stop)
            scheduler_loop: asyncio.Task[None] = await scheduler.start()
            # The watchers (item 571) look beside the scheduler, from the daemon's own clock: no inbound webhook.
            if config.watchers.enabled:
                watching: asyncio.Task[None] = asyncio.create_task(
                    watch_loop(runtime, manager, stopping), name="watchers"
                )
                watching.add_done_callback(_log_watchers_end)
                cleanup.push_async_callback(_cancelled, watching)
            # The reactions on Dot's messages (item 586) wait to be told to their runs for a moment; they stop with
            # the daemon.
            reactions: Reactions = Reactions(runtime, manager)
            cleanup.push_async_callback(reactions.close)
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=scheduler,
                token=token,
                host=host,
                port=port,
                started_at=time.time(),
                stopping=stopping,
                restart=restart,
                channels=channels,
                reactions=reactions,
                phones=await asyncio.to_thread(PairedPhones.load, phones_path()),
            )
            server: DotServer = DotServer(serving_config(create_app(context)), stopping)
            scheduler_loop.add_done_callback(functools.partial(_end_with_scheduler, server))
            stopper: asyncio.Task[None] = asyncio.create_task(_stop_server_on(stop, server), name="stop-server")
            cleanup.push_async_callback(_cancelled, stopper)
            log.info(
                "daemon_started",
                url=base_url(host, port),
                pid=os.getpid(),
                home=str(dot_home()),
                recovered=recovered,
                resumed=[run.id for run in resumed],
                campaigns_planned=campaigns,
            )
            if not stop.is_set():
                await server.serve(sockets=[listener])
            log.info("daemon_stopping")
    return RESTART_CODE if restart_wanted else 0
