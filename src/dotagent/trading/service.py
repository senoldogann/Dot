"""Trading as runs and the owner use it (docs/trading.md): the account of the mode ``[trading]`` names, its portfolio,
and orders placed one at a time under the limits.

Every order is judged and placed under one lock, so that two runs never judge against the same day. It is kept before
it reaches a market, so that it counts against the day at once, and an order sent with no answer of what it did stays
counted (``unknown``). The ``STOP`` file stops it as it stops the desktop. A ``paper`` account trades at Coinbase's real
prices with money that is not there: it starts with ``paper_start``, pays ``paper_fee_rate`` of each fill, buys at the
ask and sells at the bid, and the owner may start it again (:meth:`Trading.reset_paper`).
"""

import asyncio
import contextlib
import math
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from dotagent.config import ANY_PRODUCT, TradingConfig
from dotagent.core.errors import ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.store.db import Store
from dotagent.store.models import TradingExitRecord, TradingOrderRecord
from dotagent.trading import limits
from dotagent.trading.market import CoinbaseMarket, MarketError
from dotagent.trading.types import Fill, Holding, OrderRequest, Portfolio, ProductRules, Quote
from dotagent.vault.secretstore import SecretStore

_log = get_logger("dotagent.trading")
_PAPER_SINCE_KEY: str = "trading_paper_since"
# What an order's note holds: the run's reason (300 characters at most, the tool's bound) and, once something went
# wrong, what happened, which keeps its room (the pre-push review: a long reason cut it off).
_REASON_CHARS: int = 300
_HAPPENED_CHARS: int = 200
_NOTE_CHARS: int = _REASON_CHARS + 3 + _HAPPENED_CHARS
# The open exits read at a look; a key limited to a portfolio of Dot's own has few.
_EXITS_READ: int = 500
# The orders read back to tell whether an order was kept (:meth:`Trading.order_kept`): those since it was tried.
_SELLS_READ: int = 50
# How long an exit's sell may be held at every look (no working key, Coinbase not taking orders of the product, out of
# reach) before the owner is told, once (the pre-push review of item 770: it was tried again in silence).
HELD_TOLD_S: float = 300.0
# How many currencies held are priced for the account's worth: a key limited to a portfolio of Dot's own holds few;
# past the count the rest are unread, which holds buys (docs/trading.md says so).
_PRICED_AT_MOST: int = 100
_PRICED_AT_ONCE: int = 6
# Orders a portfolio and the history read back at most.
_ORDERS_READ: int = 10_000


@dataclass
class TradingSettings:
    """The ``[trading]`` in force: as the config file said at start, or as the owner changed it since in Settings ›
    Trading, which writes the file too. Trading reads it on each call."""

    value: TradingConfig

    def current(self) -> TradingConfig:
        return self.value


@dataclass(frozen=True)
class ExitEnd:
    """What an exit came to at a look of the watch: the exit as it stands now, and the sell that closed it, if any; or,
    with ``held``, why its sell could not go out at this look, the exit still open (the watch tells the owner once it
    lasts)."""

    exit: TradingExitRecord
    order: TradingOrderRecord | None
    held: str = ""


def _exit_problem(request: OrderRequest, quote: Quote) -> str | None:
    """Why a buy's exit cannot stand against the prices now (item 770): a stop must be under the bid, or the buy would
    be sold at once; a target over the ask, or it would be taken at once."""
    if request.side != "buy" or request.stop is None:
        return None
    if not 0 < request.stop < quote.bid:
        return f"the stop, {request.stop:.8g}, must be under the bid now, {quote.bid:.8g}"
    if request.target is not None and not request.target > quote.ask:
        return f"the target, {request.target:.8g}, must be over the ask now, {quote.ask:.8g}"
    return None


@dataclass(frozen=True)
class _Priced:
    """What pricing the currencies held came to: the quotes found, the currencies Coinbase has no product of in the
    quote, and those whose price could not be read this time."""

    quotes: dict[str, Quote]
    unpriceable: tuple[str, ...]
    unread: tuple[str, ...]


