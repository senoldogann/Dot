"""Synthetic keyboard, pointer and clipboard input (CGEvent), built as pure event lists and posted by ``post``.

Coordinates are global points (origin at the top-left of the main display, y down). Shortcuts use US-layout virtual
key codes, so they are positional; text is typed as Unicode and does not depend on the keyboard layout.
"""

import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem
from Foundation import NSData

from dotagent.core.errors import DesktopError
from dotagent.desktop.types import Modifier, Point

type CGEvent = object
type EventSource = object
type PasteboardSnapshot = tuple[tuple[tuple[str, bytes], ...], ...]


class PasteboardItemLike(Protocol):
    def types(self) -> Sequence[str]: ...

    def dataForType_(self, data_type: str) -> object | None: ...

    def setData_forType_(self, data: object, data_type: str) -> bool: ...


class PasteboardLike(Protocol):
    def pasteboardItems(self) -> Sequence[PasteboardItemLike] | None: ...

    def clearContents(self) -> int: ...

    def setString_forType_(self, text: str, data_type: str) -> bool: ...

    def stringForType_(self, data_type: str) -> str | None: ...

    def changeCount(self) -> int: ...

    def writeObjects_(self, objects: Sequence[object]) -> bool: ...


@dataclass(frozen=True, slots=True)
class TimedEvent:
    event: CGEvent
    pause_s: float


@dataclass(frozen=True, slots=True)
class Chord:
    key: str
    modifiers: tuple[Modifier, ...]


_FLAGS: dict[Modifier, int] = {
    "cmd": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "ctrl": Quartz.kCGEventFlagMaskControl,
    "fn": Quartz.kCGEventFlagMaskSecondaryFn,
}
_MODIFIER_KEYCODES: dict[Modifier, int] = {"cmd": 0x37, "shift": 0x38, "alt": 0x3A, "ctrl": 0x3B, "fn": 0x3F}
_MODIFIER_NAMES: dict[str, Modifier] = {
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "super": "cmd",
    "win": "cmd",
    "⌘": "cmd",
    "shift": "shift",
    "⇧": "shift",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "⌥": "alt",
    "ctrl": "ctrl",
    "control": "ctrl",
    "⌃": "ctrl",
    "fn": "fn",
    "function": "fn",
}
# Virtual key codes from HIToolbox/Events.h (kVK_ANSI_*): positions on a US keyboard.
_KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09,
    "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F, "u": 0x20, "i": 0x22,
    "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19, "7": 0x1A, "8": 0x1C, "0": 0x1D,
    "=": 0x18, "-": 0x1B, "]": 0x1E, "[": 0x21, "'": 0x27, ";": 0x29, "\\": 0x2A, ",": 0x2B, "/": 0x2C, ".": 0x2F,
    "`": 0x32,
    "return": 0x24, "tab": 0x30, "space": 0x31, "delete": 0x33, "escape": 0x35, "forwarddelete": 0x75,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E, "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64, "f9": 0x65,
    "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
}  # fmt: skip
_KEY_NAMES: dict[str, str] = {
    "enter": "return",
    "esc": "escape",
    "backspace": "delete",
    "del": "delete",
    "fwddelete": "forwarddelete",
    "arrowleft": "left",
    "arrowright": "right",
    "arrowdown": "down",
    "arrowup": "up",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "spacebar": "space",
    "plus": "=",
    "minus": "-",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
}
_RETURN_KEY: str = "return"
_TAB_KEY: str = "tab"
_MAX_UNITS_PER_EVENT: int = 20
_MARK_CATEGORIES: frozenset[str] = frozenset({"Mn", "Me", "Mc"})
_JOINER: str = "\u200d"
_CLICK_STATES: dict[bool, int] = {False: 1, True: 2}
_TRANSIENT_TYPE: str = "org.nspasteboard.TransientType"
_PLAIN_TEXT_TYPE: str = "public.utf8-plain-text"


def parse_chord(chord: str) -> Chord:
    """``cmd+shift+4`` as a key and its modifiers; raises ``DesktopError`` for names it does not know."""
    parts: list[str] = [part.strip().lower() for part in chord.strip().split("+")]
    if not all(parts):
        raise DesktopError(
            f"{chord!r} is not a key chord; write modifiers and one key joined by '+', for example cmd+shift+4",
            {"chord": chord},
        )
    modifiers: list[Modifier] = []
    for name in parts[:-1]:
        modifier: Modifier | None = _MODIFIER_NAMES.get(name)
        if modifier is None:
            raise DesktopError(f"{name!r} in {chord!r} is not a modifier (cmd, shift, alt, ctrl, fn)", {"chord": chord})
        modifiers.append(modifier)
    key: str = _KEY_NAMES.get(parts[-1], parts[-1])
    if key not in _KEYCODES:
        raise DesktopError(
            f"{key!r} in {chord!r} is not a key this tool can press; use `type` to enter text",
            {"chord": chord, "known_keys": ", ".join(sorted(_KEYCODES))},
        )
    return Chord(key=key, modifiers=tuple(dict.fromkeys(modifiers)))


