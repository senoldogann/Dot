"""SnapShots (item 405): the double tap of the Command key told from its other uses, the names a capture's files take,
and the service that keeps captures for the composer, plays the sound and tells the pages, with a capture of its own
in place of the window in front."""

import asyncio
import time
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import snapshots
from dotagent.desktop.flight import FlightCommand, Frame
from dotagent.desktop.snapshots import Kept, SnapshotService, SnapshotTaken, TapDetector, _stem
from dotagent.events import SNAPSHOT_EVENT, SNAPSHOT_FAILED_EVENT, SYSTEM_RUN_ID


def test_two_taps_of_command_within_the_window_are_a_double_tap_and_nothing_else_is() -> None:
    taps: TapDetector = TapDetector(0.4)
    assert taps.command(True, 0.0) is False
    assert taps.command(False, 0.1) is False
    assert taps.command(True, 0.3) is True
    # Too slow: the second press starts a new candidate.
    assert taps.command(False, 0.4) is False
    assert taps.command(True, 1.0) is False
    assert taps.command(False, 1.1) is False
    assert taps.command(True, 1.6) is False
    assert taps.command(False, 1.7) is False
    # A key between the presses is a shortcut (⌘C ⌘V), not a tap.
    assert taps.command(True, 2.5) is False
    taps.key()
    assert taps.command(False, 2.6) is False
    assert taps.command(True, 2.7) is False
    assert taps.command(False, 2.8) is False
    # The key held: a second "down" without an "up" is the same press.
    assert taps.command(True, 4.0) is False
    assert taps.command(True, 4.1) is False
    assert taps.command(False, 4.2) is False
    assert taps.command(True, 4.3) is True


def test_both_command_keys_pressed_together_are_a_capture_and_let_a_double_tap_begun_go() -> None:
    """Item 414: the left and the right Command keys down together, the shortcut another coding agent gives its own
    captures; a shortcut begun with another key is none, and a release does not capture again."""
    keys: TapDetector = TapDetector(0.4)
    # The left one, then the right one while the left is held: one capture, on the second.
    assert keys.command(True, 0.0) is False and keys.sides(True, False) is False
    assert keys.command(True, 0.05) is False and keys.sides(True, True) is True
    # Held, and let go one after the other: nothing more.
    assert keys.command(True, 0.2) is False and keys.sides(False, True) is False
    assert keys.command(False, 0.3) is False and keys.sides(False, False) is False
    # A press soon after does not finish a double tap with the pair's first press: the pair let it go.
    assert keys.command(True, 0.5) is False and keys.sides(True, False) is False
    assert keys.command(False, 0.55) is False and keys.sides(False, False) is False
    # A double tap, then the other key while the first is still held: one gesture, one capture.
    assert keys.command(True, 6.0) is False and keys.sides(True, False) is False
    assert keys.command(False, 6.1) is False and keys.sides(False, False) is False
    assert keys.command(True, 6.2) is True and keys.sides(True, False) is False
    assert keys.command(True, 6.3) is False and keys.sides(True, True) is False
    assert keys.command(False, 6.5) is False and keys.sides(False, False) is False
    # ⌘C held, then the other Command key: a shortcut, not a capture.
    assert keys.command(True, 3.0) is False and keys.sides(True, False) is False
    keys.key()
    assert keys.command(True, 3.1) is False and keys.sides(True, True) is False


def test_the_files_of_a_capture_are_named_after_the_application_and_the_window() -> None:
    assert _stem("Safari", "Weather – Helsinki") == "Safari - Weather – Helsinki"
    assert _stem("Finder", "   ") == "Finder"
    assert _stem("Notes", "x" * 200).endswith("x") and len(_stem("Notes", "x" * 200)) == 80
    assert _stem("", "") == "window"


