"""What the owner disliked reaches the learning (item 654). A run that follows up the owner's 👎 or 🔁 on an earlier run
is asked for its lessons as a run that went wrong is, with a signal that says the owner disliked the earlier run's
result; a ❓, a reaction taken back and any other run give none. The follow-up's page names the notes the disliked run
was handed and that are still kept: one may be what led it astray. Before, a 👎 started a follow-up that fixed the
answer and taught the runs after it nothing."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import dotagent.agent.lessons as lessons_module
from dotagent.agent.learning_health import SKIPPED
from dotagent.agent.lessons import (
    Disliked,
    DrawnLessons,
    LessonCase,
    disliked_of,
    learn,
    may_learn,
    may_learn_from_dislike,
    run_signals,
    stored_signals,
)
from dotagent.store.db import Store
from dotagent.store.models import ReactionName, RunRecord

_ANSWER: str = "answer"


def _case(run_id: str) -> LessonCase:
    return LessonCase(
        run_id=run_id,
        thread_id="thread-1",
        model="ollama:scripted",
        goal="My reaction 👎 to your answer above: it is not right.",
        digest="",
        failures=(),
        resolved=(),
        answer="Done, the other way.",
        tainted=False,
        reached_goal=True,
    )


def test_a_run_that_follows_up_a_dislike_has_its_signal_and_no_other_reaction_gives_one() -> None:
    wrong: tuple[str, ...] = run_signals(_case("run-2"), [], [], [], ["wrong"])
    again: tuple[str, ...] = run_signals(_case("run-2"), [], [], [], ["again"])
    assert len(wrong) == 1 and "👎" in wrong[0] and "from the start next time" in wrong[0]
    assert len(again) == 1 and "🔁" in again[0]
    assert run_signals(_case("run-2"), [], [], [], ["explain"]) == ()
    assert run_signals(_case("run-2"), [], [], [], ["wrong", "wrong"]) == wrong
    assert run_signals(_case("run-2"), [], [], []) == ()


async def _run(store: Store, thread_id: str, goal: str) -> RunRecord:
    return await store.create_run(
        thread_id, goal, "task", "ollama:qwen3", None, "ui", "medium", "standard", None, "ask", False, None, None, None
    )


async def test_the_follow_up_of_a_dislike_learns_and_names_the_notes_the_disliked_run_was_handed(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Report", "ui")
        disliked: RunRecord = await _run(store, chat.id, "Write the weekly report")
        follow_up: RunRecord = await _run(store, chat.id, "My reaction 👎 to your answer above: it is not right.")
        other: RunRecord = await _run(store, chat.id, "Something else")
        kept = await store.add_memory(
            "Write the weekly report as one long paragraph.", ["lesson"], None, None, None, False
        )
        trashed = await store.add_memory("Put the figures last.", ["lesson"], None, None, None, False)
        now: float = time.time()
        await store.mark_memories_used(disliked.id, [kept.id, trashed.id], now, "start")
        await store.trash_memory(trashed.id, now)
        reaction: ReactionName = "wrong"
        assert await store.put_reaction(disliked.id, _ANSWER, reaction, chat.id, now) is True
        await store.link_follow_up(disliked.id, _ANSWER, reaction, follow_up.id)

        assert await store.followed_reactions(follow_up.id) == [(disliked.id, _ANSWER, reaction)]
        signals: tuple[str, ...] = await stored_signals(store, _case(follow_up.id))
        assert len(signals) == 1 and "👎" in signals[0]
        named: Disliked | None = await disliked_of(store, follow_up.id)
        assert named is not None and named.run_id == disliked.id and named.reaction == reaction
        # A note forgotten since is not named again; one still kept is.
        assert [note.id for note in named.notes] == [kept.id]
        assert named.notes[0].text == kept.content and named.notes[0].pinned is False

        # Any other run of the chat is no follow-up of a dislike.
        assert await stored_signals(store, _case(other.id)) == ()
        assert await disliked_of(store, other.id) is None

        # Taken back, the dislike teaches nothing and names nothing.
        await store.take_reaction(disliked.id, _ANSWER, reaction, now + 1.0)
        assert await stored_signals(store, _case(follow_up.id)) == ()
        assert await disliked_of(store, follow_up.id) is None
    finally:
        await store.close()


def test_a_chat_that_ended_done_may_learn_from_a_dislike_and_from_nothing_else() -> None:
    """Most of the owner's runs are chats, which drew no lesson: the follow-up of a 👎 there taught nothing. A chat of
    its own that ended done is asked, and only when it follows up a dislike (``learn`` reads the reactions)."""
    assert may_learn_from_dislike("auto", "chat", None, "done") is True
    assert may_learn_from_dislike("propose", "chat", None, "done") is True
    assert may_learn_from_dislike("off", "chat", None, "done") is False
    assert may_learn_from_dislike("auto", "chat", "parent-run", "done") is False
    assert may_learn_from_dislike("auto", "chat", None, "failed") is False
    assert may_learn_from_dislike("auto", "task", None, "done") is False
    assert may_learn("auto", "chat", None, "done") is False


async def test_a_chat_is_asked_for_its_lessons_only_when_it_follows_up_a_dislike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate of ``learn``: a chat's case (``only_if_disliked``) that follows up no 👎 or 🔁 is skipped before the
    review model is asked; one that follows up a 👎 is drawn from, its dislike among the signals."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    asked: list[tuple[str, ...]] = []

    async def drawn(
        runtime: object, deciding: asyncio.Lock, case: LessonCase, signals: tuple[str, ...]
    ) -> DrawnLessons:
        asked.append(signals)
        return DrawnLessons(lessons=(), error=None)

    monkeypatch.setattr(lessons_module, "learn_from", drawn)
    try:
        chat = await store.create_thread("Report", "ui")
        disliked: RunRecord = await _run(store, chat.id, "Write the weekly report")
        follow_up: RunRecord = await _run(store, chat.id, "My reaction 👎 to your answer above: it is not right.")
        runtime = SimpleNamespace(store=store)
        case: LessonCase = _case(follow_up.id).model_copy(update={"only_if_disliked": True})

        assert await learn(runtime, asyncio.Lock(), case) == SKIPPED  # type: ignore[arg-type]
        assert asked == []

        await store.put_reaction(disliked.id, _ANSWER, "wrong", chat.id, time.time())
        await store.link_follow_up(disliked.id, _ANSWER, "wrong", follow_up.id)
        await learn(runtime, asyncio.Lock(), case)  # type: ignore[arg-type]
        assert len(asked) == 1 and "👎" in asked[0][0]
    finally:
        await store.close()
