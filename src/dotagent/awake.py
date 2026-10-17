"""Keeping the Mac awake while it charges (item 271), so that a phone reaches Dot away from home: the daemon holds
macOS's ``caffeinate -s``, which keeps the system from sleeping while it runs on power, for as long as Settings ›
Phone asks it to; ``-w`` ends it with the daemon's process. Closing a MacBook's lid still puts it to sleep, as macOS
does."""

import asyncio
import os

from dotagent.core.errors import PhoneSetupError
from dotagent.core.logs import get_logger

log = get_logger(__name__)

# macOS's own, by its path: not one a folder of the PATH that a run may write to holds.
_CAFFEINATE: str = "/usr/bin/caffeinate"


def caffeinate_program() -> str:
    """The ``caffeinate`` the daemon holds: macOS's own, unless ``DOT_CAFFEINATE`` names another, so that tests point
    at their own and never keep the owner's Mac awake."""
    override: str | None = os.environ.get("DOT_CAFFEINATE")
    return override if override is not None and override != "" else _CAFFEINATE


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
        program: str = caffeinate_program()
        try:
            self._process = await asyncio.create_subprocess_exec(
                program,
                "-s",
                "-w",
                str(os.getpid()),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as error:
            raise PhoneSetupError(
                f"caffeinate, which keeps a Mac awake, could not be started: {error.strerror or error}",
                {"program": program},
            ) from error
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

    async def switch(self, on: bool) -> None:
        """Keep the Mac awake while it charges when ``on``, else let it sleep as it would."""
        if on:
            await self.start()
        else:
            await self.stop()
