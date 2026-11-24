"""Starts the code of a ``code_run`` in its sandbox, serves its bridge and watches it (item 1500).

One :func:`run_code` is one start of the interpreter under :func:`dotagent.codemode.sandbox.launch_words`, with a clean
environment, in a process group of its own, its output (stdout and stderr together, in the order they were written) kept
as a head and a tail, and the two pipes of the bridge handed to it as descriptors. What it does while the code runs:

- serves the bridge: each request is read as exactly one call (:mod:`dotagent.codemode.bridge`) and given to the
  ``handler`` (the loop's own path for a call, :mod:`dotagent.codemode.tool`), and its answer is written back; the next
  request is not read until the code has read the answer, so a code that never reads cannot make the daemon hold them;
- watches, many times a second, for the end of the code's time (the code's own: the time of a call it waits for is not
  counted, and nor is the time a call waits for the owner), its resident memory (``[code] max_mb``), what it printed,
  what it left in its scratch folder, the run's Stop, and a frame that is not a call or the call past ``max_calls``;
  when any of them says stop, the whole process group is killed, and the verdict is why;
- kills the group whatever else ends this function (the run's Stop that cancels it, an error): no process of the code
  outlives it.

A process that cannot be started is ``unstartable`` and nothing runs: there is no other way to start the code.
"""

import asyncio
import contextlib
import json
import math
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotagent.codemode.bridge import (
    MAX_FRAME_BYTES,
    BridgeAnswer,
    BridgeRequest,
    MalformedFrame,
    read_request,
    reply_frame,
)
from dotagent.codemode.sandbox import Interpreter, Layout, launch_words
from dotagent.codemode.watch import resident_bytes, scratch_usage
from dotagent.core.types import JsonObject
from dotagent.tools.env_filter import hides_secret
from dotagent.tools.shell import kill_group

# How the code ended: ``finished`` (exit 0) and ``failed`` (an error in it, or a signal the daemon did not send) are the
# code's own; the rest are the daemon's verdicts, each a kill of the whole process group.
type Ending = Literal[
    "finished", "failed", "timeout", "memory", "calls", "protocol", "stopped", "output", "scratch", "unstartable"
]

# How often the watch looks, in seconds.
POLL_S: float = 0.025
# How long the output the process left in its pipe is read for after it ended.
_DRAIN_GRACE_S: float = 1.0
# The most a code may print before it is stopped: what it prints is read and dropped, but an endless print is a loop.
MAX_OUTPUT_BYTES: int = 64 * 1024 * 1024
# What the scratch folder may hold: a code that fills the disk is stopped, and the folder is cleared before the next.
SCRATCH_MAX_BYTES: int = 256 * 1024 * 1024
SCRATCH_MAX_ENTRIES: int = 5000
# The largest file the code may write, and how many files it may have open: limits the kernel holds the process to.
FILE_MAX_BYTES: int = 64 * 1024 * 1024
OPEN_FILES_MAX: int = 256
# How often the scratch folder is measured, in seconds (a walk of up to :data:`SCRATCH_MAX_ENTRIES` entries).
_SCRATCH_EVERY_S: float = 1.0
# What the code's own process is told of the Mac, besides its two folders: the time zone and the language, if set and
# no secret (the same rule as for a command, :func:`dotagent.tools.env_filter.hides_secret`).
_PASSED_NAMES: tuple[str, ...] = ("TZ", "LANG", "LC_ALL", "LC_CTYPE")
_SNIPPETS: str = "snippets"


@dataclass(frozen=True)
class Limits:
    """What holds the code: ``seconds`` of its own time, ``memory_mb`` resident, ``calls`` of the bridge, and how many
    bytes of the head and the tail of its output are kept (``window_bytes`` each)."""

    seconds: float
    memory_mb: int
    calls: int
    window_bytes: int


@dataclass(frozen=True)
class RunPlan:
    """One start: the interpreter, the folders (whose ``tools`` hold ``launcher.py`` and ``dot_tools``), the code, its
    limits and ``serial``, which of the run's codes this is (the file it is kept in)."""

    interpreter: Interpreter
    layout: Layout
    code: str
    limits: Limits
    serial: int


