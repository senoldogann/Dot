"""Terminal tools: a program on a pseudo-terminal that the agent types into and reads, as a person at a terminal would
(:mod:`dotagent.terminal.session`). For a program that asks questions, a REPL, or a server that must stay up while the
run goes on; ``shell`` stays the tool for a command that runs to its end.

Opening a session is classified like ``shell`` on its command, and typing like ``shell`` on every line the text
completes, with what was typed before on that line, so that a command typed in parts is judged whole. Text typed while
the program's last line asks for a password is a ``credential``: a ``yolo`` run types it, alone so that no command goes
with it unjudged, and the policy records it; in ``ask`` and ``auto`` the tool refuses, as the browser refuses a password
field, since nobody else can type into the session.
"""

from pathlib import Path

from pydantic import Field

from dotagent.core.errors import ToolInputError
from dotagent.core.text import truncate_middle
from dotagent.policy.paths import classify_path, expand_path
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, ActionKind, Flag, Risk
from dotagent.terminal.session import TerminalSession, typed_lines
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

# How long terminal_open waits for the first output of the program.
_OPEN_WAIT_S: float = 2.0
_MAX_WAIT_S: float = 60.0
_TIMEOUT_S: float = _MAX_WAIT_S + 15.0
_PREVIEW_CHARS: int = 200


class OpenArgs(ToolArgs):
    command: str = Field(
        min_length=1, description="The command line, run by zsh on a terminal of its own: python3 -i, npm create vite"
    )
    cwd: str | None = Field(default=None, description="Working directory. Defaults to the workspace.")


class SendArgs(ToolArgs):
    session: str = Field(description="The session terminal_open named, such as t1.")
    text: str = Field(
        min_length=1,
        max_length=10_000,
        description="What to type. A line end (\\n) presses Enter; text without one waits on the line.",
    )
    wait_s: float = Field(default=2.0, ge=0, le=_MAX_WAIT_S, description="Seconds to wait for the program's answer.")


class ReadArgs(ToolArgs):
    session: str = Field(description="The session terminal_open named, such as t1.")
    wait_s: float = Field(default=10.0, ge=0, le=_MAX_WAIT_S, description="Seconds to wait for new output.")


class CloseArgs(ToolArgs):
    session: str = Field(description="The session terminal_open named, such as t1.")


def _action(tool: str, kind: ActionKind, risk: Risk, summary: str, target: str, flags: tuple[Flag, ...]) -> ActionInfo:
    return ActionInfo(tool=tool, kind=kind, risk=risk, summary=summary, target=target, flags=flags)


def _command_action(tool: str, verb: str, command: str, cwd: Path, ctx: ToolContext) -> ActionInfo:
    """``command`` judged as ``shell`` judges it, run in ``cwd``."""
    assessment: ShellAssessment = assess_shell(command, cwd, ctx.workspace, ctx.runtime.protected)
    flags: tuple[Flag, ...] = assessment.flags
    if classify_path(cwd, ctx.workspace, ctx.runtime.protected) == "protected":
        flags = (*flags, "secret_access")
    preview: str = truncate_middle(" ".join(command.split()), _PREVIEW_CHARS)
    return _action(
        tool, "shell", assessment.risk, f"{verb}: {preview} ({'; '.join(assessment.reasons)})", command, flags
    )


def _output(session: TerminalSession, text: str, wait_s: float) -> str:
    body: str = text.strip("\n")
    return body if body else f"[no new output from {session.id} in {wait_s:g} s]"


def _secret_action(session: TerminalSession, lines: tuple[str, ...], rest: str, ctx: ToolContext) -> ActionInfo:
    """Typing at a password prompt: ``lines`` and ``rest`` are what the text completes and leaves on the line."""
    if ctx.approvals != "yolo":
        raise ToolInputError(
            f"The program of {session.id} asks for a password, and only a yolo run types secrets into a terminal. "
            "Ask the user to run this step in their own terminal, or go on without it.",
            {"session": session.id, "command": session.command},
        )
    if len(lines) > 1 or (lines and rest):
        raise ToolInputError(
            f"The program of {session.id} asks for a password: type the password alone, ending with \\n, and what "
            "comes after it in another terminal_send.",
            {"session": session.id},
        )
    return _action(
        "terminal_send",
        "shell",
        "high",
        f"Type the secret that {session.id} asks for ({truncate_middle(session.command, _PREVIEW_CHARS)})",
        session.command,
        ("credential",),
    )


