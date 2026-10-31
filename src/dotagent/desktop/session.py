"""The desktop session: the single owner of everything that touches the screen.

Every accessibility, CGEvent and Cocoa call runs on one worker thread, one operation at a time. The session remembers,
per conversation thread, which application the thread works in, the elements it was given refs for and what it was shown
of each view. A ref therefore means the same element for as long as the element lives, and a repeated look at a view
shows only what changed.
"""

import asyncio
import contextlib
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from dotagent.config import DesktopConfig
from dotagent.core.errors import (
    ApprovalParked,
    ApprovalRejected,
    DesktopError,
    FailSafeTriggered,
    HandedOff,
    PolicyDenied,
)
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.desktop import actions, apps, ax, memory, perms, safety, screen, shots, views
from dotagent.desktop import input as hid
from dotagent.desktop.actions import Effect, Inspection, SecureFieldCheck, StepEnv
from dotagent.desktop.memory import AppMemory, ThreadDesktop
from dotagent.desktop.pointer import PointerOverlay
from dotagent.desktop.render import describe_step
from dotagent.desktop.safety import StepContext
from dotagent.desktop.types import (
    ActOutcome,
    AppInfo,
    AppView,
    AXElement,
    Node,
    OpenedApp,
    Rect,
    ScreenshotView,
    Snapshot,
    SnapshotView,
    Step,
    StepReport,
    TextRead,
    WindowInfo,
    parse_ref,
)
from dotagent.desktop.views import ViewRequest
from dotagent.policy.elements import Element
from dotagent.vault import shown

_log = get_logger("dotagent.desktop.session")

_THREAD_CAP: int = 32
# How long a step's review may take before its answer is taken to have come from the owner, who may have moved the
# pointer to give it (:meth:`DesktopSession._run_step`).
_ANSWERED_AT_ONCE_S: float = 1.0
_WAKE_THRESHOLD: int = 8
_WAKE_WAIT_S: float = 4.0
_WAKE_POLL_S: float = 0.15
_STEP_GAP_S: float = 0.06
# How long the applications the preview of a batch listed stand for its act (item 343): the act follows at once unless
# the owner was asked about the batch, which takes longer.
_LISTING_KEPT_S: float = 1.0
_WINDOW_WAIT_S: float = 3.0
_WINDOW_POLL_S: float = 0.1
_AWAKE_MARGIN_S: float = 120.0
_DISPLAY_WAKE_S: float = 0.6
_IDLE_HOLD_S: int = 5
_REAP_TIMEOUT_S: float = 2.0
_CAFFEINATE: str = "/usr/bin/caffeinate"
_FRONT_ACTIONS: frozenset[str] = frozenset({"click", "double_click", "right_click", "type", "paste", "key", "drag"})
# How often a run that waits for its turn at the desktop looks whether the run that holds it waits for the owner, and
# how long that wait has lasted before it counts as one (a policy check answers in milliseconds), item 555.
_TURN_POLL_S: float = 0.5
_ASKING_GRACE_S: float = 2.0


class StepReview(FrozenModel):
    """One step about to run, as the reviewer sees it: what it is, the application it happens in and what it touches."""

    index: int
    step: Step
    description: str
    context: StepContext
    target: str


type StepReviewer = Callable[[StepReview], Awaitable[None]]
"""Called before each step of a batch; raises ``PolicyDenied``, ``ApprovalRejected`` or ``HandedOff`` to stop it."""


@dataclass(frozen=True, slots=True)
class _Hold:
    """A ``caffeinate`` child that keeps the Mac and its display awake until ``expires_at`` (monotonic seconds)."""

    process: subprocess.Popen[bytes]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _Walk:
    snapshot: Snapshot
    windows: tuple[WindowInfo, ...]
    scale: float


@dataclass(frozen=True, slots=True)
class _Batch:
    """What a run of steps left behind: the reports, why it stopped, the environment and the thread's memory."""

    reports: tuple[StepReport, ...]
    stopped: str | None
    env: StepEnv
    state: ThreadDesktop
    side_effects: bool


def _window_view(max_chars: int) -> ViewRequest:
    """The view shown after an application opens or a batch ends: its window, the changes since the last view."""
    return ViewRequest(scope="window", ref=None, query=None, full=False, max_chars=max_chars)


def no_browser_chrome() -> frozenset[int]:
    """The Chromes Dot's browser works in, for a desktop session with no browser beside it (``dotx desktop``): none."""
    return frozenset()


def _pick_app(state: ThreadDesktop, name: str | None, running: Sequence[AppInfo], preferred: frozenset[int]) -> AppInfo:
    """The application a call is about: the one named, else the one the thread works in, else the one in front. A name
    that fits several applications means the one this thread works in, else the ``preferred`` one, the Chrome Dot's
    browser works in, else the one in front (item 750)."""
    if name is not None:
        return apps.require_app(name, running, preferred, state.pid)
    if state.pid is not None:
        for app in running:
            if app.pid == state.pid:
                return app
        raise DesktopError(
            f"the application this conversation was working with (pid {state.pid}) is not running any more; "
            "name the application with app",
            {"pid": state.pid},
        )
    front: AppInfo | None = next((app for app in running if app.frontmost), None)
    if front is None:
        raise DesktopError("no application has the keyboard focus right now; name the application with app", {})
    return front


