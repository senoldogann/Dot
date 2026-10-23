"""The terminal's prompts for a decision (item 558). A line typed while a run worked, before its prompt appeared (the
next message of a chat), is set aside and answers nothing: before, a "yes" typed ahead approved the next step that
asked, and any line but "skip" told the run that a hand-off was done. A line typed at the prompt answers it; the chat's
own prompt keeps what was typed ahead, as does standard input that is no terminal. A pseudo-terminal of the test's own
stands for the terminal, and a stand-in of the broker for the approvals."""

import asyncio
import os
import select
import sys
import threading
from collections.abc import Iterator
from typing import cast

import pytest

from dotagent import cli_ui
from dotagent.cli_ui import ApprovalPayload, TerminalUi, UiOptions
from dotagent.runtime import Runtime

_ARRIVES_S: float = 5.0
# How long a prompt for a decision is left to read what was typed ahead, which it must not take.
_LEFT_S: float = 0.5


@pytest.fixture
def typed(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """A pseudo-terminal as standard input; yields the side the test types on."""
    keyboard, screen = os.openpty()
    stdin = os.fdopen(screen, "r")
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        yield keyboard
    finally:
        stdin.close()
        os.close(keyboard)


def _type(keyboard: int, line: bytes) -> None:
    """Type ``line`` and wait until the terminal has it to read."""
    os.write(keyboard, line)
    ready, _, _ = select.select([sys.stdin.fileno()], [], [], _ARRIVES_S)
    assert ready, "the line did not reach the terminal"


def test_a_line_typed_before_a_decision_prompt_answers_nothing(typed: int, capsys: pytest.CaptureFixture[str]) -> None:
    _type(typed, b"y\n")
    stop: threading.Event = threading.Event()
    timer: threading.Timer = threading.Timer(_LEFT_S, stop.set)
    timer.start()
    try:
        assert cli_ui._read_line_blocking("allow? [y/N]> ", stop, True) is None
    finally:
        timer.cancel()
    assert "set aside" in capsys.readouterr().err


def test_a_line_typed_at_a_decision_prompt_answers_it(typed: int) -> None:
    stop: threading.Event = threading.Event()
    # The line is typed once the prompt is up; the second timer only keeps a failure from waiting for ever.
    typing: threading.Timer = threading.Timer(_LEFT_S, os.write, (typed, b"n\n"))
    giving_up: threading.Timer = threading.Timer(_ARRIVES_S, stop.set)
    typing.start()
    giving_up.start()
    try:
        assert cli_ui._read_line_blocking("allow? [y/N]> ", stop, True) == "n"
    finally:
        typing.cancel()
        giving_up.cancel()


def test_the_chats_own_prompt_keeps_what_was_typed_ahead(typed: int) -> None:
    _type(typed, b"and the weather?\n")
    assert cli_ui._read_line_blocking("you> ", threading.Event(), False) == "and the weather?"


def test_standard_input_that_is_no_terminal_keeps_its_lines() -> None:
    reading, writing = os.pipe()
    try:
        os.write(writing, b"y\n")
        assert not cli_ui._set_aside_typed(reading)
        assert os.read(reading, 16) == b"y\n"
    finally:
        os.close(reading)
        os.close(writing)


class _Approvals:
    """The broker as the terminal meets it: nothing decides elsewhere, and each decision is written down."""

    def __init__(self) -> None:
        self.decided: list[tuple[str, bool, str]] = []

    async def settled(self, approval_id: str) -> None:
        await asyncio.Event().wait()

    async def resolve(self, approval_id: str, approve: bool, decided_by: str, note: str) -> None:
        self.decided.append((approval_id, approve, note))


class _Runtime:
    def __init__(self) -> None:
        self.approvals: _Approvals = _Approvals()


@pytest.mark.parametrize(
    ("answer", "done"), [("", True), ("done", True), ("yes", True), ("skip", False), ("no", False), ("later", False)]
)
async def test_a_hand_off_is_done_only_on_enter_or_a_yes(
    answer: str, done: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    async def typed_at_the_prompt(prompt: str, fresh: bool = False) -> str:
        assert fresh, "a prompt for a decision sets aside what was typed before it"
        asked.append(prompt)
        return answer

    monkeypatch.setattr(cli_ui, "read_line", typed_at_the_prompt)
    runtime: _Runtime = _Runtime()
    ui: TerminalUi = TerminalUi(
        cast(Runtime, runtime), UiOptions(interactive=True, stream_text=False, show_thinking=False, verbose=False)
    )
    request: ApprovalPayload = ApprovalPayload(
        approval_id="apr_1", tool="browser_act", summary="Pay the invoice", severity="handoff", args={}
    )
    await ui._ask_approval(request)
    (decision,) = runtime.approvals.decided
    assert decision[:2] == ("apr_1", done)
    assert ("completed" in decision[2]) is done
    assert "any other answer skips it" in asked[0]
