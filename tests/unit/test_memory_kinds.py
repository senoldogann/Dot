"""What a note is (item 1975): the fixed rules that tell its kind, the plans that rank a run's starting notes by kind
for where the run came from, and the fast model's pass over what the rules cannot tell (a stand-in answers: no model
is called)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent.loop import start_notes
from dotagent.config import AppConfig, load_config
from dotagent.llm.router import parse_model_ref
from dotagent.memory.kinds import classify_untold, parse_kind
from dotagent.memory.service import MemoryIndex, Remembered, prompt_memories, save_memory
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import REVIEW_SOURCE, UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.kinds import HELPER_PLAN, REVIEW_PLAN, SCHEDULE_PLAN, MemoryKind, kind_plan, rule_kind
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.memory import build_memory_tools
from dotagent.tools.team import TEAM_SOURCE

PRICE: str = "Müşteriye fiyat (Senol'un kararı): workspace/price-list.md listesinden sorulur."
WHO: str = "The owner is a software developer who lives in Oulu."
STEPS: str = "How to publish the blog: build it, then push the main branch of the site's repository."
STATE: str = "The blog's repository moved to the labs organisation."


@pytest.mark.parametrize(
    ("content", "tags", "kind"),
    [
        ("Open the whole file before citing it.", ("lesson", "review"), "lesson"),
        ("On 2026-11-25 I worked on 249 runs.", ("journal",), "fact"),
        ("Answers go in Turkish.", ("core", "dil", "tercih"), "rule"),
        ("Sends via the webmail.", ("site-webmail-zone-fi",), "workflow"),
        ("Deploy with the CLI.", ("Deploy",), "workflow"),
        ("Jani is the client's lead.", ("müşteri",), "people"),
        ("Born in 1995.", ("aboutme",), "profile"),
        ("The thesis is 40 % written.", ("proje",), "fact"),
        (PRICE, ("core", "business"), "rule"),
        ("Senol does not want version numbers in file names.", (), "rule"),
        ("Ashby forms: how to fill them without clicking.", ("job-search",), "workflow"),
        ("The printer is on the second floor.", (), None),
    ],
)
def test_the_rules_tell_a_notes_kind_by_its_tags_then_its_words(
    content: str, tags: tuple[str, ...], kind: MemoryKind | None
) -> None:
    assert rule_kind(content, tags) == kind


def test_a_plan_is_chosen_by_the_runs_source_as_the_modules_name_it() -> None:
    assert kind_plan(REVIEW_SOURCE) == REVIEW_PLAN
    assert kind_plan(SCHEDULE_SOURCE) == SCHEDULE_PLAN
    assert kind_plan(DELEGATE_SOURCE) == HELPER_PLAN
    assert kind_plan(TEAM_SOURCE) == HELPER_PLAN
    assert kind_plan(UI_SOURCE) is None


def test_the_fast_models_answer_must_be_one_kind() -> None:
    assert parse_kind(" Workflow.\n", "mem_1") == "workflow"
    with pytest.raises(Exception, match="not one of the kinds"):
        parse_kind("a workflow, probably", "mem_1")


async def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryIndex, Store]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    index: MemoryIndex = MemoryIndex(store=store, embedder=None, config=config.memory)
    # Standing notes of the owner's, one of each kind but people and lessons, written oldest first.
    await save_memory(index, PRICE, ["core", "business"], None, None, None, False)
    await save_memory(index, WHO, ["core", "aboutme"], None, None, None, False)
    await save_memory(index, STATE, ["core", "proje"], None, None, None, False)
    await save_memory(index, STEPS, ["core", "deploy"], None, None, None, False)
    return index, store


def _starting(index: MemoryIndex, source: str, goal: str) -> object:
    # start_notes reads the memory and, for how long it waits for the notes by meaning, [memory] start_recall_s.
    runtime = SimpleNamespace(memory=index, config=SimpleNamespace(memory=index.config))
    run = SimpleNamespace(source=source, agent_id=None)
    return start_notes(runtime, run, goal, 4000)  # type: ignore[arg-type]  # start_notes reads these alone


async def test_a_chat_starts_with_the_notes_it_started_with_before_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        chat: Remembered = await _starting(index, UI_SOURCE, "Fix the failing check.")  # type: ignore[misc]
        before: Remembered = await prompt_memories(index, "Fix the failing check.", 4000, None)
    finally:
        await store.close()
    assert chat == before
    assert chat.plan is None
    assert [record.content for record in chat.records] == [STEPS, STATE, WHO, PRICE]


async def test_a_schedule_starts_with_its_workflow_and_the_owners_rules_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        firing: Remembered = await _starting(index, SCHEDULE_SOURCE, "Publish this week's post.")  # type: ignore[misc]
    finally:
        await store.close()
    assert firing.plan == "schedule"
    assert [record.content for record in firing.records] == [STEPS, PRICE, STATE, WHO]


async def test_a_helper_is_handed_the_owners_standing_notes_only_when_they_fit_its_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        review: Remembered = await _starting(index, DELEGATE_SOURCE, "Review the diff of the shell guard.")  # type: ignore[misc]
        blog: Remembered = await _starting(index, TEAM_SOURCE, "Publish the blog from its repository.")  # type: ignore[misc]
    finally:
        await store.close()
    assert review.plan == "helper"
    assert review.records == ()
    assert {record.content for record in blog.records} == {STEPS, STATE}
    assert blog.records[0].content == STEPS


async def test_a_note_the_rules_cannot_tell_is_told_by_the_fast_model_and_recorded_as_its(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    answers: list[str] = ["banana", "people"]

    async def window_of(ref: object) -> None:
        return None

    async def complete(ref: object, request: object, on_delta: object, origin: object) -> SimpleNamespace:
        return SimpleNamespace(text=answers.pop(0))

    router = SimpleNamespace(window_of=window_of, complete=complete)
    try:
        odd: str = (await save_memory(index, "Kalle answers within a day.", [], None, None, None, False)).record.id
        printer: str = (await save_memory(index, "The printer is upstairs.", [], None, None, None, False)).record.id
        told: int = await classify_untold(store, router, parse_model_ref("ollama:fast"), 5.0)  # type: ignore[arg-type]
        # The newest note was answered with no kind and keeps none; the other is the model's.
        assert told == 1
        assert [note.id for note in await store.memories_without_kind(5)] == [printer]
        kalle = await store.get_memory(odd)
        assert (kalle.kind, kalle.kind_by) == ("people", "model")
        # A kind told meanwhile by a run or the owner is not overwritten by a later pass.
        await store.set_memory_kind(printer, "fact", "owner")
        assert not await store.tell_untold_kind(printer, "people", "model")
        assert (await store.get_memory(printer)).kind_by == "owner"
    finally:
        await store.close()


async def test_a_merged_note_takes_its_kept_notes_kind_and_a_note_merged_away_is_not_asked_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    try:
        kept: str = (await save_memory(index, "Kalle answers within a day.", [], "run_1", None, None, False)).record.id
        other: str = (await save_memory(index, "Kalle replies the same day.", [], "run_2", None, None, False)).record.id
        await store.set_memory_kind(kept, "people", "run")
        merged = await store.merge_memories([kept, other], kept, "rest", 10.0)
        assert (merged.kind, merged.kind_by) == ("people", "run")
        # The source the rules could not tell rests in the merged note now: the model's pass leaves it.
        assert other not in {note.id for note in await store.memories_without_kind(10)}
    finally:
        await store.close()


async def test_memory_save_records_the_kind_a_run_names_on_a_new_note_and_keeps_an_older_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, store = await _index(tmp_path, monkeypatch)
    tool = next(tool for tool in build_memory_tools(600) if tool.spec.name == "memory_save")
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(memory=index),
        run_id="run_1",
        agent_id=None,
        tainted=lambda: False,
        ever_tainted=lambda: False,
    )
    try:
        for content in ("Kalle answers within a day.", PRICE):
            prepared = await tool.prepare({"content": content, "kind": "people"}, ctx)  # type: ignore[arg-type]
            assert (await prepared.run(ctx)).is_error is False  # type: ignore[arg-type]
        notes = {note.content: (note.kind, note.kind_by) for note in await store.list_memories(10, 0)}
    finally:
        await store.close()
    assert notes["Kalle answers within a day."] == ("people", "run")
    # The owner's note had these words already: it keeps the kind it had.
    assert notes[PRICE] == ("rule", "rule")
