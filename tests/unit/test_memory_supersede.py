"""A note that says a fact changed replaces the older note of the fact (item 1729), on real SQLite: by the subject a run
gives, and with no subject only when the words say a value was swapped. The older note rests and is kept; the owner's
notes, pinned and corrected ones and a clean note a page-reading run would replace stay as they are."""

import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.core.errors import InvalidValueError
from dotagent.memory.ranking import Vector
from dotagent.memory.service import (
    REST_TAG,
    MemoryHit,
    MemoryIndex,
    forget_memory,
    recall_for,
    save_memory,
)
from dotagent.memory.supersede import Replacing, normalize_subject, save_replacing, wording_replaces
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

RUN: str = "run_supersede"
OULU: str = "The user lives in Oulu."
HELSINKI: str = "The user lives in Helsinki."
BERLIN: str = "The user lives in Berlin."
CITY: str = "user-city"


class _Embedder:
    """An embedding service that answers with the vector it was given for a text, and one apart for any other."""

    def __init__(self, vectors: dict[str, Vector]) -> None:
        self._vectors: dict[str, Vector] = vectors

    @property
    def model(self) -> str:
        return "supersede-test"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vectors.get(text, (0.0, 0.0, 0.0, 1.0)) for text in texts]

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def _index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: _Embedder | None
) -> AsyncIterator[MemoryIndex]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield MemoryIndex(store=store, embedder=embedder, config=config.memory)
    finally:
        await store.close()


async def _save(index: MemoryIndex, text: str, subject: str | None, tainted: bool = False) -> Replacing:
    return await save_replacing(index, text, [], RUN, None, tainted, subject)


async def _awake(index: MemoryIndex, query: str) -> list[str]:
    hits: list[MemoryHit] = await recall_for(index, query, 5, None)
    return [hit.record.content for hit in hits]


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("The daily standup is at 09:00.", "The daily standup is at 10:30."),
        ("Dot's daemon listens on port 8765.", "Dot's daemon listens on port 9120."),
        ("The staging server has 4 CPU cores.", "The staging server has 8 CPU cores."),
        ("The invoice template is invoice-v1.docx.", "The invoice template is invoice-v2.docx."),
        ("Nordic Pines pays within 14 days.", "Nordic Pines pays within 30 days."),
        ("The team call is on Mondays.", "The team call is now on Wednesdays."),
        ("The user drinks coffee in the morning.", "The user no longer drinks coffee in the morning."),
    ],
)
def test_the_same_sentence_with_a_value_swapped_or_a_word_of_change_says_the_fact_changed(
    older: str, newer: str
) -> None:
    assert wording_replaces(older, newer)


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        # Two facts in one frame, about other things.
        ("Nordic Pines pays within 14 days.", "Blue Harbor pays within 30 days."),
        ("The staging server has 4 CPU cores.", "The production server has 16 CPU cores."),
        ("The staging server has 4 CPU cores.", "The staging server has 16 GB RAM."),
        ("The user has 2 monitors.", "The user has 3 bikes."),
        ("Meeting 1 is at 9.", "Meeting 2 is at 10."),
        # Both can be true: a name is no number, and no word says it changed.
        ("The user likes pizza.", "The user likes sushi."),
        ("The user speaks Turkish.", "The user speaks Finnish."),
        ("The user's office is in Tampere.", "The user's office is in Turku."),
        # Reworded into another sentence, or the same one.
        ("The user lives in Espoo.", "The user moved to Vantaa in November, so Vantaa is their city now."),
        ("The user uses Python for scripts.", "The user now also uses Rust for the desktop shell."),
        ("The user lives in Oulu.", "The user lives in Oulu."),
    ],
)
def test_two_facts_that_may_both_be_true_or_are_worded_apart_are_not_taken_for_a_change(older: str, newer: str) -> None:
    assert not wording_replaces(older, newer)


async def test_a_newer_note_of_the_same_subject_puts_the_older_one_aside_kept_and_resting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        first: Replacing = await _save(index, OULU, CITY)
        assert (first.replaced, first.kept) == ((), ())
        second: Replacing = await _save(index, HELSINKI, CITY)
        assert [note.id for note in second.replaced] == [first.saved.record.id]
        old: MemoryRecord = await index.store.get_memory(first.saved.record.id)
        assert (old.content, old.replaced_by, REST_TAG in old.tags, old.subject) == (
            OULU,
            second.saved.record.id,
            True,
            CITY,
        )
        assert old.rested_at is not None
        assert await _awake(index, "Which city does the user live in?") == [HELSINKI]
        assert {note.content for note in await index.store.list_memories(10, 0)} == {OULU, HELSINKI}


async def test_three_values_leave_the_newest_awake_and_each_older_one_names_what_replaced_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        one: MemoryRecord = (await _save(index, OULU, CITY)).saved.record
        two: MemoryRecord = (await _save(index, HELSINKI, CITY)).saved.record
        three: Replacing = await _save(index, BERLIN, CITY)
        assert [note.id for note in three.replaced] == [two.id]
        assert (await index.store.get_memory(one.id)).replaced_by == two.id
        assert REST_TAG not in (await index.store.get_memory(three.saved.record.id)).tags
        assert await _awake(index, "Which city does the user live in?") == [BERLIN]


async def test_a_note_of_another_subject_or_none_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        await _save(index, OULU, CITY)
        await _save(index, "The user's office is in Turku.", "user-office")
        unkeyed: Replacing = await _save(index, "The user lives near the harbour.", None)
        assert unkeyed.replaced == ()
        awake: list[MemoryRecord] = [
            note for note in await index.store.list_memories(10, 0) if REST_TAG not in note.tags
        ]
        assert len(awake) == 3


