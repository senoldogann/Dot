"""Item 1865: a run holds at most ``MOST_HELD`` notes and says so of the next one; what waits carries the run's mark."""

import time
from pathlib import Path

from dotagent.agent.held_notes import WAITING_RUNS, WaitingProposal, waiting_proposals
from dotagent.events import EventBus
from dotagent.memory.held import MOST_HELD, HeldNote, hold_note
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import RunRecord


async def test_a_run_past_the_most_notes_keeps_none_and_its_held_notes_carry_its_mark(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        thread = await store.create_thread("t", SCHEDULE_SOURCE)
        run: RunRecord = await store.create_run(
            thread.id,
            "g",
            "task",
            "ollama:m",
            None,
            SCHEDULE_SOURCE,
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        for number in range(MOST_HELD):
            held: HeldNote | None = await hold_note(bus, store, run.id, f"Note {number}.", (), None, None, True)
            assert held is not None and held.index == number
        assert await hold_note(bus, store, run.id, "One more note.", (), None, None, True) is None
        # The same words again are the note held already, even past the most.
        again: HeldNote | None = await hold_note(bus, store, run.id, "Note 3.", (), None, None, True)
        assert again is not None and again.index == 3
        waiting: list[WaitingProposal] = await waiting_proposals(store, time.time())
        assert len(waiting) == MOST_HELD
        assert all(item.tainted and item.origin == "schedule" and item.kind == "note" for item in waiting)
    finally:
        await store.close()


async def test_runs_whose_lessons_were_all_decided_take_no_place_on_the_list(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        thread = await store.create_thread("t", SCHEDULE_SOURCE)
        scheduled: RunRecord = await store.create_run(
            thread.id,
            "g",
            "task",
            "ollama:m",
            None,
            SCHEDULE_SOURCE,
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        assert await hold_note(bus, store, scheduled.id, "The landlord answers on weekdays.", (), None, None, False)
        # More task runs than the list's runs, each drawing a lesson that auto saved at once, after it.
        for number in range(WAITING_RUNS + 5):
            chat = await store.create_thread(f"c{number}", "ui")
            run: RunRecord = await store.create_run(
                chat.id,
                "g",
                "task",
                "ollama:m",
                None,
                "ui",
                "medium",
                "standard",
                None,
                "ask",
                False,
                None,
                None,
                None,
            )
            await store.add_event(run.id, "lessons_proposed", {"lessons": [f"Lesson {number}."], "tainted": False})
            await store.add_event(run.id, "lesson_saved", {"index": 0, "memory_id": f"mem_{number}", "by": "auto"})
        waiting: list[WaitingProposal] = await waiting_proposals(store, time.time())
        assert [(item.run_id, item.kind) for item in waiting] == [(scheduled.id, "note")]
    finally:
        await store.close()
