"""SnapShots (item 405): the double tap of the Command key told from its other uses, the names a capture's files take,
and the service that keeps captures for the composer, plays the sound and tells the pages, with a capture of its own
in place of the window in front."""

import asyncio
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop.snapshots import SnapshotService, SnapshotTaken, TapDetector, _stem
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


async def test_a_capture_waits_for_the_composer_that_claims_it_and_the_pages_hear_of_it(tmp_path: Path) -> None:
    bus: _Bus = _Bus()
    asked: list[bool] = []

    def taker(with_text: bool) -> SnapshotTaken:
        asked.append(with_text)
        return _taken("Safari")

    config = load_default_config().snapshots.model_copy(update={"sound": "off", "app_text": False, "enabled": False})
    service: SnapshotService = SnapshotService(config, bus)  # type: ignore[arg-type]
    await service.start(taker)
    taken: SnapshotTaken = await service.take()
    assert taken.app == "Safari" and asked == [False]
    assert [kind for _, kind, _, _ in bus.emitted] == [SNAPSHOT_EVENT]
    run_id, _, data, persist = bus.emitted[0]
    assert (run_id, persist, data["app"], data["animations"]) == (SYSTEM_RUN_ID, False, "Safari", True)
    assert service.pending() == [taken]
    assert service.claim() == [taken] and service.claim() == [] and service.pending() == []
    # A setting changed holds from now.
    await service.apply("app_text", True)
    await service.take()
    assert asked == [False, True]


async def test_a_capture_that_fails_tells_the_pages_why_and_raises() -> None:
    bus: _Bus = _Bus()

    def refusing(_with_text: bool) -> SnapshotTaken:
        raise DesktopError("the window in front is Dot's own; switch to the window to capture", {})

    config = load_default_config().snapshots.model_copy(update={"sound": "off", "enabled": False})
    service: SnapshotService = SnapshotService(config, bus)  # type: ignore[arg-type]
    await service.start(refusing)
    with pytest.raises(DesktopError):
        await service.take()
    assert [kind for _, kind, _, _ in bus.emitted] == [SNAPSHOT_FAILED_EVENT]
    assert bus.emitted[0][2] == {"reason": "the window in front is Dot's own; switch to the window to capture"}
    assert service.pending() == []


async def test_the_service_starts_without_the_key_watched_while_snapshots_are_off() -> None:
    config = load_default_config().snapshots.model_copy(update={"enabled": False})
    service: SnapshotService = SnapshotService(config, _Bus())  # type: ignore[arg-type]
    await service.start(lambda _with_text: _taken("x"))
    assert service.listening is False and service.problem is None
    await service.stop()
    await asyncio.sleep(0)
