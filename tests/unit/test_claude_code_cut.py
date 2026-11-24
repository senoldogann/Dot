"""How a Claude Code program that ended before its result is told (item 1580): the signal behind an exit code, what the
error of a cut carries, the one log line that explains several programs ended together, and the exit a program is given
a moment to have. The runner's own use of them is in ``tests/smoke/test_claude_code_run.py``."""

import asyncio
import re
import sys

import pytest
from structlog.testing import capture_logs

from dotagent.claude_code.cut import (
    STDERR_CUT_CHARS,
    EndedTogether,
    ProgramExit,
    ended_by_dot,
    exit_seen,
    mark_ended,
    note_cut,
    signal_name,
)


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
