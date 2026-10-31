"""Three ideas from vercel-labs/agent-desktop in Dot's own desktop (item 862). A click on the ref of a button, a link, a
menu item or a box to tick is a press through accessibility: the pointer stays where the owner has it, and their front
window stays in front; an element that takes no press is clicked as before, any other click is a real one. A window's
skeleton lists its regions with what each holds. A failed call says whether the same call may be made again. The
accessibility calls and the events are replaced: nothing reaches the screen."""

from dataclasses import replace
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import actions, apps, ax, session
from dotagent.desktop import input as hid
from dotagent.desktop.actions import Inspection, StepEnv
from dotagent.desktop.ax import ElementState
from dotagent.desktop.safety import StepContext
from dotagent.desktop.types import AppInfo, DisplayInfo, Node, Point, Rect, Step

_MAIL: AppInfo = AppInfo(
    pid=700, name="Mail", bundle_id="com.apple.mail", bundle_path="", kind="regular", frontmost=False
)
_SCREEN: Rect = Rect(0, 0, 2000, 1200)
_UNSUPPORTED: int = -25206


def _env(tmp_path: Path) -> StepEnv:
    return StepEnv(
        app=_MAIL,
        app_element="app-element",
        elements={},
        running=(_MAIL,),
        displays=(DisplayInfo(display_id=1, bounds=_SCREEN, main=True),),
        scale=1.0,
        source=object(),
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "STOP",
        expected_front=None,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=lambda inspection, what: None,
    )


def _inspection() -> Inspection:
    return Inspection(
        target="button 'Archive'",
        context=StepContext(role="other", app="Mail", element=None, secure=False, line=None),
    )


class _Screen:
    """What the step did: the presses, the events posted and the applications brought forward."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, role: str, press_code: int | None = None) -> None:
        self.pressed: list[object] = []
        self.posted: list[object] = []
        self.activated: list[str] = []
        state = ElementState(
            role=role, subrole="", label="Archive", frame=None, enabled=True, focused=False, selected=False
        )
        monkeypatch.setattr(actions, "_element_for", lambda env, raw: (3, "element"))
        monkeypatch.setattr(actions, "_state_of", lambda env, number, element: state)
        monkeypatch.setattr(actions, "_point_for", lambda env, ref, x, y: Point(100.0, 100.0))
        monkeypatch.setattr(actions, "_check_corner", lambda env, point: None)

        def press(element: object, pid: int) -> None:
            if press_code is not None:
                raise DesktopError("the action is not supported", {"code": press_code, "pid": pid})
            self.pressed.append(element)

        monkeypatch.setattr(ax, "press", press)
        monkeypatch.setattr(hid, "post", self.posted.append)
        monkeypatch.setattr(hid, "left_click_events", lambda source, point, count, modifiers: ("click", point))
        monkeypatch.setattr(apps, "activate", lambda app, timeout: self.activated.append(app.name))
        # Mail is in front, as the batch brought it unless all its clicks were presses.
        self.front: int = _MAIL.pid
        monkeypatch.setattr(apps, "frontmost_pid", lambda: self.front)


@pytest.mark.parametrize("role", sorted(actions.PRESSED_ON_CLICK))
def test_a_click_on_the_ref_of_a_control_is_a_press_and_the_pointer_does_not_move(
    role: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _Screen(monkeypatch, role)
    effect = actions._click(_env(tmp_path), Step(action="click", ref="a3"), _inspection(), 1)
    assert seen.pressed == ["element"]
    assert seen.posted == [] and seen.activated == []
    assert effect.pointer is None and "the pointer did not move" in effect.detail


def test_a_press_through_accessibility_aims_dots_pointer_at_the_element_and_leaves_the_macs_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 869: the step says where Dot's own pointer is drawn, the element's press point, and still that the Mac's
    pointer did not move; an element whose frame cannot be read is drawn nowhere and the step is done all the same."""
    seen = _Screen(monkeypatch, "AXButton")
    monkeypatch.setattr(actions, "_visible_frame", lambda env, element, state: Rect(100, 200, 80, 30))
    clicked = actions._click(_env(tmp_path), Step(action="click", ref="a3"), _inspection(), 1)
    pressed = actions._press(_env(tmp_path), Step(action="press", ref="a3"))
    assert clicked.aim == Point(140.0, 215.0) == pressed.aim
    assert clicked.pointer is None and pressed.pointer is None
    assert seen.posted == [] and len(seen.pressed) == 2

    def unreadable(env: object, element: object, state: object) -> Rect:
        raise DesktopError("the element is gone", {})

    monkeypatch.setattr(actions, "_visible_frame", unreadable)
    assert actions._press(_env(tmp_path), Step(action="press", ref="a3")).aim is None
    assert len(seen.pressed) == 3
    # With the pointer off, nothing is read for it.
    off: StepEnv = replace(_env(tmp_path), config=_env(tmp_path).config.model_copy(update={"show_pointer": False}))
    monkeypatch.setattr(actions, "_visible_frame", lambda env, element, state: Rect(100, 200, 80, 30))
    assert actions._press(off, Step(action="press", ref="a3")).aim is None


def test_a_control_that_takes_no_press_is_clicked_with_its_window_in_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _Screen(monkeypatch, "AXButton", press_code=_UNSUPPORTED)
    effect = actions._click(_env(tmp_path), Step(action="click", ref="a3"), _inspection(), 1)
    assert seen.activated == ["Mail"]
    assert seen.posted == [("click", Point(100.0, 100.0))]
    assert effect.pointer == Point(100.0, 100.0)


