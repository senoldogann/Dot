"""How a run's Claude Code program was cut off (items 1580 and 1581).

A program that ends before its result with nobody ending it (killed from outside, crashed, exited by itself) used to
end its run with one line, "Claude Code ended before it answered", and nothing to tell the cause from another. On
2026-11-24 at 10:44:04 five programs of one daemon ended within 20 ms of each other that way, their stderr empty, and
the cause was never found. Now the error's context and a log line say how each one ended (:class:`ProgramExit`: the exit
code and the signal's name, the end of its error stream, whether it had said what the model has, the steps it took and
the seconds since its last line), and one more line, ``claude_code_programs_ended_together``, says when two or more
programs of this process ended inside the same second without Dot ending them (:class:`EndedTogether`), so that the next
mass cut is explained in one line. Dot's own ends are marked (:func:`mark_ended`) and never counted.

A cut run goes on in place (item 1581): when the cause is not Dot's own and nothing the program did may be done twice
(:func:`why_not_continued`), Dot starts the program again in the same session, in the same run, after 2 s and after 10 s
(:data:`CONTINUE_AFTER_S`), at most ``[providers.claude_code] continue_cut`` times, and tells it so
(:func:`continuation_note`); the run's page shows a line each time (:data:`CONTINUED_EVENT`). Measured on 2.1.331: a
program killed in the middle of a call of a tool, resumed with ``--resume``, finds the dangling call closed by Claude
Code itself (a result that says the session ended before it was recorded and that its outcome is unknown) and goes on
with no API error. A resume that is refused is met with a new session that is told what the cut one had done."""

import asyncio
import re
import signal
import time
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject
from dotagent.policy.types import OUTWARD_FLAGS, ActionInfo

log = get_logger(__name__)

# How much of the program's error stream the context of a cut keeps (the end of it).
STDERR_CUT_CHARS: int = 2048
# How much of what a program said when it failed the context of its end keeps (the start of it, which names the error).
_SAID_CHARS: int = 300
# How long Dot waits, once the program's output closed, for the program to end by itself before it ends it: a program
# killed from outside has its exit within milliseconds of its output closing, so one that is still there after this is
# a program Dot has to end, and its exit is Dot's own.
EXIT_SEEN_S: float = 2.0
# Programs that end within this many seconds of the first of them are one mass cut.
TOGETHER_S: float = 1.0
# How long Dot waits before it starts the program again, by how many times the run was continued before: the first
# continuation after 2 s (the calls of the cut program are being cancelled in Dot, its connections are closing), the
# next after 10 s (item 1581). More continuations than this wait as long as the last.
CONTINUE_AFTER_S: tuple[float, ...] = (2.0, 10.0)
# The event a continuation puts on the run's page.
CONTINUED_EVENT: str = "claude_code_continued"
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
    # What the program said when it failed without being cut (item 1581: a resume the API refused), which Dot then
    # ended itself, so that it has no exit of its own: the page and the terminal say it instead of an exit.
    said: str = ""

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
            **({"said": self.said[:_SAID_CHARS]} if self.said else {}),
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


# What a resumed program says when its session cannot be taken up again: a session it does not find, or an API error
# about the calls of the one that was cut (the API refuses a request whose tool call has no result), which Claude Code
# 2.1.331 closes itself, but another version may not.
_RESUME_REFUSED: re.Pattern[str] = re.compile(
    r"no conversation found|invalid_request_error|api error: 400|tool_use|tool_result", re.IGNORECASE
)


def resume_refused(said: str) -> bool:
    """Whether what a resumed program said is that its session could not be taken up again (:data:`_RESUME_REFUSED`)."""
    return _RESUME_REFUSED.search(said) is not None


def outward(action: ActionInfo) -> bool:
    """Whether ``action`` reaches other people in the owner's name: it pays, sends a message or mail for them, or posts
    in their name (:data:`dotagent.policy.types.OUTWARD_FLAGS`). A call of such an action that was in flight when the
    program was cut may have been done, and the model, told its result may be lost, could do it a second time."""
    return not OUTWARD_FLAGS.isdisjoint(action.flags)


