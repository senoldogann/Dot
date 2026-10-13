"""The ranking of help for a need: Okapi BM25 (k1 1.2, b 0.75) of the need's words over the words of each item. Words
are compared folded (case and accents do not matter), without common English words, a plural made singular. Everything
here is pure."""

import math
import re
from collections import Counter
from collections.abc import Sequence

from dotagent.core.text import fold_search
from dotagent.help.types import HelpItem

_WORD: re.Pattern[str] = re.compile(r"[^\W_]+")
# Words that say nothing of what a thing is for.
_COMMON: frozenset[str] = frozenset(
    {
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "up",
        "us",
        "was",
        "we",
        "what",
        "when",
        "which",
        "with",
        "you",
        "your",
    }
)
_K1: float = 1.2
_B: float = 0.75
# An item that scores less than this share of the best one is left out: it shares only the weaker words of the need
# ("event" with every application named Events, when the need is an event of a calendar).
_FLOOR: float = 0.5


def _singular(word: str) -> str:
    """``events`` is ``event``, ``classes`` ``class``, ``branches`` ``branch`` and ``entries`` ``entry``; a word of
    three letters or less stays as it is, and so does one that ends in ``ss``, ``us`` or ``is``."""
    if len(word) <= 3 or not word.endswith("s") or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return f"{word[:-3]}y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    return word[:-1]


def words(text: str) -> list[str]:
    """The words of ``text`` that tell what it is about, in order: its runs of letters and digits (``_`` and ``-`` part
    them), folded (:func:`fold_search`), without single letters and common words, each made singular."""
    return [_singular(word) for word in _WORD.findall(fold_search(text)) if len(word) > 1 and word not in _COMMON]


def _weight(items: int, holding: int) -> float:
    """How much a word tells: more the fewer of the ``items`` hold it; never zero."""
    return math.log(1 + (items - holding + 0.5) / (holding + 0.5))


def _fit(count: int, length: int, average: float) -> float:
    """What ``count`` uses of a word give an item of ``length`` words, next to the ``average`` length: each use gives
    less than the one before, and a long item's uses give less than a short one's."""
    return count * (_K1 + 1) / (count + _K1 * (1 - _B + _B * length / average))


def rank(items: Sequence[HelpItem], need: str, limit: int) -> list[HelpItem]:
    """The ``limit`` items of ``items`` that fit ``need`` best, the best first, by Okapi BM25 of the need's words over
    the words of each item's ``text``. An item that shares no word with the need is left out, and so is one that
    scores less than half as well as the best one; items that score the same keep their order."""
    asked: frozenset[str] = frozenset(words(need))
    bags: list[Counter[str]] = [Counter(words(item.text)) for item in items]
    if not asked or not bags:
        return []
    lengths: list[int] = [bag.total() for bag in bags]
    average: float = sum(lengths) / len(bags)
    weights: dict[str, float] = {word: _weight(len(bags), sum(1 for bag in bags if word in bag)) for word in asked}
    scored: list[tuple[float, int]] = []
    for index, (bag, length) in enumerate(zip(bags, lengths, strict=True)):
        score: float = sum(weights[word] * _fit(bag[word], length, average) for word in asked if bag[word])
        if score > 0:
            scored.append((score, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    if not scored:
        return []
    floor: float = scored[0][0] * _FLOOR
    return [items[index] for score, index in scored[:limit] if score >= floor]
