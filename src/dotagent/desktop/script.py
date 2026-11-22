"""AppleScript and JavaScript for Automation, run by ``osascript`` in a child process that can be timed out and
killed."""

import asyncio
import contextlib
import re
from collections.abc import Mapping
from typing import Literal

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.desktop import views

type ScriptLanguage = Literal["AppleScript", "JavaScript"]

_OSASCRIPT: str = "/usr/bin/osascript"
_SCRIPT_CONTEXT_CHARS: int = 600
_REAP_TIMEOUT_S: float = 5.0
_WINDOW_LOOK_TIMEOUT_S: float = 2.0
_WINDOWS_NAMED: int = 3
_TITLE_CHARS: int = 80

_log = get_logger("dotagent.desktop.script")

# What an osascript error number means and the move that helps next (item 1216). macOS writes the message in the
# owner's language ("Ayrıcalık ihlali") but the number in brackets is the same everywhere, so it is what is read: in the
# old store 27.5 % of apple_script calls failed with raw, localized messages the model could not act on.
_ERROR_NUMBER: re.Pattern[str] = re.compile(r"\((-?\d{1,6})\)\s*$")
OSASCRIPT_ERRORS: Mapping[int, str] = {
    -128: "the user cancelled a dialog the script showed",
    -600: "the application is not running: start it first (`tell application ... to activate`, or `open -a`)",
    -609: "the connection to the application broke: it quit or crashed while the script ran; start it and try again",
    -1700: "a value could not be made into the type the script asked for: coerce it, or read the raw value first",
    -1708: "the application does not understand that command: read its dictionary (`sdef` with its path) for the "
    "right one",
    -1712: "the application did not answer in time: it may be busy or showing a dialog; give timeout_s or wait",
    -1719: "an element index is out of range, or assistive access is not allowed for System Events UI scripting",
    -1728: "the object the script named does not exist (a window, document, row or element); read what exists first",
    -1743: "macOS did not allow Apple events to that application: the owner must allow Dot under System Settings › "
    "Privacy & Security › Automation; do not retry until then",
    -2700: "the script raised an error of its own; its message says why",
    -2741: "the script does not compile: fix its syntax",
    -2753: "the script used a variable before setting it",
    -10004: "a privilege violation: the application refused the command (sandboxed apps refuse some)",
    -10810: "macOS could not launch the application",
    -25211: "assistive access is not allowed: the owner must allow Dot under System Settings › Privacy & Security › "
    "Accessibility",
}


def osascript_error(stderr: str) -> tuple[int, str] | None:
    """The error number at the end of osascript's message and what it means, or None for a number not known or a
    message without one."""
    found: re.Match[str] | None = _ERROR_NUMBER.search(stderr.strip())
    if found is None:
        return None
    number: int = int(found.group(1))
    meaning: str | None = OSASCRIPT_ERRORS.get(number)
    return None if meaning is None else (number, meaning)


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
    # The window list is Quartz's: loaded when a script timed out, so the script tools load where it is not (item 1270).
    from dotagent.desktop import screen

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
    variables that look like secrets (items 555 and 1293). ``targets`` are the applications the
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
            + " an Automation approval dialog that has to be answered, or the script waits for something; an"
            " application it was talking to may go on with what it was asked, so a script sent to it again at once"
            " waits behind that",
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
        said: str = stderr.decode("utf-8", "replace").strip()
        known: tuple[int, str] | None = osascript_error(said)
        raise DesktopError(
            "osascript failed" if known is None else f"osascript failed with error {known[0]}: {known[1]}",
            {
                "language": language,
                "code": process.returncode or 0,
                "stderr": said,
                "script": truncate_middle(script, _SCRIPT_CONTEXT_CHARS),
            },
        )
    return stdout.decode("utf-8", "replace").strip()
