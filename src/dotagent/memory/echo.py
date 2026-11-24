"""Which of the notes a run was handed show in what it did (audit 2026-11-24, I4): a cheap trace, no model asked.

A hand-out counts as a use of a note, so the notes handed most rise whatever they did for a run. This trace says, per
run and note, whether the run's answer or the arguments of its tool calls carry the note's id or at least two of its
distinctive words (words of five letters or more that are no function words; a note with one such word needs that one).
It is a rough signal for audits, never a ranking: a run may follow a note in words of its own, and a common word may
match a note it never read.
"""

import re
from collections.abc import Sequence

from dotagent.core.text import fold_search
from dotagent.memory.words import lowercase, search_terms
from dotagent.store.models import MemoryRecord

_WORD: re.Pattern[str] = re.compile(r"\w{2,}")
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
