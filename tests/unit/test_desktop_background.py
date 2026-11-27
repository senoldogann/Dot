"""The background driver of ``[desktop] background_driver = "cua"`` (item 2007): which steps go through it, that
they go to the batch's own application with the review judging what they reach, and that they go while the owner is at
the Mac where the Mac's own pointer would wait (item 1890). A fake driver stands in for cua-driver and the accessibility
calls are replaced, so nothing reaches the screen."""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from pydantic import JsonValue

from dotagent.config import DesktopConfig, load_default_config
from dotagent.core.errors import DesktopStepNotSent, FailSafeTriggered, OwnerAtTheMac
from dotagent.desktop import actions, apps, ax, cua, screen, session
from dotagent.desktop import input as hid
from dotagent.desktop.actions import Inspection, StepEnv
from dotagent.desktop.ax import ElementState
from dotagent.desktop.cua import CuaResult
from dotagent.desktop.presence import OwnerPresence
from dotagent.desktop.safety import StepContext
from dotagent.desktop.session import DesktopSession, StepReview, no_browser_chrome
from dotagent.desktop.types import ActOutcome, AppInfo, DisplayInfo, Point, Rect, Step, WindowInfo

_MAIL: AppInfo = AppInfo(
    pid=700, name="Mail", bundle_id="com.apple.mail", bundle_path="", kind="regular", frontmost=False
)
_WINDOW: WindowInfo = WindowInfo(
    window_id=31,
    pid=_MAIL.pid,
    owner="Mail",
    title="Inbox",
    bounds=Rect(100, 100, 400, 300),
    layer=0,
    alpha=1.0,
    on_screen=True,
)

_FIELD: ElementState = ElementState(
    role="AXTextField",
    subrole="",
    label="To",
    frame=Rect(140, 110, 40, 20),
    enabled=True,
    focused=False,
    selected=False,
)


@dataclass
class _Driver:
    """cua-driver as Dot sees it: each call is kept; a capture answers the window at 2 pixels a point."""

    calls: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)
    alive: bool = True

    def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> CuaResult:
        self.calls.append((tool, dict(arguments)))
        structured: dict[str, JsonValue] = {"route": "accessibility", "effect": "confirmed"}
        if tool == "get_window_state":
            structured = {"window_bounds": {"x": 100, "y": 100, "width": 400, "height": 300}, "screenshot_width": 800}
        return CuaResult(text="ok", is_error=False, route="accessibility", effect="confirmed", structured=structured)

    def close(self) -> None:
        self.alive = False


def _env(tmp_path: Path, owner_here: bool, driver: _Driver | None) -> StepEnv:
    return StepEnv(
        app=_MAIL,
        app_element="app-element",
        elements={},
        running=(_MAIL,),
        displays=(DisplayInfo(display_id=1, bounds=Rect(0, 0, 2000, 1200), main=True),),
        scale=1.0,
        source=object(),
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "STOP",
        expected_front=None,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=lambda inspection, what: None,
        owner_here=owner_here,
        background=driver,
    )


def _inspection(pid: int) -> Inspection:
    return Inspection(
        target="the point in Mail",
        context=StepContext(role="other", app="Mail", element=None, secure=False, line=None),
        pid=pid,
    )


# The emergency stop as actions has it, before the fixture below takes it out.
_CHECK_FAILSAFE: Callable[[StepEnv], None] = actions.check_failsafe


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """No failsafe, the review's target as it was, Mail's window on the screen; what the Mac's own input would post."""
    posted: list[object] = []
    monkeypatch.setattr(actions, "check_failsafe", lambda env: None)
    monkeypatch.setattr(actions, "_reviewed_again", lambda env, step, reviewed: reviewed)
    monkeypatch.setattr(screen, "windows_on_screen", lambda: (_WINDOW,))
    monkeypatch.setattr(ax, "focused_window", lambda app, pid: "inbox-window")
    monkeypatch.setattr(ax, "frame_of", lambda element, pid: _WINDOW.bounds)
    monkeypatch.setattr(hid, "post", lambda events, check=None: posted.append(events))
    monkeypatch.setattr(hid, "chord_events", lambda source, chord: ("chord", chord.key))
    return posted


