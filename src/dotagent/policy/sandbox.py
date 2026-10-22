"""The macOS sandbox the processes of a run start in, so that what the shell guard cannot read still keeps out of Dot's
own files.

The shell guard (:mod:`dotagent.policy.shell`) reads a line, not what the programs it starts do: a script a run writes
and then runs, or code that builds a path, reaches whatever the user's account reaches. So outside yolo the processes of
a run (a shell command, a terminal session, an AppleScript and what it runs, the search tools' ripgrep) start under
``sandbox-exec`` with a profile that allows everything but three things: reading or changing Dot's own files (its keys,
token, settings, database, browser profiles and journals) and the stores no program has any use for (``[policy]
sealed_paths``: the data of the user's browsers, Mail and Messages, item 531), changing what it loads and runs or its
emergency stop, and renaming or removing a folder above any of them, which would move them away from the paths the
profile names. The search tools may not read the user's protected locations either: a search has no reason to, while
the programs a shell runs (ssh, git, a cloud CLI) read their own credentials there, so those stay open to a command and
are the guard's to keep. macOS refuses such a call with "Operation not permitted",
whatever path, link or letter case names the file. ``[shell] sandbox = false`` starts them without it, and Settings ›
Rules switches it while the daemon runs (:class:`SwitchedSandbox`).

A process that has another program act for it would act outside the sandbox, so the profile refuses that too:
opening an application, a document or an address through Launch Services (``open``, a program's own call), starting the
programs that hand one something to run (:data:`HANDING_PROGRAMS`: ``launchctl``, ``shortcuts``, ``lsregister``, where
macOS installs them), the services that run what such a program hands them (:data:`HANDING_SERVICES`: the Shortcuts
service and Launch Services' database, whatever program asks), creating a job of launchd, and writing a job for launchd
into the user's ``~/Library/LaunchAgents``. An application that is already open is still driven (Mail, Calendar,
Safari), with its own powers; ``desktop_open_app`` opens one from Dot's own process, and asks first for one that is not
on the sealed system volume, however it is named.

The clipboard is closed too (item 534): the profile refuses a lookup of the pasteboard's service
(:data:`CLIPBOARD_SERVICES`), so no process of a run reads what the user has just copied (a password from their
password manager) or puts text there, whatever the line that started it says: ``pbpaste`` and ``pbcopy``, an
AppleScript's ``the clipboard``, a program's own ``NSPasteboard``. The shell guard asks before a run tries it
(:mod:`dotagent.policy.shell`), by the names it can read; the sandbox is what holds when it cannot read them (a name
spelt by a quote the shell takes out twice, a script in a file). The tools ``desktop_clipboard_write`` and
``desktop_clipboard_read`` run in Dot's own process, outside the sandbox, and ask as the policy says.

Apple Events are the exception, and the profile cannot refuse them: ``(deny appleevent-send …)`` compiles and holds
nothing (measured on macOS 26: with the rule over the terminal apps, Script Editor, Automator, Shortcuts, System
Events, the Finder and Xcode, a script still drove the Finder and the terminal apps, and started an application that
was not running). A run's AppleScript can therefore still have another application act for it, with the powers of that
application; the Automation permission the owner grants (TCC) and the policy's reading of the script
(:mod:`dotagent.tools.apple`) are what stand in its way.

What the sandbox costs: macOS starts no program that runs as root from inside it (``ps``, ``top``, ``sudo``,
``crontab``), a run's command opens nothing (``open``), and it neither reads nor writes the clipboard (``pbcopy`` fails:
``desktop_clipboard_write`` is the way to put text there). What it does not cover: Apple Events, as above; a program
that speaks to a service through its own interface, not through the programs above; and the programs Dot starts itself
(its browser, the MCP servers), which run from where they are installed, which a process of a run may change when the
user's account may; Dot's own code is among what it loads and runs.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from dotagent.core.errors import ConfigError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

SANDBOX_EXEC: str = "/usr/bin/sandbox-exec"
_CHECKED_PROGRAM: str = "/usr/bin/true"

log = get_logger(__name__)

# The programs that hand another one something to run outside the sandbox: launchd a job, the Shortcuts service a
# shortcut, Launch Services an application to know by its name.
HANDING_PROGRAMS: tuple[Path, ...] = (
    Path("/bin/launchctl"),
    Path("/usr/bin/shortcuts"),
    Path(
        "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/"
        "Support/lsregister"
    ),
)
# The services that run what such a program hands them, outside the sandbox whatever program asks: the Shortcuts
# service runs a shortcut (its "Run Shell Script" among them), and Launch Services' database is what ``open`` finds a
# bundle by, so a program a run wrote could register a bundle it made and have it opened by its bundle id.
HANDING_SERVICES: tuple[str, ...] = (
    "com.apple.siri.VoiceShortcuts.xpc",
    "com.apple.lsd.modifydb",
)


# The service of the clipboard: a process that cannot look it up can neither read what the user copied nor put text on
# the clipboard (item 534). Measured on macOS 26: ``pbpaste`` prints nothing and an AppleScript's ``the clipboard``
# fails with -4960 under a profile that refuses it; ``bootstrap_look_up`` answers 1100 where it answers 0 outside.
CLIPBOARD_SERVICES: tuple[str, ...] = ("com.apple.pasteboard.1",)


class Sandbox(FrozenModel):
    """The words that start a process of a run in the sandbox: ``commands`` for a shell command, a terminal session and
    an AppleScript, ``search`` for ripgrep. :data:`UNSANDBOXED` has none."""

    commands: tuple[str, ...]
    search: tuple[str, ...]


UNSANDBOXED: Sandbox = Sandbox(commands=(), search=())


def _quoted(text: str) -> str:
    """``text`` as a string of the profile's language."""
    escaped: str = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _filters(kind: str, names: Sequence[str | Path]) -> str:
    return " ".join(f"({kind} {_quoted(str(name))})" for name in names)


