"""The mode a message asks for in its words (item 495), for the composer's Auto, which takes the mode picker's place.
A message that asks only to look runs as research, which reads and changes nothing: "read-only", "don't change
anything", "just research", and in Turkish "salt okunur", "hiçbir şeyi değiştirme", "sadece araştır", written with the
Turkish letters or without them. Any other runs as a task, which can do the most; a task that answers without a tool
ends as a chat does, so a question is answered as one. The words only ever narrow: Auto never picks a mode that can do
more than a task, and a mode the owner names (``/task``, ``/chat``, ``/research``) is the mode."""

import re

from dotagent.store.models import ChatMode, RunMode

# Whole words only. Strong phrases alone: "just look and fix it" is a task, and so is a goal that names a change to
# avoid among others ("don't change the colours, only the font"); only an ask to change nothing at all, or to research
# only, is read here.
_ONLY_LOOKS: re.Pattern[str] = re.compile(
    r"\b(?:"
    r"read[- ]only"
    r"|(?:don['’]?t|do not|without) (?:chang(?:e|ing)|modify(?:ing)?|touch(?:ing)?|edit(?:ing)?) (?:anything|a thing)"
    r"|change nothing|touch nothing"
    r"|(?:just|only) research|research only"
    r"|salt okunur"
    r"|h[iı][çc]bir [şs]ey(?:i)? (?:de[ğg]i[şs]tirme|de[ğg]i[şs]tirmeden|dokunma|dokunmadan)"
    r"|(?:sadece|yaln[ıi]zca) ara[şs]t[ıi]r(?:ma yap)?(?![a-zçğıöşü])"
    r")\b",
    re.IGNORECASE,
)


def only_looks(goal: str) -> str | None:
    """The words of ``goal`` that ask its run only to look, as written, or ``None`` when it asks nothing of the kind."""
    # Python folds the Turkish capital İ to no single lower-case letter, so it is read as i; the length stays, and the
    # words are cut from the goal as written.
    found: re.Match[str] | None = _ONLY_LOOKS.search(goal.replace("İ", "i"))
    return None if found is None else goal[found.start() : found.end()]


def resolved_mode(asked: ChatMode, goal: str) -> RunMode:
    """The mode a message runs in: the one asked for, or for ``auto`` research when ``goal`` asks only to look and a
    task otherwise."""
    if asked != "auto":
        return asked
    return "research" if only_looks(goal) is not None else "task"