@dataclass(frozen=True)
class Ended:
    """How a start ended. ``output`` is what the code printed, its head and its tail with a mark between them when it
    printed more than they hold (``printed_bytes`` says how much). ``exit_code`` is the process's own exit status and
    ``signal`` the signal that ended it, ``None`` for the other; ``detail`` says what the verdict names (the malformed
    frame, the memory, the file count). ``seconds`` is the wall time of the whole start, ``own_seconds`` the code's
    own."""

    ending: Ending
    output: str
    printed_bytes: int
    exit_code: int | None
    signal: int | None
    detail: str
    calls: int
    seconds: float
    own_seconds: float
    peak_mb: float


type Handler = Callable[[str, JsonObject], Awaitable[BridgeAnswer]]


class OutputWindow:
    """The first and the last bytes of a stream, with how many it dropped between them and how many it was given."""

    def __init__(self, head_bytes: int, tail_bytes: int) -> None:
        self._head_room: int = head_bytes
        self._tail_room: int = tail_bytes
        self._head: bytearray = bytearray()
        self._tail: bytearray = bytearray()
        self.dropped: int = 0
        self.total: int = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room: int = self._head_room - len(self._head)
        if room > 0:
            self._head.extend(chunk[:room])
            chunk = chunk[room:]
        if not chunk:
            return
        self._tail.extend(chunk)
        excess: int = len(self._tail) - self._tail_room
        if excess > 0:
            del self._tail[:excess]
            self.dropped += excess

    def render(self) -> str:
        head: str = self._head.decode("utf-8", errors="replace")
        tail: str = self._tail.decode("utf-8", errors="replace")
        if self.dropped:
            return f"{head}\n[... {self.dropped} bytes of output omitted ...]\n{tail}"
        return head + tail


class _Clock:
    """The code's own time: the wall time of the start less the time spent in the calls it waits for, which are the
    tools' own time and the owner's, and have limits of their own."""

    def __init__(self) -> None:
        self._started: float = time.monotonic()
        self._paused: float = 0.0
        self._pause_began: float | None = None
        self._calls_in_flight: int = 0

    def pause(self) -> None:
        self._calls_in_flight += 1
        if self._pause_began is None:
            self._pause_began = time.monotonic()

    def resume(self) -> None:
        self._calls_in_flight -= 1
        if self._calls_in_flight == 0 and self._pause_began is not None:
            self._paused += time.monotonic() - self._pause_began
            self._pause_began = None

    def own(self) -> float:
        now: float = time.monotonic()
        current: float = 0.0 if self._pause_began is None else now - self._pause_began
        return now - self._started - self._paused - current

    def wall(self) -> float:
        return time.monotonic() - self._started


class _State:
    """What the tasks of one start share."""

    def __init__(self, process: asyncio.subprocess.Process, window: OutputWindow) -> None:
        self.process: asyncio.subprocess.Process = process
        self.window: OutputWindow = window
        self.clock: _Clock = _Clock()
        self.verdict: Ending | None = None
        self.detail: str = ""
        self.calls: int = 0
        self.peak_bytes: int = 0

    def stop(self, verdict: Ending, detail: str = "") -> None:
        """Kill the code's whole process group, for ``verdict``: the first verdict stands."""
        if self.verdict is None:
            self.verdict = verdict
            self.detail = detail
        kill_group(self.process.pid)


