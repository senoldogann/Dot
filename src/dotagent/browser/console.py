"""What a tab's page wrote to its console and the errors it did not catch (item 746): the messages of ``console.error``
and its kin and the uncaught exceptions of its scripts, which a site's builder reads to see why a page does not work.
Read only: nothing is run in the page, and the messages are kept as they came, a bounded few per tab."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from playwright.async_api import ConsoleMessage
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.network import hidden_address, hidden_text

# The messages a tab keeps, newest last: the last of a page's own run, and no more held in memory.
KEPT_MESSAGES: int = 100
# The levels a message is listed under.
LEVELS: tuple[str, ...] = ("error", "warning", "info", "log", "debug")
# How much of one message is kept, and how much of it is shown: a page that logs a whole object writes pages.
_KEPT_CHARS: int = 2000
SHOWN_CHARS: int = 400


@dataclass(slots=True)
class ConsoleEntry:
    """One console message or uncaught error of a page: its level, its text, and the script line that wrote it."""

    level: str
    text: str
    url: str
    line: int
    uncaught: bool


def _cut(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def note_message(kept: deque[ConsoleEntry], message: ConsoleMessage) -> None:
    """Keep ``message``, a line of the page's console, among a tab's; a type that is no level (``dir``, ``table``,
    ``trace``...) is a plain log line."""
    where = message.location
    kept.append(
        ConsoleEntry(
            level=message.type if message.type in LEVELS else "log",
            text=_cut(message.text, _KEPT_CHARS),
            url=str(where.get("url") or ""),
            line=int(where.get("lineNumber") or 0),
            uncaught=False,
        )
    )


def note_page_error(kept: deque[ConsoleEntry], error: PlaywrightError) -> None:
    """Keep ``error``, an exception no script of the page caught, among a tab's, as an error of the page."""
    words: str = f"{error.name}: {error.message}" if error.name else error.message
    kept.append(ConsoleEntry(level="error", text=_cut(words, _KEPT_CHARS), url="", line=0, uncaught=True))


def console_lines(
    entries: list[ConsoleEntry], level: str | None, limit: int, shown_as: Callable[[str], str], hide_secrets: bool
) -> list[str]:
    """The entries of ``level`` (all when none), newest first, ``limit`` at most, each as one line: its level, an
    uncaught error marked, its text cut short (its secrets hidden with ``hide_secrets``) and the script line that wrote
    it, its address as ``shown_as`` gives it."""
    shown: list[str] = []
    for entry in reversed(entries):
        if level is not None and entry.level != level:
            continue
        text: str = hidden_text(entry.text) if hide_secrets else entry.text
        mark: str = "uncaught " if entry.uncaught else ""
        address: str = shown_as(hidden_address(entry.url) if hide_secrets else entry.url)
        where: str = f" ({address}:{entry.line})" if entry.url else ""
        shown.append(f"{entry.level} {mark}{_cut(text, SHOWN_CHARS)}{where}")
        if len(shown) >= limit:
            break
    return shown
