"""Accessibility (AX) trees of other applications: a bounded walk that gives elements stable refs, plus AX actions.

Every call talks to the target process synchronously, so all of it runs on the desktop worker thread. A walk is bounded
by a node count, a depth and a time budget; a subtree that is clipped away, empty or decorative is skipped whole. Refs
are integers handed out by the caller and kept across snapshots for as long as the application keeps the element.
"""

import time
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass

import ApplicationServices as AS
import CoreFoundation as CF
import objc

from dotagent.core.errors import DesktopError, DesktopPermissionError
from dotagent.core.logs import get_logger
from dotagent.core.text import fold_search
from dotagent.desktop.types import AXElement, Node, Point, Rect, Snapshot, SnapshotScope

_log = get_logger("dotagent.desktop.ax")

_FAILURE: int = -25200
_API_DISABLED: int = -25211
_ABSENT_CODES: frozenset[int] = frozenset({-25205, -25212})  # attribute unsupported / no value: an ordinary answer
_ERRORS: dict[int, str] = {
    -25200: "the call failed",
    -25201: "the call was malformed",
    -25202: "the element no longer exists",
    -25204: "the application did not answer (busy, hung, or it blocks accessibility)",
    -25205: "the element has no such attribute",
    -25206: "the element does not support this action",
    -25207: "the application does not send this notification",
    -25208: "the application does not implement this",
    -25211: "accessibility is switched off for this process",
    -25212: "the attribute has no value",
}
_CORE_ATTRS: tuple[str, ...] = (
    "AXRole",
    "AXSubrole",
    "AXTitle",
    "AXDescription",
    "AXPlaceholderValue",
    "AXHelp",
    "AXPosition",
    "AXSize",
    "AXEnabled",
    "AXFocused",
    "AXSelected",
    "AXChildren",
)
_LABEL_ATTRS: tuple[str, ...] = ("AXTitle", "AXDescription", "AXPlaceholderValue")
_WAKE_ATTRIBUTES: tuple[str, ...] = ("AXManualAccessibility", "AXEnhancedUserInterface")
# What an application announces while its interface changes; a settle waits until none came for a moment.
_CHANGE_NOTIFICATIONS: tuple[str, ...] = (
    "AXValueChanged",
    "AXTitleChanged",
    "AXCreated",
    "AXUIElementDestroyed",
    "AXFocusedUIElementChanged",
    "AXFocusedWindowChanged",
    "AXWindowCreated",
    "AXSheetCreated",
    "AXMenuOpened",
    "AXMenuClosed",
    "AXSelectedChildrenChanged",
    "AXSelectedRowsChanged",
    "AXRowCountChanged",
    "AXRowExpanded",
    "AXRowCollapsed",
    "AXLayoutChanged",
    "AXElementBusyChanged",
    "AXMainWindowChanged",
    "AXWindowMoved",
    "AXWindowResized",
    "AXWindowMiniaturized",
    "AXWindowDeminiaturized",
    "AXMoved",
    "AXResized",
    "AXSelectedTextChanged",
    "AXLoadComplete",  # web content (WebKit, Chromium, Electron)
    "AXLayoutComplete",
    "AXLiveRegionChanged",
)
_UNSUPPORTED_NOTIFICATION: int = -25207
_TEXT_ROLES: frozenset[str] = frozenset(
    {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXStaticText", "AXPopUpButton", "AXMenuButton"}
)
_TOGGLE_NAMES: dict[str, tuple[str, str, str]] = {
    "AXCheckBox": ("off", "on", "mixed"),
    "AXRadioButton": ("off", "on", "mixed"),
    "AXSwitch": ("off", "on", "mixed"),
    "AXDisclosureTriangle": ("collapsed", "expanded", "mixed"),
}
_NUMBER_ROLES: frozenset[str] = frozenset({"AXSlider", "AXIncrementor", "AXProgressIndicator", "AXLevelIndicator"})
INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "AXButton",
        "AXCheckBox",
        "AXRadioButton",
        "AXTextField",
        "AXTextArea",
        "AXComboBox",
        "AXPopUpButton",
        "AXMenuButton",
        "AXMenuItem",
        "AXMenuBarItem",
        "AXLink",
        "AXSlider",
        "AXTab",
        "AXSwitch",
        "AXDisclosureTriangle",
        "AXIncrementor",
        "AXSearchField",
    }
)
_PASSTHROUGH_ROLES: frozenset[str] = frozenset(
    {"AXGroup", "AXSplitGroup", "AXScrollArea", "AXLayoutArea", "AXLayoutItem", "AXUnknown", "AXGenericElement"}
)
_DECORATIVE_ROLES: frozenset[str] = frozenset(
    {"AXImage", "AXSplitter", "AXHandle", "AXRuler", "AXWindow", "AXApplication"}
)
_PRUNED_ROLES: frozenset[str] = frozenset({"AXScrollBar", "AXValueIndicator", "AXGrowArea", "AXMatte"})
_FREE_ROLES: frozenset[str] = frozenset({"AXMenu", "AXMenuItem", "AXPopover", "AXSheet", "AXDrawer"})
_VIRTUAL_LIST_ROLES: frozenset[str] = frozenset({"AXOutline", "AXTable", "AXList", "AXBrowser"})
_VALUE_CLIP: int = 80
_STATIC_TEXT_CLIP: int = 120
_LARGE_TEXT_CHARS: int = 4000
_SECURE_SUBROLE: str = "AXSecureTextField"
_INVISIBLE: dict[int, None] = {
    code: None
    for code in (
        0x200B,
        0x200E,
        0x200F,
        0x2028,
        0x2029,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
        0xFEFF,
    )
}  # zero-width space, line/paragraph separators, bidi marks and the byte order mark show up in many labels
_SUBROLE_LABELS: dict[str, str] = {
    "AXCloseButton": "close window",
    "AXMinimizeButton": "minimize window",
    "AXZoomButton": "zoom window",
    "AXFullScreenButton": "full screen",
}
_MARK_NAMES: dict[str, str] = {"✓": "checked", "•": "selected", "-": "mixed", "–": "mixed", "−": "mixed"}


