"""SnapShots (item 405): a double tap of the Command key, in whatever application the owner is in, captures the window
in front and hands it to the composer of the open chat as a file, with the window's text when the application shows
it to accessibility and the setting asks for it; a short sound says it was taken. The key is watched by a listen-only
event tap on a thread of its own: it reads only whether the Command key went down or up and whether any other key was
pressed meanwhile (⌘C ⌘V is not a double tap), never which. Dot's own window and a password manager's are never
captured. Settings › SnapShots switches it and its parts (``[snapshots]``)."""

import asyncio
import threading
import time
from collections.abc import Callable

import AppKit
import Quartz
from pydantic import JsonValue

from dotagent.config import DesktopConfig, SnapshotsConfig, SnapshotSound
from dotagent.core.errors import DesktopError, DotError, InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.desktop import apps, ax, capture, perms, render, safety, screen
from dotagent.desktop.types import AppInfo, AXElement, WindowInfo
from dotagent.events import SNAPSHOT_EVENT, SNAPSHOT_FAILED_EVENT, SYSTEM_RUN_ID, EventBus

log = get_logger(__name__)

# How the shortcut reads on the page.
SHORTCUT_WORDS: str = "⌘ ⌘"
# The system sounds of the choices.
SOUND_NAMES: dict[SnapshotSound, str] = {"click": "Tink", "pop": "Pop", "glass": "Glass", "off": ""}
# The most characters of a window's text that go into the file.
TEXT_CHARS: int = 12_000
# A capture is kept as sharp as the display delivers, at most this many pixels on a side.
_MAX_EDGE: int = 2000
_JPEG_QUALITY: int = 85
# How long the thread that watches the key gets to stand up.
_READY_S: float = 3.0
_STOP_S: float = 3.0
# A tap refused (the permission missing) is tried again this often, so the grant needs no restart of Dot.
_RETRY_S: float = 5.0
_SLOW: str = "the watch of the Command key did not start in time"


class SnapshotTaken(FrozenModel):
    """A capture kept: the application and the title of the window, the files it was kept as (as the composer lists
    them: ``id``, ``name``, ``kind``, ``size``), and when."""

    app: str
    title: str
    files: list[JsonValue]
    taken_at: float


class Capture(FrozenModel):
    """A capture as taken, before it is kept: the application and the title of the window, the name its files take
    before their extension, the picture as JPEG, and the window's text ("" without)."""

    app: str
    title: str
    stem: str
    image: bytes
    text: str


class TapDetector:
    """Tells a double tap of the Command key from its other uses: two presses within ``within_s`` of each other, the
    key released between them, and no other key pressed from the first press on (⌘C ⌘V is two shortcuts). Pure: it is
    told the key's changes and the clock."""

    def __init__(self, within_s: float) -> None:
        self._within_s: float = within_s
        self._first: float | None = None
        self._held: bool = False
        self._keyed: bool = False

    def command(self, down: bool, now: float) -> bool:
        """The Command key went ``down`` (or up) at ``now``; whether that completes a double tap."""
        if down:
            if self._held:
                return False
            self._held = True
            if self._first is not None and not self._keyed and now - self._first <= self._within_s:
                self._first = None
                return True
            self._first = now
            self._keyed = False
            return False
        self._held = False
        if self._keyed:
            self._first = None
        return False

    def key(self) -> None:
        """Another key was pressed: what was begun is a shortcut, not a tap."""
        self._keyed = True


def _front_window(pid: int, windows: tuple[WindowInfo, ...]) -> WindowInfo | None:
    """The window of ``pid`` in front, among ``windows`` (frontmost first): an ordinary window with a size."""
    for window in windows:
        if window.pid == pid and window.layer == 0 and window.bounds.width > 1 and window.bounds.height > 1:
            return window
    return None


def _stem(owner: str, title: str) -> str:
    """The name a capture's files take, before their extension: the application and the window's title."""
    words: str = f"{owner} - {title}" if title.strip() else owner
    return " ".join(words.split())[:80] or "window"


def window_text(desktop: DesktopConfig, pid: int, window: WindowInfo) -> str:
    """What the window of ``pid`` shows to accessibility, as the model reads a window (its elements, one a line), at
    most :data:`TEXT_CHARS`; "" when it shows nothing. Blocking: call it off the event loop."""
    perms.require_accessibility()
    element: AXElement = ax.app_element(pid, desktop.ax_timeout_s)
    roots: tuple[ax.Root, ...] = ax.roots_for(element, pid, "window")
    limits: ax.WalkLimits = ax.WalkLimits(desktop.ax_max_nodes, desktop.ax_max_depth, desktop.ax_budget_s)
    snapshot = ax.snapshot(None, roots, pid, 1, limits)
    scale: float = screen.model_scale(screen.main_display().bounds, desktop.screenshot_max_edge)
    text, _left = render.fit_lines(render.render_lines(snapshot.nodes, scale), TEXT_CHARS)
    if text.strip() == "":
        return ""
    return f"# {window.owner}: {window.title}\n\n{text}\n"