def test_which_steps_go_through_the_background_driver(tmp_path: Path) -> None:
    on: StepEnv = _env(tmp_path, False, _Driver())
    off: StepEnv = _env(tmp_path, False, None)
    goes: list[Step] = [
        Step(action="click", x=10.0, y=10.0),
        Step(action="click", ref="a3"),
        Step(action="double_click", ref="a3"),
        Step(action="right_click", x=5.0, y=5.0),
        Step(action="type", text="hi"),
        Step(action="paste", text="hi"),
        Step(action="key", key="cmd+s"),
        Step(action="key", key="Enter"),
        Step(action="scroll", ref="a3", direction="down"),
    ]
    stays: list[Step] = [
        Step(action="click", ref="a3", modifiers=["cmd"]),
        Step(action="hover", ref="a3"),
        Step(action="drag", ref="a3", to_ref="a4"),
        Step(action="activate", text="Notes"),
        Step(action="key", key="fwddelete"),
        Step(action="press", ref="a3"),
        Step(action="set_value", ref="a3", text="x"),
        Step(action="menu", path=["File", "Save"]),
    ]
    assert all(actions.in_background(on, step) for step in goes)
    assert not any(actions.in_background(on, step) for step in stays)
    assert not any(actions.in_background(off, step) for step in goes)
    # What goes in the background takes none of the Mac's own input; what stays takes it as before.
    assert not any(actions.takes_mac_input(on, step) for step in goes)
    assert all(actions.takes_mac_input(off, step) == actions.takes_input(step) for step in goes + stays)
    assert actions.takes_mac_input(on, Step(action="hover", ref="a3"))


def test_a_key_goes_to_the_apps_window_while_the_owner_is_at_the_mac(tmp_path: Path, mac: list[object]) -> None:
    driver: _Driver = _Driver()
    step: Step = Step(action="key", key="cmd+s")
    effect: actions.Effect = actions.execute_step(_env(tmp_path, True, driver), step, _inspection(_MAIL.pid))
    assert mac == []
    assert driver.calls == [
        (
            "press_key",
            {
                "pid": _MAIL.pid,
                "window_id": _WINDOW.window_id,
                "key": "s",
                "modifiers": ["cmd"],
                "delivery_mode": "background",
                "session": "dot",
            },
        )
    ]
    assert effect.pointer is None and "in the background through cua-driver" in effect.detail
    # Off, the same step does not go while the owner is here (item 1890).
    with pytest.raises(OwnerAtTheMac):
        actions.execute_step(_env(tmp_path, True, None), step, _inspection(_MAIL.pid))


def test_a_click_at_a_point_is_placed_in_a_fresh_capture_of_the_window(tmp_path: Path, mac: list[object]) -> None:
    driver: _Driver = _Driver()
    step: Step = Step(action="click", x=150.0, y=120.0)
    effect: actions.Effect = actions.execute_step(_env(tmp_path, True, driver), step, _inspection(_MAIL.pid))
    [capture, click] = driver.calls
    assert capture[0] == "get_window_state" and capture[1]["include_accessibility_tree"] is False
    assert capture[1]["window_id"] == _WINDOW.window_id and capture[1]["max_dimension"] == 400
    assert click == (
        "click",
        {
            "pid": _MAIL.pid,
            "window_id": _WINDOW.window_id,
            "x": 100,
            "y": 40,
            "button": "left",
            "count": 1,
            "delivery_mode": "background",
            "session": "dot",
        },
    )
    assert mac == [] and effect.pointer is None and effect.aim is not None


def test_a_point_the_review_found_in_another_application_is_not_sent(tmp_path: Path, mac: list[object]) -> None:
    """The driver would reach Mail's window under the point, not the window the review judged there."""
    driver: _Driver = _Driver()
    with pytest.raises(DesktopStepNotSent, match="not Mail's"):
        actions.execute_step(_env(tmp_path, True, driver), Step(action="click", x=150.0, y=120.0), _inspection(pid=900))
    assert driver.calls == [] and mac == []


def test_a_point_outside_the_apps_windows_is_not_sent(tmp_path: Path, mac: list[object]) -> None:
    driver: _Driver = _Driver()
    with pytest.raises(DesktopStepNotSent, match="no window of Mail"):
        actions.execute_step(
            _env(tmp_path, True, driver), Step(action="click", x=900.0, y=900.0), _inspection(_MAIL.pid)
        )
    assert driver.calls == []


