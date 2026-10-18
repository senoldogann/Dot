"""Notes written in Turkish are found by the same words typed with other letters or with another ending: real SQLite and
the real search, words only (no embedding model, no network)."""

import sqlite3
from pathlib import Path

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.memory.service import MemoryHit, MemoryIndex, open_memory_index, recall, save_memory
from dotagent.store.db import Store

MEETING: str = "Toplantı notları Notion yerine Obsidian'da tutuluyor."
CITY: str = "Kullanıcı İstanbul'da yaşıyor."
LAPTOP: str = "The user's work laptop is a MacBook Pro with an M3 chip."
WATER: str = "Kullanıcı günde iki litre su içmeyi hedefliyor."


def _words_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    return config.model_copy(update={"memory": config.memory.model_copy(update={"embed_enabled": False})})


async def _found(index: MemoryIndex, query: str) -> list[str]:
    hits: list[MemoryHit] = await recall(index, query, 5)
    return [hit.record.content for hit in hits]


@pytest.mark.parametrize(
    ("query", "note"),
    [
        ("toplanti", MEETING),
        ("TOPLANTI", MEETING),
        ("toplantıda", MEETING),
        ("toplantilarimiz", MEETING),
        ("istanbul", CITY),
        ("ISTANBUL", CITY),
        ("İstanbul'da", CITY),
        ("yasiyor", CITY),
        ("laptop", LAPTOP),
        ("su", WATER),
    ],
)
async def test_a_note_is_found_whatever_the_letters_or_the_ending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str, note: str
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    index: MemoryIndex = await open_memory_index(store, _words_only(tmp_path, monkeypatch))
    try:
        for content in (MEETING, CITY, LAPTOP, WATER):
            await save_memory(index, content, [], None, None, None, False)
        assert await _found(index, query) == [note]
    finally:
        await store.close()


async def test_a_database_with_an_index_of_the_old_form_is_rebuilt_when_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    index: MemoryIndex = await open_memory_index(store, _words_only(tmp_path, monkeypatch))
    await save_memory(index, MEETING, ["toplantı"], None, None, None, False)
    await store.close()

    # What an older version left behind: the text in the index as written.
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE memories_fts SET content = ?, tags = ?", (MEETING, "toplantı"))
        connection.commit()
    finally:
        connection.close()

    reopened: Store = await Store.open(database, tmp_path / "images")
    try:
        index = await open_memory_index(reopened, _words_only(tmp_path, monkeypatch))
        assert await _found(index, "toplanti") == [MEETING]
        assert [note.content for note in await reopened.list_memories(10, 0)] == [MEETING]
    finally:
        await reopened.close()
