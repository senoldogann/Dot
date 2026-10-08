"""Continuity across compaction and the plan state: pure functions over fixed data."""

import pytest

from dotagent.agent.continuity import (
    CONTINUITY_MARK,
    check_continuity,
    continuity_note,
    history_text,
    new_ledger,
    summary_part,
    with_actions,
    with_compaction,
    with_instruction,
)
from dotagent.agent.executor import _group_calls
from dotagent.agent.plan import PlanItem, open_steps, render_plan, with_step_status
from dotagent.core.errors import ToolInputError
from dotagent.llm.types import Message, ToolCall, user_message
from dotagent.tools.meta import build_meta_tools
from dotagent.tools.registry import build_registry

GOAL: str = "Write the weekly report to ~/Reports/week41.md and mail it to the team."
SUMMARY: str = (
    "GOAL: weekly report.\nDONE: wrote ~/Reports/week41.md.\nSTATE: draft saved.\nPROBLEMS: none.\nNEXT: send the mail."
)


def _call(name: str, arguments: dict[str, str]) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=dict(arguments))


def test_the_ledger_keeps_work_actions_touched_paths_and_instructions() -> None:
    ledger = with_actions(
        new_ledger(GOAL),
        [
            (_call("write_file", {"path": "~/Reports/week41.md", "content": "x"}), True, "Wrote 1 line."),
            (_call("update_plan", {"steps": "[]"}), True, "Plan recorded"),
            (_call("web_fetch", {"url": "https://example.com/a"}), False, "HTTP 404"),
        ],
    )
    ledger = with_instruction(ledger, "Use Turkish in the mail.")
    assert [action.tool for action in ledger.actions] == ["write_file", "web_fetch"]
    assert ledger.action_count == 2
    # A failed call touched nothing.
    assert ledger.touched == ("~/Reports/week41.md",)
    assert ledger.instructions == ("Use Turkish in the mail.",)


def test_the_check_flags_identifiers_the_history_never_mentioned() -> None:
    reference: str = history_text([user_message(GOAL, ()), user_message("wrote ~/Reports/week41.md", ())])
    ledger = new_ledger(GOAL)
    clean = check_continuity(SUMMARY, reference, ledger)
    assert clean.ok
    invented = check_continuity(SUMMARY + "\nAlso uploaded https://files.example.com/report.pdf", reference, ledger)
    assert not invented.ok
    assert invented.unverified == ("https://files.example.com/report.pdf",)
    missing = check_continuity("GOAL: report. DONE: wrote it.", reference, ledger)
    assert missing.missing_sections == ("STATE", "PROBLEMS", "NEXT")


def test_a_continuity_note_pins_the_facts_and_a_new_compaction_reads_only_its_summary() -> None:
    ledger = with_compaction(
        with_actions(new_ledger(GOAL), [(_call("write_file", {"path": "~/Reports/week41.md"}), True, "ok")])
    )
    plan = (PlanItem(text="Write the report", status="done"), PlanItem(text="Mail it", status="in_progress"))
    check = check_continuity(SUMMARY + " see https://made.up/x", GOAL, ledger)
    note: str = continuity_note(ledger, plan, SUMMARY, "written by you, ollama:m", check)
    assert note.startswith(CONTINUITY_MARK)
    assert GOAL in note
    assert "1. [done] Write the report" in note
    assert "write_file" in note
    assert "https://made.up/x" in note.split("## Consistency check", 1)[1]
    assert summary_part(note) == SUMMARY
    # Text that is no continuity note passes unchanged.
    assert summary_part("plain text") == "plain text"


def test_plan_steps_change_one_at_a_time_and_report_what_is_open() -> None:
    plan = (PlanItem(text="a", status="pending"), PlanItem(text="b", status="pending"))
    changed = with_step_status(plan, 1, "done")
    assert [step.status for step in changed] == ["done", "pending"]
    assert open_steps(changed) == (2,)
    assert open_steps(with_step_status(changed, 2, "skipped")) == ()
    assert render_plan(changed) == "1. [done] a\n2. [pending] b"
    with pytest.raises(ToolInputError):
        with_step_status(plan, 3, "done")
    with pytest.raises(ToolInputError):
        with_step_status((), 1, "done")


def test_the_plan_changes_of_one_reply_apply_one_after_another() -> None:
    # Each change rewrites the latest plan; run side by side, the second would drop the first.
    calls = [
        ToolCall(id="c1", name="mark_plan_step", arguments={"step": 1, "status": "done"}),
        ToolCall(id="c2", name="mark_plan_step", arguments={"step": 2, "status": "in_progress"}),
        ToolCall(id="c3", name="update_plan", arguments={"steps": []}),
    ]
    groups = _group_calls(calls, build_registry([build_meta_tools()]))
    assert [[call.id for call in group] for group in groups] == [["c1"], ["c2"], ["c3"]]


def test_history_text_holds_tool_arguments() -> None:
    message: Message = user_message("hello", ())
    assert "hello" in history_text([message])
