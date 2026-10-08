"""Shell tool: run commands in a clean, non-interactive zsh with bounded output and a hard timeout."""

import asyncio
import contextlib
import os
import signal
import time
from collections import deque
from pathlib import Path

from pydantic import Field

from dotagent.config import ShellConfig
from dotagent.core.errors import ToolExecutionError
from dotagent.core.text import truncate_middle
from dotagent.policy.paths import classify_path, expand_path
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Flag
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool
from dotagent.tools.context import ToolContext

_HEAD_BYTES: int = 64 * 1024
_TAIL_BYTES: int = 192 * 1024
_DRAIN_GRACE_S: float = 0.4
_SECRET_NAME_PARTS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
_FIXED_ENV: dict[str, str] = {
    "TERM": "dumb",
    "NO_COLOR": "1",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "HOMEBREW_NO_AUTO_UPDATE": "1",
    "HOMEBREW_NO_ENV_HINTS": "1",
    "PYTHONUNBUFFERED": "1",
}


def child_env(login_env: dict[str, str], pass_env: list[str]) -> dict[str, str]:
    """Environment for tool processes: the login environment minus anything that looks like a secret."""
    kept: dict[str, str] = {
        name: value
        for name, value in login_env.items()
        if name in pass_env or not any(part in name.upper() for part in _SECRET_NAME_PARTS)
    }
    return {**kept, **_FIXED_ENV}


async def read_login_env(timeout_s: float) -> dict[str, str]:
    """Capture the environment a login zsh would have (PATH from Homebrew, uv, ...), once."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "/bin/zsh",
        "-lc",
        "env -0",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        process.kill()
        raise ToolExecutionError("reading the login shell environment timed out", {"timeout_s": timeout_s}) from exc
    if process.returncode != 0:
        raise ToolExecutionError(
            "reading the login shell environment failed",
            {"returncode": process.returncode, "stderr": stderr.decode("utf-8", errors="replace")[:500]},
        )
    env: dict[str, str] = {}
    for entry in stdout.decode("utf-8", errors="replace").split("\0"):
        name, separator, value = entry.partition("=")
        if separator and name:
            env[name] = value
    return env


class OutputWindow:
    """Keeps the first and last bytes of a stream so huge outputs stay bounded yet show start and end."""

    def __init__(self) -> None:
        self._head: bytearray = bytearray()
        self._tail: deque[bytes] = deque()
        self._tail_size: int = 0
        self.dropped: int = 0

    def feed(self, chunk: bytes) -> None:
        room: int = _HEAD_BYTES - len(self._head)
        if room > 0:
            self._head.extend(chunk[:room])
            chunk = chunk[room:]
        if not chunk:
            return
        self._tail.append(chunk)
        self._tail_size += len(chunk)
        while self._tail_size > _TAIL_BYTES:
            removed: bytes = self._tail.popleft()
            self._tail_size -= len(removed)
            self.dropped += len(removed)

    def render(self) -> str:
        head: str = self._head.decode("utf-8", errors="replace")
        tail: str = b"".join(self._tail).decode("utf-8", errors="replace")
        if self.dropped:
            return f"{head}\n[... {self.dropped} bytes omitted ...]\n{tail}"
        return head + tail


async def _pump(stream: asyncio.StreamReader, window: OutputWindow) -> None:
    while True:
        chunk: bytes = await stream.read(65536)
        if not chunk:
            return
        window.feed(chunk)


def kill_group(pid: int) -> None:
    """SIGKILL the process group led by ``pid``. A group with nothing left alive needs no signal: Linux reports it as
    ESRCH, macOS as EPERM while its exited members are not reaped yet. Raising either would replace a cancellation that
    arrives just as the command ends."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def run_command(
    command: str, cwd: Path, env: dict[str, str], timeout_s: float
) -> tuple[int | None, str, float, bool]:
    """Run ``command``; returns ``(exit code or None if killed, output, seconds, timed out)``."""
    started: float = time.monotonic()
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "/bin/zsh",
        "-c",
        command,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    window: OutputWindow = OutputWindow()
    stream: asyncio.StreamReader | None = process.stdout
    if stream is None:
        raise ToolExecutionError("subprocess has no stdout pipe", {"command": command})
    reader: asyncio.Task[None] = asyncio.create_task(_pump(stream, window))
    timed_out: bool = False
    try:
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            kill_group(process.pid)
            await process.wait()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader), timeout=_DRAIN_GRACE_S)
    finally:
        if process.returncode is None:
            kill_group(process.pid)
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
    code: int | None = None if timed_out else process.returncode
    return code, window.render(), time.monotonic() - started, timed_out


class ShellArgs(ToolArgs):
    command: str = Field(description="Command line, executed by zsh. Chain steps with && and use absolute paths.")
    cwd: str | None = Field(default=None, description="Working directory. Defaults to the workspace.")
    timeout_s: int | None = Field(default=None, ge=1, description="Seconds before the command is killed.")


def _classify_shell(args: ShellArgs, workspace: Path, protected: tuple[Path, ...]) -> ActionInfo:
    cwd: Path = expand_path(args.cwd, workspace) if args.cwd else workspace
    assessment: ShellAssessment = assess_shell(args.command, cwd, workspace, protected)
    flags: tuple[Flag, ...] = assessment.flags
    if classify_path(cwd, workspace, protected) == "protected":
        flags = (*flags, "secret_access")
    preview: str = truncate_middle(" ".join(args.command.split()), 200)
    return ActionInfo(
        tool="shell",
        kind="shell",
        risk=assessment.risk,
        summary=f"Run: {preview} ({'; '.join(assessment.reasons)})",
        target=args.command,
        flags=flags,
    )


def build_shell_tool() -> RegisteredTool:
    def classify(args: ShellArgs, ctx: ToolContext) -> ActionInfo:
        return _classify_shell(args, ctx.workspace, ctx.runtime.protected)

    async def run(args: ShellArgs, ctx: ToolContext) -> ToolResult:
        shell_config: ShellConfig = ctx.runtime.config.shell
        cwd: Path = expand_path(args.cwd, ctx.workspace) if args.cwd else ctx.workspace
        if not await asyncio.to_thread(cwd.is_dir):
            raise ToolExecutionError("working directory does not exist", {"cwd": str(cwd)})
        timeout_s: int = min(args.timeout_s or shell_config.default_timeout_s, shell_config.max_timeout_s)
        code, output, seconds, timed_out = await run_command(args.command, cwd, ctx.runtime.shell_env, timeout_s)
        header: str = (
            f"[timed out after {timeout_s}s and was killed]" if timed_out else f"[exit {code} in {seconds:.1f}s]"
        )
        body: str = output.strip()
        text: str = f"{header}\n{body}" if body else header
        return ToolResult(text=text, images=(), is_error=timed_out or code != 0)

    return make_tool(
        name="shell",
        description=(
            "Run a shell command on the user's Mac (zsh, non-interactive, no stdin). Prefer this over the GUI whenever a "
            "command, script or CLI can do the job: it is the fastest and most reliable tool. Output is capped, so filter "
            "with grep/head/tail/jq. Long-running servers must be started in the background "
            "(nohup cmd > log 2>&1 &) and polled later."
        ),
        args_model=ShellArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=1860.0,
        classify=classify,
        run=run,
    )
