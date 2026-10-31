"""Desktop steps on the worker thread: what a step touches, then the step itself.

Everything here blocks (accessibility calls, CGEvent posting) and runs on the session's single worker thread.
``inspect_step`` only reads. ``execute_step`` re-reads everything it depends on, because a human approval may have taken
minutes between the two, refuses a step whose target is no longer the one that was reviewed, and is the only function
that moves the pointer or presses keys.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from math import hypot
from pathlib import Path

from dotagent.config import DesktopConfig
from dotagent.core.errors import DesktopError, DesktopPermissionError, FailSafeTriggered
from dotagent.core.logs import get_logger
from dotagent.desktop import apps, ax, screen
from dotagent.desktop import input as hid
from dotagent.desktop.ax import ElementState
from dotagent.desktop.input import EventSource, parse_chord
from dotagent.desktop.safety import DESKTOP_CLICKABLE_ROLES, AppRole, StepContext, enters_text, step_role
from dotagent.desktop.types import (
    DEFAULT_SCROLL_PX,
    AppInfo,
    AXElement,
    DisplayInfo,
    Point,
    Rect,
    Step,
    parse_ref,
)
from dotagent.policy.elements import Element, typing_reasons

_log = get_logger("dotagent.desktop.actions")

_CORNER_PT: float = 2.0
_DRIFT_PT: float = 12.0
_HIT_TEST_HOPS: int = 4
_MENU_ROLES: frozenset[str] = frozenset({"AXMenu", "AXMenuItem", "AXMenuBar", "AXMenuBarItem"})
_TOGGLE_ROLES: frozenset[str] = frozenset({"AXCheckBox", "AXRadioButton"})
_WIDE_TOGGLE_PT: float = 48.0
_TOGGLE_BOX_PT: float = 10.0
_ACTION_UNSUPPORTED: frozenset[int] = frozenset({-25206, -25208})
# What an application answers when it has nothing to say about a point, which is not the same as not answering: it has
# no such attribute, does not implement accessibility, or gives no value. A click there is a click on nothing, since
# coordinates are for what accessibility does not show (item 555).
_SAYS_NOTHING: frozenset[int] = frozenset({-25205, -25208, -25212})
_WHEEL_CHUNK_PX: int = 400
_DRAG_PX_PER_MOVE: float = 12.0
_MIN_DRAG_MOVES: int = 8
_MAX_DRAG_MOVES: int = 40
_HOVER_HOLD_S: float = 0.15
_FOCUS_SETTLE_S: float = 0.05
_LABEL_CLIP: int = 50
_SCROLL_UNITS: dict[str, tuple[int, int]] = {"down": (0, -1), "up": (0, 1), "right": (-1, 0), "left": (1, 0)}
_NO_APP: AppInfo = AppInfo(
    pid=0, name="the desktop", bundle_id=None, bundle_path="", kind="background", frontmost=False
)


@dataclass(frozen=True, slots=True)
class Inspection:
    """What a reviewer needs to judge a step: its context, and a short description of what it touches. ``pid`` is the
    application it touches (0 for none), which a step approved after a wait is compared against (item 555)."""

    context: StepContext
    target: str
    pid: int = 0


# What a step does at a secure text field, on the worker thread: called with the step's inspection and what the step
# does ("typing", "pasting", "setting a value"), it returns to let the text in and raises to refuse.
type SecureFieldCheck = Callable[[Inspection, str], None]


def refuse_secure_field(inspection: Inspection, what: str) -> None:
    """The secure-field check of every mode but yolo (item 429): nothing goes into a secure text field."""
    raise DesktopError(
        f"{what} into a secure text field is refused in this mode: a password or a code is the user's here. "
        "Ask the user to type it themselves, or go on without it",
        {"target": inspection.target},
    )


def enter_secure_field(_inspection: Inspection, _what: str) -> None:
    """The check a yolo run keeps at a secure text field (item 443): the text goes in. A yolo run asks nothing, and a
    password or a code it was given — or read from the mail that carried it — is the run's to type."""


@dataclass(frozen=True, slots=True)
class StepEnv:
    """What the worker thread needs to resolve and perform the steps of one batch.

    ``expected_front`` is the application that held the keyboard focus after the previous step and ``expected_pointer``
    where the previous pointer step left the pointer; a difference means that something else interfered.
    ``secure_field`` decides what a step does at a secure text field.
    """

    app: AppInfo
    app_element: AXElement
    elements: Mapping[int, AXElement]
    running: tuple[AppInfo, ...]
    displays: tuple[DisplayInfo, ...]
    scale: float
    source: EventSource
    config: DesktopConfig
    failsafe_file: Path
    expected_front: int | None
    expected_pointer: Point | None
    # The Chromes Dot's browser works in, which a name that fits several applications means (item 343).
    preferred: frozenset[int]
    secure_field: SecureFieldCheck


