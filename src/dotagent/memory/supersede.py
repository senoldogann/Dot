"""A note that says a fact changed replaces the older note of the fact (item 1729).

A run that saves a note gives, when the fact has one current value (a city, a rate, a client's state), the ``subject``
it is about. A newer note of the same subject puts the older notes of the subject aside: each rests, names the note
that replaced it (``replaced_by``) and keeps its text and the day it began to rest (``rested_at``: until then it was
true), so that nothing is deleted, the Memory page lists it and the owner can wake it. A search and the starting
notes pass over a resting note, so the run reads the fact as it stands. The nightly reflection asks a model about
the notes that remain close (:func:`dotagent.agent.reflection.outdated_pairs`); this is the part no model is needed
for.

With no subject, a note replaces a near one (``similar_threshold``) only when its words say the fact changed and
nothing else: the same sentence with a number, a time or a version swapped, or with a word of change added
(:func:`wording_replaces`). A cosine alone cannot tell (measured with ``bge-m3``: facts that changed lie at 0.68-0.97,
facts that are both true at 0.68-0.84, ``dotx eval memory``), and a model is not asked here: a note saved while the
embedding service is down, or reworded into another sentence, waits for a subject or for the night.

What a run changes is never what the owner wrote: a note the owner wrote, pinned or corrected, a lesson they saved, and
a standing note stay as they are (a run's change of one is a proposal, item 1711), and a note saved while its run read
outside content never replaces a note that was not (as in :func:`dotagent.agent.reflection.outdated_pairs`, item 557).
"""

import re
import time
from collections.abc import Set

from dotagent.core.errors import InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.memory.service import (
    JOURNAL_TAG,
    REST_TAG,
    MemoryIndex,
    SavedMemory,
    expired,
    normalize_tags,
    save_memory,
    stands,
)
from dotagent.memory.words import lowercase
from dotagent.secrets import mask_filled
from dotagent.store.models import OWNER_EDIT, MemoryRecord

log = get_logger("dotagent.memory")

_MOST_SUBJECT_CHARS: int = 48
_SUBJECT_NOISE: re.Pattern[str] = re.compile(r"[^\w]+|_")
_WORD: re.Pattern[str] = re.compile(r"\w+")
# A note shares at least this many words, at its start and its end, with the note it replaces.
_LEAST_SHARED_WORDS: int = 3
# What differs between the two sentences is at most this many words on each side.
_MOST_CHANGED_WORDS: int = 4
# With a word of change the sentences must share this part of the shorter one.
_LEAST_SHARED_PART: float = 0.6
# The words a value follows in a sentence that states one (``is 20 minutes``, ``at 09:00``, ``on port 8765``, ``has 4
# cores``; not ``for``, ``of``, ``in``, ``to`` or ``from``, which come as often before an issue or a file): a number
# straight after another word (``invoice 1042``, ``server 1``, ``PR 12``) names a thing, and two
# things of one kind are both true.
_BEFORE_VALUE: frozenset[str] = frozenset(
    {"is", "are", "was", "were", "at", "on", "by", "until", "than", "within", "has", "have", "had", "port", "costs"}
    | {"about", "around", "cost"}
)
# Words that say the fact changed: "the call is now on Wednesdays", "no longer drinks coffee".
_CHANGE_WORDS: frozenset[str] = frozenset({"now", "longer", "anymore", "instead", "moved", "switched", "changed"})


def normalize_subject(raw: str) -> str:
    """The subject as the notes keep it: lowercase words joined by hyphens, at most :data:`_MOST_SUBJECT_CHARS`
    characters (``User city`` is ``user-city``). One without a letter or a digit is an error. A value filled into a page
    is masked first, as in the note's text (item 621): the subject is stored and shown in every run's notes."""
    masked: str = mask_filled(raw)
    slug: str = _SUBJECT_NOISE.sub("-", lowercase(masked.strip())).strip("-")[:_MOST_SUBJECT_CHARS].strip("-")
    if not slug:
        raise InvalidValueError(
            "the subject holds no letter or digit; give a short name for what the note is about, as 'user-city', "
            "or none",
            {"subject": raw},
        )
    return slug


def _words(text: str) -> list[str]:
    return _WORD.findall(lowercase(text))


def _shared_start(first: list[str], second: list[str]) -> int:
    shared: int = 0
    for one, other in zip(first, second, strict=False):
        if one != other:
            break
        shared += 1
    return shared


def _numbers(words: list[str]) -> bool:
    """Whether ``words`` are there and each holds a digit (``10``, ``v2``, ``9120``)."""
    return bool(words) and all(any(char.isdigit() for char in word) for word in words)


