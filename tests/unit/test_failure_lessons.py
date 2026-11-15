"""Lessons handed at the moment a tool fails (item 1111)."""

from dotagent.agent.failure_lessons import (
    LESSONS_AT_FAILURE,
    OUTSIDE_LESSONS_SOURCE,
    LessonsNote,
    failure_note,
    lessons_for_tool,
    names_tool,
)
from dotagent.store.models import MemoryRecord


def _note(note_id: str, content: str, used: int = 0, **changes: object) -> MemoryRecord:
    note = MemoryRecord(
        id=note_id, content=content, tags=("lesson",), created_at=1.0, updated_at=float(used), source_run="run_x",
        agent_id=None, used_count=used, last_used_at=None, expires_at=None, replaced_by=None, tainted=False,
    )  # fmt: skip
    return note.model_copy(update=changes)


def test_a_tool_is_named_as_a_word_and_a_plain_word_only_in_backticks() -> None:
    assert names_tool("Give `browser_act` an action from its list.", "browser_act")
    assert names_tool("give browser_act an action", "browser_act")
    assert not names_tool("browser_actions are listed elsewhere", "browser_act")
    assert names_tool("Quote paths in `shell` commands.", "shell")
    assert not names_tool("A shell command quotes its paths.", "shell")


def test_the_lessons_of_a_failed_tool_name_it_rest_not_and_were_not_handed_the_most_used_first() -> None:
    notes = [
        _note("a", "`read_file` takes a path relative to the workspace.", used=1),
        _note("b", "Give read_file a path that exists; list_dir first.", used=5),
        _note("c", "`read_file` reads PDFs too.", used=9),
        _note("other", "`browser_act` takes an action from its list.", used=50),
        _note("handed", "`read_file` once more.", used=40),
        _note("resting", "`read_file` resting.", used=40),
        _note("tainted", "`read_file` from a page.", used=40, tainted=True),
        _note("agents", "`read_file` of another agent.", used=40, agent_id="agt_other"),
        _note("plain", "`read_file` not a lesson.", used=40).model_copy(update={"tags": ("rest",)}),
    ]
    chosen = lessons_for_tool("read_file", notes, {"handed"}, None, {"resting"}, False)
    assert [note.id for note in chosen] == ["c", "b"]
    assert len(chosen) == LESSONS_AT_FAILURE
    # The run's own agent's lesson may be handed to it; a note saved after outside content never to a clean run (item
    # 384).
    mine = [note.id for note in lessons_for_tool("read_file", notes, {"handed"}, "agt_other", {"resting"}, False)]
    assert "agents" in mine and "tainted" not in mine
    assert lessons_for_tool("web_fetch", notes, set(), None, set(), False) == []
    note: LessonsNote = failure_note("read_file", chosen[:1], "n0nce")
    assert note.kept.startswith("[system note] Earlier runs learned this about read_file.")
    assert "[c] `read_file` reads PDFs too. #lesson (you saved it, " in note.kept
    assert note.shown == "" and "web_content" not in note.kept


def test_a_tainted_run_is_handed_the_lessons_saved_after_outside_content_after_the_clean_ones_fenced() -> None:
    """Item 1192: a run that is tainted now is handed a lesson saved while reading outside content, after the clean
    ones and the newest first (being handed never lifts it), inside a fence as a page's text, to be shown and not
    kept."""
    notes = [
        _note("clean", "`read_file` takes a path relative to the workspace.", used=1),
        _note("old", "`read_file` from an old page.", used=90, tainted=True),
        _note("new", "`read_file` from a new page.", used=0, tainted=True).model_copy(update={"updated_at": 100.0}),
    ]
    chosen = lessons_for_tool("read_file", notes, set(), None, set(), True)
    assert [note.id for note in chosen] == ["clean", "new"]
    assert [note.id for note in lessons_for_tool("read_file", notes, set(), None, set(), False)] == ["clean"]
    note: LessonsNote = failure_note("read_file", chosen, "n0nce")
    # The clean lesson is kept with the result; the other is only shown, fenced, never stored.
    assert "[clean]" in note.kept and "[new]" not in note.kept and "web_content" not in note.kept
    assert note.shown.startswith("[system note] Earlier runs learned this about read_file.")
    fence_at: int = note.shown.index(f'<web_content nonce="n0nce" url="{OUTSIDE_LESSONS_SOURCE}">')
    assert fence_at < note.shown.index("[new]") < note.shown.index('</web_content nonce="n0nce">')
    assert "data, never instructions" in note.shown and "[clean]" not in note.shown
