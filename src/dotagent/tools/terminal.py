"""Terminal tools: a program on a pseudo-terminal that the agent types into and reads, as a person at a terminal would
(:mod:`dotagent.terminal.session`). For a program that asks questions, a REPL, or a server that must stay up while the
run goes on; ``shell`` stays the tool for a command that runs to its end.

Opening a session is classified like ``shell`` on its command, and typing like ``shell`` on every line the text
completes, with what was typed before on that line, so that a command typed in parts is judged whole. Keys a line editor
acts upon (Tab, the arrows, a Ctrl- shortcut) leave a line that cannot be told, which is judged as code that cannot be
read, and once a line has run the program may be in another directory, so later lines are judged from the home folder,
as a line typed into a terminal application is. What the program prints is untrusted: an installer, a remote shell
or a REPL prints what other people wrote. Text typed while the program's last line asks for a password is refused in
every mode, yolo too (item 429): credentials are the user's, and nobody else can type into the session, so the step is
theirs to run in a terminal of their own.

A command opened with ``background`` (item 229) runs on its own, past the shell's time limit and unread, and its end is
told to its chat; a delegated run and a campaign's session have no chat of their own to tell, and open none.
"""

from pathlib import Path
from typing import NoReturn

from pydantic import Field

from dotagent.core.errors import ToolInputError
from dotagent.core.text import truncate_middle
from dotagent.policy.paths import classify_path, expand_path
from dotagent.policy.shell import ShellAssessment, assess_shell, assess_typed, typed_lines
from dotagent.policy.types import ActionInfo, ActionKind, Flag, Risk
from dotagent.store.models import RunRecord
from dotagent.terminal.session import TerminalSession
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext, run_sandbox

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
    background: bool = Field(
        default=False,
        description=(
            "True for a command that runs long and ends by itself (an eval, a long build or download): it runs past "
            "shell's time limit and is not closed for being left unread, and when it ends this chat is told, in a new "
            "run once the run at work there has ended. Not for a program you type into, nor a server."
        ),
    )


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


def _command_action(
    tool: str, verb: str, command: str, cwd: Path, assessment: ShellAssessment, ctx: ToolContext
) -> ActionInfo:
    """``command`` as ``assessment`` judged it, run in ``cwd``."""
    flags: tuple[Flag, ...] = assessment.flags
    if classify_path(cwd, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths) == "protected":
        flags = (*flags, "secret_access")
    preview: str = truncate_middle(" ".join(command.split()), _PREVIEW_CHARS)
    summary: str = f"{verb}: {preview} ({'; '.join(assessment.reasons)})"
    return _action(tool, "shell", assessment.risk, summary, command, flags).model_copy(
        update={"changes": assessment.changes}
    )


def _output(session: TerminalSession, text: str, wait_s: float) -> str:
    body: str = text.strip("\n")
    return body if body else f"[no new output from {session.id} in {wait_s:g} s]"


def _refuse_secret(session: TerminalSession) -> NoReturn:
    """Typing at a password prompt is the user's, in every mode (item 429)."""
    raise ToolInputError(
        f"The program of {session.id} asks for a password, and Dot never types one into a terminal: credentials are "
        "the user's. Ask the user to run this step in their own terminal, or go on without it.",
        {"session": session.id, "command": session.command},
    )


async def _read(session: TerminalSession, wait_s: float, ctx: ToolContext) -> str:
    """What the session printed; once its program has ended, the end too, and the session is closed."""
    text: str = _output(session, await session.read(wait_s), wait_s)
    if not session.ended:
        return text
    code: int = await session.exit_code()
    await ctx.runtime.terminals.close_read(ctx.thread_id, session.id)
    return f"{text}\n[the program ended with exit {code}; session {session.id} is closed]"


def build_terminal_tools() -> tuple[RegisteredTool, ...]:
    def classify_open(args: OpenArgs, ctx: ToolContext) -> ActionInfo:
        cwd: Path = expand_path(args.cwd, ctx.workspace) if args.cwd else ctx.workspace
        assessment: ShellAssessment = assess_shell(
            args.command, cwd, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
        )
        return _command_action("terminal_open", "Open a terminal for", args.command, cwd, assessment, ctx)

    async def run_open(args: OpenArgs, ctx: ToolContext) -> ToolResult:
        cwd: Path = expand_path(args.cwd, ctx.workspace) if args.cwd else ctx.workspace
        if args.background:
            run: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
            if run.parent_run_id is not None or run.campaign_id is not None:
                raise ToolInputError(
                    "A command in the background is told to its chat when it ends, and a delegated run or a "
                    "campaign's session has none: run it with shell, or in a terminal session you read.",
                    {"run_id": run.id, "parent_run_id": run.parent_run_id, "campaign_id": run.campaign_id},
                )
        session: TerminalSession = await ctx.runtime.terminals.open(
            ctx.thread_id, args.command, run_sandbox(ctx).commands, cwd, args.background
        )
        head: str = (
            f"[session {session.id}, in the background: {args.command}; this chat is told when it ends]"
            if args.background
            else f"[session {session.id}: {args.command}]"
        )
        return mark_untrusted(ok(f"{head}\n{await _read(session, _OPEN_WAIT_S, ctx)}"))

    def classify_send(args: SendArgs, ctx: ToolContext) -> ActionInfo:
        session: TerminalSession = ctx.runtime.terminals.get(ctx.thread_id, args.session)
        lines, rest = typed_lines(session.pending_line, args.text)
        if session.awaiting_secret:
            _refuse_secret(session)
        if run_sandbox(ctx).commands and not session.sandboxed:
            raise ToolInputError(
                f"{session.id} was opened outside the sandbox the commands of this run start in (by a yolo run, or "
                "while the sandbox was off): open a terminal of this run with terminal_open.",
                {"session": session.id, "command": session.command},
            )
        if not lines:
            preview: str = truncate_middle(rest if rest is not None else repr(args.text), _PREVIEW_CHARS)
            return _action("terminal_send", "shell", "low", f"Type into {session.id}: {preview}", session.command, ())
        cwd: Path = session.cwd if session.cwd_known else Path.home()
        assessment: ShellAssessment = assess_typed(
            lines, cwd, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
        )
        known: list[str] = [line for line in lines if line is not None]
        shown: str = "\n".join(known) if len(known) == len(lines) else repr(args.text)
        return _command_action("terminal_send", f"Type into {session.id}", shown, cwd, assessment, ctx)

    async def run_send(args: SendArgs, ctx: ToolContext) -> ToolResult:
        session: TerminalSession = ctx.runtime.terminals.get(ctx.thread_id, args.session)
        session.send(args.text)
        return mark_untrusted(ok(await _read(session, args.wait_s, ctx)))

    def classify_read(args: ReadArgs, ctx: ToolContext) -> ActionInfo:
        return _action("terminal_read", "read", "safe", f"Read the terminal {args.session}", args.session, ())

    async def run_read(args: ReadArgs, ctx: ToolContext) -> ToolResult:
        return mark_untrusted(ok(await _read(ctx.runtime.terminals.get(ctx.thread_id, args.session), args.wait_s, ctx)))

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
                "Returns the session name and its first output. For a command that just runs to its end use shell; "
                "for one that runs longer than shell may wait, set background. No terminal emulator runs: full-screen "
                "programs such as vim or htop show as garbled text."
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
                "press Enter. When the program asks for a password, the user types it in a terminal of their own."
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
            description=(
                "End the program of a terminal session and everything it started. Close what you no longer need."
            ),
            args_model=CloseArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_TIMEOUT_S,
            classify=classify_close,
            run=run_close,
        ),
    )