class _Bus:
    """An event bus that keeps what was emitted."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, str, dict[str, object], bool]] = []

    async def emit(self, run_id: str, kind: str, data: dict[str, object], persist: bool) -> None:
        self.emitted.append((run_id, kind, data, persist))


def _taken(app: str) -> SnapshotTaken:
    return SnapshotTaken(
        app=app,
        title="Window",
        files=[{"id": "att_1", "name": f"{app}.jpg", "kind": "image", "size": 10}],
        taken_at=1.0,
    )


_WINDOW: Frame = Frame(x=100.0, y=80.0, width=800.0, height=500.0)
_DOT: Frame = Frame(x=900.0, y=40.0, width=1000.0, height=900.0)


def _kept(app: str) -> Kept:
    return Kept(snapshot=_taken(app), frame=_WINDOW, picture="/tmp/capture.jpg")


class _Overlay:
    """An overlay that keeps the commands it was sent, as the helper would draw them."""

    def __init__(self) -> None:
        self.sent: list[FlightCommand] = []
        self.warmed: int = 0
        self.closed: int = 0

    async def warm(self) -> None:
        self.warmed += 1

    async def send(self, command: FlightCommand) -> None:
        self.sent.append(command)

    async def aclose(self) -> None:
        self.closed += 1


class _Shower:
    """What brings Dot to the front, here a window at :data:`_DOT`, with the browsers it was told of."""

    def __init__(self) -> None:
        self.agents: list[str | None] = []

    def __call__(self, agent: str | None) -> Frame | None:
        self.agents.append(agent)
        return _DOT


async def test_a_capture_waits_for_the_composer_that_claims_it_and_the_pages_hear_of_it(tmp_path: Path) -> None:
    bus: _Bus = _Bus()
    asked: list[bool] = []

    def taker(with_text: bool) -> Kept:
        asked.append(with_text)
        return _kept("Safari")

    config = load_default_config().snapshots.model_copy(update={"sound": "off", "app_text": False, "enabled": False})
    service: SnapshotService = SnapshotService(config, bus)  # type: ignore[arg-type]
    overlay: _Overlay = _Overlay()
    shower: _Shower = _Shower()
    await service.start(taker, shower, overlay)
    taken: SnapshotTaken = await service.take()
    assert taken.app == "Safari" and asked == [False]
    assert [kind for _, kind, _, _ in bus.emitted] == [SNAPSHOT_EVENT]
    run_id, _, data, persist = bus.emitted[0]
    assert (run_id, persist, data["app"], data["animations"], data["open"]) == (
        SYSTEM_RUN_ID,
        False,
        "Safari",
        True,
        True,
    )
    # The window flashes and its card lifts off it, Dot comes to the front, and the card lands in its window (item 414).
    assert [(command.op, command.frame) for command in overlay.sent] == [("begin", _WINDOW), ("land", _DOT)]
    assert (overlay.sent[0].picture, overlay.sent[0].flash, overlay.sent[0].fly) == ("/tmp/capture.jpg", True, True)
    assert shower.agents == [None]
    assert service.pending() == [taken]
    assert service.claim("Safari/605.1.15") == [taken] and service.claim(None) == [] and service.pending() == []
    # The browser of the page that claimed is where Dot's tab is looked for first next time.
    await service.take()
    service.claim("Mozilla/5.0 Chrome/141.0")
    await service.take()
    assert shower.agents == [None, None, "Mozilla/5.0 Chrome/141.0"]
    # Without the flash, the animations or the opening, none of them happens.
    for key in ("flash", "animations", "open_dot"):
        await service.apply(key, False)
    overlay.sent.clear()
    await service.take()
    assert overlay.sent == [] and len(shower.agents) == 3
    await service.apply("flash", True)
    await service.take()
    assert [(command.op, command.flash, command.fly) for command in overlay.sent] == [("begin", True, False)]
    # A setting changed holds from now.
    await service.apply("app_text", True)
    await service.take()
    assert asked[-1] is True and set(asked[:-1]) == {False}


async def test_a_capture_kept_stays_kept_when_dot_cannot_be_brought_forward() -> None:
    """Item 414's review: what keeps Dot from the front (no application in front, open that did not answer) is logged;
    the capture is in the box all the same, told as taken and not as failed, and its card lands in the corner."""
    bus: _Bus = _Bus()

    def refusing_shower(_agent: str | None) -> Frame | None:
        raise DesktopError("open did not answer in time", {})

    config = load_default_config().snapshots.model_copy(update={"sound": "off", "enabled": False})
    service: SnapshotService = SnapshotService(config, bus)  # type: ignore[arg-type]
    overlay: _Overlay = _Overlay()
    await service.start(lambda _with_text: _kept("Notes"), refusing_shower, overlay)
    taken: SnapshotTaken = await service.take()
    assert taken.app == "Notes" and service.pending() == [taken]
    assert [kind for _, kind, _, _ in bus.emitted] == [SNAPSHOT_EVENT]
    assert [(command.op, command.frame) for command in overlay.sent] == [("begin", _WINDOW), ("land", None)]


async def test_a_capture_that_fails_tells_the_pages_why_and_raises() -> None:
    bus: _Bus = _Bus()

    def refusing(_with_text: bool) -> Kept:
        raise DesktopError("the window in front is Dot's own; switch to the window to capture", {})

    config = load_default_config().snapshots.model_copy(update={"sound": "off", "enabled": False})
    service: SnapshotService = SnapshotService(config, bus)  # type: ignore[arg-type]
    overlay: _Overlay = _Overlay()
    await service.start(refusing, _Shower(), overlay)
    with pytest.raises(DesktopError):
        await service.take()
    assert [kind for _, kind, _, _ in bus.emitted] == [SNAPSHOT_FAILED_EVENT]
    assert bus.emitted[0][2] == {"reason": "the window in front is Dot's own; switch to the window to capture"}
    assert service.pending() == [] and overlay.sent == []
    # The files that could not be stored tell them too.
    failing_bus: _Bus = _Bus()

    def failing(_with_text: bool) -> Kept:
        raise OSError("disk full")

    failing_service: SnapshotService = SnapshotService(config, failing_bus)  # type: ignore[arg-type]
    await failing_service.start(failing, _Shower(), _Overlay())
    with pytest.raises(OSError):
        await failing_service.take()
    assert [data for _, _, data, _ in failing_bus.emitted] == [{"reason": "OSError: disk full"}]


async def test_a_refused_tap_is_told_and_tried_again_until_the_watch_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    tries: list[float] = []

    def refusing(*_args: object) -> None:
        tries.append(time.monotonic())

    monkeypatch.setattr(snapshots.Quartz, "CGEventTapCreate", refusing)
    monkeypatch.setattr(snapshots, "_RETRY_S", 0.05)
    config = load_default_config().snapshots.model_copy(update={"enabled": True})
    service: SnapshotService = SnapshotService(config, _Bus())  # type: ignore[arg-type]
    overlay: _Overlay = _Overlay()
    await service.start(lambda _with_text: _kept("x"), _Shower(), overlay)
    assert service.listening is False and overlay.warmed == 1
    assert service.problem is not None and "Accessibility" in service.problem
    # Tried again meanwhile, so a permission granted takes without a restart; switching it on again starts nothing new.
    await asyncio.sleep(0.2)
    assert len(tries) >= 2
    await service.apply("enabled", True)
    await service.stop()
    tried: int = len(tries)
    await asyncio.sleep(0.15)
    assert len(tries) == tried and service.problem is None and service.listening is False


async def test_the_service_starts_without_the_key_watched_while_snapshots_are_off() -> None:
    config = load_default_config().snapshots.model_copy(update={"enabled": False})
    service: SnapshotService = SnapshotService(config, _Bus())  # type: ignore[arg-type]
    overlay: _Overlay = _Overlay()
    await service.start(lambda _with_text: _kept("x"), _Shower(), overlay)
    assert service.listening is False and service.problem is None and overlay.warmed == 0
    await service.stop()
    assert overlay.closed == 1
    await asyncio.sleep(0)
