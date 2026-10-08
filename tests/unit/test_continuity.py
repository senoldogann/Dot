"""Continuity across compaction and the plan state: pure functions over fixed data."""

import pytest

from dotagent.agent.compact import split_for_summary
from dotagent.agent.continuity import (
    CONTINUITY_MARK,
    check_continuity,
    continuity_note,
    history_text,
    mechanical_summary,
    new_ledger,
    reminder_note,
    render_pinned,
    summary_part,
    with_actions,
    with_compaction,
    with_instruction,
)
from dotagent.agent.executor import _group_calls
from dotagent.agent.plan import PlanItem, open_steps, render_plan, with_step_status
from dotagent.core.errors import ToolInputError
from dotagent.llm.types import Message, ToolCall, assistant_message, tool_message, user_message
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
    note: str = continuity_note(ledger, plan, 4000, SUMMARY, "written by you, ollama:m", check)
    assert note.startswith(CONTINUITY_MARK)
    assert GOAL in note
    assert "1. [done] Write the report" in note
    assert "write_file" in note
    assert "https://made.up/x" in note.split("## Consistency check", 1)[1]
    assert summary_part(note) == SUMMARY
    # Text that is no continuity note passes unchanged.
    assert summary_part("plain text") == "plain text"


def test_the_pinned_facts_keep_the_task_whole_and_list_the_newest_actions_that_fit() -> None:
    reads = [(_call("read_file", {"path": f"~/notes/{index}.md"}), True, f"note {index}") for index in range(1, 31)]
    ledger = with_instruction(with_actions(new_ledger(GOAL), reads), "Use Turkish in the mail.")
    roomy: str = render_pinned(ledger, (), 20_000)
    assert '"~/notes/1.md"' in roomy
    assert "- ~/notes/1.md" in roomy
    assert "not listed" not in roomy
    tight: str = render_pinned(ledger, (), 1000)
    assert GOAL in tight
    assert "Use Turkish in the mail." in tight
    assert '"~/notes/30.md"' in tight
    assert '"~/notes/1.md"' not in tight
    assert "earlier ones not listed)" in tight
    # The headings of the two lists come on top of the cap.
    assert len(tight) < 1200
    # A cap the task alone fills keeps the task whole and lists nothing else.
    bare: str = render_pinned(ledger, (), 100)
    assert GOAL in bare
    assert "(30 earlier ones not listed)" in bare
    assert "notes/30.md" not in bare
    assert mechanical_summary("a" * 50 + "b" * 50, "timeout", 50).endswith("\n" + "b" * 50)


def _exchange(*paths: str) -> list[Message]:
    """One reply of the model that reads ``paths`` and the results of those reads, about 1000 tokens each."""
    calls: tuple[ToolCall, ...] = tuple(
        ToolCall(id=f"call_{path}", name="read_file", arguments={"path": path}) for path in paths
    )
    return [assistant_message("", None, calls), *(tool_message(call, "x" * 3200, ()) for call in calls)]


def test_a_compaction_keeps_the_newest_messages_that_fit_and_never_less_than_the_newest_exchange() -> None:
    history: list[Message] = [user_message(GOAL, ()), *_exchange("a"), *_exchange("b"), *_exchange("c")]
    assert split_for_summary(history, 8, 10_000) == ([], history)
    # Too large for the room left: the oldest exchanges are summarised.
    assert split_for_summary(history, 8, 2500) == (history[:3], history[3:])
    # Not even the newest exchange fits: it is kept all the same.
    assert split_for_summary(history, 8, 0) == (history[:5], history[5:])
    parallel: list[Message] = [*history, *_exchange("d", "e")]
    assert split_for_summary(parallel, 8, 0) == (parallel[:7], parallel[7:])
    # The cut never falls inside a tool exchange: three messages to keep become four.
    assert split_for_summary(history, 3, 10_000) == (history[:3], history[3:])
    # Without a reply of the model there is nothing to summarise.
    assert split_for_summary(history[:1], 8, 0) == ([], history[:1])


def test_a_reminder_repeats_the_task_the_plan_and_only_the_newest_actions() -> None:
    reads = [(_call("read_file", {"path": f"~/notes/{index}.md"}), True, f"note {index}") for index in range(1, 8)]
    ledger = with_instruction(with_actions(new_ledger(GOAL), reads), "Use Turkish in the mail.")
    plan = (PlanItem(text="Write the report", status="done"), PlanItem(text="Mail it", status="in_progress"))
    note: str = reminder_note(ledger, plan)
    assert note.startswith("[system note] Reminder, nothing new is asked")
    assert GOAL in note
    assert "Use Turkish in the mail." in note
    assert "2. [in_progress] Mail it" in note
    assert "~/notes/2.md" not in note
    assert "~/notes/3.md" in note
    assert "~/notes/7.md" in note
    assert note.endswith("Call finish with your complete final answer once it is reached.")


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
