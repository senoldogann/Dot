"""What a desktop step touches, read again after the owner answered and before it goes (item 555), a click that finds
Dot's own window by the title accessibility gives it, a click on a control nobody could read, and the desktop that one
run holds while the owner decides, with no screen: the accessibility calls and the window list are replaced."""

import asyncio
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import actions, apps, ax, screen, session
from dotagent.desktop.actions import Effect, Inspection, StepEnv
from dotagent.desktop.ax import ElementState
from dotagent.desktop.safety import AppRole, StepContext, assess_step
from dotagent.desktop.session import DesktopSession, StepReview, no_browser_chrome
from dotagent.desktop.types import ActOutcome, AppInfo, DisplayInfo, Point, Rect, Step, WindowInfo
from dotagent.policy.elements import Element
from dotagent.policy.engine import auto_review
from dotagent.policy.types import ActionInfo

_CHROME: AppInfo = AppInfo(
    pid=500, name="Google Chrome", bundle_id="com.google.Chrome", bundle_path="", kind="regular", frontmost=True
)
_SCREEN: Rect = Rect(0, 0, 2000, 1200)


def _env(tmp_path: Path, app: AppInfo = _CHROME) -> StepEnv:
    return StepEnv(
        app=app,
        app_element="app-element",
        elements={},
        running=(app,),
        displays=(DisplayInfo(display_id=1, bounds=_SCREEN, main=True),),
        scale=1.0,
        source=object(),
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "STOP",
        expected_front=app.pid,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=lambda inspection, what: None,
    )


def _state(label: str, role: str = "AXButton") -> ElementState:
    return ElementState(role=role, subrole="", label=label, frame=None, enabled=True, focused=False, selected=False)


def _window(pid: int = 500, owner: str = "Google Chrome", title: str = "") -> WindowInfo:
    return WindowInfo(
        window_id=1, pid=pid, owner=owner, title=title, bounds=_SCREEN, layer=0, alpha=1.0, on_screen=True
    )


def _under_the_point(
    monkeypatch: pytest.MonkeyPatch, *, button: str, window_title: str | None, front_title: str
) -> None:
    """The point is on ``button`` of a window titled ``window_title`` (none: the control is in no window), Chrome's
    focused window is titled ``front_title``, and the window server names no window (no Screen Recording)."""
    titles: dict[object, str] = {"window": window_title or "", "front": front_title}
    monkeypatch.setattr(screen, "windows_on_screen", lambda: (_window(title=""),))
    monkeypatch.setattr(ax, "element_at", lambda point, timeout: "found")
    monkeypatch.setattr(ax, "pid_of", lambda element: 500)
    monkeypatch.setattr(ax, "enclosing", lambda element, pid, roles, hops: "control")
    monkeypatch.setattr(ax, "read_state", lambda element, pid: _state(button))
    monkeypatch.setattr(ax, "window_of", lambda element, pid: None if window_title is None else "window")
    monkeypatch.setattr(ax, "focused_window", lambda app, pid: "front")
    monkeypatch.setattr(ax, "title_of", lambda window, pid: titles[window])


