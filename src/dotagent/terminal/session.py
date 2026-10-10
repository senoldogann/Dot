"""Programs on a pseudo-terminal: one that asks questions, a REPL, a server that must stay up while the agent works.

A session is one command run by zsh with a pseudo-terminal as its controlling terminal, stdin, stdout and stderr, in a
process group of its own, with the environment of the shell tool. What it prints is kept until it is read, the newest
``_KEPT_CHARS`` of it, and read as plain text: escape sequences are removed and no terminal emulator runs, so a
full-screen program (vim, htop) shows as garbled text. The sessions belong to a thread: at most ``[shell]
terminal_sessions`` at a time, each closed once it was not used for ``terminal_idle_s``, and all of them when the daemon
stops. The end of a run closes none: a server outlives the run that started it.
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

from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.core.logs import get_logger
from dotagent.tools.shell import kill_group

log = get_logger(__name__)

_ANSI: re.Pattern[str] = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
# A prompt for a secret on the last line of the output: what is typed next is a password, a passphrase or a PIN.
_SECRET_PROMPT: re.Pattern[str] = re.compile(r"(?i)\b(?:password|passphrase|passcode|pin)\b[^\n]{0,60}:[ \t]*$")
_KEPT_CHARS: int = 192 * 1024
_RECENT_CHARS: int = 2048
_READ_BYTES: int = 65536
# A read returns once the program has printed nothing for this long, so that a burst of output comes back whole.
_QUIET_S: float = 0.3
_CLOSE_GRACE_S: float = 1.0
_REAP_EVERY_S: float = 60.0
# The basic REPL of Python 3.13 instead of the one that needs a terminal with cursor control.
_TERMINAL_ENV: dict[str, str] = {"TERM": "dumb", "PYTHON_BASIC_REPL": "1"}


def plain_text(raw: str) -> str:
    """What a terminal shows of ``raw``, without a terminal: escape sequences removed, every line end a ``\\n``."""
    return _ANSI.sub("", raw).replace("\r\n", "\n").replace("\r", "")


def asks_for_secret(recent: str) -> bool:
    """Whether the last line of the ``recent`` output prompts for a password, a passphrase or a PIN."""
    return _SECRET_PROMPT.search(plain_text(recent)) is not None


def typed_lines(pending: str, text: str) -> tuple[tuple[str, ...], str]:
    """The lines that typing ``text`` completes, the first one starting with ``pending`` (what was typed since the last
    Enter), and what is left on the line after it."""
    *lines, rest = (pending + text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return tuple(lines), rest


def _controlling_terminal() -> None:
    """In the child, before the command starts: the pseudo-terminal on its stdin becomes its controlling terminal, so
    that programs that ask through /dev/tty (ssh, sudo) reach it."""
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class TerminalSession:
    """One program on a pseudo-terminal, from ``terminal_open`` to its close."""

    def __init__(
        self, session_id: str, command: str, cwd: Path, process: asyncio.subprocess.Process, master: int
    ) -> None:
        self.id: str = session_id
        self.command: str = command
        self.cwd: Path = cwd
        self._process: asyncio.subprocess.Process = process
        self._master: int = master
        self._decoder: codecs.IncrementalDecoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._unread: str = ""
        self._dropped: int = 0
        self._recent: str = ""
        # What was typed since the last Enter: the start of the line the next text completes.
        self.pending_line: str = ""
        self.used_at: float = time.monotonic()
        self.ended: bool = False
        self._arrived: asyncio.Event = asyncio.Event()
        asyncio.get_running_loop().add_reader(master, self._on_readable)

    @property
    def awaiting_secret(self) -> bool:
        """Whether the program's last line asks for a password: what is typed next is a secret."""
        return asks_for_secret(self._recent)

    def _on_readable(self) -> None:
        try:
            data: bytes = os.read(self._master, _READ_BYTES)
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
            asyncio.get_running_loop().remove_reader(self._master)
            self.ended = True
        self._arrived.set()

    def send(self, text: str) -> None:
        """Type ``text``; a line end in it presses Enter."""
        if self.ended:
            raise ToolExecutionError(
                "the program of this terminal session has ended: terminal_read returns what it printed last and "
                "closes the session",
                {"session": self.id},
            )
        self.used_at = time.monotonic()
        os.write(self._master, text.encode())
        _, self.pending_line = typed_lines(self.pending_line, text)

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
        """End the program and everything it started in its process group, and release the terminal."""
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._process.pid, signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process.wait(), _CLOSE_GRACE_S)
        kill_group(self._process.pid)
        await self._process.wait()
        if not self.ended:
            asyncio.get_running_loop().remove_reader(self._master)
            self.ended = True
        os.close(self._master)


class Terminals:
    """The terminal sessions of the daemon, by thread."""

    def __init__(self, limit: int, idle_s: float, env: dict[str, str]) -> None:
        self._limit: int = limit
        self._idle_s: float = idle_s
        self._env: dict[str, str] = {**env, **_TERMINAL_ENV}
        self._sessions: dict[str, dict[str, TerminalSession]] = {}
        self._opened: int = 0
        self._reaper: asyncio.Task[None] | None = None

    async def open(self, thread_id: str, command: str, cwd: Path) -> TerminalSession:
        mine: dict[str, TerminalSession] = self._sessions.setdefault(thread_id, {})
        if len(mine) >= self._limit:
            raise ToolInputError(
                f"This chat has {len(mine)} terminal sessions open ({', '.join(mine)}), the most it may have: close "
                "one with terminal_close first.",
                {"open": list(mine), "limit": self._limit},
            )
        master, slave = pty.openpty()
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                "/bin/zsh",
                "-c",
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=str(cwd),
                env=self._env,
                start_new_session=True,
                preexec_fn=_controlling_terminal,
            )
        except OSError as error:
            os.close(master)
            raise ToolExecutionError(
                "the terminal session could not start", {"command": command, "cwd": str(cwd), "error": str(error)}
            ) from error
        finally:
            os.close(slave)
        self._opened += 1
        session: TerminalSession = TerminalSession(f"t{self._opened}", command, cwd, process, master)
        mine[session.id] = session
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap())
        log.info("terminal_opened", thread_id=thread_id, session=session.id, command=command)
        return session

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

    async def _reap(self) -> None:
        """Close the sessions that were not used for ``idle_s``, as long as the daemon runs."""
        while True:
            await asyncio.sleep(min(_REAP_EVERY_S, self._idle_s / 2))
            now: float = time.monotonic()
            for thread_id, mine in list(self._sessions.items()):
                for session in [session for session in mine.values() if now - session.used_at > self._idle_s]:
                    del mine[session.id]
                    await session.close()
                    log.info("terminal_idle_closed", thread_id=thread_id, session=session.id, command=session.command)

    async def aclose(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        sessions: list[TerminalSession] = [session for mine in self._sessions.values() for session in mine.values()]
        self._sessions.clear()
        for session in sessions:
            await session.close()