@dataclass(frozen=True, slots=True)
class Target:
    """What a step acts upon: the control (if one was found), the application it belongs to and the title of the
    window it is in (``None`` when there is none); for a step that presses Return, the default button of that window."""

    ref: int | None
    state: ElementState | None
    owner: AppInfo
    point: Point | None
    window: str | None
    default_button: Element | None = None


@dataclass(frozen=True, slots=True)
class Effect:
    """What a step did: the sentence for the report, where it left the pointer, whether the UI needs time to react."""

    detail: str
    pointer: Point | None
    opens_ui: bool
    switched_to: AppInfo | None


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _named(state: ElementState | None) -> str:
    if state is None:
        return "the point"
    role: str = "secure field" if state.secure() else state.role.removeprefix("AX").lower()
    return f'{role} "{_clip(state.label, _LABEL_CLIP)}"' if state.label else role


def _where(target: Target, scale: float) -> str:
    place: str = ""
    if target.point is not None and target.ref is None:
        place = f" at ({round(target.point.x * scale)}, {round(target.point.y * scale)})"
    ref: str = f" a{target.ref}" if target.ref is not None else ""
    return f"{_named(target.state)}{ref}{place} in {target.owner.name}"


def _in_corner(point: Point) -> bool:
    return point.x <= _CORNER_PT and point.y <= _CORNER_PT


def check_stop(config: DesktopConfig, failsafe_file: Path) -> None:
    """Stop everything when the user asked for it: the STOP file exists or the pointer sits in the top-left corner."""
    if failsafe_file.exists():
        raise FailSafeTriggered(
            f"the file {failsafe_file} exists; delete it to allow desktop control again",
            {"file": str(failsafe_file)},
        )
    if config.failsafe_corner and _in_corner(screen.pointer_location()):
        raise FailSafeTriggered("the pointer is in the top-left corner of the screen", {})


# The stop of the call the desktop's worker thread is running (item 747): set when the run that made the call stopped
# or its tool timed out, which cancels the call's coroutine but not the thread, so that a long text typed key by key, a
# drag or a scroll stops there instead of going on into whatever has the keyboard.
_call_stop: threading.local = threading.local()


def set_call_stop(stop: threading.Event | None) -> None:
    """Name the stop of the call the current (worker) thread runs; ``None`` once it is done."""
    _call_stop.event = stop


def check_failsafe(env: StepEnv) -> None:
    check_stop(env.config, env.failsafe_file)
    stop: threading.Event | None = getattr(_call_stop, "event", None)
    if stop is not None and stop.is_set():
        raise FailSafeTriggered("the run stopped, or its step ran out of time, while the step was under way", {})


def check_drift(env: StepEnv) -> None:
    """Refuse to go on when the pointer is not where the previous step left it: someone else is using the mouse."""
    expected: Point | None = env.expected_pointer
    if expected is None:
        return
    actual: Point = screen.pointer_location()
    if hypot(actual.x - expected.x, actual.y - expected.y) > _DRIFT_PT:
        raise DesktopError(
            "the pointer is no longer where the previous step left it, so someone else may be using the mouse; "
            "the remaining steps were not run",
            {
                "expected": f"({expected.x:.0f}, {expected.y:.0f})",
                "actual": f"({actual.x:.0f}, {actual.y:.0f})",
            },
        )


def _check_corner(env: StepEnv, point: Point) -> None:
    if env.config.failsafe_corner and _in_corner(point):
        raise DesktopError(
            "the target is in the top-left corner of the screen, which is the emergency-stop corner and is never "
            "clicked",
            {"x": round(point.x * env.scale), "y": round(point.y * env.scale)},
        )


def _required(value: str | None, what: str) -> str:
    if value is None:
        raise DesktopError(f"the step has no {what}", {})
    return value


def _app_for(env: StepEnv, pid: int, window_owner: str) -> AppInfo:
    known: AppInfo | None = next((app for app in env.running if app.pid == pid), None)
    if known is not None:
        return known
    return AppInfo(
        pid=pid, name=window_owner or f"pid {pid}", bundle_id=None, bundle_path="", kind="accessory", frontmost=False
    )