async def test_the_same_text_saved_again_replaces_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        await _save(index, OULU, CITY)
        await _save(index, HELSINKI, CITY)
        again: Replacing = await _save(index, OULU, CITY)
        assert (again.saved.created, again.replaced) == (False, ())
        helsinki: list[MemoryRecord] = [
            note for note in await index.store.list_memories(10, 0) if note.content == HELSINKI
        ]
        assert [REST_TAG in note.tags for note in helsinki] == [False]


async def test_the_owners_notes_a_pinned_note_and_a_corrected_one_stay_and_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        written: MemoryRecord = (await save_memory(index, OULU, [], None, None, None, False)).record
        pinned: MemoryRecord = (
            await save_memory(index, "The user lives in Tampere.", [], RUN, None, None, False)
        ).record
        corrected: MemoryRecord = (
            await save_memory(index, "The user lives in Turku.", [], RUN, None, None, False)
        ).record
        for note in (written, pinned, corrected):
            await index.store.set_memory_subject(note.id, CITY)
        await index.store.set_memory_pin(pinned.id, time.time())
        await index.store.update_memory_vouched(corrected.id, "The user lives in Turku, Finland.", (), None, RUN)
        newer: Replacing = await _save(index, HELSINKI, CITY)
        assert newer.replaced == ()
        assert {note.id for note in newer.kept} == {written.id, pinned.id, corrected.id}
        for note in (written, pinned, corrected):
            stored: MemoryRecord = await index.store.get_memory(note.id)
            assert (REST_TAG in stored.tags, stored.replaced_by) == (False, None)


async def test_a_note_saved_while_reading_outside_content_replaces_only_a_note_that_did_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        clean: MemoryRecord = (await _save(index, OULU, CITY)).saved.record
        read: Replacing = await _save(index, HELSINKI, CITY, tainted=True)
        assert read.replaced == ()
        assert [note.id for note in read.kept] == [clean.id]
        assert REST_TAG not in (await index.store.get_memory(clean.id)).tags
        later: Replacing = await _save(index, BERLIN, CITY, tainted=True)
        assert [note.content for note in later.replaced] == [HELSINKI]
        assert REST_TAG not in (await index.store.get_memory(clean.id)).tags
        cleaner: Replacing = await _save(index, "The user lives in Lahti.", CITY)
        assert {note.id for note in cleaner.replaced} == {clean.id, later.saved.record.id}


async def test_with_no_subject_a_swapped_value_replaces_the_near_note_and_a_cosine_alone_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close: Vector = (1.0, 0.0, 0.0, 0.0)
    near: Vector = (0.92, 0.39192, 0.0, 0.0)
    food: Vector = (0.0, 0.0, 1.0, 0.0)
    food_near: Vector = (0.0, 0.0, 0.92, 0.39192)
    standup_old: str = "The daily standup is at 09:00."
    standup_new: str = "The daily standup is at 10:30."
    embedder: _Embedder = _Embedder(
        {standup_old: close, standup_new: near, "The user likes pizza.": food, "The user likes sushi.": food_near}
    )
    async with _index(tmp_path, monkeypatch, embedder) as index:
        await _save(index, standup_old, None)
        swapped: Replacing = await _save(index, standup_new, None)
        assert [note.content for note in swapped.replaced] == [standup_old]
        await _save(index, "The user likes pizza.", None)
        both: Replacing = await _save(index, "The user likes sushi.", None)
        assert both.replaced == ()
        assert [hit.record.content for hit in both.saved.similar] == ["The user likes pizza."]


async def test_with_no_embeddings_a_swapped_value_waits_for_a_subject_or_the_night(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        await _save(index, "The daily standup is at 09:00.", None)
        swapped: Replacing = await _save(index, "The daily standup is at 10:30.", None)
        assert swapped.replaced == ()


async def test_two_notes_with_other_subjects_are_never_taken_for_one_fact_by_their_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close: Vector = (1.0, 0.0, 0.0, 0.0)
    near: Vector = (0.92, 0.39192, 0.0, 0.0)
    embedder: _Embedder = _Embedder({"The call is at 09:00.": close, "The call is at 10:30.": near})
    async with _index(tmp_path, monkeypatch, embedder) as index:
        await _save(index, "The call is at 09:00.", "call-morning")
        other: Replacing = await _save(index, "The call is at 10:30.", "call-evening")
        assert other.replaced == ()


async def test_a_note_put_aside_and_forgotten_comes_back_from_the_trash_with_its_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        old: MemoryRecord = (await _save(index, OULU, CITY)).saved.record
        await _save(index, HELSINKI, CITY)
        await forget_memory(index, old.id)
        restored: MemoryRecord = await index.store.restore_memory(old.id, REST_TAG, time.time())
        assert (restored.content, restored.subject, restored.replaced_by) == (OULU, CITY, None)


def test_a_subject_is_kept_as_lowercase_words_joined_by_hyphens_and_one_without_a_word_is_refused() -> None:
    assert normalize_subject("  User City! ") == "user-city"
    assert normalize_subject("Kullanıcı Şehri") == "kullanıcı-şehri"
    assert len(normalize_subject("x" * 200)) == 48
    with pytest.raises(InvalidValueError, match="no letter or digit"):
        normalize_subject(" -- ")


async def test_a_subject_with_no_word_refuses_the_save_and_no_note_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        with pytest.raises(InvalidValueError, match="no letter or digit"):
            await _save(index, OULU, "--")
        assert await index.store.count_memories() == 0
