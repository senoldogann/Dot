"""A key chord as a step names it (``cmd+shift+4``): its key and its modifiers, and the keys a step can press.

Pure, apart from the events that press it (:mod:`dotagent.desktop.input`), so that what reads a chord (the safety of a
step) loads where Quartz is not, as on the Linux that runs the unit tests of a cloud agent (item 1270).
"""

from dataclasses import dataclass

from dotagent.core.errors import DesktopError
from dotagent.desktop.types import Modifier


@dataclass(frozen=True, slots=True)
class Chord:
    key: str
    modifiers: tuple[Modifier, ...]


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
KEYCODES: dict[str, int] = {
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
    if key not in KEYCODES:
        raise DesktopError(
            f"{key!r} in {chord!r} is not a key this tool can press; use `type` to enter text",
            {"chord": chord, "known_keys": ", ".join(sorted(KEYCODES))},
        )
    return Chord(key=key, modifiers=tuple(dict.fromkeys(modifiers)))
