"""Owner edits that land while Dot is also writing memory.

The nightly reflection rewrites what it knows about the owner, and the Memory page saves the owner's own lines. Each
used to read the whole section and write it back, so a save during the reflection put the owner's lines back to what
they were when the night started, and a save of the owner's lines put Dot's lines back to what the page had read.

A note a run saved keeps that run as its author when the owner corrects it on the Memory page. The run was then never
handed the correction: a change was skipped whenever the author was the run, which is also how the run's own save is
kept out of the next step.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.memory.lifecycle import restore_note
from dotagent.memory.service import (
    MemoryIndex,
    OwnerSection,
    forget_memory,
    live_memories,
    owner_section,
    rewrite_memory_note,
    save_memory,
    set_dot_lines,
    set_owner_lines,
    set_owner_section,
    update_memory_note,
)
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

RUN: str = "run_this"
OTHER: str = "run_other"
SAVED: str = "The spare key is with the neighbour at number 12."
CORRECTED: str = "The spare key is in the blue bowl by the door."
REWRITTEN: str = "The spare key is on the hook in the hall."


async def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryIndex, Store]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    return MemoryIndex(store=store, embedder=None, config=config.memory), store


async def test_lines_saved_while_the_reflection_writes_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner saves their lines while the reflection's writer is answering. The night's new lines land, and the
    lines just saved stay: the reflection must not write back the lines it read when it started."""
    from dotagent.agent.reflection import _describe_owner
    from dotagent.agent.review import OwnerDraft

    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        await set_owner_section(
            store,
            OwnerSection(dot_lines=("Works late.",), owner_lines=("I read Turkish.",), updated_at=1.0),
        )

        async def write(_request: object) -> OwnerDraft:
            # What the Memory page did: read the section, then write it back with the new lines and the Dot lines
            # it had just read.
            before: OwnerSection = await owner_section(store)
            await set_owner_section(
                store,
                OwnerSection(dot_lines=before.dot_lines, owner_lines=("Answer me in Turkish.",), updated_at=5.0),
            )
            return OwnerDraft(lines=["Works from home."])

        monkeypatch.setattr("dotagent.agent.reflection.owner_writer_for", lambda *_args, **_kwargs: write)
        runtime = SimpleNamespace(
            store=store,
            router=None,
            roles=SimpleNamespace(review=lambda: "review", after_review=lambda _models: ()),
            config=SimpleNamespace(
                agent=SimpleNamespace(model_call_timeout_s=30.0),
                memory=SimpleNamespace(owner_chars=1500),
            ),
        )
        run = SimpleNamespace(finished_at=1.0, model="scripted", goal="Note where the spare key is.")
        assert await _describe_owner(runtime, [run], 2.0)  # type: ignore[arg-type]
        section: OwnerSection = await owner_section(store)
    finally:
        await store.close()
    assert section.dot_lines == ("Works from home.",)
    assert section.owner_lines == ("Answer me in Turkish.",)
    # The owner's save is newer than the night's clock: the section does not go back to the night's start.
    assert section.updated_at == 5.0


async def test_an_owner_correction_of_a_note_this_run_saved_is_handed_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        saved: MemoryRecord = (await save_memory(index, SAVED, [], RUN, None, None, False)).record
        # The run's own save is not handed back to it: the tool result already says it.
        assert await store.memories_changed_since(0.0, RUN) == []
        theirs: MemoryRecord = (
            await save_memory(index, "Other run's note about the printer.", [], OTHER, None, None, False)
        ).record
        assert [note.id for note in await store.memories_changed_since(0.0, RUN)] == [theirs.id]

        corrected: MemoryRecord = await update_memory_note(index, saved.id, CORRECTED, [])
        changed: list[MemoryRecord] = await store.memories_changed_since(0.0, RUN)
        assert [note.content for note in changed] == ["Other run's note about the printer.", CORRECTED]
        assert corrected.source_run == RUN
        handed = await live_memories(index, changed, 4000, None)
        assert [note.content for note in handed.records] == [
            "Other run's note about the printer.",
            CORRECTED,
        ]

        await rewrite_memory_note(index, saved.id, REWRITTEN, [], RUN, False, None)
        # The run's own rewrite replaces the correction and is not handed again.
        assert [note.id for note in await store.memories_changed_since(0.0, RUN)] == [theirs.id]
        # Another run is handed the rewrite: the author is now this run.
        assert saved.id in {note.id for note in await store.memories_changed_since(0.0, OTHER)}
    finally:
        await store.close()


async def test_saving_owner_lines_keeps_dot_lines_written_since_the_page_was_read(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        await set_owner_section(store, OwnerSection(dot_lines=("Old.",), owner_lines=("Mine.",), updated_at=1.0))
        seen: OwnerSection = await owner_section(store)
        await set_dot_lines(store, ("Written while the page was open.",), 4.0)
        saved: OwnerSection = await set_owner_lines(store, ("Saved from the page.",), 5.0)
    finally:
        await store.close()
    assert seen.dot_lines == ("Old.",)
    assert saved.dot_lines == ("Written while the page was open.",)
    assert saved.owner_lines == ("Saved from the page.",)
    assert saved.updated_at == 5.0


async def test_dot_lines_and_owner_lines_saved_at_once_both_stay(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        await set_owner_section(store, OwnerSection(dot_lines=("D0",), owner_lines=("O0",), updated_at=1.0))
        await asyncio.gather(
            set_dot_lines(store, ("D1",), 2.0),
            set_owner_lines(store, ("O1",), 3.0),
        )
        section: OwnerSection = await owner_section(store)
    finally:
        await store.close()
    assert section.dot_lines == ("D1",)
    assert section.owner_lines == ("O1",)


async def test_a_restored_owner_correction_is_still_handed_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        saved: MemoryRecord = (await save_memory(index, SAVED, [], RUN, None, None, True)).record
        await update_memory_note(index, saved.id, CORRECTED, [])
        await forget_memory(index, saved.id)
        assert await store.memories_changed_since(0.0, RUN) == []
        restored: MemoryRecord = await restore_note(index, saved.id, 50.0)
        changed: list[MemoryRecord] = await store.memories_changed_since(0.0, RUN)
        handed = await live_memories(index, changed, 4000, None)
    finally:
        await store.close()
    assert restored.content == CORRECTED and restored.source_run == RUN and restored.edited_by == "owner"
    assert restored.tainted is False
    assert [note.content for note in changed] == [CORRECTED]
    assert [note.content for note in handed.records] == [CORRECTED]
