"""How a run's Claude Code program was cut off (item 1580).

A program that ends before its result with nobody ending it (killed from outside, crashed, exited by itself) used to
end its run with one line, "Claude Code ended before it answered", and nothing to tell the cause from another. On
2026-11-24 at 10:44:04 five programs of one daemon ended within 20 ms of each other that way, their stderr empty, and
the cause was never found. Now the error's context and a log line say how each one ended (:class:`ProgramExit`: the exit
code and the signal's name, the end of its error stream, whether it had said what the model has, the steps it took and
the seconds since its last line), and one more line, ``claude_code_programs_ended_together``, says when two or more
programs of this process ended inside the same second without Dot ending them (:class:`EndedTogether`), so that the next
mass cut is explained in one line. Dot's own ends are marked (:func:`mark_ended`) and never counted."""

import asyncio
import signal
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject

log = get_logger(__name__)

# How much of the program's error stream the context of a cut keeps (the end of it).
STDERR_CUT_CHARS: int = 2048
# How long Dot waits, once the program's output closed, for the program to end by itself before it ends it: a program
# killed from outside has its exit within milliseconds of its output closing, so one that is still there after this is
# a program Dot has to end, and its exit is Dot's own.
EXIT_SEEN_S: float = 2.0
# Programs that end within this many seconds of the first of them are one mass cut.
TOGETHER_S: float = 1.0
# A shell reads a program that a signal ended as ``128 + the signal's number``, and Claude Code ends so itself on
# SIGTERM (measured on 2.1.331: 143), so that no negative code tells it.
_SHELL_SIGNAL: int = 128
_MOST_SIGNALS: int = 31


def signal_name(returncode: int | None) -> str | None:
    """The name of the signal a program ended with: for a program the kernel ended with one (a negative ``returncode``,
    as a killed process reports it) and for one that exited ``128 + n`` as Claude Code does on SIGTERM; ``None`` for a
    program that exited with any other code, and for one that did not end (``returncode`` ``None``)."""
    if returncode is None:
        return None
    number: int = 0
    if returncode < 0:
        number = -returncode
    elif _SHELL_SIGNAL < returncode <= _SHELL_SIGNAL + _MOST_SIGNALS:
        number = returncode - _SHELL_SIGNAL
    try:
        return signal.Signals(number).name
    except ValueError:
        return None


@dataclass(frozen=True)
class ProgramExit:
    """How a program ended before its result, and what it had done. ``returncode`` is ``None`` for a program that was
    still running when its output closed, which Dot then ended itself; ``silent_s`` is the seconds from the last line it
    wrote to when its output closed; ``at`` is the wall clock (epoch seconds) of the cut."""

    run_id: str
    returncode: int | None
    stderr: str
    inited: bool
    steps: int
    silent_s: float
    session_id: str
    at: float

    @property
    def signal(self) -> str | None:
        return signal_name(self.returncode)

    @property
    def clock(self) -> str:
        """The time of the cut as the owner reads it: ``10:44:04``, their own clock."""
        return datetime.fromtimestamp(self.at).astimezone().strftime("%H:%M:%S")

    def context(self) -> JsonObject:
        """What the error of a cut carries, and the log line: the exit and what the program had done."""
        return {
            "returncode": self.returncode,
            "signal": self.signal,
            "exited": self.returncode is not None,
            "stderr": self.stderr[-STDERR_CUT_CHARS:],
            "inited": self.inited,
            "steps": self.steps,
            "silent_s": self.silent_s,
            "claude_session": self.session_id,
            "cut_at": self.clock,
        }


# The programs Dot ended itself (SIGTERM to their group, then SIGKILL), kept weakly: a program that went away with its
# run needs no entry. A cut is never counted for one of them (:meth:`EndedTogether.note`).
_ENDED_BY_DOT: "weakref.WeakSet[asyncio.subprocess.Process]" = weakref.WeakSet()


def mark_ended(process: "asyncio.subprocess.Process") -> None:
    """Note that Dot ends ``process`` itself, so that its end is no cut and no part of a mass cut."""
    _ENDED_BY_DOT.add(process)


def ended_by_dot(process: "asyncio.subprocess.Process") -> bool:
    """Whether Dot ended ``process`` itself (:func:`mark_ended`)."""
    return process in _ENDED_BY_DOT


async def exit_seen(process: "asyncio.subprocess.Process", wait_s: float = EXIT_SEEN_S) -> int | None:
    """The exit code of ``process`` once its output closed: at once when it has ended, else after at most ``wait_s``
    seconds for it to end by itself; ``None`` when it did not (it is still running, which is no cut from outside)."""
    if process.returncode is not None:
        return process.returncode
    try:
        async with asyncio.timeout(wait_s):
            return await process.wait()
    except TimeoutError:
        return None


@dataclass(frozen=True)
class _End:
    run_id: str
    returncode: int | None
    at: float


class EndedTogether:
    """The programs of this process that were cut within :data:`TOGETHER_S` of each other, said in one log line
    (``claude_code_programs_ended_together``: the run ids, the signals and codes, the spread in milliseconds). The
    first cut opens a window; the programs cut inside it join it; when it closes the line is written once if two or more
    are in it. Only cuts are noted, never an end Dot made, and never a program that ended after its result."""

    def __init__(self, window_s: float = TOGETHER_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._window_s: float = window_s
        self._clock: Callable[[], float] = clock
        self._burst: list[_End] = []
        self._timer: asyncio.TimerHandle | None = None

    def note(self, run_id: str, returncode: int | None) -> None:
        """A program of ``run_id`` was cut and ended with ``returncode``."""
        now: float = self._clock()
        if self._burst and now - self._burst[0].at > self._window_s:
            self.flush()
        self._burst.append(_End(run_id=run_id, returncode=returncode, at=now))
        if len(self._burst) == 2 and self._timer is None:
            try:
                loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._timer = loop.call_later(max(self._burst[0].at + self._window_s - now, 0.0), self.flush)

    def flush(self) -> None:
        """Close the window: write the line when two or more programs are in it."""
        burst: list[_End] = self._burst
        self._burst = []
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if len(burst) < 2:
            return
        log.warning(
            "claude_code_programs_ended_together",
            programs=len(burst),
            run_ids=[end.run_id for end in burst],
            signals=[signal_name(end.returncode) for end in burst],
            returncodes=[end.returncode for end in burst],
            spread_ms=round((burst[-1].at - burst[0].at) * 1000),
        )


# The cuts of this process (the daemon's, or a ``dotx run``'s).
ENDED_TOGETHER: EndedTogether = EndedTogether()


def note_cut(program: ProgramExit, process_ended_by_dot: bool, ends: EndedTogether | None = None) -> None:
    """Say that ``program`` was cut (a log line with how it ended) and count it for a mass cut, unless Dot ended the
    program itself or it did not end by itself (``returncode`` ``None``)."""
    log.warning("claude_code_program_cut", run_id=program.run_id, **program.context())
    if program.returncode is not None and not process_ended_by_dot:
        (ENDED_TOGETHER if ends is None else ends).note(program.run_id, program.returncode)
