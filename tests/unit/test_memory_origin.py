"""Item 1864: a note records where it came from, set by the store from the run that saved it and never by a model."""

import sqlite3
from pathlib import Path

import pytest

from dotagent.memory.service import REFLECTION_SOURCE
from dotagent.proactive import INITIATIVE_SOURCE, NOTICE_SOURCE, REPLY_SOURCE, WATCHER_SOURCE
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import CLI_SOURCE, REVIEW_SOURCE, UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import MemoryOrigin, MemoryRecord, RunRecord
from dotagent.store.provenance import (
    HELPER_RUNS,
    INITIATIVE_RUN,
    REFLECTION_RUN,
    SCHEDULE_RUN,
    WATCHER_RUNS,
    note_origin,
    run_origin,
)
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.team import TEAM_SOURCE


def test_the_run_sources_written_out_are_the_constants_of_the_modules_that_start_the_runs() -> None:
    assert SCHEDULE_RUN == SCHEDULE_SOURCE
    assert {WATCHER_SOURCE, REPLY_SOURCE, NOTICE_SOURCE} == WATCHER_RUNS
    assert INITIATIVE_RUN == INITIATIVE_SOURCE
    assert {DELEGATE_SOURCE, TEAM_SOURCE} == HELPER_RUNS
    assert REFLECTION_RUN == REFLECTION_SOURCE


@pytest.mark.parametrize(
    ("source", "parent", "origin"),
    [
        (UI_SOURCE, None, "chat"),
        (CLI_SOURCE, None, "chat"),
        (REVIEW_SOURCE, None, "chat"),
        (SCHEDULE_SOURCE, None, "schedule"),
        (WATCHER_SOURCE, None, "watcher"),
        (REPLY_SOURCE, None, "watcher"),
        (INITIATIVE_SOURCE, None, "initiative"),
        (DELEGATE_SOURCE, "run_lead", "helper"),
        (TEAM_SOURCE, None, "helper"),
        # A schedule's run that another run started is a helper's: the one that saves is the helper.
        (SCHEDULE_SOURCE, "run_lead", "helper"),
    ],
)
def test_a_run_s_origin_follows_its_source_and_its_parent(
    source: str, parent: str | None, origin: MemoryOrigin
) -> None:
    assert run_origin(source, parent) == origin


def test_a_note_with_no_run_is_the_owner_s_the_reflection_s_is_its_own_and_a_gone_run_says_nothing() -> None:
    assert note_origin(None, None) == "owner"
    assert note_origin(REFLECTION_SOURCE, None) == "reflection"
    assert note_origin("run_gone", None) is None
    assert note_origin("run_x", (SCHEDULE_SOURCE, None)) == "schedule"


async def _run(store: Store, source: str, parent: str | None) -> RunRecord:
    thread = await store.create_thread("t", source)
    return await store.create_run(
        thread.id,
        "goal",
        "task",
        "ollama:m",
        parent,
        source,
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )


async def test_the_store_sets_the_origin_from_the_saving_run_and_keeps_it_through_trash_and_correction(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        scheduled: RunRecord = await _run(store, SCHEDULE_SOURCE, None)
        chat: RunRecord = await _run(store, UI_SOURCE, None)
        note: MemoryRecord = await store.add_memory("The mail check runs hourly.", (), scheduled.id, None, None, False)
        assert note.origin == "schedule"
        assert (await store.get_memory(note.id)).origin == "schedule"
        owner: MemoryRecord = await store.add_memory("I like tea.", (), None, None, None, False)
        assert owner.origin == "owner"
        unknown: MemoryRecord = await store.add_memory("A note of no run.", (), "run_none", None, None, False)
        assert unknown.origin is None
        # A rewrite by a run of a chat makes the words that run's.
        rewritten = await store.update_memory(note.id, "The mail check runs at 9.", (), None, chat.id, False, None)
        assert rewritten.record.origin == "chat"
        # The owner's correction keeps the author and the origin (a run's rewrite after it only waits for them).
        corrected: MemoryRecord = await store.update_memory_vouched(
            note.id, "The mail check runs every two hours.", (), None, chat.id
        )
        assert corrected.origin == "chat"
        await store.trash_memory(note.id, 1.0)
        restored: MemoryRecord = await store.restore_memory(note.id, "rest", 2.0)
        assert restored.origin == "chat"
    finally:
        await store.close()


async def test_an_older_database_has_its_notes_origins_filled_once_from_the_runs_still_stored(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.db"
    store: Store = await Store.open(path, tmp_path / "images")
    try:
        watcher: RunRecord = await _run(store, WATCHER_SOURCE, None)
        kept: MemoryRecord = await store.add_memory("A watched mail came.", (), watcher.id, None, None, False)
        owner: MemoryRecord = await store.add_memory("The owner's own note.", (), None, None, None, False)
        gone: MemoryRecord = await store.add_memory("A note whose run is gone.", (), "run_gone", None, None, False)
    finally:
        await store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE memories DROP COLUMN origin")
    reopened: Store = await Store.open(path, tmp_path / "images")
    try:
        assert (await reopened.get_memory(kept.id)).origin == "watcher"
        assert (await reopened.get_memory(owner.id)).origin == "owner"
        assert (await reopened.get_memory(gone.id)).origin is None
    finally:
        await reopened.close()
