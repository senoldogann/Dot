"""Whether a run's answer restates a text reply of the run (item 620). A task run that answers in a plain reply is told
to finish (the loop's note, item 460), and the finish writes the answer again, often in other words: the owner's chat of
2026-10-24 showed a screenshot's description twice, its every line reworded. The page drops a reply the answer restates;
one that says what the answer does not (a warning the finish left out, the review of item 567) stays.

A reply is restated when the answer is at least half its length (an answer that only points at it, "see above", leaves
it to be read), when every line of it that holds words has most of its words in the answer (a reworded line keeps its
names and the words that carry it, and a line the answer left out shares next to none), and when every number and every
word that turns a line's sense ("not", "değil") of the reply is in the answer too: an answer that lost one says another
thing, and the reply stays, as the comparison of words keeps such a reply (item 569). A Turkish verb made negative by
its ending (bitmedi) is a word of its own, which the share of words weighs like any other."""

import re

# The words of a line: letters and digits with the marks that hold inside a word or a number (16:17, %81, 2.6, +178,
# web-research, Dock'ta), in any script.
_WORD: re.Pattern[str] = re.compile(r"[\w%+]+(?:[.:'’/-][\w%+]+)*")
# Words too common to tell two lines apart, in the languages the owner writes and Dot answers in.
_COMMON: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "are", "was", "has", "have", "from", "but", "not", "its", "you",
        "your", "can", "will", "all", "one", "also", "there", "here", "what", "which", "into", "about", "then",
        "bir", "ile", "için", "olarak", "gibi", "çok", "daha", "ama", "veya", "her",
    }
)  # fmt: skip
# Words that turn a line's sense: one that the answer lost makes it say another thing.
_NOT: frozenset[str] = frozenset(
    {
        "not", "no", "never", "none", "nothing", "nobody", "neither", "nor", "cannot", "can't", "don't", "doesn't",
        "didn't", "isn't", "aren't", "wasn't", "weren't", "won't", "wouldn't", "shouldn't", "couldn't", "hasn't",
        "haven't", "hadn't", "değil", "yok", "hayır", "hiç", "asla",
    }
)  # fmt: skip
# The share of a line's words that the answer must hold for the line to be in it.
_HELD: float = 0.5


def _tokens(text: str) -> list[str]:
    """Every word of ``text``, in lower case, with a curly apostrophe made straight."""
    return [word.casefold().replace("’", "'") for word in _WORD.findall(text)]


def _number(word: str) -> str:
    """The digits of ``word``, as a number is compared whatever marks it is written with (16:17, %81, 3'ü)."""
    return "".join(c for c in word if c.isdigit())


def _words(text: str) -> list[str]:
    """The words of ``text`` that tell it apart: in lower case, the common ones and the words of one or two letters
    left out (as, is, ve, da); a word with a digit always counts (T3, 16:17)."""
    return [word for word in _tokens(text) if _number(word) or (len(word) > 2 and word not in _COMMON)]


def restates(reply: str, answer: str) -> bool:
    """Whether ``answer`` restates ``reply``: it is at least half the reply's length, each of the reply's lines that
    holds words has at least half of them in the answer, and the answer holds every number and every "not" of the
    reply."""
    if len(answer.strip()) * 2 < len(reply.strip()):
        return False
    tokens: list[str] = _tokens(answer)
    held: set[str] = set(_words(answer))
    numbers: set[str] = {_number(word) for word in tokens if _number(word)}
    nots: set[str] = {word for word in tokens if word in _NOT}
    for line in reply.splitlines():
        said: list[str] = _tokens(line)
        if any(word in _NOT and word not in nots for word in said):
            return False
        if any(_number(word) and _number(word) not in numbers for word in said):
            return False
        words: list[str] = _words(line)
        if words and sum(word in held for word in words) < _HELD * len(words):
            return False
    return True
