"""What a message sent to a chat with several tasks (item 2012, slice 2 of item 1982's design) says by its words alone,
before any model is asked: whether it is only a stop, whether it starts a new task with ``/new``, and which task's
message it quotes. Every function here is pure.

A quote is the block the pages and the phone write in front of the words (item 1972, ``quotebutton.js``'s
``splitQuote``): lines that start with ``>``, then a blank line. It names the message it quotes by its words alone, cut
at 280 characters with an ellipsis, so a task is found by the words of its messages."""

import re
from collections.abc import Sequence
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.store.models import RunStatus

# The words that are a stop and nothing else, folded (item 1982: "dur" and "stop", the owner's word of 2026-11-27).
STOP_WORDS: frozenset[str] = frozenset({"dur", "durdur", "durun", "stop", "halt"})
# A message that starts a new task, whatever the tasks at work: the command and its words.
NEW_TASK_PATTERN: re.Pattern[str] = re.compile(r"^/new(?:\s+(.*))?$", re.DOTALL)
# How much of a quote's start must be found in a task's message for the quote to name that task.
_QUOTE_PROBE_CHARS: int = 80
_QUOTE_MIN_CHARS: int = 8
_TRAILING_STOP: str = ".!?…"


class SplitMessage(FrozenModel):
    """A message's quote (``None`` when it has none) and its own words."""

    quote: str | None
    words: str


class TaskWords(FrozenModel):
    """A task of a chat by the words of its messages that a quote may name: the owner's and Dot's."""

    thread_id: str
    texts: tuple[str, ...]


def split_quote(text: str) -> SplitMessage:
    """``text``'s quote and words, as ``splitQuote`` of the pages reads them."""
    lines: list[str] = text.split("\n")
    quoted: list[str] = []
    while lines and lines[0].startswith(">"):
        quoted.append(re.sub(r"^> ?", "", lines.pop(0)))
    if not quoted or not lines or lines[0] != "":
        return SplitMessage(quote=None, words=text)
    return SplitMessage(quote="\n".join(quoted), words="\n".join(lines[1:]))


def _folded(text: str) -> str:
    return " ".join(text.replace("…", " ").split()).casefold()


def is_stop_only(words: str) -> bool:
    """Whether ``words`` are a stop word and nothing else ("dur", "Stop!")."""
    return _folded(words).strip(_TRAILING_STOP + " ") in STOP_WORDS


def new_task_words(words: str) -> str | None:
    """The words of a ``/new`` message, the new task's; ``None`` for any other message, and for ``/new`` with no
    words."""
    found: re.Match[str] | None = NEW_TASK_PATTERN.match(words.strip())
    if found is None:
        return None
    rest: str = (found.group(1) or "").strip()
    return rest or None


def quoted_task(quote: str, tasks: Sequence[TaskWords]) -> str | None:
    """The thread of the one task whose messages hold the start of ``quote``; ``None`` when none does, when more than
    one does (the quote cannot tell them apart), or when the quote is too short to tell anything."""
    probe: str = _folded(quote)[:_QUOTE_PROBE_CHARS].strip()
    if len(probe) < _QUOTE_MIN_CHARS:
        return None
    found: list[str] = [task.thread_id for task in tasks if any(probe in _folded(text) for text in task.texts)]
    unique: list[str] = list(dict.fromkeys(found))
    return unique[0] if len(unique) == 1 else None


# How long a task whose run ended with a question to the owner stays open for the router (item 2012).
OPEN_TASK_S: float = 24 * 3600.0
# How much of the end of a reply that asks is the question the router is shown.
_QUESTION_CHARS: int = 400

type RouteKind = Literal["stopped", "new", "queued", "steered", "continued"]


class ChatTask(FrozenModel):
    """A task of a chat (item 2012): its thread (the chat itself or a lane), its newest run of the owner's, the words
    that set that run going, whether it works, the question it waits on when it waits on one (``asked``), whether that
    question's words may be shown to the router (``shown``: its run read nothing a third party wrote), and the words of
    its messages a quote may name."""

    thread_id: str
    newest_run_id: str
    status: RunStatus
    goal: str
    at_work: bool
    asked: str | None
    shown: bool
    texts: tuple[str, ...]


class RoutedMessage(FrozenModel):
    """Where a message sent to a chat with several tasks went (item 2012): ``kind`` says how, ``thread_id`` is the
    task's thread (the chat for a stop of every task), ``run_id`` the run it started, steered or stopped,
    ``message_id`` the queued message it became."""

    kind: RouteKind
    thread_id: str
    run_id: str | None
    message_id: str | None


def prose_question(result: str) -> str | None:
    """The question a reply ends with, its last ``_QUESTION_CHARS`` characters, or ``None`` when it ends with none."""
    stripped: str = result.rstrip()
    if not stripped.endswith("?"):
        return None
    return stripped[-_QUESTION_CHARS:]
