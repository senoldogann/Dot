"""Keeping the Mac awake: while it charges (item 271), so that a phone reaches Dot away from home, and while Dot works
(item 1671), so that idle sleep never freezes a run, a schedule's run or a meeting in the middle of a step.

For the phone, the daemon holds macOS's ``caffeinate -s``, which keeps the system from sleeping while it runs on power,
for as long as Settings › Phone asks it to. While Dot works (``[daemon] keep_awake``), it holds ``caffeinate -i``, which
keeps the system from idle sleep on power and on battery alike, from the first work on until ``keep_awake_after_s``
after it is last needed. It also holds shortly before a routine or watcher is due; the display still sleeps.
Each hold uses a child ``caffeinate`` with ``-w`` and the daemon's pid: ``-w`` lets the
assertion go when the daemon ends however it ends (a crash too), ``pmset -g assertions`` names it, and a test points
``DOT_CAFFEINATE`` at a stand-in that keeps nothing awake. Closing a MacBook's lid still puts it to sleep, as macOS
does."""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Literal

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


# The ``caffeinate`` assertion a hold makes: ``-s`` keeps the system awake while it runs on power, ``-i`` keeps it
# from idle sleep on power and on battery.
type Assertion = Literal["-s", "-i"]


class KeepAwake:
    """The ``caffeinate`` the daemon holds for ``assertion``, while it holds one."""

    def __init__(self, assertion: Assertion) -> None:
        self._assertion: Assertion = assertion
        self._process: asyncio.subprocess.Process | None = None

    @property
    def on(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Keep the Mac awake as the assertion says, from now on; nothing changes when it already is."""
        if self.on:
            return
        program: str = caffeinate_program()
        try:
            self._process = await asyncio.create_subprocess_exec(
                program,
                self._assertion,
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
        log.info("keep_awake_on", pid=self._process.pid, assertion=self._assertion)

    async def stop(self) -> None:
        """Let the Mac sleep as it would again; nothing changes when it is not kept awake."""
        process: asyncio.subprocess.Process | None = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        await process.wait()
        log.info("keep_awake_off", assertion=self._assertion)

    async def switch(self, on: bool) -> None:
        """Keep the Mac awake while it charges when ``on``, else let it sleep as it would."""
        if on:
            await self.start()
        else:
            await self.stop()


def holds_awake(working: bool, last_work_at: float | None, now: float, after_s: float) -> bool:
    """Whether the Mac is kept awake for Dot's work now (item 1671): while something works, and for ``after_s``
    seconds after the last work was seen (``last_work_at``), which bridges a run that ends and the next one its queue
    or a schedule starts."""
    return working or (last_work_at is not None and now - last_work_at < after_s)


async def keep_awake_while_working(
    awake: KeepAwake, working: Callable[[], Awaitable[bool]], after_s: float, every_s: float, stopping: asyncio.Event
) -> None:
    """Hold ``awake`` while ``working`` says work is active or due soon, and ``after_s`` seconds
    after, looking every ``every_s`` seconds, until ``stopping`` is set (item 1671); it lets go on its way out. A
    ``caffeinate`` that cannot be started is raised (the daemon logs the end of this task): the Mac then sleeps as it
    would, and the log says why."""
    last_work_at: float | None = None
    try:
        while not stopping.is_set():
            now: float = time.monotonic()
            busy: bool = await working()
            if busy:
                last_work_at = now
            await awake.switch(holds_awake(busy, last_work_at, now, after_s))
            try:
                async with asyncio.timeout(every_s):
                    await stopping.wait()
            except TimeoutError:
                continue
    finally:
        await awake.stop()
