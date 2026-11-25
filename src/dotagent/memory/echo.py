"""Which of the notes a run was handed show in what it did (audit 2026-11-24, I4): a cheap trace, no model asked.

A hand-out counts as a use of a note, so the notes handed most rise whatever they did for a run. This trace says, per
run and note, whether the run's answer or the arguments of its tool calls carry the note's id or at least two of its
distinctive words (words of five letters or more that are no function words; a note with one such word needs that one).
It is a rough signal for audits, never a ranking: a run may follow a note in words of its own, and a common word may
match a note it never read.

:func:`recalled_echo` is the opposite, exact one (item 1866): whether a note a run would save is the words of a note
recall handed that run, which it would write back as a new note (OpenClaw's recall-loop guard). Exact on the words, so
no guess: the store records every note a run was handed (``memory_uses``), and the new text says that note when its
words, as words (case, accents, spaces and punctuation aside), are the handed note's or hold them whole.
"""

import re
from collections.abc import Sequence

from dotagent.core.text import fold_search
from dotagent.memory.words import lowercase, search_terms
from dotagent.store.models import MemoryRecord

_WORD: re.Pattern[str] = re.compile(r"\w{2,}")
# Every word, one letter or digit too: "4 CPU cores" and "8 CPU cores" are two facts.
_ANY_WORD: re.Pattern[str] = re.compile(r"\w+")
# A handed note this many words long or longer is an echo inside a longer text too (the line recall drew it in, a
# sentence around it); a shorter one only when the text is its words alone, since three words are often said anew.
_MIN_CONTAINED_WORDS: int = 4
# A word shorter than this says too little about a note to show that a run used it.
_MIN_LETTERS: int = 5
# How many of a note's distinctive words a run's text must carry.
_WORDS_NEEDED: int = 2


def said_words(text: str) -> frozenset[str]:
    """Every word of ``text``, folded as the notes' words are (case, accents, the Turkish i)."""
    return frozenset(fold_search(word) for word in _WORD.findall(lowercase(text)))


def echoed_notes(notes: Sequence[MemoryRecord], text: str) -> tuple[str, ...]:
    """The ids of the ``notes`` that ``text`` (a run's answer and the arguments of its calls) names by id or echoes in
    at least two of their distinctive words, in the order of ``notes``."""
    words: frozenset[str] = said_words(text)
    echoed: list[str] = []
    for note in notes:
        terms: tuple[str, ...] = tuple(term for term in search_terms(note.content) if len(term) >= _MIN_LETTERS)
        needed: int = min(_WORDS_NEEDED, len(terms))
        hits: int = sum(1 for term in terms if term in words)
        if note.id in text or (needed > 0 and hits >= needed):
            echoed.append(note.id)
    return tuple(echoed)


def note_words(text: str) -> tuple[str, ...]:
    """The words of ``text`` in order, folded as the notes' words are, with no punctuation or spacing between them."""
    return tuple(_ANY_WORD.findall(fold_search(lowercase(text))))


def _holds(words: tuple[str, ...], part: tuple[str, ...]) -> bool:
    size: int = len(part)
    return any(words[start : start + size] == part for start in range(len(words) - size + 1))


def recalled_echo(content: str, handed: Sequence[MemoryRecord]) -> MemoryRecord | None:
    """The note of ``handed`` (the notes recall gave the run) whose words ``content`` is, or holds whole when the note
    has at least :data:`_MIN_CONTAINED_WORDS` words; ``None`` when it is none of them. The first such note of
    ``handed``."""
    words: tuple[str, ...] = note_words(content)
    for note in handed:
        said: tuple[str, ...] = note_words(note.content)
        if said and (said == words or (len(said) >= _MIN_CONTAINED_WORDS and _holds(words, said))):
            return note
    return None