def _ref_number(raw: str) -> int:
    try:
        return parse_ref(raw)
    except ValueError as error:
        raise DesktopError(str(error), {"ref": raw}) from error


def _element_for(env: StepEnv, raw: str) -> tuple[int, AXElement]:
    number: int = _ref_number(raw)
    element: AXElement | None = env.elements.get(number)
    if element is None:
        raise DesktopError(
            f"a{number} is not an element of {env.app.name} that desktop_snapshot has shown; take a new snapshot of "
            "that application (pass app when it is not the one you were working in)",
            {"ref": raw, "pid": env.app.pid},
        )
    return number, element


def _state_of(env: StepEnv, number: int, element: AXElement) -> ElementState:
    try:
        return ax.read_state(element, env.app.pid)
    except DesktopPermissionError:
        raise
    except DesktopError as error:
        raise DesktopError(
            f"a{number} cannot be read any more ({error.message}); take a new snapshot",
            {**error.context, "ref": f"a{number}"},
        ) from error


def _window_title(element: AXElement | None, app: AXElement, pid: int) -> str | None:
    """The title of the window a step acts in: the one ``element`` is in, else the focused window of the application;
    ``None`` when the application has no window."""
    window: AXElement | None = None if element is None else ax.window_of(element, pid)
    if window is None:
        window = ax.focused_window(app, pid)
    return None if window is None else ax.title_of(window, pid)


def _default_button(element: AXElement | None, app: AXElement, pid: int) -> Element | None:
    """The button Return presses in the window or sheet ``element`` is in (item 555), as the control it is."""
    state: ElementState | None = ax.default_button_of(element, app, pid)
    return None if state is None else Element(role=state.role, name=state.label)


def _app_window_title(env: StepEnv, owner: AppInfo) -> str | None:
    """The title of the focused window of ``owner``; ``None`` for the desktop itself."""
    if owner.pid == _NO_APP.pid:
        return None
    root: AXElement = (
        env.app_element if owner.pid == env.app.pid else ax.app_element(owner.pid, env.config.ax_timeout_s)
    )
    return _window_title(None, root, owner.pid)


def _peek_ref(env: StepEnv, raw: str, presses_return: bool = False) -> Target:
    number, element = _element_for(env, raw)
    return Target(
        ref=number,
        state=_state_of(env, number, element),
        owner=env.app,
        point=None,
        window=_window_title(element, env.app_element, env.app.pid),
        default_button=_default_button(element, env.app_element, env.app.pid) if presses_return else None,
    )


def _to_points(env: StepEnv, x: float, y: float) -> Point:
    """The point of the screen a model coordinate stands for. A screenshot shows the main display only, so a coordinate
    stands for a point of that display: one that falls on another display, or on none, is refused (item 751)."""
    point: Point = Point(x / env.scale, y / env.scale)
    shown: tuple[DisplayInfo, ...] = tuple(display for display in env.displays if display.main) or env.displays
    if not any(display.bounds.contains(point) for display in shown):
        raise DesktopError(
            f"({x:g}, {y:g}) is outside the screen a screenshot shows (the main display); "
            "take a desktop_screenshot to see the coordinates",
            {"x": x, "y": y},
        )
    return point


@dataclass(frozen=True, slots=True)
class _Hit:
    """What accessibility finds under a point: the control, the application it belongs to and the title of its window.
    ``failed`` is why the application would not say, which is not the same as there being nothing there (``state`` and
    ``pid`` are then ``None`` as well)."""

    state: ElementState | None
    pid: int | None
    title: str | None
    failed: str | None = None


def _hit_test(env: StepEnv, point: Point) -> _Hit:
    """The control under a point, the application it belongs to and the title of its window (item 555)."""
    try:
        found: AXElement | None = ax.element_at(point, env.config.ax_timeout_s)
        if found is None:
            return _Hit(None, None, None)
        pid: int = ax.pid_of(found)
        control: AXElement = ax.enclosing(found, pid, DESKTOP_CLICKABLE_ROLES, _HIT_TEST_HOPS)
        window: AXElement | None = ax.window_of(found, pid)
        title: str | None = None if window is None else ax.title_of(window, pid)
        return _Hit(ax.read_state(control, pid), pid, title)
    except DesktopPermissionError:
        raise
    except DesktopError as error:
        _log.warning("hit_test_failed", x=point.x, y=point.y, error=error.message, **error.context)
        if error.context.get("code") in _SAYS_NOTHING:
            return _Hit(None, None, None)
        return _Hit(None, None, None, error.message)