class TradingRefused(ToolExecutionError):
    """An order the limits, the mode or the emergency stop do not let through; nothing reached a market."""


class ExitGone(TradingRefused):
    """An exit's sell that found the exit closed when its turn came: a sell by hand took what it held, or the paper
    account started again, while the sell waited for the lock. Nothing is sold, and nothing is told."""


class ExitHeld(TradingRefused):
    """An exit's sell that may not go now, for what holds every order and passes (the emergency stop, trading off, the
    account switched, Coinbase not taking orders of the product): the exit stays open and is tried again at the next
    look (the pre-push review of item 770: failed for good, it was never watched again once the owner released the
    stop)."""


def _refusal(request: OrderRequest, message: str) -> TradingRefused:
    """The refusal of ``request`` for what holds every order: for an exit's sell, :class:`ExitHeld`."""
    return ExitHeld(message, {}) if request.closing is not None else TradingRefused(message, {})


class Broker(Protocol):
    """An account orders are placed in: what it holds, and an order executed at the market."""

    async def balances(self) -> dict[str, float]:
        """What the account holds, by currency."""
        ...

    async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
        """Place ``request`` as a market order and return what it filled at; raise when it did nothing."""
        ...


def _floor_to(value: float, step: float) -> float:
    """``value`` rounded down to a whole number of ``step``s, as Coinbase takes an amount."""
    return math.floor(value / step + 1e-9) * step


@dataclass
class PaperBroker:
    """The paper account: its holdings are what its filled orders since its start made of ``start`` in the quote."""

    store: Store
    config: Callable[[], TradingConfig]
    clock: Callable[[], float] = time.time

    async def since(self) -> float:
        raw: str | None = await self.store.kv_get(_PAPER_SINCE_KEY)
        return float(raw) if raw is not None else 0.0

    async def balances(self) -> dict[str, float]:
        config: TradingConfig = self.config()
        held: dict[str, float] = {config.quote: config.paper_start}
        for order in await self.store.trading_orders("paper", await self.since(), _ORDERS_READ):
            if order.status != "filled":
                continue
            base: str = order.product.split("-")[0]
            if order.side == "buy":
                held[config.quote] -= order.notional + order.fee
                held[base] = held.get(base, 0.0) + order.size
            else:
                held[config.quote] += order.notional - order.fee
                held[base] = held.get(base, 0.0) - order.size
        return {currency: amount for currency, amount in held.items() if amount > 1e-12 or currency == config.quote}

    async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
        rate: float = self.config().paper_fee_rate
        if request.side == "buy":
            spend: float = request.quote_amount or 0.0
            fee: float = spend * rate
            size: float = _floor_to((spend - fee) / quote.ask, rules.base_increment)
            price: float = quote.ask
        else:
            price = quote.bid
            size = _floor_to(
                request.base_amount if request.base_amount is not None else (request.quote_amount or 0.0) / price,
                rules.base_increment,
            )
            fee = size * price * rate
        if size < rules.base_min_size or size <= 0:
            raise TradingRefused(f"the order buys or sells less than Coinbase's least size of {request.product}", {})
        return Fill(
            order_id=order_id, product=request.product, side=request.side, size=size, price=price, fee=fee,
            ts=self.clock(),
        )  # fmt: skip


