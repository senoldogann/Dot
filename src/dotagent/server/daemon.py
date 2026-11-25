"""The Dot daemon: one process that serves the API and the UI, runs the agent and fires the schedules."""

import asyncio
import contextlib
import fcntl
import functools
import os
import signal
import socket
import stat
import time
from collections.abc import Iterator
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from dotagent.agent.loop import warm_help_vectors
from dotagent.agent.manager import RunManager
from dotagent.agent.reactions import Reactions
from dotagent.agent.snapshots import stored_taker
from dotagent.awake import keep_awake_while_working
from dotagent.channels.service import Channels
from dotagent.config import AppConfig
from dotagent.core.errors import ConfigError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import db_path, dot_home, phones_path, pid_path, running_mark_path, token_path
from dotagent.desktop.flight import FlightOverlay
from dotagent.desktop.show_dot import dot_shower
from dotagent.events import DAEMON_RESTARTED_EVENT, SYSTEM_RUN_ID, DaemonRestarted, EventBus, Subscription
from dotagent.loop_watch import LoopWatch
from dotagent.meetings.desk import MeetingDesk
from dotagent.notify.apns import ApnsSender, HttpTransport, apns_set, read_key
from dotagent.notify.apns_watch import APNS_KINDS, ApnsWatch, watch_apns
from dotagent.notify.health import NotifyHealth
from dotagent.notify.leases import PhoneLeases
from dotagent.notify.needs_you import needs_you_count
from dotagent.notify.owner import tell_owner
from dotagent.notify.push import PUSH_KINDS, watch_pushes
from dotagent.notify.tell_ledger import TELL_SOURCE_KINDS, watch_tells
from dotagent.oauth.accounts import keep_revoking
from dotagent.phone.keeper import keep_phone
from dotagent.proactive.watching import watch_loop
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.server.activity import live_runs
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.guard import read_or_create_token, require_loopback
from dotagent.server.links import base_url, ui_url
from dotagent.server.phones import PairedPhones
from dotagent.server.thread_sync import watch_revisions
from dotagent.store.backup import keep_backups
from dotagent.store.db import Store
from dotagent.store.models import RunRecord

log = get_logger(__name__)

# Open connections get this long to finish when the daemon stops. Event streams end as soon as it starts to stop (see
# ``DotServer.shutdown``); this is the limit for a client that does anything else.
_GRACE_S: int = 3
# How often the daemon looks whether Dot works, to hold the Mac awake for it (item 1671).
_AWAKE_LOOK_S: float = 2.0
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


def _opened_file(path: Path, access: int) -> int:
    """A descriptor of the regular file at ``path``, made private when it is not there; a link there is not followed
    and anything but a regular file is refused (item 676)."""
    try:
        descriptor: int = os.open(path, access | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, _PRIVATE_FILE_MODE)
    except OSError as exc:
        raise ConfigError(
            "a file of the daemon in Dot's home is a link or cannot be opened; remove it and start again",
            {"path": str(path), "error": str(exc)},
        ) from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ConfigError(
            "a file of the daemon in Dot's home is not a regular file; remove it and start again", {"path": str(path)}
        )
    return descriptor


@contextlib.contextmanager
def instance_lock(path: Path) -> Iterator[None]:
    """Hold the daemon's pid file for as long as the daemon runs; a second daemon is refused. A link at ``path`` is not
    followed, nor is anything but a file written (item 676): the daemon would else empty what the link names."""
    descriptor: int = _opened_file(path, os.O_RDWR)
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
    left: bool = os.path.lexists(path)
    os.close(_opened_file(path, os.O_WRONLY))
    yield left
    # Gone already (removed by hand) is as good as removed: a clean end must not become an error here.
    path.unlink(missing_ok=True)


def _end_with_scheduler(server: DotServer, scheduler_loop: asyncio.Task[None]) -> None:
    """Schedules must not silently stop firing: when the scheduler loop ends by itself (its ticks failed
    ``FAILED_TICKS_TO_END`` times in a row, see ``scheduler/runner.py``), so does the daemon."""
    if scheduler_loop.cancelled():
        return
    log.error("scheduler_loop_ended", error=repr(scheduler_loop.exception()))
    server.should_exit = True


async def _keep_meeting_line(manager: RunManager, thread_id: str, goal: str, text: str) -> None:
    """Keep a meeting's line in its chat (item 1138), as the watchers' tells are kept."""
    await manager.keep_notice(thread_id, goal, text)


