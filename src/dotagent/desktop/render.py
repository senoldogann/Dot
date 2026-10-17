"""Render desktop results for the model: a trusted header line first, then one fenced block holding all screen data.

Application names, window titles, element labels and values come from other programs, so they only ever appear inside
the ``<screen_content>`` fence; the header carries counts and hints produced by Dot itself.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dotagent.core.text import fold_search, unclosing
from dotagent.desktop.ax import INTERACTIVE_ROLES
from dotagent.desktop.types import (
    DEFAULT_SCROLL_PX,
    ActOutcome,
    AppView,
    Node,
    OpenedApp,
    ScreenshotView,
    SnapshotView,
    Step,
    StepReport,
    TextRead,
    WindowView,
)

_LABEL_CHARS: int = 100
_VALUE_CHARS: int = 80
_TEXT_CHARS: int = 140
_DETAIL_CHARS: int = 300
_NOTE_CHARS: int = 300
_KEY_CHARS: int = 40
_MAX_INDENT: int = 12
_MAX_NOTES: int = 12
_MAX_APP_WINDOWS: int = 4
_GAP_RESERVE: int = 300
_MAX_DELTA_LINES: int = 80
_PRIORITIES: tuple[int, ...] = (0, 1, 2, 3)
_ROLE_NAMES: dict[str, str] = {
    "AXStaticText": "text",
    "AXTextField": "textfield",
    "AXTextArea": "textarea",
    "AXSearchField": "searchfield",
    "AXPopUpButton": "popup",
    "AXMenuButton": "menubutton",
    "AXRadioButton": "radio",
    "AXCheckBox": "checkbox",
    "AXTabGroup": "tabs",
    "AXScrollArea": "scrollarea",
    "AXMenuBarItem": "menubaritem",
    "AXMenuItem": "menuitem",
    "AXComboBox": "combobox",
    "AXDisclosureTriangle": "disclosure",
    "AXSplitGroup": "split",
    "AXIncrementor": "stepper",
    "AXProgressIndicator": "progress",
    "AXLevelIndicator": "level",
}
_STATE_ROLES: frozenset[str] = frozenset(
    {
        "AXCheckBox",
        "AXRadioButton",
        "AXSwitch",
        "AXDisclosureTriangle",
        "AXSlider",
        "AXIncrementor",
        "AXProgressIndicator",
        "AXLevelIndicator",
    }
)
_TEXT_ROLE: str = "AXStaticText"
_FIRST_CLASS_ROLES: frozenset[str] = frozenset({"AXWindow", "AXSheet"})
_SECURE_ROLE_NAME: str = "securefield"


@dataclass(frozen=True, slots=True)
class Line:
    """One rendered element: its ref, the text of the line (indented) and how much it matters when space is short."""

    ref: int
    text: str
    priority: int


@dataclass(frozen=True, slots=True)
class Delta:
    """The difference between two renderings of the same view."""

    changed: tuple[Line, ...]
    unchanged: int
    removed: int


def fence_screen(source: str, body: str, nonce: str) -> str:
    """Wrap screen-derived text so the prompt can state that everything inside is data and only ``nonce`` closes it."""
    safe: str = unclosing(body, "screen_content")
    quoted: str = '"' + source.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f'<screen_content nonce="{nonce}" source={quoted}>\n{safe}\n</screen_content nonce="{nonce}">'


def _clip(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters; newlines in screen text cannot fake structure lines."""
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _units(value: float, scale: float) -> int:
    return round(value * scale)