@dataclass
class Trading:
    """Trading of this installation. ``settings`` are read on each call, so a change of the owner's in Settings ›
    Trading holds from the next order; ``live`` makes the owner's Coinbase account, ``None`` while no key is kept."""

    store: Store
    settings: TradingSettings
    market: CoinbaseMarket
    zone: ZoneInfo
    failsafe_file: Path
    live: Callable[[], Broker | None] = lambda: None
    # Where the key of the owner's Coinbase account is kept (the login Keychain); ``None`` where there is none to keep.
    key_store: SecretStore | None = None
    clock: Callable[[], float] = time.time
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Since when each exit's sell has been held at every look, and the exits whose held sell the owner was told of.
    _held_since: dict[str, float] = field(default_factory=dict)
    _told_held: set[str] = field(default_factory=set)

    def config(self) -> TradingConfig:
        return self.settings.value

    def __post_init__(self) -> None:
        self.paper: PaperBroker = PaperBroker(self.store, self.config, self.clock)

    def _broker(self, config: TradingConfig) -> Broker:
        mode: str = config.mode
        if mode == "paper":
            return self.paper
        if mode == "live":
            broker: Broker | None = self.live()
            if broker is None:
                raise TradingRefused(
                    "live trading has no Coinbase key: the owner saves one in Settings › Trading", {"mode": mode}
                )
            return broker
        raise TradingRefused(
            "trading is off ([trading] mode); only the owner switches it on, in Settings › Trading", {}
        )

    def day_start(self) -> tuple[str, float]:
        """Today in the owner's zone, and when it began (seconds since the epoch)."""
        now: datetime = datetime.fromtimestamp(self.clock(), self.zone)
        midnight: datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.date().isoformat(), midnight.timestamp()

    async def _quotes(self, config: TradingConfig, currencies: Sequence[str]) -> _Priced:
        """The quotes of ``currencies`` in the quote currency, by base currency, with the currencies Coinbase has no
        product of in the quote (no price to be had, now or later: worth nothing to the account, every time alike) and
        those whose price could not be read this time (a hole in the worth). Every currency held is priced, not only
        the products the owner allows (item 765): a product allowed while it was already held read as a gain of the
        day before, which loosened the day's loss limit by its worth."""
        others: list[str] = [currency for currency in currencies if currency != config.quote]
        held: list[str] = others[:_PRICED_AT_MOST]

        # A few at a time: a burst of one request a currency could meet Coinbase's rate limit, and a 429 would read
        # as a price that could not be read, holding the day's buys.
        at_once: asyncio.Semaphore = asyncio.Semaphore(_PRICED_AT_ONCE)

        async def priced(currency: str) -> Quote | MarketError:
            try:
                async with at_once:
                    return await self.market.quote(f"{currency}-{config.quote}")
            except MarketError as error:
                _log.info("trading_holding_unpriced", currency=currency, error=error.message)
                return error

        answers: list[Quote | MarketError] = list(await asyncio.gather(*(priced(currency) for currency in held)))
        found: dict[str, Quote] = {}
        unpriceable: list[str] = []
        unread: list[str] = list(others[_PRICED_AT_MOST:])
        for currency, answer in zip(held, answers, strict=True):
            if isinstance(answer, Quote):
                found[currency] = answer
            elif answer.no_such_product:
                unpriceable.append(currency)
            else:
                unread.append(currency)
        if unpriceable:
            # A 404 is checked against Coinbase's list of what it trades: a product it trades that answered 404 is a
            # price not read this time, and a day that started without it would read its worth as the day's gain (the
            # pre-push review of item 765). Without the list, no 404 is taken at its word.
            try:
                trades: set[str] = {row.product for row in await self.market.whole_market(config.quote)}
            except MarketError as error:
                _log.info("trading_product_list_unread", error=error.message)
                trades = {f"{currency}-{config.quote}" for currency in unpriceable}
            unread.extend(currency for currency in unpriceable if f"{currency}-{config.quote}" in trades)
            unpriceable = [currency for currency in unpriceable if f"{currency}-{config.quote}" not in trades]
        return _Priced(found, tuple(unpriceable), tuple(unread))

    async def portfolio(self) -> Portfolio:
        """The account of the mode now, what it is worth at the bids, and its day so far."""
        config: TradingConfig = self.config()
        return await self._portfolio(config, self._broker(config))

    async def _portfolio(self, config: TradingConfig, broker: Broker) -> Portfolio:
        """The account of ``broker`` under ``config``, read once by the caller, so that an order is judged against the
        account it goes to whatever the owner switches meanwhile (the security review of item 763). The first look of
        a day keeps what the account was worth then, which the day's loss is counted from."""
        mode: Literal["paper", "live"] = "paper" if broker is self.paper else "live"
        balances: dict[str, float] = await broker.balances()
        priced: _Priced = await self._quotes(config, sorted(balances))
        quotes: dict[str, Quote] = priced.quotes
        holdings: list[Holding] = []
        for currency, amount in sorted(balances.items()):
            if currency == config.quote:
                holdings.append(Holding(currency=currency, amount=amount, value=amount))
            else:
                quote: Quote | None = quotes.get(currency)
                holdings.append(Holding(currency=currency, amount=amount, value=amount * quote.bid if quote else 0.0))
        equity: float = sum(holding.value for holding in holdings)
        day, since = self.day_start()
        key: str = f"trading_day:{mode}:{day}"
        if priced.unread:
            # A worth with a hole in it is not what a day starts from: the first whole look of the day is. Until there
            # is one, the day's start is not known, and no buy is placed (:meth:`place`): a start taken from each look
            # would read every loss as none (the pre-push review of item 765). A currency Coinbase has no product of
            # is no hole: it is worth nothing to the account at every look alike.
            start: str | None = await self.store.kv_get(key)
            kept: str = start if start is not None else repr(equity)
            known: bool = start is not None
        else:
            kept = await self.store.kv_update(key, lambda value: value if value is not None else repr(equity))
            known = True
        traded: float = await self.store.trading_traded_since(mode, since)
        return Portfolio(
            mode=mode,
            quote=config.quote,
            holdings=tuple(holdings),
            equity=equity,
            day_start_equity=float(kept),
            day_traded=traded,
            unpriceable=priced.unpriceable,
            unpriced=priced.unread,
            day_start_known=known,
        )

    async def place(
        self, request: OrderRequest, run_id: str, approved: str, *, lock_held: bool = False
    ) -> TradingOrderRecord:
        """Judge ``request`` against the limits and place it on the account ``approved`` names (the mode its action was
        approved for): the order as it ended. Refused with why (:class:`TradingRefused`) before anything reaches a
        market when a limit, the mode or the stop holds it, or when the account is no longer the one approved: an
        order approved for paper waits here behind another, and the owner switched to live meanwhile (the security
        review of item 763). The settings are read once, inside the lock, and the order is judged against the account
        it goes to. ``lock_held``: the caller holds the lock already (an exit's sell, :meth:`_close`)."""
        guard: contextlib.AbstractAsyncContextManager[object] = contextlib.nullcontext() if lock_held else self._lock
        async with guard:
            if self.failsafe_file.exists():
                raise _refusal(request, "the emergency stop is on: no order is placed until the owner releases it")
            if request.closing is not None:
                # The exit was read before its sell waited here: a sell by hand may have taken from it meanwhile, or
                # the paper account started again. It sells what the exit holds now, or nothing: never what is not
                # its own.
                kept: TradingExitRecord = await self.store.get_trading_exit(request.closing)
                if kept.status != "open" or kept.size <= 0:
                    raise ExitGone(f"exit {kept.id} was {kept.status} before its sell", {"exit_id": kept.id})
                request = request.model_copy(update={"base_amount": min(request.base_amount or kept.size, kept.size)})
            config: TradingConfig = self.config()
            if config.mode != approved:
                raise _refusal(
                    request,
                    f"the trading account is now {config.mode}, not the {approved} one this order was approved for",
                )
            try:
                broker: Broker = self._broker(config)
            except TradingRefused as refused:
                raise _refusal(request, refused.message) from refused
            mode: Literal["paper", "live"] = "paper" if broker is self.paper else "live"
            if request.closing is None and ANY_PRODUCT not in config.products:
                # Refused before any read when it is not named, as no read could make it allowed.
                named: str | None = limits.not_allowed(config, request.product, None)
                if named is not None:
                    raise TradingRefused(named, {})
            quote, rules = await asyncio.gather(self.market.quote(request.product), self.market.rules(request.product))
            exit_problem: str | None = _exit_problem(request, quote)
            if exit_problem is not None:
                raise TradingRefused(exit_problem, {"product": request.product, "bid": quote.bid, "ask": quote.ask})
            if request.closing is not None and not rules.trading:
                raise ExitHeld(f"Coinbase does not take orders of {request.product} now", {})
            try:
                portfolio: Portfolio = await self._portfolio(config, broker)
            except TradingRefused as refused:
                # The live key refused (expired, removed): what holds every order.
                raise _refusal(request, refused.message) from refused
            base: str = request.product.split("-")[0]
            held: float = next((holding.amount for holding in portfolio.holdings if holding.currency == base), 0.0)
            if request.closing is not None and (request.base_amount or 0.0) > held:
                # The owner sold some by hand on Coinbase: the exit sells what is left, which it still protects.
                request = request.model_copy(update={"base_amount": held})
            cash: float = next(
                (holding.amount for holding in portfolio.holdings if holding.currency == config.quote), 0.0
            )
            if request.side == "buy":
                notional: float = request.quote_amount or 0.0
            elif request.base_amount is not None:
                notional = min(request.base_amount, held) * quote.bid
            else:
                notional = request.quote_amount or 0.0
            if request.side == "sell" and request.base_amount is not None and request.base_amount > held + 1e-12:
                raise TradingRefused(f"the account holds {held:g} {base}, less than the order sells", {})
            if request.side == "sell" and request.quote_amount is not None and request.quote_amount > held * quote.bid:
                raise TradingRefused(f"the account's {base} is worth {held * quote.bid:.2f} {config.quote}, less", {})
            if request.side == "buy" and not portfolio.day_start_known:
                raise TradingRefused(
                    f"the account's worth cannot be read whole today (the price in {config.quote} of "
                    f"{', '.join(portfolio.unpriced)} could not be read), so today's loss cannot be counted: no buy "
                    "until it can; a sell is still placed",
                    {"unpriced": ", ".join(portfolio.unpriced)},
                )
            why: str | None = limits.refusal(
                config,
                request.product,
                request.side,
                notional,
                portfolio,
                rules,
                cash,
                held,
                held * quote.bid,
                closing=request.closing is not None,
            )
            if why is not None:
                raise TradingRefused(why, {"product": request.product, "side": request.side, "notional": notional})
            order: TradingOrderRecord = TradingOrderRecord(
                id=f"ord_{secrets.token_hex(8)}",
                mode=mode,
                product=request.product,
                side=request.side,
                notional=notional,
                size=0.0,
                price=0.0,
                fee=0.0,
                status="pending",
                ts=self.clock(),
                run_id=run_id,
                exchange_id="",
                note=request.reason[:_NOTE_CHARS],
            )
            await self.store.add_trading_order(order)
            try:
                if self.failsafe_file.exists():
                    # Engaged while the account was read: the order goes nowhere.
                    raise _refusal(request, "the emergency stop is on: no order is placed until the owner releases it")
                fill: Fill = await broker.execute(order.id, request, quote, rules)
            except TradingRefused as refused:
                failed = order.model_copy(update={"status": "failed", "note": _noted(request.reason, refused.message)})
                await self.store.settle_trading_order(failed)
                raise
            except BaseException as error:
                # Sent, perhaps (an error, the tool's timeout, a stopped run): what it did is not known, so it stays
                # counted against the day, and its record says so instead of staying pending.
                unknown = order.model_copy(
                    update={"status": "unknown", "note": _noted(request.reason, str(error) or type(error).__name__)}
                )
                await asyncio.shield(self.store.settle_trading_order(unknown))
                raise
            filled: TradingOrderRecord = order.model_copy(
                update={
                    "status": "filled",
                    "notional": fill.notional,
                    "size": fill.size,
                    "price": fill.price,
                    "fee": fill.fee,
                    "exchange_id": fill.order_id if fill.order_id != order.id else "",
                }
            )
            await self.store.settle_trading_order(filled)
            if request.side == "buy" and request.stop is not None and mode == "paper":
                # Exits are the paper account's in this item: what Dot sells by itself on the owner's Coinbase account
                # is a later item's, with a review of its own (the owner's choice after eight pre-push reviews).
                await self.store.put_trading_exit(
                    TradingExitRecord(
                        id=f"ext_{secrets.token_hex(8)}",
                        mode=mode,
                        product=request.product,
                        size=fill.size,
                        stop=request.stop,
                        target=request.target,
                        ts=self.clock(),
                        order_id=order.id,
                        run_id=run_id,
                        status="open",
                        closed_ts=None,
                        sell_id="",
                        note="",
                    )
                )
            elif request.side == "sell" and request.closing is None:
                await self._shrink_exits(mode, request.product, fill.size, order.id)
            return filled

    async def _shrink_exits(self, mode: str, product: str, sold: float, sell_id: str) -> None:
        """A sell by hand of ``sold`` of ``product`` takes it from the open exits of the product, oldest first: what it
        sold is no longer there for them to sell (item 770). An exit left with nothing is ``closed``."""
        left: float = sold
        for exit_ in await self.store.trading_exits("open", _EXITS_READ):
            if left <= 0:
                break
            if exit_.mode != mode or exit_.product != product:
                continue
            taken: float = min(left, exit_.size)
            left -= taken
            remaining: float = exit_.size - taken
            if remaining <= exit_.size * 1e-9:
                await self.store.put_trading_exit(
                    exit_.model_copy(
                        update={"size": 0.0, "status": "closed", "closed_ts": self.clock(), "sell_id": sell_id,
                                "note": "sold by an order of a run"}
                    )
                )  # fmt: skip
            else:
                await self.store.put_trading_exit(exit_.model_copy(update={"size": remaining}))

    async def open_exits(self) -> list[TradingExitRecord]:
        """The exits watched now: those of the account in use, oldest first."""
        mode: str = self.config().mode
        return [exit_ for exit_ in await self.store.trading_exits("open", _EXITS_READ) if exit_.mode == mode]

    async def move_exit(self, exit_id: str, stop: float | None, target: float | None) -> TradingExitRecord:
        """Move an open exit's stop or target (item 770), each judged against the prices now as a buy's is: the stop
        under the bid, the target over the ask. Under the order lock, from the exit as it stands then, as every change
        of an exit is made: one written from a copy read before could reopen an exit sold meanwhile, or give back what
        a sell by hand took from it (the pre-push review of item 770)."""
        async with self._lock:
            exit_: TradingExitRecord = await self.store.get_trading_exit(exit_id)
            if exit_.status != "open" or exit_.mode != self.config().mode:
                raise TradingRefused(f"the exit {exit_id} is not one watched now ({exit_.status}, {exit_.mode})", {})
            quote: Quote = await self.market.quote(exit_.product)
            moved: OrderRequest = OrderRequest(
                product=exit_.product,
                side="buy",
                quote_amount=None,
                base_amount=None,
                stop=exit_.stop if stop is None else stop,
                target=exit_.target if target is None else target,
            )
            problem: str | None = _exit_problem(moved, quote)
            if problem is not None:
                raise TradingRefused(problem, {"product": exit_.product, "bid": quote.bid, "ask": quote.ask})
            updated: TradingExitRecord = exit_.model_copy(update={"stop": moved.stop, "target": moved.target})
            await self.store.put_trading_exit(updated)
            return updated

    async def check_exits(self) -> list[ExitEnd]:
        """Sell what an exit of the paper account holds once the bid is at its stop or its target (item 770): the
        daemon's watch calls this every few seconds, with no model and no run. While trading is off or live, the exits
        wait, open, watched again once the paper account is in use (the pre-push review of item 770: marked stale, a
        stop died for good when the owner switched accounts and back); a sell refused leaves the exit ``failed``, as
        does one that may have gone out
        without saying what it did; prices or an account that could not be read, before anything went out, and what
        holds every order for a while (:class:`ExitHeld`) leave it open for the next look, and the other exits are
        looked at all the same. What each exit came to is returned, for the owner to be told, a sell held at every look
        once it has lasted :data:`HELD_TOLD_S`."""
        config: TradingConfig = self.config()
        ends: list[ExitEnd] = []
        watched: list[TradingExitRecord] = []
        if config.mode != "paper" or self.failsafe_file.exists():
            # Off, live, or the owner's emergency stop: nothing is sold, and every exit stays as it is, watched again
            # once the paper account is in use and the stop released; a sell held before is held, and told, anew.
            self._held_since.clear()
            self._told_held.clear()
            return ends
        for exit_ in await self.store.trading_exits("open", _EXITS_READ):
            if exit_.mode == config.mode:
                watched.append(exit_)
        quotes: dict[str, Quote] = {}
        for product in sorted({exit_.product for exit_ in watched}):
            try:
                quotes[product] = await self.market.quote(product)
            except MarketError as error:
                _log.info("trading_exit_unpriced", product=product, error=error.message)
        for exit_ in watched:
            quote: Quote | None = quotes.get(exit_.product)
            if quote is None or _reached(exit_, quote) is None:
                continue
            end: ExitEnd | None = await self._close(exit_.id, quote)
            if end is not None:
                ends.append(end)
        return self._told_once_held(ends)

    def _told_once_held(self, ends: list[ExitEnd]) -> list[ExitEnd]:
        """``ends`` but the held sells that have not lasted :data:`HELD_TOLD_S` at every look yet, or were told before:
        a look that reads the account a moment late is no news, and a sell held for long is told once."""
        now: float = self.clock()
        held_now: set[str] = {end.exit.id for end in ends if end.held}
        # An exit held no more is forgotten: held again later, it is told again once that lasts.
        self._held_since = {exit_id: since for exit_id, since in self._held_since.items() if exit_id in held_now}
        self._told_held &= held_now
        told: list[ExitEnd] = []
        for end in ends:
            if end.held:
                since: float = self._held_since.setdefault(end.exit.id, now)
                if now - since < HELD_TOLD_S or end.exit.id in self._told_held:
                    continue
                self._told_held.add(end.exit.id)
            told.append(end)
        return told

    async def _close(self, exit_id: str, quote: Quote) -> ExitEnd | None:
        """Sell what the exit ``exit_id`` holds, at the market, on the account it was made on, and write what came of
        it: under the order lock, from the exit as it stands then (the pre-push review of item 770: written from a copy
        read before, a change could reopen an exit sold meanwhile, or sell what a sell by hand took from it). None when
        nothing is to be told: the exit closed or moved out of reach before its turn, or its sell never went out."""
        async with self._lock:
            exit_: TradingExitRecord = await self.store.get_trading_exit(exit_id)
            reached: Literal["stopped", "targeted"] | None = _reached(exit_, quote)
            if exit_.status != "open" or reached is None:
                return None
            word: str = "stop" if reached == "stopped" else "target"
            level: float = exit_.stop if reached == "stopped" else exit_.target or 0.0
            sell: OrderRequest = OrderRequest(
                product=exit_.product,
                side="sell",
                quote_amount=None,
                base_amount=exit_.size,
                reason=f"Exit {exit_.id}: the bid {quote.bid:.8g} reached its {word} {level:.8g}.",
                closing=exit_.id,
            )
            started: float = self.clock()
            try:
                order: TradingOrderRecord = await self.place(sell, f"exit:{exit_.id}", exit_.mode, lock_held=True)
            except ExitGone:
                return None
            except ExitHeld as held:
                _log.info("trading_exit_held", exit_id=exit_.id, why=held.message)
                return ExitEnd(exit=exit_, order=None, held=held.message)
            except TradingRefused as refused:
                return await self._ended(exit_.id, "failed", note=refused.message)
            except Exception as error:  # noqa: BLE001 - told apart by what was kept of the sell, below
                if not await self.order_kept(exit_.mode, f"exit:{exit_.id}", started):
                    # Nothing reached Coinbase: its prices or the account could not be read before the sell was kept.
                    # The exit stays open and the next look tries again, the other exits are looked at meanwhile (the
                    # pre-push review of item 770: it was failed for good, and the position left without its stop).
                    _log.info("trading_exit_sell_not_sent", exit_id=exit_.id, error=str(error)[:200])
                    return ExitEnd(exit=exit_, order=None, held=f"its sell could not go out: {str(error)[:200]}")
                # Sent, perhaps: a second sell could sell twice, so the exit is not tried again; the owner is told.
                return await self._ended(
                    exit_.id, "failed", note=f"its sell may have gone through, look at the account: {error}"
                )
            return await self._ended(exit_.id, reached, sell_id=order.id, order=order)

    async def _ended(
        self,
        exit_id: str,
        status: Literal["stopped", "targeted", "failed"],
        note: str = "",
        sell_id: str = "",
        order: TradingOrderRecord | None = None,
    ) -> ExitEnd | None:
        """Write how the exit ``exit_id`` ended over the exit as it stands now, the caller holding the order lock; None
        when it is no longer open (another change ended it first)."""
        exit_: TradingExitRecord = await self.store.get_trading_exit(exit_id)
        if exit_.status != "open":
            return None
        changes: dict[str, object] = {"status": status, "closed_ts": self.clock()}
        if note:
            changes["note"] = note[:_NOTE_CHARS]
        if sell_id:
            changes["sell_id"] = sell_id
        ended: TradingExitRecord = exit_.model_copy(update=changes)
        await self.store.put_trading_exit(ended)
        return ExitEnd(exit=ended, order=order)

    async def order_kept(self, mode: str, run_id: str, since: float) -> bool:
        """Whether an order of ``run_id`` tried from ``since`` was kept as one that went out, or may have (pending,
        unknown or filled): an order is kept before it is sent, so none kept means nothing reached Coinbase."""
        recent: list[TradingOrderRecord] = await self.store.trading_orders(mode, since - 1.0, _SELLS_READ)
        return any(order.run_id == run_id and order.ts >= since and order.status != "failed" for order in recent)

    async def history(self, limit: int) -> list[TradingOrderRecord]:
        """The orders of the mode, newest first: the paper account's since its start."""
        broker: Broker = self._broker(self.config())
        if broker is self.paper:
            return await self.store.trading_orders("paper", await self.paper.since(), limit)
        return await self.store.trading_orders("live", 0.0, limit)

    async def reset_paper(self) -> None:
        """Start the paper account again with ``paper_start``: its orders before stay in the record, outside it, and
        today's loss is counted from the start again (the pre-push review of item 763: kept from before, the day showed
        the start as a loss or a gain, and could lift the day's loss limit or hold every buy for nothing). Its open
        exits close with it: what they would sell is gone, and a later buy's holding is not theirs to sell (item 770).
        Under the lock orders are placed under, so that no sell is judged across the start."""
        async with self._lock:
            now: float = self.clock()
            await self.store.kv_set(_PAPER_SINCE_KEY, repr(now))
            day, _ = self.day_start()
            await self.store.kv_set(f"trading_day:paper:{day}", repr(self.config().paper_start))
            for exit_ in await self.store.trading_exits("open", _EXITS_READ):
                if exit_.mode == "paper":
                    await self.store.put_trading_exit(
                        exit_.model_copy(
                            update={"status": "closed", "closed_ts": now, "note": "the paper account started again"}
                        )
                    )


def _reached(exit_: TradingExitRecord, quote: Quote) -> Literal["stopped", "targeted"] | None:
    """Whether the bid of ``quote`` is at the exit's stop or its target."""
    if quote.bid <= exit_.stop:
        return "stopped"
    if exit_.target and quote.bid >= exit_.target:
        return "targeted"
    return None


def _noted(reason: str, happened: str) -> str:
    """An order's note once something went wrong: the run's reason, kept, and what happened, which keeps its room."""
    said: str = happened[:_HAPPENED_CHARS]
    return f"{reason[:_REASON_CHARS]} — {said}" if reason else said


def holdings_text(holdings: Sequence[Holding], quote: str) -> str:
    return ", ".join(f"{holding.amount:.8g} {holding.currency} ({holding.value:.2f} {quote})" for holding in holdings)