def _refuse_secrets(app: AppInfo) -> None:
    if safety.is_secrets_app(app.name, app.bundle_id):
        raise DesktopError(
            f"{app.name} holds passwords or keys; its content is never read and it is never controlled",
            {"app": app.name, "bundle_id": app.bundle_id or ""},
        )


_OWN_WINDOW_SHOWN: str = (
    "Dot's own window may show a secret of the owner's vault right now, and the accessibility tree of a window gives "
    "the text of what it shows: it is not read while the secret may be on the screen, as a password manager is never "
    "read. Try again in a minute"
)


def _notes_of(snapshot: Snapshot) -> tuple[str, ...]:
    return tuple(
        f"{count} element{'s' if count != 1 else ''} could not be read: {ax.error_text(code)}"
        for code, count in sorted(snapshot.skipped.items())
    )


def _root_of(memory_of_app: AppMemory, request: ViewRequest, app: AppInfo) -> AXElement | None:
    """The element a subtree view starts from, or ``None`` for a view of windows or the menu bar."""
    if request.ref is None:
        return None
    element: AXElement | None = memory.known_elements(memory_of_app).get(request.ref)
    if element is None:
        raise DesktopError(
            f"a{request.ref} is not an element of {app.name} that this conversation has been shown; "
            "take a new snapshot first",
            {"ref": f"a{request.ref}", "app": app.name},
        )
    return element


def _walk_job(
    config: DesktopConfig,
    pid: int,
    request: ViewRequest,
    root: AXElement | None,
    known: Snapshot | None,
    first_ref: int,
) -> _Walk:
    """Walk the accessibility tree of an application and list the windows (worker thread)."""
    element: AXElement = ax.app_element(pid, config.ax_timeout_s)
    roots: tuple[ax.Root, ...] = (
        (ax.Root(root, None),) if root is not None else ax.roots_for(element, pid, request.scope)
    )
    limits: ax.WalkLimits = ax.WalkLimits(config.ax_max_nodes, config.ax_max_depth, config.ax_budget_s)
    snapshot: Snapshot = ax.snapshot(known, roots, pid, first_ref, limits)
    return _Walk(
        snapshot=snapshot,
        windows=screen.windows_everywhere(),
        scale=screen.model_scale(screen.main_display().bounds, config.screenshot_max_edge),
    )


def _list_job(config: DesktopConfig) -> tuple[AppView, ...]:
    running: tuple[AppInfo, ...] = apps.list_apps()
    scale: float = screen.model_scale(screen.main_display().bounds, config.screenshot_max_edge)
    return views.app_views(running, screen.windows_everywhere(), scale)


def _app_view_job(config: DesktopConfig, app: AppInfo) -> AppView:
    scale: float = screen.model_scale(screen.main_display().bounds, config.screenshot_max_edge)
    return views.app_view(app, screen.windows_everywhere(), scale)


def _open_job(
    config: DesktopConfig, query: str, preferred: frozenset[int], bundle: str | None, current: int | None
) -> tuple[AppInfo, bool]:
    """Open an application (worker thread): the application and whether this call started it. An application that
    runs without a window is asked to reopen one, as a click on its Dock icon would. ``bundle``, the path a check of
    ``query`` came to, is what starts when none runs (item 418)."""
    listed: tuple[AppInfo, ...] = apps.list_apps()
    running: AppInfo | None = apps.find_app(query, listed, preferred, current)
    if running is None:
        return apps.open_app(query, config.launch_timeout_s, preferred, bundle, current), True
    # Launch Services reopens a window for a bundle id, whichever copy of it it picks: not while another copy runs.
    twins: bool = any(app.pid != running.pid and app.bundle_id == running.bundle_id for app in listed)
    if not twins and not views.has_window(running.pid, screen.windows_everywhere()):
        apps.launch(running.bundle_id or running.bundle_path or running.name)
    apps.activate(running, config.activate_timeout_s)
    return running, False


def _context_of(app: AppInfo, node: Node | None, lines: Mapping[str, str]) -> StepContext:
    role: safety.AppRole = safety.app_role(app.name, app.bundle_id)
    return StepContext(
        role=role,
        app=app.name,
        element=None if node is None else Element(role=node.role, name=node.label),
        secure=node is not None and node.secure(),
        line=lines.get(app.name) if role == "terminal" else None,
    )


def _node_for(known: Mapping[int, Node], raw_ref: str | None) -> Node | None:
    if raw_ref is None:
        return None
    try:
        number: int = parse_ref(raw_ref)
    except ValueError as error:
        raise DesktopError(str(error), {"ref": raw_ref}) from error
    return known.get(number)


