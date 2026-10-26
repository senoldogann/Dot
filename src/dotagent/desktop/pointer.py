"""Dot's own pointer on the screen (item 705): a glowing arrow that glides to where a step of ``desktop_act`` went
and ripples where it clicked, the way another coding agent shows its own, so that the owner sees what Dot does and
where.
It only shows: the step itself is done as before, through the HID event tap, which moves the Mac's own pointer.

The daemon has no AppKit run loop for windows of its own, so a helper process draws it (``python -m
dotagent.desktop.pointer``), as the flight of SnapShots is drawn (:mod:`dotagent.desktop.flight`), started at the first
step that needs it and fed one JSON line per command (:class:`PointerCommand`): ``move`` glides to a point and, with
``click``, ripples there; ``hide`` takes it off the screen at once and answers ``hidden <n>`` on its output, so that a
screenshot the model is shown never holds it; ``reveal`` puts it back; ``rest`` fades it out. Points are the screen's,
from the top left of the main display, as the steps' events take them. The helper takes no permission: it draws, and
reads nothing of the screen. A helper that cannot start, or that died, only costs the pointer: the steps go on.
"""

import asyncio
import contextlib
import math
import sys
import threading
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

# The window the pointer is drawn in, and where in it the tip is: room around the arrow for its glow and its ripple.
SIZE_PX: float = 72.0
TIP_PX: float = 20.0
# A glide over a short way is quick, one across the screen still short enough to follow; a step does not wait for it.
GLIDE_MIN_S: float = 0.16
GLIDE_MAX_S: float = 0.45
_GLIDE_SCALE_PX: float = 1600.0
# How long the pointer stays after its last command before it fades, and how long the fade takes.
IDLE_S: float = 2.5
FADE_S: float = 0.4
RIPPLE_S: float = 0.38
# How long the daemon waits for the helper to say it hid the pointer (2 ms once it runs; its start takes about a
# second), and for it to stop. A helper that does not answer in time is ended, and no pointer is drawn from then on.
HIDE_WITHIN_S: float = 2.0
_STOP_S: float = 2.0
# Dot's pointer: a violet arrow with a white edge and a glow of its own colour.
COLOUR: tuple[float, float, float] = (0.49, 0.36, 1.0)
HIDDEN: str = "hidden"


class PointerCommand(FrozenModel):
    """One command of the helper: ``move`` to the point (``x``, ``y``), rippling there when ``click``; ``hide``,
    ``reveal`` or ``rest``."""

    op: Literal["move", "hide", "reveal", "rest"]
    x: float = 0.0
    y: float = 0.0
    click: bool = False
    # The number of a ``hide``, which the helper's answer repeats: an answer that came too late for an earlier one is
    # not taken for this one's.
    number: int = 0


def glide_s(distance: float) -> float:
    """How long a glide over ``distance`` points takes: from :data:`GLIDE_MIN_S` for none to :data:`GLIDE_MAX_S` for a
    long way."""
    return max(GLIDE_MIN_S, min(GLIDE_MAX_S, GLIDE_MAX_S - 0.3 * math.exp(-max(0.0, distance) / _GLIDE_SCALE_PX)))


def window_origin(x: float, y: float, primary_height: float) -> tuple[float, float]:
    """Where AppKit puts the pointer's window (its bottom left, from the bottom left of the main screen) so that the
    tip of the arrow is at (``x``, ``y``), a point from the top left of the main screen."""
    return x - TIP_PX, primary_height - y - (SIZE_PX - TIP_PX)


def arrow(scale: float) -> tuple[tuple[float, float], ...]:
    """The outline of the arrow, from its tip, in points from the top left of the arrow (y down), ``scale`` times the
    size of the Mac's own."""
    shape: tuple[tuple[float, float], ...] = (
        (0.0, 0.0),
        (0.0, 17.0),
        (4.2, 13.2),
        (7.0, 19.6),
        (9.8, 18.4),
        (7.0, 12.0),
        (12.6, 12.0),
    )
    return tuple((x * scale, y * scale) for x, y in shape)