def capture_front_window(desktop: DesktopConfig, with_text: bool) -> Capture:
    """Capture the window in front: a JPEG, with its text when ``with_text`` and the application shows some. Dot's own
    window and a password manager's are refused. Blocking: call it off the event loop."""
    perms.require_screen_recording()
    windows: tuple[WindowInfo, ...] = screen.windows_on_screen()
    pid: int | None = apps.frontmost_pid()
    if pid is None:
        raise DesktopError("no application has the focus right now", {})
    window: WindowInfo | None = _front_window(pid, windows)
    if window is None:
        raise DesktopError("the application in front has no window on the screen", {"pid": pid})
    app: AppInfo | None = next((running for running in apps.list_apps() if running.pid == pid), None)
    role: safety.AppRole = safety.window_role(window.owner, None if app is None else app.bundle_id, window.title)
    if role == "dot":
        raise DesktopError("the window in front is Dot's own; switch to the window to capture", {"app": window.owner})
    if role == "secrets":
        raise DesktopError("the window in front belongs to a password manager, which is never captured", {})
    picture = capture.capture_native(window.bounds, desktop.capture_timeout_s)
    picture, _ = capture.black_out(picture, window.bounds, safety.hidden_regions(windows))
    width, height = capture.zoom_size(window.bounds, picture.size, _MAX_EDGE)
    text: str = ""
    if with_text:
        try:
            text = window_text(desktop, pid, window)
        except DesktopError as error:
            log.warning("snapshot_text_skipped", app=window.owner, error=error.message)
    return Capture(
        app=window.owner,
        title=window.title,
        stem=_stem(window.owner, window.title),
        image=capture.render_jpeg(picture, width, height, _JPEG_QUALITY),
        text=text,
    )


def play_sound(sound: SnapshotSound) -> None:
    """Play the system sound of the choice ``sound``; "off" plays nothing."""
    name: str = SOUND_NAMES[sound]
    if name == "":
        return
    found = AppKit.NSSound.soundNamed_(name)
    if found is not None:
        found.play()


class SnapshotWatcher(threading.Thread):
    """The thread that watches the Command key: a listen-only event tap of the session, fed to a
    :class:`TapDetector`; a double tap calls ``on_double`` on this thread. ``problem`` says why the tap could not be
    made (macOS has not granted Accessibility or Input Monitoring to the app that runs Dot), once ``ready`` is set."""

    def __init__(self, within_s: float, on_double: Callable[[], None]) -> None:
        super().__init__(name="snapshots", daemon=True)
        self._detector: TapDetector = TapDetector(within_s)
        self._on_double: Callable[[], None] = on_double
        self._run_loop: object | None = None
        self._tap: object | None = None
        self.ready: threading.Event = threading.Event()
        self.problem: str | None = None
        self._stopping: threading.Event = threading.Event()

    def _event(self, _proxy: object, kind: int, event: object, _refcon: object) -> object:
        if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            # macOS switches a slow tap off; a listen-only one is never slow, but it is switched back on all the same.
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        if kind == Quartz.kCGEventFlagsChanged:
            down: bool = bool(Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskCommand)
            if self._detector.command(down, time.monotonic()):
                self._on_double()
        else:
            # A key, a click or a scroll while the key is held: a shortcut (⌘C, ⌘-click), not a tap.
            self._detector.key()
        return event

    def run(self) -> None:
        mask: int = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
        )
        # Refused until the permission is granted: tried again every few seconds, so the grant takes without a
        # restart of Dot; ``ready`` is set at the first outcome either way, and ``problem`` clears once a tap is made.
        tap: object | None = None
        while tap is None and not self._stopping.is_set():
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                self._event,
                None,
            )
            if tap is None:
                self.problem = (
                    "macOS lets Dot watch the Command key only once Accessibility (or Input Monitoring) is granted to "
                    "the app that runs it: " + perms.grant_hint("accessibility")
                )
                if not self.ready.is_set():
                    log.warning("snapshots_not_listening", problem=self.problem)
                self.ready.set()
                self._stopping.wait(_RETRY_S)
        if tap is None:
            return
        self.problem = None
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.ready.set()
        log.info("snapshots_listening")
        if not self._stopping.is_set():
            Quartz.CFRunLoopRun()
        Quartz.CGEventTapEnable(tap, False)

    def stop(self) -> None:
        """End the watch and wait for the thread, briefly; a thread still standing up is stopped once its loop runs,
        one still trying for the tap at once."""
        self._stopping.set()
        self.ready.wait(_STOP_S)
        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)
        self.join(_STOP_S)


type Taker = Callable[[bool], SnapshotTaken]


