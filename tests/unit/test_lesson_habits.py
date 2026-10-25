"""A lesson is compared with the habits and the tool guidance the adopted harness gives every run (item 584, the
audit of the harness): the lesson writer is shown them, and a lesson that repeats one, by its words or by its meaning
the same way round, is not drawn, since the prompt says it already. Real SQLite; stubs stand for the review model and
the embedding service."""

import math
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent import lessons as lessons_module
from dotagent.agent.lessons import LessonCase, draw_lessons, repeating_rules
from dotagent.agent.review import LessonProposal, LessonRequest, _lessons_prompt
from dotagent.config import AppConfig, load_config
from dotagent.harness.components import HarnessText, rules_of
from dotagent.harness.service import adopt, create_version
from dotagent.memory.ranking import Vector
from dotagent.memory.service import MemoryIndex
from dotagent.store.db import Store

HABIT: str = "Read a file before you change it, and check the result after."
TOOL_RULE: str = "Quote a path that holds a space when you give it to a command."
FRESH: str = "Run pdftotext with -layout when a table must keep its columns."
GOAL: str = "Turn the quarterly report into a table."


def _unit(angle: float) -> Vector:
    return (math.cos(angle), math.sin(angle))


class _Embedder:
    """A text the test placed is where it said; any other is on a place of its own, a good way from the others."""

    def __init__(self, places: dict[str, Vector]) -> None:
        self.places: dict[str, Vector] = places

    @property
    def model(self) -> str:
        return "stub"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self.places.setdefault(text, _unit(1.0 + 0.7 * len(self.places))) for text in texts]

    async def aclose(self) -> None:
        return None


class _Writer:
    """The lesson writer: it keeps what it is asked and proposes the lessons it was given."""

    def __init__(self, lessons: Sequence[str]) -> None:
        self.lessons: list[str] = list(lessons)
        self.requests: list[LessonRequest] = []

    def factory(self, *_: object) -> "_Writer":
        return self

    async def __call__(self, request: LessonRequest) -> LessonProposal:
        self.requests.append(request)
        return LessonProposal(lessons=self.lessons)


@asynccontextmanager
async def _home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: _Embedder | None, habits: HarnessText | None
) -> AsyncIterator[tuple[SimpleNamespace, LessonCase]]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    runtime = SimpleNamespace(
        config=config,
        store=store,
        memory=MemoryIndex(store=store, embedder=embedder, config=config.memory),
        router=object(),
        roles=SimpleNamespace(review=lambda: "scripted:any", after_review=lambda own=(): tuple(own)),
    )
    thread = await store.create_thread("t", "test")
    run = await store.create_run(
        thread.id, GOAL, "task", "ollama:x", None, "test", None, "standard", None, "ask", False, None, None, None
    )
    if habits is not None:
        version = await create_version(store, config.harness, habits, "habits", "owner", "", ())
        await adopt(store, version.id, time.time())
    case: LessonCase = LessonCase(
        run_id=run.id,
        thread_id=thread.id,
        model="ollama:x",
        goal=GOAL,
        digest="",
        failures=(),
        resolved=(),
        answer="Done.",
        tainted=False,
        reached_goal=True,
    )
    try:
        yield runtime, case
    finally:
        await store.close()


def test_the_rules_of_the_adopted_texts_are_their_lines_without_bullets_or_blanks_habits_first() -> None:
    text: HarnessText = HarnessText(
        habits=f"- {HABIT}\n\n* Plan first.\n", tool_guidance=f"1. {TOOL_RULE}\n2) Slow down.\n"
    )
    assert rules_of(text) == (HABIT, "Plan first.", TOOL_RULE, "Slow down.")
    assert rules_of(HarnessText(habits="", tool_guidance="")) == ()


def test_the_writer_is_shown_the_habits_in_a_block_of_their_own_and_a_run_with_none_is_asked_as_before() -> None:
    plain: LessonRequest = LessonRequest(goal=GOAL, digest="", signals=(), answer="", known=(), reached_goal=True)
    shown: str = _lessons_prompt(plain.model_copy(update={"habits": (HABIT, TOOL_RULE)}), "abc")
    assert f"<<HABITS AND TOOL GUIDANCE THE AGENT FOLLOWS ALREADY abc>>\n- {HABIT}\n- {TOOL_RULE}\n" in shown
    assert "HABITS AND TOOL GUIDANCE" not in _lessons_prompt(plain, "abc")


async def test_a_lesson_that_repeats_a_habit_by_its_words_is_not_drawn_and_the_writer_was_shown_the_habits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text: HarnessText = HarnessText(habits=f"- {HABIT}\n", tool_guidance=f"- {TOOL_RULE}\n")
    async with _home(tmp_path, monkeypatch, None, text) as (runtime, case):
        writer: _Writer = _Writer([HABIT.rstrip("."), FRESH, TOOL_RULE])
        monkeypatch.setattr(lessons_module, "lesson_writer_for", writer.factory)
        drawn: tuple[str, ...] = await draw_lessons(runtime, case, ("a call failed",))  # type: ignore[arg-type]
        assert drawn == (FRESH,)
        assert writer.requests[0].habits == (HABIT, TOOL_RULE)


async def test_a_lesson_that_repeats_a_habit_by_its_meaning_is_not_drawn_but_its_opposite_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close: str = "Look at a file before editing it, then check the outcome."
    opposite: str = "Do not look at a file before editing it; check the outcome only."
    embedder: _Embedder = _Embedder({HABIT: _unit(0.0), close: _unit(0.03), opposite: _unit(0.03)})
    async with _home(tmp_path, monkeypatch, embedder, HarnessText(habits=HABIT, tool_guidance="")) as (runtime, case):
        writer: _Writer = _Writer([close, opposite, FRESH])
        monkeypatch.setattr(lessons_module, "lesson_writer_for", writer.factory)
        drawn: tuple[str, ...] = await draw_lessons(runtime, case, ("a call failed",))  # type: ignore[arg-type]
        assert drawn == (opposite, FRESH)
        # The comparison alone, with the rule each text repeats.
        found: dict[str, str] = await repeating_rules(runtime, [close, opposite, FRESH], [HABIT])  # type: ignore[arg-type]
        assert found == {close: HABIT}
        assert await repeating_rules(runtime, [], [HABIT]) == {}  # type: ignore[arg-type]
        assert await repeating_rules(runtime, [close], []) == {}  # type: ignore[arg-type]


async def test_with_no_habit_adopted_every_lesson_goes_on_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _home(tmp_path, monkeypatch, None, None) as (runtime, case):
        writer: _Writer = _Writer([HABIT, FRESH])
        monkeypatch.setattr(lessons_module, "lesson_writer_for", writer.factory)
        drawn: tuple[str, ...] = await draw_lessons(runtime, case, ("a call failed",))  # type: ignore[arg-type]
        assert drawn == (HABIT, FRESH) and writer.requests[0].habits == ()
