"""Programs on a pseudo-terminal: one that asks questions, a REPL, a server that must stay up while the agent works.

A session is one command run by zsh with a pseudo-terminal as its controlling terminal, stdin, stdout and stderr, in a
process group of its own, with the environment of the shell tool. What it prints is kept until it is read, the newest
``_KEPT_CHARS`` of it, and read as plain text: escape sequences are removed and no terminal emulator runs, so a
full-screen program (vim, htop) shows as garbled text. The sessions belong to a thread: at most ``[shell]
terminal_sessions`` at a time, each closed once it was not used for ``terminal_idle_s``, and all of them when the daemon
stops. The end of a run closes none: a server outlives the run that started it.

A command in the background (item 229) is a session the run left to run on its own, as an eval or a long build: it is
not closed for being left unread while it runs, and when it ends by itself its chat is told (:class:`TerminalWatcher`,
the run manager), with no word of what it printed: that is read with ``terminal_read``, as untrusted. A session closed
by the run, by the owner or by the daemon's stop tells nothing.
"""

import asyncio
import codecs
import contextlib
import errno
import fcntl
import os
import pty
import re
import signal
import termios
import time
from pathlib import Path
from typing import Protocol

from dotagent.core.errors import TerminalInputFullError, ToolExecutionError, ToolInputError
from dotagent.core.logs import get_logger
from dotagent.core.text import duration_text, truncate_middle
from dotagent.policy.shell import typed_lines
from dotagent.tools.shell import kill_group

log = get_logger(__name__)

_ANSI: re.Pattern[str] = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
# A prompt for a secret on the last line of the output: what is typed next is a password, a passphrase or a PIN.
_SECRET_PROMPT: re.Pattern[str] = re.compile(r"(?i)\b(?:password|passphrase|passcode|pin)\b[^\n]{0,60}:[ \t]*$")
_KEPT_CHARS: int = 192 * 1024
_RECENT_CHARS: int = 2048
_READ_BYTES: int = 65536
# The most that may wait to be typed into a program that has not read it: a pseudo-terminal takes 1022 bytes ahead of
# the program, and a blocking write of more held the daemon for as long as the program did not read (measured
# 2026-10-15), so the rest waits here and is written as the program reads. One send is at most 10_000 characters
# (``terminal_send``), so a send into an empty wait always fits.
_PENDING_INPUT_MAX: int = 64 * 1024
# A read returns once the program has printed nothing for this long, so that a burst of output comes back whole.
_QUIET_S: float = 0.3
_CLOSE_GRACE_S: float = 1.0
_REAP_EVERY_S: float = 60.0
# The most of a command that the notice of its end names.
_NOTICE_COMMAND_CHARS: int = 300
# The basic REPL of Python 3.13 instead of the one that needs a terminal with cursor control.
_TERMINAL_ENV: dict[str, str] = {"TERM": "dumb", "PYTHON_BASIC_REPL": "1"}


def plain_text(raw: str) -> str:
    """What a terminal shows of ``raw``, without a terminal: escape sequences removed, every line end a ``\\n``."""
    return _ANSI.sub("", raw).replace("\r\n", "\n").replace("\r", "")


def asks_for_secret(recent: str) -> bool:
    """Whether the last line of the ``recent`` output prompts for a password, a passphrase or a PIN."""
    return _SECRET_PROMPT.search(plain_text(recent)) is not None


def ended_notice(session_id: str, command: str, exit_status: int, seconds: float) -> str:
    """What the chat of a command in the background that ended is told (item 229): the session, its exit status, how
    long it ran and the command, as Dot saw them, on the first line, and none of what it printed, which is untrusted and
    read with ``terminal_read``."""
    shown: str = truncate_middle(" ".join(command.split()), _NOTICE_COMMAND_CHARS)
    return (
        f"The command in the background {session_id} ended with exit {exit_status} after {duration_text(seconds)}: "
        f"{shown}\nRead what it printed with terminal_read {session_id}, then go on with what it was started for."
    )