@dataclass(frozen=True, slots=True)
class WalkLimits:
    max_nodes: int
    max_depth: int
    budget_s: float


@dataclass(frozen=True, slots=True)
class Root:
    """Where a walk starts; ``clip`` drops subtrees outside it (scrolled-away rows, hidden panes)."""

    element: AXElement
    clip: Rect | None


@dataclass(frozen=True, slots=True)
class ElementState:
    role: str
    subrole: str
    label: str
    frame: Rect | None
    enabled: bool
    focused: bool
    selected: bool

    def secure(self) -> bool:
        return self.subrole == _SECURE_SUBROLE


@dataclass(frozen=True, slots=True)
class _Core:
    state: ElementState
    children: tuple[AXElement, ...]


@dataclass(frozen=True, slots=True)
class _Pending:
    element: AXElement
    depth: int
    indent: int
    strict: bool


def error_text(code: int) -> str:
    """A sentence for an accessibility status code."""
    return _ERRORS.get(code, "unknown accessibility error")


# What to do after a failed call, by its code (item 862, after vercel-labs/agent-desktop's retry dispositions): whether
# the same call may simply be made again. A read has changed nothing; an action that got no answer may have been done.
_READ_RETRY: dict[int, str] = {
    -25202: "take a new desktop_snapshot and use its refs",
    -25204: "safe to retry after a moment",
    -25206: "retrying fails the same way: use another step",
    -25208: "retrying fails the same way: use another step",
}
_ACT_RETRY: dict[int, str] = {
    -25202: "nothing was done: take a new desktop_snapshot and use its refs",
    -25204: "not safe to retry at once: the step may have been done; take a new desktop_snapshot and look first",
    -25206: "nothing was done, and retrying fails the same way: use another step (click, a key, the menu)",
    -25208: "nothing was done, and retrying fails the same way: use another step (click, a key, the menu)",
}


