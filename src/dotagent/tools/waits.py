"""``wait_until`` (item 887, after OpenMausBot's waits): a run that waits for something to happen (a deploy to go
live, a build to write its file, a check of a pull request to pass) waits in code, not in model turns. It names one
condition, a command that changes nothing, an address, or a file, and Dot checks it every ``every_s`` seconds for up to
``within_minutes`` (60 at most), and says when it held, or what the last check saw. On the owner's Mac, runs of one
week slept and polled 131 times in 41 runs (``sleep 60; gh pr checks …``, ``sleep 20; curl …``), each poll a turn.

What holds:

- Each condition is classified as the tool it stands for, under that tool's name, so that the owner's rules for it
  bind it as they bind that tool (the pre-push review of item 887): a command as ``shell``, an address as ``web_fetch``,
  a file as ``read_file``.
- A command is a check only when the shell guard reads it as reading on this Mac (risk ``safe``, no flag, no file it
  changes, nothing it fetches): it runs again and again, and a line that sends, writes or removes would do so each time.
  ``low`` is not reading: it is ``git pull``, ``git commit``, a script run (the review again); and a line that reaches
  the network, even one the guard reads as safe (``git ls-remote``), is no check either. A public address is waited on
  as one.
- An address is checked where ``web_fetch`` may go (the owner's allowed and blocked hosts too); only the status and
  whether the page holds ``contains`` come back, not its words.
- A file is judged as ``read_file`` judges it (a protected place asks) and opened as it opens one, with no link
  followed along its path: a link that came there since the call was allowed ends the wait unread. Only whether it is
  there and holds ``contains`` comes back.
- The run is stopped as any call is: the emergency stop and the owner's stop end the wait at once.
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import Field, model_validator

from dotagent.core.errors import DotError, PrivateHostRefused, ToolExecutionError, ToolInputError
from dotagent.core.text import truncate_middle
from dotagent.policy.shell import assess_shell
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_path_tool
from dotagent.tools.context import ToolContext, run_sandbox
from dotagent.tools.files import _opened, read_action, resolve_path
from dotagent.tools.shell import ShellArgs, _classify_shell, run_command
from dotagent.tools.third_party import holds_third_party, mentions_third_party
from dotagent.tools.web import checked_address

WAIT_UNTIL_TOOL: str = "wait_until"
MOST_MINUTES: float = 60.0
_LEAST_EVERY_S: int = 2
_MOST_EVERY_S: int = 600
_CONTAINS_CHARS: int = 200
# What a check may take: a command is killed past this, a page that does not answer is a check that failed.
_CHECK_S: int = 60
# The most of a page or a file read for ``contains``, and of a command's output kept for the result.
_READ_BYTES: int = 1024 * 1024
_SEEN_CHARS: int = 600


class WaitArgs(ToolArgs):
    command: str | None = Field(
        default=None, description="A check that only reads on this Mac (test -f out.zip, grep -q done build.log)."
    )
    url: str | None = Field(default=None, description="A public address that must answer 2xx or 3xx.")
    file: str | None = Field(default=None, description="A file that must be there (relative to the workspace).")
    contains: str | None = Field(
        default=None, max_length=_CONTAINS_CHARS, description="Text the output, page or file must hold."
    )
    every_s: int = Field(default=10, ge=_LEAST_EVERY_S, le=_MOST_EVERY_S, description="Seconds between checks.")
    within_minutes: float = Field(default=10.0, gt=0, le=MOST_MINUTES, description="Most minutes to wait.")

    @model_validator(mode="after")
    def _one_condition(self) -> "WaitArgs":
        named: int = sum(value is not None and value.strip() != "" for value in (self.command, self.url, self.file))
        if named != 1:
            raise ValueError("name exactly one of command, url and file")
        return self


def _when(args: WaitArgs) -> str:
    return f"every {args.every_s} s for up to {args.within_minutes:g} min"


def _classify(args: WaitArgs, ctx: ToolContext) -> ActionInfo:
    if args.command is not None and args.command.strip():
        shell: ActionInfo = _classify_shell(
            ShellArgs(command=args.command), ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
        )
        fetches: bool = assess_shell(
            args.command, ctx.workspace, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
        ).fetches
        if shell.risk != "safe" or shell.flags or shell.changes or fetches:
            raise ToolInputError(
                "wait_until runs its command again and again, so it takes only a check that only reads on this Mac "
                f"(test -f, grep, git status); this one does more ({shell.summary}). Run it once with shell, then wait "
                "on what shows it is done: a file, a public address (url), or a command that only reads",
                {"risk": shell.risk, "flags": list(shell.flags)},
            )
        preview: str = truncate_middle(" ".join(args.command.split()), 200)
        return ActionInfo(
            tool="shell",
            kind="shell",
            risk=shell.risk,
            summary=f"Wait until {preview} succeeds, checking {_when(args)}",
            target=args.command,
            flags=(),
        )
    if args.url is not None and args.url.strip():
        url, flags = checked_address(args.url, ctx.runtime.config)
        return ActionInfo(
            tool="web_fetch",
            kind="network",
            risk="low",
            summary=f"Wait until {truncate_middle(url, 200)} answers, checking {_when(args)}",
            target=url,
            flags=flags,
        )
    path: Path = resolve_path(args.file or "", ctx)
    return read_action(
        "read_file",
        path,
        ctx.workspace,
        ctx.runtime.protected,
        ctx.runtime.dot_paths,
        f"Wait, checking {_when(args)}, for",
    )


# A check: whether the condition held, what it saw (a line for the result), and whether that is untrusted.
type Check = Callable[[], Awaitable[tuple[bool, str, bool]]]


class _NeverHolds(Exception):
    """A check whose answer no later check changes (an address on the local network the web tools do not reach): the
    wait ends at once, with what it said."""


def _command_check(args: WaitArgs, ctx: ToolContext, command: str) -> Check:
    cwd: Path = ctx.workspace
    fetches: bool = assess_shell(command, cwd, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths).fetches
    untrusted: bool = fetches or mentions_third_party(command, cwd, ctx.workspace)

    async def check() -> tuple[bool, str, bool]:
        code, output, _seconds, timed_out = await run_command(
            command, run_sandbox(ctx).commands, cwd, ctx.runtime.shell_env, _CHECK_S
        )
        held: bool = not timed_out and code == 0 and (args.contains is None or args.contains in output)
        tail: str = truncate_middle(output.strip(), _SEEN_CHARS)
        head: str = f"timed out after {_CHECK_S} s" if timed_out else f"exit {code}"
        return held, f"{head}{': ' + tail if tail else ''}", untrusted

    return check


def _url_check(args: WaitArgs, ctx: ToolContext, url: str) -> Check:
    async def check() -> tuple[bool, str, bool]:
        try:
            async with asyncio.timeout(_CHECK_S):
                document = await ctx.runtime.web.get(url, _READ_BYTES)
        except PrivateHostRefused as error:
            raise _NeverHolds(error.message) from error
        except (DotError, TimeoutError) as error:
            said: str = error.message if isinstance(error, DotError) else f"no answer within {_CHECK_S} s"
            return False, f"no answer ({truncate_middle(said, 200)})", False
        answered: bool = 200 <= document.status < 400
        if args.contains is None:
            return answered, f"HTTP {document.status}", False
        holds: bool = args.contains in document.body.decode(document.charset or "utf-8", errors="replace")
        return answered and holds, f"HTTP {document.status}, {'with' if holds else 'without'} the text", False

    return check


def _file_check(args: WaitArgs, ctx: ToolContext, path: Path) -> Check:
    untrusted: bool = holds_third_party(path, ctx.workspace)

    def look() -> tuple[bool, str]:
        # Opened as read_file opens it (item 681), with no link followed along the path: a link put there since the
        # call was allowed, to a protected file, is refused and ends the wait, never read (the third pre-push review).
        try:
            descriptor: int = _opened(path)
        except ToolExecutionError as error:
            if not os.path.lexists(path):
                return False, "not there yet"
            raise _NeverHolds(f"{path} is there but is no file Dot reads ({error.message})") from error
        with os.fdopen(descriptor, "rb") as handle:
            if args.contains is None:
                return True, "there"
            holds: bool = args.contains in handle.read(_READ_BYTES).decode("utf-8", errors="replace")
        return holds, "there, with the text" if holds else "there, without the text"

    async def check() -> tuple[bool, str, bool]:
        held, seen = await asyncio.to_thread(look)
        return held, seen, untrusted

    return check


def _checks(count: int) -> str:
    return f"{count} check{'s' if count != 1 else ''}"


def _minutes(seconds: float) -> str:
    whole: int = round(seconds)
    return f"{whole // 60} min {whole % 60} s" if whole >= 60 else f"{whole} s"


async def _wait(args: WaitArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
    check: Check
    if args.command is not None and args.command.strip():
        check = _command_check(args, ctx, authorized.target)
    elif args.url is not None and args.url.strip():
        check = _url_check(args, ctx, authorized.target)
    else:
        check = _file_check(args, ctx, Path(authorized.target))
    started: float = time.monotonic()
    deadline: float = started + args.within_minutes * 60.0
    checks: int = 0
    untrusted: bool = False
    seen: str = ""
    text: str
    while True:
        checks += 1
        try:
            held, seen, said_by_others = await check()
        except _NeverHolds as never:
            text = f"The condition cannot hold, so Dot stopped waiting: {never}"
            return ToolResult(text=text, images=(), is_error=True, untrusted=untrusted)
        untrusted = untrusted or said_by_others
        waited: str = _minutes(time.monotonic() - started)
        if held:
            text = f"The condition held after {waited} ({_checks(checks)}): {seen}"
            return ToolResult(text=text, images=(), is_error=False, untrusted=untrusted)
        if time.monotonic() + args.every_s > deadline:
            within: str = f"{args.within_minutes:g} min"
            text = f"The condition did not hold within {within} ({_checks(checks)}); the last saw: {seen}"
            return ToolResult(text=text, images=(), is_error=True, untrusted=untrusted)
        await asyncio.sleep(args.every_s)


def build_wait_tools() -> tuple[RegisteredTool, ...]:
    return (
        make_path_tool(
            name=WAIT_UNTIL_TOOL,
            description=(
                "Wait in code, with no turn spent, until one condition holds: command exits 0, url answers, or file "
                "is there. Use it instead of sleep and a check. A command that changes something or reaches the "
                "network is refused."
            ),
            args_model=WaitArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=MOST_MINUTES * 60.0 + 2 * _CHECK_S + 30.0,
            classify=_classify,
            run=_wait,
        ),
    )
