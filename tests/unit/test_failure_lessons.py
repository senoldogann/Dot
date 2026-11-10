"""Lessons handed at the moment a tool fails (item 1111)."""

from dotagent.agent.failure_lessons import LESSONS_AT_FAILURE, failure_note, lessons_for_tool, names_tool
from dotagent.store.models import MemoryRecord

_NOW: float = 1_000.0


def _note(note_id: str, content: str, used: int = 0, **changes: object) -> MemoryRecord:
    note = MemoryRecord(
        id=note_id, content=content, tags=("lesson",), created_at=1.0, updated_at=float(used), source_run=None,
        agent_id=None, used_count=used, last_used_at=None, expires_at=None, replaced_by=None, tainted=False,
    )  # fmt: skip
    return note.model_copy(update=changes)


def test_a_tool_is_named_as_a_word() -> None:
    assert names_tool("Give `browser_act` an action from its list.", "browser_act")
    assert not names_tool("browser_actions are listed elsewhere", "browser_act")


def test_the_lessons_of_a_failed_tool_are_those_that_name_it_hold_and_were_not_handed_the_most_used_first() -> None:
    notes = [
        _note("a", "`read_file` takes a path relative to the workspace.", used=1),
        _note("b", "Give read_file a path that exists; list_dir first.", used=5),
        _note("c", "`read_file` reads PDFs too.", used=9),
        _note("other", "`browser_act` takes an action from its list.", used=50),
        _note("handed", "`read_file` once more.", used=40),
        _note("tainted", "`read_file` from a page.", used=40, tainted=True),
        _note("replaced", "`read_file` old.", used=40, replaced_by="b"),
        _note("rested", "`read_file` resting.", used=40, rested_at=2.0),
        _note("expired", "`read_file` for a while.", used=40, expires_at=_NOW),
        _note("agents", "`read_file` of another agent.", used=40, agent_id="agt_other"),
        _note("plain", "`read_file` not a lesson.", used=40).model_copy(update={"tags": ("rest",)}),
    ]
    chosen = lessons_for_tool("read_file", notes, {"handed"}, None, False, _NOW)
    assert [note.id for note in chosen] == ["c", "b"]
    assert len(chosen) == LESSONS_AT_FAILURE
    # The run's own agent's lesson may be handed to it.
    assert "agents" in [note.id for note in lessons_for_tool("read_file", notes, {"handed"}, "agt_other", False, _NOW)]
    # A run that read outside content itself may be handed a lesson written after such content.
    assert "tainted" in [note.id for note in lessons_for_tool("read_file", notes, {"handed"}, None, True, _NOW)]
    assert lessons_for_tool("web_fetch", notes, set(), None, False, _NOW) == []
    assert failure_note("read_file", chosen[:1]) == (
        "What earlier runs learned about read_file, from Dot's memory:\n- `read_file` reads PDFs too."
    )
