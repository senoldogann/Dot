"""How the end of a schedule's run is told (item 461): always every end, never none, and problems an end that needs
the owner, unless it was stopped, and a failure that follows a run that went well (or one whose row is unknown)."""

from pathlib import Path

import pytest

from dotagent.core.types import JsonObject
from dotagent.scheduler.notices import BY_HAND_LEVEL, notify_level, said_nothing_new, tells
from dotagent.store.db import Store
from dotagent.store.models import NotifyLevel, RunRecord, RunStatus
from dotagent.tools.meta import FINISH_TOOL


def test_the_level_is_the_schedules_own_else_the_configured_one_and_a_run_by_hand_is_always_told() -> None:
    assert notify_level(None, "problems") == "problems"
    assert notify_level("never", "always") == "never"
    assert BY_HAND_LEVEL == "always"


@pytest.mark.parametrize(
    ("level", "status", "needs", "failed_in_row", "told"),
    [
        ("always", "done", "", 0, True),
        ("always", "cancelled", "", 0, True),
        ("never", "failed", "Sign in.", 1, False),
        ("problems", "failed", "", 1, True),
        ("problems", "failed", "", 2, False),
        # A run that needs the owner is told even as the second failure of a row.
        ("problems", "failed", "Sign in to the bank.", 2, True),
        ("problems", "failed", "", None, True),
        ("problems", "done", "", 0, False),
        ("problems", "done", "Sign in to the bank.", 0, True),
        ("problems", "done", "   ", 0, False),
        ("problems", "cancelled", "Sign in to the bank.", 0, False),
    ],
)
def test_a_level_tells_the_ends_it_is_for(
    level: NotifyLevel, status: RunStatus, needs: str, failed_in_row: int | None, told: bool
) -> None:
    assert tells(level, status, needs, failed_in_row) is told


def test_at_always_a_done_end_that_found_nothing_new_and_needs_nothing_is_not_told() -> None:
    """Item 880: the owner's client mail ran every two hours and told them seven times that nobody had answered."""
    assert tells("always", "done", "", 0, nothing_new=True) is False
    assert tells("always", "done", "Sign in to the bank.", 0, nothing_new=True) is True
    assert tells("always", "failed", "", 1, nothing_new=True) is True
    assert tells("problems", "done", "Sign in.", 0, nothing_new=True) is True


async def test_nothing_new_is_read_from_the_finish_that_went_through_not_one_refused(tmp_path: Path) -> None:
    """The review of item 880: a finish refused before the run went on and found something says nothing of its end."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    try:
        thread = await store.create_thread("Mail check", "schedule")
        run: RunRecord = await store.create_run(
            thread.id,
            "goal",
            "task",
            "m:m",
            None,
            "schedule",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        quiet: JsonObject = {"answer": "Nothing new.", "success": True, "nothing_new": True}
        await store.add_event(run.id, "tool_call", {"call_id": "a", "tool": FINISH_TOOL, "args": quiet})
        await store.add_event(run.id, "tool_result", {"call_id": "a", "tool": FINISH_TOOL, "ok": False, "text": "no"})
        assert await said_nothing_new(store, run.id) is False
        await store.add_event(run.id, "tool_call", {"call_id": "b", "tool": FINISH_TOOL, "args": quiet})
        await store.add_event(run.id, "tool_result", {"call_id": "b", "tool": FINISH_TOOL, "ok": True, "text": "ok"})
        assert await said_nothing_new(store, run.id) is True
        # A provider may repeat a call's id across turns: a call is matched to the first result after it alone.
        again = await store.create_run(
            thread.id,
            "goal",
            "task",
            "m:m",
            None,
            "schedule",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        found: JsonObject = {"answer": "A reply came.", "success": True}
        await store.add_event(again.id, "tool_call", {"call_id": "c", "tool": FINISH_TOOL, "args": found})
        await store.add_event(again.id, "tool_result", {"call_id": "c", "tool": FINISH_TOOL, "ok": True, "text": "ok"})
        await store.add_event(again.id, "tool_call", {"call_id": "c", "tool": FINISH_TOOL, "args": quiet})
        await store.add_event(again.id, "tool_result", {"call_id": "c", "tool": FINISH_TOOL, "ok": False, "text": "no"})
        assert await said_nothing_new(store, again.id) is False
        # A run that read a mail or a page says nothing new in vain: that mail may have told it to (the pre-push review
        # of item 880); its end is told as any other.
        read = await store.create_run(
            thread.id,
            "goal",
            "task",
            "m:m",
            None,
            "schedule",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        await store.add_event(
            read.id, "tool_result", {"call_id": "m", "tool": "mail_read", "ok": True, "tainted": True}
        )
        await store.add_event(read.id, "tool_call", {"call_id": "d", "tool": FINISH_TOOL, "args": quiet})
        await store.add_event(read.id, "tool_result", {"call_id": "d", "tool": FINISH_TOOL, "ok": True, "text": "ok"})
        assert await said_nothing_new(store, read.id) is False
        assert await store.run_ever_tainted(read.id) and not await store.run_ever_tainted(run.id)
    finally:
        await store.close()
