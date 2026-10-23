"""What every route of the server works with."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from dotagent.agent.manager import RunManager
from dotagent.channels.service import Channels
from dotagent.core.errors import ConfigError
from dotagent.runtime import Runtime
from dotagent.scheduler.runner import Scheduler


def no_restart() -> None:
    """The restart of a daemon nothing starts again once it stops: a test's, or ``dotx daemon`` in a terminal."""
    raise ConfigError(
        "Dot was not started by its service, so it cannot start itself again: stop it and start it again by hand, or "
        "install the service (dotx service install)",
        {},
    )


@dataclass(frozen=True)
class ServerContext:
    """The services of one daemon and the address it listens on. ``scheduler`` is ``None`` when the server runs
    without one. ``stopping`` is set once the server starts to stop; streams that would stay open end then.
    ``restart`` stops the daemon so that what started it starts it again (the service's launchd job, item 409); the
    default refuses, for a daemon nothing would start again. ``channels`` is the owner's messaging apps as ways to reach
    Dot (item 572), ``None`` when the server runs without them."""

    runtime: Runtime
    manager: RunManager
    scheduler: Scheduler | None
    token: str
    host: str
    port: int
    started_at: float
    stopping: asyncio.Event
    restart: Callable[[], None] = no_restart
    channels: Channels | None = None