def _controlling_terminal() -> None:
    """In the child, before the command starts: the pseudo-terminal on its stdin becomes its controlling terminal, so
    that programs that ask through /dev/tty (ssh, sudo) reach it."""
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class TerminalSession:
    """One program on a pseudo-terminal, from ``terminal_open`` to its close."""

    def __init__(
        self,
        session_id: str,
        command: str,
        cwd: Path,
        sandboxed: bool,
        background: bool,
        process: asyncio.subprocess.Process,
        master: int,
        follower: int,
    ) -> None:
        self.id: str = session_id
        self.command: str = command
        self.cwd: Path = cwd
        # Whether the program started in the sandbox of a run (:mod:`dotagent.policy.sandbox`).
        self.sandboxed: bool = sandboxed
        # Whether the run left it to run on its own (item 229): it is not closed while it runs for being left unread,
        # and its end is told to its chat.
        self.background: bool = background
        # When it started, for the pages (seconds since the epoch).
        self.started_at: float = time.time()
        self._process: asyncio.subprocess.Process = process
        self._master: int = master
        # Dot's own copy of the terminal's other end, held until the program has exited (:meth:`release_follower`).
        # When the program's exit closed that end last, macOS kept what it printed last from the read side, and a
        # program that leads its session waited in its exit for that output to drain, for good.
        self._follower: int | None = follower
        # What was typed and the program has not read yet, and whether the loop is asked to say when it takes more.
        self._pending: bytearray = bytearray()
        self._writing: bool = False
        self._decoder: codecs.IncrementalDecoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._unread: str = ""
        self._dropped: int = 0
        self._recent: str = ""
        # What was typed since the line was last empty: the start of the line the next text completes, or None once keys
        # a line editor acts upon (an arrow, Tab, a Ctrl- shortcut) made it unknown.
        self.pending_line: str | None = ""
        # Whether the program is still where it started: once a line went in, a shell may have changed directory.
        self.cwd_known: bool = True
        self.used_at: float = time.monotonic()
        self.ended: bool = False
        # Set once the program is gone, whether it ended by itself or the session was closed.
        self.finished: asyncio.Event = asyncio.Event()
        # Whether the session is being closed by hand: by the run, the owner, its idleness or the daemon's stop.
        self.closing: bool = False
        # The program's exit status, once it ended by itself.
        self.exit_status: int | None = None
        # The message of the chat's queue that told the end of this command (item 427), until a run has read the end
        # itself: that run has the news, and the message is taken back out.
        self.told: str | None = None
        # A run read the program's end and closed the session (:meth:`Terminals.close_read`), as opposed to a close by
        # the owner, the run's own ``terminal_close``, the idle reaper or the daemon's stop, which read nothing.
        self.end_read: bool = False
        self._arrived: asyncio.Event = asyncio.Event()
        # Neither a read nor a write of the terminal waits in the event loop: what is not there yet comes with the
        # loop's next word on the descriptor.
        os.set_blocking(master, False)
        asyncio.get_running_loop().add_reader(master, self._on_readable)

    @property
    def awaiting_secret(self) -> bool:
        """Whether the program's last line asks for a password: what is typed next is a secret."""
        return asks_for_secret(self._recent)

    def _on_readable(self) -> None:
        self._take()

    def _take(self) -> bool:
        """Take what the terminal holds now, and say whether it held something; its end (EIO, or a read of nothing)
        ends the session."""
        try:
            data: bytes = os.read(self._master, _READ_BYTES)
        except BlockingIOError:
            # The loop spoke too soon: nothing to read after all, and nothing ended.
            return False
        except OSError as error:
            # EIO is how the terminal says that nothing holds its other end any more: the program is gone.
            if error.errno != errno.EIO:
                log.warning("terminal_read_failed", session=self.id, errno=error.errno, error=str(error))
            data = b""
        if data:
            text: str = self._decoder.decode(data)
            self._unread += text
            if len(self._unread) > _KEPT_CHARS:
                self._dropped += len(self._unread) - _KEPT_CHARS
                self._unread = self._unread[-_KEPT_CHARS:]
            self._recent = (self._recent + text)[-_RECENT_CHARS:]
        else:
            self._drop_pending()
            asyncio.get_running_loop().remove_reader(self._master)
            self.ended = True
            self.finished.set()
        self._arrived.set()
        return bool(data)

    async def release_follower(self) -> None:
        """Once the program has exited: take what it printed last, then close Dot's copy of the terminal's other end,
        so that the read side comes to the end once nothing else holds it. A session closed by hand takes nothing
        more. The close runs off the event loop: a last close of that end with output still waiting is held by macOS
        until the output is read, which the loop goes on doing meanwhile."""
        if self._follower is None:
            return
        if not self.closing:
            # What is kept is the newest _KEPT_CHARS, so a program's descendants that still print cannot hold the loop.
            for _ in range(_KEPT_CHARS // _READ_BYTES + 1):
                if self.ended or not self._take():
                    break
        follower: int = self._follower
        self._follower = None
        await asyncio.to_thread(os.close, follower)

    def _ended_error(self) -> ToolExecutionError:
        return ToolExecutionError(
            "the program of this terminal session has ended: terminal_read returns what it printed last and closes "
            "the session",
            {"session": self.id},
        )

    def send(self, text: str) -> None:
        """Type ``text``; a line end in it presses Enter. What the program has not read yet waits here and is written
        as it reads (:meth:`_on_writable`), so a program that stopped reading holds nothing but its own session; one
        that left more than :data:`_PENDING_INPUT_MAX` unread is typed nothing more until it reads."""
        if self.ended:
            raise self._ended_error()
        data: bytes = text.encode()
        waiting: int = len(self._pending)
        if waiting + len(data) > _PENDING_INPUT_MAX:
            raise TerminalInputFullError(
                f"the program of this terminal session has not read {waiting} bytes typed into it before: read its "
                "output with terminal_read and wait for it to take input, or close the session",
                {"session": self.id, "waiting_bytes": waiting, "limit_bytes": _PENDING_INPUT_MAX},
            )
        self.used_at = time.monotonic()
        self._pending.extend(data)
        try:
            self._write_pending()
        except OSError as error:
            self._drop_pending()
            if error.errno == errno.EIO:
                raise self._ended_error() from error
            raise ToolExecutionError(
                "typing into the terminal session failed",
                {"session": self.id, "errno": error.errno, "error": str(error)},
            ) from error
        lines, self.pending_line = typed_lines(self.pending_line, text)
        self.cwd_known = self.cwd_known and not lines

    def _write_pending(self) -> None:
        """Write what waits, as much as the terminal takes now; for the rest the loop says when it takes more."""
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        while self._pending:
            try:
                written: int = os.write(self._master, self._pending)
            except BlockingIOError:
                break
            del self._pending[:written]
        if self._pending and not self._writing:
            loop.add_writer(self._master, self._on_writable)
            self._writing = True
        elif not self._pending and self._writing:
            loop.remove_writer(self._master)
            self._writing = False

    def _on_writable(self) -> None:
        try:
            self._write_pending()
        except OSError as error:
            # EIO: the program is gone, which the read side says and ends the session on.
            if error.errno != errno.EIO:
                log.warning("terminal_write_failed", session=self.id, errno=error.errno, error=str(error))
            self._drop_pending()

    def _drop_pending(self) -> None:
        """Forget what waited to be typed, and stop asking the loop when the terminal takes more."""
        self._pending.clear()
        if self._writing:
            asyncio.get_running_loop().remove_writer(self._master)
            self._writing = False

    async def read(self, wait_s: float) -> str:
        """What the program printed since the last read, as plain text: after waiting up to ``wait_s`` for something
        new, until it has printed nothing for ``_QUIET_S``."""
        self.used_at = time.monotonic()
        deadline: float = time.monotonic() + wait_s
        while not self.ended:
            remaining: float = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._arrived.clear()
            try:
                await asyncio.wait_for(self._arrived.wait(), min(_QUIET_S, remaining) if self._unread else remaining)
            except TimeoutError:
                break
        text: str = plain_text(self._unread)
        dropped: int = self._dropped
        self._unread = ""
        self._dropped = 0
        self.used_at = time.monotonic()
        return f"[... {dropped} characters that were not read in time are left out ...]\n{text}" if dropped else text

    async def exit_code(self) -> int:
        """The exit status of the program, once it has ended."""
        return await self._process.wait()

    async def close(self) -> None:
        """End the program and everything it started in its process group, and release the terminal, also when the
        close is cancelled while it waits for the program (item 351: the terminal's file stayed open then)."""
        self.closing = True
        try:
            if self._process.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self._process.pid, signal.SIGTERM)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._process.wait(), _CLOSE_GRACE_S)
            kill_group(self._process.pid)
            await self._process.wait()
        finally:
            if self._process.returncode is None:
                kill_group(self._process.pid)
            self._drop_pending()
            if not self.ended:
                asyncio.get_running_loop().remove_reader(self._master)
                self.ended = True
            self.finished.set()
            # The terminal's own end first: once it is closed, a close of the other end waits for no reader.
            os.close(self._master)
            if self._follower is not None:
                os.close(self._follower)
                self._follower = None