def _preview_job(
    state: ThreadDesktop,
    name: str | None,
    steps: Sequence[Step],
    lines: Mapping[str, str],
    running: tuple[AppInfo, ...],
    preferred: frozenset[int],
) -> tuple[StepContext, ...]:
    """The context of each step as far as it can be told without touching the screen (worker thread), each with the
    terminal lines the steps before it typed."""
    current: AppInfo = _pick_app(state, name, running, preferred)
    known: Mapping[int, Node] = memory.known_nodes(memory.memory_of(state, current.pid))
    contexts: list[StepContext] = []
    typed: Mapping[str, str] = lines
    for step in steps:
        if step.action == "activate":
            named: AppInfo | None = apps.find_app(step.text or "", running, preferred, current.pid)
            if named is not None:
                current = named
                known = memory.known_nodes(memory.memory_of(state, current.pid))
            context: StepContext = _context_of(current, None, typed)
        else:
            context = _context_of(current, _node_for(known, step.ref), typed)
        contexts.append(context)
        typed = safety.lines_after(typed, step, context)
    return tuple(contexts)


def _needs_front(steps: Sequence[Step], known: Mapping[int, Node]) -> bool:
    """Whether the steps, up to the first one that chooses the application, need the target in front. A click that
    is a press through accessibility (item 862) needs it no more than a press does; ``known`` holds the elements the
    last view showed."""
    for step in steps:
        if step.action == "activate":
            return False
        if step.action in _FRONT_ACTIONS:
            node: Node | None = _node_for(known, step.ref) if step.action == "click" else None
            if node is not None and actions.pressed_on_click(step, node.role, node.enabled):
                continue
            return True
    return False


def _seconds_of(step: Step) -> float:
    if step.seconds is None:
        raise DesktopError("a wait step has no seconds", {})
    return step.seconds


def _remeasure(env: StepEnv) -> StepEnv:
    """Note which application holds the keyboard now; the next keyboard step must find it still there."""
    front: int | None = apps.frontmost_pid()
    running: tuple[AppInfo, ...] = env.running
    if front is not None and all(app.pid != front for app in running):
        running = apps.list_apps()
    return replace(env, expected_front=front, running=running)


def _front_taken_by(before: StepEnv, after: StepEnv) -> str | None:
    """The name of the application that came to the front during a step, when it is neither the one that was in front
    nor the one the batch works in (which a click may well bring forward); ``None`` when the front did not change or
    cannot be told. An unknown front before the step is no change to speak of: nothing is known to have been taken."""
    front: int | None = after.expected_front
    if front is None or before.expected_front is None or front in (before.expected_front, after.app.pid):
        return None
    taker: AppInfo | None = next((app for app in after.running if app.pid == front), None)
    return "another application" if taker is None else taker.name


def _switch_job(config: DesktopConfig, env: StepEnv, state: ThreadDesktop, app: AppInfo) -> StepEnv:
    """The environment for the steps that follow an ``activate`` step (worker thread)."""
    return replace(
        env,
        app=app,
        app_element=ax.app_element(app.pid, config.ax_timeout_s),
        elements=memory.known_elements(memory.memory_of(state, app.pid)),
        running=apps.list_apps(),
        expected_front=apps.frontmost_pid(),
        expected_pointer=None,
    )


@dataclass(slots=True)
class _Front:
    before: int | None
    left: int | None = None


def _front_or_none() -> int | None:
    """The application in front, or ``None`` when that cannot be told (worker thread)."""
    try:
        return apps.frontmost_pid()
    except DesktopError:
        return None


# The steps whose pointer ripples where it lands (item 706).
_CLICKS: frozenset[str] = frozenset({"click", "double_click", "right_click"})