def test_a_press_that_fails_otherwise_fails_the_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Screen(monkeypatch, "AXButton", press_code=-25202)
    with pytest.raises(DesktopError):
        actions._click(_env(tmp_path), Step(action="click", ref="a3"), _inspection(), 1)


@pytest.mark.parametrize(
    "step",
    [
        Step(action="click", ref="a3", modifiers=["cmd"]),
        Step(action="click", x=100.0, y=100.0),
    ],
    ids=["a key held", "a point"],
)
def test_a_click_with_a_key_held_or_on_a_point_is_a_real_one(
    step: Step, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _Screen(monkeypatch, "AXButton")
    actions._click(_env(tmp_path), step, _inspection(), 1)
    assert seen.pressed == [] and len(seen.posted) == 1


@pytest.mark.parametrize("role", ["AXTextField", "AXCell", "AXRow", "AXStaticText", "AXImage"])
def test_a_click_on_a_field_a_row_or_a_text_is_a_real_one(
    role: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _Screen(monkeypatch, role)
    actions._click(_env(tmp_path), Step(action="click", ref="a3"), _inspection(), 1)
    assert seen.pressed == [] and len(seen.posted) == 1


def test_a_double_click_is_a_real_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _Screen(monkeypatch, "AXButton")
    actions._click(_env(tmp_path), Step(action="double_click", ref="a3"), _inspection(), 2)
    assert seen.pressed == [] and len(seen.posted) == 1


def _node(ref: int, role: str, enabled: bool = True) -> Node:
    return Node(
        ref=ref,
        role=role,
        subrole="",
        label="x",
        value="",
        frame=None,
        enabled=enabled,
        focused=False,
        selected=False,
        indent=0,
    )


def test_a_batch_of_presses_and_clicks_that_are_presses_leaves_the_owners_window_in_front() -> None:
    known = {3: _node(3, "AXButton"), 4: _node(4, "AXTextField"), 5: _node(5, "AXButton", enabled=False)}
    assert not session._needs_front([Step(action="click", ref="a3"), Step(action="press", ref="a3")], known)
    # A click on a field, on a disabled button, on an element no view showed, or a key, needs it in front.
    assert session._needs_front([Step(action="click", ref="a3"), Step(action="click", ref="a4")], known)
    assert session._needs_front([Step(action="click", ref="a5")], known)
    assert session._needs_front([Step(action="click", ref="a9")], known)
    assert session._needs_front([Step(action="click", ref="a3"), Step(action="key", key="return")], known)


@pytest.mark.parametrize(
    ("code", "acting", "says"),
    [
        (-25202, False, "take a new desktop_snapshot"),
        (-25204, False, "safe to retry after a moment"),
        (-25204, True, "not safe to retry at once: the step may have been done"),
        (-25206, True, "nothing was done, and retrying fails the same way"),
        (-25200, True, None),
    ],
)
def test_a_failed_call_says_whether_it_may_be_made_again(code: int, acting: bool, says: str | None) -> None:
    """After agent-desktop's retry dispositions: a read changed nothing; an action that got no answer may
    have been done, so it is not retried blind."""
    error: DesktopError = ax.failure(code, "AXPress failed", 700, "AXPress", acting=acting)
    if says is None:
        assert "retry" not in error.context
    else:
        assert says in str(error) and says in str(error.context["retry"])


def _region(ref: int, role: str, indent: int) -> Node:
    return Node(
        ref=ref,
        role=role,
        subrole="",
        label="",
        value="",
        frame=None,
        enabled=True,
        focused=False,
        selected=False,
        indent=indent,
    )


def test_the_skeleton_shows_the_regions_with_what_each_holds_and_leaves_their_items_out() -> None:
    """After agent-desktop's skeleton: a dense window's map, each region with a ref to read it by."""
    from dotagent.desktop.types import AppView, Snapshot
    from dotagent.desktop.views import ViewRequest, build_view

    nodes = (
        _region(1, "AXWindow", 0),
        _region(2, "AXOutline", 1),
        _region(3, "AXRow", 2),
        _region(4, "AXStaticText", 3),
        _region(5, "AXRow", 2),
        _region(6, "AXButton", 3),
        _region(7, "AXToolbar", 1),
        _region(8, "AXButton", 2),
        _region(9, "AXButton", 2),
    )
    snapshot = Snapshot(pid=700, nodes=nodes, elements={}, next_ref=10, truncated=False, elapsed_ms=0.0, skipped={})
    app = AppView(pid=700, name="Mail", bundle_id=None, frontmost=False, windows=())
    request = ViewRequest("window", None, None, True, 20000, skeleton=True)
    view = build_view(app, "Mail", snapshot, request, None, 1.0, ()).view
    lines = view.body.splitlines()
    assert len(lines) == 3, lines
    assert "[a1]" in lines[0] and "holds 8, 3 to act on" in lines[0]
    assert "[a2]" in lines[1] and "holds 4, 1 to act on" in lines[1]
    assert "[a7]" in lines[2] and "holds 2, 2 to act on" in lines[2]
    assert "skeleton" in view.heading


def test_a_control_disabled_since_the_last_view_is_clicked_with_its_window_brought_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-push review of item 862: the last view showed an enabled button, so the batch left the owner's window in
    front; by the step it is disabled, takes no press, and the real click needs its window in front."""
    seen = _Screen(monkeypatch, "AXButton")
    monkeypatch.setattr(
        actions,
        "_state_of",
        lambda env, number, element: ElementState(
            role="AXButton", subrole="", label="Archive", frame=None, enabled=False, focused=False, selected=False
        ),
    )
    seen.front = 1
    actions._click(_env(tmp_path), Step(action="click", ref="a3"), _inspection(), 1)
    assert seen.pressed == [] and seen.activated == ["Mail"] and len(seen.posted) == 1