async def _waiting_on_owner(store: Store, manager: RunManager) -> int:
    """How many things wait on the owner now (item 1422), which an alert sets the iPhone app's badge to."""
    return await needs_you_count(store, await live_runs(store, manager), time.time())


def _log_push_end(health: NotifyHealth, channel: str, pusher: asyncio.Task[None]) -> None:
    """Pushes that stop before the daemon does are logged, and the health check says the phone is down, so a process
    that still answers HTTP is not taken for a phone that can hear."""
    if pusher.cancelled() or pusher.exception() is None:
        return
    if channel == "apns":
        health.mark_down("apns")
    else:
        health.mark_down("push")
    log.error("push_watch_ended", channel=channel, error=repr(pusher.exception()))


async def _tell_phone_owner(bus: EventBus, title: str, text: str) -> None:
    """Tell the owner the iPhone needs them; a tap opens Settings › Phone, which shows the link's state."""
    await tell_owner(bus, title, text, "#/settings/phone", "system", "")


def _log_phone_end(task: asyncio.Task[None]) -> None:
    """A keeper of the phone that stops before the daemon does is logged: the phone is not kept up after it."""
    if task.cancelled() or task.exception() is None:
        return
    log.error("phone_keeper_ended", error=repr(task.exception()))


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


def _dot_works(manager: RunManager, meetings: MeetingDesk) -> bool:
    """Whether Dot works now (item 1671): a run of its own, one of a ``dotx`` command it relays, or a meeting."""
    return bool(manager.active_run_ids()) or bool(manager.elsewhere_run_ids()) or meetings.busy() is not None


def _log_awake_end(awake_task: asyncio.Task[None]) -> None:
    """The hold on the Mac's idle sleep that stops before the daemon does is logged (``caffeinate`` could not be
    started): the Mac sleeps as it would until the daemon starts again."""
    if awake_task.cancelled() or awake_task.exception() is None:
        return
    log.error("keep_awake_while_working_ended", error=repr(awake_task.exception()))


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


