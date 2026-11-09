"""Item 1086: a rule of the UI's stylesheets that styles a pointer resting on something (``:hover``) applies only where
a pointer can rest, inside ``@media (hover: hover)``. A phone keeps the hover of the last thing a finger pressed: the
owner's screenshots of 2026-11-09 showed a chat's "…" and a row left tinted after a tap, everywhere."""

import re
from pathlib import Path

_CSS: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static" / "css"


def _closing(text: str, start: int) -> int:
    """The index of the brace that closes the one at ``start``, comments skipped."""
    depth: int = 0
    index: int = start
    while index < len(text):
        if text.startswith("/*", index):
            index = text.index("*/", index) + 2
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise AssertionError(f"a block opened at {start} is never closed")


def _bare_hovers(text: str, inside_hover: bool) -> list[str]:
    """The selectors with ``:hover`` of ``text``'s rules that no ``@media (hover: hover)`` holds."""
    found: list[str] = []
    index: int = 0
    while index < len(text):
        if text.startswith("/*", index):
            index = text.index("*/", index) + 2
            continue
        brace: int = text.find("{", index)
        semicolon: int = text.find(";", index)
        if brace == -1:
            break
        if semicolon != -1 and semicolon < brace:
            index = semicolon + 1
            continue
        # The comments before a rule are no part of its head.
        head: str = re.sub(r"/\*.*?\*/", "", text[index:brace], flags=re.S).strip()
        end: int = _closing(text, brace)
        if head.startswith("@"):
            found += _bare_hovers(text[brace + 1 : end], inside_hover or "hover: hover" in head)
        elif ":hover" in head and not inside_hover:
            found.append(" ".join(head.split()))
        index = end + 1
    return found


def test_every_hover_style_waits_for_a_pointer_that_can_rest() -> None:
    bare: list[str] = [
        f"{sheet.name}: {selector}"
        for sheet in sorted(_CSS.glob("*.css"))
        for selector in _bare_hovers(sheet.read_text(encoding="utf-8"), False)
    ]
    assert bare == [], bare
