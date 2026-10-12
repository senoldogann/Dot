"""The words of a goal that ask its run to keep going until it is done (item 132), in English or in Turkish, written
with the Turkish letters or without them. A person's task or research run whose goal holds them runs in goal mode, and
the goal checker is told so: the run goes on as long as its owner asked, on every model, not as long as its model sees
fit."""

import re

# Whole words only: "durmadan" (without stopping) asks, "durmak istiyorum" (I want to stop) and "durması" (its
# stopping) do not. "durma" (don't stop) asks after "hiç" or "sakın" or at the end of a clause, since it is also the
# noun of "durma noktası" (the stopping point). "devam et" (go on) is left out: it is how a follow-up says "continue",
# not "until it is done".
_KEEP_GOING: re.Pattern[str] = re.compile(
    r"\b(?:"
    r"don'?t stop|do not stop|never stop|keep going|without stopping|non-?stop"
    r"|until (?:it is |it's |everything is |all is |they are )?(?:done|finished|complete|completed)"
    r"|(?:h[iı][çc]|sak[ıi]n) durma|durma(?=\s*(?:[.!,;:)\"'”’]|$))|durma(?:dan|y[ıi]n)|durmak yok"
    r"|bit(?:ene|irene|inceye) kadar|tamamlan(?:ana|[ıi]ncaya) kadar"
    r")\b",
    re.IGNORECASE,
)


def keep_going_ask(goal: str) -> str | None:
    """The words of ``goal`` that ask its run to keep going until it is done, as written, or ``None`` when it asks
    nothing of the kind."""
    # Python folds the Turkish capital İ to no single lower-case letter, so it is read as i; the length stays, and the
    # words are cut from the goal as written.
    found: re.Match[str] | None = _KEEP_GOING.search(goal.replace("İ", "i"))
    return None if found is None else goal[found.start() : found.end()]
