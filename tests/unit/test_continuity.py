"""Continuity across compaction and the plan state: pure functions over fixed data."""

import pytest

from dotagent.agent.compact import estimate_tokens, split_for_summary
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
from dotagent.agent.plan import (
    EMPTY_PLAN,
    PlanItem,
    PlanPayload,
    checked_dependencies,
    held_steps,
    open_steps,
    plan_event_data,
    plan_from_event,
    plan_lines,
    readiness_lines,
    ready_steps,
    render_plan,
    rewritten_steps,
    unevidenced_done,
    with_step_status,
)
from dotagent.core.errors import ToolInputError
from dotagent.llm.types import Message, ToolCall, assistant_message, tool_message, user_message
from dotagent.store.models import EventRecord
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


def test_the_check_takes_an_address_cut_short_or_lengthened_as_known_and_another_one_not() -> None:
    page: str = "https://docs.example.com/guides/backups/restore-from-snapshot"
    reference: str = history_text([user_message(GOAL, ()), user_message(f"read {page} and ~/Reports/a.md", ())])
    ledger = new_ledger(GOAL)
    # The model copied the address as a rendering cut it, ellipsis and all, or added a fragment the history lacks.
    cut = check_continuity(SUMMARY + f"\nSee {page[:40]}…", reference, ledger)
    assert cut.ok, cut.unverified
    lengthened = check_continuity(SUMMARY + f"\nSee {page}#step-3", reference, ledger)
    assert lengthened.ok, lengthened.unverified
    # Another page under the same prefix is not this one, and a short known path says nothing about a longer one.
    other = check_continuity(
        SUMMARY + f"\nSee {page.rsplit('/', 1)[0]}/delete-all and ~/Reports/a.md.bak", reference, ledger
    )
    assert other.unverified == ("https://docs.example.com/guides/backups/delete-all", "~/Reports/a.md.bak")


def test_a_continuity_note_pins_the_facts_and_a_new_compaction_reads_only_its_summary() -> None:
    ledger = with_compaction(
        with_actions(new_ledger(GOAL), [(_call("write_file", {"path": "~/Reports/week41.md"}), True, "ok")])
    )
    plan = PlanPayload(
        steps=(PlanItem(text="Write the report", status="done"), PlanItem(text="Mail it", status="in_progress")),
        targets="the team has the report: the mail is in the Sent folder",
    )
    check = check_continuity(SUMMARY + " see https://made.up/x", GOAL, ledger)
    note: str = continuity_note(ledger, plan, 4000, SUMMARY, "written by you, ollama:m", check)
    assert note.startswith(CONTINUITY_MARK)
    assert GOAL in note
    assert "1. [done] Write the report\n2. [in_progress] Mail it\nTargets: the team has the report" in note
    assert "write_file" in note
    assert "https://made.up/x" in note.split("## Consistency check", 1)[1]
    assert summary_part(note) == SUMMARY
    # Text that is no continuity note passes unchanged.
    assert summary_part("plain text") == "plain text"


def test_the_pinned_facts_keep_the_task_whole_and_list_the_newest_actions_that_fit() -> None:
    reads = [(_call("read_file", {"path": f"~/notes/{index}.md"}), True, f"note {index}") for index in range(1, 31)]
    ledger = with_instruction(with_actions(new_ledger(GOAL), reads), "Use Turkish in the mail.")
    roomy: str = render_pinned(ledger, EMPTY_PLAN, 20_000)
    assert '"~/notes/1.md"' in roomy
    assert "- ~/notes/1.md" in roomy
    assert "not listed" not in roomy
    tight: str = render_pinned(ledger, EMPTY_PLAN, 1000)
    assert GOAL in tight
    assert "Use Turkish in the mail." in tight
    assert '"~/notes/30.md"' in tight
    assert '"~/notes/1.md"' not in tight
    assert "earlier ones not listed)" in tight
    # The headings of the two lists come on top of the cap.
    assert len(tight) < 1200
    # A cap the task alone fills keeps the task whole and lists nothing else.
    bare: str = render_pinned(ledger, EMPTY_PLAN, 100)
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
    assert split_for_summary(history, 10_000) == ([], history)
    # Too large for the room left: the oldest exchanges are summarised.
    assert split_for_summary(history, 2500) == (history[:3], history[3:])
    # Not even the newest exchange fits: it is kept all the same.
    assert split_for_summary(history, 0) == (history[:5], history[5:])
    parallel: list[Message] = [*history, *_exchange("d", "e")]
    assert split_for_summary(parallel, 0) == (parallel[:7], parallel[7:])
    # The cut never falls inside a tool exchange: room for a result but not for its call keeps neither.
    assert split_for_summary(history, estimate_tokens(history[4:], ())) == (history[:5], history[5:])
    # Item 155: the room decides, not a number of messages: twenty exchanges that fit are all kept.
    long: list[Message] = [user_message(GOAL, ()), *(message for name in range(20) for message in _exchange(f"{name}"))]
    assert split_for_summary(long, estimate_tokens(long[1:], ())) == (long[:1], long[1:])
    # Without a reply of the model there is nothing to summarise.
    assert split_for_summary(history[:1], 0) == ([], history[:1])


