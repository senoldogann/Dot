"""The windows a screenshot blacks out are read again right after the capture (item 1299): a password manager that was
opened, or moved, while the picture was being taken is in the picture but not in the list read before it. And a script
that times out says so as the explained error, with its whole process group killed. macOS is a stand-in: nothing is
captured, the window lists are scripted."""

import asyncio
import io
import os
import signal
import subprocess
import time
import uuid
from collections.abc import Callable

import pytest
from PIL import Image

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import apps, capture, perms, screen, script, shots, snapshots
from dotagent.desktop.safety import merged_windows
from dotagent.desktop.script import run_osascript
from dotagent.desktop.types import AppInfo, Rect, WindowInfo

_AREA: Rect = Rect(0, 0, 100, 100)
_WHITE: tuple[int, int, int] = (255, 255, 255)
_BLACK: tuple[int, int, int] = (0, 0, 0)


def _window(window_id: int, owner: str, bounds: Rect, on_screen: bool = True, layer: int = 0) -> WindowInfo:
    return WindowInfo(
        window_id=window_id, pid=window_id, owner=owner, title="", bounds=bounds, layer=layer, alpha=1.0,
        on_screen=on_screen,
    )  # fmt: skip


_NOTES: WindowInfo = _window(1, "Notes", Rect(0, 0, 100, 100))
_MANAGER: WindowInfo = _window(2, "1Password", Rect(10, 10, 40, 40))
_BUNDLES: dict[int, str | None] = {1: "com.apple.Notes", 2: "com.1password.1password"}