def failure(code: int, what: str, pid: int, attribute: str, acting: bool = False) -> DesktopError:
    """The exception for a failed accessibility call, with the code explained and, where it tells, whether the same
    call may be made again (``acting``: the call was a step's action, not a read; item 862)."""
    context: dict[str, str | int] = {"pid": pid, "attribute": attribute, "code": code}
    if code == _API_DISABLED:
        return DesktopPermissionError("macOS has not granted Accessibility to the app that runs Dot", context)
    hint: str | None = (_ACT_RETRY if acting else _READ_RETRY).get(code)
    if hint is not None:
        context["retry"] = hint
    return DesktopError(f"{what}: {error_text(code)}" + (f" ({hint})" if hint else ""), context)


def app_element(pid: int, timeout_s: float) -> AXElement:
    """The accessibility element of an application; every call on it gives up after ``timeout_s``."""
    element: AXElement = AS.AXUIElementCreateApplication(pid)
    code: int = int(AS.AXUIElementSetMessagingTimeout(element, timeout_s))
    if code != 0:
        raise failure(code, "cannot set the accessibility timeout", pid, "messaging timeout")
    return element


def _is_ax_value(raw: object) -> bool:
    return "AXValue" in type(raw).__name__


def _is_ax_error(raw: object | None) -> bool:
    """Attributes that CopyMultipleAttributeValues could not read come back as AXValue objects of the error type."""
    return raw is not None and _is_ax_value(raw) and int(AS.AXValueGetType(raw)) == int(AS.kAXValueAXErrorType)


def _read(element: AXElement, attribute: str) -> tuple[int, object | None]:
    code, raw = AS.AXUIElementCopyAttributeValue(element, attribute, None)
    return int(code), (None if _is_ax_error(raw) else raw)


def read_optional(element: AXElement, attribute: str, pid: int) -> object | None:
    """An attribute's value; ``None`` when the element has none. Other failures raise."""
    code, raw = _read(element, attribute)
    if code == 0:
        return raw
    if code in _ABSENT_CODES:
        return None
    raise failure(code, f"cannot read {attribute}", pid, attribute)


def _read_counted(element: AXElement, attribute: str, skipped: dict[int, int]) -> object | None:
    """Like ``read_optional`` during a walk: elements that vanish or hang are counted, not fatal."""
    code, raw = _read(element, attribute)
    if code == 0:
        return raw
    if code not in _ABSENT_CODES:
        skipped[code] = skipped.get(code, 0) + 1
    return None


def _text(raw: object | None) -> str:
    if raw is None or _is_ax_value(raw):
        return ""
    return " ".join(str(raw).translate(_INVISIBLE).split())


def _number(raw: object | None) -> float | None:
    if isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, int | float):
        return float(raw)
    return None


def _flag(raw: object | None, default: bool) -> bool:
    return default if raw is None or _is_ax_error(raw) else bool(raw)


def _frame(position: object | None, size: object | None) -> Rect | None:
    if position is None or size is None or not _is_ax_value(position) or not _is_ax_value(size):
        return None
    if int(AS.AXValueGetType(position)) != int(AS.kAXValueCGPointType):
        return None
    if int(AS.AXValueGetType(size)) != int(AS.kAXValueCGSizeType):
        return None
    point = AS.AXValueGetValue(position, AS.kAXValueCGPointType, None)[1]
    extent = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)[1]
    return Rect(float(point.x), float(point.y), float(extent.width), float(extent.height))


def _elements_of(raw: object | None) -> tuple[AXElement, ...]:
    if raw is None or _is_ax_error(raw) or isinstance(raw, str):
        return ()
    if isinstance(raw, Sequence):
        return tuple(raw)
    raise DesktopError(
        "an accessibility attribute that should list elements returned something else", {"type": type(raw).__name__}
    )


