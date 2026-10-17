"""Keeping the Mac awake while it charges (item 271), so that a phone reaches Dot away from home: the daemon holds
macOS's ``caffeinate -s``, which keeps the system from sleeping while it runs on power, for as long as Settings ›
Phone asks it to; ``-w`` ends it with the daemon's process. Closing a MacBook's lid still puts it to sleep, as macOS
does."""

import asyncio
import os
import shutil

from dotagent.core.errors import PhoneSetupError
from dotagent.core.logs import get_logger

log = get_logger(__name__)


class KeepAwake:
    """The ``caffeinate`` the daemon holds, while it holds one."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None

    @property
    def on(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Keep the Mac awake while it charges, from now on; nothing changes when it already is."""
        if self.on:
            return
        program: str | None = shutil.which("caffeinate")
        if program is None:
            raise PhoneSetupError("caffeinate, which keeps a Mac awake, is not on this Mac", {})
        self._process = await asyncio.create_subprocess_exec(
            program,
            "-s",
            "-w",
            str(os.getpid()),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        log.info("keep_awake_on", pid=self._process.pid)

    async def stop(self) -> None:
        """Let the Mac sleep as it would again; nothing changes when it is not kept awake."""
        process: asyncio.subprocess.Process | None = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        await process.wait()
        log.info("keep_awake_off")
