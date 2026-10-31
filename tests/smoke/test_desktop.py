"""Desktop use end to end: the real pointer and keyboard of this Mac drive a small AppKit fixture window.

The fixture app (``tests/fixtures/dot_fixture_app.py``) writes one JSON line per event it receives, so every check
compares what the application saw with what the step asked for. The tests move the real pointer and type on the real
keyboard, so they only run when ``DOT_DESKTOP_TESTS=1`` and macOS has granted Accessibility, input events and screen
recording to the process that runs pytest.
"""

import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from AppKit import NSPasteboard
from pydantic import BaseModel, ConfigDict

from dotagent.config import DesktopConfig, load_config, load_default_config
from dotagent.core.errors import FailSafeTriggered
from dotagent.desktop import screen
from dotagent.desktop.actions import refuse_secure_field
from dotagent.desktop.input import PasteboardLike, PasteboardSnapshot, _restore_pasteboard, _snapshot_pasteboard
from dotagent.desktop.perms import check_permissions
from dotagent.desktop.session import DesktopSession, StepReview, no_browser_chrome
from dotagent.desktop.types import ActOutcome, AppView, Permissions, Point, SnapshotView, Step, TextRead
from dotagent.desktop.views import ViewRequest

_FIXTURE: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_fixture_app.py"
_THREAD: str = "smoke"
_READY_TIMEOUT_S: float = 15.0
_FINDER: str = "com.apple.finder"
# The whole window of the fixture application, as [desktop] snapshot_max_chars lets a view carry it.
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


class FixtureEvent(BaseModel):
    """One line of the fixture's event log; only the fields the checks read."""

    model_config = ConfigDict(extra="ignore")

    event: str
    field: str | None = None
    item: str | None = None
    chars: str | None = None
    value: str | None = None
    count: int | None = None
    clicks: int | None = None
    on: bool | None = None
    x: float | None = None
    y: float | None = None
    pid: int | None = None


@dataclass(frozen=True)
class FixtureApp:
    pid: int
    log: Path


@dataclass(frozen=True)
class Spot:
    """The centre of an element on screen, in the units of desktop_screenshot."""

    x: int
    y: int


def _events(log: Path) -> list[FixtureEvent]:
    # The app may be in the middle of writing its last line; only lines that end in a newline are complete.
    complete: list[str] = log.read_text(encoding="utf-8").split("\n")[:-1]
    return [FixtureEvent.model_validate(json.loads(line)) for line in complete if line.strip()]


def _since(app: FixtureApp, position: int, kind: str) -> list[FixtureEvent]:
    return [event for event in _events(app.log)[position:] if event.event == kind]


def _mark(app: FixtureApp) -> int:
    return len(_events(app.log))


async def _allow(review: StepReview) -> None:
    return None


def _arm_failsafe(path: Path) -> None:
    path.write_text("stop", encoding="utf-8")


def _disarm_failsafe(path: Path) -> None:
    path.unlink()


def _line_of(body: str, pattern: str) -> str:
    for line in body.splitlines():
        if re.search(pattern, line):
            return line
    raise AssertionError(f"no element matches {pattern!r} in:\n{body}")


def _ref_of(body: str, pattern: str) -> str:
    line: str = _line_of(body, pattern)
    found: re.Match[str] | None = re.search(r"\[(a\d+)\]", line)
    assert found is not None, f"no ref on the line {line!r}"
    return found.group(1)


def _spot_of(body: str, pattern: str) -> Spot:
    line: str = _line_of(body, pattern)
    found: re.Match[str] | None = re.search(r"@(-?\d+),(-?\d+)", line)
    assert found is not None, f"no position on the line {line!r}"
    return Spot(x=int(found.group(1)), y=int(found.group(2)))


