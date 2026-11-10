"""Lessons handed at the moment a tool fails (item 1111): what earlier runs learned about that tool, beside its error.

A lesson is a note tagged ``lesson`` (:mod:`dotagent.agent.lessons`). At a run's start the notes are chosen by the
words of its goal, which a lesson about a tool rarely shares: on the owner's Dot, 2026-11-10, 448 lessons were kept and
3 of 60 runs were handed one. Most of them name the tool they are about (`browser_act`, `read_file`), and the moment a
run needs one is when that tool has just failed. So a call that fails is answered with its error and, after it, the
lessons that name its tool, which the run was not handed yet: at most :data:`LESSONS_AT_FAILURE`, the most used first.

Only notes every run of the agent may be handed are offered (Dot's, or the run's agent's own) and none that holds no
longer (replaced, merged, resting or expired). A note written while its run had read content third parties can author
taints the run that reads it, and 432 of the 448 were such notes on 2026-11-10, as most runs read pages: one is handed
only to a run that has read such content itself, so that no run is marked untrusted by an error. Everything here is
pure but :func:`hand_failure_lessons`, which reads and writes the store.
"""

import re
import time
from collections.abc import Sequence, Set

from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

# How many lessons one failed call is answered with.
LESSONS_AT_FAILURE: int = 2
# How many lessons are read to choose from: the newest; the owner's Dot kept 448 on 2026-11-10.
LESSONS_READ: int = 2000
LESSON_TAG: str = "lesson"


def names_tool(content: str, tool: str) -> bool:
    """Whether a note names ``tool`` as a word (``browser_act``, not ``browser_action``)."""
    return re.search(rf"(?<![\w]){re.escape(tool)}(?![\w])", content) is not None


def lessons_for_tool(
    tool: str, notes: Sequence[MemoryRecord], handed: Set[str], agent_id: str | None, tainted: bool, now: float
) -> list[MemoryRecord]:
    """The lessons of ``notes`` to hand a run of ``agent_id`` whose call of ``tool`` failed at ``now``: the ones that
    name the tool, hold, may be handed to the run and were not handed to it yet; one written after outside content only
    when the run is ``tainted`` already. The most used first, then the newest, :data:`LESSONS_AT_FAILURE` at most."""
    chosen: list[MemoryRecord] = [
        note
        for note in notes
        if LESSON_TAG in note.tags
        and note.id not in handed
        and (tainted or not note.tainted)
        and note.replaced_by is None
        and note.merged_into is None
        and note.rested_at is None
        and (note.expires_at is None or note.expires_at > now)
        and (note.agent_id is None or note.agent_id == agent_id)
        and names_tool(note.content, tool)
    ]
    chosen.sort(key=lambda note: (-note.used_count, -note.updated_at))
    return chosen[:LESSONS_AT_FAILURE]


def failure_note(tool: str, lessons: Sequence[MemoryRecord]) -> str:
    """The words added after a failed call's error: the lessons, one a line."""
    lines: str = "\n".join(f"- {lesson.content}" for lesson in lessons)
    return f"What earlier runs learned about {tool}, from Dot's memory:\n{lines}"


async def hand_failure_lessons(store: Store, run_id: str, agent_id: str | None, tainted: bool, tool: str) -> str | None:
    """The note of the lessons about ``tool`` for run ``run_id``, whose call of it just failed, counted as handed to
    the run (``failure``); ``None`` when there is none to hand. ``tainted`` says the run has read outside content."""
    now: float = time.time()
    notes: list[MemoryRecord] = await store.memories_with_tag(LESSON_TAG, LESSONS_READ)
    handed: set[str] = await store.handed_memory_ids(run_id)
    lessons: list[MemoryRecord] = lessons_for_tool(tool, notes, handed, agent_id, tainted, now)
    if not lessons:
        return None
    await store.mark_memories_used(run_id, [lesson.id for lesson in lessons], now, "failure")
    return failure_note(tool, lessons)