class SnapshotService:
    """SnapShots as the daemon runs them: the settings as they stand (changed live from the page), the watch of the
    key while they are on, the captures taken and not yet claimed by a composer, and the event that tells the pages
    of each. :meth:`start` is given what captures the window in front and keeps it (``agent/snapshots.py``'s
    ``stored_taker``; a test gives one of its own)."""

    def __init__(self, config: SnapshotsConfig, bus: EventBus) -> None:
        self.enabled: bool = config.enabled
        self.app_text: bool = config.app_text
        self.sound: SnapshotSound = config.sound
        self.animations: bool = config.animations
        self._within_s: float = config.double_tap_ms / 1000
        self._bus: EventBus = bus
        self._taker: Taker | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watcher: SnapshotWatcher | None = None
        self._pending: list[SnapshotTaken] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._taking: asyncio.Lock = asyncio.Lock()
        self._slow: bool = False

    @property
    def listening(self) -> bool:
        """Whether the Command key is being watched right now."""
        return self._watcher is not None and self._watcher.is_alive() and self.problem is None

    @property
    def problem(self) -> str | None:
        """Why the key is not watched, as it stands now: the watcher's reason, which clears by itself once the
        permission is granted (it tries again every few seconds), or that it did not start in time; None while it
        listens or SnapShots are off."""
        watcher: SnapshotWatcher | None = self._watcher
        if watcher is None:
            return None
        if watcher.ready.is_set():
            return watcher.problem
        return _SLOW if self._slow else None

    async def start(self, taker: Taker) -> None:
        """Take ``taker`` as what captures the window in front and keeps it, and start watching the key when SnapShots
        are on; called once the event loop runs."""
        self._loop = asyncio.get_running_loop()
        self._taker = taker
        if self.enabled:
            await asyncio.to_thread(self._listen)

    def _listen(self) -> None:
        watcher: SnapshotWatcher = SnapshotWatcher(self._within_s, self._tapped)
        # The service's to stop from now, also when it is slow to stand up.
        self._watcher = watcher
        self._slow = False
        watcher.start()
        if not watcher.ready.wait(_READY_S):
            self._slow = True
            log.warning("snapshots_not_listening", problem=_SLOW)

    def _tapped(self) -> None:
        """A double tap, on the watcher's thread: the capture is taken on the event loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._begin)

    def _begin(self) -> None:
        task: asyncio.Task[None] = asyncio.create_task(self._take_quietly(), name="snapshot")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _take_quietly(self) -> None:
        """The capture a double tap asked for: no caller waits for it, so what fails is told to the pages here."""
        try:
            await self.take()
        except (DesktopError, InvalidValueError, OSError):
            return
        except Exception as error:  # the owner tapped and nothing came: they see why, and the log has the trace
            reason: str = f"{type(error).__name__}: {error}"
            log.exception("snapshot_failed", error=reason)
            await self._bus.emit(SYSTEM_RUN_ID, SNAPSHOT_FAILED_EVENT, {"reason": reason}, False)

    async def stop(self) -> None:
        """End the watch of the key, if any."""
        watcher: SnapshotWatcher | None = self._watcher
        self._watcher = None
        if watcher is not None:
            await asyncio.to_thread(watcher.stop)

    async def apply(self, key: str, value: bool | SnapshotSound) -> None:
        """A setting changed on the page: it holds from now; SnapShots switched on start the watch, off end it. A
        value that does not fit its key is refused."""
        if key == "enabled" and isinstance(value, bool):
            self.enabled = value
            if value and (self._watcher is None or not self._watcher.is_alive()):
                await asyncio.to_thread(self._listen)
            elif not value:
                await self.stop()
        elif key == "app_text" and isinstance(value, bool):
            self.app_text = value
        elif key == "animations" and isinstance(value, bool):
            self.animations = value
        elif key == "sound" and isinstance(value, str) and value in SOUND_NAMES:
            self.sound = value
        else:
            raise InvalidValueError("the setting does not take this value", {"key": key, "value": str(value)})

    async def take(self) -> SnapshotTaken:
        """Capture the window in front now: the files are kept for the composer to claim, the sound plays, and the
        pages hear of it. A capture that fails tells the pages why and raises."""
        if self._taker is None:
            raise DesktopError("SnapShots are not started", {})
        taker: Taker = self._taker
        async with self._taking:
            try:
                taken: SnapshotTaken = await asyncio.to_thread(taker, self.app_text)
            except (DesktopError, InvalidValueError, OSError) as error:
                # Refused (Dot's own window, a password manager, no window, no permission) or the files could not be
                # stored: the pages hear why, and the caller.
                reason: str = error.message if isinstance(error, DotError) else f"{type(error).__name__}: {error}"
                log.warning("snapshot_failed", error=reason)
                await self._bus.emit(SYSTEM_RUN_ID, SNAPSHOT_FAILED_EVENT, {"reason": reason}, False)
                raise
            if self.sound != "off":
                await asyncio.to_thread(play_sound, self.sound)
            self._pending.append(taken)
            log.info("snapshot_taken", app=taken.app, files=len(taken.files))
            await self._bus.emit(
                SYSTEM_RUN_ID, SNAPSHOT_EVENT, {**taken.model_dump(), "animations": self.animations}, False
            )
            return taken

    def claim(self) -> list[SnapshotTaken]:
        """The captures not yet claimed, for the composer that asks first; they are its from now."""
        claimed: list[SnapshotTaken] = list(self._pending)
        self._pending.clear()
        return claimed

    def pending(self) -> list[SnapshotTaken]:
        return list(self._pending)