def _wait_until_ready(process: subprocess.Popen[bytes], log: Path) -> int:
    deadline: float = time.monotonic() + _READY_TIMEOUT_S
    while time.monotonic() < deadline:
        assert process.poll() is None, f"the fixture app exited with code {process.returncode}"
        if log.exists():
            for event in _events(log):
                if event.event == "ready" and event.pid is not None:
                    return event.pid
        time.sleep(0.1)
    raise AssertionError("the fixture app did not become ready")


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FixtureApp]:
    permissions: Permissions = check_permissions()
    if not (permissions.accessibility and permissions.post_event and permissions.screen_recording):
        pytest.skip(f"macOS has not granted the desktop permissions to this process: {permissions}")
    log: Path = tmp_path_factory.mktemp("fixture") / "events.jsonl"
    process: subprocess.Popen[bytes] = subprocess.Popen([sys.executable, str(_FIXTURE), str(log)])
    try:
        yield FixtureApp(pid=_wait_until_ready(process, log), log=log)
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def failsafe_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("failsafe") / "STOP"


@pytest.fixture(scope="module")
def settings_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[desktop]\nenabled = true\n", encoding="utf-8")
    return settings


@pytest.fixture
def owner_clipboard() -> Iterator[None]:
    """Whatever the clipboard holds (text, an image, files) is put back after the test."""
    pasteboard: PasteboardLike = NSPasteboard.generalPasteboard()
    kept: PasteboardSnapshot = _snapshot_pasteboard(pasteboard)
    yield
    _restore_pasteboard(pasteboard, kept)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session(app: FixtureApp, failsafe_file: Path, settings_file: Path) -> AsyncIterator[DesktopSession]:
    config: DesktopConfig = load_config(settings_file).desktop
    desktop: DesktopSession = DesktopSession(config, failsafe_file, no_browser_chrome)
    await desktop.open_app(_THREAD, str(app.pid), _MAX_CHARS, None)
    yield desktop
    await desktop.close()


async def _body(session: DesktopSession, app: FixtureApp) -> str:
    view: SnapshotView = await session.snapshot(_THREAD, str(app.pid), _FULL_WINDOW)
    return view.body


async def _act(session: DesktopSession, app: FixtureApp, steps: list[Step]) -> ActOutcome:
    outcome: ActOutcome = await session.act(_THREAD, str(app.pid), steps, _allow, refuse_secure_field, _MAX_CHARS)
    await asyncio.sleep(0.15)
    return outcome


async def test_click_and_press_reach_the_button(session: DesktopSession, app: FixtureApp) -> None:
    button: str = _ref_of(await _body(session, app), r'button "Increment"')
    mark: int = _mark(app)
    pointer: Point = screen.pointer_location()
    outcome: ActOutcome = await _act(session, app, [Step(action="click", ref=button), Step(action="press", ref=button)])
    assert [report.outcome for report in outcome.steps] == ["done", "done"]
    # A click on a button's ref is a press through accessibility (item 862): the pointer stayed where it was.
    assert screen.pointer_location() == pointer
    assert "the pointer did not move" in outcome.steps[0].detail
    counts: list[int | None] = [event.count for event in _since(app, mark, "button")]
    assert len(counts) == 2
    assert counts[1] == (counts[0] or 0) + 1


async def test_press_toggles_the_checkbox_both_ways(session: DesktopSession, app: FixtureApp) -> None:
    checkbox: str = _ref_of(await _body(session, app), r'checkbox "Enabled"')
    mark: int = _mark(app)
    await _act(session, app, [Step(action="press", ref=checkbox)])
    await _act(session, app, [Step(action="click", ref=checkbox)])
    states: list[bool | None] = [event.on for event in _since(app, mark, "checkbox")]
    assert len(states) == 2
    assert states[0] is not None
    assert states[1] == (not states[0])


async def test_type_enters_turkish_text_key_by_key(session: DesktopSession, app: FixtureApp) -> None:
    name: str = _ref_of(await _body(session, app), r'textfield "Name"')
    mark: int = _mark(app)
    await _act(
        session,
        app,
        [
            Step(action="click", ref=name),
            Step(action="key", key="cmd+a"),
            Step(action="type", text="Merhaba ğüşiöç İI"),
        ],
    )
    typed: list[FixtureEvent] = [event for event in _since(app, mark, "text") if event.field == "name"]
    assert typed
    assert typed[-1].value == "Merhaba ğüşiöç İI"