def _peek_point(env: StepEnv, x: float, y: float) -> Target:
    point: Point = _to_points(env, x, y)
    window = screen.window_at(point, screen.windows_on_screen())
    hit: _Hit = _hit_test(env, point)
    if hit.failed is not None:
        # A click on a control nobody read could be on a Pay, a Send or a Delete, or in Dot's own window: it does not
        # go as a click on nothing (item 555), as a ref that cannot be read does not either.
        raise DesktopError(
            f"the application under ({x:g}, {y:g}) did not say what is there ({hit.failed}), so nothing was done; take "
            "a new desktop_snapshot and press or click by ref, or try again",
            {"x": x, "y": y},
        )
    if hit.pid is not None:
        owner: AppInfo = _app_for(env, hit.pid, window.owner if window is not None and window.pid == hit.pid else "")
    elif window is not None:
        owner = _app_for(env, window.pid, window.owner)
    else:
        owner = _NO_APP
    # The title of the window the control is in, from accessibility, which gives it whether or not macOS lets Dot see
    # the screen; else the window server's, when the window is the owner's (it is empty without Screen Recording); else
    # the owner's focused window, which need not be the one under the point (item 555).
    title: str | None = hit.title
    if not title and window is not None and window.pid == owner.pid:
        title = window.title
    return Target(ref=None, state=hit.state, owner=owner, point=point, window=title or _app_window_title(env, owner))


def _window_frame(env: StepEnv) -> Rect:
    window: AXElement | None = ax.focused_window(env.app_element, env.app.pid)
    frame: Rect | None = None if window is None else ax.frame_of(window, env.app.pid)
    if frame is None:
        raise DesktopError(f"{env.app.name} has no window to scroll in; give a ref or x and y", {"pid": env.app.pid})
    return frame


def _peek(env: StepEnv, ref: str | None, x: float | None, y: float | None) -> Target:
    if ref is not None:
        return _peek_ref(env, ref)
    if x is not None and y is not None:
        return _peek_point(env, x, y)
    return Target(
        ref=None,
        state=None,
        owner=env.app,
        point=_window_frame(env).center(),
        window=_app_window_title(env, env.app),
    )


def _context(target: Target, lines: Mapping[str, str]) -> StepContext:
    state: ElementState | None = target.state
    role: AppRole = step_role(
        target.owner.name, target.owner.bundle_id, target.window, None if state is None else state.label
    )
    return StepContext(
        role=role,
        app=target.owner.name,
        element=None if state is None else Element(role=state.role, name=state.label),
        secure=state is not None and state.secure(),
        line=lines.get(target.owner.name) if role == "terminal" else None,
        default_button=target.default_button,
    )


def _inspection(env: StepEnv, lines: Mapping[str, str], target: Target) -> Inspection:
    return Inspection(context=_context(target, lines), target=_where(target, env.scale), pid=target.owner.pid)


def _keyboard_target(env: StepEnv, presses_return: bool = False) -> Target:
    """The focused field of the application that has the keyboard, provided that is the one the steps expect; for a
    step that presses Return, with the default button of its window."""
    pid: int | None = apps.frontmost_pid()
    if pid is None:
        raise DesktopError("no application has the keyboard focus right now; activate one first", {})
    if env.expected_front is not None and pid != env.expected_front:
        taker: AppInfo = _app_for(env, pid, "")
        raise DesktopError(
            f"{taker.name} has the keyboard focus, not the application the previous step left in front; "
            "the keys would go to the wrong place, so nothing was typed. Use activate to choose where the keys go",
            {"pid": pid, "expected_pid": env.expected_front},
        )
    owner: AppInfo = env.app if pid == env.app.pid else _app_for(env, pid, "")
    root: AXElement = env.app_element if pid == env.app.pid else ax.app_element(pid, env.config.ax_timeout_s)
    focused: AXElement | None = ax.focused_element(root, pid)
    state: ElementState | None = None if focused is None else ax.read_state(focused, pid)
    return Target(
        ref=None,
        state=state,
        owner=owner,
        point=None,
        window=_window_title(focused, root, pid),
        default_button=_default_button(focused, root, pid) if presses_return else None,
    )


