"""Resuming a run that the daemon cut off: the calls of the step it was in, which runs may be resumed by hand and which
the daemon resumes on its own, and what the resumed run is told."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from dotagent.agent.compact import pending_calls
from dotagent.agent.continuity import continuity_note, named_ids, new_ledger, with_actions
from dotagent.agent.plan import EMPTY_PLAN, PlanItem, PlanPayload
from dotagent.agent.prompt import turn_note
from dotagent.agent.resume import (
    OUTWARD_FLAGS,
    REPEATABLE_META_TOOLS,
    RESUME_SOURCE,
    SENT_OUT_FLAGS,
    CallEnd,
    InterruptedCall,
    Resumption,
    _action_of,
    can_resume,
    cut_by_restart,
    cut_off_by,
    handoff_of,
    held_back,
    helper_not_resumed,
    identities_of,
    resume_note,
    resumed_event_data,
    resumes_on_start,
    stopped_line,
)
from dotagent.config import load_default_config
from dotagent.llm.types import Message, ToolCall, assistant_message, tool_message, user_message
from dotagent.policy import engine
from dotagent.policy.types import ActionKind
from dotagent.process_groups import GroupRecord
from dotagent.runtime import _tool_groups
from dotagent.scheduler import SCHEDULE_SOURCE, recovery
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_RESTARTED, RESULT_SHUTDOWN, RESULT_STOPPED
from dotagent.store.models import RunRecord, RunStatus
from dotagent.tools.context import ToolContext

GOAL: str = "Write the report from data.csv."
READ: ToolCall = ToolCall(id="call_1", name="read_file", arguments={"path": "data.csv"})
WRITE: ToolCall = ToolCall(id="call_2", name="write_file", arguments={"path": "report.md", "content": "# Report"})


def _run(status: RunStatus, result: str, source: str, parent_run_id: str | None) -> RunRecord:
    return RunRecord(
        id="run_1",
        thread_id="thread_1",
        goal=GOAL,
        mode="task",
        model="ollama:gpt-oss:20b-cloud",
        status=status,
        created_at=1.0,
        finished_at=2.0,
        result=result,
        steps=3,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=parent_run_id,
        source=source,
        effort=None,
        speed="standard",
        once=None,
        approvals="yolo",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
        rewindable=False,
    )


def _call(tool: str, kind: ActionKind | None, end: CallEnd, result: str) -> InterruptedCall:
    return InterruptedCall(tool=tool, arguments="{}", kind=kind, end=end, result=result)


RESTARTED: RunRecord = _run("failed", RESULT_RESTARTED, UI_SOURCE, None)
SHUT_DOWN: RunRecord = _run("cancelled", RESULT_SHUTDOWN, UI_SOURCE, None)


def test_the_pending_calls_are_the_calls_of_the_newest_step_that_have_no_result() -> None:
    goal: Message = user_message(GOAL, ())
    step: Message = assistant_message("", None, (READ, WRITE))
    assert pending_calls([goal, step]) == (READ, WRITE)
    assert pending_calls([goal, step, tool_message(READ, "a,b", ())]) == (WRITE,)
    assert pending_calls([goal, step, tool_message(READ, "a,b", ()), tool_message(WRITE, "ok", ())]) == ()
    # A message of the user after the step: the run went on from there.
    assert pending_calls([goal, step, user_message("Go on.", ())]) == ()
    assert pending_calls([goal, assistant_message("Done.", None, ())]) == ()
    assert pending_calls([goal]) == ()
    assert pending_calls([]) == ()


def test_only_the_newest_run_of_a_thread_that_the_daemon_cut_off_can_be_resumed() -> None:
    assert cut_off_by(RESTARTED) == "daemon_restarted"
    assert cut_off_by(SHUT_DOWN) == "daemon_shutdown"
    assert cut_off_by(_run("cancelled", RESULT_STOPPED, UI_SOURCE, None)) is None
    assert cut_off_by(_run("done", "Done.", UI_SOURCE, None)) is None
    assert cut_off_by(_run("running", "", UI_SOURCE, None)) is None
    assert can_resume(RESTARTED, "run_1")
    assert can_resume(SHUT_DOWN, "run_1")
    # A blocked run and one that failed can be resumed too. The thread went on after it, another run started it, or
    # the owner stopped it: those cannot. A campaign session keeps its own follow-up. A scheduled firing can be
    # resumed by hand when it was cut off, and a blocked or failed firing cannot; the daemon does not resume either.
    blocked: RunRecord = _run("blocked", "", UI_SOURCE, None)
    failed: RunRecord = _run("failed", "the report was not written", UI_SOURCE, None)
    assert handoff_of(blocked) == "blocked" and can_resume(blocked, "run_1")
    assert handoff_of(failed) == "failed" and can_resume(failed, "run_1")
    assert not resumes_on_start(failed, "run_1", [])
    assert not can_resume(RESTARTED, "run_2")
    assert not can_resume(_run("failed", RESULT_RESTARTED, UI_SOURCE, "run_0"), "run_1")
    assert not can_resume(_run("cancelled", RESULT_STOPPED, UI_SOURCE, None), "run_1")
    campaign: RunRecord = _run("failed", "no", UI_SOURCE, None).model_copy(update={"campaign_id": "cmp_1"})
    scheduled_failed: RunRecord = _run("failed", "no", SCHEDULE_SOURCE, None)
    scheduled_cut: RunRecord = _run("failed", RESULT_RESTARTED, SCHEDULE_SOURCE, None)
    assert handoff_of(campaign) is None and not can_resume(campaign, "run_1")
    assert handoff_of(scheduled_failed) is None and not can_resume(scheduled_failed, "run_1")
    assert handoff_of(scheduled_cut) == "daemon_restarted" and can_resume(scheduled_cut, "run_1")
    assert not resumes_on_start(scheduled_cut, "run_1", [])


def test_the_daemon_resumes_on_its_own_a_run_of_the_web_ui_unless_a_cut_call_may_have_paid_sent_or_posted() -> None:
    """Item 1854: a cut call that may have changed something is told in the note; only one that may have paid, sent or
    posted for the owner, or whose kind cannot be told, holds the run back for the owner."""
    read: InterruptedCall = _call("read_file", "read", "cut_off", "")
    assert resumes_on_start(RESTARTED, "run_1", [read])
    assert resumes_on_start(SHUT_DOWN, "run_1", [])
    finished_write: InterruptedCall = _call("write_file", "write", "succeeded", "Wrote report.md")
    unstarted_shell: InterruptedCall = _call("shell", "shell", "not_started", "")
    assert resumes_on_start(RESTARTED, "run_1", [finished_write, read, unstarted_shell])
    # A write, a command, a delegation or an agent made, cut while it ran: the run goes on and checks the state.
    for tool, kind in (("write_file", "write"), ("shell", "shell"), ("delegate", "delegate"), ("create_agent", "meta")):
        assert resumes_on_start(RESTARTED, "run_1", [_call(tool, kind, "cut_off", "")]), tool
    # A call that pays, sends or posts was cut: done or not, a second try could do it twice.
    for flag in OUTWARD_FLAGS:
        sent: InterruptedCall = InterruptedCall(
            tool="shell", arguments="{}", kind="shell", end="cut_off", result="", flags=(flag,)
        )
        assert not resumes_on_start(RESTARTED, "run_1", [read, sent]), flag
        # Finished, its result is in the note and nothing is repeated blind.
        assert resumes_on_start(RESTARTED, "run_1", [sent.model_copy(update={"end": "succeeded"})]), flag
    # A call whose kind cannot be told may be one of those.
    assert not resumes_on_start(RESTARTED, "run_1", [_call("desktop_act", None, "cut_off", "")])
    assert not resumes_on_start(RESTARTED, "run_2", [read])
    for source in ("cli", SCHEDULE_SOURCE, RESUME_SOURCE):
        assert not resumes_on_start(_run("failed", RESULT_RESTARTED, source, None), "run_1", [read])
    # The owner's stop is no cut-off: such a run never goes on by itself.
    assert not resumes_on_start(_run("cancelled", RESULT_STOPPED, UI_SOURCE, None), "run_1", [])


def test_a_meta_call_is_told_as_repeatable_only_when_a_second_call_sets_what_the_first_set() -> None:
    """Item 552: the kind ``meta`` is the kind of tools that make an agent, show a notification or a page and ask the
    owner, as well as of the plan and the help; the note tells a cut one as repeatable only when it sets again."""
    resumption: Resumption = Resumption(
        from_run_id="run_1",
        cut_off="daemon_restarted",
        automatic=True,
        calls=(_call("create_agent", "meta", "cut_off", ""), _call("find_help", "meta", "cut_off", "")),
        plan=EMPTY_PLAN,
    )
    note: str = resume_note(GOAL, resumption)
    assert "create_agent {}: it was cut off while it ran and may have changed something" in note
    assert "find_help {}: it was cut off while it ran. It only reads, so it may run again." in note


@pytest.mark.macos
def test_the_meta_tools_named_repeatable_are_tools_that_exist() -> None:
    names: set[str] = {tool.spec.name for group in _tool_groups(load_default_config(), False) for tool in group}
    assert names >= REPEATABLE_META_TOOLS, REPEATABLE_META_TOOLS - names


def test_the_resumed_run_is_told_how_each_call_of_the_step_ended_and_takes_over_the_plan() -> None:
    resumption: Resumption = Resumption(
        from_run_id="run_1",
        cut_off="daemon_restarted",
        automatic=False,
        calls=(
            _call("read_file", "read", "succeeded", "a,b"),
            _call("shell", "shell", "failed", "[exit 1 in 0.1s] no such file"),
            _call("web_fetch", "network", "cut_off", ""),
            _call("write_file", "write", "cut_off", ""),
            _call("move_file", "write", "not_started", ""),
        ),
        plan=PlanPayload(
            steps=(
                PlanItem(text="Read the data", status="done"),
                PlanItem(text="Write the report", status="in_progress"),
            ),
            targets="report.md holds every row of the data",
        ),
    )
    note: str = resume_note(GOAL, resumption)
    assert note.startswith(
        "[system note] The run that worked on this goal was cut off: the Dot daemon stopped while it ran (a crash, or "
        "a restart that did not wait for it). You go on with it; the conversation above holds every step it took. Its "
        f"goal:\n{GOAL}\n\n"
    )
    assert (
        "\n- read_file {}: it finished and succeeded. Its result began: a,b"
        "\n- shell {}: it finished and failed: [exit 1 in 0.1s] no such file"
        "\n- web_fetch {}: it was cut off while it ran. It only reads, so it may run again."
        "\n- write_file {}: it was cut off while it ran and may have changed something: check the state before you run "
        "it again."
        "\n- move_file {}: it had not started, so nothing of it ran.\n\n"
    ) in note
    assert (
        "Its plan, which is your plan now:\n1. [done] Read the data\n2. [in_progress] Write the report\n"
        "Targets: report.md holds every row of the data\n\n"
    ) in note
    assert note.endswith("finish the goal.")
    assert resumed_event_data(resumption) == {
        "from_run_id": "run_1",
        "cut_off": "daemon_restarted",
        "automatic": False,
        "calls": [
            {"tool": "read_file", "end": "succeeded", "repeatable": True},
            {"tool": "shell", "end": "failed", "repeatable": False},
            {"tool": "web_fetch", "end": "cut_off", "repeatable": True},
            {"tool": "write_file", "end": "cut_off", "repeatable": False},
            {"tool": "move_file", "end": "not_started", "repeatable": False},
        ],
    }


def test_a_run_cut_off_between_steps_is_told_only_its_goal_and_to_go_on() -> None:
    resumption: Resumption = Resumption(
        from_run_id="run_1", cut_off="daemon_shutdown", automatic=False, calls=(), plan=EMPTY_PLAN
    )
    assert resume_note(GOAL, resumption) == (
        "[system note] The run that worked on this goal was cut off: the Dot daemon was shut down while it ran. You go "
        f"on with it; the conversation above holds every step it took. Its goal:\n{GOAL}\n\nPick up where it left off: "
        "do not redo what is done, check what a call that was cut off may have changed, and finish the goal."
    )


def test_a_run_the_daemon_resumed_by_itself_is_told_to_check_the_state_before_it_repeats_anything() -> None:
    """Item 1854: OpenClaw's words for a run its gateway's restart cut."""
    resumption: Resumption = Resumption(
        from_run_id="run_1", cut_off="daemon_restarted", automatic=True, calls=(), plan=EMPTY_PLAN
    )
    assert resume_note(GOAL, resumption).startswith(
        "[system note] Dot restarted while you were working on this goal, and goes on with it now: the Dot daemon "
        "stopped while it ran (a crash, or a restart that did not wait for it). The conversation above holds every "
        "step it took. Before you repeat anything, check the current state of what was in progress (files, processes, "
        f"helpers, outside actions), then continue. Its goal:\n{GOAL}\n\n"
    )


_OPEN: PlanPayload = PlanPayload(
    steps=(PlanItem(text="Write the report", status="in_progress"),),
    targets="report.md holds every row",
)


def test_a_blocked_failed_or_continued_run_is_handed_its_goal_its_calls_and_its_plan() -> None:
    calls: tuple[InterruptedCall, ...] = (_call("read_file", "read", "succeeded", "a,b"),)
    for reason in ("blocked", "failed", "continued"):
        note: str = resume_note(
            GOAL,
            Resumption(from_run_id="run_1", cut_off=reason, automatic=False, calls=calls, plan=_OPEN),
        )
        assert GOAL in note, reason
        assert "read_file {}: it finished and succeeded. Its result began: a,b" in note, reason
        assert "Its plan, which is your plan now:\n1. [in_progress] Write the report" in note, reason
        assert note.endswith("finish the goal."), reason


def test_after_a_compaction_the_handoff_keeps_the_open_plan_and_the_ledger_ids() -> None:
    """The note stands in front of the model as the opening message. The per-message turn note, which keeps the system
    prompt's bytes stable, does not carry it."""
    named: str = "run_abc123_wxyz"
    ledger = with_actions(new_ledger(GOAL), [(WRITE, True, f"wrote {named}")])
    compacted: str = continuity_note(ledger, _OPEN, 4000, "NEXT: write the report.", "the fast model", None)
    ids: tuple[str, ...] = named_ids(compacted)
    assert ids == (named,)
    assert identities_of(["an older message", compacted]) == ids
    note: str = resume_note(
        GOAL,
        Resumption(
            from_run_id="run_1",
            cut_off="continued",
            automatic=False,
            calls=(_call("read_file", "read", "cut_off", ""),),
            plan=_OPEN,
            identities=ids,
        ),
    )
    assert named in note and "[in_progress] Write the report" in note and GOAL in note
    now: datetime = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert named not in turn_note(now, [], 0, [], [])


