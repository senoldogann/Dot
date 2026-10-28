"""A failed run becomes a draft eval task, with its goal and failed tools and without its values (item 799)."""

from __future__ import annotations

from pathlib import Path

from dotagent.evals.drafts import draft_from_run, save_draft
from dotagent.store.models import EventRecord, RunRecord


def _run(**changes: object) -> RunRecord:
    fields: dict[str, object] = {
        "id": "run-799",
        "thread_id": "thread-799",
        "goal": "Fix the broken page at the shop",
        "mode": "task",
        "model": "ollama:a",
        "status": "failed",
        "created_at": 1_700_000_000.0,
        "finished_at": 1_700_000_060.0,
        "result": "The page still did not load.",
        "steps": 4,
        "input_tokens": 120,
        "output_tokens": 30,
        "parent_run_id": None,
        "source": "test",
        "effort": None,
        "speed": "standard",
        "once": None,
        "approvals": "ask",
        "goal_mode": False,
        "campaign_id": None,
        "agent_id": None,
        "rewindable": False,
    }
    fields.update(changes)
    return RunRecord.model_validate(fields)


def _event(event_id: int, kind: str, **data: object) -> EventRecord:
    return EventRecord(id=event_id, run_id="run-799", ts=1_700_000_000.0, kind=kind, data=data)


def test_a_draft_names_the_goal_and_failed_tools_without_their_arguments() -> None:
    text = draft_from_run(
        _run(),
        [
            _event(1, "tool_call", name="browser_open", args={"text": "hunter2"}),
            _event(2, "tool_result", tool="browser_open", ok=False),
            _event(3, "tool_result", tool="browser_open", ok=True),
        ],
    )
    assert "Fix the broken page at the shop" in text
    assert "browser_open" in text
    assert "`browser_open` failed" in text
    assert "hunter2" not in text
    assert "`failed`" in text
    assert "The page still did not load." in text


def test_a_draft_without_events_still_says_what_to_do() -> None:
    text = draft_from_run(_run(result=""), [])
    assert "no tool call was recorded" in text
    assert "(no result was written)" in text
    assert "dotx eval check" in text


def test_the_draft_is_written_where_it_is_asked(tmp_path: Path) -> None:
    text: str = draft_from_run(_run(), [])
    path: Path = save_draft(text, "run-799", tmp_path / "drafts" / "run-799.md")
    assert path.read_text(encoding="utf-8") == text