def hidden_line(number: int) -> str:
    """The helper's answer to the ``hide`` ``number``."""
    return f"{HIDDEN} {number}"


def read_command(line: str) -> PointerCommand | None:
    """The command a line of the helper's input holds, None for a line that holds none (logged)."""
    try:
        return PointerCommand.model_validate_json(line)
    except ValidationError as error:
        log.warning("pointer_command_unread", error=str(error))
        return None


class Fades:
    """When the pointer fades (on the helper's main thread): :data:`IDLE_S` after its last move, which a hide and a
    reveal around a capture do not put off or call off, and at once when a run rests it. Each move, and each reveal of
    a pointer that shows, sets a fade off for the move it came after; one whose move is no longer the last is
    dropped."""

    def __init__(self) -> None:
        self.moves: int = 0
        self.shown: bool = False

    def moved(self) -> int:
        """A move: the pointer shows; the fade to set off is for this move."""
        self.moves += 1
        self.shown = True
        return self.moves

    def revealed(self) -> int | None:
        """A reveal after a capture: the fade to set off again, for the last move, when the pointer shows."""
        return self.moves if self.shown else None

    def due(self, move: int) -> bool:
        """Whether the fade set off for ``move`` is to happen now; once it is, the pointer no longer shows."""
        if move != self.moves or not self.shown:
            return False
        self.shown = False
        return True


