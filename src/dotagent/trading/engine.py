"""The trend engine (item 818, docs/trading.md): a part of the daemon's scheduler that trades ``[trading] engine_coins``
by the rule of :mod:`dotagent.trading.trend`, with no model. Once an hour it reads each product's daily closes, the
shares the rule wants and what the account holds, and places the steps toward them through
:meth:`~dotagent.trading.service.Trading.place`: the limits of ``[trading]``, the emergency stop and the mode judge each
as they judge a run's order, and what a limit holds back today is done at a later look.

It trades on paper only: an order of the owner's Coinbase account waits for an item of its own and the owner's word.
A run (the daily supervisor of the docs) may pause it with a reason and resume it (:func:`pause`); paused, it holds
what it holds and places nothing. What it saw and did at its last look is kept for the page and the tools
(:func:`read_state`), with what the account made since the engine started beside what holding the first of its coins
would have (item 819)."""

from collections.abc import Sequence

from dotagent.config import TradingConfig
from dotagent.core.logs import get_logger
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.engine_state import (
    START_KEY,
    STATE_KEY,
    EngineResult,
    EngineShare,
    EngineStart,
    EngineState,
    read_pause,
    read_start,
    read_state,
)
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
        before: EngineState | None = await read_state(store)
        # A look that trades nothing keeps what was being brought to its target, and the last result, unless the
        # paper account was started again since: that result was the account's before (the review).
        chasing: tuple[str, ...] = () if before is None else before.chasing
        start: EngineStart | None = await read_start(store)
        current: bool = start is not None and start.paper_since == await self._trading.paper.since()
        result: EngineResult | None = before.result if before is not None and current else None
        if config.mode != "paper":
            notes.append(
                f"the trend engine trades on paper only; trading is {config.mode} ([trading] mode): nothing placed"
            )
        elif (paused := await read_pause(store)) is not None:
            notes.append(f"paused by {paused.by}: {paused.reason}")
        else:
            try:
                shares, placed, notes, chasing, found = await self._trade(config, now, chasing)
                # A price that could not be read for the result keeps the last one, as a look that trades nothing
                # does (the review: the daily supervisor could read none that day).
                if found is not None:
                    result = found
            except MarketError as error:
                notes.append(f"the market could not be read: {error.message}")
        state: EngineState = EngineState(
            looked_at=now,
            shares=shares,
            placed=tuple(placed),
            notes=tuple(notes[:_NOTES_KEPT]),
            chasing=chasing,
            result=result,
        )
        await store.kv_set(STATE_KEY, state.model_dump_json())
        _log.info("trend_engine_looked", placed=len(placed), notes=list(state.notes))
        return state

    async def _trade(
        self, config: TradingConfig, now: float, chasing: tuple[str, ...]
    ) -> tuple[tuple[EngineShare, ...], list[str], list[str], tuple[str, ...], EngineResult | None]:
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
        result: EngineResult | None = await self._result(config, portfolio, now, notes)
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
        # What a step does not bring to its target is traded on at the next look until it is there (item 819); one
        # this look cannot judge is still on its way when it can.
        unfinished: list[str] = [product for product in chasing if product in unjudged]
        steps: list[Step] = steps_toward(
            judged, values, sizes, portfolio.equity, config.engine_band, config.max_order, chasing
        )
        for step in steps:
            try:
                order: TradingOrderRecord = await self._trading.place(_request(step), ENGINE_RUN_ID, "paper")
            except (TradingRefused, MarketError) as refused:
                # A price that could not be read for one order leaves the orders placed before it in the record.
                notes.append(f"{step.side} {step.product} not placed: {refused.message}")
                unfinished.append(step.product)
                continue
            placed.append(order.id)
            if not step.whole:
                unfinished.append(step.product)
        return shares, placed, notes, tuple(unfinished), result

    async def _result(
        self, config: TradingConfig, portfolio: Portfolio, now: float, notes: list[str]
    ) -> EngineResult | None:
        """What the account made since the engine's first look on it, beside holding the first of its coins: the
        start is kept at the first look on an account whose worth is whole, and again when the paper account is
        started again. ``None`` when a price could not be read, said in ``notes``."""
        if portfolio.equity <= 0 or portfolio.unpriced:
            return None
        store: Store = self._runtime.store
        since: float = await self._trading.paper.since()
        start: EngineStart | None = await read_start(store)
        try:
            if start is None or start.paper_since != since:
                product: str = config.engine_products()[0]
                bid: float = (await self._trading.market.quote(product)).bid
                start = EngineStart(
                    started_at=now, paper_since=since, equity=portfolio.equity, product=product, price=bid
                )
                await store.kv_set(START_KEY, start.model_dump_json())
            else:
                bid = (await self._trading.market.quote(start.product)).bid
        except MarketError as error:
            notes.append(f"the result since the start could not be read: {error.message}")
            return None
        return EngineResult(
            started_at=start.started_at,
            product=start.product,
            account=portfolio.equity / start.equity - 1,
            held=bid / start.price - 1,
        )


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