HELPER_CUT: RunRecord = _run("cancelled", RESULT_SHUTDOWN, "delegate", "run_parent")


def test_a_helper_the_daemons_end_cut_is_resumed_unless_a_rule_says_why_not() -> None:
    """Item 1668: a helper cut by a crash or a stop of the daemon is resumed at the next start; each refusal names its
    reason, and an outward call cut while it ran (it pays, sends or posts) is one, as a second try could send twice."""
    crashed: RunRecord = _run("failed", RESULT_RESTARTED, "delegate", "run_parent")
    assert cut_by_restart(crashed) and cut_by_restart(HELPER_CUT)
    assert not cut_by_restart(_run("cancelled", RESULT_STOPPED, "delegate", "run_parent"))
    write: InterruptedCall = _call("write_file", "write", "cut_off", "")
    assert helper_not_resumed(HELPER_CUT, "run_1", [write], False, "safe", 10.0, 1800.0) is None
    assert helper_not_resumed(crashed, "run_1", [], False, "safe", 10.0, 1800.0) is None
    assert "off" in str(helper_not_resumed(HELPER_CUT, "run_1", [], False, "off", 10.0, 1800.0))
    assert "longer ago" in str(helper_not_resumed(HELPER_CUT, "run_1", [], False, "safe", 4000.0, 1800.0))
    assert "another run" in str(helper_not_resumed(HELPER_CUT, "run_2", [], False, "safe", 10.0, 1800.0))
    assert "not resumed twice" in str(helper_not_resumed(HELPER_CUT, "run_1", [], True, "safe", 10.0, 1800.0))
    mail: InterruptedCall = InterruptedCall(
        tool="mail_send", arguments="{}", kind="message", end="cut_off", result="", flags=("message_send",)
    )
    assert "mail_send" in str(helper_not_resumed(HELPER_CUT, "run_1", [mail], False, "safe", 10.0, 1800.0))
    finished: InterruptedCall = mail.model_copy(update={"end": "succeeded"})
    assert helper_not_resumed(HELPER_CUT, "run_1", [finished], False, "safe", 10.0, 1800.0) is None


