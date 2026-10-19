"""The flash and the flight of a capture of SnapShots (item 414): over the window just captured a soft white flash, then
a card with the picture that flies to Dot's window as it comes to the front, the way another coding agent shows its own
captures. The daemon has no AppKit run loop for windows of its own (its main thread runs the event loop), so a helper
process draws them (``python -m dotagent.desktop.flight``), started with SnapShots and fed one JSON line per command
(:class:`FlightCommand`): ``begin`` flashes the window's frame and lifts the card off it, ``land`` flies the card to
Dot's window, or to the corner of the screen when none came to the front. Frames are the screen's, from its top left,
as CGWindowList gives them. The helper takes no permission: it draws, and reads nothing of the screen."""

import asyncio
import math
import sys
import threading
from typing import Literal

from pydantic import ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

# The flight's length grows with the way it covers, between these, as another coding agent's does: a short hop is quick,
# a flight across the screen still short enough to follow.
FLIGHT_MIN_MS: float = 280.0
FLIGHT_MAX_MS: float = 680.0
_FLIGHT_SCALE_PX: float = 2000.0
# The flash: up to this much white over the window, then gone, in these.
FLASH_PEAK: float = 0.18
FLASH_IN_S: float = 0.07
FLASH_OUT_S: float = 0.23
# The card lands this wide in Dot's window, this far above its foot, where the box is.
CARD_WIDTH_PX: float = 180.0
CARD_FOOT_PX: float = 120.0
# The corner it flies to when no Dot window came to the front, and how small it is there.
CORNER_PX: float = 24.0
CORNER_WIDTH_PX: float = 96.0
# A card that heard of no landing lands in the corner after this; the helper's commands wait this long to be read.
LAND_WITHIN_S: float = 1.5
_STOP_S: float = 2.0


class Frame(FrozenModel):
    """A rectangle of the screen, from its top left (as CGWindowList gives a window's bounds)."""

    x: float
    y: float
    width: float
    height: float


class FlightCommand(FrozenModel):
    """One command of the helper: ``begin`` (the captured window's ``frame``, its ``picture`` and whether to ``flash``
    and to ``fly``) or ``land`` (Dot's window's ``frame``, or None for the corner of the screen)."""

    op: Literal["begin", "land"]
    frame: Frame | None
    picture: str | None
    flash: bool
    fly: bool


def flight_ms(distance: float) -> float:
    """How long a flight over ``distance`` pixels takes, in milliseconds: from :data:`FLIGHT_MIN_MS` for none to
    :data:`FLIGHT_MAX_MS` for a long way."""
    return max(
        FLIGHT_MIN_MS, min(FLIGHT_MAX_MS, FLIGHT_MAX_MS - 400.0 * math.exp(-max(0.0, distance) / _FLIGHT_SCALE_PX))
    )


def landing(target: Frame, aspect: float) -> Frame:
    """Where the card lands in Dot's window ``target``: :data:`CARD_WIDTH_PX` wide (a third of a narrow window), in the
    picture's proportions (``aspect``: its height over its width), centred over the foot of the window, where the box
    is."""
    width: float = min(CARD_WIDTH_PX, target.width / 3)
    height: float = width * aspect
    return Frame(
        x=target.x + (target.width - width) / 2,
        y=max(target.y, target.y + target.height - CARD_FOOT_PX - height),
        width=width,
        height=height,
    )


def corner(screen: Frame, aspect: float) -> Frame:
    """Where the card goes when no Dot window came to the front: small, at the bottom right corner of ``screen``."""
    width: float = CORNER_WIDTH_PX
    height: float = width * aspect
    return Frame(
        x=screen.x + screen.width - width - CORNER_PX,
        y=screen.y + screen.height - height - CORNER_PX,
        width=width,
        height=height,
    )


def distance(start: Frame, end: Frame) -> float:
    """How far the centre of ``end`` is from the centre of ``start``."""
    return math.hypot(
        (end.x + end.width / 2) - (start.x + start.width / 2), (end.y + end.height / 2) - (start.y + start.height / 2)
    )


def to_appkit(frame: Frame, primary_height: float) -> Frame:
    """``frame`` (from the top left of the main screen) as AppKit places windows: from the bottom left of the main
    screen, ``primary_height`` tall."""
    return Frame(x=frame.x, y=primary_height - frame.y - frame.height, width=frame.width, height=frame.height)


def read_command(line: str) -> FlightCommand | None:
    """The command a line of the helper's input holds, None for a line that holds none (logged)."""
    try:
        return FlightCommand.model_validate_json(line)
    except ValidationError as error:
        log.warning("flight_command_unread", error=str(error))
        return None


class FlightOverlay:
    """The daemon's end of the helper (a connector): starts it when first needed, or with :meth:`warm`, sends it the
    commands, and ends it. A helper that cannot start, or that died, only costs the animation: the capture is kept
    all the same, and the log says why."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._sending: asyncio.Lock = asyncio.Lock()

    async def _running(self) -> asyncio.subprocess.Process:
        if self._process is None or self._process.returncode is not None:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "dotagent.desktop.flight", stdin=asyncio.subprocess.PIPE
            )
        return self._process

    async def warm(self) -> None:
        """Start the helper now, so that the first capture's flash is not late by its start."""
        try:
            async with self._sending:
                await self._running()
        except OSError as error:
            log.warning("flight_not_started", error=str(error))

    async def send(self, command: FlightCommand) -> None:
        """Hand ``command`` to the helper, which draws it at once."""
        try:
            async with self._sending:
                process: asyncio.subprocess.Process = await self._running()
                if process.stdin is None:
                    raise OSError("the helper has no input")
                process.stdin.write((command.model_dump_json() + "\n").encode("utf-8"))
                await process.stdin.drain()
        except (OSError, ConnectionError) as error:
            log.warning("flight_not_drawn", op=command.op, error=str(error))
            self._process = None

    async def aclose(self) -> None:
        """End the helper: its input closes, and it stops once it drew what it had."""
        process: asyncio.subprocess.Process | None = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), _STOP_S)
        except TimeoutError:
            process.kill()
            await process.wait()


