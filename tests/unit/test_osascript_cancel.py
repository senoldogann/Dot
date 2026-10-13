"""A script whose wait is cancelled (a stopped run, the tool's own timeout) must not leave ``osascript`` running."""

import asyncio
import subprocess
import time
import uuid

import pytest

from dotagent.desktop.script import run_osascript

GONE_TIMEOUT_S: float = 5.0
SCRIPT_TIMEOUT_S: float = 60.0


async def _count_processes(marker: str) -> int:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "pgrep", "-f", marker, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return len(out.split())


async def _kill_processes(marker: str) -> None:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("pkill", "-f", marker)
    await process.wait()


async def test_a_cancelled_script_does_not_leave_osascript_running() -> None:
    # The marker sits in a comment of the script, so it names this osascript among the processes and nothing else.
    marker: str = f"dot-test-176-{uuid.uuid4().hex}"
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(1.0):
                await run_osascript("AppleScript", f"delay 30 -- {marker}", (), SCRIPT_TIMEOUT_S)
        deadline: float = time.monotonic() + GONE_TIMEOUT_S
        while await _count_processes(marker) > 0:
            assert time.monotonic() < deadline, "osascript is still running after its wait was cancelled"
            await asyncio.sleep(0.1)
    finally:
        # A failing run must not leave the script behind either.
        await _kill_processes(marker)