def _read_core(element: AXElement) -> tuple[_Core | None, int]:
    code, values = AS.AXUIElementCopyMultipleAttributeValues(element, list(_CORE_ATTRS), 0, None)
    if int(code) != 0 or values is None:
        return None, int(code) or _FAILURE
    raw: dict[str, object | None] = dict(zip(_CORE_ATTRS, values, strict=True))
    subrole: str = _text(raw["AXSubrole"])
    label: str = next((text for text in (_text(raw[name]) for name in _LABEL_ATTRS) if text), "")
    label = label or _SUBROLE_LABELS.get(subrole, "") or _text(raw["AXHelp"])  # help text is a last resort
    state: ElementState = ElementState(
        role=_text(raw["AXRole"]) or "AXUnknown",
        subrole=subrole,
        label=label,
        frame=_frame(raw["AXPosition"], raw["AXSize"]),
        enabled=_flag(raw["AXEnabled"], True),
        focused=_flag(raw["AXFocused"], False),
        selected=_flag(raw["AXSelected"], False),
    )
    return _Core(state=state, children=_elements_of(raw["AXChildren"])), 0


def read_state(element: AXElement, pid: int) -> ElementState:
    """What one element looks like right now (a live read, not the snapshot's copy)."""
    core, code = _read_core(element)
    if core is None:
        raise failure(code, "cannot read the element", pid, "AXRole")
    return core.state


def _shortcut(element: AXElement, skipped: dict[int, int]) -> str:
    """The keyboard shortcut of a menu item in the notation of the ``key`` step, for example ``cmd+shift+n``."""
    character: str = _text(_read_counted(element, "AXMenuItemCmdChar", skipped))
    if not character or not character.isprintable():
        return ""
    bits: float | None = _number(_read_counted(element, "AXMenuItemCmdModifiers", skipped))
    mask: int = 0 if bits is None else int(bits)
    names: list[str] = []
    if mask & 4:
        names.append("ctrl")
    if mask & 2:
        names.append("alt")
    if mask & 1:
        names.append("shift")
    if not mask & 8:
        names.append("cmd")
    return "+".join([*names, character.lower()])


def _menu_item_value(element: AXElement, skipped: dict[int, int]) -> str:
    mark: str = _MARK_NAMES.get(_text(_read_counted(element, "AXMenuItemMarkChar", skipped)), "")
    return " ".join(part for part in (mark, _shortcut(element, skipped)) if part)


def _read_value(element: AXElement, state: ElementState, skipped: dict[int, int]) -> str:
    role: str = state.role
    if role == "AXMenuItem":
        return _menu_item_value(element, skipped)
    if role in _TOGGLE_NAMES:
        number: float | None = _number(_read_counted(element, "AXValue", skipped))
        names: tuple[str, str, str] = _TOGGLE_NAMES[role]
        return names[int(number)] if number is not None and 0 <= int(number) <= 2 else ""
    if role in _NUMBER_ROLES:
        measure: float | None = _number(_read_counted(element, "AXValue", skipped))
        return "" if measure is None else f"{measure:g}"
    if role not in _TEXT_ROLES:
        return ""
    if role == "AXTextArea":  # terminal scrollback and editors can hold megabytes: only small ones are read here
        size: float | None = _number(_read_counted(element, "AXNumberOfCharacters", skipped))
        if size is None:
            return ""
        if size > _LARGE_TEXT_CHARS:
            return f"<{int(size)} chars; desktop_read shows them>"
    clip: int = _STATIC_TEXT_CLIP if role == "AXStaticText" else _VALUE_CLIP
    return _text(_read_counted(element, "AXValue", skipped))[:clip]


def _emits(state: ElementState, value: str) -> bool:
    if state.role == "AXMenuItem" and not state.label:  # separators
        return False
    if state.role in INTERACTIVE_ROLES or state.label or value:
        return True
    return state.role not in _PASSTHROUGH_ROLES and state.role not in _DECORATIVE_ROLES


