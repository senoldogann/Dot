"""The macOS sandbox the processes of a run start in, so that what the shell guard cannot read still keeps out of Dot's
own files.

The shell guard (:mod:`dotagent.policy.shell`) reads a line, not what the programs it starts do: a script a run writes
and then runs, or code that builds a path, reaches whatever the user's account reaches. So outside yolo the processes of
a run (a shell command, a terminal session, an AppleScript and what it runs, the search tools' ripgrep) start under
``sandbox-exec`` with a profile that allows everything but three things: reading or changing Dot's own files (its keys,
token, settings, database, browser profiles and journals), changing what it loads and runs or its emergency stop, and
renaming or removing a folder above any of them, which would move them away from the paths the profile names. The search
tools may not read the user's protected locations either: a search has no reason to, while the programs a shell runs
(ssh, git, a cloud CLI) read their own credentials there. macOS refuses such a call with "Operation not permitted",
whatever path, link or letter case names the file.

What the sandbox costs: macOS starts no program that runs as root from inside it (``ps``, ``top``, ``sudo``,
``crontab``). What it does not cover: a process that has another program act for it (Terminal through ``open``, a job
of launchd, an application driven by Apple Events) acts through that program, outside it; those lines are what the guard
reads.
"""

import asyncio
from pathlib import Path

from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel

SANDBOX_EXEC: str = "/usr/bin/sandbox-exec"
_CHECKED_PROGRAM: str = "/usr/bin/true"


class Sandbox(FrozenModel):
    """The words that start a process of a run in the sandbox: ``commands`` for a shell command, a terminal session and
    an AppleScript, ``search`` for ripgrep. :data:`UNSANDBOXED` has none."""

    commands: tuple[str, ...]
    search: tuple[str, ...]


UNSANDBOXED: Sandbox = Sandbox(commands=(), search=())


def _quoted(path: Path) -> str:
    """``path`` as a string of the profile's language."""
    escaped: str = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _filters(kind: str, paths: list[Path]) -> str:
    return " ".join(f"({kind} {_quoted(path)})" for path in paths)


def sandbox_profile(sealed: tuple[Path, ...], unwritable: tuple[Path, ...]) -> str:
    """A profile that allows everything but reading or changing ``sealed``, changing ``unwritable``, and renaming or
    removing a folder above any of them (a file can still be made inside such a folder). The paths must be resolved:
    the sandbox matches the path a file has, not the link a process names it by."""
    ancestors: list[Path] = sorted(
        {folder for path in (*sealed, *unwritable) for folder in path.parents if folder != Path(folder.anchor)}
    )
    rules: list[str] = ["(version 1)", "(allow default)"]
    if sealed:
        rules.append(f"(deny file-read* file-write* {_filters('subpath', sorted(set(sealed)))})")
    if unwritable:
        rules.append(f"(deny file-write* {_filters('subpath', sorted(set(unwritable)))})")
    if ancestors:
        rules.append(f"(deny file-write* {_filters('literal', ancestors)})")
    return "\n".join(rules)


def build_sandbox(sealed: tuple[Path, ...], protected: tuple[Path, ...], unwritable: tuple[Path, ...]) -> Sandbox:
    """The sandbox of a run's processes: a command may neither read nor change ``sealed`` (Dot's own files) nor change
    ``unwritable`` (what Dot loads and runs); a search may not read any ``protected`` location either."""
    return Sandbox(
        commands=(SANDBOX_EXEC, "-p", sandbox_profile(sealed, unwritable)),
        search=(SANDBOX_EXEC, "-p", sandbox_profile(protected, unwritable)),
    )


async def check_sandbox(sandbox: Sandbox) -> None:
    """Start a program that does nothing in each profile of ``sandbox``, so that a Mac without ``sandbox-exec``, or a
    profile it cannot read, stops the daemon at its start rather than every process of a run."""
    for words in (sandbox.commands, sandbox.search):
        if not words:
            continue
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                *words,
                _CHECKED_PROGRAM,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise ConfigError(
                "the macOS sandbox for the processes of a run cannot start; [shell] sandbox = false runs them without it",
                {"program": SANDBOX_EXEC, "error": str(error)},
            ) from error
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise ConfigError(
                "the macOS sandbox refused its profile; [shell] sandbox = false runs the processes of a run without it",
                {
                    "program": SANDBOX_EXEC,
                    "returncode": process.returncode,
                    "stderr": stderr.decode("utf-8", errors="replace")[:500],
                },
            )
