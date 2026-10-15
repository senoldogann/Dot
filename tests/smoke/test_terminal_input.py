"""Typing into a terminal session never holds the daemon: a real pseudo-terminal and a real program that does not read,
reads late, or is ended with input still waiting for it.
"""

import asyncio
import hashlib
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.core.errors import TerminalInputFullError
from dotagent.terminal.session import Terminals, TerminalSession

pytestmark = pytest.mark.asyncio(loop_scope="module")

# What one terminal_send may carry at most (its ``text`` field), as lines a terminal passes on whole.
_SEND_CHARS: int = 10_000
_LINE: str = "x" * 79 + "\n"
_SEND: str = (_LINE * (_SEND_CHARS // len(_LINE) + 1))[:_SEND_CHARS]
_HEARTBEAT_S: float = 0.01
# The longest the event loop may go without a turn while input is typed and waits; a blocking write held it for as
# long as the program did not read (seconds).
_LONGEST_GAP_S: float = 0.1
_SEND_CALL_S: float = 0.05


class Heartbeat:
    """Measures the longest gap between turns of the event loop while it runs."""

    def __init__(self) -> None:
        self.longest_s: float = 0.0
        self._task: asyncio.Task[None] | None = None

    async def _beat(self) -> None:
        last: float = time.monotonic()
        while True:
            await asyncio.sleep(_HEARTBEAT_S)
            now: float = time.monotonic()
            self.longest_s = max(self.longest_s, now - last)
            last = now

    def start(self) -> None:
        self._task = asyncio.create_task(self._beat())

    async def stop(self) -> float:
        assert self._task is not None
        self._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self._task
        return self.longest_s


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def terminals() -> AsyncIterator[Terminals]:
    built: Terminals = Terminals(limit=4, idle_s=120.0, env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]})
    try:
        yield built
    finally:
        await built.aclose()


async def _open(terminals: Terminals, thread: str, command: str, cwd: Path) -> TerminalSession:
    return await terminals.open(thread, command, (), cwd, False)


async def test_typing_into_a_program_that_does_not_read_returns_at_once(terminals: Terminals, tmp_path: Path) -> None:
    session: TerminalSession = await _open(terminals, "deaf", "sleep 30", tmp_path)
    heartbeat: Heartbeat = Heartbeat()
    heartbeat.start()
    try:
        for _ in range(4):
            started: float = time.monotonic()
            session.send(_SEND)
            assert time.monotonic() - started < _SEND_CALL_S
        await asyncio.sleep(1.0)
        assert await heartbeat.stop() < _LONGEST_GAP_S
    finally:
        await terminals.close("deaf", session.id)


async def test_what_waits_unread_is_bounded_and_the_rest_refused(terminals: Terminals, tmp_path: Path) -> None:
    session: TerminalSession = await _open(terminals, "full", "sleep 30", tmp_path)
    try:
        sent: int = 0
        with pytest.raises(TerminalInputFullError) as refused:
            for _ in range(10):
                session.send(_SEND)
                sent += len(_SEND)
        waiting: object = refused.value.context["waiting_bytes"]
        assert isinstance(waiting, int) and 0 < waiting <= sent, refused.value.context
        assert refused.value.context["limit_bytes"] == 64 * 1024
        # The refusal changed nothing: the same send is refused again with the same amount waiting.
        with pytest.raises(TerminalInputFullError) as again:
            session.send(_SEND)
        assert again.value.context["waiting_bytes"] == waiting
    finally:
        await terminals.close("full", session.id)


async def test_a_program_that_reads_late_gets_everything_typed(terminals: Terminals, tmp_path: Path) -> None:
    total: int = 4 * _SEND_CHARS
    reader: str = (
        f'"{sys.executable}" -c "import sys, time, hashlib; time.sleep(1.5); data = sys.stdin.buffer.read({total}); '
        'print(\\"SHA\\", hashlib.sha256(data).hexdigest(), flush=True)"'
    )
    session: TerminalSession = await _open(terminals, "late", reader, tmp_path)
    heartbeat: Heartbeat = Heartbeat()
    heartbeat.start()
    try:
        typed: bytes = b""
        for _ in range(4):
            session.send(_SEND)
            typed += _SEND.encode()
        output: str = ""
        deadline: float = time.monotonic() + 15.0
        while "SHA " not in output and time.monotonic() < deadline:
            output += await session.read(2.0)
        assert await heartbeat.stop() < _LONGEST_GAP_S
        assert f"SHA {hashlib.sha256(typed).hexdigest()}" in output, output[-300:]
    finally:
        await terminals.close("late", session.id)


async def test_a_session_closed_with_input_waiting_lets_go_of_it(terminals: Terminals, tmp_path: Path) -> None:
    session: TerminalSession = await _open(terminals, "gone", "sleep 30", tmp_path)
    for _ in range(5):
        session.send(_SEND)
    started: float = time.monotonic()
    await terminals.close("gone", session.id)
    assert time.monotonic() - started < 5.0
    assert session.ended and session.finished.is_set()