def child_environment(layout: Layout, parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of the code: its two folders (``HOME`` and ``TMPDIR`` are the scratch folder), UTF-8, and the
    time zone and language of the Mac if they are set and hold no secret. Nothing else of the daemon's environment goes:
    no token, key, cookie or address with a password (:func:`dotagent.tools.env_filter.hides_secret`), no ``PATH``."""
    source: Mapping[str, str] = os.environ if parent is None else parent
    kept: dict[str, str] = {
        name: source[name] for name in _PASSED_NAMES if name in source and not hides_secret(name, source[name])
    }
    return {"LANG": "en_US.UTF-8", **kept, "HOME": str(layout.scratch), "TMPDIR": str(layout.scratch)}


def clear_scratch_if_full(layout: Layout) -> bool:
    """Empty the scratch folder when what an earlier start left in it is over its quota, so that the next code starts
    with room; whether it did. Blocking."""
    size, entries = scratch_usage(layout.scratch, SCRATCH_MAX_ENTRIES)
    if size <= SCRATCH_MAX_BYTES and entries <= SCRATCH_MAX_ENTRIES:
        return False
    for entry in layout.scratch.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    return True


def _write_code(plan: RunPlan) -> Path:
    """Keep the code in the tools folder, which the code itself can read and not change, and return its path."""
    folder: Path = plan.layout.tools / _SNIPPETS
    folder.mkdir(parents=True, exist_ok=True)
    path: Path = folder / f"{plan.serial}.py"
    path.write_text(plan.code, encoding="utf-8")
    return path


async def _pump(stream: asyncio.StreamReader, state: _State) -> None:
    """Read what the code prints, until it ends, and stop it when it prints without end."""
    while True:
        chunk: bytes = await stream.read(65536)
        if not chunk:
            return
        state.window.feed(chunk)
        if state.window.total > MAX_OUTPUT_BYTES:
            state.stop("output", f"it printed more than {MAX_OUTPUT_BYTES // (1024 * 1024)} MiB")
            return


class _Replies(asyncio.Protocol):
    """The write end of the reply pipe, which knows when what was written to it has been read: the buffer limits are one
    byte and none, so the transport pauses the moment it holds anything, and :attr:`free` is set again when the code has
    read it all (or the pipe closed). A reply is held by the daemon only until then."""

    def __init__(self) -> None:
        self.free: asyncio.Event = asyncio.Event()
        self.free.set()

    def pause_writing(self) -> None:
        self.free.clear()

    def resume_writing(self) -> None:
        self.free.set()

    def connection_lost(self, exc: Exception | None) -> None:
        self.free.set()


async def _serve(
    reader: asyncio.StreamReader,
    transport: asyncio.WriteTransport,
    replies: _Replies,
    handler: Handler,
    limits: Limits,
    state: _State,
) -> None:
    """Answer the calls of the code, one at a time, until it closes the bridge or something stops it."""
    while True:
        try:
            line: bytes = await reader.readline()
        except ValueError:
            state.stop("protocol", f"a frame of more than {MAX_FRAME_BYTES} bytes")
            return
        if not line:
            return
        try:
            request: BridgeRequest = read_request(line, state.calls + 1)
        except MalformedFrame as error:
            state.stop("protocol", error.reason)
            return
        state.calls += 1
        if state.calls > limits.calls:
            state.stop("calls", f"more than {limits.calls} tool calls")
            return
        state.clock.pause()
        try:
            answer: BridgeAnswer = await handler(request.tool, request.args)
        finally:
            state.clock.resume()
        if transport.is_closing():
            return
        transport.write(reply_frame(request.id, answer))
        await replies.free.wait()


async def _watch(plan: RunPlan, state: _State, cancel: asyncio.Event) -> None:
    """Look at the process until it ends: the run's Stop, the code's own time, its memory, its scratch folder."""
    limit_bytes: int = plan.limits.memory_mb * 1024 * 1024
    scratch_looked: float = 0.0
    while state.process.returncode is None:
        await asyncio.sleep(POLL_S)
        if cancel.is_set():
            state.stop("stopped", "the run was stopped")
            return
        if state.clock.own() > plan.limits.seconds:
            state.stop("timeout", f"{plan.limits.seconds:g} s of its own time")
            return
        held: int | None = resident_bytes(state.process.pid)
        if held is not None:
            state.peak_bytes = max(state.peak_bytes, held)
            if held > limit_bytes:
                state.stop("memory", f"{held // (1024 * 1024)} MB held, the limit is {plan.limits.memory_mb} MB")
                return
        now: float = time.monotonic()
        if now - scratch_looked >= _SCRATCH_EVERY_S:
            scratch_looked = now
            size, entries = await asyncio.to_thread(scratch_usage, plan.layout.scratch, SCRATCH_MAX_ENTRIES)
            if size > SCRATCH_MAX_BYTES or entries > SCRATCH_MAX_ENTRIES:
                state.stop(
                    "scratch",
                    f"its scratch folder holds {size // (1024 * 1024)} MB in over {min(entries, SCRATCH_MAX_ENTRIES)} "
                    f"entries; the limits are {SCRATCH_MAX_BYTES // (1024 * 1024)} MB and {SCRATCH_MAX_ENTRIES}",
                )
                return


async def _cancelled(tasks: tuple[asyncio.Task[None], ...]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _ended(state: _State, wall: float) -> Ended:
    code: int | None = state.process.returncode
    signalled: int | None = -code if code is not None and code < 0 else None
    exit_code: int | None = code if code is not None and code >= 0 else None
    ending: Ending = state.verdict or ("finished" if code == 0 else "failed")
    return Ended(
        ending=ending,
        output=state.window.render(),
        printed_bytes=state.window.total,
        exit_code=exit_code,
        signal=signalled,
        detail=state.detail,
        calls=state.calls,
        seconds=wall,
        own_seconds=max(state.clock.own(), 0.0),
        peak_mb=state.peak_bytes / (1024 * 1024),
    )


async def run_code(plan: RunPlan, handler: Handler, cancel: asyncio.Event) -> Ended:
    """Start the code of ``plan`` in its sandbox and see it to its end. ``handler`` answers each call of the code;
    ``cancel`` is the run's Stop. Whatever ends this function, the code's process group does not outlive it."""
    layout: Layout = plan.layout
    code_file: Path = _write_code(plan)
    requests_read, requests_write = os.pipe()
    replies_read, replies_write = os.pipe()
    limits_json: str = json.dumps(
        {
            "cpu_s": math.ceil(plan.limits.seconds) + 5,
            "fsize_bytes": FILE_MAX_BYTES,
            "nofile": OPEN_FILES_MAX,
        }
    )
    words: tuple[str, ...] = launch_words(
        plan.interpreter,
        layout,
        layout.tools / "launcher.py",
        (
            str(requests_write),
            str(replies_read),
            str(layout.scratch),
            str(layout.tools),
            str(code_file),
            limits_json,
        ),
    )
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *words,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=child_environment(layout),
            cwd=layout.scratch,
            pass_fds=(requests_write, replies_read),
            start_new_session=True,
        )
    except OSError as error:
        for descriptor in (requests_read, requests_write, replies_read, replies_write):
            os.close(descriptor)
        return Ended("unstartable", "", 0, None, None, f"{type(error).__name__}: {error}", 0, 0.0, 0.0, 0.0)
    # The code has its ends; these are the daemon's copies of them, and holding them would keep the bridge open.
    os.close(requests_write)
    os.close(replies_read)
    stream: asyncio.StreamReader | None = process.stdout
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    reader: asyncio.StreamReader = asyncio.StreamReader(limit=MAX_FRAME_BYTES)
    read_transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(requests_read, "rb", buffering=0)
    )
    replies: _Replies = _Replies()
    write_transport, _ = await loop.connect_write_pipe(lambda: replies, os.fdopen(replies_write, "wb", buffering=0))
    write_transport.set_write_buffer_limits(high=1, low=0)
    state: _State = _State(process, OutputWindow(plan.limits.window_bytes, plan.limits.window_bytes))
    tasks: tuple[asyncio.Task[None], ...] = ()
    try:
        if stream is not None:
            tasks = (
                asyncio.create_task(_pump(stream, state)),
                asyncio.create_task(_serve(reader, write_transport, replies, handler, plan.limits, state)),
                asyncio.create_task(_watch(plan, state, cancel)),
            )
        await process.wait()
        if tasks:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[0]), timeout=_DRAIN_GRACE_S)
        return _ended(state, state.clock.wall())
    finally:
        if process.returncode is None:
            kill_group(process.pid)
        await _cancelled(tasks)
        read_transport.close()
        write_transport.close()
        with contextlib.suppress(ProcessLookupError):
            await process.wait()
        # A group that outlived its leader (nothing can start one: a thread's work is not one) is gone with it.
        kill_group(process.pid)
