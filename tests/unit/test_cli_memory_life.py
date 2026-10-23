"""``dotx memory`` for the life of the notes (item 584): the commands the parser knows, the proposals to forget and the
Keep and Forget that decide them, the trash with Restore and Purge, the pin, the merge taken back and the order the list
takes. A throwaway home with its own database; embeddings are off, so no service is reached and no model is called."""

import sqlite3
import time
from pathlib import Path

import pytest

from dotagent.cli import build_parser, parse_command
from dotagent.cli_commands import (
    MemoryForgetCommand,
    MemoryKeepCommand,
    MemoryListCommand,
    MemoryPinCommand,
    MemoryProposalsCommand,
    MemoryPurgeCommand,
    MemoryRestoreCommand,
    MemoryTrashCommand,
    MemoryUnmergeCommand,
)
from dotagent.cli_memory import _count, dispatch
from dotagent.core.paths import db_path, screenshots_dir
from dotagent.memory.service import REST_TAG
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

DAY_S: float = 86400.0


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("[memory]\nembed_enabled = false\n", encoding="utf-8")
    return tmp_path


async def _seeded(*notes: tuple[str, tuple[str, ...], float | None]) -> list[MemoryRecord]:
    """The notes a run saved, in the home's database, each with its tags and, when it is a number, the days since it
    began to rest (the note rests, by its tag, from then); the database is closed again for the command to open."""
    store: Store = await Store.open(db_path(), screenshots_dir())
    made: list[MemoryRecord] = []
    try:
        for content, tags, rested in notes:
            note: MemoryRecord = await store.add_memory(content, tags, "run_1", None, None, False)
            if rested is not None:
                note = await store.rest_memory(note.id, (*tags, REST_TAG), time.time() - rested * DAY_S)
            made.append(note)
    finally:
        await store.close()
    return made


def _uses(memory_id: str, count: int, last_used_at: float | None) -> None:
    connection: sqlite3.Connection = sqlite3.connect(db_path())
    try:
        connection.execute(
            "UPDATE memories SET used_count = ?, last_used_at = ? WHERE id = ?", (count, last_used_at, memory_id)
        )
        connection.commit()
    finally:
        connection.close()


def _say(*words: str) -> object:
    return parse_command(build_parser(), ["memory", *words])


def test_the_parser_knows_the_commands_of_the_notes_life_and_refuses_what_makes_no_sense() -> None:
    assert _say("list", "--order", "used") == MemoryListCommand(name="memory-list", limit=50, order="used")
    assert _say("forget", "mem_1") == MemoryForgetCommand(name="memory-forget", memory_id="mem_1", proposed=False)
    assert _say("forget", "--proposed") == MemoryForgetCommand(name="memory-forget", memory_id=None, proposed=True)
    assert _say("proposals") == MemoryProposalsCommand(name="memory-proposals")
    assert _say("keep", "mem_1", "mem_2") == MemoryKeepCommand(name="memory-keep", memory_ids=["mem_1", "mem_2"])
    assert _say("trash") == MemoryTrashCommand(name="memory-trash")
    assert _say("restore", "mem_1") == MemoryRestoreCommand(name="memory-restore", memory_id="mem_1")
    assert _say("purge", "mem_1") == MemoryPurgeCommand(name="memory-purge", memory_id="mem_1")
    assert _say("pin", "mem_1") == MemoryPinCommand(name="memory-pin", memory_id="mem_1")
    assert _say("unpin", "mem_1") == MemoryPinCommand(name="memory-unpin", memory_id="mem_1")
    assert _say("unmerge", "mem_1") == MemoryUnmergeCommand(name="memory-unmerge", memory_id="mem_1")
    # A note and --proposed together, neither of them, and an order that is none are refused.
    for refused in (["forget", "mem_1", "--proposed"], ["forget"], ["list", "--order", "sideways"], ["keep"]):
        with pytest.raises(SystemExit):
            parse_command(build_parser(), ["memory", *refused])


def test_a_line_of_the_notes_commands_that_counts_one_says_the_noun_in_the_singular() -> None:
    """The review of item 585, round 3, found "3 skill merge proposed" and, looking for the same slip, these lines said
    "1 notes in the trash", "1 proposed notes forgotten" and "1 days"."""
    assert [_count(number, "note") for number in (0, 1, 2)] == ["0 notes", "1 note", "2 notes"]
    assert [_count(number, "day") for number in (1, 30)] == ["1 day", "30 days"]


