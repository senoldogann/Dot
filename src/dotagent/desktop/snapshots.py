"""SnapShots (item 405): both Command keys pressed together (item 414), or the Command key tapped twice, in whatever
application the owner is in, captures the window in front and hands it to the composer of the open chat as a file,
with the window's text when the application shows it to accessibility and the setting asks for it; a short sound says
it was taken, a soft flash over the window and a card that flies to Dot's window show it
(:mod:`dotagent.desktop.flight`), and Dot's window or tab comes to the front with the capture in its box
(:mod:`dotagent.desktop.show_dot`). The keys are watched by a listen-only event tap on a thread of its own: it reads
only whether a Command key went down or up and whether any other key was pressed meanwhile (⌘C ⌘V is not a capture),
never which. Dot's own window and a password manager's are never captured. Settings › SnapShots switches it and its
parts (``[snapshots]``)."""

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Protocol

import AppKit
import Quartz
from pydantic import JsonValue

from dotagent.config import DesktopConfig, SnapshotsConfig, SnapshotSound
from dotagent.core.errors import DesktopError, DotError, InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.desktop import apps, ax, capture, perms, render, safety, screen
from dotagent.desktop.flight import FlightCommand, Frame
from dotagent.desktop.show_dot import Shower
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
# The left and the right Command keys in an event's flags (NX_DEVICELCMDKEYMASK, NX_DEVICERCMDKEYMASK): both down
# together is the shortcut another coding agent gives its own captures (item 414).
_LEFT_COMMAND: int = 0x08
_RIGHT_COMMAND: int = 0x10
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
    before their extension, the picture as JPEG, the window's text ("" without) and where the window is on the screen
    (for the flash and the flight, item 414)."""

    app: str
    title: str
    stem: str
    image: bytes
    text: str
    frame: Frame


class Kept(FrozenModel):
    """A capture kept: what the composer is handed (``snapshot``), where the window was (``frame``) and the picture's
    file (``picture``), which the card of the flight shows."""

    snapshot: SnapshotTaken
    frame: Frame
    picture: str


class TapDetector:
    """Tells a double tap of the Command key from its other uses: two presses within ``within_s`` of each other, the
    key released between them, and no other key pressed from the first press on (⌘C ⌘V is two shortcuts). Pure: it is
    told the key's changes and the clock."""

    def __init__(self, within_s: float) -> None:
        self._within_s: float = within_s
        self._first: float | None = None
        self._held: bool = False
        self._keyed: bool = False
        self._both: bool = False
        # A double tap just captured while the key is still held: the other key pressed now is the same gesture.
        self._captured: bool = False

    def command(self, down: bool, now: float) -> bool:
        """The Command key went ``down`` (or up) at ``now``; whether that completes a double tap."""
        if down:
            if self._held:
                return False
            self._held = True
            if self._first is not None and not self._keyed and now - self._first <= self._within_s:
                self._first = None
                self._captured = True
                return True
            self._first = now
            self._keyed = False
            return False
        self._held = False
        self._captured = False
        if self._keyed:
            self._first = None
        return False

    def key(self) -> None:
        """Another key was pressed: what was begun is a shortcut, not a tap."""
        self._keyed = True

    def sides(self, left: bool, right: bool) -> bool:
        """The left and the right Command keys are down (or up) after a change: whether both just went down together,
        with no other key pressed since the first did (item 414). A double tap begun is let go, so it does not
        capture again."""
        both: bool = left and right
        fired: bool = both and not self._both and not self._keyed and not self._captured
        self._both = both
        if fired:
            self._first = None
        return fired


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
    running: tuple[AppInfo, ...] = apps.list_apps()
    app: AppInfo | None = next((candidate for candidate in running if candidate.pid == pid), None)
    role: safety.AppRole = safety.window_role(window.owner, None if app is None else app.bundle_id, window.title)
    if role == "dot":
        raise DesktopError("the window in front is Dot's own; switch to the window to capture", {"app": window.owner})
    if role == "secrets":
        raise DesktopError("the window in front belongs to a password manager, which is never captured", {})
    picture = capture.capture_native(window.bounds, desktop.capture_timeout_s)
    bundles: dict[int, str | None] = {candidate.pid: candidate.bundle_id for candidate in running}
    picture, _ = capture.black_out(picture, window.bounds, safety.hidden_regions(windows, bundles))
    width, height = capture.zoom_size(window.bounds, picture.size, _MAX_EDGE)
    text: str = ""
    if with_text:
        try:
            text = window_text(desktop, pid, window)
        except DesktopError as error:
            log.warning("snapshot_text_skipped", app=window.owner, error=error.message)
    bounds = window.bounds
    return Capture(
        app=window.owner,
        title=window.title,
        stem=_stem(window.owner, window.title),
        image=capture.render_jpeg(picture, width, height, _JPEG_QUALITY),
        text=text,
        frame=Frame(x=bounds.x, y=bounds.y, width=bounds.width, height=bounds.height),
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
            flags: int = int(Quartz.CGEventGetFlags(event))
            tapped: bool = self._detector.command(bool(flags & Quartz.kCGEventFlagMaskCommand), time.monotonic())
            paired: bool = self._detector.sides(bool(flags & _LEFT_COMMAND), bool(flags & _RIGHT_COMMAND))
            if tapped or paired:
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


type Taker = Callable[[bool], Kept]


class Overlay(Protocol):
    """What draws the flash and the flight (:class:`dotagent.desktop.flight.FlightOverlay`; a test gives its own)."""

    async def warm(self) -> None: ...

    async def send(self, command: FlightCommand) -> None: ...

    async def aclose(self) -> None: ...


class SnapshotService:
    """SnapShots as the daemon runs them: the settings as they stand (changed live from the page), the watch of the
    key while they are on, the captures taken and not yet claimed by a composer, and the event that tells the pages
    of each. :meth:`start` is given what captures the window in front and keeps it (``agent/snapshots.py``'s
    ``stored_taker``), what brings Dot's window to the front (``show_dot``'s ``dot_shower``) and what draws the flash
    and the flight (``FlightOverlay``); a test gives its own of each."""

    def __init__(self, config: SnapshotsConfig, bus: EventBus) -> None:
        self.enabled: bool = config.enabled
        self.app_text: bool = config.app_text
        self.sound: SnapshotSound = config.sound
        self.animations: bool = config.animations
        self.flash: bool = config.flash
        self.open_dot: bool = config.open_dot
        # The User-Agent of the page that last claimed captures: Dot's tab is looked for in its browser first.
        self.agent: str | None = None
        self._within_s: float = config.double_tap_ms / 1000
        self._bus: EventBus = bus
        self._taker: Taker | None = None
        self._shower: Shower | None = None
        self._overlay: Overlay | None = None
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

    async def start(self, taker: Taker, shower: Shower, overlay: Overlay) -> None:
        """Take ``taker`` as what captures the window in front and keeps it, ``shower`` as what brings Dot's window to
        the front and ``overlay`` as what draws the flash and the flight, and start watching the keys when SnapShots
        are on (the overlay's helper with them, so the first flash is not late); called once the event loop runs."""
        self._loop = asyncio.get_running_loop()
        self._taker = taker
        self._shower = shower
        self._overlay = overlay
        if self.enabled:
            await asyncio.to_thread(self._listen)
            if self.flash or self.animations:
                await overlay.warm()

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
        """End the watch of the keys, if any, and the overlay's helper."""
        watcher: SnapshotWatcher | None = self._watcher
        self._watcher = None
        if watcher is not None:
            await asyncio.to_thread(watcher.stop)
        if self._overlay is not None:
            await self._overlay.aclose()

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
        elif key == "flash" and isinstance(value, bool):
            self.flash = value
        elif key == "open_dot" and isinstance(value, bool):
            self.open_dot = value
        elif key == "sound" and isinstance(value, str) and value in SOUND_NAMES:
            self.sound = value
        else:
            raise InvalidValueError("the setting does not take this value", {"key": key, "value": str(value)})

    async def take(self) -> SnapshotTaken:
        """Capture the window in front now: the files are kept for the composer to claim, the sound plays, the window
        flashes and its card lifts off it, the pages hear of it, and Dot's window comes to the front, where the card
        lands (item 414). A capture that fails tells the pages why and raises; once it is kept, what brings Dot forward
        cannot undo it (:meth:`_show`)."""
        if self._taker is None or self._shower is None or self._overlay is None:
            raise DesktopError("SnapShots are not started", {})
        taker: Taker = self._taker
        overlay: Overlay = self._overlay
        async with self._taking:
            try:
                kept: Kept = await asyncio.to_thread(taker, self.app_text)
            except (DesktopError, InvalidValueError, OSError) as error:
                # Refused (Dot's own window, a password manager, no window, no permission) or the files could not be
                # stored: the pages hear why, and the caller.
                reason: str = error.message if isinstance(error, DotError) else f"{type(error).__name__}: {error}"
                log.warning("snapshot_failed", error=reason)
                await self._bus.emit(SYSTEM_RUN_ID, SNAPSHOT_FAILED_EVENT, {"reason": reason}, False)
                raise
            taken: SnapshotTaken = kept.snapshot
            if self.sound != "off":
                await asyncio.to_thread(play_sound, self.sound)
            if self.flash or self.animations:
                begin: FlightCommand = FlightCommand(
                    op="begin", frame=kept.frame, picture=kept.picture, flash=self.flash, fly=self.animations
                )
                await overlay.send(begin)
            self._pending.append(taken)
            log.info("snapshot_taken", app=taken.app, files=len(taken.files))
            await self._bus.emit(
                SYSTEM_RUN_ID,
                SNAPSHOT_EVENT,
                {**taken.model_dump(), "animations": self.animations, "open": self.open_dot},
                False,
            )
        # Out of the lock: macOS may ask whether Dot may drive the browser, which the next capture does not wait for.
        await self._show(overlay)
        return taken

    async def _show(self, overlay: Overlay) -> None:
        """Bring Dot's window to the front for the capture just kept, and land its card there (or in the corner of the
        screen). The capture is in the box whatever happens here: what keeps Dot from the front is logged, not told
        as a failed capture."""
        target: Frame | None = None
        if self.open_dot and self._shower is not None:
            try:
                target = await asyncio.to_thread(self._shower, self.agent)
            except (DesktopError, OSError) as error:
                reason: str = error.message if isinstance(error, DotError) else f"{type(error).__name__}: {error}"
                log.warning("snapshot_dot_not_shown", error=reason)
        if self.animations:
            await overlay.send(FlightCommand(op="land", frame=target, picture=None, flash=False, fly=True))

    def claim(self, agent: str | None) -> list[SnapshotTaken]:
        """The captures not yet claimed, for the composer that asks first, whose page runs in the browser ``agent``
        (its User-Agent: Dot's tab is looked for there first); they are its from now."""
        self.agent = agent
        claimed: list[SnapshotTaken] = list(self._pending)
        self._pending.clear()
        return claimed

    def pending(self) -> list[SnapshotTaken]:
        return list(self._pending)
