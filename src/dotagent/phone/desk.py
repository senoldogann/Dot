"""The daemon's phone: the link to WebDriverAgent and its state (item 1196).

The keeper (:mod:`dotagent.phone.keeper`) asks the desk to look and to relaunch the runner; ``phone_status`` asks it for
a fresh look. Nothing here reads the phone's screen or acts on it: that waits for the screen and step tools, which need
every rule the Mac's desktop keeps (a password manager never read, Dot's own app never touched) before they come.
"""

import asyncio
from collections.abc import Callable

from dotagent.config import PhoneConfig
from dotagent.core.errors import PhoneError
from dotagent.core.logs import get_logger
from dotagent.phone.devicectl import DeviceControl
from dotagent.phone.keeper import next_state
from dotagent.phone.types import PhoneProbe, PhoneState
from dotagent.phone.wda import WdaClient

log = get_logger(__name__)

_RELAUNCH_WAIT_S: float = 40.0
_RELAUNCH_POLL_S: float = 0.5
# After a launch that did not bring the server up, the runner is not launched again for this long: a phone out of the
# Mac's reach fails every try (the owner's own watchdog waited 540 s after one).
_RELAUNCH_BACKOFF_S: float = 300.0


class PhoneDesk:
    """The phone as the daemon holds it: ``wda`` reaches WebDriverAgent, ``devices`` the Mac's view of the phone,
    ``clock`` is a monotonic clock in seconds."""

    def __init__(self, config: PhoneConfig, wda: WdaClient, devices: DeviceControl, clock: Callable[[], float]) -> None:
        self._config: PhoneConfig = config
        self._wda: WdaClient = wda
        self._devices: DeviceControl = devices
        self._clock: Callable[[], float] = clock
        self._state: PhoneState | None = None
        # When the last launch of the runner failed, by ``clock``; ``None`` after one that worked.
        self._failed_launch_at: float | None = None

    @property
    def config(self) -> PhoneConfig:
        return self._config

    @property
    def state(self) -> PhoneState | None:
        """The state as the last look left it; ``None`` before the first."""
        return self._state

    def now(self) -> float:
        return self._clock()

    async def aclose(self) -> None:
        await self._wda.aclose()

    async def check(self) -> PhoneState:
        """Look at the phone and keep what was found as its state."""
        probe: PhoneProbe = await self._wda.probe()
        self._state = next_state(self._state, probe, self._clock())
        return self._state

    def mark_notified(self) -> None:
        if self._state is not None:
            self._state = self._state.model_copy(update={"notified": True})

    async def relaunch(self) -> bool:
        """Start the runner app again when the Mac lists the phone as available; ``True`` once WebDriverAgent answers,
        looked for until :data:`_RELAUNCH_WAIT_S` have passed by the desk's clock (a look at a silent address takes its
        whole timeout, and counts). With no ``udid`` configured nothing is started, and within
        :data:`_RELAUNCH_BACKOFF_S` of a launch that failed none is tried: a listing or a launch that raised, or a
        server that stayed silent, starts that wait."""
        udid: str = self._config.udid
        if not udid:
            return False
        failed_at: float | None = self._failed_launch_at
        if failed_at is not None and self._clock() - failed_at < _RELAUNCH_BACKOFF_S:
            return False
        try:
            available: bool = await self._devices.is_available(udid)
        except PhoneError:
            self._failed_launch_at = self._clock()
            raise
        if not available:
            return False
        log.info("phone_runner_relaunch", udid=udid, bundle=self._config.runner_bundle)
        try:
            await self._devices.launch(udid, self._config.runner_bundle)
        except PhoneError:
            self._failed_launch_at = self._clock()
            raise
        started: float = self._clock()
        while self._clock() - started < _RELAUNCH_WAIT_S:
            if (await self._wda.probe()).url is not None:
                self._failed_launch_at = None
                return True
            await asyncio.sleep(_RELAUNCH_POLL_S)
        log.warning("phone_runner_relaunch_unanswered", udid=udid, waited_s=round(self._clock() - started, 1))
        self._failed_launch_at = self._clock()
        return False
