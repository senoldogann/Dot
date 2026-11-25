"""Item 1866: the words of a note recall handed a run are not saved back as a new note, judged on the words exactly."""

from pathlib import Path

from dotagent.config import load_default_config
from dotagent.memory.echo import note_words, recalled_echo
from dotagent.memory.render import render_note
from dotagent.memory.service import MemoryIndex
from dotagent.memory.supersede import handed_echo
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

CITY: str = "The user lives in Oulu."
CORES: str = "The staging server has 4 CPU cores."


def _note(note_id: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        id=note_id,
        content=content,
        tags=(),
        created_at=1.0,
        updated_at=1.0,
        source_run="run_x",
        agent_id=None,
        used_count=1,
        last_used_at=1.0,
        expires_at=None,
        replaced_by=None,
        tainted=False,
    )


def test_the_same_words_in_another_case_spacing_or_punctuation_are_the_handed_note() -> None:
    city: MemoryRecord = _note("mem_city", CITY)
    assert recalled_echo("the user  lives in OULU", [city]) == city
    assert note_words("İstanbul'da, 09:00.") == ("istanbul", "da", "09", "00")


def test_the_line_recall_drew_a_note_in_and_a_sentence_around_it_are_the_note() -> None:
    city: MemoryRecord = _note("mem_city", CITY)
    assert recalled_echo(render_note(city), [city]) == city
    assert recalled_echo(f"Remember: {CITY} She works from home.", [city]) == city


def test_a_changed_value_a_new_fact_or_a_short_note_inside_a_longer_one_is_not_an_echo() -> None:
    cores: MemoryRecord = _note("mem_cores", CORES)
    assert recalled_echo("The staging server has 8 CPU cores.", [cores]) is None
    assert recalled_echo("The user lives in Helsinki.", [_note("mem_city", CITY)]) is None
    short: MemoryRecord = _note("mem_short", "Prefers tea.")
    assert recalled_echo("Prefers tea.", [short]) == short
    assert recalled_echo("Prefers tea in the morning and coffee after lunch.", [short]) is None


async def test_only_the_notes_handed_to_the_run_count(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        index: MemoryIndex = MemoryIndex(store=store, embedder=None, config=load_default_config().memory)
        city: MemoryRecord = await store.add_memory(CITY, (), "run_a", None, None, False)
        assert await handed_echo(index, "run_b", CITY) is None
        await store.mark_memories_used("run_b", [city.id], 2.0, "search")
        echo: MemoryRecord | None = await handed_echo(index, "run_b", f"{CITY}")
        assert echo is not None and echo.id == city.id
        assert await handed_echo(index, "run_c", CITY) is None
    finally:
        await store.close()
