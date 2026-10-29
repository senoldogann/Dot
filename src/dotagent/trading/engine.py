"""The trend engine (item 818, docs/trading.md): a part of the daemon's scheduler that trades ``[trading] engine_coins``
by the rule of :mod:`dotagent.trading.trend`, with no model. Once an hour it reads each product's daily closes, the
shares the rule wants and what the account holds, and places the steps toward them through
:meth:`~dotagent.trading.service.Trading.place`: the limits of ``[trading]``, the emergency stop and the mode judge each
as they judge a run's order, and what a limit holds back today is done at a later look.

It trades on paper only: an order of the owner's Coinbase account waits for an item of its own and the owner's word.
A run (the daily supervisor of the docs) may pause it with a reason and resume it (:func:`pause`); paused, it holds
what it holds and places nothing. What it saw and did at its last look is kept for the page and the tools
(:func:`read_state`)."""

from collections.abc import Sequence

from dotagent.config import TradingConfig
from dotagent.core.logs import get_logger
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.engine_state import STATE_KEY, EngineShare, EngineState, read_pause
from dotagent.trading.market import MarketError
from dotagent.trading.service import Trading, TradingRefused
from dotagent.trading.trend import DAYS_NEEDED, VOL_DAYS, Step, steps_toward, strength, target_weights
from dotagent.trading.types import Candle, OrderRequest, Portfolio

_log = get_logger(__name__)

# The run id the engine's orders are kept under: no run places them.
ENGINE_RUN_ID: str = "trend-engine"
ENGINE_LOOK_S: float = 3600.0
_DAY_S: float = 86_400.0
# The daily closes asked for: the rule's and a few more, as Coinbase leaves a day out now and then.
_CANDLES: int = max(DAYS_NEEDED, VOL_DAYS + 1) + 20
_NOTES_KEPT: int = 10


def closed_days(candles: Sequence[Candle], now: float) -> list[float]:
    """The closes of the days that have ended by ``now``: today's period is still trading, and its last price is no
    close the rule was played with."""
    return [candle.close for candle in candles if candle.start + _DAY_S <= now]


class TrendEngine:
    """The engine of the daemon, ticked by the scheduler; a look that fails is logged and tried at the next hour."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime: Runtime = runtime
        self._looked_at: float = 0.0

    @property
    def _trading(self) -> Trading:
        return self._runtime.trading

    async def tick(self, now: float) -> None:
        """Look and trade when an hour has passed since the last look; nothing while the engine is off."""
        config: TradingConfig = self._trading.config()
        if config.engine != "trend" or now - self._looked_at < ENGINE_LOOK_S:
            return
        self._looked_at = now
        await self.look(config, now)

    async def look(self, config: TradingConfig, now: float) -> EngineState:
        """One look: the shares the rule wants, and the orders that bring the account toward them."""
        store: Store = self._runtime.store
        notes: list[str] = []
        placed: list[str] = []
        shares: tuple[EngineShare, ...] = ()
        if config.mode != "paper":
            notes.append(
                f"the trend engine trades on paper only; trading is {config.mode} ([trading] mode): nothing placed"
            )
        elif (paused := await read_pause(store)) is not None:
            notes.append(f"paused by {paused.by}: {paused.reason}")
        else:
            try:
                shares, placed, notes = await self._trade(config, now)
            except MarketError as error:
                notes.append(f"the market could not be read: {error.message}")
        state: EngineState = EngineState(
            looked_at=now, shares=shares, placed=tuple(placed), notes=tuple(notes[:_NOTES_KEPT])
        )
        await store.kv_set(STATE_KEY, state.model_dump_json())
        _log.info("trend_engine_looked", placed=len(placed), notes=list(state.notes))
        return state

    async def _trade(self, config: TradingConfig, now: float) -> tuple[tuple[EngineShare, ...], list[str], list[str]]:
        products: list[str] = config.engine_products()
        closes: dict[str, list[float]] = {}
        for product in products:
            candles: tuple[Candle, ...] = await self._trading.market.candles(product, "ONE_DAY", _CANDLES)
            closes[product] = closed_days(candles, now)
        portfolio: Portfolio = await self._trading.portfolio()
        values: dict[str, float] = {}
        sizes: dict[str, float] = {}
        for holding in portfolio.holdings:
            held: str = f"{holding.currency}-{config.quote}"
            if held in closes:
                values[held] = holding.value
                sizes[held] = holding.amount
        notes: list[str] = []
        # A product Coinbase gave too few days of is not judged at this look, rather than sold for want of closes: a
        # coin listed lately is held by the rule once it has them, but one held already keeps what it holds, and its
        # share is not given to the others (the review: it was, and they were bought up and sold back).
        unjudged: list[str] = [product for product, series in closes.items() if strength(series) is None]
        for product in unjudged:
            notes.append(
                f"{product}: Coinbase gave {len(closes[product])} daily closes, the rule needs {DAYS_NEEDED}; "
                "nothing traded of it at this look"
            )
        kept: int = len([product for product in unjudged if sizes.get(product, 0.0) > 0])
        targets: dict[str, float] = target_weights(closes, config.engine_target_vol, kept)
        shares: tuple[EngineShare, ...] = (
            tuple(
                EngineShare(product=product, weight=values.get(product, 0.0) / portfolio.equity, target=target)
                for product, target in targets.items()
            )
            if portfolio.equity > 0
            else ()
        )
        placed: list[str] = []
        judged: dict[str, float] = {product: target for product, target in targets.items() if product not in unjudged}
        for step in steps_toward(judged, values, sizes, portfolio.equity, config.engine_band, config.max_order):
            try:
                order: TradingOrderRecord = await self._trading.place(_request(step), ENGINE_RUN_ID, "paper")
            except (TradingRefused, MarketError) as refused:
                # A price that could not be read for one order leaves the orders placed before it in the record.
                notes.append(f"{step.side} {step.product} not placed: {refused.message}")
                continue
            placed.append(order.id)
        return shares, placed, notes


def _request(step: Step) -> OrderRequest:
    reason: str = (
        f"Trend engine: {step.product} is {step.weight:.0%} of the account, the rule wants {step.target:.0%} "
        "(daily closes over 20, 50, 100 and 200 days, sized to its volatility)"
    )
    return OrderRequest(
        product=step.product,
        side=step.side,
        quote_amount=step.quote_amount,
        base_amount=step.base_amount,
        reason=reason,
    )