async def test_the_notes_that_rested_long_are_listed_and_kept_or_forgotten_into_a_trash_that_gives_them_back(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second, third, recent = await _seeded(
        ("Quote the glob patterns of zorblax scripts.", ("lesson",), 120),
        ("Read a PDF with pdftotext, not the browser.", ("lesson",), 100),
        ("Ask before you rename a folder.", ("lesson",), 95),
        ("A lesson that rested only ten days ago.", ("lesson",), 10),
    )
    assert await dispatch(MemoryProposalsCommand(name="memory-proposals")) == 0
    shown = capsys.readouterr()
    lines: list[str] = shown.out.splitlines()
    # The ones that rested longest first, the one that rested ten days not among them.
    assert len(lines) == 3 and all(
        note.content in line for note, line in zip((first, second, third), lines, strict=True)
    )
    assert all("resting since" in line for line in lines)
    assert "3 notes rested for 90 days or more" in shown.err and "--proposed" in shown.err

    # Keep wakes a note; an id of no note is said and the status says so.
    assert await dispatch(MemoryKeepCommand(name="memory-keep", memory_ids=[first.id])) == 0
    assert "Kept: " in capsys.readouterr().out
    assert await dispatch(MemoryKeepCommand(name="memory-keep", memory_ids=["mem_none"])) == 1
    assert "no note mem_none" in capsys.readouterr().err
    await dispatch(MemoryProposalsCommand(name="memory-proposals"))
    assert "2 notes rested" in capsys.readouterr().err

    # Forget one by its id: it goes to the trash, which says so and when it is deleted for good.
    assert await dispatch(MemoryForgetCommand(name="memory-forget", memory_id=second.id, proposed=False)) == 0
    assert "Forgot (in the trash for 30 days)" in capsys.readouterr().out
    assert await dispatch(MemoryTrashCommand(name="memory-trash")) == 0
    trashed = capsys.readouterr()
    assert second.content in trashed.out and "deleted for good" in trashed.out and "1 note in the trash" in trashed.err

    # Forget what is proposed: the one left (a line that counts one says "note", not "notes"); then nothing is
    # proposed, and what was forgotten comes back or goes for good.
    await dispatch(MemoryProposalsCommand(name="memory-proposals"))
    assert "1 note rested for 90 days or more" in capsys.readouterr().err
    assert await dispatch(MemoryForgetCommand(name="memory-forget", memory_id=None, proposed=True)) == 0
    assert "1 proposed note forgotten" in capsys.readouterr().err
    assert await dispatch(MemoryForgetCommand(name="memory-forget", memory_id=None, proposed=True)) == 0
    assert "no note is proposed to be forgotten" in capsys.readouterr().err
    assert await dispatch(MemoryRestoreCommand(name="memory-restore", memory_id=second.id)) == 0
    assert "Restored: " in capsys.readouterr().out
    assert await dispatch(MemoryRestoreCommand(name="memory-restore", memory_id="mem_none")) == 1
    assert "mem_none" in capsys.readouterr().err
    assert await dispatch(MemoryPurgeCommand(name="memory-purge", memory_id=third.id)) == 0
    assert f"Deleted for good: {third.id}" in capsys.readouterr().out
    assert await dispatch(MemoryPurgeCommand(name="memory-purge", memory_id=third.id)) == 1
    assert "not in the trash" in capsys.readouterr().err
    # The note that rested ten days and the one kept were never proposed, and nothing was forgotten but what was asked.
    await dispatch(MemoryListCommand(name="memory-list", limit=50, order="changed"))
    listed = capsys.readouterr()
    assert recent.content in listed.out and first.content in listed.out and second.content in listed.out
    assert third.content not in listed.out and "3 of 3 notes" in listed.err


async def test_a_note_is_pinned_and_a_merge_is_taken_back_and_the_list_takes_the_order_asked(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    busy, lately, never = await _seeded(
        ("Busy: used five times.", ("work",), None),
        ("Lately: used once, yesterday.", ("work",), None),
        ("Never: no run was handed it.", ("work",), None),
    )
    now: float = time.time()
    _uses(busy.id, 5, now - 3 * DAY_S)
    _uses(lately.id, 1, now - 0.5 * DAY_S)
    for order, first_line in (("used", lately.content), ("most_used", busy.content), ("least_used", never.content)):
        await dispatch(MemoryListCommand(name="memory-list", limit=50, order=order))  # type: ignore[arg-type]
        assert first_line in capsys.readouterr().out.splitlines()[0], order

    # The pin shows in the list, and comes off.
    assert await dispatch(MemoryPinCommand(name="memory-pin", memory_id=never.id)) == 0
    assert "Pinned: " in capsys.readouterr().out
    await dispatch(MemoryListCommand(name="memory-list", limit=50, order="changed"))
    assert any(never.content in line and "pinned" in line for line in capsys.readouterr().out.splitlines())
    assert await dispatch(MemoryPinCommand(name="memory-unpin", memory_id=never.id)) == 0
    assert "Unpinned: " in capsys.readouterr().out

    # A merge made by the store is taken back from the command line: the sources wake apart, the merged note is trashed.
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        merged: MemoryRecord = await store.merge_memories([busy.id, lately.id], lately.id, REST_TAG, now)
    finally:
        await store.close()
    await dispatch(MemoryListCommand(name="memory-list", limit=50, order="changed"))
    assert "merged from 2 notes" in capsys.readouterr().out
    assert await dispatch(MemoryUnmergeCommand(name="memory-unmerge", memory_id=merged.id)) == 0
    undone = capsys.readouterr().out
    assert "Took back the merge" in undone and undone.count("woke:") == 2
    await dispatch(MemoryTrashCommand(name="memory-trash"))
    assert merged.content in capsys.readouterr().out


async def test_the_trash_says_how_many_more_notes_it_holds_than_it_lists(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        for number in range(201):
            note: MemoryRecord = await store.add_memory(f"Note {number:03d} to forget.", (), "run_1", None, None, False)
            await store.trash_memory(note.id, time.time())
    finally:
        await store.close()
    assert await dispatch(MemoryTrashCommand(name="memory-trash")) == 0
    shown = capsys.readouterr()
    assert len(shown.out.splitlines()) == 200
    assert "200 of 201 notes in the trash, the newest first" in shown.err