def modifier_flags(modifiers: Sequence[Modifier]) -> int:
    flags: int = 0
    for modifier in modifiers:
        flags |= _FLAGS[modifier]
    return flags


def make_source() -> EventSource:
    """The event source of this login session (what Apple advises when posting from inside a session)."""
    return Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)


def move_events(source: EventSource, to: Point, pause_s: float) -> tuple[TimedEvent, ...]:
    moved: CGEvent = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventMouseMoved, (to.x, to.y), Quartz.kCGMouseButtonLeft
    )
    return (TimedEvent(moved, pause_s),)


def _click_events(
    source: EventSource, at: Point, down: int, up: int, button: int, count: int, modifiers: Sequence[Modifier]
) -> tuple[TimedEvent, ...]:
    flags: int = modifier_flags(modifiers)
    events: list[TimedEvent] = list(move_events(source, at, 0.05))  # many controls only arm after a mouseMoved
    for click in range(1, count + 1):
        for event_type, pause in ((down, 0.02), (up, 0.04)):
            event: CGEvent = Quartz.CGEventCreateMouseEvent(source, event_type, (at.x, at.y), button)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, click)
            Quartz.CGEventSetFlags(event, flags)
            events.append(TimedEvent(event, pause))
    return tuple(events)


def left_click_events(
    source: EventSource, at: Point, count: int, modifiers: Sequence[Modifier]
) -> tuple[TimedEvent, ...]:
    return _click_events(
        source,
        at,
        Quartz.kCGEventLeftMouseDown,
        Quartz.kCGEventLeftMouseUp,
        Quartz.kCGMouseButtonLeft,
        count,
        modifiers,
    )


def right_click_events(source: EventSource, at: Point, modifiers: Sequence[Modifier]) -> tuple[TimedEvent, ...]:
    return _click_events(
        source, at, Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight, 1, modifiers
    )


