"""A model's reply as it streams, with the secrets Dot holds masked in it (item 551). The message a step stores is
masked whole, so what the screens draw while the step streams must not show the start of a secret that its next delta
completes. Only what may still become part of one waits: plain words are shown as they come, so the reply streams as
it did and its end adds no more than the word it was writing."""

from collections.abc import Sequence
from typing import Literal

from dotagent.core.text import KNOWN_SECRET_MIN_CHARS, redact_known
from dotagent.llm.types import DeltaSink, StreamDelta

# Put after the text that waits: a mask that takes it in, with the secret before it, says the text ends inside a secret
# that the next characters would lengthen.
_PROBE: str = "A"
# Put after the last words, alone or after the separator that may still come between a name and its value: a mask
# that takes it in says they introduce a secret (``Bearer``, ``password =``, ``Authorization: Basic``, ``--token``),
# whose value is still to come, so they wait for it.
_VALUE_PROBE: str = "Zq7Xv0Wk3Rt8Yp2Mn5Lb9Hc4"
_SEPARATORS: tuple[str, ...] = (" ", ": ", " = ", "=")
# How many words before the value a secret's introduction takes at most (``Authorization: Basic`` is two).
_INTRODUCTION_WORDS: int = 4
# The header of a PEM block: from it to its end the block is a private key's, which the mask takes whole.
_BLOCK_START: str = "-----BEGIN"
_BLOCK_EDGE: str = "-----"


def _in_word(char: str) -> bool:
    """Whether ``char`` can be a character of a key or a token: one that is not a space, in ASCII, where a key is. A run
    of the words of a language written without spaces has none, so it is cut where it is."""
    return char.isascii() and not char.isspace()


def _word_start(text: str) -> int:
    """Where the word ``text`` ends with starts, the word being written; ``len(text)`` when it ends with none."""
    start: int = len(text)
    while start > 0 and _in_word(text[start - 1]):
        start -= 1
    return start


def _word_starts(text: str, before: int, most: int) -> list[int]:
    """Where the last ``most`` words of ``text[:before]`` start, the nearest first."""
    starts: list[int] = []
    at: int = before
    while at > 0 and len(starts) < most:
        while at > 0 and text[at - 1].isspace():
            at -= 1
        start: int = at
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        if start == at:
            break
        starts.append(start)
        at = start
    return starts


def _block_start(text: str) -> int:
    """Where the header of a PEM block starts on the last line of ``text`` when the header has not ended yet;
    ``len(text)`` when none is under way."""
    line: int = text.rfind("\n") + 1
    begin: int = text.rfind(_BLOCK_START, line)
    if begin == -1 or text.find(_BLOCK_EDGE, begin + len(_BLOCK_START)) != -1:
        return len(text)
    return begin


class StreamMask:
    """The deltas of a step on their way to the screens (``show``), masked as the stored message is. The text so far is
    masked as a whole, so a secret split between two deltas is found; what may still become a part of one waits: the
    word being written, the end of the text where it is the start of a secret Dot holds, words that introduce a secret
    whose value is still to come, the header of a private key's block, and text that ends inside a secret which the
    next characters would lengthen. :meth:`flush` shows what waits when the step's stream ends. A secret that is
    quoted, has spaces in it and is introduced by more than a few words can show its start before its end comes; the
    stored message masks it whole."""

    def __init__(self, show: DeltaSink) -> None:
        self._show: DeltaSink = show
        self._known: tuple[str, ...] = ()
        self._kind: Literal["text", "thinking"] = "text"
        self._waiting: str = ""

    def begin(self, known: Sequence[str]) -> None:
        """The stream of the next step: masked with the secrets ``known`` (:func:`dotagent.secrets.known_secrets`);
        what the last one left waiting is dropped."""
        self._known = tuple(value for value in known if len(value) >= KNOWN_SECRET_MIN_CHARS)
        self._waiting = ""

    async def __call__(self, delta: StreamDelta) -> None:
        if delta.kind == "reset":
            self._waiting = ""
            await self._show(delta)
            return
        if delta.kind != self._kind:
            await self.flush()
            self._kind = delta.kind
        shown, self._waiting = self._settled(self._waiting + delta.text)
        if shown:
            await self._show(StreamDelta(kind=self._kind, text=shown))

    async def flush(self) -> None:
        """The stream of the step has ended: what waits is shown, masked."""
        rest: str = self._waiting
        self._waiting = ""
        if rest:
            await self._show(StreamDelta(kind=self._kind, text=redact_known(rest, self._known)))

    def _secret_start(self, text: str) -> int:
        """Where the longest end of ``text`` that is the start of a secret Dot holds begins; ``len(text)`` when none
        is."""
        start: int = len(text)
        for value in self._known:
            for size in range(min(len(value) - 1, len(text)), 0, -1):
                if text.endswith(value[:size]):
                    start = min(start, len(text) - size)
                    break
        return start

    def _introduction_start(self, text: str, before: int) -> int:
        """Where the last words of ``text[:before]`` start when they introduce a secret whose value is still to come
        (:data:`_VALUE_PROBE`), with or without the separator that may come before it (``password`` waits for its
        ``=``); ``len(text)`` when they do not."""
        for start in _word_starts(text, before, _INTRODUCTION_WORDS):
            words: str = text[start:before]
            for separator in _SEPARATORS:
                if not redact_known(f"{words}{separator}{_VALUE_PROBE}", self._known).endswith(_VALUE_PROBE):
                    return start
        return len(text)

    def _settled(self, waiting: str) -> tuple[str, str]:
        """The start of ``waiting`` that no later text can change, masked, and the rest of it, as it is."""
        whole: str = redact_known(waiting, self._known)
        if redact_known(f"{waiting}{_PROBE}", self._known) != f"{whole}{_PROBE}":
            return "", waiting
        word: int = _word_start(waiting)
        cut: int = min(
            word, self._secret_start(waiting), self._introduction_start(waiting, word), _block_start(waiting)
        )
        if cut <= 0:
            return "", waiting
        head: str = redact_known(waiting[:cut], self._known)
        if f"{head}{redact_known(waiting[cut:], self._known)}" != whole:
            # A secret stands across the cut: it is shown once it has ended.
            return "", waiting
        return head, waiting[cut:]