def sandbox_profile(sealed: tuple[Path, ...], unwritable: tuple[Path, ...], launch_agents: Path) -> str:
    """A profile that allows everything but reading or changing ``sealed``, changing ``unwritable``, and renaming or
    removing a folder above any of them (a file can still be made inside such a folder); and having another program act
    outside it: opening anything through Launch Services, starting :data:`HANDING_PROGRAMS` where macOS installs them or
    the services of :data:`HANDING_SERVICES` they would hand something to, creating a job of launchd, writing into
    ``launch_agents``, where launchd finds the jobs it starts at a login, and looking up the clipboard's service
    (:data:`CLIPBOARD_SERVICES`). The paths must be resolved: the sandbox
    matches the path a file has, not the link a process names it by. Apple Events are not among them: macOS does not
    hold this profile to ``appleevent-send``."""
    ancestors: list[Path] = sorted(
        {
            folder
            for path in (*sealed, *unwritable, launch_agents)
            for folder in path.parents
            if folder != Path(folder.anchor)
        }
    )
    rules: list[str] = ["(version 1)", "(allow default)"]
    if sealed:
        rules.append(f"(deny file-read* file-write* {_filters('subpath', sorted(set(sealed)))})")
    if unwritable:
        rules.append(f"(deny file-write* {_filters('subpath', sorted(set(unwritable)))})")
    rules.append(f"(deny file-write* {_filters('subpath', [launch_agents])})")
    if ancestors:
        rules.append(f"(deny file-write* {_filters('literal', ancestors)})")
    rules.append("(deny lsopen)")
    rules.append(f"(deny process-exec {_filters('literal', list(HANDING_PROGRAMS))})")
    rules.append(f"(deny mach-lookup {_filters('global-name', HANDING_SERVICES)})")
    rules.append(f"(deny mach-lookup {_filters('global-name', CLIPBOARD_SERVICES)})")
    rules.append("(deny job-creation)")
    return "\n".join(rules)


def build_sandbox(
    sealed: tuple[Path, ...], protected: tuple[Path, ...], unwritable: tuple[Path, ...], launch_agents: Path
) -> Sandbox:
    """The sandbox of a run's processes: a command may neither read nor change ``sealed`` (Dot's own files) nor change
    ``unwritable`` (what Dot loads and runs); a search may not read any ``protected`` location either. Neither has
    another program act for it (:func:`sandbox_profile`)."""
    return Sandbox(
        commands=(SANDBOX_EXEC, "-p", sandbox_profile(sealed, unwritable, launch_agents)),
        search=(SANDBOX_EXEC, "-p", sandbox_profile(protected, unwritable, launch_agents)),
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
                "the macOS sandbox for the processes of a run cannot start; [shell] sandbox = false runs them without "
                "it",
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
    """Where the processes of a run start outside yolo, as ``[shell] sandbox`` said at the daemon's start or as the
    owner switched it since in Settings › Rules (item 262). The sandbox is built at start whatever the setting, and
    checked to start each time it is switched on, so that a Mac without ``sandbox-exec`` refuses the switch rather
    than every process of a run."""

    def __init__(self, built: Sandbox, on: bool) -> None:
        self._built: Sandbox = built
        self._on: bool = on

    @property
    def on(self) -> bool:
        return self._on

    def current(self) -> Sandbox:
        """The sandbox a process of a run outside yolo starts in now: the built one, or :data:`UNSANDBOXED` while
        off."""
        return self._built if self._on else UNSANDBOXED

    async def switch(self, on: bool) -> None:
        """Start the processes of runs outside yolo in the sandbox, or without it, from the next one on; a process or a
        terminal session already started keeps where it started. Switching it on starts its profiles first
        (:func:`check_sandbox`): when they do not start, nothing changes and the error says why."""
        if on:
            await check_sandbox(self._built)
        self._on = on
        log.info("sandbox_switched", on=on)
