"""The words Dot sends on a channel (item 572): an answer as plain text, cut to the channel's size with a link to the
chat for the rest, and the one line that tells a run waits for the owner. A messaging app shows what it is given as it
is: no Markdown is drawn, so what an answer holds of it is left out."""

import re

from dotagent.core.text import plain_line, text_window

# A fence's own line (``` or ~~~, with its language) is left out and the code in it stays; the marks of a heading go and
# its words stay; a link keeps its address in brackets after its words, as the page it points to would be lost else.
_FENCE: re.Pattern[str] = re.compile(r"^[ \t]*(?:```|~~~)[^\n]*\n?", re.MULTILINE)
_HEADING: re.Pattern[str] = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_LINK: re.Pattern[str] = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)\)")
# Bold, strike and code marks that come in pairs on one line; a lone star or underscore stays (``snake_case``, a * b).
_PAIRED: re.Pattern[str] = re.compile(r"(\*\*|__|~~|`)(?=\S)(.+?)(?<=\S)\1")
_BLANK_RUNS: re.Pattern[str] = re.compile(r"\n{3,}")
# What a cut leaves of the answer is followed by this line.
_REST: str = "\n\n… The rest of the answer is in Dot: "


def as_plain_text(markdown: str) -> str:
    """``markdown`` as the plain text a messaging app shows well: fences, heading marks and paired bold or code marks
    are left out, a link reads ``words (address)``, and runs of blank lines shrink to one."""
    text: str = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _FENCE.sub("", text)
    text = _HEADING.sub("", text)
    text = _LINK.sub(lambda found: f"{found.group(1)} ({found.group(2)})" if found.group(1) else found.group(2), text)
    text = _PAIRED.sub(lambda found: found.group(2), text)
    return _BLANK_RUNS.sub("\n\n", text).strip()


def cut_with_link(text: str, limit: int, link: str) -> str:
    """``text`` as it is when it fits ``limit`` characters; else its start, cut at a line or word break when one is
    close, then a line that says where the rest is: ``link``, the chat in Dot. The result is within ``limit``."""
    if len(text) <= limit:
        return text
    tail: str = f"{_REST}{link}"
    head, _ = text_window(text, 0, max(0, limit - len(tail)))
    return f"{head.rstrip()}{tail}"


def one_line(text: str, chars: int) -> str:
    """``text`` as one line of at most ``chars`` characters, its marks left out and its end marked when it was cut."""
    flat: str = plain_line(text, len(text))
    return flat if len(flat) <= chars else f"{flat[: chars - 1].rstrip()}…"