class _Stage:
    """The helper's windows (on its main thread): the flash and the card of the capture under way, kept until they
    close, since a window nothing holds would go at once."""

    def __init__(self) -> None:
        self._windows: list[object] = []
        self._card: object | None = None
        self._source: Frame | None = None
        self._aspect: float = 0.625
        self._flights: int = 0

    def handle(self, command: FlightCommand) -> None:
        if command.op == "begin" and command.frame is not None:
            self._begin(command, command.frame)
        elif command.op == "land":
            self._land(command.frame)

    def _window(self, frame: Frame) -> object:
        import AppKit

        primary: float = float(AppKit.NSScreen.screens()[0].frame().size.height)
        placed: Frame = to_appkit(frame, primary)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(placed.x, placed.y, placed.width, placed.height),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSScreenSaverWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setHasShadow_(False)
        window.setReleasedWhenClosed_(False)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )
        self._windows.append(window)
        return window

    def _close(self, window: object) -> None:
        window.orderOut_(None)  # type: ignore[attr-defined]
        window.close()  # type: ignore[attr-defined]
        if window in self._windows:
            self._windows.remove(window)

    def _animate(self, seconds: float, change: object, then: object) -> None:
        import AppKit
        import Quartz

        def group(context: object) -> None:
            context.setDuration_(seconds)  # type: ignore[attr-defined]
            context.setTimingFunction_(  # type: ignore[attr-defined]
                Quartz.CAMediaTimingFunction.functionWithControlPoints____(0.2, 0.8, 0.2, 1.0)
            )
            change()  # type: ignore[operator]

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(group, then)

    def _begin(self, command: FlightCommand, frame: Frame) -> None:
        import AppKit

        if command.flash:
            flash = self._window(frame)
            flash.setBackgroundColor_(AppKit.NSColor.whiteColor())  # type: ignore[attr-defined]
            flash.setAlphaValue_(0.0)  # type: ignore[attr-defined]
            flash.orderFrontRegardless()  # type: ignore[attr-defined]

            def fade() -> None:
                self._animate(FLASH_OUT_S, lambda: flash.animator().setAlphaValue_(0.0), lambda: self._close(flash))  # type: ignore[attr-defined]

            self._animate(FLASH_IN_S, lambda: flash.animator().setAlphaValue_(FLASH_PEAK), fade)  # type: ignore[attr-defined]
        if not command.fly or command.picture is None:
            return
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(command.picture)
        if image is None:
            log.warning("flight_picture_unread", picture=command.picture)
            return
        if self._card is not None:
            self._land(None)
        size = image.size()
        self._aspect = float(size.height) / float(size.width) if size.width > 0 else self._aspect
        card = self._window(frame)
        view = AppKit.NSImageView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, frame.width, frame.height))
        view.setImage_(image)
        view.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(12.0)
        view.layer().setMasksToBounds_(True)
        card.setContentView_(view)  # type: ignore[attr-defined]
        card.setHasShadow_(True)  # type: ignore[attr-defined]
        card.orderFrontRegardless()  # type: ignore[attr-defined]
        self._card = card
        self._source = frame
        self._flights += 1
        flight: int = self._flights
        from PyObjCTools import AppHelper

        AppHelper.callLater(LAND_WITHIN_S, self._land_late, flight)

    def _land_late(self, flight: int) -> None:
        if flight == self._flights and self._card is not None:
            self._land(None)

    def _land(self, target: Frame | None) -> None:
        import AppKit

        card = self._card
        source: Frame | None = self._source
        if card is None or source is None:
            return
        self._card = None
        screens = AppKit.NSScreen.screens()
        primary: float = float(screens[0].frame().size.height)
        main = screens[0].frame()
        goal: Frame = (
            landing(target, self._aspect)
            if target is not None
            else corner(Frame(x=0.0, y=0.0, width=float(main.size.width), height=primary), self._aspect)
        )
        placed: Frame = to_appkit(goal, primary)
        seconds: float = flight_ms(distance(source, goal)) / 1000

        def done() -> None:
            self._animate(0.12, lambda: card.animator().setAlphaValue_(0.0), lambda: self._close(card))  # type: ignore[attr-defined]

        self._animate(
            seconds,
            lambda: card.animator().setFrame_display_(  # type: ignore[attr-defined]
                AppKit.NSMakeRect(placed.x, placed.y, placed.width, placed.height), True
            ),
            done,
        )


def main() -> None:
    """The helper: an application without a Dock icon that draws what it reads on its input, a command a line, and
    ends when its input closes."""
    import AppKit
    from PyObjCTools import AppHelper

    application = AppKit.NSApplication.sharedApplication()
    application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    stage: _Stage = _Stage()

    def read() -> None:
        for line in sys.stdin:
            command: FlightCommand | None = read_command(line)
            if command is not None:
                AppHelper.callAfter(stage.handle, command)
        AppHelper.callAfter(AppHelper.stopEventLoop)

    threading.Thread(target=read, name="flight-commands", daemon=True).start()
    AppHelper.runEventLoop(installInterrupt=False)


if __name__ == "__main__":
    main()