def _coordinate(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"


def role_name(node: Node) -> str:
    """The role as the model reads it: ``button``, ``textfield``, ``securefield``, ..."""
    if node.secure():
        return _SECURE_ROLE_NAME
    return _ROLE_NAMES.get(node.role) or node.role.removeprefix("AX").lower()


def is_offscreen(node: Node) -> bool:
    """Elements scrolled out of view are reported with a sliver of a frame, closed menus with no frame at all."""
    frame = node.frame
    return frame is not None and frame.width > 0 and frame.height > 0 and min(frame.width, frame.height) <= 1


def _geometry(node: Node, scale: float) -> str:
    frame = node.frame
    if frame is None or frame.width <= 0 or frame.height <= 0:
        return ""
    center = frame.center()
    size: str = f"{_units(frame.width, scale)}x{_units(frame.height, scale)}"
    return f"@{_units(center.x, scale)},{_units(center.y, scale)} {size}"


def _label_and_value(node: Node) -> tuple[str, str]:
    if node.role == _TEXT_ROLE:
        return _clip(node.value or node.label, _TEXT_CHARS), ""
    return _clip(node.label, _LABEL_CHARS), node.value


def _value_part(node: Node, value: str) -> str:
    if not value:
        return ""
    if node.role == "AXMenuItem":
        return f"({value})"
    if node.role in _STATE_ROLES:
        return f"={value}"
    return f"='{_clip(value, _VALUE_CHARS)}'"


def node_line(node: Node, scale: float) -> str:
    """``[a12] button "Save" @120,340 60x24 disabled`` — coordinates are model units, the centre first."""
    label, value = _label_and_value(node)
    parts: list[str] = [f"[a{node.ref}]", role_name(node)]
    if label:
        parts.append(f'"{label}"')
    shown_value: str = _value_part(node, value)
    if shown_value:
        parts.append(shown_value)
    if node.role != _TEXT_ROLE:
        geometry: str = _geometry(node, scale)
        if geometry:
            parts.append(geometry)
    flags: tuple[tuple[str, bool], ...] = (
        ("disabled", not node.enabled),
        ("focused", node.focused),
        ("selected", node.selected),
        ("offscreen", is_offscreen(node)),
    )
    parts.extend(name for name, active in flags if active)
    return " ".join(parts)


def _priority(node: Node) -> int:
    if node.focused or node.selected or node.role in _FIRST_CLASS_ROLES:
        return 0
    if node.role in INTERACTIVE_ROLES:
        return 1
    return 2 if node.role == _TEXT_ROLE else 3


def render_lines(nodes: Sequence[Node], scale: float) -> tuple[Line, ...]:
    return tuple(
        Line(ref=node.ref, text="  " * min(node.indent, _MAX_INDENT) + node_line(node, scale), priority=_priority(node))
        for node in nodes
    )


def lines_by_ref(lines: Sequence[Line]) -> dict[int, str]:
    return {line.ref: line.text for line in lines}


def _leading_space(text: str) -> str:
    return text[: len(text) - len(text.lstrip(" "))]


def fit_lines(lines: Sequence[Line], max_chars: int) -> tuple[str, int]:
    """The lines that fit ``max_chars`` in their original order, and how many were left out.

    Focused and selected elements go first, then interactive ones, then text, then structure; each run of left-out
    lines becomes one ``… N more`` marker so the model sees where the gaps are.
    """
    if sum(len(line.text) + 1 for line in lines) <= max_chars:
        return "\n".join(line.text for line in lines), 0
    room: int = max_chars - _GAP_RESERVE
    kept: list[bool] = [False] * len(lines)
    for priority in _PRIORITIES:
        for index, line in enumerate(lines):
            cost: int = len(line.text) + 1
            if line.priority == priority and cost <= room:
                kept[index] = True
                room -= cost
    out: list[str] = []
    gap: int = 0
    for line, shown in zip(lines, kept, strict=True):
        if not shown:
            gap += 1
            continue
        if gap > 0:
            out.append(f"{_leading_space(line.text)}… {gap} more")
            gap = 0
        out.append(line.text)
    if gap > 0:
        out.append(f"… {gap} more")
    return "\n".join(out), kept.count(False)


def diff_lines(previous: Mapping[int, str], current: Sequence[Line]) -> Delta:
    """Lines that are new or differ from the previous rendering of the same view, plus how many elements are gone."""
    changed: tuple[Line, ...] = tuple(line for line in current if previous.get(line.ref) != line.text)
    present: set[int] = {line.ref for line in current}
    return Delta(
        changed=changed,
        unchanged=len(current) - len(changed),
        removed=sum(1 for ref in previous if ref not in present),
    )


def prefers_delta(delta: Delta) -> bool:
    """A delta is only worth showing when it is clearly smaller than the whole view."""
    return (
        delta.unchanged > 0
        and len(delta.changed) * 2 <= len(delta.changed) + delta.unchanged
        and len(delta.changed) <= _MAX_DELTA_LINES
    )


def search_lines(nodes: Sequence[Node], scale: float, query: str, limit: int) -> tuple[tuple[str, ...], int]:
    """Elements whose role, label or value contain every word of ``query`` (any case, accents ignored), each with the
    nearest named ancestor so identical buttons can be told apart; also the number of matches in all."""
    words: list[str] = fold_search(query).split()
    hits: list[str] = []
    total: int = 0
    ancestors: list[Node] = []
    for node in nodes:
        del ancestors[node.indent :]
        label, value = _label_and_value(node)
        if words and all(word in fold_search(f"{role_name(node)} {label} {value}") for word in words):
            total += 1
            if len(hits) < limit:
                context: Node | None = next((a for a in reversed(ancestors) if a.label or a.value), None)
                where: str = (
                    f'   (in {role_name(context)} "{_clip(context.label or context.value, 60)}")' if context else ""
                )
                hits.append(node_line(node, scale) + where)
        ancestors.append(node)
    return tuple(hits), total


def _window_place(window: WindowView) -> str:
    """Where a window is, like an element: the point to click at its centre, then its size."""
    return f"@{window.x + window.width // 2},{window.y + window.height // 2} {window.width}x{window.height}"


def _window_line(window: WindowView) -> str:
    hidden: str = "" if window.on_screen else " (not on this screen)"
    return f'  "{_clip(window.title, _LABEL_CHARS)}" {_window_place(window)}{hidden}'


def _app_windows_summary(app: AppView) -> str:
    if not app.windows:
        return "no window"
    shown: list[str] = [
        f'"{_clip(window.title, 60)}" {_window_place(window)}' for window in app.windows[:_MAX_APP_WINDOWS]
    ]
    more: str = f" (+{len(app.windows) - _MAX_APP_WINDOWS} more)" if len(app.windows) > _MAX_APP_WINDOWS else ""
    return f"{len(app.windows)} window{'s' if len(app.windows) > 1 else ''}: {' | '.join(shown)}{more}"


def app_line(app: AppView) -> str:
    marker: str = "*" if app.frontmost else " "
    bundle: str = f" ({app.bundle_id})" if app.bundle_id else ""
    return f"{marker} pid {app.pid} {app.name}{bundle} — {_app_windows_summary(app)}"


def render_apps(apps: Sequence[AppView], nonce: str) -> str:
    header: str = f"{len(apps)} applications · * has the keyboard focus"
    body: str = "\n".join(app_line(app) for app in apps) if apps else "(none)"
    return "\n".join((header, fence_screen("applications", body, nonce)))


def snapshot_header(view: SnapshotView) -> str:
    """The trusted part of a snapshot result: what kind of view it is and what was left out."""
    if view.mode == "delta":
        kind: str = f"changes only: {view.shown} new or changed of {view.nodes} elements, {view.removed} gone"
    else:
        kind = f"full view · {view.nodes} elements"
    parts: list[str] = [f"pid {view.app.pid}", kind]
    if view.omitted > 0:
        parts.append(f"{view.omitted} more lines not shown; narrow the view with query, ref or scope")
    if view.truncated:
        parts.append("the walk stopped at its node or time limit; narrow the view with query, ref or scope")
    return "Snapshot · " + " · ".join(parts)


def snapshot_block(view: SnapshotView) -> list[str]:
    lines: list[str] = [f"view: {_clip(view.heading, _LABEL_CHARS + 40)}"]
    if len(view.app.windows) > 1:
        lines.append("windows:")
        lines.extend(_window_line(window) for window in view.app.windows)
    lines.append(view.body if view.body else "(nothing to show)")
    if view.notes:
        lines.append("notes:")
        lines.extend(f"- {_clip(note, _NOTE_CHARS)}" for note in view.notes[:_MAX_NOTES])
    return lines


def render_snapshot(view: SnapshotView, nonce: str) -> str:
    return "\n".join((snapshot_header(view), fence_screen(view.app.name, "\n".join(snapshot_block(view)), nonce)))


def render_opened_app(opened: OpenedApp, nonce: str) -> str:
    """The window of an application that was just opened, or why there is none yet (the note is written by Dot)."""
    if opened.view is not None:
        return render_snapshot(opened.view, nonce)
    note: str = f" · {opened.note}" if opened.note else ""
    header: str = f"Opened · pid {opened.app.pid} · no window to show{note}"
    return "\n".join((header, fence_screen(opened.app.name, app_line(opened.app), nonce)))


def _step_line(report: StepReport) -> str:
    detail: str = f": {_clip(report.detail, _DETAIL_CHARS)}" if report.detail else ""
    return f"  {report.index}. {report.description} — {report.outcome}{detail}"


def render_act_outcome(outcome: ActOutcome, source: str, nonce: str) -> str:
    counts: Counter[str] = Counter(report.outcome for report in outcome.steps)
    summary: str = f"Steps: {counts['done']} done, {counts['failed']} failed, {counts['skipped']} skipped"
    inner: list[str] = ["steps:", *(_step_line(report) for report in outcome.steps)]
    if outcome.stopped is not None:
        inner.append(f"stopped: {_clip(outcome.stopped, _DETAIL_CHARS)}")
    header: str = summary
    if outcome.view is not None:
        inner.extend(snapshot_block(outcome.view))
        header = f"{summary} · {snapshot_header(outcome.view)}"
    elif outcome.view_note is not None:
        inner.append(f"no view: {_clip(outcome.view_note, _DETAIL_CHARS)}")
    return "\n".join((header, fence_screen(source, "\n".join(inner), nonce)))


def render_text_read(read: TextRead, source: str, nonce: str) -> str:
    end: int = read.offset + len(read.text)
    tail: str = f"next offset {end}" if end < read.total_chars else "end of text"
    header: str = f"Element text: characters {read.offset}–{end} of {read.total_chars} · {tail}"
    body: str = f'label: "{_clip(read.label, _LABEL_CHARS)}"\n{read.text if read.text else "(empty)"}'
    return "\n".join((header, fence_screen(source, body, nonce)))


def render_clipboard(text: str | None, nonce: str) -> str:
    if text is None:
        return "The clipboard holds no plain text (it may hold an image or files)."
    header: str = f"Clipboard text: {len(text)} characters"
    return "\n".join((header, fence_screen("clipboard", text if text else "(empty)", nonce)))


def render_screenshot_caption(view: ScreenshotView, nonce: str) -> str:
    """The text that goes with a screenshot: how its pixels map to coordinates, where the pointer is, who is in
    front."""
    if view.zoom == 1 and view.left == 0 and view.top == 0:
        mapping: str = "1 pixel = 1 coordinate unit"
    else:
        mapping = (
            f"zoomed region from ({view.left}, {view.top}) at {view.zoom:g}x: "
            "pixel (px, py) is coordinate (left + px/zoom, top + py/zoom)"
        )
    parts: list[str] = [
        f"Screenshot {view.width}x{view.height} px",
        mapping,
        f"pointer at ({view.pointer_x}, {view.pointer_y})",
    ]
    if view.redacted > 0:
        parts.append(f"{view.redacted} password-manager windows are blacked out")
    return "\n".join((" · ".join(parts), fence_screen("frontmost application", view.frontmost or "(none)", nonce)))


def _target(step: Step) -> str:
    if step.ref is not None:
        return step.ref
    if step.x is not None and step.y is not None:
        return f"({_coordinate(step.x)}, {_coordinate(step.y)})"
    return ""


def describe_step(step: Step) -> str:
    """A one-line description of a step. Text entered into a field is reduced to its length: it may be a secret."""
    target: str = _target(step)
    match step.action:
        case "click" | "double_click" | "right_click" | "hover":
            held: str = f" holding {'+'.join(step.modifiers)}" if step.modifiers else ""
            return f"{step.action} {target}{held}"
        case "press" | "focus":
            return f"{step.action} {target}"
        case "set_value" | "type" | "paste":
            return f"{step.action}{' ' + target if target else ''} ({len(step.text or '')} chars)"
        case "key":
            return f"key {_clip(step.key or '', _KEY_CHARS)}"
        case "scroll":
            return f"scroll {step.direction} {step.amount or DEFAULT_SCROLL_PX} px{' at ' + target if target else ''}"
        case "drag":
            end: str = step.to_ref or f"({_coordinate(step.to_x)}, {_coordinate(step.to_y)})"
            return f"drag {target} → {end}"
        case "menu":
            return f"menu {_clip(' > '.join(step.path or []), _LABEL_CHARS)}"
        case "wait":
            return f"wait {_coordinate(step.seconds)} s"
        case "activate":
            return f"activate {_clip(step.text or '', _LABEL_CHARS)}"
