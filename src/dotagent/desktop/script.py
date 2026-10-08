"""AppleScript and JavaScript for Automation, run by ``osascript`` in a child process that can be timed out and killed."""

import asyncio
from typing import Literal

from dotagent.core.errors import DesktopError
from dotagent.core.text import truncate_middle

type ScriptLanguage = Literal["AppleScript", "JavaScript"]

_SCRIPT_CONTEXT_CHARS: int = 600
_REAP_TIMEOUT_S: float = 5.0


async def run_osascript(language: ScriptLanguage, script: str, timeout_s: float) -> str:
    """The script's result as text. A script that waits for an Automation approval the user never sees times out."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "osascript",
        "-l",
        language,
        "-e",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError as error:
        process.kill()
        await asyncio.wait_for(process.wait(), _REAP_TIMEOUT_S)
        raise DesktopError(
            f"osascript did not finish within {timeout_s:g} s; macOS may be showing an Automation approval dialog "
            "that has to be answered, or the script waits for something",
            {"language": language, "script": truncate_middle(script, _SCRIPT_CONTEXT_CHARS)},
        ) from error
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