def _outside(state: ElementState, strict: bool, clip: Rect | None) -> bool:
    if state.role in _PRUNED_ROLES:
        return True
    if not strict or state.role in _FREE_ROLES or state.frame is None:
        return False
    if state.frame.width <= 0 or state.frame.height <= 0:
        return True
    return clip is not None and not state.frame.intersects(clip)


def _index(previous: Snapshot | None) -> dict[int, list[tuple[int, AXElement]]]:
    index: dict[int, list[tuple[int, AXElement]]] = {}
    if previous is not None:
        for ref, element in previous.elements.items():
            index.setdefault(hash(element), []).append((ref, element))
    return index


def _known_ref(index: dict[int, list[tuple[int, AXElement]]], element: AXElement) -> int | None:
    return next((ref for ref, candidate in index.get(hash(element), []) if candidate == element), None)


def snapshot(
    previous: Snapshot | None, roots: Sequence[Root], pid: int, first_ref: int, limits: WalkLimits
) -> Snapshot:
    """Walk the trees under ``roots`` in reading order. Elements that ``previous`` knew keep their refs; new ones are
    numbered from ``first_ref``. Raises when a root cannot be read at all."""
    started: float = time.perf_counter()
    index: dict[int, list[tuple[int, AXElement]]] = _index(previous)
    next_ref: int = first_ref
    nodes: list[Node] = []
    elements: dict[int, AXElement] = {}
    skipped: dict[int, int] = {}
    truncated: bool = False
    for root in roots:
        stack: list[_Pending] = [_Pending(root.element, 0, 0, True)]
        while stack:
            if len(nodes) >= limits.max_nodes or time.perf_counter() - started > limits.budget_s:
                truncated = True
                break
            pending: _Pending = stack.pop()
            core, code = _read_core(pending.element)
            if core is None:
                if pending.depth == 0:
                    raise failure(code, "cannot read the accessibility tree", pid, "AXRole")
                skipped[code] = skipped.get(code, 0) + 1  # children vanish or hang mid-walk: skip, but account for them
                continue
            state: ElementState = core.state
            if _outside(state, pending.strict, root.clip):
                continue
            value: str = "" if state.secure() else _read_value(pending.element, state, skipped)
            indent: int = pending.indent
            if _emits(state, value):
                ref: int | None = _known_ref(index, pending.element)
                if ref is None:
                    ref, next_ref = next_ref, next_ref + 1
                if ref in elements:  # reached twice (a cycle or a shared child): once is enough
                    continue
                elements[ref] = pending.element
                nodes.append(
                    Node(
                        ref=ref,
                        role=state.role,
                        subrole=state.subrole,
                        label=state.label,
                        value="" if value == state.label else value,
                        frame=state.frame,
                        enabled=state.enabled,
                        focused=state.focused,
                        selected=state.selected,
                        indent=indent,
                    )
                )
                indent += 1
            if pending.depth < limits.max_depth:
                children: tuple[AXElement, ...] = core.children
                if state.role in _VIRTUAL_LIST_ROLES:  # a list of 10 000 rows answers with the rows on screen
                    visible: tuple[AXElement, ...] = _elements_of(
                        _read_counted(pending.element, "AXVisibleChildren", skipped)
                    )
                    children = visible or children
                strict: bool = pending.strict and state.role not in _FREE_ROLES
                stack.extend(_Pending(child, pending.depth + 1, indent, strict) for child in reversed(children))
    return Snapshot(
        pid=pid,
        nodes=tuple(nodes),
        elements=elements,
        next_ref=next_ref,
        truncated=truncated,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        skipped=skipped,
    )


def frame_of(element: AXElement, pid: int) -> Rect | None:
    return _frame(read_optional(element, "AXPosition", pid), read_optional(element, "AXSize", pid))


