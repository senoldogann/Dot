"""Coinbase's public market data (Advanced Trade's ``/market`` endpoints): no key, no account, the same prices for paper
and live. Each answer is checked before a number of it is used: a price that is missing, not a number or not above 0
fails the call rather than trading at it."""

import math
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import quote as escape

import httpx

from dotagent.core.errors import ToolExecutionError
from dotagent.trading.types import Candle, MarketRow, ProductDay, ProductRules, Quote

MARKET_URL: str = "https://api.coinbase.com/api/v3/brokerage/market"
_GRANULARITY_S: Mapping[str, int] = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "ONE_HOUR": 3600,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
}
_MOST_CANDLES: int = 300
# How long the whole market's list is kept before it is asked again (it is about a megabyte).
_SNAPSHOT_S: float = 30.0


class MarketError(ToolExecutionError):
    """Coinbase's market data could not be read, or said something that is not a price."""

    @property
    def no_such_product(self) -> bool:
        """Whether Coinbase answered that the product does not exist: no price is to be had, now or later. Measured on
        2026-10-28: ``/products/FOO-EUR/ticker`` answers 404 with ``{"error": "NOT_FOUND", "error_details": "ProductID
        \"FOO-EUR\" could not be found."}``; a product that exists answers 200 whatever its quote."""
        return self.context.get("status") == 404 or "NOT_FOUND" in str(self.context.get("body", ""))


def _positive(payload: Mapping[str, object], key: str, what: str) -> float:
    raw: object = payload.get(key)
    try:
        value: float = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise MarketError(f"Coinbase gave no {what}", {"field": key}) from error
    if not math.isfinite(value) or value <= 0:
        raise MarketError(f"Coinbase gave no usable {what}", {"field": key, "value": str(raw)[:40]})
    return value


def _finite(payload: Mapping[str, object], key: str) -> float | None:
    """The number at ``key`` when it is a finite one, else ``None``: a sum Coinbase may leave out."""
    raw: object = payload.get(key)
    try:
        value: float = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _volume(row: Mapping[str, object]) -> float:
    """A candle's volume, which may be 0; anything that is not a finite number of 0 or more fails the read."""
    raw: object = row.get("volume")
    try:
        value: float = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise MarketError("Coinbase gave no usable volume", {"value": str(raw)[:40]}) from error
    if not math.isfinite(value) or value < 0:
        raise MarketError("Coinbase gave no usable volume", {"value": str(raw)[:40]})
    return value


def granularities() -> tuple[str, ...]:
    return tuple(_GRANULARITY_S)


