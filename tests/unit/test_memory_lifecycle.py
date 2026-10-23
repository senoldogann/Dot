"""The life of a note (item 584): what a store from before it still holds, the owner's Forget that moves a note whole to
a trash and brings it back, the date a note began to rest and the proposals to forget the ones that rested long
enough, the pin that keeps a note awake, and the merge that keeps its sources and can be taken back. Real SQLite; the
notes carry vectors the test places, no embedding service is used."""

import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.memory.lifecycle import (
    Gone,
    delete_for_good,
    forget_proposals,
    forget_proposed,
    keep_proposed,
    merge_notes,
    notes_gone,
    pin_memory,
    proposable,
    purge_trash,
    rested_since,
    restore_note,
    trash_of,
    undo_merge,
    unpin_memory,
)
from dotagent.memory.service import (
    CORE_TAG,
    JOURNAL_TAG,
    REFLECTION_SOURCE,
    REST_TAG,
    SEPARATE_TAG,
    MemoryIndex,
    forget_memory,
    recall,
    resting,
    save_memory,
)
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord, TrashedMemory
from dotagent.tools.memory import build_memory_tools

DAY_S: float = 86400.0
TWIN: str = "pdftotext keeps the columns of a table only with the -layout flag."
OTHER: str = "Use pdftotext with -layout when a table must keep its columns."
_COLUMNS: tuple[str, ...] = ("merged_into", "merged_from", "pinned_at", "rested_at")


@asynccontextmanager
async def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryIndex]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield MemoryIndex(store=store, embedder=None, config=config.memory)
    finally:
        await store.close()


def _age(path: Path, memory_id: str, *, created: float, used: float | None = None, rested: float | None = None) -> None:
    """Make a note look as if it were written at ``created``, last used at ``used`` and put to rest at ``rested``."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE memories SET created_at = ?, updated_at = ?, last_used_at = ?, rested_at = ? WHERE id = ?",
            (created, created, used, rested, memory_id),
        )
        connection.commit()
    finally:
        connection.close()


async def _note(
    store: Store,
    content: str,
    tags: tuple[str, ...] = (),
    run: str | None = "run_1",
    *,
    agent_id: str | None = None,
    vector: tuple[float, ...] | None = None,
    tainted: bool = False,
) -> MemoryRecord:
    """A note a run saved, or the owner's own with ``run`` of ``None``."""
    return await store.add_memory(content, tags, run, agent_id, vector, tainted)


