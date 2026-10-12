"""A search that is cancelled (tool timeout, stopped run) must not leave ripgrep running in the background."""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from dotagent.tools.files import run_rg

GONE_TIMEOUT_S: float = 5.0


async def _count_processes(marker: str) -> int:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "pgrep", "-f", marker, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return len(out.split())


async def _kill_processes(marker: str) -> None:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("pkill", "-f", marker)
    await process.wait()


async def test_a_cancelled_search_does_not_leave_ripgrep_running(tmp_path: Path) -> None:
    # Ripgrep blocks opening a FIFO until something writes to it, which makes a search that never ends.
    pipe: Path = tmp_path / "never-written"
    os.mkfifo(pipe)
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(1.0):
                await run_rg(["-e", "x", "--", str(pipe)], (), tmp_path, dict(os.environ))
        deadline: float = time.monotonic() + GONE_TIMEOUT_S
        while await _count_processes(str(pipe)) > 0:
            assert time.monotonic() < deadline, "ripgrep is still running after its search was cancelled"
            await asyncio.sleep(0.1)
    finally:
        # A failing run must not leave the blocked search behind either.
        await _kill_processes(str(pipe))
