"""The mode a message asks for in its words (item 495), for the composer's Auto, which takes the mode picker's place.
A message that asks only to look runs as research, which reads and changes nothing: "stay read-only", "don't change
anything", "just research", and in Turkish "salt okunur kal", "hiçbir şeyi değiştirme", "sadece araştır", written with
the Turkish letters or without them. Any other runs as a task, which can do the most; a task that answers without a tool
ends as a chat does, so a question is answered as one. The words only ever narrow: Auto never picks a mode that can do
more than a task, and a mode the owner names (``/task``, ``/chat``, ``/research``) is the mode."""

import re

from dotagent.store.models import ChatMode, RunMode

# Whole words only. Strong phrases alone: "just look and fix it" is a task, and so is a goal that names a change to
# avoid among others ("don't change the colours, only the font"); only an ask to change nothing at all, or to research
# only, is read here (item 691). "Read-only" and "salt okunur" are read only when they ask the run to be so ("stay
# read-only", "read-only: find out why", "salt okunur bak"); a task that makes or names something read-only ("make
# config.toml read-only", "create a read-only API key", "dosyayı salt okunur yap") is a task.
_ONLY_LOOKS: re.Pattern[str] = re.compile(
    r"\b(?P<words>"
    r"(?:don['’]?t|do not|without) (?:chang(?:e|ing)|modify(?:ing)?|touch(?:ing)?|edit(?:ing)?) (?:anything|a thing)"
    r"|change nothing|touch nothing"
    r"|(?:just|only) research|research only"
    r"|salt okunur (?:kal|[çc]al[ıi][şs]|ol|bak|incele"
    r"|(?:olarak|[şs]ekilde|bi[çc]imde|modda|modunda) (?:kal|ol|bak|incele|[çc]al[ıi][şs]|ara[şs]t[ıi]r|oku))"
    r"|h[iı][çc]bir [şs]ey(?:i)? (?:de[ğg]i[şs]tirme|de[ğg]i[şs]tirmeden|dokunma|dokunmadan)"
    r"|(?:sadece|yaln[ıi]zca) ara[şs]t[ıi]r(?:ma yap)?(?![a-zçğıöşü])"
    r")\b",
    re.IGNORECASE,
)
# The same words as a label that opens a sentence ("Read-only: find out why the build is slow", "Salt okunur, özetle"),
# where they cannot be about a thing the task makes.
_LABEL: re.Pattern[str] = re.compile(
    r"(?:^|(?<=[.!?\n]))\s*(?P<words>(?:please\s+)?(?:be\s+)?read[- ]only(?:\s+mode)?|salt okunur)"
    r"(?=\s*(?:[:,.;!\-–—]|please\b|l[üu]tfen\b|$))",
    re.IGNORECASE,
)
# What may follow "read-only" in an ask to the run: a mark, the end, or a word that goes on with the sentence; a noun
# there makes it an adjective of a thing ("run read-only checks", "a read-only key"), which no ask is.
_ASK_ENDS: str = r"(?=\s*(?:[,.;:!?)—–]|$|(?:and|then|while|but|on|in|for|to|when|please)\b))"
# Where an ask to the run stands: the start of the message, of a sentence, or of a clause (after a comma, a semicolon,
# a colon, a dash or a bracket, or "and", "then", "but"), one class of boundaries rather than one spelling at a time
# (the code review of item 691 took four rounds over spellings).
_CLAUSE_STARTS: str = r"(?:(?:^|(?<=[.!?\n,;:(—–]))\s*|\b(?:and|then|but)\s+)"
# The verbs whose subject is the run there ("Stay read-only.", "investigate the crash; work read-only", "and keep things
# read-only"); a thing's state is put otherwise ("the DB must stay read-only").
_ASKED_TO_STAY: re.Pattern[str] = re.compile(
    _CLAUSE_STARTS
    + r"(?P<words>(?:please\s+)?(?:stay|remain|work|operate|run|go|keep things)(?:\s+in)?\s+read[- ]only(?:\s+mode)?)"
    + _ASK_ENDS,
    re.IGNORECASE,
)
# "Keep it" read-only is the run's only as the message opens with it: after a task names a thing ("Copy prod.db. Keep it
# read-only.") "it" is the thing.
_OPENS_READ_ONLY: re.Pattern[str] = re.compile(
    r"^\s*(?P<words>(?:please\s+)?keep it\s+read[- ]only(?:\s+mode)?)" + _ASK_ENDS,
    re.IGNORECASE,
)


def only_looks(goal: str) -> str | None:
    """The words of ``goal`` that ask its run only to look, as written, or ``None`` when it asks nothing of the kind."""
    # Python folds the Turkish capital İ to no single lower-case letter, so it is read as i; the length stays, and the
    # words are cut from the goal as written.
    text: str = goal.replace("İ", "i")
    patterns: tuple[re.Pattern[str], ...] = (_ONLY_LOOKS, _LABEL, _ASKED_TO_STAY, _OPENS_READ_ONLY)
    found: list[re.Match[str]] = [match for pattern in patterns if (match := pattern.search(text))]
    if not found:
        return None
    first: re.Match[str] = min(found, key=lambda match: match.start("words"))
    return goal[first.start("words") : first.end("words")]


def resolved_mode(asked: ChatMode, goal: str) -> RunMode:
    """The mode a message runs in: the one asked for, or for ``auto`` research when ``goal`` asks only to look and a
    task otherwise."""
    if asked != "auto":
        return asked
    return "research" if only_looks(goal) is not None else "task"
