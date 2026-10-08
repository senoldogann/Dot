"""What every route of the server works with."""

import asyncio
from dataclasses import dataclass

from dotagent.agent.manager import RunManager
from dotagent.runtime import Runtime
from dotagent.scheduler.runner import Scheduler


@dataclass(frozen=True)
class ServerContext:
    """The services of one daemon and the address it listens on. ``scheduler`` is ``None`` when the server runs
    without one. ``stopping`` is set once the server starts to stop; streams that would stay open end then."""

    runtime: Runtime
    manager: RunManager
    scheduler: Scheduler | None
    token: str
    host: str
    port: int
    started_at: float
    stopping: asyncio.Event