@pytest.mark.usefixtures("owner_clipboard")
async def test_a_long_typed_text_goes_in_through_the_clipboard_which_is_put_back(
    session: DesktopSession, app: FixtureApp
) -> None:
    name: str = _ref_of(await _body(session, app), r'textfield "Name"')
    long_text: str = "Uzun metin panodan gelir: " + "ğüşiöç İI " * 8
    await session.write_clipboard("önceki pano")
    mark: int = _mark(app)
    outcome: ActOutcome = await _act(
        session,
        app,
        [Step(action="click", ref=name), Step(action="key", key="cmd+a"), Step(action="type", text=long_text)],
    )
    assert "pasted" in outcome.steps[-1].detail, outcome.steps
    typed: list[FixtureEvent] = [event for event in _since(app, mark, "text") if event.field == "name"]
    assert typed
    assert typed[-1].value == long_text
    assert await session.read_clipboard() == "önceki pano"


async def test_set_value_replaces_the_value_and_read_returns_it(session: DesktopSession, app: FixtureApp) -> None:
    name: str = _ref_of(await _body(session, app), r'textfield "Name"')
    await _act(session, app, [Step(action="set_value", ref=name, text="değer AX ile")])
    read: TextRead = await session.read(_THREAD, str(app.pid), int(name.removeprefix("a")), 0, 100)
    assert "değer AX ile" in read.text


async def test_paste_inserts_unicode_through_the_clipboard(session: DesktopSession, app: FixtureApp) -> None:
    name: str = _ref_of(await _body(session, app), r'textfield "Name"')
    mark: int = _mark(app)
    await _act(
        session,
        app,
        [Step(action="click", ref=name), Step(action="key", key="cmd+a"), Step(action="paste", text="yapıştırıldı ✓")],
    )
    pasted: list[FixtureEvent] = [event for event in _since(app, mark, "text") if event.field == "name"]
    assert pasted
    assert pasted[-1].value == "yapıştırıldı ✓"


async def test_scroll_moves_the_area_in_all_four_directions(session: DesktopSession, app: FixtureApp) -> None:
    scroller: str = _ref_of(await _body(session, app), r"scrollarea")
    origins: dict[str, FixtureEvent] = {}
    for direction in ("down", "up", "right", "left"):
        mark: int = _mark(app)
        await _act(session, app, [Step(action="scroll", ref=scroller, direction=direction, amount=300)])
        moved: list[FixtureEvent] = _since(app, mark, "scrolled")
        assert moved, f"scrolling {direction} did not move the area"
        origins[direction] = moved[-1]
    assert (origins["down"].y or 0) > 0
    assert (origins["up"].y or 0) < (origins["down"].y or 0)
    assert (origins["right"].x or 0) > 0
    assert (origins["left"].x or 0) < (origins["right"].x or 0)


async def test_double_click_is_seen_as_a_double_click(session: DesktopSession, app: FixtureApp) -> None:
    pad: str = _ref_of(await _body(session, app), r'group "Pad"')
    mark: int = _mark(app)
    await _act(session, app, [Step(action="double_click", ref=pad)])
    assert any(event.clicks == 2 for event in _since(app, mark, "pad_down"))


async def test_right_click_opens_a_menu_whose_items_can_be_chosen(session: DesktopSession, app: FixtureApp) -> None:
    pad: str = _ref_of(await _body(session, app), r'group "Pad"')
    mark: int = _mark(app)
    await _act(session, app, [Step(action="right_click", ref=pad)])
    assert _since(app, mark, "pad_context")
    menu: SnapshotView = await session.snapshot(
        _THREAD, str(app.pid), ViewRequest(scope="window", ref=None, query="Alpha", full=False, max_chars=_MAX_CHARS)
    )
    await _act(session, app, [Step(action="click", ref=_ref_of(menu.body, r"Alpha"))])
    assert [event.item for event in _since(app, mark, "context_pick")] == ["Alpha"]


async def test_drag_hover_and_keys_reach_the_pad(session: DesktopSession, app: FixtureApp) -> None:
    body: str = await _body(session, app)
    pad: str = _ref_of(body, r'group "Pad"')
    spot: Spot = _spot_of(body, r'group "Pad"')

    mark: int = _mark(app)
    await _act(session, app, [Step(action="drag", x=spot.x - 80, y=spot.y, to_x=spot.x + 80, to_y=spot.y + 20)])
    assert _since(app, mark, "pad_down")
    assert _since(app, mark, "pad_up")

    mark = _mark(app)
    await _act(session, app, [Step(action="hover", x=spot.x, y=spot.y), Step(action="hover", x=spot.x + 20, y=spot.y)])
    assert _since(app, mark, "pad_move") or _since(app, mark, "pad_enter")

    mark = _mark(app)
    await _act(session, app, [Step(action="click", ref=pad), Step(action="key", key="x")])
    assert any(event.chars == "x" for event in _since(app, mark, "key"))