def test_the_review_judges_the_field_of_the_batchs_own_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the background the keys go to Mail's focused field, not to the field of the application in front."""
    asked: list[int] = []

    def front() -> int:
        raise AssertionError("the application in front is not where the background driver's keys go")

    def focused(root: object, pid: int) -> None:
        asked.append(pid)

    monkeypatch.setattr(apps, "frontmost_pid", front)
    monkeypatch.setattr(ax, "focused_element", focused)
    monkeypatch.setattr(actions, "_window_title", lambda element, app, pid: "Inbox")
    inspection: Inspection = actions.inspect_step(
        _env(tmp_path, True, _Driver()), Step(action="type", text="hello"), {}
    )
    assert inspection.pid == _MAIL.pid and asked == [_MAIL.pid]


def _presence(idle_s: float) -> OwnerPresence:
    """The owner's last input ``idle_s`` ago: at the Mac under 600 s, away from it after."""
    return OwnerPresence(lambda: idle_s, lambda: 10_000.0, 600.0)


async def _allow(review: StepReview) -> None:
    return None


def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    presence: OwnerPresence,
    start: Callable[[], _Driver] | None,
) -> DesktopSession:
    """A session on fakes whose steps run :func:`actions.execute_step` itself, with the driver ``start`` gives."""
    monkeypatch.setattr(session.apps, "ensure_window_server", lambda: None)
    monkeypatch.setattr(DesktopSession, "_prepare_acting", lambda self: None)
    monkeypatch.setattr(DesktopSession, "_prepare_listing", lambda self: None)
    monkeypatch.setattr(session, "_needs_front", lambda steps, nodes: False)
    monkeypatch.setattr(session, "_STEP_GAP_S", 0.0)

    def open_env(self: DesktopSession, state: object, name: object, steps: object, secure_field: object) -> StepEnv:
        return _env(tmp_path, self._owner_here(), self._background_job())

    async def closing_view(
        self: DesktopSession, state: object, app: object, max_chars: int
    ) -> tuple[None, str, object]:
        return None, "no view", state

    monkeypatch.setattr(DesktopSession, "_open_env", open_env)
    monkeypatch.setattr(DesktopSession, "_closing_view", closing_view)
    monkeypatch.setattr(session, "_remeasure", lambda env: env)
    monkeypatch.setattr(session, "_front_taken_by", lambda env, remeasured: None)
    monkeypatch.setattr(ax, "wait_until_quiet", lambda pid, quiet_s, max_s: None)
    monkeypatch.setattr(actions, "inspect_step", lambda env, step, lines: _inspection(_MAIL.pid))
    config: DesktopConfig = load_default_config().desktop.model_copy(update={"show_pointer": False})
    return DesktopSession(config, tmp_path / "STOP", no_browser_chrome, None, presence, start)


async def test_a_batch_sends_its_background_steps_while_the_owner_is_here_and_waits_at_a_hover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: list[object]
) -> None:
    driver: _Driver = _Driver()
    started: list[_Driver] = []

    def start() -> _Driver:
        started.append(driver)
        return driver

    held: DesktopSession = _session(tmp_path, monkeypatch, _presence(1.0), start)
    steps: list[Step] = [Step(action="key", key="tab"), Step(action="hover", x=150.0, y=120.0)]
    try:
        outcome: ActOutcome = await held.act("thread", None, steps, _allow, lambda i, w: None, 1000, "Dot", 1)
        assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
        assert outcome.waits_from == 2 and not outcome.owner_came_back
        assert [call[0] for call in driver.calls] == ["press_key"] and mac == []
        # A second batch uses the driver that runs; one that stopped is started again.
        await held.act("thread", None, steps[:1], _allow, lambda i, w: None, 1000, "Dot", 1)
        assert len(started) == 1
        driver.alive = False
        await held.act("thread", None, steps[:1], _allow, lambda i, w: None, 1000, "Dot", 1)
        assert len(started) == 2
    finally:
        await held.close()
    assert not driver.alive