async def test_a_store_from_before_the_item_opens_with_its_notes_whole(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.db"
    store: Store = await Store.open(path, tmp_path / "images")
    try:
        kept: MemoryRecord = await _note(store, TWIN, ("lesson", "pdf"))
    finally:
        await store.close()
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        for column in _COLUMNS:
            connection.execute(f"ALTER TABLE memories DROP COLUMN {column}")
        connection.execute("DROP TABLE memory_trash")
        connection.commit()
    finally:
        connection.close()
    store = await Store.open(path, tmp_path / "images")
    try:
        found: MemoryRecord = await store.get_memory(kept.id)
        assert (found.content, found.tags, found.created_at) == (TWIN, ("lesson", "pdf"), kept.created_at)
        assert (found.merged_into, found.merged_from, found.pinned_at, found.rested_at) == (None, (), None, None)
        assert await store.count_trashed() == 0
    finally:
        await store.close()
    connection = sqlite3.connect(path)
    try:
        assert {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)")} >= set(_COLUMNS)
    finally:
        connection.close()


async def test_forgetting_a_note_keeps_it_whole_in_the_trash_and_restoring_brings_it_back_awake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        note: MemoryRecord = await _note(store, TWIN, ("lesson", REST_TAG), vector=(1.0, 0.0), tainted=True)
        await store.retire_memory(note.id, ("lesson", REST_TAG), "mem_newer")
        before: MemoryRecord = await store.get_memory(note.id)
        assert before.rested_at is not None and before.replaced_by == "mem_newer"
        forgotten: MemoryRecord = await forget_memory(index, note.id)
        assert forgotten == before
        # It is in none of what a list, a count, a search or a similar-note look reads.
        assert await store.count_memories() == 0 and await store.list_memories(10, 0) == []
        assert await store.all_memory_vectors() == [] and await recall(index, "pdftotext columns", 5) == []
        with pytest.raises(NotFoundError):
            await store.get_memory(note.id)
        in_trash: list[TrashedMemory] = await trash_of(index)
        assert [found.record for found in in_trash] == [before] and in_trash[0].trashed_at <= time.time()
        now: float = time.time()
        back: MemoryRecord = await restore_note(index, note.id, now)
        # Awake and written anew; what put it aside is cleared; the rest is as it was.
        assert back.tags == ("lesson",) and (back.replaced_by, back.merged_into, back.rested_at) == (None, None, None)
        assert (back.updated_at, back.created_at, back.tainted) == (now, before.created_at, True)
        assert await store.all_memory_vectors() == [(note.id, (1.0, 0.0))]
        assert [hit.record.id for hit in await recall(index, "pdftotext columns", 5)] == [note.id]
        assert await store.count_trashed() == 0
        with pytest.raises(NotFoundError, match="not in the trash"):
            await restore_note(index, note.id, now)


async def test_the_trash_is_emptied_by_age_and_a_note_is_deleted_for_good_only_at_the_owners_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        old, recent, by_hand = [await _note(store, f"Note {name} about the garden.") for name in ("a", "b", "c")]
        now: float = time.time()
        for note, when in ((old, now - 31 * DAY_S), (recent, now - 29 * DAY_S), (by_hand, now - DAY_S)):
            await store.trash_memory(note.id, when)
        assert await purge_trash(index, now) == 1
        assert {found.record.id for found in await trash_of(index)} == {recent.id, by_hand.id}
        # The owner's own press deletes one at once, and only a note that is in the trash.
        assert await delete_for_good(index, by_hand.id) is True and await delete_for_good(index, old.id) is False
        assert [found.record.id for found in await trash_of(index)] == [recent.id]


async def test_the_date_a_note_began_to_rest_is_kept_and_for_an_older_one_the_latest_it_could_have_been(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        rest_days: int = index.config.rest_days
        dated: MemoryRecord = await _note(store, "A dated rest.", ("lesson",))
        await store.rest_memory(dated.id, ("lesson", REST_TAG), now - 40 * DAY_S)
        undated: MemoryRecord = await _note(store, "A rest from before the date was kept.", ("lesson", REST_TAG))
        _age(tmp_path / "dot.db", undated.id, created=now - 300 * DAY_S)
        by_age: MemoryRecord = await _note(store, "Unused for a long time.")
        _age(tmp_path / "dot.db", by_age.id, created=now - 200 * DAY_S, used=now - 150 * DAY_S)
        fresh: MemoryRecord = await _note(store, "Written yesterday.")
        owner: MemoryRecord = await _note(store, "The owner's own note.", run=None)
        _age(tmp_path / "dot.db", owner.id, created=now - 500 * DAY_S)
        standing: MemoryRecord = await _note(store, "A standing note.", (CORE_TAG, REST_TAG))
        expired: MemoryRecord = (
            await store.add_memory_once("Ran out.", (), "run_1", None, None, now - 10 * DAY_S, False)
        )[0]
        notes: dict[str, MemoryRecord] = {note.id: note for note in await store.list_memories(50, 0)}

        def since(note: MemoryRecord) -> float | None:
            return rested_since(notes[note.id], now, rest_days)

        assert since(dated) == pytest.approx(now - 40 * DAY_S)
        assert since(undated) == pytest.approx(now - 300 * DAY_S + rest_days * DAY_S)
        assert since(by_age) == pytest.approx(now - 150 * DAY_S + rest_days * DAY_S)
        assert since(expired) == pytest.approx(now - 10 * DAY_S)
        assert since(fresh) is None and since(owner) is None and since(standing) is None


async def test_a_note_that_rested_for_ninety_days_is_proposed_and_only_the_owners_press_forgets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        path: Path = tmp_path / "dot.db"
        now: float = time.time()
        long_ago, rested, young = [await _note(store, f"Lesson {name} of a run.", ("lesson",)) for name in "abc"]
        _age(path, long_ago.id, created=now - 400 * DAY_S, used=now - 300 * DAY_S)
        await store.rest_memory(rested.id, ("lesson", REST_TAG), now - 100 * DAY_S)
        await store.rest_memory(young.id, ("lesson", REST_TAG), now - 20 * DAY_S)
        # Never proposed: the owner's note, a standing one, a pinned one, a lesson the owner saved, and the reflection's
        # own journal of a day (tagged so, and any note it wrote), which the nightly merge leaves alone too.
        owner = await _note(store, "The owner's note.", run=None)
        standing = await _note(store, "A standing rule.", (CORE_TAG, REST_TAG))
        pinned = await _note(store, "A pinned lesson.", ("lesson", REST_TAG))
        saved = await _note(store, "A lesson the owner saved.", ("lesson", REST_TAG))
        journal = await _note(store, "On 2026-06-01 I worked on 3 runs.", (JOURNAL_TAG,), REFLECTION_SOURCE)
        resting_journal = await _note(
            store, "On 2026-06-02 I worked on 1 run.", (JOURNAL_TAG, REST_TAG), REFLECTION_SOURCE
        )
        by_reflection = await _note(store, "A note the reflection wrote without a tag.", (), REFLECTION_SOURCE)
        for note in (owner, standing, pinned, saved, journal, resting_journal, by_reflection):
            _age(path, note.id, created=now - 500 * DAY_S, rested=now - 400 * DAY_S)
        await store.set_memory_pin(pinned.id, now - 300 * DAY_S)
        await store.add_event(
            "run_saved", "lesson_saved", {"index": 0, "memory_id": saved.id, "by": "owner", "created": True}
        )
        proposals = await forget_proposals(index, now)
        assert [proposal.record.id for proposal in proposals] == [long_ago.id, rested.id]
        assert proposals[1].rested_since == pytest.approx(now - 100 * DAY_S)
        assert await store.count_memories() == 10
        # The press names the notes the page showed; one that is not proposed any more is left alone.
        outcome = await forget_proposed(index, [rested.id, young.id, "mem_unknown"], now)
        assert [note.id for note in outcome.done] == [rested.id]
        assert outcome.skipped == (young.id, "mem_unknown")
        assert await store.count_memories() == 9 and await store.count_trashed() == 1
        # Keep wakes a note, written anew: it is proposed again only after it rests for as long.
        kept = await keep_proposed(index, [long_ago.id, "mem_unknown"], now)
        assert [note.id for note in kept.done] == [long_ago.id] and kept.skipped == ("mem_unknown",)
        assert [proposal.record.id for proposal in await forget_proposals(index, now)] == []
        woken: MemoryRecord = await store.get_memory(long_ago.id)
        assert woken.updated_at == pytest.approx(now) and REST_TAG not in woken.tags
        assert not resting(woken, now, index.config.rest_days, None, index.config.score_min_uses)


async def test_the_reflections_own_notes_are_no_candidate_in_the_store_and_not_proposable_by_the_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each of the two layers refuses the reflection's journal alone (the review of item 584): the store does not
    list it among the notes that may have rested long enough, and the rule that decides what is proposed refuses it
    whatever the store gives it."""
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        journal = await _note(store, "On 2026-06-01 I worked on 3 runs.", (JOURNAL_TAG,), REFLECTION_SOURCE)
        lesson = await _note(store, "A lesson of a run.", ("lesson",))
        for note in (journal, lesson):
            _age(tmp_path / "dot.db", note.id, created=now - 500 * DAY_S)
        everything = await store.resting_candidates(REST_TAG, now - 180 * DAY_S, now)
        assert {note.id for note in everything} == {journal.id, lesson.id}
        apart = await store.resting_candidates(REST_TAG, now - 180 * DAY_S, now, not_from=(REFLECTION_SOURCE,))
        assert [note.id for note in apart] == [lesson.id]
        owned: MemoryRecord = await store.get_memory(journal.id)
        assert proposable(await store.get_memory(lesson.id), set()) and not proposable(owned, set())
        assert not proposable(owned.model_copy(update={"tags": ()}), set())
        assert not proposable(owned.model_copy(update={"source_run": "run_1"}), set())


async def test_the_agents_forget_tool_says_the_note_goes_to_the_trash_and_it_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of item 584 (P3): the tool still told the model that it deletes a note, which since this item moves it
    to the owner's trash, whence only the owner brings it back."""
    forget = next(tool for tool in build_memory_tools() if tool.spec.name == "memory_forget")
    assert "trash" in forget.spec.description and "restore" in forget.spec.description
    assert "Delete" not in forget.spec.description
    async with _index(tmp_path, monkeypatch) as index:
        note: MemoryRecord = await _note(index.store, "A lesson of a run.", ("lesson",))
        context = SimpleNamespace(runtime=SimpleNamespace(memory=index), run_id="run_1", agent_id=None)
        prepared = await forget.prepare({"memory_id": note.id}, context)  # type: ignore[arg-type]
        result = await prepared.run(context)  # type: ignore[arg-type]
        assert result.text.startswith("Forgot: ")
        assert await index.store.count_memories() == 0
        assert [found.record.id for found in await trash_of(index)] == [note.id]


async def test_a_pinned_note_never_rests_and_pinning_wakes_a_resting_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        note: MemoryRecord = await _note(store, "A lesson that went unused.", ("lesson", REST_TAG))
        await store.retire_memory(note.id, ("lesson", REST_TAG), "mem_newer")
        _age(tmp_path / "dot.db", note.id, created=now - 400 * DAY_S)
        stale: MemoryRecord = await store.get_memory(note.id)
        assert resting(stale, now, index.config.rest_days, None, 3)
        pinned: MemoryRecord = await pin_memory(index, note.id, now)
        assert pinned.pinned_at == pytest.approx(now) and REST_TAG not in pinned.tags
        assert (pinned.replaced_by, pinned.rested_at) == (None, None)
        # A year later, with no use, it still does not rest, is not proposed, and a search finds it.
        later: float = now + 365 * DAY_S
        assert not resting(pinned, later, index.config.rest_days, None, 3)
        assert await forget_proposals(index, later) == []
        assert [hit.record.id for hit in await recall(index, "lesson went unused", 3)] == [note.id]
        unpinned: MemoryRecord = await unpin_memory(index, note.id)
        assert unpinned.pinned_at is None and resting(unpinned, later, index.config.rest_days, None, 3)
        with pytest.raises(NotFoundError):
            await pin_memory(index, "mem_unknown", now)


async def test_a_merge_makes_one_note_of_its_sources_and_keeps_them_whole_and_marked_from_outside_if_any_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        path: Path = tmp_path / "dot.db"
        now: float = time.time()
        first = await _note(store, TWIN, ("lesson", "pdf", "a", "b"), "run_1", vector=(1.0, 0.0))
        second = await _note(
            store, OTHER, ("lesson", "tables", "c", "d", "e"), "run_2", vector=(0.9, 0.1), tainted=True
        )
        _age(path, first.id, created=now - 30 * DAY_S, used=now - 3 * DAY_S)
        _age(path, second.id, created=now - 10 * DAY_S, used=now - 2 * DAY_S)
        await store.mark_memories_used("run_9", [first.id, second.id], now - DAY_S, "start")
        merged: MemoryRecord = await merge_notes(index, [first.id, second.id], second.id, now)
        # Its words are the kept source's, its tags all the sources' (past the six a save keeps), its dates the oldest
        # written and the newest changed, its vector the kept one's, and it is from outside content: a source was.
        assert (merged.content, merged.source_run, merged.agent_id) == (OTHER, "run_2", None)
        assert merged.tags == ("lesson", "tables", "c", "d", "e", "pdf", "a", "b") and merged.tainted is True
        assert merged.created_at == pytest.approx(now - 30 * DAY_S)
        assert merged.updated_at == pytest.approx(now - 10 * DAY_S)
        assert (merged.used_count, merged.last_used_at) == (2, pytest.approx(now - DAY_S))
        assert [(found.id, found.source_run) for found in merged.merged_from] == [
            (second.id, "run_2"),
            (first.id, "run_1"),
        ]
        assert merged.merged_from[1].created_at == pytest.approx(now - 30 * DAY_S) and merged.merged_from[0].tainted
        assert dict(await store.all_memory_vectors())[merged.id] == pytest.approx((0.9, 0.1))
        # The sources rest whole, naming the merged note, and a search finds the merged note alone.
        for source in (first, second):
            held: MemoryRecord = await store.get_memory(source.id)
            assert (held.content, held.merged_into, REST_TAG in held.tags) == (source.content, merged.id, True)
            assert held.rested_at == pytest.approx(now) and held.tags[:-1] == source.tags
        assert [hit.record.id for hit in await recall(index, "pdftotext layout table columns", 5)] == [merged.id]
        # Two clean notes make a clean one.
        a = await _note(store, "One lesson about the garden shed.", ("lesson",), vector=(0.0, 1.0))
        b = await _note(store, "A lesson about the shed in the garden.", ("lesson",), vector=(0.0, 1.0))
        assert (await merge_notes(index, [a.id, b.id], a.id, now)).tainted is False


async def test_a_merge_refuses_what_is_the_owners_and_what_it_cannot_merge_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        good = await _note(store, "A lesson about the shed.", ("lesson",))
        twin = await _note(store, "A lesson about the shed again.", ("lesson",))
        owner = await _note(store, "The owner's note about the shed.", run=None)
        saved = await _note(store, "A lesson the owner saved about the shed.", ("lesson",))
        await store.add_event("run_saved", "lesson_saved", {"index": 0, "memory_id": saved.id, "by": "owner"})
        pinned = await _note(store, "A pinned note about the shed.")
        await store.set_memory_pin(pinned.id, now)
        standing = await _note(store, "A standing note about the shed.", (CORE_TAG,))
        journal = await _note(store, "On 2026-10-11 I worked on 2 runs.", (JOURNAL_TAG,))
        agents = await _note(store, "An agent's lesson about the shed.", ("lesson",), agent_id="agent_1")
        resting_note = await _note(store, "A resting note about the shed.", ("lesson", REST_TAG))
        for other in (owner, saved, pinned, standing, journal, agents, resting_note):
            with pytest.raises(InvalidValueError):
                await merge_notes(index, [good.id, other.id], good.id, now)
        with pytest.raises(InvalidValueError, match="two notes at least"):
            await merge_notes(index, [good.id], good.id, now)
        with pytest.raises(InvalidValueError, match="two notes at least"):
            await merge_notes(index, [good.id, good.id], good.id, now)
        with pytest.raises(InvalidValueError, match="keeps the words of one of them"):
            await merge_notes(index, [good.id, twin.id], owner.id, now)
        with pytest.raises(NotFoundError):
            await merge_notes(index, [good.id, "mem_unknown"], good.id, now)
        assert await store.count_memories() == 9
        assert {note.id for note in await store.list_memories(50, 0) if note.merged_into is not None} == set()


async def test_a_merge_taken_back_wakes_its_sources_apart_and_trashes_the_merged_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        first = await _note(store, TWIN, ("lesson", "pdf"), vector=(1.0, 0.0))
        second = await _note(store, OTHER, ("lesson",), vector=(0.9, 0.1))
        merged: MemoryRecord = await merge_notes(index, [first.id, second.id], first.id, now)
        # The owner forgot one source after the merge: taking the merge back leaves it where it is.
        await forget_memory(index, second.id)
        undone = await undo_merge(index, merged.id, now + 60)
        assert [note.id for note in undone.woken] == [first.id] and undone.merged.id == merged.id
        woken: MemoryRecord = await store.get_memory(first.id)
        assert woken.tags == ("lesson", "pdf", SEPARATE_TAG) and (woken.merged_into, woken.rested_at) == (None, None)
        assert {found.record.id for found in await trash_of(index)} == {merged.id, second.id}
        with pytest.raises(NotFoundError):
            await store.get_memory(merged.id)
        # The merged note comes back from the trash like any note, and a note no merge made cannot be taken back.
        assert (await restore_note(index, merged.id, now + 120)).merged_from[0].id == first.id
        with pytest.raises(InvalidValueError, match="not made by merging"):
            await undo_merge(index, first.id, now)


async def test_a_note_saved_again_that_a_merge_put_aside_is_the_note_it_was_merged_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        now: float = time.time()
        first = await _note(index.store, TWIN, ("lesson",))
        second = await _note(index.store, OTHER, ("lesson",))
        merged: MemoryRecord = await merge_notes(index, [first.id, second.id], second.id, now)
        again = await save_memory(index, TWIN, ["pdf"], "run_3", None, None, False)
        # Not a new note, and the note the merge put aside stays aside.
        assert again.created is False and again.record.id == merged.id and "pdf" in again.record.tags
        assert REST_TAG in (await index.store.get_memory(first.id)).tags
        # The merged note itself, saved again by its words.
        same = await save_memory(index, OTHER, [], "run_3", None, None, False)
        assert same.created is False and same.record.id == merged.id
        assert await index.store.count_memories() == 3
        # A merged note that holds more than six tags keeps them when its words are saved again.
        crowded = await _note(index.store, "Eight tags on this one.", tuple(f"t{n}" for n in range(8)))
        assert (await save_memory(index, crowded.content, ["t0", "new"], "run_3", None, None, False)).record.tags == (
            *crowded.tags,
            "new",
        )[:8]


async def test_the_notes_are_listed_by_what_changed_last_by_the_last_use_or_by_how_often_they_were_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        quiet, busy, lately, never = [await _note(store, f"A note about {name}.") for name in ("a", "b", "c", "d")]
        _age(tmp_path / "dot.db", quiet.id, created=now - 40 * DAY_S, used=now - 30 * DAY_S)
        _age(tmp_path / "dot.db", busy.id, created=now - 30 * DAY_S, used=now - 20 * DAY_S)
        _age(tmp_path / "dot.db", lately.id, created=now - 20 * DAY_S, used=now - DAY_S)
        _age(tmp_path / "dot.db", never.id, created=now - 10 * DAY_S)
        connection: sqlite3.Connection = sqlite3.connect(tmp_path / "dot.db")
        try:
            connection.executemany(
                "UPDATE memories SET used_count = ? WHERE id = ?",
                [(2, quiet.id), (9, busy.id), (1, lately.id), (0, never.id)],
            )
            connection.commit()
        finally:
            connection.close()
        assert [note.id for note in await store.list_memories(10, 0)] == [never.id, lately.id, busy.id, quiet.id]
        assert [note.id for note in await store.list_memories(10, 0, "used")] == [
            lately.id,
            busy.id,
            quiet.id,
            never.id,
        ]
        assert [note.id for note in await store.list_memories(10, 0, "most_used")] == [
            busy.id,
            quiet.id,
            lately.id,
            never.id,
        ]
        assert [note.id for note in await store.list_memories(10, 0, "least_used")] == [
            never.id,
            lately.id,
            quiet.id,
            busy.id,
        ]


async def test_a_chat_that_quoted_a_note_a_merge_put_aside_is_told_it_is_gone_and_handed_the_merged_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit of the harness: the chat keeps the old text of a note, and only the ids tell the model that it was
    forgotten; a merge puts notes aside and makes a new one, so the sources are named as forgotten and the merged note
    is handed in their place, however many merges later."""
    async with _index(tmp_path, monkeypatch) as index:
        store: Store = index.store
        now: float = time.time()
        first = await _note(store, TWIN, ("lesson",))
        second = await _note(store, OTHER, ("lesson",))
        third = await _note(store, "Unrelated: the shed is blue.", ("lesson",))
        gone_for_good = await _note(store, "Soon forgotten.", ("lesson",))
        await forget_memory(index, gone_for_good.id)
        merged: MemoryRecord = await merge_notes(index, [first.id, second.id], first.id, now)
        quoted: set[str] = {first.id, second.id, third.id, gone_for_good.id, "mem_never_existed"}
        away: Gone = await notes_gone(index, quoted, None, now)
        assert away.gone == tuple(sorted([first.id, second.id, gone_for_good.id, "mem_never_existed"]))
        assert [note.id for note in away.stand_in] == [merged.id]
        # Merged again: the chat's note stands for the latest merged note only.
        other = await _note(store, "pdftotext -layout keeps a table's columns, which plain text drops.", ("lesson",))
        latest: MemoryRecord = await merge_notes(index, [merged.id, other.id], other.id, now)
        assert [note.id for note in (await notes_gone(index, {first.id}, None, now)).stand_in] == [latest.id]
        # What a run may not be handed stands in for nothing: a note saved while reading outside content, another
        # agent's lesson, and a merged note that rests.
        tainted_a = await _note(store, "Tainted twin one about the barn.", ("lesson",), tainted=True)
        tainted_b = await _note(store, "Tainted twin two about the barn.", ("lesson",), tainted=True)
        outside: MemoryRecord = await merge_notes(index, [tainted_a.id, tainted_b.id], tainted_a.id, now)
        assert outside.tainted and (await notes_gone(index, {tainted_a.id}, None, now)).stand_in == ()
        assert (await notes_gone(index, {tainted_a.id}, None, now)).gone == (tainted_a.id,)
        mine_a = await _note(store, "An agent lesson about the barn door.", ("lesson",), agent_id="agent_1")
        mine_b = await _note(store, "A lesson of the agent about the barn door.", ("lesson",), agent_id="agent_1")
        await merge_notes(index, [mine_a.id, mine_b.id], mine_a.id, now)
        assert (await notes_gone(index, {mine_a.id}, None, now)).stand_in == ()
        assert len((await notes_gone(index, {mine_a.id}, "agent_1", now)).stand_in) == 1
        await store.rest_memory(latest.id, (*latest.tags, REST_TAG), now)
        assert (await notes_gone(index, {first.id}, None, now)).stand_in == ()
        # A note that is awake is never named.
        assert (await notes_gone(index, {third.id}, None, now)) == Gone(gone=(), stand_in=())


async def test_a_note_in_the_trash_is_in_no_table_a_search_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trashed note is in the trash table alone: not the notes, not their index, not their vectors."""
    async with _index(tmp_path, monkeypatch) as index:
        note: MemoryRecord = await _note(index.store, TWIN, ("lesson",), vector=(1.0, 0.0))
        await forget_memory(index, note.id)
    connection: sqlite3.Connection = sqlite3.connect(tmp_path / "dot.db")
    try:
        counts: dict[str, int] = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("memories", "memories_fts", "memory_vectors", "memory_trash")
        }
    finally:
        connection.close()
    assert counts == {"memories": 0, "memories_fts": 0, "memory_vectors": 0, "memory_trash": 1}
