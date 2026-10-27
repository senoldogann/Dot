"""The daemon's watch of trading: with no model and no run, at each tick of the scheduler, it looks at the open exits
and sells what one holds once the bid reaches its stop or its target, and tells the owner (item 770); and it looks at
the market the owner allows about once a minute, and starts the schedule ``[trading] wake_schedule`` names when a
product moves (item 772), day and night. A run decides what to buy and where it is wrong; the watch keeps to it between
runs, in seconds rather than at the next run's hour, and wakes one when the market does what is worth a look."""

import time
from collections.abc import Awaitable, Callable

from dotagent.config import TradingConfig
from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.notify.owner import tell_owner
from dotagent.runtime import Runtime
from dotagent.trading.limits import market_allows
from dotagent.trading.market import MarketError
from dotagent.trading.service import ExitEnd, Trading
from dotagent.trading.signals import Move, PriceMemory, move_words
from dotagent.trading.types import MarketRow

_log = get_logger("dotagent.trading.watch")
# How often the open exits are looked at, at most: the scheduler ticks every 15 seconds by default.
EXIT_LOOK_S: float = 10.0
# How often the market is looked at for a move, at most (Coinbase's list: about 70 kB compressed, measured).
MARKET_LOOK_S: float = 60.0
# How long a product that moved wakes nothing again, whether its move woke a run or found none to wake.
PRODUCT_REST_S: float = 7200.0
# How many of the moves found at one look the woken run is told of, the largest first.
_MOVES_TOLD: int = 3
# The day's count of runs woken (with ":<day>"), and when the last one was.
WAKES_KEY: str = "trading_wakes"
LAST_WAKE_KEY: str = "trading_wake_last"
_SETTINGS_ROUTE: str = "#/settings/trading"

# What starts a run of a schedule because the market moved: the scheduler's fire_for_signal (schedule id, now, what
# moved).
type Wake = Callable[[str, float, str], Awaitable[object]]


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
    """The watch of the open exits and of the market, ticked by the scheduler; a tick that fails is logged and tried at
    the next one. ``wake`` starts a run when the market moves; without it, the market is not looked at."""

    def __init__(self, runtime: Runtime, wake: Wake | None = None) -> None:
        self._runtime: Runtime = runtime
        self._wake: Wake | None = wake
        self._looked_at: float = 0.0
        self._market_looked_at: float = 0.0
        self._memory: PriceMemory = PriceMemory()
        # When each product last moved enough to wake a run, so that one move wakes one run at most.
        self._rested: dict[str, float] = {}

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

    async def look_at_market(self, now: float) -> None:
        """Look at the market the owner allows when it is time, and start the schedule ``[trading] wake_schedule``
        names when a product moved (:class:`~dotagent.trading.signals.PriceMemory`): within ``wake_per_day`` runs a day
        and ``wake_gap_min`` minutes apart, a product resting :data:`PRODUCT_REST_S` once it moved. Nothing while
        trading is off or no schedule is named; a run that cannot start (a run of the schedule at work, the emergency
        stop, the schedule switched off or gone) is logged and not counted."""
        config: TradingConfig = self._trading.config()
        if config.mode == "off" or not config.wake_schedule or self._wake is None:
            return
        if now - self._market_looked_at < MARKET_LOOK_S:
            return
        self._market_looked_at = now
        try:
            rows: tuple[MarketRow, ...] = await self._trading.market.whole_market(config.quote)
        except MarketError as error:
            _log.info("trading_market_not_looked_at", error=error.message)
            return
        self._memory.add(now, [row for row in rows if market_allows(config, row)])
        moves: list[Move] = [
            move
            for move in self._memory.moves(now, config.wake_move_pct)
            if now - self._rested.get(move.product, now - PRODUCT_REST_S) >= PRODUCT_REST_S
        ]
        if not moves:
            return
        for move in moves:
            self._rested[move.product] = now
        held: str | None = await self._held(config, now)
        if held is not None:
            _log.info("trading_wake_held", products=[move.product for move in moves], why=held)
            return
        signal: str = f"{move_words(moves[:_MOVES_TOLD], config.quote)}. Look at what moved first."
        try:
            await self._wake(config.wake_schedule, now, signal)
        except DotError as error:
            _log.info("trading_wake_not_started", schedule_id=config.wake_schedule, error=error.message)
            return
        day: str = self._trading.day_start()[0]
        await self._runtime.store.kv_update(f"{WAKES_KEY}:{day}", lambda value: str(int(value or 0) + 1))
        await self._runtime.store.kv_set(LAST_WAKE_KEY, repr(now))
        _log.info("trading_wake_started", schedule_id=config.wake_schedule, products=[move.product for move in moves])

    async def _held(self, config: TradingConfig, now: float) -> str | None:
        """Why no run may be woken now (the day's count reached, the last one less than the gap ago), or None."""
        store = self._runtime.store
        woken: int = int(await store.kv_get(f"{WAKES_KEY}:{self._trading.day_start()[0]}") or 0)
        if woken >= config.wake_per_day:
            return f"{woken} runs woken today, of {config.wake_per_day}"
        last: str | None = await store.kv_get(LAST_WAKE_KEY)
        if last is not None and now - float(last) < config.wake_gap_min * 60.0:
            return f"the last was woken {(now - float(last)) / 60.0:.0f} minutes ago, of {config.wake_gap_min:g}"
        return None