def windows_of(app: AXElement, pid: int) -> tuple[AXElement, ...]:
    return _elements_of(read_optional(app, "AXWindows", pid))


def window_of(element: AXElement, pid: int) -> AXElement | None:
    """The window ``element`` is in; ``None`` for one outside any window (the menu bar)."""
    return read_optional(element, "AXWindow", pid)


def title_of(window: AXElement, pid: int) -> str:
    """The title of a window as its application gives it; a browser's is the title of the page it shows."""
    return _text(read_optional(window, "AXTitle", pid))


def focused_window(app: AXElement, pid: int) -> AXElement | None:
    """The window that has the keyboard focus, else the main window, else the first one."""
    for attribute in ("AXFocusedWindow", "AXMainWindow"):
        window: object | None = read_optional(app, attribute, pid)
        if window is not None:
            return window
    windows: tuple[AXElement, ...] = windows_of(app, pid)
    return windows[0] if windows else None


def default_button_of(element: AXElement | None, app: AXElement, pid: int) -> ElementState | None:
    """The default button of the window or sheet the keyboard is in, which Return presses (live): the top-level window
    or sheet of ``element``, the control that has the keyboard, else the focused window of ``app``. ``None`` when it has
    none."""
    top: AXElement | None = None if element is None else read_optional(element, "AXTopLevelUIElement", pid)
    if top is None:
        top = focused_window(app, pid)
    button: object | None = None if top is None else read_optional(top, "AXDefaultButton", pid)
    return None if button is None else read_state(button, pid)


def _open_menus(app: AXElement, pid: int) -> tuple[Root, ...]:
    """Context menus and pop-up menus that are open belong to the application, not to any window."""
    menus: list[Root] = []
    for child in _elements_of(read_optional(app, "AXChildren", pid)):
        role: object | None = read_optional(child, "AXRole", pid)
        if role == "AXMenu":
            menus.append(Root(child, None))
    return tuple(menus)


def roots_for(app: AXElement, pid: int, scope: SnapshotScope) -> tuple[Root, ...]:
    """The trees a snapshot of ``scope`` walks."""
    if scope == "menu_bar":
        bar: object | None = read_optional(app, "AXMenuBar", pid)
        if bar is None:
            raise DesktopError("the application has no menu bar", {"pid": pid})
        return (Root(bar, None),)
    windows: tuple[AXElement, ...]
    if scope == "window":
        window: AXElement | None = focused_window(app, pid)
        windows = () if window is None else (window,)
    else:
        windows = windows_of(app, pid)
    if not windows:
        raise DesktopError(
            "the application has no open window; use scope menu_bar, or open a window first",
            {"pid": pid, "scope": scope},
        )
    return (*(Root(window, frame_of(window, pid)) for window in windows), *_open_menus(app, pid))


def focused_element(app: AXElement, pid: int) -> AXElement | None:
    return read_optional(app, "AXFocusedUIElement", pid)


def read_text(element: AXElement, pid: int) -> tuple[str, str, int]:
    """(label, text, total characters) of an element: the whole value of a field rather than the clipped one."""
    state: ElementState = read_state(element, pid)
    if state.secure():
        raise DesktopError("the field is a secure text field; its content is never read", {"pid": pid})
    raw: object | None = read_optional(element, "AXValue", pid)
    number: float | None = _number(raw)
    if isinstance(raw, str):
        text: str = raw
    elif number is not None:
        text = f"{number:g}"
    else:
        text = state.label
    return state.label, text, len(text)


def perform(element: AXElement, action: str, pid: int) -> None:
    code: int = int(AS.AXUIElementPerformAction(element, action))
    if code != 0:
        raise failure(code, f"{action} failed", pid, action, acting=True)


def set_attribute(element: AXElement, attribute: str, value: str | bool, pid: int) -> None:
    code: int = int(AS.AXUIElementSetAttributeValue(element, attribute, value))
    if code != 0:
        raise failure(code, f"cannot set {attribute}", pid, attribute, acting=True)


