"""A code review starts with the notes about code alone (audit 2026-11-24, D3).

The pre-push review was handed the owner's business notes and Dot's pinned character notes at every start, 1,606 tokens
a review on average, none of which tells it anything about a diff.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent.loop import start_notes
from dotagent.config import AppConfig, load_config
from dotagent.memory.service import MemoryIndex, Remembered, prompt_memories, review_memories, save_memory
from dotagent.server import REVIEW_SOURCE, UI_SOURCE
from dotagent.store.db import Store

PRICE: str = "A website costs the client 900 euros, delivered in two weeks."
GIT: str = "In the Dot repository a branch is merged with scripts/merge_pr.py, never by hand."
LESSON: str = "Run ruff format before the review: the gate fails on formatting alone."
GOAL: str = "Review the diff of this branch."


async def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryIndex, Store]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    index: MemoryIndex = MemoryIndex(store=store, embedder=None, config=config.memory)
    await save_memory(index, PRICE, ["core", "business"], None, None, None, False)
    await save_memory(index, GIT, ["core", "git"], None, None, None, False)
    await save_memory(index, LESSON, ["lesson", "review"], "run_old", None, None, False)
    return index, store


async def test_a_review_starts_with_the_notes_about_code_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        everything: Remembered = await prompt_memories(index, GOAL, 4000, None)
        review: Remembered = await review_memories(index, GOAL, 4000, None)
    finally:
        await store.close()
    assert PRICE in {record.content for record in everything.records}
    assert {record.content for record in review.records} == {GIT, LESSON}
    assert review.tokens < everything.tokens


async def test_a_runs_source_decides_which_notes_it_starts_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    # start_notes reads the memory and, for how long it waits for the notes by meaning, [memory] start_recall_s.
    runtime = SimpleNamespace(memory=index, config=SimpleNamespace(memory=index.config))
    try:
        review: Remembered = await start_notes(
            runtime,  # type: ignore[arg-type]  # start_notes reads the memory and its config
            SimpleNamespace(source=REVIEW_SOURCE, agent_id=None),  # type: ignore[arg-type]
            GOAL,
            4000,
        )
        chat: Remembered = await start_notes(
            runtime,  # type: ignore[arg-type]  # start_notes reads the memory and its config
            SimpleNamespace(source=UI_SOURCE, agent_id=None),  # type: ignore[arg-type]
            GOAL,
            4000,
        )
    finally:
        await store.close()
    assert PRICE not in {record.content for record in review.records}
    assert PRICE in {record.content for record in chat.records}