def drag_events(source: EventSource, start: Point, end: Point, moves: int, event_number: int) -> tuple[TimedEvent, ...]:
    """Press at ``start``, hold, drag in ``moves`` interpolated steps, release at ``end`` (one event number
    throughout)."""
    events: list[TimedEvent] = list(move_events(source, start, 0.05))
    pressed: CGEvent = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventLeftMouseDown, (start.x, start.y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventSetIntegerValueField(pressed, Quartz.kCGMouseEventNumber, event_number)
    events.append(TimedEvent(pressed, 0.12))  # hold so that the drag source arms
    for index in range(1, moves + 1):
        progress: float = index / moves
        position: tuple[float, float] = (start.x + (end.x - start.x) * progress, start.y + (end.y - start.y) * progress)
        dragged: CGEvent = Quartz.CGEventCreateMouseEvent(
            source, Quartz.kCGEventLeftMouseDragged, position, Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventSetIntegerValueField(dragged, Quartz.kCGMouseEventNumber, event_number)
        events.append(TimedEvent(dragged, 0.012))
    released: CGEvent = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventLeftMouseUp, (end.x, end.y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventSetIntegerValueField(released, Quartz.kCGMouseEventNumber, event_number)
    events.append(TimedEvent(released, 0.05))
    return tuple(events)


def scroll_events(source: EventSource, at: Point, dx_px: int, dy_px: int) -> tuple[TimedEvent, ...]:
    """Wheel events go to the window under the pointer, so the pointer moves first. Positive dy scrolls up."""
    wheel: CGEvent = Quartz.CGEventCreateScrollWheelEvent(source, Quartz.kCGScrollEventUnitPixel, 2, dy_px, dx_px)
    Quartz.CGEventSetLocation(wheel, (at.x, at.y))  # a new event starts at the pointer, which has not moved yet
    return (*move_events(source, at, 0.03), TimedEvent(wheel, 0.03))


def chord_events(source: EventSource, chord: Chord) -> tuple[TimedEvent, ...]:
    """Modifier key-downs, the key, then the modifier key-ups (some apps follow flagsChanged, not per-event flags)."""
    flags: int = modifier_flags(chord.modifiers)
    events: list[TimedEvent] = []
    for modifier in chord.modifiers:
        events.append(TimedEvent(Quartz.CGEventCreateKeyboardEvent(source, _MODIFIER_KEYCODES[modifier], True), 0.005))
    for is_down in (True, False):
        event: CGEvent = Quartz.CGEventCreateKeyboardEvent(source, _KEYCODES[chord.key], is_down)
        Quartz.CGEventSetFlags(event, flags)
        events.append(TimedEvent(event, 0.01))
    for modifier in reversed(chord.modifiers):
        events.append(TimedEvent(Quartz.CGEventCreateKeyboardEvent(source, _MODIFIER_KEYCODES[modifier], False), 0.005))
    return tuple(events)


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def chunk_utf16(text: str, max_units: int) -> tuple[str, ...]:
    """Pieces of at most ``max_units`` UTF-16 units; a surrogate pair (emoji) is never split."""
    chunks: list[str] = []
    current: str = ""
    used: int = 0
    for character in text:
        units: int = utf16_units(character)
        if used + units > max_units:
            chunks.append(current)
            current, used = "", 0
        current += character
        used += units
    if current:
        chunks.append(current)
    return tuple(chunks)


def _press_key_events(source: EventSource, key: str) -> tuple[TimedEvent, ...]:
    return chord_events(source, Chord(key=key, modifiers=()))


def _extends_unit(character: str, previous: str) -> bool:
    """Whether ``character`` belongs with the character before it: a combining mark, a variation selector, a skin tone
    modifier, or a part of an emoji sequence joined by a zero-width joiner."""
    code: int = ord(character)
    return (
        unicodedata.category(character) in _MARK_CATEGORIES
        or 0xFE00 <= code <= 0xFE0F
        or 0x1F3FB <= code <= 0x1F3FF
        or character == _JOINER
        or previous == _JOINER
    )


def typed_units(text: str) -> tuple[str, ...]:
    """``text`` as the pieces that are typed with one key event each: a character together with the marks it carries."""
    units: list[str] = []
    for character in text:
        if units and _extends_unit(character, units[-1][-1]):
            units[-1] += character
        else:
            units.append(character)
    return tuple(units)


def presses_keys(text: str) -> bool:
    """Whether typing ``text`` presses Return or Tab (for a line break or a tab), which a paste would not."""
    return "\n" in text or "\t" in text


def text_events(source: EventSource, text: str) -> tuple[TimedEvent, ...]:
    """Type ``text`` as Unicode strings, one key event per character (calculators, terminals and games read a single
    character per event); a newline presses Return and a tab presses Tab."""
    events: list[TimedEvent] = []
    for line_index, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        if line_index > 0:
            events.extend(_press_key_events(source, _RETURN_KEY))
        for tab_index, segment in enumerate(line.split("\t")):
            if tab_index > 0:
                events.extend(_press_key_events(source, _TAB_KEY))
            for unit in typed_units(segment):
                for chunk in chunk_utf16(unit, _MAX_UNITS_PER_EVENT):
                    for is_down in (True, False):
                        event: CGEvent = Quartz.CGEventCreateKeyboardEvent(source, 0, is_down)
                        Quartz.CGEventKeyboardSetUnicodeString(event, utf16_units(chunk), chunk)
                        events.append(TimedEvent(event, 0.004))
    return tuple(events)


def post(events: Sequence[TimedEvent]) -> None:
    """Send the events to the session's HID tap and wait out each pause."""
    for item in events:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, item.event)
        time.sleep(item.pause_s)


def _snapshot_pasteboard(pasteboard: PasteboardLike) -> PasteboardSnapshot:
    items: Sequence[PasteboardItemLike] = pasteboard.pasteboardItems() or ()
    snapshot: list[tuple[tuple[str, bytes], ...]] = []
    for item in items:
        pairs: list[tuple[str, bytes]] = []
        for data_type in item.types():
            data: object | None = item.dataForType_(data_type)
            if data is not None:  # promised but unfulfilled types answer None
                pairs.append((str(data_type), bytes(data)))  # type: ignore[call-overload]
        snapshot.append(tuple(pairs))
    return tuple(snapshot)


def _restore_pasteboard(pasteboard: PasteboardLike, snapshot: PasteboardSnapshot) -> None:
    pasteboard.clearContents()
    items: list[PasteboardItemLike] = []
    for pairs in snapshot:
        item: PasteboardItemLike = NSPasteboardItem.alloc().init()
        for data_type, data in pairs:
            item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), data_type)
        items.append(item)
    if items:
        pasteboard.writeObjects_(items)


def read_clipboard() -> str | None:
    """The plain text on the clipboard, or ``None`` when it holds something else."""
    pasteboard: PasteboardLike = NSPasteboard.generalPasteboard()
    text: str | None = pasteboard.stringForType_(_PLAIN_TEXT_TYPE)
    return None if text is None else str(text)


def write_clipboard(text: str) -> None:
    pasteboard: PasteboardLike = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    if not pasteboard.setString_forType_(text, _PLAIN_TEXT_TYPE):
        raise DesktopError("the clipboard refused the text", {"chars": len(text)})


def paste_text(source: EventSource, text: str, settle_s: float) -> None:
    """Paste ``text`` through the clipboard and put the previous clipboard back unless someone changed it meanwhile."""
    pasteboard: PasteboardLike = NSPasteboard.generalPasteboard()
    previous: PasteboardSnapshot = _snapshot_pasteboard(pasteboard)
    pasteboard.clearContents()
    if not pasteboard.setString_forType_(text, _PLAIN_TEXT_TYPE):
        raise DesktopError("the clipboard refused the text", {"chars": len(text)})
    pasteboard.setString_forType_("", _TRANSIENT_TYPE)  # clipboard managers skip transient items
    ours: int = pasteboard.changeCount()
    post(chord_events(source, Chord(key="v", modifiers=("cmd",))))
    time.sleep(settle_s)  # the target reads the pasteboard asynchronously (Electron needs 100-300 ms)
    if pasteboard.changeCount() == ours:
        _restore_pasteboard(pasteboard, previous)