def press(element: AXElement, pid: int) -> None:
    perform(element, "AXPress", pid)


def show_in_view(element: AXElement, pid: int) -> None:
    perform(element, "AXScrollToVisible", pid)


def raise_window(window: AXElement, pid: int) -> None:
    perform(window, "AXRaise", pid)


def _set_wake_flags(app: AXElement, enabled: bool) -> None:
    for attribute in _WAKE_ATTRIBUTES:
        AS.AXUIElementSetAttributeValue(app, attribute, enabled)


def wake_accessibility(app: AXElement) -> None:
    """Ask an Electron/Chromium application to build its accessibility tree, which they do lazily.

    Electron listens to AXManualAccessibility and Chromium to AXEnhancedUserInterface. The status codes do not say which
    one took effect (Chromium answers "not implemented" and builds the tree anyway), so both are set and the caller
    looks at the tree itself.
    """
    _set_wake_flags(app, True)


def rest_accessibility(app: AXElement) -> None:
    """Tell an application that woke up for accessibility that nobody is reading its tree any more (it costs CPU)."""
    _set_wake_flags(app, False)


def _announced(observer: object, element: object, notification: str, refcon: object) -> None:
    """Nothing to do: the run loop reports that an announcement arrived, and which one does not matter."""


# Wrapped once: PyObjC keeps every wrapped callback alive, so a wrapper per settle would leak (about 6 KB each).
_ANNOUNCED: object = objc.callbackFor(AS.AXObserverCreate)(_announced)


def wait_until_quiet(pid: int, quiet_s: float, max_s: float) -> None:
    """Return once the application has announced no change of its interface for ``quiet_s`` (a value or a title, an
    element or a window that came, went, moved or resized, the focus, a selection, the layout, a page load), at most
    ``max_s`` after the call; removing the registrations follows (well under a millisecond for an application that
    answers). The announcements arrive on this thread's run loop. The quiet window opens once the application has taken
    the registration, so a busy one is heard after it answers. Every call to the application gives up after ``max_s``
    and registering stops at ``max_s``, so an application that stops answering adds at most ``max_s``. An application
    that announces nothing is quiet after ``quiet_s``; one that cannot be watched gets the whole ``max_s``."""
    started: float = time.monotonic()
    deadline: float = started + max_s
    app: AXElement = app_element(pid, max_s)
    code, observer = AS.AXObserverCreate(pid, _ANNOUNCED, None)
    watched: list[str] = []
    if int(code) == 0:
        for name in _CHANGE_NOTIFICATIONS:
            if time.monotonic() >= deadline:
                break
            code = AS.AXObserverAddNotification(observer, app, name, None)
            if int(code) == 0:
                watched.append(name)
            elif int(code) != _UNSUPPORTED_NOTIFICATION:
                break  # gone or not answering: every further call would wait out the timeout as well
    if not watched:
        _log.warning("desktop_settle_unwatched", pid=pid, code=int(code), error=error_text(int(code)), wait_s=max_s)
        time.sleep(max(0.0, deadline - time.monotonic()))
        return
    loop: object = CF.CFRunLoopGetCurrent()
    source: object = AS.AXObserverGetRunLoopSource(observer)
    CF.CFRunLoopAddSource(loop, source, CF.kCFRunLoopDefaultMode)
    last: float = time.monotonic()
    try:
        while (left := min(last + quiet_s, deadline) - time.monotonic()) > 0:
            if CF.CFRunLoopRunInMode(CF.kCFRunLoopDefaultMode, left, True) == CF.kCFRunLoopRunHandledSource:
                last = time.monotonic()
    finally:
        CF.CFRunLoopRemoveSource(loop, source, CF.kCFRunLoopDefaultMode)
        for name in watched:
            if int(AS.AXObserverRemoveNotification(observer, app, name)) != 0:
                break  # not answering: the rest would wait as well, and the released observer hears nothing more