class TerminalWatcher(Protocol):
    """Who hears of the daemon's terminal sessions (item 229), the run manager: the pages follow a chat's sessions as
    they change, and the end of a command in the background is told to its chat."""

    async def sessions_changed(self, thread_id: str) -> None:
        """A session of the chat ``thread_id`` opened, ended or was closed."""
        ...

    async def command_ended(self, thread_id: str, session: TerminalSession) -> None:
        """The program of ``session``, a command in the background of the chat ``thread_id``, ended by itself."""
        ...

    async def command_read(self, thread_id: str, session: TerminalSession) -> None:
        """A run read the end of ``session``, a command in the background of the chat ``thread_id``, and closed it."""
        ...


class Terminals:
    """The terminal sessions of the daemon, by thread."""

    def __init__(self, limit: int, idle_s: float, env: dict[str, str]) -> None:
        self._limit: int = limit
        self._idle_s: float = idle_s
        self._env: dict[str, str] = {**env, **_TERMINAL_ENV}
        self._sessions: dict[str, dict[str, TerminalSession]] = {}
        self._opened: int = 0
        self._reaper: asyncio.Task[None] | None = None
        # What waits for the end of each session's program, by session.
        self._follows: dict[str, asyncio.Task[None]] = {}
        self._watcher: TerminalWatcher | None = None

    def watch(self, watcher: TerminalWatcher) -> None:
        """Have ``watcher`` hear of the sessions from now on, in place of the one before."""
        self._watcher = watcher

    def unwatch(self, watcher: TerminalWatcher) -> None:
        """Stop telling ``watcher``, if it is the one that hears."""
        if self._watcher is watcher:
            self._watcher = None

    async def open(
        self, thread_id: str, command: str, confinement: tuple[str, ...], cwd: Path, background: bool
    ) -> TerminalSession:
        """Start ``command`` in zsh on a terminal of its own, started by ``confinement`` (the words of the run's
        sandbox, or none); ``background`` for a command the run leaves to run on its own (item 229), which a process
        that tells no chat of its end (a ``dotx`` command, which ends with its run) refuses."""
        if background and self._watcher is None:
            raise ToolInputError(
                "This process tells no chat when a command ends (a dotx command ends with its run): run it with shell, "
                "or in a terminal session you read.",
                {"command": command},
            )
        mine: dict[str, TerminalSession] = self._sessions.setdefault(thread_id, {})
        if len(mine) >= self._limit:
            raise ToolInputError(
                f"This chat has {len(mine)} terminal sessions open ({', '.join(mine)}), the most it may have: close "
                "one with terminal_close first.",
                {"open": list(mine), "limit": self._limit},
            )
        master, follower = pty.openpty()
        started: bool = False
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                *confinement,
                "/bin/zsh",
                "-c",
                command,
                stdin=follower,
                stdout=follower,
                stderr=follower,
                cwd=str(cwd),
                env=self._env,
                start_new_session=True,
                preexec_fn=_controlling_terminal,
            )
            started = True
        except OSError as error:
            raise ToolExecutionError(
                "the terminal session could not start", {"command": command, "cwd": str(cwd), "error": str(error)}
            ) from error
        finally:
            if not started:
                os.close(master)
                os.close(follower)
        self._opened += 1
        session: TerminalSession = TerminalSession(
            f"t{self._opened}", command, cwd, bool(confinement), background, process, master, follower
        )
        mine[session.id] = session
        self._follows[session.id] = asyncio.create_task(self._follow(thread_id, session))
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap())
        log.info("terminal_opened", thread_id=thread_id, session=session.id, command=command, background=background)
        await self._changed(thread_id)
        return session

    def sessions(self, thread_id: str) -> list[TerminalSession]:
        """The sessions of the chat ``thread_id``, in the order they opened."""
        return list(self._sessions.get(thread_id, {}).values())

    def get(self, thread_id: str, session_id: str) -> TerminalSession:
        mine: dict[str, TerminalSession] = self._sessions.get(thread_id, {})
        session: TerminalSession | None = mine.get(session_id)
        if session is None:
            raise ToolInputError(
                f"This chat has no terminal session {session_id}; open ones: {', '.join(mine) or 'none'}.",
                {"session": session_id, "open": list(mine)},
            )
        return session

    async def close(self, thread_id: str, session_id: str) -> None:
        session: TerminalSession = self.get(thread_id, session_id)
        del self._sessions[thread_id][session_id]
        await session.close()
        log.info("terminal_closed", thread_id=thread_id, session=session_id)
        await self._changed(thread_id)

    async def close_read(self, thread_id: str, session_id: str) -> None:
        """Close a session whose program's end a run has read (item 427): a command in the background that ended is not
        told to the chat again, or is taken back from it if it was told already
        (:meth:`TerminalWatcher.command_read`)."""
        session: TerminalSession = self.get(thread_id, session_id)
        session.end_read = True
        await self.close(thread_id, session_id)
        if session.background and self._watcher is not None:
            await self._watcher.command_read(thread_id, session)

    async def _changed(self, thread_id: str) -> None:
        if self._watcher is not None:
            await self._watcher.sessions_changed(thread_id)

    async def _follow(self, thread_id: str, session: TerminalSession) -> None:
        """Wait for the program of ``session`` to end, and for the session to take what it printed last
        (:meth:`TerminalSession.release_follower`). One that ended by itself is kept ``idle_s`` more for that to be
        read, and the watcher hears of it: of a command in the background, its chat is told. A session closed by hand
        tells nothing more. A watcher that fails is logged: nothing awaits this."""
        try:
            await session.exit_code()
            await session.release_follower()
            await session.finished.wait()
            if session.closing:
                return
            session.exit_status = await session.exit_code()
            session.used_at = time.monotonic()
            log.info(
                "terminal_ended",
                thread_id=thread_id,
                session=session.id,
                background=session.background,
                exit_status=session.exit_status,
            )
            watcher: TerminalWatcher | None = self._watcher
            if watcher is None:
                return
            await watcher.sessions_changed(thread_id)
            if session.background:
                await watcher.command_ended(thread_id, session)
        except Exception:
            log.exception("terminal_end_not_told", thread_id=thread_id, session=session.id)
        finally:
            self._follows.pop(session.id, None)

    async def _reap(self) -> None:
        """Close the sessions that were not used for ``idle_s``, as long as the daemon runs; a command in the
        background is left alone while it runs. A session that fails to close is logged, and the others are still
        closed."""
        while True:
            await asyncio.sleep(min(_REAP_EVERY_S, self._idle_s / 2))
            now: float = time.monotonic()
            for thread_id, mine in list(self._sessions.items()):
                idle: list[TerminalSession] = [
                    session
                    for session in mine.values()
                    if now - session.used_at > self._idle_s and (session.ended or not session.background)
                ]
                for session in idle:
                    del mine[session.id]
                    try:
                        await session.close()
                    except Exception:
                        log.exception("terminal_idle_close_failed", thread_id=thread_id, session=session.id)
                        continue
                    log.info("terminal_idle_closed", thread_id=thread_id, session=session.id, command=session.command)
                if idle:
                    await self._changed(thread_id)

    async def aclose(self) -> None:
        """Close every session, as the daemon stops: no watcher hears of it."""
        self._watcher = None
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        follows: list[asyncio.Task[None]] = list(self._follows.values())
        for follow in follows:
            follow.cancel()
        for follow in follows:
            with contextlib.suppress(asyncio.CancelledError):
                await follow
        sessions: list[TerminalSession] = [session for mine in self._sessions.values() for session in mine.values()]
        self._sessions.clear()
        # Every session is closed even when one fails to; the error is raised once all were tried.
        async with contextlib.AsyncExitStack() as closing:
            for session in sessions:
                closing.push_async_callback(session.close)