class _World:
    """The screen as it changes during a capture: the list the next read of the windows gives."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, before: tuple[WindowInfo, ...]) -> None:
        self.now: tuple[WindowInfo, ...] = before
        self.reads: int = 0
        self.on_capture: Callable[[], None] = lambda: None
        monkeypatch.setattr(perms, "require_screen_recording", lambda: None)
        monkeypatch.setattr(capture, "capture_native", self._capture)
        monkeypatch.setattr(screen, "windows_on_screen", self._windows)
        monkeypatch.setattr(apps, "bundle_ids", lambda: _BUNDLES)

    def _capture(self, area: Rect, timeout_s: float) -> Image.Image:
        self.on_capture()
        return Image.new("RGB", (100, 100), _WHITE)

    def _windows(self) -> tuple[WindowInfo, ...]:
        self.reads += 1
        return self.now


def test_a_password_manager_opened_while_the_picture_was_taken_is_blacked_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, (_NOTES,))
    read_before: tuple[WindowInfo, ...] = (_NOTES,)

    def opens() -> None:
        world.now = (_MANAGER, _NOTES)

    world.on_capture = opens

    picture, redacted = shots._picture_of(load_default_config().desktop, _AREA, read_before)

    assert redacted == 1
    assert picture.getpixel((30, 30)) == _BLACK
    assert picture.getpixel((80, 80)) == _WHITE


def test_a_password_manager_moved_while_the_picture_was_taken_is_blacked_out_where_it_was_and_where_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moved: WindowInfo = _window(2, "1Password", Rect(60, 60, 30, 30))
    world = _World(monkeypatch, (_MANAGER, _NOTES))
    world.on_capture = lambda: setattr(world, "now", (moved, _NOTES))

    picture, redacted = shots._picture_of(load_default_config().desktop, _AREA, (_MANAGER, _NOTES))

    assert redacted == 2
    assert picture.getpixel((30, 30)) == _BLACK
    assert picture.getpixel((70, 70)) == _BLACK
    assert picture.getpixel((95, 5)) == _WHITE


def test_a_screen_with_no_change_is_blacked_out_once_per_window_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, (_MANAGER, _NOTES))

    picture, redacted = shots._picture_of(load_default_config().desktop, _AREA, (_MANAGER, _NOTES))

    assert redacted == 1
    assert world.reads == 1
    assert picture.getpixel((30, 30)) == _BLACK


def test_a_window_that_closed_while_the_picture_was_taken_stays_blacked_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """It may be in the picture: the list before the capture still counts."""
    world = _World(monkeypatch, (_MANAGER, _NOTES))
    world.on_capture = lambda: setattr(world, "now", (_NOTES,))

    picture, redacted = shots._picture_of(load_default_config().desktop, _AREA, (_MANAGER, _NOTES))

    assert redacted == 1
    assert picture.getpixel((30, 30)) == _BLACK


def test_two_readings_of_the_screen_make_one_list_with_each_window_once_as_it_stood() -> None:
    moved: WindowInfo = _window(2, "1Password", Rect(60, 60, 30, 30))

    assert merged_windows((_MANAGER, _NOTES), (_MANAGER, _NOTES)) == (_MANAGER, _NOTES)
    assert merged_windows((_MANAGER,), (moved, _NOTES)) == (_MANAGER, moved, _NOTES)


def test_a_window_capture_blacks_out_a_password_manager_opened_during_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """``capture_front_window`` has the same order: windows, capture, black out. The manager is not in the list read
    first and not among the applications listed first either."""
    notes_app: AppInfo = AppInfo(
        pid=1, name="Notes", bundle_id="com.apple.Notes", bundle_path="/Applications/Notes.app", kind="regular",
        frontmost=True,
    )  # fmt: skip
    manager_app: AppInfo = AppInfo(
        pid=2, name="1Password", bundle_id="com.1password.1password", bundle_path="/Applications/1Password.app",
        kind="regular", frontmost=False,
    )  # fmt: skip
    running: list[tuple[AppInfo, ...]] = [(notes_app,)]
    world = _World(monkeypatch, (_NOTES,))

    def opens() -> None:
        world.now = (_MANAGER, _NOTES)
        running[0] = (notes_app, manager_app)

    world.on_capture = opens
    monkeypatch.setattr(apps, "frontmost_pid", lambda: 1)
    monkeypatch.setattr(apps, "list_apps", lambda: running[0])

    taken = snapshots.capture_front_window(load_default_config().desktop, with_text=False)

    with Image.open(io.BytesIO(taken.image)) as picture:
        scale: float = picture.width / 100
        assert picture.convert("RGB").getpixel((int(30 * scale), int(30 * scale))) == pytest.approx(_BLACK, abs=8)
        assert picture.convert("RGB").getpixel((int(80 * scale), int(80 * scale))) == pytest.approx(_WHITE, abs=8)


class _Hung:
    """An ``osascript`` that is killed and then does not exit: its ``wait`` never returns."""

    pid: int = 424242
    returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return b"", b""

    async def wait(self) -> int:
        await asyncio.sleep(30)
        return -9


async def test_a_script_that_will_not_die_still_raises_the_explained_error_and_its_group_is_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, object] = {}
    killed: list[tuple[int, int]] = []

    async def start(*arguments: str, **options: object) -> _Hung:
        started.update(options)
        return _Hung()

    monkeypatch.setattr(script.asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(script.os, "killpg", lambda group, sent: killed.append((group, sent)))
    monkeypatch.setattr(script, "_REAP_TIMEOUT_S", 0.05)

    with pytest.raises(DesktopError, match=r"osascript did not finish within 0\.05 s"):
        await run_osascript("AppleScript", "delay 30", (), (), 0.05)

    assert started["start_new_session"] is True
    assert killed.count((424242, signal.SIGKILL)) >= 1


@pytest.mark.macos
async def test_a_script_that_works_still_works_in_a_process_group_of_its_own() -> None:
    assert await run_osascript("AppleScript", "return 1 + 1", (), (), 30.0) == "2"


@pytest.mark.macos
async def test_the_shell_command_of_a_script_that_timed_out_dies_with_it() -> None:
    marker: str = f"dot-test-1299-{uuid.uuid4().hex}"
    try:
        with pytest.raises(DesktopError, match="did not finish"):
            await run_osascript("AppleScript", f'do shell script "sleep 60 # {marker}"', (), (), 1.5)
        deadline: float = time.monotonic() + 5.0
        while subprocess.run(["pgrep", "-f", marker], capture_output=True, check=False).stdout.split():  # noqa: ASYNC221
            assert time.monotonic() < deadline, "the command the script started is still running"
            await asyncio.sleep(0.1)
    finally:
        subprocess.run(["pkill", "-f", marker], check=False)  # noqa: ASYNC221
    assert os.getpgid(os.getpid()) > 0