@pytest.mark.parametrize(
    ("window_title", "front_title", "role"),
    [
        # The click is in Dot's window, which is behind the focused one: the focused window's title says nothing of it.
        ("Dot", "Weekly report", "dot"),
        ("(2) Plan the trip · Dot - Google Chrome", "Weekly report", "dot"),
        ("Weekly report", "Dot", "other"),
        ("Weekly report", "Weekly report", "other"),
        # A control that is in no window (a menu): the focused window is all there is to go by.
        (None, "Dot", "dot"),
        (None, "Weekly report", "other"),
    ],
)
def test_a_click_is_in_dots_window_by_the_title_of_the_window_under_it_whatever_macos_shows_of_the_screen(
    window_title: str | None, front_title: str, role: AppRole, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit's F30: without Screen Recording the window server gives no title, so the title was the focused
    window's, and an Approve in Dot's window behind it was a click on 'other'."""
    _under_the_point(monkeypatch, button="Approve", window_title=window_title, front_title=front_title)
    inspection: Inspection = actions.inspect_step(_env(tmp_path), Step(action="click", x=300, y=200), {})
    assert inspection.context.role == role
    refused: bool = (
        auto_review(_info(assess_step(Step(action="click", x=300, y=200), inspection.context, tmp_path, (), ())))
        is not None
    )
    assert refused is (role == "dot")


def _info(assessment: object) -> ActionInfo:
    risk = getattr(assessment, "risk")  # noqa: B009
    flags = getattr(assessment, "flags")  # noqa: B009
    return ActionInfo(
        tool="desktop_act", kind="desktop_input", risk=risk, summary="click", target="Chrome", flags=flags
    )


def test_the_title_the_window_server_gives_stands_when_accessibility_names_no_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _under_the_point(monkeypatch, button="Approve", window_title=None, front_title="Weekly report")
    monkeypatch.setattr(screen, "windows_on_screen", lambda: (_window(title="Settings · Dot"),))
    inspection: Inspection = actions.inspect_step(_env(tmp_path), Step(action="click", x=300, y=200), {})
    assert inspection.context.role == "dot"


def test_a_click_where_the_application_will_not_say_what_is_there_does_not_go_as_a_click_on_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second audit's finding: a hit test that failed was a click on no control, so a Pay or a Send under it was a
    plain click. Nothing under the point is still such a click: coordinates exist for what accessibility does not
    show."""
    monkeypatch.setattr(screen, "windows_on_screen", lambda: (_window(),))
    step: Step = Step(action="click", x=300, y=200)

    def busy(point: Point, timeout: float) -> object:
        raise DesktopError("cannot find the element under the pointer: cannot complete", {"code": -25204})

    monkeypatch.setattr(ax, "element_at", busy)
    with pytest.raises(DesktopError, match=r"did not say what is there.*desktop_snapshot"):
        actions.inspect_step(_env(tmp_path), step, {})
    monkeypatch.setattr(ax, "element_at", lambda point, timeout: None)
    monkeypatch.setattr(ax, "focused_window", lambda app, pid: None)
    inspection: Inspection = actions.inspect_step(_env(tmp_path), step, {})
    assert inspection.context.element is None

    # An application that does not implement accessibility, or has no value there, has nothing to say: the same.
    for code in (-25205, -25208, -25212):

        def silent(point: Point, timeout: float, code: int = code) -> object:
            raise DesktopError("cannot find the element under the pointer", {"code": code})

        monkeypatch.setattr(ax, "element_at", silent)
        assert actions.inspect_step(_env(tmp_path), step, {}).context.element is None, code


def _keyboard_in_a_sheet(
    monkeypatch: pytest.MonkeyPatch, *, default: str | None = "Pay now", top_level: bool = True
) -> list[str]:
    """The keyboard is in a card number field of Chrome's window, whose sheet (or, without ``top_level``, the window
    itself) has ``default`` for its default button; the attributes read are listed."""
    read: list[str] = []
    monkeypatch.setattr(apps, "frontmost_pid", lambda: 500)
    monkeypatch.setattr(ax, "focused_element", lambda app, pid: "field")
    monkeypatch.setattr(ax, "window_of", lambda element, pid: "window")
    monkeypatch.setattr(ax, "focused_window", lambda app, pid: "window")
    monkeypatch.setattr(ax, "title_of", lambda window, pid: "Checkout")
    holder: str = "sheet" if top_level else "window"
    attributes: dict[tuple[str, str], object] = {
        ("field", "AXTopLevelUIElement"): "sheet" if top_level else None,
        (holder, "AXDefaultButton"): "default-button" if default is not None else None,
    }

    def attribute(element: object, name: str, pid: int) -> object:
        read.append(name)
        return attributes.get((str(element), name))

    monkeypatch.setattr(ax, "read_optional", attribute)
    monkeypatch.setattr(
        ax,
        "read_state",
        lambda element, pid: (
            _state(default or "", "AXButton") if element == "default-button" else _state("Card number", "AXTextField")
        ),
    )
    return read


@pytest.mark.parametrize("top_level", [True, False])
def test_a_return_reads_the_default_button_of_the_window_or_sheet_the_keyboard_is_in(
    top_level: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second audit's finding: the default button of the dialog a Return goes to was never read, so a Pay it
    presses was judged as a key."""
    read: list[str] = _keyboard_in_a_sheet(monkeypatch, top_level=top_level)
    for step in (Step(action="key", key="return"), Step(action="key", key="enter"), Step(action="type", text="4111\n")):
        inspection: Inspection = actions.inspect_step(_env(tmp_path), step, {})
        assert inspection.context.default_button == Element(role="AXButton", name="Pay now"), step
        assessment = assess_step(step, inspection.context, tmp_path, (), ())
        assert "financial" in assessment.flags and assessment.risk == "high", step
    assert "AXDefaultButton" in read


@pytest.mark.parametrize(
    "step",
    [
        Step(action="key", key="tab"),
        Step(action="key", key="shift+return"),
        Step(action="key", key="space"),
        Step(action="type", text="4111"),
        Step(action="paste", text="4111\n"),
    ],
    ids=lambda step: f"{step.action} {step.key or step.text!r}",
)
def test_a_step_that_presses_no_plain_return_reads_no_default_button(
    step: Step, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[str] = _keyboard_in_a_sheet(monkeypatch)
    inspection: Inspection = actions.inspect_step(_env(tmp_path), step, {})
    assert inspection.context.default_button is None
    assert "AXDefaultButton" not in read


def test_a_window_with_no_default_button_has_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _keyboard_in_a_sheet(monkeypatch, default=None)
    inspection: Inspection = actions.inspect_step(_env(tmp_path), Step(action="key", key="return"), {})
    assert inspection.context.default_button is None
    assert assess_step(Step(action="key", key="return"), inspection.context, tmp_path, (), ()).flags == ()


def _reviewed(label: str = "Send", **changes: object) -> Inspection:
    context: StepContext = StepContext(
        role="other", app="Mail", element=Element(role="AXButton", name=label), secure=False, line=None
    )
    return Inspection(
        context=context.model_copy(update=changes), target=f'button "{label}" at (640, 400) in Mail', pid=70
    )


@pytest.mark.parametrize(
    ("fresh", "went"),
    [
        (_reviewed(), True),
        (_reviewed("Buy now"), False),
        (_reviewed("Pay €49"), False),
        (_reviewed(role="dot"), False),
        (_reviewed(app="Mail Helper"), False),
        (_reviewed(secure=True), False),
        (_reviewed(element=None), False),
        (_reviewed(default_button=Element(role="AXButton", name="Send")), False),
        (_reviewed(line="ls"), True),
        (Inspection(context=_reviewed().context, target=_reviewed().target, pid=71), False),
    ],
)
def test_a_step_that_would_touch_something_else_than_the_owner_approved_does_not_go(
    fresh: Inspection, went: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit's F29: a click approved as 'Send' went to whatever stood at its point after the wait, a 'Buy now'
    among it."""
    clicked: list[Step] = []
    monkeypatch.setattr(actions, "check_failsafe", lambda env: None)
    monkeypatch.setattr(actions, "inspect_step", lambda env, step, lines: fresh)
    monkeypatch.setattr(
        actions,
        "_click",
        lambda env, step, inspection, count: clicked.append(step) or Effect("clicked", None, True, None),
    )
    step: Step = Step(action="click", x=640, y=400)
    if went:
        actions.execute_step(_env(tmp_path), step, _reviewed())
        assert clicked == [step]
    else:
        with pytest.raises(DesktopError, match="screen changed after this step was reviewed"):
            actions.execute_step(_env(tmp_path), step, _reviewed())
        assert clicked == []


def _held_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[DesktopSession, list[Inspection]]:
    """A session whose acting runs on fakes: one click step, a review that the test holds, and a screen that never
    changes; ``executed`` lists the inspections the steps were performed for."""
    executed: list[Inspection] = []
    inspection: Inspection = _reviewed()
    monkeypatch.setattr(session.apps, "ensure_window_server", lambda: None)
    monkeypatch.setattr(DesktopSession, "_prepare_acting", lambda self: None)
    monkeypatch.setattr(DesktopSession, "_prepare_listing", lambda self: None)
    monkeypatch.setattr(DesktopSession, "_open_env", lambda self, state, name, steps, secure_field: _env(tmp_path))

    async def closing_view(
        self: DesktopSession, state: object, app: object, max_chars: int
    ) -> tuple[None, str, object]:
        return None, "no view", state

    monkeypatch.setattr(DesktopSession, "_closing_view", closing_view)
    monkeypatch.setattr(session, "_list_job", lambda config: ())
    monkeypatch.setattr(ax, "wait_until_quiet", lambda pid, quiet_s, max_s: None)
    monkeypatch.setattr(actions, "inspect_step", lambda env, step, lines: inspection)

    def execute(env: StepEnv, step: Step, reviewed: Inspection) -> Effect:
        executed.append(reviewed)
        return Effect("clicked", None, True, None)

    monkeypatch.setattr(actions, "execute_step", execute)
    return DesktopSession(load_default_config().desktop, tmp_path / "STOP", no_browser_chrome), executed


async def _act(held: DesktopSession, review: object) -> ActOutcome:
    return await held.act(
        "thread",
        "Mail",
        [Step(action="click", x=640, y=400)],
        review,
        lambda inspection, what: None,
        1000,  # type: ignore[arg-type]
    )


async def test_the_step_goes_for_what_the_review_judged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    held, executed = _held_session(tmp_path, monkeypatch)

    async def allow(review: StepReview) -> None:
        return None

    outcome: ActOutcome = await _act(held, allow)
    assert [report.outcome for report in outcome.steps] == ["done"]
    assert executed == [_reviewed()]


@pytest.mark.parametrize("queued_first", [False, True])
async def test_another_run_is_told_at_once_while_a_step_waits_for_the_owner(
    queued_first: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit's F32: a run that asked for the desktop while a step waited for the owner (up to fifteen minutes)
    queued until its tool timed out, and a batch's preview with no timeout at all waited as long. It is told why, one
    that was queued when the wait began as well."""
    monkeypatch.setattr(session, "_ASKING_GRACE_S", 0.0)
    monkeypatch.setattr(session, "_TURN_POLL_S", 0.01)
    held, executed = _held_session(tmp_path, monkeypatch)
    answered: asyncio.Event = asyncio.Event()
    asking: asyncio.Event = asyncio.Event()

    async def owner(review: StepReview) -> None:
        asking.set()
        await answered.wait()

    acting: asyncio.Task[ActOutcome] = asyncio.create_task(_act(held, owner))
    queued: asyncio.Task[object] | None = None
    try:
        if queued_first:
            # The run asks for the desktop while the batch still holds it and has asked nothing yet.
            await asyncio.sleep(0)
            queued = asyncio.create_task(held.list_apps())
            await asyncio.sleep(0.05)
            assert not queued.done() or queued.exception() is not None
        await asyncio.wait_for(asking.wait(), 5.0)
        if queued is not None:
            with pytest.raises(DesktopError, match="waiting for the owner's answer"):
                await asyncio.wait_for(queued, 5.0)
        with pytest.raises(DesktopError, match="waiting for the owner's answer"):
            await held.list_apps()
        with pytest.raises(DesktopError, match="waiting for the owner's answer"):
            await held.preview("other-thread", "Mail", [Step(action="click", x=1, y=1)])
        assert executed == []
    finally:
        answered.set()
    outcome: ActOutcome = await asyncio.wait_for(acting, 5.0)
    assert [report.outcome for report in outcome.steps] == ["done"]
    assert executed == [_reviewed()]
    # The answer given, the desktop is there for the next run.
    assert await held.list_apps() == ()


async def test_a_run_that_waits_for_a_batch_at_work_is_served_when_it_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch at work that asks nothing of the owner holds the desktop as before: the next run waits its turn."""
    monkeypatch.setattr(session, "_ASKING_GRACE_S", 0.0)
    monkeypatch.setattr(session, "_TURN_POLL_S", 0.01)
    held, executed = _held_session(tmp_path, monkeypatch)
    ending: asyncio.Event = asyncio.Event()
    closing: asyncio.Event = asyncio.Event()

    async def closing_view(
        self: DesktopSession, state: object, app: object, max_chars: int
    ) -> tuple[None, str, object]:
        closing.set()
        await ending.wait()
        return None, "no view", state

    monkeypatch.setattr(DesktopSession, "_closing_view", closing_view)

    async def allow(review: StepReview) -> None:
        return None

    first: asyncio.Task[ActOutcome] = asyncio.create_task(_act(held, allow))
    await asyncio.wait_for(closing.wait(), 5.0)
    second: asyncio.Task[object] = asyncio.create_task(held.list_apps())
    await asyncio.sleep(0.1)
    assert not second.done()
    ending.set()
    assert await asyncio.wait_for(second, 5.0) == ()
    assert [report.outcome for report in (await asyncio.wait_for(first, 5.0)).steps] == ["done"]
    assert executed == [_reviewed()]


@pytest.mark.parametrize(("answered_at_once_s", "second_expects"), [(60.0, Point(x=5.0, y=5.0)), (-1.0, None)])
async def test_an_answer_that_took_a_while_does_not_count_the_owners_mouse_as_someone_elses(
    answered_at_once_s: float, second_expects: Point | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code review of item 683: the owner who approves a step with the mouse moves the pointer; the check that no
    one else is at the mouse must not stop the step they approved. An answer that came at once keeps the check."""
    held, _ = _held_session(tmp_path, monkeypatch)
    seen: list[Point | None] = []

    def execute(env: StepEnv, step: Step, reviewed: Inspection) -> Effect:
        seen.append(env.expected_pointer)
        return Effect("clicked", Point(x=5.0, y=5.0), True, None)

    monkeypatch.setattr(actions, "execute_step", execute)
    monkeypatch.setattr(session, "_ANSWERED_AT_ONCE_S", answered_at_once_s)
    # Between two steps the session reads which application holds the keyboard.
    monkeypatch.setattr(apps, "frontmost_pid", lambda: 500)

    async def allow(review: StepReview) -> None:
        return None

    outcome: ActOutcome = await held.act(
        "thread",
        "Mail",
        [Step(action="click", x=640, y=400), Step(action="click", x=650, y=410)],
        allow,
        lambda inspection, what: None,
        1000,  # type: ignore[arg-type]
    )
    assert [report.outcome for report in outcome.steps] == ["done", "done"]
    assert seen == [None, second_expects]
