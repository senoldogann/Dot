"""What a channel does with a message of the owner before the router sees it (item 572): the limits that hold for
every channel. A message is read when it is the owner's words, not too long, not too old and not one of a flood;
the ones that are not are told why, once, in a line the channel sends back, or let go when telling would only add to a
flood. Nothing here decides who the owner is: that is each channel's own check of where a message came from."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from dotagent.channels.types import is_stop
from dotagent.channels.words import ENGLISH, Words

# The longest message Dot reads: a longer one is told so and not read, as cutting it would change what it asks.
MAX_IN_CHARS: int = 4000
# The most messages Dot reads in a minute; the rest of a flood is let go.
MAX_PER_MINUTE: int = 30
# A message older than this when Dot reads it (the Mac slept, Dot was not running) is not run: it may have been meant
# for a moment that is gone, and the owner has seen no answer to it.
STALE_S: float = 900.0
_WINDOW_S: float = 60.0

# The lines a message that is not read is told, in English; the channel says them in the owner's language (item 805).
TOO_OLD: str = ENGLISH.too_old
ATTACHMENT: str = ENGLISH.attachment_only
# A message that came with a picture or a file as well as words (item 611) runs on its words, with this note at the end
# of what the run is given, and the line the answer starts with says the same to the owner. The router finds the note
# at the end of the run's goal, so the two stay together through the chat's queue. The note is for the model, so it is
# in English whatever the owner writes.
UNREAD_NOTE: str = (
    "(Dot's note: this message came with a picture or a file, which Dot cannot read on this channel yet. Only its "
    "words reached you. Do not guess what the attachment shows.)"
)
UNREAD_ANSWER: str = ENGLISH.unread_answer
FLOOD: str = ENGLISH.flood


def too_long(chars: int, words: Words = ENGLISH) -> str:
    return words.fill(words.too_long, chars=chars, limit=MAX_IN_CHARS)


@dataclass(frozen=True)
class Accept:
    """The words to hand to the router."""

    text: str


@dataclass(frozen=True)
class Tell:
    """A line to send back to the owner instead."""

    notice: str


@dataclass(frozen=True)
class Skip:
    """Nothing to do, and why (for the log)."""

    reason: str


type Verdict = Accept | Tell | Skip


class Intake:
    """The limits of one channel, kept as messages come: how many were read in the last minute."""

    def __init__(self, words: Callable[[], Words] = lambda: ENGLISH) -> None:
        # The lines of the owner's language as it is now (item 805).
        self._words: Callable[[], Words] = words
        self._seen: deque[float] = deque()
        self._told_of_flood: bool = False

    def judge(self, text: str | None, sent_at: float, attachment: bool, now: float) -> Verdict:
        """What to do with a message sent at ``sent_at`` that came to be read at ``now``: ``text`` is its words
        (``None`` when they could not be read) and ``attachment`` whether it carries a file. A message whose words
        cannot be read and that carries no file is never answered, as it may be one of Dot's own (the owner's chat
        holds both). A file with no words is told it is not read; a file with words is accepted with the words and
        :data:`UNREAD_NOTE` after them, so that the run, and then the owner, know it was not looked at (item 611)."""
        if text is None and not attachment:
            return Skip("its words could not be read")
        words: str = (text or "").strip()
        # A stop is read whatever came before it (item 683): a run that floods the chat is what it is sent to stop.
        if is_stop(words) and now - sent_at <= STALE_S:
            return Accept(words)
        while self._seen and now - self._seen[0] >= _WINDOW_S:
            self._seen.popleft()
        if len(self._seen) >= MAX_PER_MINUTE:
            if self._told_of_flood:
                return Skip("more than 30 messages in a minute")
            self._told_of_flood = True
            return Tell(self._words().flood)
        self._told_of_flood = False
        self._seen.append(now)
        if now - sent_at > STALE_S:
            return Tell(self._words().too_old)
        if words == "":
            return Tell(self._words().attachment_only) if attachment else Skip("it holds no words")
        if len(words) > MAX_IN_CHARS:
            return Tell(too_long(len(words), self._words()))
        if attachment and not is_stop(words):
            # The words run, and the run is told what did not reach it (item 611); a stop is left as it was written.
            return Accept(f"{words}\n\n{UNREAD_NOTE}")
        return Accept(words)
