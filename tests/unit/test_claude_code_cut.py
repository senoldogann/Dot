"""How a Claude Code program that ended before its result is told (item 1580): the signal behind an exit code, what the
error of a cut carries, the one log line that explains several programs ended together, and the exit a program is given
a moment to have. The runner's own use of them is in ``tests/smoke/test_claude_code_run.py``."""

import asyncio
import re
import sys

import pytest
from structlog.testing import capture_logs

from dotagent.claude_code import cut as cut_module
from dotagent.claude_code.cut import (
    CONTINUE_AFTER_S,
    STDERR_CUT_CHARS,
    EndedTogether,
    ProgramExit,
    continuation_note,
    continue_after_s,
    continued_event_data,
    ended_by_dot,
    exit_seen,
    mark_ended,
    note_cut,
    outward,
    resume_refused,
    run_refusal,
    signal_name,
    why_not_continued,
)
from dotagent.claude_code.run import _drop_stale, _end_program, _Ended, _noting_outward, _Quiet, _Silent
from dotagent.cli_ui import TerminalUi, UiOptions
from dotagent.config import load_default_config
from dotagent.llm.types import ToolSpec
from dotagent.policy.types import ActionInfo, Flag
from dotagent.store.models import EventRecord
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.registry import ToolRegistry


def _exit(returncode: int | None, run_id: str = "run_a", **fields: object) -> ProgramExit:
    base: dict[str, object] = {
        "run_id": run_id,
        "returncode": returncode,
        "stderr": "",
        "inited": True,
        "steps": 3,
        "silent_s": 1.5,
        "session_id": "ses-1",
        "at": 1_795_000_000.0,
    }
    return ProgramExit(**{**base, **fields})  # type: ignore[arg-type]  # the fields above are the right types


@pytest.mark.parametrize(
    ("returncode", "name"),
    [
        (-9, "SIGKILL"),
        (-15, "SIGTERM"),
        (-6, "SIGABRT"),
        # Claude Code ends 128 + the number of the signal on SIGTERM (143, measured on 2.1.331), and a shell reports a
        # program the kernel killed so (137).
        (143, "SIGTERM"),
        (137, "SIGKILL"),
        (1, None),
        (0, None),
        (None, None),
        # Not a signal's number: a plain exit code, and a code far past the signals.
        (128, None),
        (200, None),
        (-999, None),
    ],
)
def test_the_signal_behind_an_exit_code_is_named_only_when_there_is_one(
    returncode: int | None, name: str | None
) -> None:
    assert signal_name(returncode) == name


def test_the_context_of_a_cut_says_the_exit_and_what_the_program_had_done() -> None:
    context = _exit(-9, stderr="oom", steps=7, silent_s=12.3).context()
    assert context["returncode"] == -9 and context["signal"] == "SIGKILL" and context["exited"] is True
    assert context["stderr"] == "oom" and context["inited"] is True and context["steps"] == 7
    assert context["silent_s"] == 12.3 and context["claude_session"] == "ses-1"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", str(context["cut_at"])), context["cut_at"]


def test_a_program_still_running_when_its_output_closed_has_no_exit_of_its_own() -> None:
    context = _exit(None).context()
    assert context["returncode"] is None and context["signal"] is None and context["exited"] is False


def test_the_context_keeps_the_last_two_kilobytes_of_the_error_stream() -> None:
    shown = str(_exit(1, stderr="start " + "x" * 5000 + " the end").context()["stderr"])
    assert len(shown) == STDERR_CUT_CHARS and shown.endswith(" the end") and not shown.startswith("start")


def _clock(times: list[float]) -> "tuple[EndedTogether, list[float]]":
    now: list[float] = times
    return EndedTogether(window_s=1.0, clock=lambda: now[0]), now


