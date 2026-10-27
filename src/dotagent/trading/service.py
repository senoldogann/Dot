"""Trading as runs and the owner use it (docs/trading.md): the account of the mode ``[trading]`` names, its portfolio,
and orders placed one at a time under the limits.

Every order is judged and placed under one lock, so that two runs never judge against the same day. It is kept before
it reaches a market, so that it counts against the day at once, and an order sent with no answer of what it did stays
counted (``unknown``). The ``STOP`` file stops it as it stops the desktop. A ``paper`` account trades at Coinbase's real
prices with money that is not there: it starts with ``paper_start``, pays ``paper_fee_rate`` of each fill, buys at the
ask and sells at the bid, and the owner may start it again (:meth:`Trading.reset_paper`).
"""

import asyncio
import math
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from dotagent.config import TradingConfig
from dotagent.core.errors import ToolExecutionError
from dotagent.store.db import Store
from dotagent.store.models import TradingOrderRecord
from dotagent.trading import limits
from dotagent.trading.market import CoinbaseMarket
from dotagent.trading.types import Fill, Holding, OrderRequest, Portfolio, ProductRules, Quote
from dotagent.vault.secretstore import SecretStore

_PAPER_SINCE_KEY: str = "trading_paper_since"
# Orders a portfolio and the history read back at most.
_ORDERS_READ: int = 10_000


@dataclass
class TradingSettings:
    """The ``[trading]`` in force: as the config file said at start, or as the owner changed it since in Settings ›
    Trading, which writes the file too. Trading reads it on each call."""

    value: TradingConfig

    def current(self) -> TradingConfig:
        return self.value


class TradingRefused(ToolExecutionError):
    """An order the limits, the mode or the emergency stop do not let through; nothing reached a market."""


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

    async def _quotes(self, config: TradingConfig, currencies: Sequence[str]) -> dict[str, Quote]:
        """The quotes of the products of ``currencies`` among those the owner allows, by base currency."""
        wanted: list[str] = [f"{currency}-{config.quote}" for currency in currencies if currency != config.quote]
        products: list[str] = [product for product in wanted if product in config.products]
        quotes: list[Quote] = list(await asyncio.gather(*(self.market.quote(product) for product in products)))
        return {quote.product.split("-")[0]: quote for quote in quotes}

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
        quotes: dict[str, Quote] = await self._quotes(config, list(balances))
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
        kept: str = await self.store.kv_update(key, lambda value: value if value is not None else repr(equity))
        traded: float = await self.store.trading_traded_since(mode, since)
        return Portfolio(
            mode=mode,
            quote=config.quote,
            holdings=tuple(holdings),
            equity=equity,
            day_start_equity=float(kept),
            day_traded=traded,
        )

    async def place(self, request: OrderRequest, run_id: str, approved: str) -> TradingOrderRecord:
        """Judge ``request`` against the limits and place it on the account ``approved`` names (the mode its action was
        approved for): the order as it ended. Refused with why (:class:`TradingRefused`) before anything reaches a
        market when a limit, the mode or the stop holds it, or when the account is no longer the one approved: an
        order approved for paper waits here behind another, and the owner switched to live meanwhile (the security
        review of item 763). The settings are read once, inside the lock, and the order is judged against the account
        it goes to."""
        async with self._lock:
            if self.failsafe_file.exists():
                raise TradingRefused("the emergency stop is on: no order is placed until the owner releases it", {})
            config: TradingConfig = self.config()
            if config.mode != approved:
                raise TradingRefused(
                    f"the trading account is now {config.mode}, not the {approved} one this order was approved for", {}
                )
            broker: Broker = self._broker(config)
            mode: Literal["paper", "live"] = "paper" if broker is self.paper else "live"
            if request.product not in config.products:
                raise TradingRefused(
                    f"{request.product} is not among the products the owner allows ({', '.join(config.products)})", {}
                )
            quote, rules = await asyncio.gather(self.market.quote(request.product), self.market.rules(request.product))
            portfolio: Portfolio = await self._portfolio(config, broker)
            base: str = request.product.split("-")[0]
            held: float = next((holding.amount for holding in portfolio.holdings if holding.currency == base), 0.0)
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
            why: str | None = limits.refusal(
                config, request.product, request.side, notional, portfolio, rules, cash, held, held * quote.bid
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
                note="",
            )
            await self.store.add_trading_order(order)
            try:
                if self.failsafe_file.exists():
                    # Engaged while the account was read: the order goes nowhere.
                    raise TradingRefused("the emergency stop is on: no order is placed until the owner releases it", {})
                fill: Fill = await broker.execute(order.id, request, quote, rules)
            except TradingRefused as refused:
                failed = order.model_copy(update={"status": "failed", "note": refused.message[:300]})
                await self.store.settle_trading_order(failed)
                raise
            except BaseException as error:
                # Sent, perhaps (an error, the tool's timeout, a stopped run): what it did is not known, so it stays
                # counted against the day, and its record says so instead of staying pending.
                note: str = str(error)[:300] or type(error).__name__
                unknown = order.model_copy(update={"status": "unknown", "note": note})
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
            return filled

    async def history(self, limit: int) -> list[TradingOrderRecord]:
        """The orders of the mode, newest first: the paper account's since its start."""
        broker: Broker = self._broker(self.config())
        if broker is self.paper:
            return await self.store.trading_orders("paper", await self.paper.since(), limit)
        return await self.store.trading_orders("live", 0.0, limit)

    async def reset_paper(self) -> None:
        """Start the paper account again with ``paper_start``: its orders before stay in the record, outside it, and
        today's loss is counted from the start again (the pre-push review of item 763: kept from before, the day showed
        the start as a loss or a gain, and could lift the day's loss limit or hold every buy for nothing)."""
        await self.store.kv_set(_PAPER_SINCE_KEY, repr(self.clock()))
        day, _ = self.day_start()
        await self.store.kv_set(f"trading_day:paper:{day}", repr(self.config().paper_start))


def holdings_text(holdings: Sequence[Holding], quote: str) -> str:
    return ", ".join(f"{holding.amount:.8g} {holding.currency} ({holding.value:.2f} {quote})" for holding in holdings)