def run_refusal(*, cancelled: bool, continued: int, allowed: int) -> str | None:
    """Why the run itself may not go on after its program ended before its result (item 1581), whatever the program
    did: continuing is off (``allowed`` is ``[providers.claude_code] continue_cut``), the run was stopped
    (``cancelled``: the owner's Stop, the emergency stop and the daemon's shutdown all set it), or it was continued
    ``continued`` times already, as often as allowed. ``None`` when it may."""
    if allowed <= 0:
        return "continuing a cut run is off ([providers.claude_code] continue_cut = 0)"
    if cancelled:
        return "the run was stopped"
    if continued >= allowed:
        return f"the run was continued {continued} time{'' if continued == 1 else 's'} already, as often as allowed"
    return None


def why_not_continued(
    *,
    program: ProgramExit,
    resumed: bool,
    cancelled: bool,
    outward_calls: Sequence[str],
    continued: int,
    allowed: int,
) -> str | None:
    """Why the run is not continued after this cut, ``None`` when it is (item 1581). A cut run goes on in place only
    when the cause is not Dot's own and nothing it did may be done twice: the run may go on (:func:`run_refusal`), the
    program ended by itself and not by Dot after its output closed, it had said what the model has (``inited``, but for
    a program that was itself a continuation, which a new session replaces), its session is known, and no call of an
    outward action began since its last step (``outward_calls``: repeating it could send twice). A program cut after
    the model's finish is the run's end, not a cut to go on from, and the plan's limit, the run's limits, the silence
    watch and a program Dot refused at its init are other errors, which never come here."""
    refusal: str | None = run_refusal(cancelled=cancelled, continued=continued, allowed=allowed)
    if refusal is not None:
        return refusal
    if program.returncode is None:
        return "the program did not end by itself: Dot ended it"
    if not program.inited and not resumed:
        return "the program ended before it said what the model has"
    if not program.session_id:
        return "the program's session is not known"
    if outward_calls:
        calls: str = ", ".join(sorted(set(outward_calls)))
        return f"a call of an outward action was in flight ({calls}): repeating it could send twice"
    return None


def continue_after_s(continued: int) -> float:
    """The seconds Dot waits before it starts the program again, when the run was ``continued`` times already."""
    return CONTINUE_AFTER_S[min(continued, len(CONTINUE_AFTER_S) - 1)]


_CONTINUED: str = (
    "[system note] The program you run in was cut off at {clock} by something outside this run: not the owner, not "
    "Dot's limits. The result of your last tool call may be lost: look at the real state (git status, the files, the "
    "tabs, Dot's own lists) before you repeat anything, then go on from where your plan says."
)
_CONTINUED_FRESH: str = (
    "[system note] The program you run in was cut off at {clock} by something outside this run: not the owner, not "
    "Dot's limits. Its session could not be taken up again, so you are a new session of it: what it had done is above, "
    "from Dot's record of this run, and its last call may be lost. Look at the real state (git status, the files, the "
    "tabs, Dot's own lists) before you repeat anything, then go on from where the plan says."
)


def continuation_note(program: ProgramExit, fresh: bool) -> str:
    """What the program is told when it goes on after a cut (item 1581): when it was cut, that nothing in the run did
    it, that the result of its last call may be lost, to look at the real state before it repeats anything and to go
    on from where its plan says. ``fresh`` is a new session (the cut one was refused), which also says what it had
    done."""
    return (_CONTINUED_FRESH if fresh else _CONTINUED).format(clock=program.clock)


def continued_event_data(program: ProgramExit, attempt: int, allowed: int, fresh: bool, waited_s: float) -> JsonObject:
    """The data of the ``claude_code_continued`` event, which the run's page says as one line: when the program was
    cut off, how it ended (:meth:`ProgramExit.context`), which continuation this is of how many, and whether it is a new
    session."""
    return {**program.context(), "attempt": attempt, "of": allowed, "fresh_session": fresh, "waited_s": waited_s}