def _lines(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [line for line in logs if line["event"] == "claude_code_programs_ended_together"]


def test_programs_cut_within_a_second_are_said_once_with_their_ids_and_signals() -> None:
    ends, now = _clock([100.0])
    with capture_logs() as logs:
        ends.note("run_a", -9)
        now[0] = 100.01
        ends.note("run_b", 143)
        now[0] = 100.02
        ends.note("run_c", 1)
        assert _lines(logs) == []
        ends.flush()
    (line,) = _lines(logs)
    assert line["run_ids"] == ["run_a", "run_b", "run_c"]
    assert line["signals"] == ["SIGKILL", "SIGTERM", None]
    assert line["returncodes"] == [-9, 143, 1] and line["programs"] == 3 and line["spread_ms"] == 20
    assert line["log_level"] == "warning"


def test_one_cut_alone_and_cuts_a_second_apart_say_nothing() -> None:
    ends, now = _clock([10.0])
    with capture_logs() as logs:
        ends.note("run_a", -9)
        now[0] = 10.2
        ends.flush()
        now[0] = 50.0
        ends.note("run_b", -9)
        now[0] = 52.5
        ends.note("run_c", -9)
        ends.flush()
    assert _lines(logs) == []


def test_a_cut_after_the_window_closed_opens_a_window_of_its_own() -> None:
    ends, now = _clock([0.0])
    with capture_logs() as logs:
        ends.note("run_a", -9)
        now[0] = 0.5
        ends.note("run_b", -9)
        now[0] = 3.0
        ends.note("run_c", -9)
        now[0] = 3.4
        ends.note("run_d", -9)
        ends.flush()
    first, second = _lines(logs)
    assert first["run_ids"] == ["run_a", "run_b"] and second["run_ids"] == ["run_c", "run_d"]


async def test_the_window_closes_by_itself_and_says_the_line_once() -> None:
    ends = EndedTogether(window_s=0.05)
    with capture_logs() as logs:
        ends.note("run_a", -9)
        ends.note("run_b", -9)
        await asyncio.sleep(0.3)
    (line,) = _lines(logs)
    assert line["run_ids"] == ["run_a", "run_b"]
    ends.flush()
    assert len(_lines(logs)) == 1


async def test_a_program_dot_ended_itself_or_that_did_not_end_is_no_part_of_a_mass_cut() -> None:
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    ends = EndedTogether(window_s=1.0)
    try:
        assert not ended_by_dot(process)
        mark_ended(process)
        assert ended_by_dot(process)
        with capture_logs() as logs:
            note_cut(_exit(-9, "run_a"), True, ends)
            note_cut(_exit(None, "run_b"), False, ends)
            note_cut(_exit(-9, "run_c"), False, ends)
            ends.flush()
        # Every cut is said on its own line, and the one of a program that ended by itself is the only one counted.
        assert [line["run_id"] for line in logs if line["event"] == "claude_code_program_cut"] == [
            "run_a",
            "run_b",
            "run_c",
        ]
        assert _lines(logs) == []
    finally:
        process.kill()
        await process.wait()


async def test_a_program_that_ended_has_its_exit_at_once_and_one_that_lives_has_none() -> None:
    gone = await asyncio.create_subprocess_exec(sys.executable, "-c", "import sys; sys.exit(3)")
    await gone.wait()
    assert await exit_seen(gone) == 3
    living = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    try:
        assert await exit_seen(living, 0.1) is None
    finally:
        living.kill()
        await living.wait()
    # One that ends while it is waited for (its output closed a moment before its exit was reaped) has its code.
    slow = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(0.2)")
    assert await exit_seen(slow, 5.0) == 0


def _action(*flags: Flag, kind: str = "message") -> ActionInfo:
    return ActionInfo(
        tool="send_mail",
        kind=kind,  # type: ignore[arg-type]  # a kind the tests choose
        risk="medium",
        summary="send it",
        target="",
        flags=flags,
    )


@pytest.mark.parametrize(
    ("flags", "kept_for_the_owner"),
    [
        (("message_send", "external_send"), True),
        (("financial",), True),
        (("public_post",), True),
        (("public_post", "social"), True),
        # Actions the policy asks about in some modes but does not keep for the owner in yolo, or that a second call
        # does not do twice: nothing here may stop a run from going on.
        ((), False),
        (("release",), False),
        (("upload",), False),
        (("external_send",), False),
        (("social",), False),
        (("destructive",), False),
    ],
)
def test_an_action_is_outward_when_the_policy_keeps_it_for_the_owner_even_in_yolo(
    flags: tuple[Flag, ...], kept_for_the_owner: bool
) -> None:
    """Item 1581: the classes are the policy's own (``owners_guard``: a payment, a message or mail sent for the owner, a
    public post), not a list of tool names."""
    assert outward(_action(*flags)) is kept_for_the_owner


def test_the_backoff_is_two_seconds_then_ten_and_stays_at_ten() -> None:
    assert CONTINUE_AFTER_S == (2.0, 10.0)
    assert [continue_after_s(count) for count in (0, 1, 2, 5)] == [2.0, 10.0, 10.0, 10.0]


def _why(program: ProgramExit | None = None, **fields: object) -> str | None:
    base: dict[str, object] = {
        "program": program or _exit(-9),
        "resumed": False,
        "cancelled": False,
        "outward_calls": (),
        "continued": 0,
        "allowed": 2,
    }
    return why_not_continued(**{**base, **fields})  # type: ignore[arg-type]  # the fields above are the right types


def test_a_cut_that_nothing_of_dots_caused_and_nothing_sent_is_continued() -> None:
    assert _why() is None
    assert _why(_exit(143)) is None
    assert _why(_exit(1, stderr="crashed")) is None
    # The second continuation is allowed as well, the third is not.
    assert _why(continued=1) is None
    assert "as often as allowed" in str(_why(continued=2))


def test_a_cut_is_not_continued_when_dot_or_the_owner_ended_it_or_nothing_is_known_of_the_run() -> None:
    assert "continue_cut = 0" in str(_why(allowed=0))
    assert _why(cancelled=True) == "the run was stopped"
    assert "Dot ended it" in str(_why(_exit(None)))
    assert "before it said what the model has" in str(_why(_exit(-9, inited=False)))
    assert "session is not known" in str(_why(_exit(-9, session_id="")))


def test_a_program_that_was_itself_a_continuation_may_be_cut_before_its_init_a_new_session_goes_on() -> None:
    assert _why(_exit(1, inited=False), resumed=True) is None


def test_a_cut_with_an_outward_call_in_flight_is_not_continued_and_names_it() -> None:
    reason: str = str(_why(outward_calls=("mail_send", "mail_send", "browser_act")))
    assert "outward action was in flight (browser_act, mail_send)" in reason and "send twice" in reason


def test_the_runs_own_refusals_come_first_and_alone() -> None:
    assert run_refusal(cancelled=False, continued=0, allowed=2) is None
    assert "continue_cut = 0" in str(run_refusal(cancelled=True, continued=0, allowed=0))
    assert run_refusal(cancelled=True, continued=0, allowed=2) == "the run was stopped"
    assert "continued 1 time already" in str(run_refusal(cancelled=False, continued=1, allowed=1))
    assert "continued 2 times already" in str(run_refusal(cancelled=False, continued=2, allowed=2))


@pytest.mark.parametrize(
    ("said", "refused"),
    [
        ("No conversation found with session ID: 8dd2d749", True),
        ("API Error: 400 messages.12: `tool_use` ids were found without `tool_result` blocks", True),
        ('{"type":"invalid_request_error","message":"bad"}', True),
        # Failures that are no sign the session cannot be taken up again stay failures.
        ("API Error: 529 overloaded_error", False),
        ("Claude Code's session failed: error_during_execution", False),
        ("Invalid API key", False),
        ("", False),
    ],
)
def test_a_resume_is_refused_only_by_words_that_say_the_session_cannot_be_taken_up_again(
    said: str, refused: bool
) -> None:
    assert resume_refused(said) is refused


def test_the_note_to_the_program_says_when_it_was_cut_and_what_to_do() -> None:
    program: ProgramExit = _exit(-9, at=1_795_000_000.0)
    note: str = continuation_note(program, fresh=False)
    assert note.startswith("[system note]") and f"cut off at {program.clock} by something outside this run" in note
    for words in ("may be lost", "git status", "the files", "the tabs", "before you repeat anything", "your plan says"):
        assert words in note
    fresh: str = continuation_note(program, fresh=True)
    assert "could not be taken up again" in fresh and "what it had done is above" in fresh
    assert f"cut off at {program.clock}" in fresh


def test_the_event_of_a_continuation_carries_the_exit_and_which_continuation_it_is() -> None:
    data = continued_event_data(_exit(-9, steps=4), attempt=2, allowed=2, fresh=True, waited_s=10.0)
    assert (data["attempt"], data["of"], data["fresh_session"], data["waited_s"]) == (2, 2, True, 10.0)
    assert data["returncode"] == -9 and data["signal"] == "SIGKILL" and data["steps"] == 4
    assert re.fullmatch(r"\d\d:\d\d:\d\d", str(data["cut_at"]))


async def test_a_call_the_policy_keeps_for_the_owner_is_noted_as_its_tool_classifies_it_and_runs_as_before() -> None:
    """Item 1581: the registry the door's calls go through notes an outward call as the tool classifies it, and
    changes nothing else: the prepared call is the tool's own."""
    ran: list[str] = []

    def tool(name: str, flags: tuple[Flag, ...]) -> RegisteredTool:
        async def prepare(arguments: dict[str, object], ctx: object) -> PreparedCall:
            async def run(_: object) -> ToolResult:
                ran.append(name)
                return ToolResult(text="done", images=(), is_error=False)

            return PreparedCall(action=_action(*flags), run=run)  # type: ignore[arg-type]  # a stand-in call

        return RegisteredTool(
            spec=ToolSpec(name=name, description="", parameters={"type": "object", "properties": {}}),
            modes=frozenset({"task"}),
            parallel_safe=True,
            timeout_s=5.0,
            prepare=prepare,  # type: ignore[arg-type]  # a stand-in
        )

    quiet: _Quiet = _Quiet(heard=0.0, queue=asyncio.Queue())
    noted: ToolRegistry = _noting_outward(
        quiet,
        ToolRegistry(
            tools={
                "read": tool("read", ()),
                "send_mail": tool("send_mail", ("message_send", "external_send")),
                "pay": tool("pay", ("financial",)),
            }
        ),
    )
    assert set(noted.tools) == {"read", "send_mail", "pay"}
    for name in ("read", "send_mail", "pay"):
        prepared: PreparedCall = await noted.tools[name].prepare({}, None)  # type: ignore[arg-type]  # no context needed
        assert (await prepared.run(None)).text == "done"  # type: ignore[arg-type]
    assert quiet.outward == ["send_mail", "pay"] and ran == ["read", "send_mail", "pay"]


async def test_what_a_program_that_is_gone_left_in_the_queue_is_dropped_and_the_rest_waits() -> None:
    queue: asyncio.Queue[object] = asyncio.Queue()
    for item in (_Ended(), "a line", _Silent(seconds=700.0), _Ended()):
        queue.put_nowait(item)
    _drop_stale(queue)  # type: ignore[arg-type]  # the queue of a run holds these
    assert [queue.get_nowait()] == ["a line"] and queue.empty()


def test_the_module_keeps_its_backoff_where_the_tests_can_change_it() -> None:
    assert cut_module.CONTINUE_AFTER_S is CONTINUE_AFTER_S


async def test_the_program_dot_ends_is_marked_and_one_that_ended_already_is_not() -> None:
    """Item 1580: ``_end_program`` marks the program it ends while it is running, so that its end is never counted as a
    cut; a program that ended by itself before is not Dot's end."""
    # Started as the runner starts the program: a group of its own, which Dot ends whole.
    living = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", start_new_session=True
    )
    gone = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await gone.wait()
    await _end_program(living)
    await _end_program(gone)
    assert ended_by_dot(living) and not ended_by_dot(gone)
    assert living.returncode is not None


@pytest.mark.parametrize(
    ("fresh", "says"),
    [
        (False, "Dot continued the run in its session (1 of 2)"),
        (True, "Dot continued the run in a new session (1 of 2)"),
    ],
)
async def test_the_terminal_says_the_run_was_continued_and_how_the_program_ended(
    fresh: bool, says: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Item 1581: ``dotx run`` shows the line the page shows; an event kind it does not know stays quiet (a default)."""
    ui: TerminalUi = TerminalUi(
        None,  # type: ignore[arg-type]  # no decision is asked here
        load_default_config().server,
        UiOptions(interactive=False, stream_text=False, show_thinking=False, verbose=False),
    )
    data = continued_event_data(_exit(-9, at=1_795_000_000.0), attempt=1, allowed=2, fresh=fresh, waited_s=2.0)
    await ui.handle(EventRecord(id=1, run_id="run_a", ts=1.0, kind="claude_code_continued", data=data))
    await ui.handle(EventRecord(id=2, run_id="run_a", ts=2.0, kind="a_kind_of_a_later_version", data={"x": 1}))
    shown: str = capsys.readouterr().err
    assert "the program was cut off at" in shown and "(SIGKILL)" in shown and says in shown
    assert shown.count("\n") == 1