async def _read(session: TerminalSession, wait_s: float, ctx: ToolContext) -> str:
    """What the session printed; once its program has ended, the end too, and the session is closed."""
    text: str = _output(session, await session.read(wait_s), wait_s)
    if not session.ended:
        return text
    code: int = await session.exit_code()
    await ctx.runtime.terminals.close(ctx.thread_id, session.id)
    return f"{text}\n[the program ended with exit {code}; session {session.id} is closed]"


def build_terminal_tools() -> tuple[RegisteredTool, ...]:
    def classify_open(args: OpenArgs, ctx: ToolContext) -> ActionInfo:
        cwd: Path = expand_path(args.cwd, ctx.workspace) if args.cwd else ctx.workspace
        return _command_action("terminal_open", "Open a terminal for", args.command, cwd, ctx)

    async def run_open(args: OpenArgs, ctx: ToolContext) -> ToolResult:
        cwd: Path = expand_path(args.cwd, ctx.workspace) if args.cwd else ctx.workspace
        session: TerminalSession = await ctx.runtime.terminals.open(ctx.thread_id, args.command, cwd)
        return ok(f"[session {session.id}: {args.command}]\n{await _read(session, _OPEN_WAIT_S, ctx)}")

    def classify_send(args: SendArgs, ctx: ToolContext) -> ActionInfo:
        session: TerminalSession = ctx.runtime.terminals.get(ctx.thread_id, args.session)
        lines, rest = typed_lines(session.pending_line, args.text)
        if session.awaiting_secret:
            return _secret_action(session, lines, rest, ctx)
        if not lines:
            preview: str = truncate_middle(rest, _PREVIEW_CHARS)
            return _action("terminal_send", "shell", "low", f"Type into {session.id}: {preview}", session.command, ())
        return _command_action("terminal_send", f"Type into {session.id}", "\n".join(lines), session.cwd, ctx)

    async def run_send(args: SendArgs, ctx: ToolContext) -> ToolResult:
        session: TerminalSession = ctx.runtime.terminals.get(ctx.thread_id, args.session)
        session.send(args.text)
        return ok(await _read(session, args.wait_s, ctx))

    def classify_read(args: ReadArgs, ctx: ToolContext) -> ActionInfo:
        return _action("terminal_read", "read", "safe", f"Read the terminal {args.session}", args.session, ())

    async def run_read(args: ReadArgs, ctx: ToolContext) -> ToolResult:
        return ok(await _read(ctx.runtime.terminals.get(ctx.thread_id, args.session), args.wait_s, ctx))

    def classify_close(args: CloseArgs, ctx: ToolContext) -> ActionInfo:
        return _action("terminal_close", "shell", "low", f"Close the terminal {args.session}", args.session, ())

    async def run_close(args: CloseArgs, ctx: ToolContext) -> ToolResult:
        await ctx.runtime.terminals.close(ctx.thread_id, args.session)
        return ok(f"[session {args.session} is closed]")

    return (
        make_tool(
            name="terminal_open",
            description=(
                "Start a program on a terminal of its own and keep it running: one that asks questions (an installer, "
                "npm create, ssh), a REPL (python3 -i, psql, node), or a server that must stay up while you work. "
                "Returns the session name and its first output. For a command that just runs to its end use shell. "
                "No terminal emulator runs: full-screen programs such as vim or htop show as garbled text."
            ),
            args_model=OpenArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_open,
            run=run_open,
        ),
        make_tool(
            name="terminal_send",
            description=(
                "Type into a terminal session and return what the program printed back. End a line with \\n to "
                "press Enter. When the program asks for a password, only a yolo run types it."
            ),
            args_model=SendArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_send,
            run=run_send,
        ),
        make_tool(
            name="terminal_read",
            description=(
                "What a terminal session printed since the last read, waiting up to wait_s for something new: a "
                "server's log, the end of a long install."
            ),
            args_model=ReadArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_read,
            run=run_read,
        ),
        make_tool(
            name="terminal_close",
            description="End the program of a terminal session and everything it started. Close what you no longer need.",
            args_model=CloseArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_close,
            run=run_close,
        ),
    )
