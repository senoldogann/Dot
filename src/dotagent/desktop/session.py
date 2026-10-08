"""The desktop session: the single owner of everything that touches the screen.

Every accessibility, CGEvent and Cocoa call runs on one worker thread, one operation at a time. The session remembers,
per conversation thread, which application the thread works in, the elements it was given refs for and what it was shown
of each view. A ref therefore means the same element for as long as the element lives, and a repeated look at a view
shows only what changed.
"""

import asyncio
import subprocess
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from dotagent.config import DesktopConfig
from dotagent.core.errors import (
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
from dotagent.desktop.actions import Effect, Inspection, StepEnv
from dotagent.desktop.memory import AppMemory, ThreadDesktop
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

_log = get_logger("dotagent.desktop.session")

_THREAD_CAP: int = 32
_WAKE_THRESHOLD: int = 8
_WAKE_WAIT_S: float = 4.0
_WAKE_POLL_S: float = 0.15
_STEP_GAP_S: float = 0.06
_WINDOW_WAIT_S: float = 3.0
_WINDOW_POLL_S: float = 0.1
_AWAKE_MARGIN_S: float = 120.0
_DISPLAY_WAKE_S: float = 0.6
_IDLE_HOLD_S: int = 5
_REAP_TIMEOUT_S: float = 2.0
_CAFFEINATE: str = "/usr/bin/caffeinate"
_FRONT_ACTIONS: frozenset[str] = frozenset({"click", "double_click", "right_click", "type", "paste", "key", "drag"})
_WINDOW_VIEW: ViewRequest = ViewRequest(scope="window", ref=None, query=None, full=False)


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


def _pick_app(state: ThreadDesktop, name: str | None, running: Sequence[AppInfo]) -> AppInfo:
    """The application a call is about: the one named, else the one the thread works in, else the one in front."""
    if name is not None:
        return apps.require_app(name, running)
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


def _open_job(config: DesktopConfig, query: str) -> tuple[AppInfo, bool]:
    """Open an application (worker thread): the application and whether this call started it. An application that
    runs without a window is asked to reopen one, as a click on its Dock icon would."""
    running: AppInfo | None = apps.find_app(query, apps.list_apps())
    if running is None:
        return apps.open_app(query, config.launch_timeout_s), True
    if not views.has_window(running.pid, screen.windows_everywhere()):
        apps.launch(running.bundle_id or running.bundle_path or running.name)
    apps.activate(running, config.activate_timeout_s)
    return running, False


def _context_of(app: AppInfo, node: Node | None) -> StepContext:
    return StepContext(
        role=safety.app_role(app.name, app.bundle_id),
        app=app.name,
        element=None if node is None else Element(role=node.role, name=node.label),
        secure=node is not None and node.secure(),
    )


def _node_for(known: Mapping[int, Node], raw_ref: str | None) -> Node | None:
    if raw_ref is None:
        return None
    try:
        number: int = parse_ref(raw_ref)
    except ValueError as error:
        raise DesktopError(str(error), {"ref": raw_ref}) from error
    return known.get(number)


def _preview_job(state: ThreadDesktop, name: str | None, steps: Sequence[Step]) -> tuple[StepContext, ...]:
    """The context of each step as far as it can be told without touching the screen (worker thread)."""
    running: tuple[AppInfo, ...] = apps.list_apps()
    current: AppInfo = _pick_app(state, name, running)
    known: Mapping[int, Node] = memory.known_nodes(memory.memory_of(state, current.pid))
    contexts: list[StepContext] = []
    for step in steps:
        if step.action == "activate":
            named: AppInfo | None = apps.find_app(step.text or "", running)
            if named is not None:
                current = named
                known = memory.known_nodes(memory.memory_of(state, current.pid))
            contexts.append(_context_of(current, None))
        else:
            contexts.append(_context_of(current, _node_for(known, step.ref)))
    return tuple(contexts)


def _needs_front(steps: Sequence[Step]) -> bool:
    """Whether the steps, up to the first one that chooses the application, need the target in front."""
    for step in steps:
        if step.action == "activate":
            return False
        if step.action in _FRONT_ACTIONS:
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


class DesktopSession:
    """Looks at and controls the Mac's desktop for the conversation threads of one run."""

    def __init__(self, config: DesktopConfig, failsafe_file: Path) -> None:
        self._config: DesktopConfig = config
        self._failsafe_file: Path = failsafe_file
        self._pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dot-desktop")
        self._lock: asyncio.Lock = asyncio.Lock()
        self._threads: OrderedDict[str, ThreadDesktop] = OrderedDict()
        self._woken: dict[int, AXElement] = {}
        self._wake_tried: set[int] = set()
        self._hold: _Hold | None = None

    async def _call[T](self, work: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._pool, work)

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
        perms.require_accessibility(perms.check_permissions())

    def _prepare_looking(self) -> None:
        perms.require_accessibility(perms.check_permissions())
        self._check_screen()

    def _prepare_acting(self) -> None:
        perms.require_accessibility(perms.check_permissions())
        actions.check_stop(self._config, self._failsafe_file)
        self._check_screen()

    def _prepare_screenshot(self) -> None:
        perms.require_screen_recording(perms.check_permissions())
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
                    "this application shows almost nothing to accessibility; "
                    "use desktop_screenshot to see it and coordinates to act"
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
            self._config.snapshot_max_chars,
            tuple(notes),
        )
        return result.view, memory.after_walk(state, app.pid, walk.snapshot, key, result.baseline)

    async def _closing_view(
        self, state: ThreadDesktop, app: AppInfo
    ) -> tuple[SnapshotView | None, str | None, ThreadDesktop]:
        """The window after a batch, or the reason there is none."""
        try:
            view, updated = await self._look(state, app, _WINDOW_VIEW)
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
        async with self._lock:
            await self._prepare(self._prepare_listing)
            return await self._call(partial(_list_job, self._config))

    async def snapshot(self, thread_id: str, app: str | None, request: ViewRequest) -> SnapshotView:
        async with self._lock:
            await self._prepare(self._prepare_looking)
            state: ThreadDesktop = self._thread(thread_id)
            running: tuple[AppInfo, ...] = await self._call(apps.list_apps)
            target: AppInfo = _pick_app(state, app, running)
            view, updated = await self._look(memory.targeting(state, target.pid), target, request)
            self._remember(thread_id, updated)
            return view

    async def screenshot(self) -> ScreenshotView:
        async with self._lock:
            await self._prepare(self._prepare_screenshot)
            return await self._call(partial(shots.take_screen, self._config))

    async def zoom(self, region: Rect) -> ScreenshotView:
        """A screenshot of ``region`` (model units) at the display's own sharpness."""
        async with self._lock:
            await self._prepare(self._prepare_screenshot)
            return await self._call(partial(shots.take_region, self._config, region))

    async def read(self, thread_id: str, app: str | None, ref: int, offset: int, max_chars: int) -> TextRead:
        """The text of one element, ``max_chars`` of it from ``offset``."""
        async with self._lock:
            await self._prepare(self._prepare_looking)
            state: ThreadDesktop = self._thread(thread_id)
            running: tuple[AppInfo, ...] = await self._call(apps.list_apps)
            target: AppInfo = _pick_app(state, app, running)
            _refuse_secrets(target)
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
        async with self._lock:
            return await self._call(hid.read_clipboard)

    async def write_clipboard(self, text: str) -> None:
        async with self._lock:
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

    async def open_app(self, thread_id: str, query: str) -> OpenedApp:
        """Open (or bring forward) an application, make it the thread's target and show its window."""
        async with self._lock:
            await self._prepare(self._prepare_acting)
            opened, fresh = await self._call(partial(_open_job, self._config, query))
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
            view, updated = await self._look(state, opened, _WINDOW_VIEW)
            self._remember(thread_id, updated)
            return OpenedApp(app=view.app, view=view, note=None)

    # -- acting ----------------------------------------------------------------------------------------------------

    def _open_env(self, state: ThreadDesktop, name: str | None, steps: Sequence[Step]) -> StepEnv:
        """The environment of a batch (worker thread); the target comes to the front when the steps need it."""
        running: tuple[AppInfo, ...] = apps.list_apps()
        app: AppInfo = _pick_app(state, name, running)
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
        )
        actions.check_failsafe(env)
        if _needs_front(steps):
            apps.activate(app, self._config.activate_timeout_s)
        return replace(env, expected_front=apps.frontmost_pid())

    async def _run_step(self, env: StepEnv, step: Step, index: int, reviewer: StepReviewer) -> Effect:
        """Review a step, then perform it; a wait is only a pause."""
        if step.action == "wait":
            seconds: float = _seconds_of(step)
            await asyncio.sleep(seconds)
            return Effect(detail=f"waited {seconds:g} s", pointer=None, opens_ui=False, switched_to=None)
        inspection: Inspection = await self._call(partial(actions.inspect_step, env, step))
        await reviewer(
            StepReview(
                index=index,
                step=step,
                description=describe_step(step),
                context=inspection.context,
                target=inspection.target,
            )
        )
        return await self._call(partial(actions.execute_step, env, step))

    async def _run_steps(
        self, state: ThreadDesktop, env: StepEnv, steps: Sequence[Step], reviewer: StepReviewer
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
            side_effects = side_effects or step.has_side_effects()
            pointer = effect.pointer if effect.pointer is not None else env.expected_pointer
            env = replace(env, expected_pointer=pointer)
            if effect.switched_to is not None:
                state = memory.targeting(state, effect.switched_to.pid)
                env = await self._call(partial(_switch_job, self._config, env, state, effect.switched_to))
            if index < len(steps):
                pause: float = self._config.settle_ms / 3000 if effect.opens_ui else _STEP_GAP_S
                await asyncio.sleep(pause)
                env = await self._call(partial(_remeasure, env))
        return _Batch(reports=tuple(reports), stopped=stopped, env=env, state=state, side_effects=side_effects)

    async def act(self, thread_id: str, app: str | None, steps: Sequence[Step], reviewer: StepReviewer) -> ActOutcome:
        """Perform a batch of steps in one application, each reviewed first, and show the window afterwards."""
        async with self._lock:
            await self._prepare(self._prepare_acting)
            state: ThreadDesktop = self._thread(thread_id)
            env: StepEnv = await self._call(partial(self._open_env, state, app, steps))
            _log.info("desktop_act", thread=thread_id, app=env.app.name, steps=len(steps))
            batch: _Batch = await self._run_steps(memory.targeting(state, env.app.pid), env, steps, reviewer)
            if batch.side_effects:
                await asyncio.sleep(self._config.settle_ms / 1000)
            view, note, updated = await self._closing_view(batch.state, batch.env.app)
            self._remember(thread_id, updated)
            return ActOutcome(steps=batch.reports, stopped=batch.stopped, view=view, view_note=note)

    async def preview(self, thread_id: str, app: str | None, steps: Sequence[Step]) -> tuple[StepContext, ...]:
        """The context of each step as far as it is known before anything runs: used to judge a batch up front."""
        async with self._lock:
            await self._prepare(self._prepare_listing)
            return await self._call(partial(_preview_job, self._thread(thread_id), app, steps))

    # -- closing ---------------------------------------------------------------------------------------------------

    def _shutdown_job(self, woken: Mapping[int, AXElement]) -> None:
        for element in woken.values():
            ax.rest_accessibility(element)
        self._release_hold()

    async def close(self) -> None:
        """Let go of everything: applications woken for accessibility rest, the wake-lock ends, the thread stops."""
        woken: dict[int, AXElement] = dict(self._woken)
        self._woken = {}
        await self._call(partial(self._shutdown_job, woken))
        self._pool.shutdown(wait=False, cancel_futures=True)
