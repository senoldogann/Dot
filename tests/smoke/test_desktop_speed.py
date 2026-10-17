"""How fast a computer-use loop is (item 343), measured the same way before and after a change: the whole screen is
looked at, a button of the fixture window is clicked as ``desktop_act`` does it (the preview that judges the batch, then
the act), and the screen is looked at again; ten times, against the AppKit fixture window of ``test_desktop``.

The medians (milliseconds) are printed as JSON, and written to the file DOT_SPEED_OUT names when it is set. The test
checks that each click reached the button, never how fast: the numbers are for the record of a change. It moves the real
pointer, so it runs only with ``DOT_DESKTOP_TESTS=1``, as the other desktop tests do.
"""

import os
import statistics
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from test_chrome_speed import _record
from test_desktop import _THREAD, FixtureApp, _allow, _events, _ref_of
from test_desktop import app as app
from test_desktop import failsafe_file as failsafe_file
from test_desktop import settings_file as settings_file

from dotagent.config import DesktopConfig, load_config, load_default_config
from dotagent.desktop.actions import refuse_secure_field
from dotagent.desktop.session import DesktopSession, no_browser_chrome
from dotagent.desktop.types import ActOutcome, ScreenshotView, SnapshotView, Step
from dotagent.desktop.views import ViewRequest

_LOOPS: int = 10
_MAX_CHARS: int = load_default_config().desktop.snapshot_max_chars
_FULL_WINDOW: ViewRequest = ViewRequest(scope="window", ref=None, query=None, full=True, max_chars=_MAX_CHARS)

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        os.environ.get("DOT_DESKTOP_TESTS") != "1",
        reason="set DOT_DESKTOP_TESTS=1 to move the real pointer and keyboard",
    ),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def desktop(app: FixtureApp, failsafe_file: Path, settings_file: Path) -> AsyncIterator[DesktopSession]:
    config: DesktopConfig = load_config(settings_file).desktop
    session: DesktopSession = DesktopSession(config, failsafe_file, no_browser_chrome)
    await session.open_app(_THREAD, str(app.pid), _MAX_CHARS)
    yield session
    await session.close()


def _clicks(app: FixtureApp) -> int:
    return sum(1 for event in _events(app.log) if event.event == "button")


async def test_a_loop_of_screen_click_screen(desktop: DesktopSession, app: FixtureApp) -> None:
    view: SnapshotView = await desktop.snapshot(_THREAD, str(app.pid), _FULL_WINDOW)
    button: str = _ref_of(view.body, r'button "Increment"')
    steps: list[Step] = [Step(action="click", ref=button)]
    timings: dict[str, list[float]] = {"screenshot": [], "preview": [], "act": [], "loop": []}
    before: int = _clicks(app)
    for _ in range(_LOOPS):
        started: float = time.monotonic()
        first: ScreenshotView = await desktop.screenshot()
        looked: float = time.monotonic()
        await desktop.preview(_THREAD, str(app.pid), steps)
        previewed: float = time.monotonic()
        done: ActOutcome = await desktop.act(_THREAD, str(app.pid), steps, _allow, refuse_secure_field, _MAX_CHARS)
        acted: float = time.monotonic()
        again: ScreenshotView = await desktop.screenshot()
        ended: float = time.monotonic()
        assert [report.outcome for report in done.steps] == ["done"], done.steps
        assert first.image and again.image
        timings["screenshot"].extend([looked - started, ended - acted])
        timings["preview"].append(previewed - looked)
        timings["act"].append(acted - previewed)
        timings["loop"].append(ended - started)
    assert _clicks(app) - before == _LOOPS
    _record("desktop", {name: round(statistics.median(seconds) * 1000, 1) for name, seconds in timings.items()})
