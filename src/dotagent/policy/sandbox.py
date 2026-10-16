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
whatever path, link or letter case names the file. ``[shell] sandbox = false`` starts them without it, and Settings ›
Rules switches it while the daemon runs (:class:`SwitchedSandbox`).

A shell command the guard reads as plain work in the run's workspace (no more than low risk and no flag: a script, a
build, ``npm install``) is held further: it may write only in the workspace, the temporary folders and ``[shell]
allow_write`` (:func:`confined_words`), so what its programs write elsewhere is refused. A line that names a place
outside (``cp``, ``mv``, a redirect), which the guard judges and may ask about, writes there as before. A terminal
session and an AppleScript are not held so: a session runs many lines, each judged as it is typed, and an AppleScript
acts through applications.

What the sandbox costs: macOS starts no program that runs as root from inside it (``ps``, ``top``, ``sudo``,
``crontab``). What it does not cover: a process that has another program act for it (Terminal through ``open``, a job
of launchd, an application driven by Apple Events) acts through that program, outside it; those lines are what the guard
reads. The programs Dot starts itself (its browser, the MCP servers) run from where they are installed, which a process
of a run may change when the user's account may; Dot's own code is among what it loads and runs.
"""

import asyncio
import os
from pathlib import Path

from dotagent.core.errors import ConfigError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

SANDBOX_EXEC: str = "/usr/bin/sandbox-exec"
_CHECKED_PROGRAM: str = "/usr/bin/true"

log = get_logger(__name__)


class Sandbox(FrozenModel):
    """The words that start a process of a run in the sandbox: ``commands`` for a shell command, a terminal session and
    an AppleScript, ``search`` for ripgrep; and what the words of a command held to its run's workspace are built from
    (:func:`confined_words`): ``sealed`` and ``unwritable`` as in ``commands``, and the places besides the workspace it
    may write (``writable``). :data:`UNSANDBOXED` has none."""

    commands: tuple[str, ...]
    search: tuple[str, ...]
    sealed: tuple[Path, ...]
    unwritable: tuple[Path, ...]
    writable: tuple[Path, ...]


UNSANDBOXED: Sandbox = Sandbox(commands=(), search=(), sealed=(), unwritable=(), writable=())
_HEAD: tuple[str, ...] = ("(version 1)", "(allow default)")
# Where every process writes what it prints, reads a terminal and passes descriptors (``/dev/null``, ``/dev/fd``).
_DEVICES: Path = Path("/dev")


def _quoted(path: Path) -> str:
    """``path`` as a string of the profile's language."""
    escaped: str = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _filters(kind: str, paths: list[Path]) -> str:
    return " ".join(f"({kind} {_quoted(path)})" for path in paths)


def _denials(sealed: tuple[Path, ...], unwritable: tuple[Path, ...]) -> list[str]:
    """The rules that refuse reading or changing ``sealed``, changing ``unwritable``, and renaming or removing a folder
    above any of them (a file can still be made inside such a folder). They come last in a profile, where they win over
    any rule before them that would allow the same."""
    ancestors: list[Path] = sorted(
        {folder for path in (*sealed, *unwritable) for folder in path.parents if folder != Path(folder.anchor)}
    )
    rules: list[str] = []
    if sealed:
        rules.append(f"(deny file-read* file-write* {_filters('subpath', sorted(set(sealed)))})")
    if unwritable:
        rules.append(f"(deny file-write* {_filters('subpath', sorted(set(unwritable)))})")
    if ancestors:
        rules.append(f"(deny file-write* {_filters('literal', ancestors)})")
    return rules


def sandbox_profile(sealed: tuple[Path, ...], unwritable: tuple[Path, ...]) -> str:
    """A profile that allows everything but reading or changing ``sealed``, changing ``unwritable``, and renaming or
    removing a folder above any of them. The paths must be resolved: the sandbox matches the path a file has, not the
    link a process names it by."""
    return "\n".join([*_HEAD, *_denials(sealed, unwritable)])


def confined_profile(sealed: tuple[Path, ...], unwritable: tuple[Path, ...], writable: tuple[Path, ...]) -> str:
    """:func:`sandbox_profile` with every write held to ``writable`` (resolved) and the devices a process writes to."""
    held: list[Path] = [*sorted(set(writable)), _DEVICES]
    return "\n".join(
        [
            *_HEAD,
            "(deny file-write*)",
            f"(allow file-write* {_filters('subpath', held)})",
            *_denials(sealed, unwritable),
        ]
    )


def build_sandbox(
    sealed: tuple[Path, ...], protected: tuple[Path, ...], unwritable: tuple[Path, ...], writable: tuple[Path, ...]
) -> Sandbox:
    """The sandbox of a run's processes: a command may neither read nor change ``sealed`` (Dot's own files) nor change
    ``unwritable`` (what Dot loads and runs); a search may not read any ``protected`` location either; a command held
    to its workspace may write there and in ``writable`` only (:func:`confined_words`)."""
    return Sandbox(
        commands=(SANDBOX_EXEC, "-p", sandbox_profile(sealed, unwritable)),
        search=(SANDBOX_EXEC, "-p", sandbox_profile(protected, unwritable)),
        sealed=sealed,
        unwritable=unwritable,
        writable=writable,
    )


def confined_words(sandbox: Sandbox, workspace: Path) -> tuple[str, ...]:
    """The words that start a shell command held to ``workspace`` and the places the sandbox lets it write besides
    (the temporary folders, ``[shell] allow_write``), out of Dot's own files as every command is; none while the
    sandbox is off."""
    if not sandbox.commands:
        return ()
    writable: tuple[Path, ...] = (Path(os.path.realpath(workspace)), *sandbox.writable)
    return (SANDBOX_EXEC, "-p", confined_profile(sandbox.sealed, sandbox.unwritable, writable))


async def check_sandbox(sandbox: Sandbox) -> None:
    """Start a program that does nothing in each profile of ``sandbox`` (a held command's with the home folder for its
    workspace), so that a Mac without ``sandbox-exec``, or a profile it cannot read, stops the daemon at its start
    rather than every process of a run."""
    for words in (sandbox.commands, sandbox.search, confined_words(sandbox, Path.home())):
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
                {"program": words[0], "error": str(error)},
            ) from error
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise ConfigError(
                "the macOS sandbox refused its profile; [shell] sandbox = false runs the processes of a run without it",
                {
                    "program": words[0],
                    "returncode": process.returncode,
                    "stderr": stderr.decode("utf-8", errors="replace")[:500],
                },
            )


class SwitchedSandbox:
    """Where the processes of a run start outside yolo, as ``[shell] sandbox`` said at the daemon's start or as the owner
    switched it since in Settings › Rules (item 262). The sandbox is built at start whatever the setting, and checked to
    start each time it is switched on, so that a Mac without ``sandbox-exec`` refuses the switch rather than every process
    of a run."""

    def __init__(self, built: Sandbox, on: bool) -> None:
        self._built: Sandbox = built
        self._on: bool = on

    @property
    def on(self) -> bool:
        return self._on

    def current(self) -> Sandbox:
        """The sandbox a process of a run outside yolo starts in now: the built one, or :data:`UNSANDBOXED` while off."""
        return self._built if self._on else UNSANDBOXED

    async def switch(self, on: bool) -> None:
        """Start the processes of runs outside yolo in the sandbox, or without it, from the next one on; a process or a
        terminal session already started keeps where it started. Switching it on starts its profiles first
        (:func:`check_sandbox`): when they do not start, nothing changes and the error says why."""
        if on:
            await check_sandbox(self._built)
        self._on = on
        log.info("sandbox_switched", on=on)