def _log_revision_watch_end(watch: asyncio.Task[None]) -> None:
    """A look at the chats' revisions that stops before the daemon does is logged: the phones' streams hear of no
    change of a chat until the daemon starts again (item 1686)."""
    if watch.cancelled() or watch.exception() is None:
        return
    log.error("revision_watch_ended", error=repr(watch.exception()))


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
    # Read before anything starts (item 1239): a file that cannot be read keeps the daemon from starting, as
    # docs/phone.md says, not after it resumed runs, opened iMessage and started the scheduler, all cut off again.
    phones: PairedPhones = await asyncio.to_thread(PairedPhones.load, phones_path())
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
            # A meeting's summary is a line in its chat, which the run manager keeps (item 1138); a meeting a
            # restart cut is marked failed.
            runtime.meetings.tell_with(functools.partial(_keep_meeting_line, manager))
            await runtime.meetings.start()
            scheduler: Scheduler = Scheduler(runtime, manager, scout=True)
            cleanup.push_async_callback(scheduler.drain)
            cleanup.push_async_callback(manager.shutdown)
            cleanup.push_async_callback(scheduler.stop)
            stopping: asyncio.Event = asyncio.Event()
            # The phone hears what the desktop notifications say, from before the start's own work (the restart below,
            # the firings the scheduler's first tick finds missed) until the runs are stopped on the way out.
            phone: NotifyHealth = NotifyHealth()
            # Which phones show Dot now: the route the app renews it through and the watch of its alerts share them.
            leases: PhoneLeases = PhoneLeases()
            feed: Subscription = await cleanup.enter_async_context(runtime.bus.subscribe_kinds(PUSH_KINDS))
            pusher: asyncio.Task[None] = asyncio.create_task(
                watch_pushes(runtime.store, feed, runtime.push_keys, stopping, runtime.config.notify.push_hosts),
                name="push",
            )
            phone.mark_live("push")
            pusher.add_done_callback(functools.partial(_log_push_end, phone, "push"))
            cleanup.push_async_callback(_cancelled, pusher)
            # What the owner is told is kept, to count what they do with it (item 1743).
            told_feed: Subscription = await cleanup.enter_async_context(runtime.bus.subscribe_kinds(TELL_SOURCE_KINDS))
            teller: asyncio.Task[None] = asyncio.create_task(
                watch_tells(runtime.store, told_feed, stopping), name="tell-ledger"
            )
            cleanup.push_async_callback(_cancelled, teller)
            # A sign-out the provider did not take is asked again now and every hour for a week (item 1312).
            revoking: asyncio.Task[None] = asyncio.create_task(
                keep_revoking(runtime.accounts, stopping), name="sign-out-revokes"
            )
            cleanup.push_async_callback(_cancelled, revoking)
            # The iPhone app hears the same through APNs, and a run's Live Activity too (item 997), once a key is set.
            if apns_set(config.apns):
                transport: HttpTransport = HttpTransport()
                cleanup.push_async_callback(transport.aclose)
                sender: ApnsSender = ApnsSender(config.apns, await asyncio.to_thread(read_key, config.apns), transport)
                apns_feed: Subscription = await cleanup.enter_async_context(runtime.bus.subscribe_kinds(APNS_KINDS))
                apns: asyncio.Task[None] = asyncio.create_task(
                    watch_apns(
                        ApnsWatch(
                            runtime.store,
                            sender,
                            leases,
                            functools.partial(_waiting_on_owner, runtime.store, manager),
                        ),
                        apns_feed,
                        stopping,
                    ),
                    name="apns",
                )
                phone.mark_live("apns")
                apns.add_done_callback(functools.partial(_log_push_end, phone, "apns"))
                cleanup.push_async_callback(_cancelled, apns)
            # The skills' embeddings are made now, in the background, so that the first run finds them (item 404).
            warming: asyncio.Task[None] = asyncio.create_task(warm_help_vectors(runtime), name="help-vectors")
            warming.add_done_callback(_log_warm_end)
            cleanup.push_async_callback(_cancelled, warming)
            # The Command key is watched for Snapshots while they are on (item 405); the watch ends with the daemon.
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
            # The iPhone's runner app is kept up, and the owner told once when the phone needs them
            # (docs/phone-control.md).
            if runtime.phone is not None:
                phone_task: asyncio.Task[None] = asyncio.create_task(
                    keep_phone(
                        runtime.phone,
                        partial(_tell_phone_owner, runtime.bus),
                        config.phone.check_every_s,
                        config.phone.notify_after_s,
                        stopping,
                    ),
                    name="phone-keeper",
                )
                phone_task.add_done_callback(_log_phone_end)
                cleanup.push_async_callback(_cancelled, phone_task)
            # The runs of dotx commands work on the pages as this daemon's own do, and their chats' queues move here.
            relay: asyncio.Task[None] = asyncio.create_task(manager.relay_elsewhere(), name="relay")
            relay.add_done_callback(_log_relay_end)
            cleanup.push_async_callback(_cancelled, relay)
            # The chats whose revision moved, whichever process wrote, told to the phones' streams (item 1686).
            watch: asyncio.Task[None] = asyncio.create_task(
                watch_revisions(runtime.store, runtime.bus), name="revision-watch"
            )
            watch.add_done_callback(_log_revision_watch_end)
            cleanup.push_async_callback(_cancelled, watch)
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
            # The helpers that worked on without their run when the daemon stopped (item 1351): their chats are told
            # what they reported, or that the stop cut them off, by the run the chat has now or one started for it.
            untold: int = await manager.tell_helper_reports()
            # The owner's messaging apps (item 572): a message of theirs reaches its chat only after the runs the last
            # start cut off are resumed, and the channels stop before the manager does.
            channels: Channels = Channels(runtime, manager, host, port)
            await channels.start()
            cleanup.push_async_callback(channels.stop)
            scheduler_loop: asyncio.Task[None] = await scheduler.start()
            # The Mac does not idle-sleep while Dot works (item 1671): a run of its own or of a dotx command, a
            # schedule's run, a meeting.
            if config.daemon.keep_awake:
                awake_task: asyncio.Task[None] = asyncio.create_task(
                    keep_awake_while_working(
                        runtime.work_awake,
                        functools.partial(_dot_works, manager, runtime.meetings),
                        config.daemon.keep_awake_after_s,
                        _AWAKE_LOOK_S,
                        stopping,
                    ),
                    name="keep-awake",
                )
                awake_task.add_done_callback(_log_awake_end)
                cleanup.push_async_callback(_cancelled, awake_task)
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
            # A loop that stops answering is said in the log with where it is, and listed in Diagnostics (item 1672).
            loop_watch: LoopWatch = LoopWatch()
            loop_watch.start()
            cleanup.callback(loop_watch.stop)
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
                phones=phones,
                notifications=phone,
                leases=leases,
                loop_watch=loop_watch,
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
                helper_reports_untold=untold,
            )
            if not stop.is_set():
                await server.serve(sockets=[listener])
            log.info("daemon_stopping")
    return RESTART_CODE if restart_wanted else 0