class DesktopSession:
    """Looks at and controls the Mac's desktop for the conversation threads of one run."""

    def __init__(
        self,
        config: DesktopConfig,
        failsafe_file: Path,
        browser_chrome: Callable[[], frozenset[int]],
        pointer: PointerOverlay | None = None,
    ) -> None:
        self._config: DesktopConfig = config
        self._failsafe_file: Path = failsafe_file
        # Dot's own pointer on the screen (item 706), which shows where the steps go; it never does a step. A test
        # gives its own, or none is drawn: only the daemon's session starts the helper that draws it.
        self._pointer: PointerOverlay = pointer if pointer is not None else PointerOverlay(enabled=False)
        # The chats whose steps moved it since their run began, so that the end of the last one fades it.
        self._pointing: set[str] = set()
        # The pids of the Chromes Dot's browser works in (item 343), asked on the worker thread when a name is resolved.
        self._browser_chrome: Callable[[], frozenset[int]] = browser_chrome
        # The applications as the preview of a batch listed them, and when: the act that follows at once uses them
        # rather than listing them again (item 343).
        self._listed: tuple[float, tuple[AppInfo, ...]] | None = None
        self._pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dot-desktop")
        self._lock: asyncio.Lock = asyncio.Lock()
        # When the batch that holds the desktop began to wait for the owner's answer about a step (monotonic seconds),
        # else None (item 555).
        self._asking_since: float | None = None
        self._threads: OrderedDict[str, ThreadDesktop] = OrderedDict()
        self._woken: dict[int, AXElement] = {}
        self._wake_tried: set[int] = set()
        self._hold: _Hold | None = None
        # The command line of each terminal application, by name, as far as Dot typed it since the line was last
        # empty; a terminal whose line cannot be told is not in it, so Return there runs code that is not judged.
        self._lines: dict[str, str] = {}
        # By chat whose run acts on the desktop (item 702): the application in front before its first act, and the
        # one its last act left in front. The end of the run gives the front back to the first while the second is
        # still there.
        self._fronts: dict[str, _Front] = {}

    async def _call[T](self, work: Callable[[], T]) -> T:
        """``work`` on the desktop's worker thread. A call whose coroutine is cancelled (the run stopped, its tool timed
        out) cannot stop the thread: it sets the call's stop, which the steps' failsafe checks read between keys, so
        the step ends at the next check instead of typing on (item 747)."""
        stop: threading.Event = threading.Event()

        def run() -> T:
            actions.set_call_stop(stop)
            try:
                return work()
            finally:
                actions.set_call_stop(None)

        try:
            return await asyncio.get_running_loop().run_in_executor(self._pool, run)
        except asyncio.CancelledError:
            stop.set()
            raise

    def _refuse_while_asking(self) -> None:
        since: float | None = self._asking_since
        if since is not None and time.monotonic() - since >= _ASKING_GRACE_S:
            raise DesktopError(
                "another run is waiting for the owner's answer about a step on the desktop, and no other run uses the "
                "desktop while it waits, since the owner may be doing that step by hand on this screen; try again "
                "later, or go on with what does not need the desktop",
                {},
            )

    @contextlib.asynccontextmanager
    async def _turn(self) -> AsyncIterator[None]:
        """One operation's turn at the desktop, which one run uses at a time. A run that finds the desktop held by a
        batch that waits for the owner's answer, which takes minutes, is told so at once instead of queueing behind it
        until its own tool times out (a batch's preview has no timeout at all): the lock is polled, so a run that was
        already queued when the wait began is told too (item 555)."""
        while True:
            self._refuse_while_asking()
            try:
                await asyncio.wait_for(self._lock.acquire(), _TURN_POLL_S)
            except TimeoutError:
                continue
            break
        try:
            yield
        finally:
            self._lock.release()

    async def _ask(self, reviewer: StepReviewer, review: StepReview) -> None:
        """Put a step to the reviewer, who may wait minutes for the owner's answer: the desktop stays held meanwhile
        (:meth:`_turn` says why to the runs that ask for it)."""
        self._asking_since = time.monotonic()
        try:
            await reviewer(review)
        finally:
            self._asking_since = None

    def _running(self) -> tuple[tuple[AppInfo, ...], frozenset[int]]:
        """The applications running now and the Chromes Dot's browser works in (worker thread)."""
        return apps.list_apps(), self._browser_chrome()

    def _listing_for_preview(self) -> tuple[AppInfo, ...]:
        """The applications for the preview of a batch, kept for the act that follows (worker thread)."""
        running: tuple[AppInfo, ...] = apps.list_apps()
        self._listed = (time.monotonic(), running)
        return running

    def _listing_for_act(self) -> tuple[AppInfo, ...]:
        """The applications for an act: the preview's when it listed them less than ``_LISTING_KEPT_S`` ago, as when the
        act follows its preview with no question to the owner between them, else listed now (worker thread). Each
        listing takes 25 ms (``lsappinfo``)."""
        listed: tuple[float, tuple[AppInfo, ...]] | None = self._listed
        self._listed = None
        if listed is not None and time.monotonic() - listed[0] < _LISTING_KEPT_S:
            return listed[1]
        return apps.list_apps()

    def _preview_in_worker(
        self, state: ThreadDesktop, name: str | None, steps: Sequence[Step], lines: Mapping[str, str]
    ) -> tuple[StepContext, ...]:
        return _preview_job(state, name, steps, lines, self._listing_for_preview(), self._browser_chrome())

    def _thread(self, thread_id: str) -> ThreadDesktop:
        found: ThreadDesktop | None = self._threads.get(thread_id)
        return found if found is not None else memory.fresh_thread()

    def _remember(self, thread_id: str, state: ThreadDesktop) -> None:
        self._threads[thread_id] = state
        self._threads.move_to_end(thread_id)
        while len(self._threads) > _THREAD_CAP:
            self._threads.popitem(last=False)

    # -- keeping the Mac awake -------------------------------------------------------------------------------------

    def _release_hold(self) -> None:
        hold: _Hold | None = self._hold
        self._hold = None
        if hold is not None:
            hold.process.terminate()
            hold.process.wait(timeout=_REAP_TIMEOUT_S)

    def _hold_awake(self, seconds: int) -> None:
        """Keep the Mac and its display awake for ``seconds``; a sleeping display is switched on."""
        self._release_hold()
        process: subprocess.Popen[bytes] = subprocess.Popen(
            [_CAFFEINATE, "-d", "-i", "-u", "-t", str(seconds)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._hold = _Hold(process=process, expires_at=time.monotonic() + seconds)

    def _keep_awake(self) -> None:
        hold: _Hold | None = self._hold
        alive: bool = hold is not None and hold.process.poll() is None
        if self._config.keep_awake and not (
            alive and hold is not None and hold.expires_at - time.monotonic() > _AWAKE_MARGIN_S
        ):
            self._hold_awake(self._config.keep_awake_s)

    def _check_screen(self) -> None:
        """Raise unless the screen can be seen and used; a sleeping display or a screen saver is woken first."""
        state = screen.screen_state(screen.windows_on_screen())
        if state == "locked":
            raise DesktopError("the Mac is locked; the user has to unlock it before the desktop can be used", {})
        if state != "ready":
            self._hold_awake(self._config.keep_awake_s if self._config.keep_awake else _IDLE_HOLD_S)
            time.sleep(_DISPLAY_WAKE_S)
            state = screen.screen_state(screen.windows_on_screen())
            if state != "ready":
                raise DesktopError(f"the screen is not usable ({state}) and could not be woken", {"state": state})
        self._keep_awake()

    # -- what must hold before an operation ------------------------------------------------------------------------

    def _prepare_listing(self) -> None:
        perms.require_accessibility()

    def _prepare_looking(self) -> None:
        perms.require_accessibility()
        self._check_screen()

    def _prepare_acting(self) -> None:
        perms.require_accessibility()
        actions.check_stop(self._config, self._failsafe_file)
        self._check_screen()

    def _prepare_screenshot(self) -> None:
        perms.require_screen_recording()
        self._check_screen()

    async def _prepare(self, job: Callable[[], None]) -> None:
        apps.ensure_window_server()
        await self._call(job)

    # -- looking ---------------------------------------------------------------------------------------------------

    async def _wake(self, app: AppInfo, probe: Callable[[], _Walk]) -> _Walk | None:
        """Ask an Electron or Chromium application to build its accessibility tree and wait until it has one.
        The application is told to stop again when nothing came of it."""
        self._wake_tried.add(app.pid)
        element: AXElement = await self._call(partial(ax.app_element, app.pid, self._config.ax_timeout_s))
        await self._call(partial(ax.wake_accessibility, element))
        woken: bool = False
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        deadline: float = loop.time() + _WAKE_WAIT_S
        try:
            while loop.time() < deadline:
                await asyncio.sleep(_WAKE_POLL_S)
                walk: _Walk = await self._call(probe)
                if len(walk.snapshot.nodes) > _WAKE_THRESHOLD:
                    woken = True
                    self._woken[app.pid] = element
                    _log.info("accessibility_woken", app=app.name, pid=app.pid, nodes=len(walk.snapshot.nodes))
                    return walk
            return None
        finally:
            if not woken:
                await self._call(partial(ax.rest_accessibility, element))

    async def _look(
        self, state: ThreadDesktop, app: AppInfo, request: ViewRequest
    ) -> tuple[SnapshotView, ThreadDesktop]:
        """Take a view of ``app`` and the thread's memory after it."""
        _refuse_secrets(app)
        await self._refuse_own_while_shown(app)
        remembered: AppMemory = memory.memory_of(state, app.pid)
        probe: Callable[[], _Walk] = partial(
            _walk_job,
            self._config,
            app.pid,
            request,
            _root_of(remembered, request, app),
            remembered.known,
            state.next_ref,
        )
        walk: _Walk = await self._call(probe)
        notes: list[str] = list(_notes_of(walk.snapshot))
        if (
            request.ref is None
            and request.scope != "menu_bar"
            and len(walk.snapshot.nodes) <= _WAKE_THRESHOLD
            and app.pid not in self._wake_tried
        ):
            woken: _Walk | None = await self._wake(app, probe)
            if woken is None:
                notes.append(
                    "this application shows almost nothing to accessibility; use desktop_screenshot, which lists "
                    "the text on the screen with the x, y of each line, and act by coordinates"
                )
            else:
                walk = woken
                notes.append("accessibility was switched on for this application; it builds its elements on demand")
        key: str = views.view_key(request)
        result: views.ViewResult = views.build_view(
            views.app_view(app, walk.windows, walk.scale),
            views.heading_of(request, walk.snapshot.nodes),
            walk.snapshot,
            request,
            remembered.rendered.get(key),
            walk.scale,
            tuple(notes),
        )
        return result.view, memory.after_walk(state, app.pid, walk.snapshot, key, result.baseline)

    async def _refuse_own_while_shown(self, app: AppInfo) -> None:
        """No look at Dot's own windows while a secret of the owner's vault may be on the screen (item 621,
        :mod:`dotagent.vault.shown`): the eye of Settings › Vault shows it in the window's text, which the
        accessibility tree gives whole. As a password manager's content is never read (:func:`_refuse_secrets`), the
        window of Dot is not, for as long as the secret may be on the screen. The windows are listed only then."""
        if not shown.on_screen():
            return
        windows: tuple[WindowInfo, ...] = await self._call(screen.windows_everywhere)
        if safety.shows_own_window(app.name, app.bundle_id, app.pid, windows):
            raise DesktopError(_OWN_WINDOW_SHOWN, {"app": app.name})

    async def _closing_view(
        self, state: ThreadDesktop, app: AppInfo, max_chars: int
    ) -> tuple[SnapshotView | None, str | None, ThreadDesktop]:
        """The window after a batch, at most ``max_chars`` of its text, or the reason there is none."""
        try:
            view, updated = await self._look(state, app, _window_view(max_chars))
        except FailSafeTriggered:
            raise
        except DesktopError as error:
            running: tuple[AppInfo, ...] = await self._call(apps.list_apps)
            if all(candidate.pid != app.pid for candidate in running):
                return None, f"{app.name} is no longer running", state
            return None, error.message, state
        return view, None, updated

    async def list_apps(self) -> tuple[AppView, ...]:
        """The applications that are open, the one with the keyboard focus first."""
        async with self._turn():
            await self._prepare(self._prepare_listing)
            return await self._call(partial(_list_job, self._config))

    async def snapshot(self, thread_id: str, app: str | None, request: ViewRequest) -> SnapshotView:
        async with self._turn():
            await self._prepare(self._prepare_looking)
            state: ThreadDesktop = self._thread(thread_id)
            running, preferred = await self._call(self._running)
            target: AppInfo = _pick_app(state, app, running, preferred)
            view, updated = await self._look(memory.targeting(state, target.pid), target, request)
            self._remember(thread_id, updated)
            return view

    async def screenshot(self) -> ScreenshotView:
        async with self._turn():
            await self._prepare(self._prepare_screenshot)
            return await self._without_pointer(partial(shots.take_screen, self._config))

    async def zoom(self, region: Rect) -> ScreenshotView:
        """A screenshot of ``region`` (model units) at the display's own sharpness."""
        async with self._turn():
            await self._prepare(self._prepare_screenshot)
            return await self._without_pointer(partial(shots.take_region, self._config, region))

    @property
    def pointer(self) -> PointerOverlay:
        """Dot's own pointer on the screen (item 706), which what else captures the screen takes off it first."""
        return self._pointer

    async def _without_pointer(self, capture: Callable[[], ScreenshotView]) -> ScreenshotView:
        """``capture`` with Dot's pointer off the screen (item 706): the model sees the screen, not Dot's arrow over
        what it is to read."""
        async with self._pointer.hidden():
            return await self._call(capture)

    async def read(self, thread_id: str, app: str | None, ref: int, offset: int, max_chars: int) -> TextRead:
        """The text of one element, ``max_chars`` of it from ``offset``."""
        async with self._turn():
            await self._prepare(self._prepare_looking)
            state: ThreadDesktop = self._thread(thread_id)
            running, preferred = await self._call(self._running)
            target: AppInfo = _pick_app(state, app, running, preferred)
            _refuse_secrets(target)
            await self._refuse_own_while_shown(target)
            element: AXElement | None = memory.known_elements(memory.memory_of(state, target.pid)).get(ref)
            if element is None:
                raise DesktopError(
                    f"a{ref} is not an element of {target.name} that this conversation has been shown; "
                    "take a new snapshot first",
                    {"ref": f"a{ref}", "app": target.name},
                )
            label, text, total = await self._call(partial(ax.read_text, element, target.pid))
            self._remember(thread_id, memory.targeting(state, target.pid))
            return TextRead(label=label, text=text[offset : offset + max_chars], total_chars=total, offset=offset)

    async def read_clipboard(self) -> str | None:
        async with self._turn():
            return await self._call(hid.read_clipboard)

    async def write_clipboard(self, text: str) -> None:
        async with self._turn():
            await self._call(partial(hid.write_clipboard, text))

    # -- opening ---------------------------------------------------------------------------------------------------

    async def _wait_for_window(self, pid: int) -> bool:
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        deadline: float = loop.time() + _WINDOW_WAIT_S
        while True:
            if views.has_window(pid, await self._call(screen.windows_everywhere)):
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(_WINDOW_POLL_S)

    async def open_app(self, thread_id: str, query: str, max_chars: int, bundle: str | None) -> OpenedApp:
        """Open (or bring forward) an application, make it the thread's target and show its window, at most
        ``max_chars`` of its text. ``bundle`` is the path a check of ``query`` came to, and is what starts
        (item 418)."""
        async with self._turn():
            await self._prepare(self._prepare_acting)
            await self._note_front_before(thread_id)
            working_in: int | None = self._thread(thread_id).pid
            try:
                opened, fresh = await self._call(
                    lambda: _open_job(self._config, query, self._browser_chrome(), bundle, working_in)
                )
            finally:
                await self._note_front_left(thread_id)
            _refuse_secrets(opened)
            _log.info("app_opened", app=opened.name, pid=opened.pid, fresh=fresh)
            state: ThreadDesktop = memory.targeting(self._thread(thread_id), opened.pid)
            if not await self._wait_for_window(opened.pid):
                self._remember(thread_id, state)
                return OpenedApp(
                    app=await self._call(partial(_app_view_job, self._config, opened)),
                    view=None,
                    note=(
                        "it is running but has no window yet; try desktop_act with the key cmd+n or the menu "
                        "File > New, or look at its menu bar with scope menu_bar"
                    ),
                )
            view, updated = await self._look(state, opened, _window_view(max_chars))
            self._remember(thread_id, updated)
            return OpenedApp(app=view.app, view=view, note=None)

    # -- acting ----------------------------------------------------------------------------------------------------

    def _open_env(
        self, state: ThreadDesktop, name: str | None, steps: Sequence[Step], secure_field: SecureFieldCheck
    ) -> StepEnv:
        """The environment of a batch (worker thread); the target comes to the front when the steps need it."""
        running: tuple[AppInfo, ...] = self._listing_for_act()
        preferred: frozenset[int] = self._browser_chrome()
        app: AppInfo = _pick_app(state, name, running, preferred)
        _refuse_secrets(app)
        env: StepEnv = StepEnv(
            app=app,
            app_element=ax.app_element(app.pid, self._config.ax_timeout_s),
            elements=memory.known_elements(memory.memory_of(state, app.pid)),
            running=running,
            displays=screen.displays(),
            scale=screen.model_scale(screen.main_display().bounds, self._config.screenshot_max_edge),
            source=hid.make_source(),
            config=self._config,
            failsafe_file=self._failsafe_file,
            expected_front=None,
            expected_pointer=None,
            preferred=preferred,
            secure_field=secure_field,
        )
        actions.check_failsafe(env)
        if _needs_front(steps, memory.known_nodes(memory.memory_of(state, app.pid))):
            apps.activate(app, self._config.activate_timeout_s)
        return replace(env, expected_front=apps.frontmost_pid())

    async def _run_step(self, env: StepEnv, step: Step, index: int, reviewer: StepReviewer) -> Effect:
        """Review a step, then perform it; a wait is only a pause."""
        if step.action == "wait":
            seconds: float = _seconds_of(step)
            await asyncio.sleep(seconds)
            return Effect(detail=f"waited {seconds:g} s", pointer=None, opens_ui=False, switched_to=None)
        typed: dict[str, str] = self._lines
        inspection: Inspection = await self._call(partial(actions.inspect_step, env, step, typed))
        actions.refuse_secrets(inspection.context)
        # No terminal line is known while the step is reviewed and runs: one that is refused, taken over or fails
        # halfway may leave any text behind.
        self._lines = {}
        asked_at: float = time.monotonic()
        await self._ask(
            reviewer,
            StepReview(
                index=index,
                step=step,
                description=describe_step(step),
                context=inspection.context,
                target=inspection.target,
            ),
        )
        # An answer that took a while may have been given with the mouse, on this Mac: the pointer is not where the step
        # before left it, and that is no one else at the mouse (item 683's check of it). The step still goes to what it
        # was reviewed for, not to whatever is there after the wait (item 555).
        if time.monotonic() - asked_at > _ANSWERED_AT_ONCE_S:
            env = replace(env, expected_pointer=None)
        effect: Effect = await self._call(partial(actions.execute_step, env, step, inspection))
        self._lines = safety.lines_after(typed, step, inspection.context)
        return effect

    async def _run_steps(
        self, thread_id: str, state: ThreadDesktop, env: StepEnv, steps: Sequence[Step], reviewer: StepReviewer
    ) -> _Batch:
        """Run the steps in order until one fails, is refused or is taken over by the user."""
        reports: list[StepReport] = []
        stopped: str | None = None
        side_effects: bool = False
        for index, step in enumerate(steps, start=1):
            description: str = describe_step(step)
            if stopped is not None:
                reports.append(StepReport(index=index, description=description, outcome="skipped", detail=""))
                continue
            try:
                effect: Effect = await self._run_step(env, step, index, reviewer)
            except ApprovalParked as error:
                # Kept for the owner (item 482): its words say that it did not happen and waits for them.
                reports.append(StepReport(index=index, description=description, outcome="failed", detail=error.message))
                stopped = "a step waits for the user"
                continue
            except (PolicyDenied, ApprovalRejected) as error:
                detail: str = f"Not allowed: {error.message}. Do not retry this step or look for a way around it."
                reports.append(StepReport(index=index, description=description, outcome="failed", detail=detail))
                stopped = "a step was not allowed"
                continue
            except HandedOff as error:
                detail = f"the user handled this step themselves ({error.message})"
                reports.append(StepReport(index=index, description=description, outcome="done", detail=detail))
                stopped = "the user took over this step; look at the screen before going on"
                continue
            except FailSafeTriggered:
                raise
            except DesktopError as error:
                reports.append(StepReport(index=index, description=description, outcome="failed", detail=str(error)))
                stopped = "a step failed"
                continue
            reports.append(StepReport(index=index, description=description, outcome="done", detail=effect.detail))
            if effect.pointer is not None and self._config.show_pointer:
                # Dot's pointer glides to where the step went and ripples there for a click (item 706).
                self._pointing.add(thread_id)
                await self._pointer.move(effect.pointer.x, effect.pointer.y, step.action in _CLICKS)
            side_effects = side_effects or step.has_side_effects()
            pointer = effect.pointer if effect.pointer is not None else env.expected_pointer
            env = replace(env, expected_pointer=pointer)
            if effect.switched_to is not None:
                state = memory.targeting(state, effect.switched_to.pid)
                env = await self._call(partial(_switch_job, self._config, env, state, effect.switched_to))
            if index < len(steps):
                pause: float = self._config.settle_ms / 3000 if effect.opens_ui else _STEP_GAP_S
                await asyncio.sleep(pause)
                remeasured: StepEnv = await self._call(partial(_remeasure, env))
                # A step that brought another application forward (a link opened in the browser, a dialog of another
                # program) leaves the next keys with it: the batch stops where the model can look (item 749). An
                # ``activate`` step chose the front itself.
                taker: str | None = None if effect.switched_to is not None else _front_taken_by(env, remeasured)
                env = remeasured
                if taker is not None:
                    stopped = f"{taker} came to the front after step {index}; look at the screen before going on"
        return _Batch(reports=tuple(reports), stopped=stopped, env=env, state=state, side_effects=side_effects)

    async def act(
        self,
        thread_id: str,
        app: str | None,
        steps: Sequence[Step],
        reviewer: StepReviewer,
        secure_field: SecureFieldCheck,
        max_chars: int,
    ) -> ActOutcome:
        """Perform a batch of steps in one application, each reviewed first, and show the window afterwards, at most
        ``max_chars`` of its text. ``secure_field`` decides what a step does at a secure text field: a step it refuses
        fails and stops the batch."""
        async with self._turn():
            await self._prepare(self._prepare_acting)
            await self._note_front_before(thread_id)
            if self._config.show_pointer:
                # Started now, the helper is ready to draw by the time the first step has gone somewhere.
                await self._pointer.warm()
            state: ThreadDesktop = self._thread(thread_id)
            env: StepEnv = await self._call(partial(self._open_env, state, app, steps, secure_field))
            _log.info("desktop_act", thread=thread_id, app=env.app.name, steps=len(steps))
            batch: _Batch = await self._run_steps(thread_id, memory.targeting(state, env.app.pid), env, steps, reviewer)
            if batch.side_effects:
                quiet_s: float = self._config.settle_quiet_ms / 1000
                await self._call(
                    partial(ax.wait_until_quiet, batch.env.app.pid, quiet_s, self._config.settle_ms / 1000)
                )
            view, note, updated = await self._closing_view(batch.state, batch.env.app, max_chars)
            self._remember(thread_id, updated)
            await self._note_front_left(thread_id)
            return ActOutcome(steps=batch.reports, stopped=batch.stopped, view=view, view_note=note)

    async def preview(self, thread_id: str, app: str | None, steps: Sequence[Step]) -> tuple[StepContext, ...]:
        """The context of each step as far as it is known before anything runs: used to judge a batch up front."""
        async with self._turn():
            await self._prepare(self._prepare_listing)
            return await self._call(partial(self._preview_in_worker, self._thread(thread_id), app, steps, self._lines))

    # -- giving the front back ------------------------------------------------------------------------------------

    async def _note_front_before(self, thread_id: str) -> None:
        if self._config.give_front_back and thread_id not in self._fronts:
            self._fronts[thread_id] = _Front(before=await self._call(_front_or_none))

    async def _note_front_left(self, thread_id: str) -> None:
        front: _Front | None = self._fronts.get(thread_id)
        if front is not None:
            front.left = await self._call(_front_or_none)

    def _give_front_job(self, front: _Front) -> str | None:
        """Bring ``front.before`` forward again when the application the run left in front still is (worker thread);
        return its name, or ``None`` when nothing was done."""
        now: int | None = _front_or_none()
        if front.before is None or front.left is None or now != front.left or now == front.before:
            return None
        previous: AppInfo | None = next((app for app in apps.list_apps() if app.pid == front.before), None)
        if previous is None:
            return None
        apps.activate(previous, self._config.activate_timeout_s)
        return previous.name

    async def end_of_run(self, thread_id: str) -> None:
        """Give the front of the Mac back once a run that acted on the desktop has ended (item 702): the application
        that was in front before its first act comes forward again, when the one its last act left in front is still
        there. Nothing is done when the owner went to another application meanwhile, when that application has quit,
        while another run's acts on the desktop are under way (it would be pulled from under them), or while a batch
        waits for the owner's answer. A failure is logged; the run has ended all the same."""
        if thread_id in self._pointing:
            self._pointing.discard(thread_id)
            if not self._pointing:
                await self._pointer.rest()
        front: _Front | None = self._fronts.pop(thread_id, None)
        if front is None or self._fronts or self._lock.locked():
            return
        try:
            async with self._turn():
                name: str | None = await self._call(partial(self._give_front_job, front))
        except DesktopError as error:
            _log.info("desktop_front_not_given_back", thread=thread_id, error=error.message)
            return
        if name is not None:
            _log.info("desktop_front_given_back", thread=thread_id, app=name)

    # -- closing ---------------------------------------------------------------------------------------------------

    def _shutdown_job(self, woken: Mapping[int, AXElement]) -> None:
        for element in woken.values():
            ax.rest_accessibility(element)
        self._release_hold()

    async def close(self) -> None:
        """Let go of everything: applications woken for accessibility rest, the wake-lock ends, the thread stops."""
        woken: dict[int, AXElement] = dict(self._woken)
        self._woken = {}
        await self._pointer.aclose()
        await self._call(partial(self._shutdown_job, woken))
        self._pool.shutdown(wait=False, cancel_futures=True)
