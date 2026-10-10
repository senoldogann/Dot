"""The Actions panel's pure parts: which tool results are acting steps, and how the undo plan reads as file edits."""

from pathlib import Path

from dotagent.server.actions import action_steps, file_edits
from dotagent.store.models import EventRecord
from dotagent.undo.journal import JournalEntry
from dotagent.undo.restore import RunChange, UndoStep


def _event(event_id: int, kind: str, data: dict[str, object]) -> EventRecord:
    return EventRecord.model_validate(
        {"id": event_id, "run_id": "run_1", "ts": 1_000.0 + event_id, "kind": kind, "data": data}
    )


def _result(event_id: int, tool: str, kind: str, risk: str, target: str, ok: bool) -> EventRecord:
    return _event(
        event_id,
        "tool_result",
        {
            "tool": tool,
            "ok": ok,
            "text": "written" if ok else "no such file",
            "summary": f"{tool} on {target}" if target else "",
            "kind": kind,
            "risk": risk,
            "target": target,
            "site": "example.com" if tool == "browser_act" else "",
            "channel": "browser" if tool == "browser_act" else "command",
        },
    )


def test_only_the_tool_results_of_calls_that_changed_something_are_acting_steps() -> None:
    events: list[EventRecord] = [
        _event(1, "tool_call", {"tool": "read_file"}),
        _result(2, "read_file", "read", "safe", "/w/a.txt", True),
        _result(3, "write_file", "write", "low", "/w/a.txt", True),
        _result(4, "browser_act", "browser_act", "medium", "https://example.com/post", False),
        # Recorded before the event carried the kind: the risk tells.
        _event(5, "tool_result", {"tool": "shell", "ok": True, "text": "done", "risk": "medium", "channel": "command"}),
        _event(6, "tool_result", {"tool": "nope", "ok": False, "text": "Unknown tool"}),
    ]
    steps = action_steps(events)
    assert [(step.event_id, step.tool, step.channel, step.target, step.ok) for step in steps] == [
        (3, "write_file", "command", "/w/a.txt", True),
        (4, "browser_act", "browser", "https://example.com/post", False),
        (5, "shell", "command", "", True),
    ]
    assert steps[1].evidence == "no such file"


def test_the_undo_plan_reads_as_file_edits_with_the_first_change_and_the_count() -> None:
    entry: JournalEntry = JournalEntry(
        seq=1,
        ts=1_500.0,
        tool="edit_file",
        path="/w/a.txt",
        existed=True,
        mode=0o644,
        backup=True,
        before="abc",
        copied=3,
        skipped=None,
        created_dirs=(),
        after="def",
        size=3,
    )
    step: UndoStep = UndoStep(
        path="/w/a.txt",
        state="conflict",
        action="restore",
        reason="it changed after the run's last change to it",
        origin=RunChange(run_id="run_2", entry=entry),
        changes=2,
    )
    (edit,) = file_edits([step])
    assert (edit.path, edit.state, edit.action, edit.tool, edit.run_id, edit.noted_at, edit.changes) == (
        "/w/a.txt",
        "conflict",
        "restore",
        "edit_file",
        "run_2",
        1_500.0,
        2,
    )
    assert edit.reason is not None and Path(edit.path).name == "a.txt"
