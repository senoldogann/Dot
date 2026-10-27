"""The daemon's watch of trading (item 770): with no model and no run, at each tick of the scheduler, it looks at the
open exits and sells what one holds once the bid reaches its stop or its target, day and night, and tells the owner.
A run decides what to buy and where it is wrong; the watch keeps to it between runs, in seconds rather than at the next
run's hour."""

import time

from dotagent.core.logs import get_logger
from dotagent.notify.owner import tell_owner
from dotagent.runtime import Runtime
from dotagent.trading.market import MarketError
from dotagent.trading.service import ExitEnd, Trading

_log = get_logger("dotagent.trading.watch")
# How often the open exits are looked at, at most: the scheduler ticks every 15 seconds by default.
EXIT_LOOK_S: float = 10.0
_SETTINGS_ROUTE: str = "#/settings/trading"


def end_words(end: ExitEnd, quote: str) -> tuple[str, str]:
    """The title and the line the owner is told of what an exit came to."""
    exit_ = end.exit
    base: str = exit_.product.split("-")[0]
    if end.held:
        return (
            f"Exit of {exit_.product} cannot sell yet",
            f"It should sell now, but {end.held}. Dot tries again at each look.",
        )
    account: str = "paper" if exit_.mode == "paper" else "your Coinbase account"
    if end.order is not None:
        word: str = "Stop" if exit_.status == "stopped" else "Target"
        return (
            f"{word} reached: {exit_.product} sold",
            f"Sold {end.order.size:.8g} {base} at {end.order.price:.8g} {quote} ({end.order.notional:.2f} {quote}) on "
            f"{account}.",
        )
    return (f"Exit of {exit_.product} did not sell", exit_.note or "Its sell was refused.")


class TradingWatch:
    """The watch of the open exits, ticked by the scheduler; a tick that fails is logged and tried at the next one."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime: Runtime = runtime
        self._looked_at: float = 0.0

    @property
    def _trading(self) -> Trading:
        return self._runtime.trading

    async def tick(self, now: float) -> None:
        """Look at the open exits when it is time; nothing while trading is off."""
        if self._trading.config().mode == "off" or now - self._looked_at < EXIT_LOOK_S:
            return
        self._looked_at = now
        started: float = time.monotonic()
        try:
            ends: list[ExitEnd] = await self._trading.check_exits()
        except MarketError as error:
            _log.info("trading_exits_not_looked_at", error=error.message)
            return
        quote: str = self._trading.config().quote
        for end in ends:
            title, line = end_words(end, quote)
            _log.info(
                "trading_exit_ended",
                exit_id=end.exit.id,
                status=end.exit.status,
                order_id=end.order.id if end.order is not None else None,
                seconds=round(time.monotonic() - started, 3),
            )
            await tell_owner(self._runtime.bus, title, line, _SETTINGS_ROUTE)