def _require_keyboard(env: StepEnv) -> None:
    """Refuse to send keys unless the application of the step's ref holds the keyboard, as the previous step left it:
    keys go to the frontmost application, whichever control a step focused in another (the code review's Y2)."""
    pid: int | None = apps.frontmost_pid()
    if pid != env.app.pid:
        holder: str = "No application" if pid is None else _app_for(env, pid, "").name
        raise DesktopError(
            f"{holder} has the keyboard focus, not {env.app.name}, whose control the step names; the keys would go "
            f"to the wrong place, so nothing was typed. Use activate to bring {env.app.name} to the front",
            {"pid": pid, "expected_pid": env.app.pid},
        )
    if env.expected_front is not None and pid != env.expected_front:
        raise DesktopError(
            f"{env.app.name} took the keyboard focus after the previous step left it with another application, so "
            "someone else may be using the Mac; nothing was typed",
            {"pid": pid, "expected_pid": env.expected_front},
        )


def refuse_secrets(context: StepContext) -> None:
    """Refuse, in every approval mode, a step that touches an application holding passwords or keys: such an
    application is never controlled."""
    if context.role == "secrets":
        raise DesktopError(f"{context.app} holds passwords or keys; it is never controlled", {"app": context.app})


def _presses_return(step: Step) -> bool:
    """Whether a step presses Return with no modifier, the key or a line break typed: it presses the default button of
    the window the keyboard is in (item 555)."""
    if step.action == "type":
        text: str = step.text or ""
        return "\n" in text or "\r" in text
    if step.action == "key" and step.key is not None:
        chord = parse_chord(step.key)
        return chord.key == "return" and not chord.modifiers
    return False


def inspect_step(env: StepEnv, step: Step, lines: Mapping[str, str]) -> Inspection:
    """What the step touches, read from the machine right now: the application, the control and whether it is secret;
    ``lines`` holds the command line of each terminal application as far as Dot typed it."""
    presses_return: bool = _presses_return(step)
    match step.action:
        case "click" | "double_click" | "right_click" | "hover" | "scroll":
            return _inspection(env, lines, _peek(env, step.ref, step.x, step.y))
        case "drag":
            return _inspection(env, lines, _peek(env, step.ref, step.x, step.y))
        case "press" | "focus" | "set_value":
            return _inspection(env, lines, _peek_ref(env, _required(step.ref, "ref")))
        case "type" | "paste":
            if step.ref is not None:
                return _inspection(env, lines, _peek_ref(env, step.ref, presses_return))
            return _inspection(env, lines, _keyboard_target(env, presses_return))
        case "key":
            return _inspection(env, lines, _keyboard_target(env, presses_return))
        case "menu" | "wait":
            return _inspection(
                env,
                lines,
                Target(ref=None, state=None, owner=env.app, point=None, window=_app_window_title(env, env.app)),
            )
        case "activate":
            named: AppInfo | None = apps.find_app(
                _required(step.text, "application"), env.running, env.preferred, env.app.pid
            )
            owner: AppInfo = named or env.app
            return _inspection(
                env, lines, Target(ref=None, state=None, owner=owner, point=None, window=_app_window_title(env, owner))
            )


def _window_frame_of(env: StepEnv, element: AXElement) -> Rect | None:
    window: object | None = ax.read_optional(element, "AXWindow", env.app.pid)
    return None if window is None else ax.frame_of(window, env.app.pid)


def _visible_frame(env: StepEnv, element: AXElement, state: ElementState) -> Rect | None:
    """The frame of an element when its centre can be clicked: on a display and inside its window (menus excepted)."""
    frame: Rect | None = state.frame
    if frame is None or min(frame.width, frame.height) <= 1:
        return None
    centre: Point = frame.center()
    if not any(display.bounds.contains(centre) for display in env.displays):
        return None
    if state.role in _MENU_ROLES:
        return frame
    window: Rect | None = _window_frame_of(env, element)
    if window is not None and not window.contains(centre):
        return None
    return frame


def _bring_into_view(env: StepEnv, number: int, element: AXElement) -> Rect:
    state: ElementState = _state_of(env, number, element)
    frame: Rect | None = _visible_frame(env, element, state)
    if frame is not None:
        return frame
    try:
        ax.show_in_view(element, env.app.pid)
    except DesktopError as error:
        raise DesktopError(
            f"a{number} is outside the visible part of its window and cannot be scrolled into view ({error.message})",
            {**error.context, "ref": f"a{number}"},
        ) from error
    frame = _visible_frame(env, element, _state_of(env, number, element))
    if frame is None:
        raise DesktopError(
            f"a{number} is not visible on the screen (scrolled away, hidden or in a minimized window); "
            "scroll to it or take a new snapshot",
            {"ref": f"a{number}"},
        )
    return frame