def pid_of(element: AXElement) -> int:
    code, pid = AS.AXUIElementGetPid(element, None)
    if int(code) != 0:
        raise failure(int(code), "cannot read the pid of an element", 0, "AXUIElementGetPid")
    return int(pid)


def element_at(point: Point, timeout_s: float) -> AXElement | None:
    """The deepest element under a screen point, in whichever application is there (``None``: nothing answers)."""
    system: AXElement = AS.AXUIElementCreateSystemWide()
    AS.AXUIElementSetMessagingTimeout(system, timeout_s)
    code, found = AS.AXUIElementCopyElementAtPosition(system, point.x, point.y, None)
    element: AXElement | None = found
    if element is not None:
        return element
    if int(code) in _ABSENT_CODES or int(code) == 0:
        return None
    raise failure(int(code), "cannot find the element under the pointer", 0, "AXUIElementCopyElementAtPosition")


def parent_of(element: AXElement, pid: int) -> AXElement | None:
    return read_optional(element, "AXParent", pid)


def enclosing(element: AXElement, pid: int, roles: Collection[str], hops: int) -> AXElement:
    """The element itself or its nearest ancestor (within ``hops``) whose role is in ``roles``; else the element.

    A hit test returns the deepest element, for example the text inside a button, while the control is the button.
    """
    current: AXElement | None = element
    for _ in range(hops + 1):
        if current is None:
            break
        if _text(read_optional(current, "AXRole", pid)) in roles:
            return current
        current = parent_of(current, pid)
    return element


def _fold(title: str) -> str:
    """A menu title as it is compared: without a trailing ellipsis, and whatever the case, the accents and the Turkish
    dotted or dotless i ("Görünüm" is "gorunum", "İçe Aktar…" is "ice aktar")."""
    return fold_search(unicodedata.normalize("NFKC", title).strip().removesuffix("…").removesuffix("..."))


def _titles(items: Sequence[AXElement], pid: int) -> list[str]:
    titles: list[str] = []
    for item in items:
        title: str = _text(read_optional(item, "AXTitle", pid))
        if title:
            titles.append(title)
    return titles


def _entries_of(item: AXElement, pid: int) -> tuple[AXElement, ...]:
    """The entries of the menu that hangs under a menu bar item or a menu item."""
    for child in _elements_of(read_optional(item, "AXChildren", pid)):
        if read_optional(child, "AXRole", pid) == "AXMenu":
            return _elements_of(read_optional(child, "AXChildren", pid))
    return ()


def choose_menu_item(app: AXElement, pid: int, path: Sequence[str]) -> str:
    """Press the menu entry at ``path`` without opening any menu on screen; returns the full path that was pressed."""
    bar: object | None = read_optional(app, "AXMenuBar", pid)
    if bar is None:
        raise DesktopError("the application has no menu bar", {"pid": pid})
    level: tuple[AXElement, ...] = _elements_of(read_optional(bar, "AXChildren", pid))
    pressed: list[str] = []
    for depth, wanted in enumerate(path):
        match: AXElement | None = next(
            (item for item in level if _fold(_text(read_optional(item, "AXTitle", pid))) == _fold(wanted)), None
        )
        if match is None:
            raise DesktopError(
                f"there is no menu entry {wanted!r}"
                + (f" under {' > '.join(pressed)}" if pressed else " in the menu bar")
                + f"; available: {', '.join(_titles(level, pid))}",
                {"pid": pid, "path": " > ".join(path), "level": depth + 1},
            )
        pressed.append(_text(read_optional(match, "AXTitle", pid)))
        if depth == len(path) - 1:
            if not read_state(match, pid).enabled:
                raise DesktopError(f"the menu entry {' > '.join(pressed)} is disabled right now", {"pid": pid})
            press(match, pid)
            return " > ".join(pressed)
        level = _entries_of(match, pid)
    raise DesktopError("an empty menu path", {"pid": pid})
