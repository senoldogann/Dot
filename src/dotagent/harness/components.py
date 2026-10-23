"""The components of a version of the harness (item 389, docs/harness.md): the texts a run's system prompt takes from
the adopted version, and what makes a text no component. Two for now: habits (how to work here, after how to work) and
tool guidance (what earlier runs learned of the tools on this Mac, after the tools). Both are plain text the owner
reads on one screen, bounded in size, with nothing in them that must not stand in a prompt for good: a secret, a link,
an e-mail address, a path in a home folder, an account name (any of which a page a run read may have put into a
proposal; the bounds of a lesson's, :mod:`dotagent.agent.lessons`), or a character that does not print, which could
hide a line from the owner who reads the text."""

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from dotagent.core.text import redact_known
from dotagent.core.types import FrozenModel

type Component = Literal["habits", "tool_guidance"]
COMPONENTS: tuple[Component, ...] = ("habits", "tool_guidance")
# The longest line a component may hold: a rule is a sentence or two, never a page.
LINE_CHARS: int = 300
_EMAIL: re.Pattern[str] = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOME_PATH: re.Pattern[str] = re.compile(r"/(?:Users|home)/(?!Shared(?:/|\b))[^/\s]+", re.IGNORECASE)
_LINK: re.Pattern[str] = re.compile(r"\b(?:[a-z][a-z0-9+.-]*://|www\.)\S", re.IGNORECASE)
_HANDLE: re.Pattern[str] = re.compile(r"(?<![\w.@])@[A-Za-z0-9_]{2,}")
# The Unicode categories of the characters that do not print: controls, format characters (zero-width, bidi, tags),
# surrogates, private use and unassigned code points.
_UNPRINTABLE: frozenset[str] = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


class HarnessText(FrozenModel):
    """What the adopted version adds to a run's prompt; an empty component adds nothing."""

    habits: str
    tool_guidance: str

    def component(self, name: Component) -> str:
        return self.habits if name == "habits" else self.tool_guidance


# Dot's own text alone: what a run takes while no version is adopted.
NO_HARNESS: HarnessText = HarnessText(habits="", tool_guidance="")
# The mark before a rule of a component that is a list: a bullet or a number.
_LIST_MARK: re.Pattern[str] = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")


def rules_of(text: HarnessText) -> tuple[str, ...]:
    """The rules the adopted ``text`` gives a run, one line each, the habits first and then the tool guidance, a
    bullet or a number before a rule taken off and the blank lines left out: what a lesson is compared with, so that
    the memory does not learn again what the prompt already says (item 584)."""
    return tuple(
        line
        for name in COMPONENTS
        for raw in text.component(name).splitlines()
        if (line := _LIST_MARK.sub("", raw).strip())
    )


def normalized(text: str) -> str:
    """``text`` as a component is kept: line breaks as newlines, no trailing spaces on a line, at most one blank line in
    a row and none at either end."""
    lines: list[str] = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    kept: list[str] = []
    for line in lines:
        if line == "" and (not kept or kept[-1] == ""):
            continue
        kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept)


def unprintable(text: str) -> str | None:
    """The first character of ``text`` that does not print, as ``U+XXXX``, or ``None``: a control (a newline and a tab
    apart), a format character (zero-width, bidi, tag), a surrogate, a private-use or an unassigned code point, any of
    which could hide text from the owner who reads it."""
    return next(
        (f"U+{ord(char):04X}" for char in text if char not in "\n\t" and unicodedata.category(char) in _UNPRINTABLE),
        None,
    )


def component_problem(text: str, max_chars: int, max_lines: int, secrets: Sequence[str]) -> str | None:
    """Why ``text`` cannot be a component, or ``None`` when it can: longer than ``max_chars``, more than ``max_lines``
    lines, a line past :data:`LINE_CHARS`, a character that does not print (:func:`unprintable`), a secret (one of
    ``secrets``, the values Dot holds, or a key of a known form), a link, an e-mail address, a path in a home folder or
    an account name; the last four are looked for in the text as it is and with its compatibility characters folded
    (NFKC), so that a fullwidth scheme or sign is one too."""
    if len(text) > max_chars:
        return f"too long: {len(text)} characters, the limit is {max_chars}"
    lines: list[str] = text.split("\n") if text else []
    if len(lines) > max_lines:
        return f"too many lines: {len(lines)}, the limit is {max_lines}"
    long_line: int | None = next((number for number, line in enumerate(lines, start=1) if len(line) > LINE_CHARS), None)
    if long_line is not None:
        return f"line {long_line} is longer than {LINE_CHARS} characters"
    hidden: str | None = unprintable(text)
    if hidden is not None:
        return f"a character that does not print ({hidden})"
    if redact_known(text, secrets) != text:
        return "a secret"
    views: tuple[str, str] = (text, unicodedata.normalize("NFKC", text))
    if any(_LINK.search(view) is not None for view in views):
        return "a link"
    if any(_EMAIL.search(view) is not None for view in views):
        return "an e-mail address"
    if any(_HOME_PATH.search(view) is not None for view in views):
        return "a path in a home folder"
    if any(_HANDLE.search(view) is not None for view in views):
        return "an account name"
    return None
