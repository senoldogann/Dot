"""Resuming a run that the daemon cut off: the calls of the step it was in, which runs may be resumed by hand and which
the daemon resumes on its own, and what the resumed run is told."""

from dotagent.agent.compact import pending_calls
from dotagent.agent.plan import EMPTY_PLAN, PlanItem, PlanPayload
from dotagent.agent.resume import (
    RESUME_SOURCE,
    CallEnd,
    InterruptedCall,
    Resumption,
    can_resume,
    cut_off_by,
    resume_note,
    resumed_event_data,
    resumes_on_start,
)
from dotagent.llm.types import Message, ToolCall, assistant_message, tool_message, user_message
from dotagent.policy.types import ActionKind
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_RESTARTED, RESULT_SHUTDOWN, RESULT_STOPPED
from dotagent.store.models import RunRecord, RunStatus

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
    # The thread went on after it, another run started it, or the owner stopped it.
    assert not can_resume(RESTARTED, "run_2")
    assert not can_resume(_run("failed", RESULT_RESTARTED, UI_SOURCE, "run_0"), "run_1")
    assert not can_resume(_run("cancelled", RESULT_STOPPED, UI_SOURCE, None), "run_1")


def test_the_daemon_resumes_on_its_own_only_runs_of_the_web_ui_whose_calls_cut_off_while_they_ran_only_read() -> None:
    read: InterruptedCall = _call("read_file", "read", "cut_off", "")
    assert resumes_on_start(RESTARTED, "run_1", [read])
    assert resumes_on_start(SHUT_DOWN, "run_1", [])
    # A call that finished is reported with its result, and one that had not started did nothing.
    finished_write: InterruptedCall = _call("write_file", "write", "succeeded", "Wrote report.md")
    unstarted_shell: InterruptedCall = _call("shell", "shell", "not_started", "")
    assert resumes_on_start(RESTARTED, "run_1", [finished_write, read, unstarted_shell])
    assert not resumes_on_start(RESTARTED, "run_1", [_call("write_file", "write", "cut_off", "")])
    # A delegated run may have done anything its mode allows; a call that could not be classified may have too.
    assert not resumes_on_start(RESTARTED, "run_1", [_call("delegate", "delegate", "cut_off", "")])
    assert not resumes_on_start(RESTARTED, "run_1", [_call("desktop_act", None, "cut_off", "")])
    assert not resumes_on_start(RESTARTED, "run_2", [read])
    for source in ("cli", SCHEDULE_SOURCE, RESUME_SOURCE):
        assert not resumes_on_start(_run("failed", RESULT_RESTARTED, source, None), "run_1", [read])


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
        from_run_id="run_1", cut_off="daemon_shutdown", automatic=True, calls=(), plan=EMPTY_PLAN
    )
    assert resume_note(GOAL, resumption) == (
        "[system note] The run that worked on this goal was cut off: the Dot daemon was shut down while it ran. You go "
        f"on with it; the conversation above holds every step it took. Its goal:\n{GOAL}\n\nPick up where it left off: "
        "do not redo what is done, check what a call that was cut off may have changed, and finish the goal."
    )
