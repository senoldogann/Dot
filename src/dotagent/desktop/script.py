"""AppleScript and JavaScript for Automation, run by ``osascript`` in a child process that can be timed out and
killed."""

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Literal

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.desktop import screen, views

type ScriptLanguage = Literal["AppleScript", "JavaScript"]

_OSASCRIPT: str = "/usr/bin/osascript"
_SCRIPT_CONTEXT_CHARS: int = 600
_REAP_TIMEOUT_S: float = 5.0
_WINDOW_LOOK_TIMEOUT_S: float = 2.0
_WINDOWS_NAMED: int = 3
_TITLE_CHARS: int = 80

_log = get_logger("dotagent.desktop.script")


def _window_note(shown: tuple[tuple[str, str], ...]) -> str:
    """What the windows a script's target applications show say of why it waits: each, as a sentence."""
    parts: list[str] = []
    for owner, title in shown[:_WINDOWS_NAMED]:
        label: str = f"'{truncate_middle(title, _TITLE_CHARS)}'" if title else "without a title"
        parts.append(f"{owner} shows a window {label} that may wait for an answer")
    return "; ".join(parts)


async def _windows_of_targets(targets: tuple[str, ...]) -> str:
    """The windows on screen of the applications a script targets, as a sentence, or ``""`` when there are none or they
    cannot be listed: a script that timed out is explained by what is on the screen, never made to fail by looking."""
    if not targets:
        return ""
    try:
        windows = await asyncio.wait_for(asyncio.to_thread(screen.windows_everywhere), _WINDOW_LOOK_TIMEOUT_S)
        return _window_note(views.titles_of_windows_shown_by(targets, windows))
    except Exception as error:  # noqa: BLE001 - the look only explains the timeout; its failure is logged, not raised
        _log.warning("script_windows_unreadable", error=repr(error))
        return ""


async def run_osascript(
    language: ScriptLanguage,
    script: str,
    argv: tuple[str, ...],
    confinement: tuple[str, ...],
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    targets: tuple[str, ...] = (),
) -> str:
    """The script's result as text, ``osascript`` started by ``confinement`` (the words of a run's sandbox, or
    none) with the environment ``env`` (``None``: Dot's own). ``argv`` reaches the script's ``run`` handler as its
    arguments, so that values go in as they are, never as words of the script: after ``--``, since ``osascript`` reads a
    first argument that starts with a dash as one of its options, ``-e`` and a line of script among them. A script that
    waits for an Automation approval the user never sees times out; one whose wait is cancelled (a stopped run, the
    tool's own timeout) is killed, so it never goes on acting on the Mac without its run. What a run's script starts
    (``do shell script``) has ``env`` too, so a run's script is given the environment its commands are: without the
    variables that look like secrets, ``SSH_AUTH_SOCK`` among them (item 555). ``targets`` are the applications the
    script names: when it times out, the windows they show say what it may be waiting for (item 751)."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *confinement,
        _OSASCRIPT,
        "-l",
        language,
        "-e",
        script,
        "--",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError as error:
        process.kill()
        await asyncio.wait_for(process.wait(), _REAP_TIMEOUT_S)
        shown: str = await _windows_of_targets(targets)
        raise DesktopError(
            f"osascript did not finish within {timeout_s:g} s"
            + (f"; {shown}" if shown else "")
            + ("; macOS may also be showing" if shown else "; macOS may be showing")
            + " an Automation approval dialog that has to be answered, or the script waits for something",
            {
                "language": language,
                "script": truncate_middle(script, _SCRIPT_CONTEXT_CHARS),
                **({"windows": shown} if shown else {}),
            },
        ) from error
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
    if process.returncode != 0:
        raise DesktopError(
            "osascript failed",
            {
                "language": language,
                "code": process.returncode or 0,
                "stderr": stderr.decode("utf-8", "replace").strip(),
                "script": truncate_middle(script, _SCRIPT_CONTEXT_CHARS),
            },
        )
    return stdout.decode("utf-8", "replace").strip()