def _check_uncovered(env: StepEnv, number: int, state: ElementState, point: Point) -> None:
    if state.role in _MENU_ROLES:
        return
    pid: int | None = _hit_test(env, point).pid
    if pid is not None and pid != env.app.pid:
        covering: AppInfo = _app_for(env, pid, "")
        raise DesktopError(
            f"a{number} is covered by a window of {covering.name}; close or move it, or activate {env.app.name} first",
            {"ref": f"a{number}", "covering_pid": pid},
        )


def _press_point(frame: Rect, state: ElementState) -> Point:
    """Where an element is pressed: its centre, except for a wide checkbox or radio button, whose empty trailing space
    ignores clicks, so the box at its leading edge is pressed instead."""
    centre: Point = frame.center()
    if state.role in _TOGGLE_ROLES and frame.width > _WIDE_TOGGLE_PT:
        return Point(frame.x + _TOGGLE_BOX_PT, centre.y)
    return centre


def _point_for(env: StepEnv, ref: str | None, x: float | None, y: float | None) -> Point:
    """Where the pointer goes for a step: the press point of a ref's fresh frame, the given point, or the window
    centre."""
    if ref is not None:
        number, element = _element_for(env, ref)
        frame: Rect = _bring_into_view(env, number, element)
        state: ElementState = _state_of(env, number, element)
        point: Point = _press_point(frame, state)
        _check_uncovered(env, number, state, point)
        return point
    if x is not None and y is not None:
        return _to_points(env, x, y)
    return _window_frame(env).center()


def _check_secure(env: StepEnv, inspection: Inspection, what: str) -> None:
    if inspection.context.secure:
        env.secure_field(inspection, what)


def _focus(env: StepEnv, raw: str) -> None:
    number, element = _element_for(env, raw)
    ax.set_attribute(element, "AXFocused", True, env.app.pid)
    time.sleep(_FOCUS_SETTLE_S)
    if not _state_of(env, number, element).focused:
        raise DesktopError(
            f"a{number} did not take the keyboard focus; click it first", {"ref": f"a{number}", "pid": env.app.pid}
        )


def _effect(detail: str, pointer: Point | None, opens_ui: bool) -> Effect:
    return Effect(detail=detail, pointer=pointer, opens_ui=opens_ui, switched_to=None)


# The roles a click on whose ref is a press through accessibility (item 862, after vercel-labs/agent-desktop): the
# element does what a click does, with the pointer left where the owner has it and their front window left in front.
PRESSED_ON_CLICK: frozenset[str] = frozenset(
    {
        "AXButton",
        "AXCheckBox",
        "AXRadioButton",
        "AXMenuItem",
        "AXMenuBarItem",
        "AXMenuButton",
        "AXPopUpButton",
        "AXLink",
        "AXDisclosureTriangle",
    }
)


def pressed_on_click(step: Step, role: str | None, enabled: bool) -> bool:
    """Whether ``step`` is a plain click on the ref of an enabled element of :data:`PRESSED_ON_CLICK`: one click,
    with no key held, which is done as a press."""
    return (
        step.action == "click"
        and step.ref is not None
        and not step.modifiers
        and enabled
        and role is not None
        and role in PRESSED_ON_CLICK
    )


def _click(env: StepEnv, step: Step, inspection: Inspection, count: int) -> Effect:
    if count == 1 and step.ref is not None:
        number, element = _element_for(env, step.ref)
        state: ElementState = _state_of(env, number, element)
        if pressed_on_click(step, state.role, state.enabled):
            try:
                ax.press(element, env.app.pid)
            except DesktopError as error:
                code: object = error.context.get("code")
                if not (isinstance(code, int) and code in _ACTION_UNSUPPORTED):
                    raise
                # It takes no press: the click is a real one, with its window in front, as any click is.
                apps.activate(env.app, env.config.activate_timeout_s)
            else:
                return _effect(
                    f"clicked {inspection.target} (through accessibility: the pointer did not move)", None, True
                )
        elif apps.frontmost_pid() != env.app.pid:
            # The last view showed a control the batch would press, so its window was left behind; it is no longer
            # one (disabled meanwhile): a real click goes to its window in front, as every other click does.
            apps.activate(env.app, env.config.activate_timeout_s)
    point: Point = _point_for(env, step.ref, step.x, step.y)
    _check_corner(env, point)
    hid.post(hid.left_click_events(env.source, point, count, step.modifiers or ()))
    verb: str = "clicked" if count == 1 else "double-clicked"
    return _effect(f"{verb} {inspection.target}", point, True)