def test_the_outward_flags_are_the_policys_own() -> None:
    """The class a resumed helper refuses is what even yolo keeps for the owner (``owners_guard``)."""
    assert OUTWARD_FLAGS == engine._OWNERS_FLAGS


def test_the_note_names_the_work_in_the_background_the_start_stopped() -> None:
    """Item 1670: a group the daemon's start ended is named to the run that goes on, with when it started."""
    group: GroupRecord = GroupRecord(
        pgid=4242,
        started=datetime(2026, 11, 24, 10, 41, 5).timestamp(),
        run_id="run_1",
        kind="shell",
        what="npm run build",
        dot_pid=1,
        dot_started=0.0,
        at=0.0,
    )
    line: str = stopped_line(group)
    assert line == "a shell command started at 10:41:05: npm run build"
    resumption: Resumption = Resumption(
        from_run_id="run_1", cut_off="daemon_restarted", automatic=True, calls=(), plan=EMPTY_PLAN, stopped=(line,)
    )
    note: str = resume_note(GOAL, resumption)
    assert "was stopped when Dot started again; it no longer runs" in note and f"- {line}" in note
    assert "no longer runs" not in resume_note(GOAL, resumption.model_copy(update={"stopped": ()}))


def test_a_cut_call_that_sent_something_out_holds_the_run_as_the_schedulers_set_does() -> None:
    """Item 1854: an upload, a release or data sent out holds a run back as a payment does: the scheduler's set."""
    assert recovery.OUTWARD_FLAGS == SENT_OUT_FLAGS >= OUTWARD_FLAGS
    for flag in ("upload", "release", "external_send", "exfiltration", "social"):
        sent: InterruptedCall = InterruptedCall(
            tool="shell", arguments="{}", kind="shell", end="cut_off", result="", flags=(flag,)
        )
        assert not resumes_on_start(RESTARTED, "run_1", [sent]), flag


async def test_a_browser_click_is_not_classified_again_by_a_daemon_that_holds_no_snapshot_of_its_tab() -> None:
    """Item 1854: ``browser_act`` reads the element of a click from the tab's last snapshot, which a daemon that has
    just started does not hold; classified again, a click on "Pay now" would raise nothing. Its kind is unknown."""
    tools: dict[str, object] = {"browser_act": object()}
    context: ToolContext = cast(
        ToolContext, SimpleNamespace(runtime=SimpleNamespace(registry=SimpleNamespace(tools=tools)))
    )
    click: ToolCall = ToolCall(id="call_9", name="browser_act", arguments={"action": "click", "ref": "e12"})
    assert await _action_of(click, context) is None
    assert held_back(_call("browser_act", None, "cut_off", ""))
