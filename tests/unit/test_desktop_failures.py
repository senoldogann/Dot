"""Computer use's failures say what happened (item 751): a hint that cannot name the program that holds a permission
does not replace the error it belongs to, a script that timed out names the window that may wait for an answer, a
capture that came back empty is asked for again and says how long it took and whether Screen Recording is granted, and
a coordinate that lies off the display a screenshot shows is refused. macOS is a stand-in throughout: no script runs,
nothing is captured."""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from dotagent.core.errors import DesktopError, DesktopPermissionError
from dotagent.desktop import actions, capture, perms, script
from dotagent.desktop.actions import StepEnv
from dotagent.desktop.types import DisplayInfo, Rect, WindowInfo


def _unreadable() -> None:
    raise DesktopError(
        "macOS did not give the path of the program it holds responsible for Dot", {"pid": 7, "errno": 3}
    )


def test_the_hint_for_a_missing_permission_never_raises_when_the_program_cannot_be_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(perms, "responsible_program", _unreadable)
    assert "switch on the app that runs Dot, then start it again" in perms.grant_hint("accessibility")
    # The error it is part of comes out, not the error of the hint.
    monkeypatch.setattr(perms, "_accessibility_granted", lambda: False)
    monkeypatch.setattr(perms.Quartz, "CGPreflightPostEventAccess", lambda: True)
    with pytest.raises(DesktopPermissionError, match="has not granted Accessibility"):
        perms.require_accessibility()


class _Osascript:
    """A process that never finishes until it is killed."""

    returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return b"", b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return -9


def _window(owner: str, title: str, on_screen: bool = True) -> WindowInfo:
    return WindowInfo(
        window_id=1,
        pid=5,
        owner=owner,
        title=title,
        bounds=Rect(0, 0, 400, 300),
        layer=0,
        alpha=1.0,
        on_screen=on_screen,
    )


async def _timeout(monkeypatch: pytest.MonkeyPatch, windows: object, targets: tuple[str, ...]) -> DesktopError:
    async def start(*arguments: str, **options: object) -> _Osascript:
        return _Osascript()

    monkeypatch.setattr(script.asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(script.screen, "windows_everywhere", windows)
    with pytest.raises(DesktopError) as caught:
        await script.run_osascript(
            "AppleScript", 'tell application "Microsoft Word" to open x', (), (), 0.05, None, targets
        )
    return caught.value


async def test_a_script_that_timed_out_names_the_window_of_its_target_that_may_wait_for_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown = lambda: (  # noqa: E731
        _window("Safari", "Inbox"),
        _window("Microsoft Word", "Grant File Access"),
        _window("Microsoft Word", "Hidden", on_screen=False),
    )
    error: DesktopError = await _timeout(monkeypatch, shown, ("microsoft word",))
    assert "Microsoft Word shows a window 'Grant File Access' that may wait for an answer" in error.message
    assert "Inbox" not in error.message and "Hidden" not in error.message
    # The Automation dialog stays one possibility.
    assert "Automation approval dialog" in error.message and "did not finish within 0.05 s" in error.message


async def test_a_script_that_timed_out_keeps_its_old_words_when_no_window_is_found_or_the_look_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error: DesktopError = await _timeout(monkeypatch, lambda: (_window("Safari", "Inbox"),), ("Microsoft Word",))
    assert error.message.startswith("osascript did not finish within 0.05 s; macOS may be showing an Automation")
    assert "shows a window" not in error.message

    def broken() -> None:
        raise DesktopError("cannot list the windows", {})

    failed: DesktopError = await _timeout(monkeypatch, broken, ("Microsoft Word",))
    assert "did not finish within 0.05 s" in failed.message and "shows a window" not in failed.message


def test_a_capture_that_came_back_empty_is_asked_for_again_and_a_second_empty_one_says_how_it_went(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[object | None] = [None, "picture"]
    asked: list[int] = []

    def start(completion: capture.Completion) -> None:
        asked.append(1)
        completion(answers.pop(0), None)

    assert capture._await_image(start, 1.0, "captureImageInRect") == "picture"
    assert len(asked) == 2
    asked.clear()
    answers[:] = [None, None]
    monkeypatch.setattr(capture.Quartz, "CGPreflightScreenCaptureAccess", lambda: False)
    with pytest.raises(DesktopError, match="neither an image nor an error") as caught:
        capture._await_image(start, 1.0, "captureImageInRect")
    assert len(asked) == 2
    assert caught.value.context["screen_recording_granted"] is False
    assert caught.value.context["attempts"] == 2 and "elapsed_s" in caught.value.context


def test_a_coordinate_off_the_display_a_screenshot_shows_is_refused() -> None:
    main: DisplayInfo = DisplayInfo(display_id=1, bounds=Rect(0, 0, 1440, 900), main=True)
    beside: DisplayInfo = DisplayInfo(display_id=2, bounds=Rect(1440, 0, 1920, 1080), main=False)
    env: StepEnv = cast(StepEnv, SimpleNamespace(scale=0.5, displays=(main, beside)))
    assert actions._to_points(env, 100.0, 200.0).x == 200.0
    # (3000, 100) in model units is (6000, 200) points: on no display; (800, 100) is (1600, 200): on the second one.
    for x, y in ((3000.0, 100.0), (800.0, 100.0), (-5.0, 10.0)):
        with pytest.raises(DesktopError, match="take a desktop_screenshot"):
            actions._to_points(env, x, y)