def wording_replaces(older: str, newer: str) -> bool:
    """Whether ``newer`` says the fact of ``older`` changed and nothing else, by its words alone. Both are cut at the
    words they share at the start and at the end; what is left between is at most :data:`_MOST_CHANGED_WORDS` words on
    each side, and either all of it is numbers on both sides after a word a value follows (:data:`_BEFORE_VALUE`:
    ``at 09:00`` and ``at 10:30``, ``port 8765`` and ``port 9120``: a value swapped; not ``invoice 1042`` and ``invoice
    1043``, two things), or the new side holds a word of change (:data:`_CHANGE_WORDS`) the old note lacks and the
    two share :data:`_LEAST_SHARED_PART` of the shorter one. At least :data:`_LEAST_SHARED_WORDS` words are shared.
    Two facts about other things in one frame (``4 CPU cores`` and ``16 GB RAM``, ``Nordic Pines pays within 14 days``
    and ``Blue Harbor pays within 30 days``, ``Invoice 1042 is paid`` and ``Invoice 1043 is paid``) share too little,
    change a word that is no number, or swap a number that names a thing."""
    before: list[str] = _words(older)
    after: list[str] = _words(newer)
    start: int = _shared_start(before, after)
    end: int = _shared_start(before[start:][::-1], after[start:][::-1])
    shared: int = start + end
    old_part: list[str] = before[start : len(before) - end]
    new_part: list[str] = after[start : len(after) - end]
    if shared < _LEAST_SHARED_WORDS or not (old_part or new_part):
        return False
    if len(old_part) > _MOST_CHANGED_WORDS or len(new_part) > _MOST_CHANGED_WORDS:
        return False
    if _numbers(old_part) and _numbers(new_part):
        return start > 0 and before[start - 1] in _BEFORE_VALUE
    cue: bool = any(word in _CHANGE_WORDS and word not in before for word in new_part)
    return cue and shared >= _LEAST_SHARED_PART * min(len(before), len(after))


def holds(older: MemoryRecord, agent_id: str | None, said_at: float, now: float) -> bool:
    """Whether ``older`` is a note a note of ``agent_id`` said at ``said_at`` could replace: awake (not resting, merged
    away or run out), written before it, and for the same agent."""
    return (
        REST_TAG not in older.tags
        and older.merged_into is None
        and not expired(older, now)
        and older.created_at < said_at
        and older.agent_id == agent_id
        and JOURNAL_TAG not in older.tags
    )


def replaceable(older: MemoryRecord, newer: MemoryRecord, owner_saved: Set[str]) -> bool:
    """Whether a run's ``newer`` note may put ``older`` aside: ``older`` is a run's own, not the owner's (written,
    pinned, corrected, a lesson they saved, a standing note), and ``newer`` did not read outside content when ``older``
    did not."""
    return (
        older.source_run is not None
        and older.pinned_at is None
        and older.edited_by != OWNER_EDIT
        and older.id not in owner_saved
        and not stands(older)
        and not (newer.tainted and not older.tainted)
    )


class Replacing(FrozenModel):
    """A note saved, the notes it put aside (``replaced``, now resting) and the notes of the same fact it left as they
    are (``kept``: the owner's, or from before this run read outside content), with the near notes still awake in
    ``saved.similar``."""

    saved: SavedMemory
    replaced: tuple[MemoryRecord, ...]
    kept: tuple[MemoryRecord, ...]


async def save_replacing(
    index: MemoryIndex,
    content: str,
    tags: list[str],
    run_id: str,
    expires_at: float | None,
    tainted: bool,
    subject: str | None,
) -> Replacing:
    """Save a note as a run saves it (:func:`save_memory`, a note of Dot's) and put aside the notes it replaces: those
    of its ``subject`` when it has one, and the near notes whose words say the fact changed
    (:func:`wording_replaces`). A note saved again, with the text a note has already, is said now: it takes the subject
    and puts aside the other notes of it (the city that was Oulu, then Helsinki, saved as Oulu again is Oulu), and no
    near note by its wording."""
    # Before anything is written: a subject that holds no word is refused with no note saved.
    key: str | None = None if subject is None else normalize_subject(subject)
    saved: SavedMemory = await save_memory(index, content, tags, run_id, None, expires_at, tainted)
    newer: MemoryRecord = saved.record
    if key is not None and newer.subject != key:
        newer = await index.store.set_memory_subject(newer.id, key)
    now: float = time.time()
    said_at: float = newer.created_at if saved.created else now
    found: dict[str, tuple[MemoryRecord, str]] = {}
    if key is not None:
        for older in await index.store.memories_with_subject(key, None):
            found[older.id] = (older, "subject")
    for hit in saved.similar:
        other_slot: bool = hit.record.subject is not None and key is not None and hit.record.subject != key
        if not other_slot and wording_replaces(hit.record.content, newer.content):
            found.setdefault(hit.record.id, (hit.record, "wording"))
    found.pop(newer.id, None)
    matching: list[tuple[MemoryRecord, str]] = [
        (older, why) for older, why in found.values() if holds(older, newer.agent_id, said_at, now)
    ]
    owner_saved: set[str] = await index.store.owner_saved_memory_ids() if matching else set()
    replaced: list[MemoryRecord] = []
    kept: list[MemoryRecord] = []
    for older, why in matching:
        if not replaceable(older, newer, owner_saved):
            kept.append(older)
            continue
        resting: MemoryRecord = await index.store.retire_memory(
            older.id, tuple(dict.fromkeys((*normalize_tags(older.tags), REST_TAG))), newer.id
        )
        log.info("note_superseded", memory_id=older.id, replaced_by=newer.id, why=why, subject=key)
        replaced.append(resting)
    # A note named as replaced or kept is not offered again among the near notes to forget.
    gone: set[str] = {record.id for record in (*replaced, *kept)}
    shown: SavedMemory = saved.model_copy(
        update={"record": newer, "similar": tuple(hit for hit in saved.similar if hit.record.id not in gone)}
    )
    return Replacing(saved=shown, replaced=tuple(replaced), kept=tuple(kept))
