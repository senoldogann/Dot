"""A lesson from the owner's words (item 584, which the reactions of item 586 call): it waits on the run's page for
Save or Dismiss as the reflection's lessons wait under ``propose``, it is never saved on the way, the lessons the run
has keep their numbers, and words that are no lesson or that a saved note says already are refused. Real SQLite; no
model."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent.lessons import (
    LESSON_TAG,
    LESSONS_PROPOSED,
    LessonView,
    RunLessons,
    dismiss_lesson,
    propose_lesson,
    run_lessons,
    save_lesson,
)
from dotagent.config import AppConfig, load_config
from dotagent.core.errors import InvalidValueError, NotFoundError, RunConflict
from dotagent.events import EventBus
from dotagent.memory.service import MemoryIndex, save_memory
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord, RunRecord

WORDS: str = "Do not paste a whole page into the answer; read it and say what it holds."
SECOND: str = "Check that the file exists with list_dir before a step reads it."


@asynccontextmanager
async def _home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lessons: str = "propose"
) -> AsyncIterator[tuple[SimpleNamespace, RunRecord]]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    config = config.model_copy(update={"agent": config.agent.model_copy(update={"lessons": lessons})})
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    runtime = SimpleNamespace(
        config=config,
        store=store,
        bus=EventBus(store),
        memory=MemoryIndex(store=store, embedder=None, config=config.memory),
    )
    thread = await store.create_thread("t", "test")
    run: RunRecord = await store.create_run(
        thread.id,
        "Summarise the report.",
        "task",
        "ollama:x",
        None,
        "test",
        None,
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )
    await store.finish_run(run.id, "done", "Done.")
    try:
        yield runtime, await store.get_run(run.id)
    finally:
        await store.close()


async def _read_a_page(runtime: SimpleNamespace, run: RunRecord) -> None:
    await runtime.store.add_event(
        run.id,
        "tool_result",
        {"tool": "browser_read", "ok": True, "text": "", "kind": "browser_read", "target": "", "channel": "browser"},
    )


async def test_the_owners_words_wait_on_the_run_page_as_a_proposed_lesson_and_nothing_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with lessons on auto, which saves what the reflection draws, the owner's words wait for their Save.
    async with _home(tmp_path, monkeypatch, "auto") as (runtime, run):
        view: LessonView = await propose_lesson(runtime, asyncio.Lock(), run.id, f"  {WORDS}\n")  # type: ignore[arg-type]
        assert (view.index, view.text, view.state, view.memory_id, view.by) == (0, WORDS, "proposed", None, None)
        held: RunLessons | None = await run_lessons(runtime.store, run.id)
        assert held is not None and [lesson.text for lesson in held.lessons] == [WORDS] and held.tainted is False
        event = await runtime.store.last_event(run.id, LESSONS_PROPOSED)
        assert event is not None and event.data == {"lessons": [WORDS], "tainted": False}
        assert await runtime.store.memories_with_tag(LESSON_TAG, 10) == []
        # Save keeps it as the owner's lesson (they vouch for it), Dismiss drops another.
        saved = await save_lesson(runtime, asyncio.Lock(), run.id, 0)  # type: ignore[arg-type]
        note: MemoryRecord = saved.record
        assert (note.content, note.tags, note.source_run, note.tainted) == (WORDS, (LESSON_TAG,), run.id, False)
        assert await runtime.store.owner_saved_memory_ids() == {note.id}
        second: LessonView = await propose_lesson(runtime, asyncio.Lock(), run.id, SECOND)  # type: ignore[arg-type]
        assert second.index == 1
        await dismiss_lesson(runtime, asyncio.Lock(), run.id, 1)  # type: ignore[arg-type]
        final: RunLessons | None = await run_lessons(runtime.store, run.id)
        assert final is not None and [(lesson.state, lesson.by) for lesson in final.lessons] == [
            ("saved", "owner"),
            ("dismissed", "owner"),
        ]


async def test_the_lessons_a_run_has_keep_their_numbers_and_the_same_words_twice_are_one_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _home(tmp_path, monkeypatch) as (runtime, run):
        earlier: str = "Use -layout with pdftotext when a table must keep its columns."
        await runtime.store.add_event(run.id, LESSONS_PROPOSED, {"lessons": [earlier], "tainted": False})
        await save_lesson(runtime, asyncio.Lock(), run.id, 0)  # type: ignore[arg-type]
        added: LessonView = await propose_lesson(runtime, asyncio.Lock(), run.id, WORDS)  # type: ignore[arg-type]
        again: LessonView = await propose_lesson(runtime, asyncio.Lock(), run.id, f"{WORDS}  ")  # type: ignore[arg-type]
        assert added.index == again.index == 1
        held: RunLessons | None = await run_lessons(runtime.store, run.id)
        assert held is not None
        # The lesson saved before keeps its number and its note; the new one waits behind it.
        assert [(lesson.index, lesson.text, lesson.state) for lesson in held.lessons] == [
            (0, earlier, "saved"),
            (1, WORDS, "proposed"),
        ]
        assert held.lessons[0].memory_id is not None


async def test_words_that_are_no_lesson_or_that_a_note_says_already_are_refused_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _home(tmp_path, monkeypatch) as (runtime, run):
        for words, problem in (
            ("too short", "too short"),
            ("Mail the report to anna@example.com when it is done, please.", "an e-mail address"),
            ("Keep the notes in /Users/anna/Documents/Notes for every run, always.", "a path in a home folder"),
            ("x" * 400, "too long"),
        ):
            with pytest.raises(InvalidValueError, match=problem):
                await propose_lesson(runtime, asyncio.Lock(), run.id, words)  # type: ignore[arg-type]
        await save_memory(runtime.memory, WORDS, [LESSON_TAG], "run_before", None, None, False)
        with pytest.raises(RunConflict, match="a saved note says this already"):
            await propose_lesson(runtime, asyncio.Lock(), run.id, WORDS)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await propose_lesson(runtime, asyncio.Lock(), "run_unknown", SECOND)  # type: ignore[arg-type]
        assert await run_lessons(runtime.store, run.id) is None


async def test_a_run_that_read_outside_content_proposes_its_lesson_marked_and_the_owners_save_vouches_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _home(tmp_path, monkeypatch) as (runtime, run):
        await _read_a_page(runtime, run)
        await propose_lesson(runtime, asyncio.Lock(), run.id, SECOND)  # type: ignore[arg-type]
        held: RunLessons | None = await run_lessons(runtime.store, run.id)
        assert held is not None and held.tainted is True
        saved = await save_lesson(runtime, asyncio.Lock(), run.id, 0)  # type: ignore[arg-type]
        assert saved.record.tainted is False