async def test_menu_step_and_key_equivalent_choose_the_same_item(session: DesktopSession, app: FixtureApp) -> None:
    mark: int = _mark(app)
    await _act(session, app, [Step(action="key", key="cmd+p")])
    assert [event.item for event in _since(app, mark, "menu")] == ["Ping"]
    mark = _mark(app)
    await _act(session, app, [Step(action="menu", path=["Actions", "Ping"])])
    assert [event.item for event in _since(app, mark, "menu")] == ["Ping"]


async def test_secure_fields_are_refused_and_stay_untouched(session: DesktopSession, app: FixtureApp) -> None:
    secret: str = _ref_of(await _body(session, app), r"securefield")
    mark: int = _mark(app)
    outcome: ActOutcome = await _act(
        session, app, [Step(action="click", ref=secret), Step(action="type", text="fixture-test-value")]
    )
    assert [report.outcome for report in outcome.steps] == ["done", "failed"]
    assert "secure text field is refused" in outcome.steps[-1].detail
    assert not _since(app, mark, "text")


@pytest.mark.usefixtures("owner_clipboard")
async def test_clipboard_round_trip(session: DesktopSession) -> None:
    await session.write_clipboard("Dot panosu ✓ 😀")
    assert await session.read_clipboard() == "Dot panosu ✓ 😀"


async def test_activate_switches_to_another_application_and_back(session: DesktopSession, app: FixtureApp) -> None:
    await _act(session, app, [Step(action="activate", text=_FINDER)])
    front: list[AppView] = [view for view in await session.list_apps() if view.frontmost]
    assert [view.bundle_id for view in front] == [_FINDER]
    await _act(session, app, [Step(action="activate", text=str(app.pid))])
    assert [view.pid for view in await session.list_apps() if view.frontmost] == [app.pid]


async def _timed_act(session: DesktopSession, app: FixtureApp, steps: list[Step]) -> float:
    started: float = time.perf_counter()
    outcome: ActOutcome = await session.act(_THREAD, str(app.pid), steps, _allow, refuse_secure_field, _MAX_CHARS)
    elapsed: float = time.perf_counter() - started
    assert [report.outcome for report in outcome.steps] == ["done"] * len(steps), outcome
    await asyncio.sleep(0.15)
    return elapsed


async def test_a_click_and_a_batch_of_five_clicks_stay_fast(session: DesktopSession, app: FixtureApp) -> None:
    """20 acts of one click and 5 of five clicks on the fixture's button. The medians are printed (``-s`` shows them):
    ROADMAP item 13 keeps them."""
    click: Step = Step(action="click", ref=_ref_of(await _body(session, app), r'button "Increment"'))
    singles: list[float] = [await _timed_act(session, app, [click]) for _ in range(20)]
    batches: list[float] = [await _timed_act(session, app, [click] * 5) for _ in range(5)]
    single: float = statistics.median(singles)
    batch: float = statistics.median(batches)
    print(f"\ndesktop: one click {single:.3f} s, five clicks {batch:.3f} s (medians)")
    assert single < 1.5
    assert batch < 3.0


async def test_the_failsafe_file_stops_a_batch_and_work_resumes_when_it_is_removed(
    session: DesktopSession, app: FixtureApp, failsafe_file: Path
) -> None:
    button: str = _ref_of(await _body(session, app), r'button "Increment"')
    await asyncio.to_thread(_arm_failsafe, failsafe_file)
    try:
        with pytest.raises(FailSafeTriggered):
            await session.act(
                _THREAD, str(app.pid), [Step(action="click", ref=button)], _allow, refuse_secure_field, _MAX_CHARS
            )
    finally:
        await asyncio.to_thread(_disarm_failsafe, failsafe_file)
    mark: int = _mark(app)
    await _act(session, app, [Step(action="click", ref=button)])
    assert _since(app, mark, "button")