@pytest.mark.parametrize("background", [True, False], ids=["driver", "the Mac's own"])
async def test_a_background_step_is_not_taken_as_dots_own_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: list[object], background: bool
) -> None:
    """The owner is away. A step the driver sends posts nothing to the Mac's own input, so no span of it is Dot's own
    and the owner's touch meanwhile reads as their return; the same steps on the Mac's own input are Dot's."""
    driver: _Driver = _Driver()
    presence: OwnerPresence = _presence(10_000.0)
    noted: list[tuple[float, float]] = []
    monkeypatch.setattr(presence, "note_own_input", lambda began, ended: noted.append((began, ended)))
    monkeypatch.setattr(actions, "_element_for", lambda env, raw: (4, "field"))
    monkeypatch.setattr(actions, "_state_of", lambda env, number, element: _FIELD)
    monkeypatch.setattr(actions, "_pressed", lambda env, inspection, number, element, state: None)
    monkeypatch.setattr(actions, "_bring_into_view", lambda env, number, element: Rect(140, 110, 40, 20))
    monkeypatch.setattr(actions, "_check_uncovered", lambda env, number, state, point: None)
    monkeypatch.setattr(actions.apps, "frontmost_pid", lambda: _MAIL.pid)
    monkeypatch.setattr(hid, "left_click_events", lambda source, point, count, modifiers: ("click", count))
    held: DesktopSession = _session(tmp_path, monkeypatch, presence, (lambda: driver) if background else None)
    steps: list[Step] = [Step(action="key", key="tab"), Step(action="click", ref="a4")]
    try:
        outcome: ActOutcome = await held.act("thread", None, steps, _allow, lambda i, w: None, 1000, "Dot", 1)
    finally:
        await held.close()
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    if background:
        assert noted == [] and mac == []
        assert [call[0] for call in driver.calls] == ["press_key", "get_window_state", "click"]
    else:
        assert len(noted) == 2 and driver.calls == []


class _StopsAfter(_Driver):
    """A driver during whose first piece of text the owner stops Dot: ``stop`` is called after that call."""

    def __init__(self, stop: Callable[[], None]) -> None:
        super().__init__()
        self._stop: Callable[[], None] = stop

    def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> CuaResult:
        result: CuaResult = super().call(tool, arguments)
        self._stop()
        return result


@pytest.mark.parametrize("how", ["stop file", "run stopped"])
def test_a_stop_during_a_long_text_ends_it_within_one_piece(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: list[object], how: str
) -> None:
    """A text goes to the driver in pieces with the emergency stop checked before each (item 683's promise for the
    Mac's own typing): the STOP file, or the run's stop, ends it after the piece under way."""
    monkeypatch.setattr(actions, "check_failsafe", _CHECK_FAILSAFE)
    monkeypatch.setattr(screen, "pointer_location", lambda: Point(500.0, 500.0))
    monkeypatch.setattr(actions, "_field_in", lambda env, ref, holder: None)
    stop: threading.Event = threading.Event()
    env: StepEnv = _env(tmp_path, True, None)

    def stops() -> None:
        if how == "stop file":
            env.failsafe_file.write_text("stop", encoding="utf-8")
        else:
            stop.set()

    driver: _StopsAfter = _StopsAfter(stops)
    text: str = "x" * (cua.TEXT_CHUNK_CHARS * 3 + 7)
    actions.set_call_stop(stop)
    try:
        with pytest.raises(FailSafeTriggered):
            actions.execute_step(
                replace(env, background=driver), Step(action="type", text=text), _inspection(_MAIL.pid)
            )
    finally:
        actions.set_call_stop(None)
    typed: list[str] = [str(arguments["text"]) for tool, arguments in driver.calls if tool == "type_text"]
    assert typed == [text[: cua.TEXT_CHUNK_CHARS]] and mac == []


def test_a_long_text_goes_whole_in_pieces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: list[object]) -> None:
    monkeypatch.setattr(actions, "_field_in", lambda env, ref, holder: None)
    driver: _Driver = _Driver()
    text: str = "ğüşiöç İI " * 25
    effect: actions.Effect = actions.execute_step(
        _env(tmp_path, True, driver), Step(action="type", text=text), _inspection(_MAIL.pid)
    )
    typed: list[str] = [str(arguments["text"]) for tool, arguments in driver.calls if tool == "type_text"]
    assert len(typed) == 3 and "".join(typed) == text and f"typed {len(text)} chars" in effect.detail


def test_a_menu_items_ref_is_refused_with_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: list[object]
) -> None:
    menu_item: ElementState = replace(_FIELD, role="AXMenuItem", label="Save")
    monkeypatch.setattr(actions, "_element_for", lambda env, raw: (9, "item"))
    monkeypatch.setattr(actions, "_state_of", lambda env, number, element: menu_item)
    driver: _Driver = _Driver()
    with pytest.raises(DesktopStepNotSent, match="in a menu"):
        actions.execute_step(_env(tmp_path, True, driver), Step(action="right_click", ref="a9"), _inspection(_MAIL.pid))
    assert driver.calls == []