class CoinbaseMarket:
    """The public market data of Coinbase. ``transport`` replaces the network in tests."""

    def __init__(self, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._timeout_s: float = timeout_s
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._snapshot: tuple[float, str, tuple[MarketRow, ...]] | None = None

    async def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                base_url=MARKET_URL, timeout=self._timeout_s, transport=self._transport, trust_env=False
            ) as client:
                response: httpx.Response = await client.get(path, params=params)
        except httpx.HTTPError as error:
            raise MarketError("Coinbase's market data could not be reached", {"error": type(error).__name__}) from error
        if response.status_code != 200:
            raise MarketError(
                "Coinbase's market data answered with an error",
                {"status": response.status_code, "path": path, "body": response.text[:200]},
            )
        try:
            payload: object = response.json()
        except ValueError as error:
            raise MarketError("Coinbase's market data answered with no JSON", {"path": path}) from error
        if not isinstance(payload, dict):
            raise MarketError("Coinbase's market data answered with something else than an object", {"path": path})
        return payload

    async def quote(self, product: str) -> Quote:
        """The best bid and ask of ``product`` now, and its last trade's price."""
        payload: dict[str, object] = await self._get(f"/products/{escape(product, safe='-')}/ticker", {"limit": "1"})
        trades: object = payload.get("trades")
        last_trade: object = trades[0] if isinstance(trades, list) and trades else {}
        bid: float = _positive(payload, "best_bid", f"bid of {product}")
        ask: float = _positive(payload, "best_ask", f"ask of {product}")
        if ask < bid:
            raise MarketError(f"Coinbase's ask of {product} is under its bid", {"bid": bid, "ask": ask})
        last: float = (
            _positive(last_trade, "price", f"last price of {product}") if isinstance(last_trade, dict) else bid
        )
        return Quote(product=product, bid=bid, ask=ask, last=last, ts=time.time())

    async def day(self, product: str) -> ProductDay:
        """How ``product`` moved over the last day, as Coinbase sums it (item 765); a number it leaves out is None."""
        payload: dict[str, object] = await self._get(f"/products/{escape(product, safe='-')}")
        return ProductDay(
            product=product,
            change_24h_pct=_finite(payload, "price_percentage_change_24h"),
            quote_volume_24h=_finite(payload, "approximate_quote_24h_volume"),
        )

    async def rules(self, product: str) -> ProductRules:
        """What Coinbase takes of an order of ``product``, and whether it trades now."""
        payload: dict[str, object] = await self._get(f"/products/{escape(product, safe='-')}")
        trading: bool = (
            payload.get("status") == "online"
            and payload.get("trading_disabled") is False
            and payload.get("is_disabled") is False
            and payload.get("cancel_only") is False
            and payload.get("limit_only") is False
        )
        return ProductRules(
            quote_volume_24h=_finite(payload, "approximate_quote_24h_volume"),
            product=product,
            base_increment=_positive(payload, "base_increment", f"base increment of {product}"),
            quote_increment=_positive(payload, "quote_increment", f"quote increment of {product}"),
            quote_min_size=_positive(payload, "quote_min_size", f"least order of {product}"),
            base_min_size=_positive(payload, "base_min_size", f"least size of {product}"),
            trading=trading,
        )

    async def whole_market(self, quote: str) -> tuple[MarketRow, ...]:
        """Every spot product priced in ``quote`` that Coinbase trades now, in one request (item 771), the busiest
        first. Kept for :data:`_SNAPSHOT_S`, so that a scan and the watch of the same moment ask once."""
        now: float = time.monotonic()
        kept: tuple[float, str, tuple[MarketRow, ...]] | None = self._snapshot
        if kept is not None and kept[1] == quote and now - kept[0] < _SNAPSHOT_S:
            return kept[2]
        payload: dict[str, object] = await self._get("/products", {"product_type": "SPOT"})
        listed: object = payload.get("products")
        if not isinstance(listed, list):
            raise MarketError("Coinbase gave no product list", {})
        rows: list[MarketRow] = []
        for item in listed:
            if not isinstance(item, dict) or item.get("quote_currency_id") != quote:
                continue
            if not (
                item.get("status") == "online"
                and item.get("trading_disabled") is False
                and item.get("is_disabled") is False
                and item.get("cancel_only") is False
                and item.get("limit_only") is False
            ):
                continue
            price: float | None = _finite(item, "price")
            product: object = item.get("product_id")
            if price is None or price <= 0 or not isinstance(product, str):
                continue
            rows.append(
                MarketRow(
                    product=product,
                    price=price,
                    bid=_finite(item, "best_bid_price"),
                    ask=_finite(item, "best_ask_price"),
                    change_24h_pct=_finite(item, "price_percentage_change_24h"),
                    high_24h=_finite(item, "high_24h"),
                    low_24h=_finite(item, "low_24h"),
                    quote_volume_24h=_finite(item, "approximate_quote_24h_volume") or 0.0,
                )
            )
        found: tuple[MarketRow, ...] = tuple(sorted(rows, key=lambda row: row.quote_volume_24h, reverse=True))
        self._snapshot = (now, quote, found)
        return found

    async def candles(self, product: str, granularity: str, count: int) -> tuple[Candle, ...]:
        """The last ``count`` periods of ``product`` at ``granularity``, oldest first."""
        seconds: int = _GRANULARITY_S[granularity]
        count = max(1, min(count, _MOST_CANDLES))
        end: int = int(time.time())
        params: dict[str, str] = {
            "start": str(end - seconds * count),
            "end": str(end),
            "granularity": granularity,
            "limit": str(count),
        }
        payload: dict[str, object] = await self._get(f"/products/{escape(product, safe='-')}/candles", params)
        rows: object = payload.get("candles")
        if not isinstance(rows, list):
            raise MarketError(f"Coinbase gave no candles of {product}", {})
        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candles.append(
                Candle(
                    start=_positive(row, "start", "candle start"),
                    low=_positive(row, "low", "low"),
                    high=_positive(row, "high", "high"),
                    open=_positive(row, "open", "open"),
                    close=_positive(row, "close", "close"),
                    volume=_volume(row),
                )
            )
        return tuple(sorted(candles, key=lambda candle: candle.start))


def when(ts: float) -> str:
    """``ts`` as an ISO time in UTC, to the minute."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")