def _right_click(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    point: Point = _point_for(env, step.ref, step.x, step.y)
    _check_corner(env, point)
    hid.post(hid.right_click_events(env.source, point, step.modifiers or ()))
    return _effect(f"right-clicked {inspection.target}", point, True)


def _hover(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    if step.modifiers:
        raise DesktopError("hover does not take modifiers", {})
    point: Point = _point_for(env, step.ref, step.x, step.y)
    _check_corner(env, point)
    hid.post(hid.move_events(env.source, point, _HOVER_HOLD_S))
    return _effect(f"moved the pointer over {inspection.target}", point, False)


def _press(env: StepEnv, step: Step) -> Effect:
    number, element = _element_for(env, _required(step.ref, "ref"))
    state: ElementState = _state_of(env, number, element)
    try:
        ax.press(element, env.app.pid)
    except DesktopError as error:
        code: object = error.context.get("code")
        if isinstance(code, int) and code in _ACTION_UNSUPPORTED:
            raise DesktopError(
                f"a{number} ({_named(state)}) cannot be pressed through accessibility; use click on it",
                {**error.context, "ref": f"a{number}"},
            ) from error
        raise
    return _effect(f"pressed {_named(state)} a{number}", None, True)


def _focus_step(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    _focus(env, _required(step.ref, "ref"))
    return _effect(f"focused {inspection.target}", None, False)


def _normalised(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _set_value(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    _check_secure(env, inspection, "setting a value")
    number, element = _element_for(env, _required(step.ref, "ref"))
    wanted: str = _required(step.text, "text")
    ax.set_attribute(element, "AXValue", wanted, env.app.pid)
    if inspection.context.secure:
        # A secure field is never read, so what it kept cannot be checked.
        return _effect(f"set the value of {inspection.target} ({len(wanted)} chars, not read back)", None, False)
    _, actual, _ = ax.read_text(element, env.app.pid)
    if _normalised(actual) != _normalised(wanted):
        raise DesktopError(
            f"a{number} did not keep the value: it now reads {_clip(actual, 80)!r}. "
            "Click the field and use type or paste instead",
            {"ref": f"a{number}", "wanted_chars": len(wanted), "actual_chars": len(actual)},
        )
    return _effect(f"set the value of {inspection.target} ({len(wanted)} chars)", None, False)


def _pastes(text: str, context: StepContext, threshold: int) -> bool:
    """Whether a type step's text goes in through the clipboard, faster than a key per character: a text longer than
    ``threshold`` (0: never) that presses no Return or Tab, into a field that is known, not secure and not one for a
    credential or a payment, and nothing key-like (the clipboard is shared with every application)."""
    return (
        0 < threshold < len(text)
        and context.element is not None
        and not context.secure
        and not hid.presses_keys(text)
        and not typing_reasons(context.element, text)
    )


def _type(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    text: str = _required(step.text, "text")
    if _pastes(text, inspection.context, env.config.paste_threshold_chars):
        return _paste(env, step, inspection)
    _check_secure(env, inspection, "typing")
    if step.ref is not None:
        _focus(env, step.ref)
        _require_keyboard(env)
    # A long text is typed key by key over seconds: the emergency stop ends it between keys (item 683).
    hid.post(hid.text_events(env.source, text), partial(check_failsafe, env))
    return _effect(f"typed {len(text)} chars into {inspection.target}", None, False)


def _paste(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    _check_secure(env, inspection, "pasting")
    if step.ref is not None:
        _focus(env, step.ref)
        _require_keyboard(env)
    text: str = _required(step.text, "text")
    hid.paste_text(env.source, text, env.config.paste_settle_ms / 1000)
    return _effect(f"pasted {len(text)} chars into {inspection.target}", None, False)


def _key(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    key: str = _required(step.key, "key")
    chord = parse_chord(key)
    if enters_text(chord):
        _check_secure(env, inspection, "typing")
    hid.post(hid.chord_events(env.source, chord))
    return _effect(f"pressed {key} in {inspection.context.app}", None, True)


def _scroll(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    point: Point = _point_for(env, step.ref, step.x, step.y)
    _check_corner(env, point)
    direction: str = _required(step.direction, "direction")
    unit_x, unit_y = _SCROLL_UNITS[direction]
    amount: int = step.amount or DEFAULT_SCROLL_PX
    remaining: int = amount
    while remaining > 0:
        chunk: int = min(remaining, _WHEEL_CHUNK_PX)
        hid.post(hid.scroll_events(env.source, point, unit_x * chunk, unit_y * chunk), partial(check_failsafe, env))
        remaining -= chunk
    return _effect(f"scrolled {direction} {amount} px over {inspection.target}", point, False)


def _event_number() -> int:
    return int(time.monotonic() * 1000) % 1_000_000 + 1


def _drag(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    start: Point = _point_for(env, step.ref, step.x, step.y)
    end: Point = _point_for(env, step.to_ref, step.to_x, step.to_y)
    _check_corner(env, start)
    _check_corner(env, end)
    moves: int = max(
        _MIN_DRAG_MOVES, min(_MAX_DRAG_MOVES, round(hypot(end.x - start.x, end.y - start.y) / _DRAG_PX_PER_MOVE))
    )
    hid.post(hid.drag_events(env.source, start, end, moves, _event_number()), partial(check_failsafe, env))
    destination: str = f"({round(end.x * env.scale)}, {round(end.y * env.scale)})"
    return _effect(f"dragged {inspection.target} to {destination}", end, True)


def _menu(env: StepEnv, step: Step) -> Effect:
    chosen: str = ax.choose_menu_item(env.app_element, env.app.pid, step.path or [])
    return _effect(f"chose {chosen} in {env.app.name}", None, True)


def _activate(env: StepEnv, step: Step, inspection: Inspection) -> Effect:
    """Bring forward the application the step names, resolved as its inspection resolved it (the thread's own copy
    first, item 750), and only when that is the copy the review judged: two running copies of one application must not
    resolve to one there and to the other here."""
    app: AppInfo = apps.require_app(_required(step.text, "application"), apps.list_apps(), env.preferred, env.app.pid)
    if app.pid != inspection.pid:
        raise DesktopError(
            f"{step.text!r} now names {app.name} (pid {app.pid}), not the copy the step was judged for (pid "
            f"{inspection.pid}); nothing was done. Name the pid of the one you mean",
            {"named": app.pid, "reviewed": inspection.pid},
        )
    apps.activate(app, env.config.activate_timeout_s)
    return Effect(detail=f"{app.name} is in front", pointer=None, opens_ui=True, switched_to=app)


def _changed_since(reviewed: Inspection, fresh: Inspection) -> bool:
    """Whether a step would touch something else than the review judged: another application, kind of window, control
    or label, a default button that is another, or a field that turned secret (the command line a terminal holds is
    not compared: the review reads it from what Dot typed, the step does not)."""
    before: StepContext = reviewed.context
    after: StepContext = fresh.context
    return (reviewed.pid, before.role, before.app, before.element, before.secure, before.default_button) != (
        fresh.pid,
        after.role,
        after.app,
        after.element,
        after.secure,
        after.default_button,
    )


def execute_step(env: StepEnv, step: Step, reviewed: Inspection) -> Effect:
    """Perform one step. The failsafe is checked first and the context re-read, since time passed after the review: a
    question to the owner takes minutes, and a page can put another control where the one they approved was. A step that
    would touch something else than ``reviewed``, what the review judged and the owner approved, is not performed (item
    555)."""
    check_failsafe(env)
    # Someone else moving the pointer since the previous step stops the batch, as docs/safety.md says (item 683).
    check_drift(env)
    # Performing a step does not depend on what Dot typed on a terminal's line, only judging it does.
    inspection: Inspection = inspect_step(env, step, {})
    refuse_secrets(inspection.context)
    if _changed_since(reviewed, inspection):
        raise DesktopError(
            f"the screen changed after this step was reviewed: it was to touch {reviewed.target}, and it would now "
            f"touch {inspection.target}; nothing was done. Take a new desktop_snapshot and look again",
            {"reviewed": reviewed.target, "now": inspection.target},
        )
    match step.action:
        case "click":
            return _click(env, step, inspection, 1)
        case "double_click":
            return _click(env, step, inspection, 2)
        case "right_click":
            return _right_click(env, step, inspection)
        case "hover":
            return _hover(env, step, inspection)
        case "press":
            return _press(env, step)
        case "focus":
            return _focus_step(env, step, inspection)
        case "set_value":
            return _set_value(env, step, inspection)
        case "type":
            return _type(env, step, inspection)
        case "paste":
            return _paste(env, step, inspection)
        case "key":
            return _key(env, step, inspection)
        case "scroll":
            return _scroll(env, step, inspection)
        case "drag":
            return _drag(env, step, inspection)
        case "menu":
            return _menu(env, step)
        case "activate":
            return _activate(env, step, inspection)
        case "wait":
            raise DesktopError("wait is performed by the session, not on the worker thread", {})
