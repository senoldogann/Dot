"""What a nightly reflection did, and the last one that failed, as the store keeps them (item 70): a module of its
own, light to import, so that `dotx stats` reads them without the reflection's work (:mod:`dotagent.agent.reflection`).
"""

from dotagent.core.types import FrozenModel

REFLECTION_KEY: str = "reflection.last"
# ``[agent] reflection_time`` that turns the reflection off.
REFLECTION_OFF: str = "off"
# The last reflection that failed, kept until one after it is done: the Activity list and dotx stats show it.
REFLECTION_FAILED_KEY: str = "reflection.failed"


class Reflection(FrozenModel):
    """What a reflection did: it read the runs that ended between ``since`` and ``at``, ``lessons_written`` lessons
    were proposed or saved from them, ``merged`` lesson notes were folded into another, ``retired`` were rested,
    ``outdated`` notes were put aside for newer ones that made them outdated (item 306), and ``journal`` is the id of
    the day's journal note (``None`` when nothing happened). ``merged`` counts the notes the review model had merged too
    (item 584); ``to_forget`` is how many notes wait for the owner to forget or keep them, and ``purged`` how many notes
    of the trash were deleted for good."""

    at: float
    since: float
    runs_read: int
    lessons_written: int
    merged: int
    retired: int
    journal: str | None
    # Whether the owner section (what Dot knows about the owner, item 93) was rewritten.
    owner_updated: bool
    # A reflection recorded before item 306 put no note aside for a newer one.
    outdated: int = 0
    # A reflection recorded before item 584 proposed no note to be forgotten and emptied no trash.
    to_forget: int = 0
    purged: int = 0


class ReflectionFailure(FrozenModel):
    """A reflection that failed at ``at``, with what failed."""

    at: float
    error: str


def summary_line(reflection: Reflection) -> str:
    """``Reflected on 7 runs: 2 lessons, 1 merged, 1 retired.``, and ``, 2 outdated`` when notes were put aside for
    newer ones, ``, 3 to forget`` when notes wait for the owner to forget or keep them."""
    runs: str = "1 run" if reflection.runs_read == 1 else f"{reflection.runs_read} runs"
    parts: list[str] = [
        f"{reflection.lessons_written} {'lesson' if reflection.lessons_written == 1 else 'lessons'}",
        f"{reflection.merged} merged",
        f"{reflection.retired} retired",
        *([f"{reflection.outdated} outdated"] if reflection.outdated else []),
        *([f"{reflection.to_forget} to forget"] if reflection.to_forget else []),
    ]
    return f"Reflected on {runs}: {', '.join(parts)}."