class PointerOverlay:
    """The daemon's end of the helper: starts it at the first move, sends it the commands, and ends it. A helper that
    cannot start or died is logged and started again at the next move; nothing it does fails a step."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled: bool = enabled
        self._process: asyncio.subprocess.Process | None = None
        self._sending: asyncio.Lock = asyncio.Lock()
        self._hides: int = 0
        # The captures under way that took the pointer off the screen: it comes back once the last of them has ended,
        # so that a capture that ends first does not put it back over one still being taken.
        self._capturing: int = 0

    async def _running(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is not None:
            log.warning("pointer_helper_ended", returncode=self._process.returncode)
        if self._process is None or self._process.returncode is not None:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "dotagent.desktop.pointer",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
        return self._process

    async def _send(self, command: PointerCommand, start: bool) -> asyncio.subprocess.Process | None:
        """Hand ``command`` to the helper, starting it when ``start``; the helper it went to, or None."""
        if not self._enabled:
            return None
        process: asyncio.subprocess.Process | None = self._process
        if not start and (process is None or process.returncode is not None):
            return None
        try:
            process = await self._running()
            if process.stdin is None:
                raise OSError("the helper has no input")
            process.stdin.write((command.model_dump_json() + "\n").encode("utf-8"))
            await process.stdin.drain()
        except (OSError, ConnectionError) as error:
            log.warning("pointer_not_drawn", op=command.op, error=str(error))
            self._process = None
            return None
        return process

    async def move(self, x: float, y: float, click: bool) -> None:
        """Glide the pointer to (``x``, ``y``), from the top left of the main screen, rippling there when ``click``."""
        async with self._sending:
            await self._send(PointerCommand(op="move", x=x, y=y, click=click), start=True)

    async def warm(self) -> None:
        """Start the helper now, if it is to draw, so that it is ready by the time a step has gone somewhere."""
        if not self._enabled:
            return
        async with self._sending:
            try:
                await self._running()
            except OSError as error:
                log.warning("pointer_not_started", error=str(error))

    @contextlib.asynccontextmanager
    async def hidden(self) -> AsyncIterator[None]:
        """The pointer off the screen while the block runs, for a capture: the helper is asked to hide it and the block
        starts once it says it did (:data:`HIDE_WITHIN_S` at most), and it comes back once the last of the captures
        under way has ended. A capture that comes while another hid it starts at once. The count of captures goes back
        down however the block ends, cancelled while it waited for the helper as well."""
        counted: bool = False
        try:
            async with self._sending:
                self._capturing += 1
                counted = True
                if self._capturing == 1:
                    await self._hide_now()
            yield
        finally:
            if counted:
                async with self._sending:
                    self._capturing -= 1
                    if self._capturing == 0:
                        await self._send(PointerCommand(op="reveal"), start=False)

    async def _hide_now(self) -> None:
        """Ask the helper to hide the pointer and wait until it says it did. A helper that is not running has nothing
        on the screen; one that does not answer in time is ended, and no pointer is drawn from then on, so that no
        capture waits for it again."""
        self._hides += 1
        wanted: str = hidden_line(self._hides)
        process: asyncio.subprocess.Process | None = await self._send(
            PointerCommand(op="hide", number=self._hides), start=False
        )
        if process is None or process.stdout is None:
            return
        try:
            async with asyncio.timeout(HIDE_WITHIN_S):
                while (line := await process.stdout.readline()) and line.decode("utf-8").strip() != wanted:
                    pass
        except TimeoutError:
            log.warning("pointer_not_hidden_in_time", waited_s=HIDE_WITHIN_S)
            self._enabled = False
            process.kill()
            await process.wait()
            self._process = None

    async def rest(self) -> None:
        """Fade the pointer out: the run that moved it has ended."""
        async with self._sending:
            await self._send(PointerCommand(op="rest"), start=False)

    async def aclose(self) -> None:
        """End the helper: its input closes, and it stops."""
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


class _Stage:  # pragma: no cover - draws on the screen; the helper's commands are tested through read_command
    """The helper's window (on its main thread): one pointer, moved, rippled, hidden and faded."""

    def __init__(self) -> None:
        import AppKit
        import Quartz

        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, SIZE_PX, SIZE_PX),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window = self._window
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSScreenSaverWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setHasShadow_(False)
        window.setReleasedWhenClosed_(False)
        # Kept out of what a screen capture sees where macOS honours it; Dot also hides it around its own captures.
        window.setSharingType_(AppKit.NSWindowSharingNone)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )
        view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, SIZE_PX, SIZE_PX))
        view.setWantsLayer_(True)
        window.setContentView_(view)
        root = view.layer()
        colour = Quartz.CGColorCreateGenericRGB(COLOUR[0], COLOUR[1], COLOUR[2], 1.0)
        white = Quartz.CGColorCreateGenericRGB(1.0, 1.0, 1.0, 1.0)
        # The ripple, under the arrow, centred on the tip.
        ripple = Quartz.CAShapeLayer.layer()
        ripple.setFrame_(Quartz.CGRectMake(TIP_PX - 18, SIZE_PX - TIP_PX - 18, 36, 36))
        ripple.setPath_(Quartz.CGPathCreateWithEllipseInRect(Quartz.CGRectMake(0, 0, 36, 36), None))
        ripple.setFillColor_(Quartz.CGColorCreateGenericRGB(COLOUR[0], COLOUR[1], COLOUR[2], 0.25))
        ripple.setStrokeColor_(colour)
        ripple.setLineWidth_(2.0)
        ripple.setOpacity_(0.0)
        root.addSublayer_(ripple)
        self._ripple = ripple
        # The arrow: its tip at (TIP_PX, TIP_PX) from the top left, drawn in the layer's own space (y up).
        path = Quartz.CGPathCreateMutable()
        for index, (x, y) in enumerate(arrow(1.45)):
            point: tuple[float, float] = (TIP_PX + x, SIZE_PX - TIP_PX - y)
            if index == 0:
                Quartz.CGPathMoveToPoint(path, None, *point)
            else:
                Quartz.CGPathAddLineToPoint(path, None, *point)
        Quartz.CGPathCloseSubpath(path)
        shape = Quartz.CAShapeLayer.layer()
        shape.setFrame_(Quartz.CGRectMake(0, 0, SIZE_PX, SIZE_PX))
        shape.setPath_(path)
        shape.setFillColor_(colour)
        shape.setStrokeColor_(white)
        shape.setLineWidth_(1.6)
        shape.setLineJoin_(Quartz.kCALineJoinRound)
        shape.setShadowColor_(colour)
        shape.setShadowOpacity_(0.9)
        shape.setShadowRadius_(9.0)
        shape.setShadowOffset_(Quartz.CGSizeMake(0, 0))
        root.addSublayer_(shape)
        window.setAlphaValue_(0.0)
        self._at: tuple[float, float] | None = None
        self._hidden: bool = False
        self._fades: Fades = Fades()

    def handle(self, command: PointerCommand) -> None:
        from PyObjCTools import AppHelper

        match command.op:
            case "move":
                self._move(command.x, command.y, command.click)
            case "hide":
                self._window.orderOut_(None)
                self._hidden = True
                sys.stdout.write(hidden_line(command.number) + "\n")
                sys.stdout.flush()
            case "reveal":
                self._hidden = False
                move: int | None = self._fades.revealed()
                if move is not None:
                    self._window.orderFrontRegardless()
                    AppHelper.callLater(IDLE_S, self._fade, move)
            case "rest":
                self._fade(self._fades.moves)

    def _primary_height(self) -> float:
        import AppKit

        return float(AppKit.NSScreen.screens()[0].frame().size.height)

    def _move(self, x: float, y: float, click: bool) -> None:
        import AppKit
        import Quartz
        from PyObjCTools import AppHelper

        left, bottom = window_origin(x, y, self._primary_height())
        target = AppKit.NSMakeRect(left, bottom, SIZE_PX, SIZE_PX)
        window = self._window
        if self._at is None or not self._fades.shown:
            window.setFrame_display_(target, True)
            seconds: float = 0.0
        else:
            seconds = glide_s(math.hypot(x - self._at[0], y - self._at[1]))
        self._at = (x, y)
        move: int = self._fades.moved()
        if not self._hidden:
            window.orderFrontRegardless()

        def change(context: object) -> None:
            context.setDuration_(seconds)  # type: ignore[attr-defined]
            context.setTimingFunction_(  # type: ignore[attr-defined]
                Quartz.CAMediaTimingFunction.functionWithControlPoints____(0.2, 0.8, 0.2, 1.0)
            )
            window.animator().setFrame_display_(target, True)
            window.animator().setAlphaValue_(1.0)

        def landed() -> None:
            if click:
                self._ripple_now()

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(change, landed)
        AppHelper.callLater(IDLE_S, self._fade, move)

    def _ripple_now(self) -> None:
        import Quartz

        grow = Quartz.CABasicAnimation.animationWithKeyPath_("transform.scale")
        grow.setFromValue_(0.3)
        grow.setToValue_(1.6)
        fade = Quartz.CABasicAnimation.animationWithKeyPath_("opacity")
        fade.setFromValue_(0.9)
        fade.setToValue_(0.0)
        both = Quartz.CAAnimationGroup.animation()
        both.setAnimations_([grow, fade])
        both.setDuration_(RIPPLE_S)
        both.setTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseOut))
        self._ripple.addAnimation_forKey_(both, "ripple")

    def _fade(self, move: int) -> None:
        import AppKit

        if not self._fades.due(move):
            return
        window = self._window

        def change(context: object) -> None:
            context.setDuration_(FADE_S)  # type: ignore[attr-defined]
            window.animator().setAlphaValue_(0.0)

        def gone() -> None:
            if not self._fades.shown:
                window.orderOut_(None)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(change, gone)


def main() -> None:  # pragma: no cover - the helper process
    """The helper: an application without a Dock icon that draws what it reads on its input, a command a line, and
    ends when its input closes."""
    import AppKit
    from PyObjCTools import AppHelper

    application = AppKit.NSApplication.sharedApplication()
    application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    stage: _Stage = _Stage()

    def read() -> None:
        for line in sys.stdin:
            command: PointerCommand | None = read_command(line)
            if command is not None:
                AppHelper.callAfter(stage.handle, command)
        AppHelper.callAfter(AppHelper.stopEventLoop)

    threading.Thread(target=read, name="pointer-commands", daemon=True).start()
    AppHelper.runEventLoop(installInterrupt=False)


if __name__ == "__main__":
    main()