def test_a_reminder_repeats_the_task_the_plan_and_only_the_newest_actions() -> None:
    reads = [(_call("read_file", {"path": f"~/notes/{index}.md"}), True, f"note {index}") for index in range(1, 8)]
    ledger = with_instruction(with_actions(new_ledger(GOAL), reads), "Use Turkish in the mail.")
    plan = PlanPayload(
        steps=(PlanItem(text="Write the report", status="done"), PlanItem(text="Mail it", status="in_progress")),
        targets="",
    )
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
    changed = with_step_status(plan, 1, "done", "cat a.txt printed 3 lines")
    assert [step.status for step in changed] == ["done", "pending"]
    assert open_steps(changed) == (2,)
    assert open_steps(with_step_status(changed, 2, "skipped", "")) == ()
    assert render_plan(changed) == "1. [done] a\n   evidence: cat a.txt printed 3 lines\n2. [pending] b"
    with pytest.raises(ToolInputError):
        with_step_status(plan, 3, "done", "x")
    with pytest.raises(ToolInputError):
        with_step_status((), 1, "done", "x")


def test_a_step_is_done_only_with_evidence_and_a_reopened_step_drops_it() -> None:
    plan = (PlanItem(text="a", status="in_progress"), PlanItem(text="b", status="pending"))
    with pytest.raises(ToolInputError) as caught:
        with_step_status(plan, 1, "done", "")
    assert "step 1 cannot be marked done without evidence" in str(caught.value)
    assert "the tool result that shows it" in str(caught.value)
    done = with_step_status(plan, 1, "done", "wc -l a.txt printed 3")
    assert done[0].evidence == "wc -l a.txt printed 3"
    assert unevidenced_done(done) == 0
    # Failed and skipped steps may carry evidence or not; a step opened again holds nothing shown yet.
    assert with_step_status(done, 1, "failed", "")[0].evidence == ""
    assert with_step_status(done, 1, "in_progress", "")[0] == PlanItem(text="a", status="in_progress", evidence="")
    # A plan stored before evidence existed counts its done steps as unevidenced.
    assert unevidenced_done((PlanItem(text="a", status="done"), PlanItem(text="b", status="done", evidence="e"))) == 1


def test_steps_are_ready_when_what_they_wait_for_is_done_and_held_by_a_failed_step() -> None:
    plan = (
        PlanItem(text="Fetch prices", status="pending"),
        PlanItem(text="Fetch reviews", status="pending"),
        PlanItem(text="Write the report", status="pending", depends_on=(1, 2)),
        PlanItem(text="Mail it", status="pending", depends_on=(3,)),
    )
    assert checked_dependencies(plan) == plan
    assert ready_steps(plan) == (1, 2)
    assert held_steps(plan) == ()
    assert readiness_lines(plan) == "Ready now: steps 1, 2; they are independent, so delegate can run them in one turn."
    fetched = with_step_status(with_step_status(plan, 1, "done", "prices.csv: 40 rows"), 2, "skipped", "")
    assert ready_steps(fetched) == (3,)
    assert readiness_lines(fetched) == "Ready now: step 3."
    failed = with_step_status(plan, 1, "failed", "the site refused")
    assert (ready_steps(failed), held_steps(failed)) == ((2,), (3,))
    assert readiness_lines(failed) == "Ready now: step 2.\nHeld by a failed step: 3."
    assert render_plan(plan[2:3]) == "1. [pending] Write the report (after 1, 2)"
    closed = tuple(step.model_copy(update={"status": "skipped"}) for step in plan)
    assert readiness_lines(closed) == ""


@pytest.mark.parametrize(
    ("depends_on", "message"),
    [
        (((5,), ()), "step 1 depends on step 5, and the plan has steps 1 to 2"),
        (((1,), ()), "step 1 cannot depend on itself"),
        (((2,), (1,)), "step 1 waits, through the steps it depends on, for itself"),
    ],
)
def test_a_dependency_outside_the_plan_on_itself_or_in_a_cycle_is_refused(
    depends_on: tuple[tuple[int, ...], tuple[int, ...]], message: str
) -> None:
    plan = tuple(
        PlanItem(text=text, status="pending", depends_on=wanted) for text, wanted in zip("ab", depends_on, strict=True)
    )
    with pytest.raises(ToolInputError) as caught:
        checked_dependencies(plan)
    assert message in str(caught.value)


def test_a_rewritten_plan_keeps_the_evidence_of_the_steps_it_keeps_and_refuses_done_without_any() -> None:
    previous = (PlanItem(text="Read the data", status="done", evidence="read_file data.csv: 12 rows"),)
    kept = rewritten_steps(
        previous,
        (
            PlanItem(text="read the data ", status="done"),
            PlanItem(text="Sum the rows", status="done", evidence="awk printed 420"),
            PlanItem(text="Mail it", status="pending"),
        ),
    )
    assert [step.evidence for step in kept] == ["read_file data.csv: 12 rows", "awk printed 420", ""]
    with pytest.raises(ToolInputError) as caught:
        rewritten_steps(previous, (PlanItem(text="Something new", status="done"),))
    assert "step 1 cannot be marked done without evidence" in str(caught.value)


def test_the_targets_of_a_plan_are_stored_with_it_and_read_under_its_steps() -> None:
    plan = PlanPayload(steps=(PlanItem(text="a", status="done"),), targets="50 leads in leads.csv, counted with wc -l")
    stored = EventRecord(id=1, run_id="run_1", ts=0.0, kind="plan", data=plan_event_data(plan))
    assert plan_from_event(stored) == plan
    assert plan_lines(plan) == "1. [done] a\nTargets: 50 leads in leads.csv, counted with wc -l"
    assert plan_lines(plan.model_copy(update={"targets": ""})) == "1. [done] a"
    # A plan stored before targets existed has none.
    older = stored.model_copy(update={"data": {"steps": [{"text": "a", "status": "done"}]}})
    assert plan_from_event(older) == PlanPayload(steps=plan.steps, targets="")
    assert (plan_from_event(older).steps[0].evidence, plan_from_event(older).steps[0].depends_on) == ("", ())


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
