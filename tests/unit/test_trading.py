"""Trading on Coinbase (docs/trading.md): the limits that judge every order before a market sees it, in every mode;
the paper account, which trades at the market's prices with money that is not there; and the order of a run, placed in
the account it was judged for. Coinbase is a stand-in (``httpx.MockTransport``): no request leaves the Mac."""

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio

from dotagent.config import TradingConfig, load_default_config
from dotagent.store.db import Store
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.limits import refusal
from dotagent.trading.market import CoinbaseMarket, MarketError
from dotagent.trading.service import Trading, TradingRefused, TradingSettings
from dotagent.trading.types import OrderRequest, Portfolio, ProductRules

_ZONE: ZoneInfo = ZoneInfo("Europe/Helsinki")
_PRICES: dict[str, tuple[str, str, str]] = {"BTC-EUR": ("50000.00", "50010.00", "50005.00")}


def _market_answer(request: httpx.Request) -> httpx.Response:
    path: str = request.url.path
    if path.endswith("/market/products"):
        # Coinbase's list of what it trades: the products the stand-in has a price of.
        listed: list[dict[str, object]] = [
            {
                "product_id": product, "quote_currency_id": product.split("-")[1], "status": "online",
                "trading_disabled": False, "is_disabled": False, "cancel_only": False, "limit_only": False,
                "price": last, "approximate_quote_24h_volume": "1000000",
            }
            for product, (_, _, last) in _PRICES.items()
        ]  # fmt: skip
        return httpx.Response(200, content=json.dumps({"products": listed}).encode())
    product: str = path.split("/products/")[1].split("/")[0]
    if product not in _PRICES:
        # A product Coinbase is down for, against one it does not have.
        status, body = (503, b"unavailable") if product.startswith("DOWN") else (404, b'{"error": "NOT_FOUND"}')
        return httpx.Response(status, content=body)
    bid, ask, last = _PRICES[product]
    if path.endswith("/ticker"):
        body: dict[str, object] = {"trades": [{"price": last}], "best_bid": bid, "best_ask": ask}
    else:
        body = {
            "product_id": product, "status": "online", "trading_disabled": False, "is_disabled": False,
            "cancel_only": False, "limit_only": False, "base_increment": "0.00000001", "quote_increment": "0.01",
            "quote_min_size": "1", "base_min_size": "0.00000001",
        }  # fmt: skip
    return httpx.Response(200, content=json.dumps(body).encode())


def _config(**changes: object) -> TradingConfig:
    base: TradingConfig = load_default_config().trading
    return base.model_copy(update={"mode": "paper", "products": ["BTC-EUR", "ETH-EUR"], **changes})


class _Clock:
    def __init__(self) -> None:
        self.now: float = 1_793_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    yield opened
    await opened.close()


@pytest.fixture
def clock() -> Iterator[_Clock]:
    yield _Clock()


def _trading(store: Store, settings: TradingSettings, clock: _Clock, tmp_path: Path) -> Trading:
    return Trading(
        store=store,
        settings=settings,
        market=CoinbaseMarket(5.0, httpx.MockTransport(_market_answer)),
        zone=_ZONE,
        failsafe_file=tmp_path / "STOP",
        clock=clock,
    )


def _rules(minimum: float = 1.0, trading: bool = True) -> ProductRules:
    return ProductRules(
        product="BTC-EUR", base_increment=1e-8, quote_increment=0.01, quote_min_size=minimum, base_min_size=1e-8,
        trading=trading,
    )  # fmt: skip


def _portfolio(traded: float = 0.0, change: float = 0.0) -> Portfolio:
    return Portfolio(
        mode="paper", quote="EUR", holdings=(), equity=1000.0 + change, day_start_equity=1000.0, day_traded=traded
    )


def test_each_limit_refuses_what_is_past_it_and_a_sell_is_never_held_by_the_days_loss() -> None:
    config: TradingConfig = _config(max_order=25.0, max_day=100.0, max_day_loss=25.0, max_position=200.0)

    def judged(notional: float, side: str = "buy", **kwargs: object) -> str | None:
        args: dict[str, object] = {
            "product": "BTC-EUR", "portfolio": _portfolio(), "rules": _rules(), "cash": 1000.0, "held": 0.0,
            "held_value": 0.0, **kwargs,
        }  # fmt: skip
        return refusal(config, side=side, notional=notional, **args)  # type: ignore[arg-type]

    assert judged(20.0) is None
    assert "most one order may be" in (judged(25.01) or "")
    assert "not among the products" in (judged(10.0, product="DOGE-EUR") or "")
    assert "least order" in (judged(0.5) or "")
    assert "does not take orders" in (judged(10.0, rules=_rules(trading=False)) or "")
    assert "still add up to" in (judged(20.0, portfolio=_portfolio(traded=90.0)) or "")
    assert "as much as a day may lose" in (judged(10.0, portfolio=_portfolio(change=-25.0)) or "")
    assert "most one product may be" in (judged(20.0, held=0.004, held_value=190.0) or "")
    assert "less than the order" in (judged(20.0, cash=10.0) or "")
    # A sell after the day's loss lowers what is at stake: it goes through, as long as there is something to sell.
    assert judged(10.0, "sell", portfolio=_portfolio(change=-30.0), held=0.001, held_value=50.0) is None
    assert "holds no BTC" in (judged(10.0, "sell") or "")
    off: TradingConfig = config.model_copy(update={"mode": "off"})
    assert "trading is off" in (refusal(off, "BTC-EUR", "buy", 10.0, _portfolio(), _rules(), 1000.0, 0.0, 0.0) or "")


def test_the_config_refuses_products_of_another_quote_and_an_order_cap_over_the_day() -> None:
    with pytest.raises(ValueError, match="priced in EUR"):
        TradingConfig.model_validate({**_config().model_dump(), "products": ["BTC-USD"]})
    with pytest.raises(ValueError, match="max_order is more than max_day"):
        TradingConfig.model_validate({**_config().model_dump(), "max_order": 500.0, "max_day": 100.0})


async def test_the_paper_account_buys_at_the_ask_sells_at_the_bid_and_pays_the_fee(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    trading: Trading = _trading(store, TradingSettings(_config(paper_fee_rate=0.01)), clock, tmp_path)
    bought: TradingOrderRecord = await trading.place(OrderRequest(product="BTC-EUR", side="buy", quote_amount=20.0,
                                                                  base_amount=None), "run_a", "paper")  # fmt: skip
    assert bought.status == "filled" and bought.price == 50010.0
    assert bought.fee == pytest.approx(0.2) and bought.size == pytest.approx(19.8 / 50010.0, abs=1e-8)
    portfolio: Portfolio = await trading.portfolio()
    cash: float = next(h.amount for h in portfolio.holdings if h.currency == "EUR")
    assert cash == pytest.approx(1000.0 - bought.notional - bought.fee)
    assert portfolio.day_traded == pytest.approx(bought.notional)
    sold: TradingOrderRecord = await trading.place(
        OrderRequest(product="BTC-EUR", side="sell", quote_amount=None, base_amount=bought.size), "run_a", "paper"
    )
    assert sold.price == 50000.0 and sold.size == bought.size and sold.fee == pytest.approx(sold.notional * 0.01)
    after: Portfolio = await trading.portfolio()
    assert [h.currency for h in after.holdings] == ["EUR"]
    # The round trip lost the spread and two fees, which the day counts.
    assert after.day_change == pytest.approx(-(bought.fee + sold.fee + bought.size * 10.0), abs=1e-6)
    # Started again, the paper account holds what it started with; its orders stay in the record, outside it.
    clock.now += 1
    await trading.reset_paper()
    started: Portfolio = await trading.portfolio()
    assert started.equity == pytest.approx(1000.0)
    # Today's loss is counted from the new start, not from the account before it.
    assert started.day_change == pytest.approx(0.0)
    assert await trading.history(10) == []


async def test_the_days_orders_stop_at_the_cap_and_the_stop_file_stops_every_order(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    trading: Trading = _trading(store, TradingSettings(_config(max_order=25.0, max_day=50.0)), clock, tmp_path)
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=25.0, base_amount=None)
    await trading.place(buy, "run_a", "paper")
    await trading.place(buy, "run_a", "paper")
    with pytest.raises(TradingRefused, match="still add up to"):
        await trading.place(
            OrderRequest(product="BTC-EUR", side="buy", quote_amount=5.0, base_amount=None), "run_a", "paper"
        )
    # The next day in the owner's zone starts again.
    clock.now += 86_400
    await trading.place(buy, "run_a", "paper")
    (tmp_path / "STOP").write_text("")
    with pytest.raises(TradingRefused, match="emergency stop"):
        await trading.place(buy, "run_a", "paper")


async def test_switched_off_or_live_with_no_key_nothing_is_placed(store: Store, clock: _Clock, tmp_path: Path) -> None:
    settings: TradingSettings = TradingSettings(_config())
    trading: Trading = _trading(store, settings, clock, tmp_path)
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=10.0, base_amount=None)
    settings.value = settings.value.model_copy(update={"mode": "off"})
    with pytest.raises(TradingRefused, match="trading is off"):
        await trading.place(buy, "run_a", "off")
    settings.value = settings.value.model_copy(update={"mode": "live"})
    with pytest.raises(TradingRefused, match="no Coinbase key"):
        await trading.place(buy, "run_a", "live")
    # An order approved for paper is not placed once the account is live, whatever the tool checked before it.
    with pytest.raises(TradingRefused, match="not the paper one this order was approved for"):
        await trading.place(buy, "run_a", "paper")
    assert await store.trading_orders("paper", 0.0, 10) == [] and await store.trading_orders("live", 0.0, 10) == []


async def test_a_price_coinbase_did_not_give_fails_the_read_rather_than_trading_at_it() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"trades": [], "best_bid": "", "best_ask": "50010"}')

    with pytest.raises(MarketError, match="no bid"):
        await CoinbaseMarket(5.0, httpx.MockTransport(broken)).quote("BTC-EUR")


async def test_a_live_order_is_money_to_the_policy_and_one_judged_for_paper_never_goes_live(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The order tool: a live order carries ``trade`` (the owner says yes in every mode but yolo, and a yolo run
    that read outside content has the review model check it); a paper one moves no money. An order approved while the
    account was paper is not placed once the owner switched to live while it waited, nor the reverse."""
    from types import SimpleNamespace
    from typing import cast

    from dotagent.core.errors import ToolInputError
    from dotagent.tools.base import PreparedCall, ToolResult
    from dotagent.tools.context import ToolContext
    from dotagent.tools.trading import build_trading_tools

    settings: TradingSettings = TradingSettings(_config())
    trading: Trading = _trading(store, settings, clock, tmp_path)
    ctx: ToolContext = cast(ToolContext, SimpleNamespace(runtime=SimpleNamespace(trading=trading), run_id="run_a"))
    order_tool = {tool.spec.name: tool for tool in build_trading_tools()}["trading_order"]
    raw: dict[str, object] = {
        "product": "BTC-EUR",
        "side": "buy",
        "quote_amount": 10.0,
        "stop_price": 49000.0,
        "reason": "The hour's candles closed above the SMA20 with RSI 55; a small buy, sold under 49,000.",
    }
    paper: PreparedCall = await order_tool.prepare(raw, ctx)
    assert (paper.action.flags, paper.action.risk) == ((), "low")
    settings.value = settings.value.model_copy(update={"mode": "live"})
    live: PreparedCall = await order_tool.prepare(raw, ctx)
    assert (live.action.flags, live.action.risk) == (("trade",), "high") and "real money" in live.action.summary
    switched: ToolResult = await paper.run(ctx)
    assert switched.is_error and "not the one this order was judged for" in switched.text
    settings.value = settings.value.model_copy(update={"mode": "paper"})
    assert (await live.run(ctx)).is_error
    assert await store.trading_orders("paper", 0.0, 10) == [] and await store.trading_orders("live", 0.0, 10) == []
    placed: ToolResult = await paper.run(ctx)
    assert not placed.is_error and "paper account" in placed.text, placed.text
    # The reason is kept with the order and shown with it (item 765).
    (kept,) = await store.trading_orders("paper", 0.0, 10)
    assert kept.note.startswith("The hour's candles closed above the SMA20")
    # An order with no reason, or with spaces for one, is not placed.
    with pytest.raises(ToolInputError):
        await order_tool.prepare({"product": "BTC-EUR", "side": "buy", "quote_amount": 10.0}, ctx)
    with pytest.raises(ToolInputError):
        await order_tool.prepare({**raw, "reason": "ok" + " " * 30}, ctx)
    # A sell must say how much; a buy says what to spend, and where it is sold if wrong (item 770).
    with pytest.raises(ToolInputError):
        await order_tool.prepare({**raw, "quote_amount": None, "base_amount": 0.1}, ctx)
    with pytest.raises(ToolInputError, match="a buy gives stop_price"):
        await order_tool.prepare({**raw, "stop_price": None}, ctx)
    assert "sold at 49000 or under" in paper.action.summary


def test_a_live_order_is_asked_of_the_owner_in_every_mode_that_asks_and_dot_places_it_on_their_yes() -> None:
    """The pre-push review of item 763: as ``financial`` a live order was a hand-off, the owner's own hands' to do, so
    their yes placed nothing and the run went on as if it had bought. ``trade`` is a question, the owner's to answer in
    every mode that asks (never the review model's, never a rule's to let through), whose yes runs the order; a yolo run
    asks nothing, and one that read outside content has the review model check it (item 525)."""
    from dotagent.policy.engine import checked_when_tainted, decide, route_for
    from dotagent.policy.types import ActionInfo
    from dotagent.store.models import RuleRecord

    order: ActionInfo = ActionInfo(
        tool="trading_order", kind="network", risk="high", summary="Buy 20.00 EUR of BTC-EUR on your Coinbase account",
        target="trading:live:BTC-EUR", flags=("trade",),
    )  # fmt: skip
    allow: RuleRecord = RuleRecord(
        id="rule-1", position=1, enabled=True, behavior="allow", tool_glob="trading_order", match_regex=".*",
        description="orders", created_at=0.0,
    )  # fmt: skip
    for preset in ("aggressive", "balanced", "cautious"):
        asked = decide(order, "task", False, preset, [allow], None)  # type: ignore[arg-type]
        assert (asked.verdict, asked.source) == ("ask", "trade"), preset
    asked = decide(order, "task", False, "aggressive", [], None)
    assert route_for(asked, "auto") == "user" and route_for(asked, "ask") == "user"
    assert route_for(asked, "yolo") == "waived"
    assert checked_when_tainted(order)


async def test_an_order_cut_off_while_it_was_sent_is_kept_unknown_and_counts_against_the_day(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 763: a tool that ran out of time, or a run stopped, cancelled the order's call and
    left its record pending for good. It is ``unknown`` now: it may have been placed, so it still counts."""
    import asyncio

    from dotagent.trading.types import Fill, ProductRules, Quote

    class _Slow:
        async def balances(self) -> dict[str, float]:
            return {"EUR": 500.0}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            await asyncio.sleep(60)
            raise AssertionError("not reached")

    settings: TradingSettings = TradingSettings(_config(mode="live"))
    trading: Trading = _trading(store, settings, clock, tmp_path)
    trading.live = _Slow
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=20.0, base_amount=None)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(trading.place(buy, "run_a", "live"), 0.3)
    (kept,) = await store.trading_orders("live", 0.0, 10)
    assert kept.status == "unknown" and kept.note == "CancelledError"
    assert await store.trading_traded_since("live", 0.0) == pytest.approx(20.0)


async def test_an_order_is_judged_against_the_account_it_goes_to_whatever_is_switched_meanwhile(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The security review of item 763: a live order whose prices were being read when the owner switched to paper was
    judged against the paper account's day and placed on Coinbase past the live day's cap."""
    from dotagent.trading.types import Fill, ProductRules, Quote

    settings: TradingSettings = TradingSettings(_config(mode="live", max_order=10.0, max_day=30.0))

    def switching(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker"):
            settings.value = settings.value.model_copy(update={"mode": "paper"})
        return _market_answer(request)

    class _Live:
        placed: int = 0

        async def balances(self) -> dict[str, float]:
            return {"EUR": 500.0}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            _Live.placed += 1
            return Fill(order_id="cb", product=request.product, side="buy", size=0.0002, price=quote.ask, fee=0.0,
                        ts=clock())  # fmt: skip

    trading: Trading = Trading(
        store=store, settings=settings, market=CoinbaseMarket(5.0, httpx.MockTransport(switching)), zone=_ZONE,
        failsafe_file=tmp_path / "STOP", live=_Live, clock=clock,
    )  # fmt: skip
    for number in range(3):
        await store.add_trading_order(
            TradingOrderRecord(
                id=f"ord_{number}", mode="live", product="BTC-EUR", side="buy", notional=10.0, size=0.0002,
                price=50000.0, fee=0.0, status="filled", ts=clock.now - 60, run_id="r", exchange_id="cb", note="",
            )
        )  # fmt: skip
    with pytest.raises(TradingRefused, match="still add up to"):
        await trading.place(
            OrderRequest(product="BTC-EUR", side="buy", quote_amount=10.0, base_amount=None), "run_a", "live"
        )
    assert _Live.placed == 0


async def test_every_currency_held_is_priced_and_a_days_start_waits_for_a_whole_look(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 764: the account was worth only its allowed products, so a product allowed while it
    was already held read as a gain of the day before, which loosened the day's loss limit by its worth. Every holding
    is priced now (item 765); one Coinbase has no price of is said and counted as nothing, and no day starts from a
    worth with a hole in it."""
    from dotagent.trading.types import Fill, ProductRules, Quote

    class _Held:
        async def balances(self) -> dict[str, float]:
            return {"EUR": 100.0, "BTC": 0.001, "DOGE": 5.0, "DOWN": 1.0}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            return Fill(
                order_id="cb", product=request.product, side=request.side, size=0.005, price=quote.ask, fee=0.0,
                ts=clock(),
            )  # fmt: skip

    settings: TradingSettings = TradingSettings(_config(mode="live", products=["ETH-EUR"]))
    trading: Trading = _trading(store, settings, clock, tmp_path)
    trading.live = _Held
    # ETH, the allowed product, has a price; BTC has one though it is not allowed; DOGE has none in the stand-in.
    _PRICES["ETH-EUR"] = ("2000.00", "2001.00", "2000.50")
    try:
        await _priced_holdings(store, trading)
    finally:
        _PRICES.pop("ETH-EUR", None)
        _PRICES.pop("DOWN-EUR", None)


async def _priced_holdings(store: Store, trading: Trading) -> None:
    seen: Portfolio = await trading.portfolio()
    # BTC is priced though it is not allowed; DOGE has no EUR product in the stand-in, which is no hole in the worth;
    # DOWN's price could not be read this time, which is one.
    assert seen.equity == pytest.approx(100.0 + 0.001 * 50000.0)
    assert (seen.unpriceable, seen.unpriced) == (("DOGE",), ("DOWN",))
    assert await store.kv_get(f"trading_day:live:{trading.day_start()[0]}") is None
    # Until a whole look, today's loss cannot be counted: no buy is placed (the pre-push review: a start taken from
    # each look read every loss as none), a sell still is.
    assert seen.day_start_known is False
    with pytest.raises(TradingRefused, match="no buy until it can"):
        await trading.place(
            OrderRequest(product="ETH-EUR", side="buy", quote_amount=10.0, base_amount=None), "run_a", "live"
        )
    _PRICES["DOWN-EUR"] = ("1.00", "1.10", "1.00")
    whole: Portfolio = await trading.portfolio()
    # DOGE is still worth nothing, every look alike: that is no hole, and the day starts.
    assert (whole.unpriceable, whole.unpriced) == (("DOGE",), ()) and whole.day_start_known
    assert whole.equity == pytest.approx(150.0 + 1.0)
    assert await store.kv_get(f"trading_day:live:{trading.day_start()[0]}") == repr(whole.equity)
    await trading.place(
        OrderRequest(product="ETH-EUR", side="buy", quote_amount=10.0, base_amount=None, reason="x" * 20),
        "run_a",
        "live",
    )


async def test_a_404_of_a_product_coinbase_trades_is_a_price_not_read(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 765: a 404 at the day's first look took a holding for one Coinbase has no product
    of, the day started without its worth, and that worth then read as the day's gain. A 404 is checked against
    Coinbase's list of what it trades; without the list, no 404 is taken at its word."""
    from dotagent.trading.types import Fill, Quote

    class _Held:
        async def balances(self) -> dict[str, float]:
            return {"EUR": 100.0, "BTC": 0.001}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            raise AssertionError("no order is placed here")

    answers: dict[str, int] = {"ticker": 404, "list": 503}

    def blinking(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/BTC-EUR/ticker") and answers["ticker"] != 200:
            return httpx.Response(answers["ticker"], content=b'{"error": "NOT_FOUND"}')
        if request.url.path.endswith("/market/products") and answers["list"] != 200:
            return httpx.Response(answers["list"], content=b"unavailable")
        return _market_answer(request)

    trading: Trading = Trading(
        store=store, settings=TradingSettings(_config(mode="live")),
        market=CoinbaseMarket(5.0, httpx.MockTransport(blinking)), zone=_ZONE, failsafe_file=tmp_path / "STOP",
        clock=clock,
    )  # fmt: skip
    trading.live = _Held
    day: str = f"trading_day:live:{trading.day_start()[0]}"
    # The list cannot be read: BTC's 404 is not taken at its word, and the day waits for a whole look.
    unlisted: Portfolio = await trading.portfolio()
    assert (unlisted.unpriceable, unlisted.unpriced, unlisted.day_start_known) == ((), ("BTC",), False)
    # BTC-EUR is on Coinbase's list: its 404 is a price not read this time.
    answers["list"] = 200
    listed: Portfolio = await trading.portfolio()
    assert (listed.unpriceable, listed.unpriced, listed.day_start_known) == ((), ("BTC",), False)
    assert await store.kv_get(day) is None
    # Read again, BTC is priced and the day starts from the whole worth.
    answers["ticker"] = 200
    whole: Portfolio = await trading.portfolio()
    assert (whole.unpriceable, whole.unpriced, whole.day_start_known) == ((), (), True)
    assert await store.kv_get(day) == repr(100.0 + 0.001 * 50000.0)


async def test_candles_asked_for_with_no_product_get_the_overview_and_what_the_candles_need(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The first paper shift asked for candles with a count and no product four times, getting the overview each time
    (item 765): the overview now says what the candles need."""
    from types import SimpleNamespace
    from typing import cast

    from dotagent.tools.base import ToolResult
    from dotagent.tools.context import ToolContext
    from dotagent.tools.trading import build_trading_tools

    trading: Trading = _trading(store, TradingSettings(_config(products=["BTC-EUR"])), clock, tmp_path)
    ctx: ToolContext = cast(ToolContext, SimpleNamespace(runtime=SimpleNamespace(trading=trading), run_id="run_a"))
    market = {tool.spec.name: tool for tool in build_trading_tools()}["trading_market"]
    shown: ToolResult = await (await market.prepare({"count": 30}, ctx)).run(ctx)
    assert "BTC-EUR: bid 50000" in shown.text and "give product and granularity" in shown.text
    with_product: ToolResult = await (await market.prepare({"product": "BTC-EUR", "count": 30}, ctx)).run(ctx)
    assert "give product and granularity" in with_product.text
    plain: ToolResult = await (await market.prepare({}, ctx)).run(ctx)
    assert "give product and granularity" not in plain.text and "last 24 h" in plain.text


async def test_currencies_past_the_priced_count_are_said_unpriced_and_the_quote_is_never_one(
    store: Store, clock: _Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-push review of item 765: the currencies past the count were cut from the wrong list, so one of them was
    priced at nothing without being said, and the quote currency could be reported unpriced."""
    from dotagent.trading import service
    from dotagent.trading.types import Fill, ProductRules, Quote

    monkeypatch.setattr(service, "_PRICED_AT_MOST", 1)
    _PRICES["ETH-EUR"] = ("2000.00", "2001.00", "2000.50")

    class _Many:
        async def balances(self) -> dict[str, float]:
            return {"BTC": 0.001, "ETH": 1.0, "EUR": 100.0, "SOL": 2.0}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            raise AssertionError("not reached")

    trading: Trading = _trading(store, TradingSettings(_config(mode="live")), clock, tmp_path)
    trading.live = _Many
    try:
        seen: Portfolio = await trading.portfolio()
    finally:
        del _PRICES["ETH-EUR"]
    assert seen.unpriced == ("ETH", "SOL") and seen.unpriceable == ()
    assert seen.equity == pytest.approx(100.0 + 0.001 * 50000.0)


# The longest reason the order tool takes: 300 characters.
_LONG_REASON: str = (
    "Hourly close over the SMA20, RSI 55, the day's trend up; a small buy, sold under 49,000 or at 52,000. " * 3
)[:300]
assert len(_LONG_REASON) == 300


async def test_a_refused_or_unknown_order_keeps_the_runs_reason_with_what_happened(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 765: what went wrong replaced the reason in the order's note, on the orders the
    owner looks at most."""
    from dotagent.trading.types import Fill, ProductRules, Quote

    class _Refusing:
        async def balances(self) -> dict[str, float]:
            return {"EUR": 500.0}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            raise TradingRefused("Coinbase did not take the order: Insufficient balance", {})

    trading: Trading = _trading(store, TradingSettings(_config(mode="live")), clock, tmp_path)
    trading.live = _Refusing
    buy: OrderRequest = OrderRequest(
        product="BTC-EUR", side="buy", quote_amount=20.0, base_amount=None, reason=_LONG_REASON
    )
    with pytest.raises(TradingRefused):
        await trading.place(buy, "run_a", "live")
    (kept,) = await store.trading_orders("live", 0.0, 10)
    assert (
        kept.status == "failed"
        and kept.note == f"{_LONG_REASON} — Coinbase did not take the order: Insufficient balance"
    )


async def test_the_overview_stands_when_coinbases_sum_of_the_day_cannot_be_read(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 765: the last day's change failing to read failed the whole overview."""
    from types import SimpleNamespace
    from typing import cast

    from dotagent.tools.base import ToolResult
    from dotagent.tools.context import ToolContext
    from dotagent.tools.trading import build_trading_tools

    def no_day(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker"):
            return _market_answer(request)
        return httpx.Response(503, content=b"unavailable")

    trading: Trading = Trading(
        store=store, settings=TradingSettings(_config(products=["BTC-EUR"])),
        market=CoinbaseMarket(5.0, httpx.MockTransport(no_day)), zone=_ZONE, failsafe_file=tmp_path / "STOP",
        clock=clock,
    )  # fmt: skip
    ctx: ToolContext = cast(ToolContext, SimpleNamespace(runtime=SimpleNamespace(trading=trading), run_id="run_a"))
    market = {tool.spec.name: tool for tool in build_trading_tools()}["trading_market"]
    shown: ToolResult = await (await market.prepare({}, ctx)).run(ctx)
    assert not shown.is_error and "BTC-EUR: bid 50000" in shown.text and "no change given" in shown.text


# ----- exits (item 770) ------------------------------------------------------------------------------------------


def _exit_buy(quote_amount: float = 20.0, stop: float = 49000.0, target: float | None = 52000.0) -> OrderRequest:
    return OrderRequest(
        product="BTC-EUR", side="buy", quote_amount=quote_amount, base_amount=None, reason="x" * 20, stop=stop,
        target=target,
    )  # fmt: skip


async def test_a_buy_with_a_stop_is_watched_and_sold_at_its_stop_past_every_limit(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """Item 770: Dot's own reply to the owner named it: no stop existed, so a stop waited for the next hourly run. A
    buy now carries one, kept as an exit, and the daemon's look sells at it, past one order's cap and the day's: a
    stop a limit could hold would protect nothing."""
    trading: Trading = _trading(store, TradingSettings(_config(max_order=25.0, max_day=25.0)), clock, tmp_path)
    with pytest.raises(TradingRefused, match="the stop, 51000, must be under the bid"):
        await trading.place(_exit_buy(stop=51000.0), "run_a", "paper")
    with pytest.raises(TradingRefused, match="must be over the ask"):
        await trading.place(_exit_buy(target=50000.0), "run_a", "paper")
    bought: TradingOrderRecord = await trading.place(_exit_buy(quote_amount=25.0), "run_a", "paper")
    (watched,) = await trading.open_exits()
    assert (watched.size, watched.stop, watched.target, watched.order_id) == (bought.size, 49000.0, 52000.0, bought.id)
    # Nothing reached: nothing sold.
    assert await trading.check_exits() == []
    # The price rises: what the buy holds is now worth more than one order may be, and the day's cap is used up.
    _PRICES["BTC-EUR"] = ("51990.00", "52000.00", "51995.00")
    try:
        assert await trading.check_exits() == []
        _PRICES["BTC-EUR"] = ("48900.00", "48910.00", "48905.00")
        (end,) = await trading.check_exits()
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    assert end.exit.status == "stopped" and end.order is not None and end.order.side == "sell"
    assert end.order.size == bought.size and end.order.price == 48900.0
    assert end.order.note.startswith(f"Exit {watched.id}: the bid 48900 reached its stop 49000")
    assert await trading.open_exits() == []
    assert [h.currency for h in (await trading.portfolio()).holdings] == ["EUR"]


async def test_a_target_takes_the_gain_and_a_sell_by_hand_takes_from_the_exits(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    trading: Trading = _trading(store, TradingSettings(_config()), clock, tmp_path)
    first: TradingOrderRecord = await trading.place(_exit_buy(), "run_a", "paper")
    second: TradingOrderRecord = await trading.place(_exit_buy(), "run_a", "paper")
    # A sell by hand of half the first takes from the oldest exit; selling the rest of it closes it.
    await trading.place(
        OrderRequest(product="BTC-EUR", side="sell", quote_amount=None, base_amount=first.size / 2, reason="x" * 20),
        "run_a",
        "paper",
    )
    older, newer = await trading.open_exits()
    assert older.size == pytest.approx(first.size / 2) and newer.size == pytest.approx(second.size)
    await trading.place(
        OrderRequest(product="BTC-EUR", side="sell", quote_amount=None, base_amount=first.size / 2, reason="x" * 20),
        "run_a",
        "paper",
    )
    (left,) = await trading.open_exits()
    assert left.order_id == second.id
    _PRICES["BTC-EUR"] = ("52100.00", "52110.00", "52105.00")
    try:
        (end,) = await trading.check_exits()
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    assert end.exit.status == "targeted" and end.order is not None and end.order.price == 52100.0


async def test_an_exit_is_never_sold_on_another_account_nor_while_the_stop_is_engaged(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """While the other account is in use, an exit waits, open, and sells once its own is in use again (the pre-push
    review of item 770: marked stale, a live stop died for good when the owner switched to paper and back)."""
    settings: TradingSettings = TradingSettings(_config())
    trading: Trading = _trading(store, settings, clock, tmp_path)
    await trading.place(_exit_buy(), "run_a", "paper")
    _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
    try:
        # The emergency stop: nothing is sold, and the exit stays watched for when it is released.
        (tmp_path / "STOP").write_text("")
        assert await trading.check_exits() == []
        (still,) = await trading.open_exits()
        assert still.status == "open"
        (tmp_path / "STOP").unlink()
        # The account switched to live: the paper exit is not sold there; it waits.
        settings.value = settings.value.model_copy(update={"mode": "live"})
        assert await trading.check_exits() == []
        assert (await store.get_trading_exit(still.id)).status == "open"
        assert await store.trading_orders("live", 0.0, 10) == []
        # Back on paper, it sells.
        settings.value = settings.value.model_copy(update={"mode": "paper"})
        (end,) = await trading.check_exits()
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    assert end.exit.status == "stopped" and end.order is not None and end.order.mode == "paper"


async def test_an_exit_is_moved_within_the_prices_now(store: Store, clock: _Clock, tmp_path: Path) -> None:
    trading: Trading = _trading(store, TradingSettings(_config()), clock, tmp_path)
    await trading.place(_exit_buy(target=None), "run_a", "paper")
    (watched,) = await trading.open_exits()
    moved = await trading.move_exit(watched.id, 49500.0, 53000.0)
    assert (moved.stop, moved.target) == (49500.0, 53000.0)
    with pytest.raises(TradingRefused, match="must be under the bid"):
        await trading.move_exit(watched.id, 50500.0, None)
    (kept,) = await trading.open_exits()
    assert kept.stop == 49500.0


async def test_an_exit_whose_sell_never_went_out_stays_open_and_the_others_are_looked_at(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 770: an error before the sell went out (Coinbase's product out of reach) failed the
    exit for good, the position left without its stop, and a price that could not be read ended the look at the other
    exits. Only a sell kept as sent, perhaps, is not tried again; one held at every look for five minutes is told."""
    from dotagent.trading.market import MarketError
    from dotagent.trading.service import HELD_TOLD_S

    trading: Trading = _trading(store, TradingSettings(_config(max_day=500.0)), clock, tmp_path)
    _PRICES["ETH-EUR"] = ("2000.00", "2001.00", "2000.50")
    real_rules = trading.market.rules
    down: list[bool] = [True]

    async def rules(product: str) -> ProductRules:
        if product == "BTC-EUR" and down[0]:
            raise MarketError("Coinbase's market data could not be reached", {"error": "ConnectError"})
        return await real_rules(product)

    try:
        await trading.place(_exit_buy(), "run_a", "paper")
        await trading.place(
            OrderRequest(product="ETH-EUR", side="buy", quote_amount=20.0, base_amount=None, reason="x" * 20,
                         stop=1950.0),
            "run_a",
            "paper",
        )  # fmt: skip
        trading.market.rules = rules  # type: ignore[method-assign]
        _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
        _PRICES["ETH-EUR"] = ("1900.00", "1901.00", "1900.50")
        # BTC's sell cannot go out: its exit stays open, and ETH's is sold all the same.
        (eth,) = await trading.check_exits()
        assert (eth.exit.product, eth.exit.status) == ("ETH-EUR", "stopped")
        (btc,) = await trading.open_exits()
        assert btc.product == "BTC-EUR"
        # Held at every look for five minutes, it is told, once.
        clock.now += HELD_TOLD_S
        (held,) = await trading.check_exits()
        assert held.exit.id == btc.id and "could not go out" in held.held
        assert await trading.check_exits() == []
        down[0] = False
        (sold,) = await trading.check_exits()
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
        _PRICES.pop("ETH-EUR", None)
    assert sold.exit.status == "stopped" and sold.order is not None and sold.order.size == btc.size


async def test_a_live_buy_keeps_no_exit_and_says_its_stop_is_not_watched_yet(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """Item 770 keeps exits on the paper account only: what Dot sells by itself on the owner's Coinbase account is a
    later item's, reviewed on its own (the owner's choice after eight pre-push reviews). A live buy's question and its
    answer say so."""
    from types import SimpleNamespace
    from typing import cast

    from dotagent.tools.base import PreparedCall, ToolResult
    from dotagent.tools.context import ToolContext
    from dotagent.tools.trading import build_trading_tools
    from dotagent.trading.types import Fill, Quote

    class _Live:
        async def balances(self) -> dict[str, float]:
            return {"EUR": 100.0}

        async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
            return Fill(
                order_id="cb", product=request.product, side=request.side, size=0.0002, price=quote.ask, fee=0.0,
                ts=clock(),
            )  # fmt: skip

    trading: Trading = _trading(store, TradingSettings(_config(mode="live")), clock, tmp_path)
    trading.live = _Live
    ctx: ToolContext = cast(ToolContext, SimpleNamespace(runtime=SimpleNamespace(trading=trading), run_id="run_a"))
    order_tool = {tool.spec.name: tool for tool in build_trading_tools()}["trading_order"]
    raw: dict[str, object] = {
        "product": "BTC-EUR", "side": "buy", "quote_amount": 10.0, "stop_price": 49000.0,
        "reason": "The hour's candles closed above the SMA20; a small buy, wrong under 49,000.",
    }  # fmt: skip
    prepared: PreparedCall = await order_tool.prepare(raw, ctx)
    assert "Dot does not sell at it on your Coinbase account yet" in prepared.action.summary
    placed: ToolResult = await prepared.run(ctx)
    assert not placed.is_error and "Dot does not watch stops on the owner's Coinbase account yet" in placed.text
    assert await trading.open_exits() == [] and await store.trading_exits("open", 10) == []


async def test_an_exit_sells_what_it_holds_when_its_turn_comes_and_starting_paper_again_closes_them(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """Found while making item 772: an exit is read before its sell waits for the lock, and a sell by hand in the
    meantime took from it or closed it, so the exit sold what was no longer its own; and the paper account started
    again kept its exits open, to sell a later buy's holding at an old stop."""
    from dotagent.trading.types import Quote

    trading: Trading = _trading(store, TradingSettings(_config(max_day=500.0)), clock, tmp_path)
    bought: TradingOrderRecord = await trading.place(_exit_buy(), "run_a", "paper")
    (read,) = await trading.open_exits()
    half: OrderRequest = OrderRequest(
        product="BTC-EUR", side="sell", quote_amount=None, base_amount=bought.size / 2, reason="x" * 20
    )
    await trading.place(half, "run_a", "paper")
    fallen: Quote = Quote(product="BTC-EUR", bid=48900.0, ask=48910.0, last=48905.0, ts=clock())
    taken = await trading._close(read.id, fallen)
    assert taken is not None and taken.order is not None and taken.order.size == pytest.approx(bought.size / 2)
    assert taken.exit.status == "stopped" and taken.exit.size == pytest.approx(bought.size / 2)
    # Closed by a sell by hand before its turn came: nothing is sold, nothing is told, and it stays closed.
    again: TradingOrderRecord = await trading.place(_exit_buy(), "run_a", "paper")
    (fresh,) = await trading.open_exits()
    whole: OrderRequest = OrderRequest(
        product="BTC-EUR", side="sell", quote_amount=None, base_amount=again.size, reason="x" * 20
    )
    await trading.place(whole, "run_a", "paper")
    placed: int = len(await store.trading_orders("paper", 0.0, 50))
    assert await trading._close(fresh.id, fallen) is None
    assert len(await store.trading_orders("paper", 0.0, 50)) == placed
    assert (await store.get_trading_exit(fresh.id)).status == "closed"
    # Starting the paper account again closes its exits: a later buy's holding is not theirs to sell.
    await trading.place(_exit_buy(stop=49500.0), "run_a", "paper")
    await trading.reset_paper()
    assert await trading.open_exits() == []
    clock.now += 1.0
    await trading.place(_exit_buy(stop=48000.0), "run_a", "paper")
    _PRICES["BTC-EUR"] = ("49000.00", "49010.00", "49005.00")
    try:
        assert await trading.check_exits() == []
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    (kept,) = await trading.open_exits()
    assert kept.stop == 48000.0


async def test_a_move_and_a_sell_of_one_exit_wait_for_each_other_and_a_sold_exit_is_never_reopened(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 770: a move read the exit, waited for the prices, and wrote it back over the sell
    the watch made meanwhile, the exit open again to sell a later buy's holding. Every change of an exit is made under
    the order lock, from the exit as it stands then."""
    import asyncio

    from dotagent.trading.types import Quote

    trading: Trading = _trading(store, TradingSettings(_config()), clock, tmp_path)
    await trading.place(_exit_buy(target=None), "run_a", "paper")
    (watched,) = await trading.open_exits()
    real_quote = trading.market.quote
    gate: asyncio.Event = asyncio.Event()
    # Set once the quote that waits for the gate is read, by whoever holds the lock then.
    entered: asyncio.Event = asyncio.Event()
    calls: list[int] = [0, 1]  # the quotes read so far, and which of them waits for the gate

    async def slow_quote(product: str) -> Quote:
        calls[0] += 1
        if calls[0] == calls[1]:
            entered.set()
            await gate.wait()
        return await real_quote(product)

    trading.market.quote = slow_quote  # type: ignore[method-assign]
    _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
    try:
        # The move reads the prices first; the watch's sell waits for it, then finds the stop moved under the bid.
        moving = asyncio.create_task(trading.move_exit(watched.id, 47000.0, None))
        await entered.wait()
        looking = asyncio.create_task(trading.check_exits())
        await asyncio.sleep(0.01)
        gate.set()
        assert (await moving).stop == 47000.0
        assert await looking == []
        assert len(await store.trading_orders("paper", 0.0, 10)) == 1
        # The sell first, its prices read inside the lock: the move waits for it, then finds the exit sold.
        _PRICES["BTC-EUR"] = ("46000.00", "46010.00", "46005.00")
        gate.clear()
        entered.clear()
        calls[:] = [0, 2]
        looking = asyncio.create_task(trading.check_exits())
        # The sell holds the lock, waiting for its prices, before the move is asked for.
        await entered.wait()
        moving = asyncio.create_task(trading.move_exit(watched.id, 45000.0, None))
        await asyncio.sleep(0.01)
        gate.set()
        (end,) = await looking
        with pytest.raises(TradingRefused, match="is not one watched now"):
            await moving
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    assert end.exit.status == "stopped" and end.exit.stop == 47000.0
    assert (await store.get_trading_exit(watched.id)).status == "stopped"


async def test_an_exit_whose_sell_is_held_by_what_holds_every_order_stays_open_and_sells_after(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 770: the emergency stop engaged, trading switched off or the account switched while
    the watch read the prices failed the exit for good, never watched again once the owner released the stop. Those,
    and Coinbase not taking orders of the product, hold the sell: the exit stays open and sells at a later look."""
    from dotagent.trading.types import Quote

    settings: TradingSettings = TradingSettings(_config())
    trading: Trading = _trading(store, settings, clock, tmp_path)
    await trading.place(_exit_buy(), "run_a", "paper")
    (watched,) = await trading.open_exits()
    real_quote = trading.market.quote
    real_rules = trading.market.rules
    meanwhile: list[object] = []

    async def quote_then(product: str) -> Quote:
        # What the owner does while the watch reads the prices, before the sell's turn.
        for act in meanwhile:
            if callable(act):
                act()
        meanwhile.clear()
        return await real_quote(product)

    trading.market.quote = quote_then  # type: ignore[method-assign]
    _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
    try:
        for held_by in (
            lambda: (tmp_path / "STOP").write_text(""),
            lambda: setattr(settings, "value", settings.value.model_copy(update={"mode": "off"})),
            # The account switched: held now, and waiting while the other account is in use (another test).
            lambda: setattr(settings, "value", settings.value.model_copy(update={"mode": "live"})),
        ):
            meanwhile.append(held_by)
            assert await trading.check_exits() == []
            assert (await store.get_trading_exit(watched.id)).status == "open"
            (tmp_path / "STOP").unlink(missing_ok=True)
            settings.value = settings.value.model_copy(update={"mode": "paper"})
        # Coinbase not taking orders of the product now: held too.
        halted: ProductRules = (await real_rules("BTC-EUR")).model_copy(update={"trading": False})

        async def halted_rules(product: str) -> ProductRules:
            return halted

        trading.market.rules = halted_rules  # type: ignore[method-assign]
        assert await trading.check_exits() == []
        assert (await store.get_trading_exit(watched.id)).status == "open"
        trading.market.rules = real_rules  # type: ignore[method-assign]
        # Nothing holds it any more: it sells.
        (end,) = await trading.check_exits()
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    assert end.exit.status == "stopped" and end.order is not None and end.order.side == "sell"


async def test_the_watch_tells_a_held_exit_once_it_has_lasted_five_minutes(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """The pre-push review of item 770: a sell that could not go out was tried again in silence, a stop passed and
    nothing sold for hours with no word to the owner. Held at every look for five minutes, it is told once."""
    from types import SimpleNamespace
    from typing import cast

    from dotagent.runtime import Runtime
    from dotagent.trading.service import HELD_TOLD_S
    from dotagent.trading.watch import TradingWatch

    trading: Trading = _trading(store, TradingSettings(_config()), clock, tmp_path)
    await trading.place(_exit_buy(), "run_a", "paper")
    told: list[dict[str, object]] = []

    class _Bus:
        async def emit(self, run_id: str, kind: str, data: dict[str, object], persist: bool) -> None:
            told.append({"kind": kind, **data})

    real_rules = trading.market.rules
    halted: ProductRules = (await real_rules("BTC-EUR")).model_copy(update={"trading": False})

    async def halted_rules(product: str) -> ProductRules:
        return halted

    trading.market.rules = halted_rules  # type: ignore[method-assign]
    watch: TradingWatch = TradingWatch(cast(Runtime, SimpleNamespace(trading=trading, bus=_Bus())))
    _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
    try:
        for second in range(0, int(HELD_TOLD_S) + 120, 15):
            clock.now += 15.0
            await watch.tick(1000.0 + second)
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    (message,) = told
    assert message["title"] == "Exit of BTC-EUR cannot sell yet"
    assert "Coinbase does not take orders of BTC-EUR now" in str(message["text"])
    # Released, then held again for as long: told again.
    trading.market.rules = real_rules  # type: ignore[method-assign]
    _PRICES["BTC-EUR"] = ("49500.00", "49510.00", "49505.00")
    try:
        clock.now += 15.0
        await watch.tick(5000.0)
        trading.market.rules = halted_rules  # type: ignore[method-assign]
        _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
        for second in range(0, int(HELD_TOLD_S) + 120, 15):
            clock.now += 15.0
            await watch.tick(6000.0 + second)
        assert len(told) == 2
        # The emergency stop engaged and released: held anew, and told anew once that lasts.
        (tmp_path / "STOP").write_text("")
        clock.now += 15.0
        await watch.tick(7000.0)
        (tmp_path / "STOP").unlink()
        for second in range(0, int(HELD_TOLD_S) + 120, 15):
            clock.now += 15.0
            await watch.tick(8000.0 + second)
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    assert len(told) == 3


async def test_the_watch_tells_the_owner_what_an_exit_came_to(store: Store, clock: _Clock, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from typing import cast

    from dotagent.runtime import Runtime
    from dotagent.trading.watch import TradingWatch

    trading: Trading = _trading(store, TradingSettings(_config()), clock, tmp_path)
    await trading.place(_exit_buy(), "run_a", "paper")
    told: list[dict[str, object]] = []

    class _Bus:
        async def emit(self, run_id: str, kind: str, data: dict[str, object], persist: bool) -> None:
            told.append({"kind": kind, **data})

    watch: TradingWatch = TradingWatch(cast(Runtime, SimpleNamespace(trading=trading, bus=_Bus())))
    _PRICES["BTC-EUR"] = ("48000.00", "48010.00", "48005.00")
    try:
        await watch.tick(1000.0)
        # The next tick comes before its time: nothing is looked at.
        await watch.tick(1001.0)
    finally:
        _PRICES["BTC-EUR"] = ("50000.00", "50010.00", "50005.00")
    (message,) = told
    assert message["title"] == "Stop reached: BTC-EUR sold" and "on paper" in str(message["text"])


# ----- the whole market (item 771) -------------------------------------------------------------------------------

_VOLUMES: dict[str, str] = {"BTC-EUR": "12000000", "ETH-EUR": "5000000", "XRP-EUR": "800000"}


def _whole_market(request: httpx.Request) -> httpx.Response:
    """A stand-in of Coinbase's product list and products, each with its day's volume."""
    path: str = request.url.path
    if path.endswith("/market/products"):
        listed: list[dict[str, object]] = [
            {
                "product_id": product, "quote_currency_id": "EUR", "status": "online", "trading_disabled": False,
                "is_disabled": False, "cancel_only": False, "limit_only": False, "price": price,
                "best_bid_price": price, "best_ask_price": price, "price_percentage_change_24h": change,
                "high_24h": high, "low_24h": low, "approximate_quote_24h_volume": _VOLUMES[product],
            }
            for product, price, change, high, low in (
                ("BTC-EUR", "50000", "1.5", "50500", "48000"),
                ("ETH-EUR", "2000", "-4.0", "2150", "1990"),
                ("XRP-EUR", "1.2", "9.0", "1.21", "1.0"),
            )
        ]  # fmt: skip
        listed.append({"product_id": "BTC-USDC", "quote_currency_id": "USDC", "status": "online", "price": "1"})
        return httpx.Response(200, content=json.dumps({"products": listed}).encode())
    product: str = path.split("/products/")[1].split("/")[0]
    answer: httpx.Response = _market_answer(request)
    if path.endswith("/ticker") or answer.status_code != 200:
        return answer
    body: dict[str, object] = json.loads(answer.content)
    return httpx.Response(200, content=json.dumps({**body, "approximate_quote_24h_volume": _VOLUMES[product]}).encode())


async def test_with_any_product_an_order_is_of_a_product_that_trades_enough_a_day(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    """Item 771: the owner asked why Dot does not look across the market. "*" among the products allows every product
    of the quote that trades at least min_volume of it a day: a market thinner than that is one a small order moves."""
    from dotagent.trading.limits import not_allowed

    config: TradingConfig = _config(products=["*"], min_volume=1_000_000.0)
    _PRICES["ETH-EUR"] = ("2000.00", "2001.00", "2000.50")
    _PRICES["XRP-EUR"] = ("1.20", "1.21", "1.20")
    trading: Trading = Trading(
        store=store, settings=TradingSettings(config), market=CoinbaseMarket(5.0, httpx.MockTransport(_whole_market)),
        zone=_ZONE, failsafe_file=tmp_path / "STOP", clock=clock,
    )  # fmt: skip
    try:
        bought: TradingOrderRecord = await trading.place(
            OrderRequest(product="ETH-EUR", side="buy", quote_amount=10.0, base_amount=None, reason="x" * 20,
                         stop=1900.0),
            "run_a",
            "paper",
        )  # fmt: skip
        assert bought.status == "filled"
        with pytest.raises(TradingRefused, match=r"under the 1000000\.00 EUR the owner allows"):
            await trading.place(
                OrderRequest(product="XRP-EUR", side="buy", quote_amount=10.0, base_amount=None, reason="x" * 20,
                             stop=1.1),
                "run_a",
                "paper",
            )  # fmt: skip
    finally:
        del _PRICES["ETH-EUR"], _PRICES["XRP-EUR"]
    assert not_allowed(config, "BTC-USDC", None) == "BTC-USDC is not priced in EUR, the currency of the owner's limits"
    # A product whose day Coinbase did not sum is not taken to trade enough.
    assert "traded an unknown amount" in str(not_allowed(config, "ADA-EUR", None))
    named: TradingConfig = _config(products=["BTC-EUR"])
    assert not_allowed(named, "ETH-EUR", None) is not None and not_allowed(named, "BTC-EUR", None) is None


async def test_the_scan_ranks_what_the_owner_allows_of_the_whole_market(
    store: Store, clock: _Clock, tmp_path: Path
) -> None:
    from types import SimpleNamespace
    from typing import cast

    from dotagent.tools.base import ToolResult
    from dotagent.tools.context import ToolContext
    from dotagent.tools.trading import build_trading_tools

    trading: Trading = Trading(
        store=store, settings=TradingSettings(_config(products=["*"], min_volume=1_000_000.0)),
        market=CoinbaseMarket(5.0, httpx.MockTransport(_whole_market)), zone=_ZONE, failsafe_file=tmp_path / "STOP",
        clock=clock,
    )  # fmt: skip
    ctx: ToolContext = cast(ToolContext, SimpleNamespace(runtime=SimpleNamespace(trading=trading), run_id="run_a"))
    scan = {tool.spec.name: tool for tool in build_trading_tools()}["trading_scan"]
    movers: ToolResult = await (await scan.prepare({"sort": "movers"}, ctx)).run(ctx)
    rows: list[str] = movers.text.splitlines()[2:]
    # XRP moved most but trades under the floor: not listed; ETH's fall comes before BTC's rise.
    assert movers.text.startswith("2 products you may trade") and [row.split(" |")[0] for row in rows] == [
        "ETH-EUR",
        "BTC-EUR",
    ]
    lows: ToolResult = await (await scan.prepare({"sort": "lows"}, ctx)).run(ctx)
    assert lows.text.splitlines()[2].startswith("ETH-EUR") and "6 %" in lows.text.splitlines()[2]
    for sort, first in (("volume", "BTC-EUR"), ("highs", "BTC-EUR")):
        ranked: ToolResult = await (await scan.prepare({"sort": sort}, ctx)).run(ctx)
        assert ranked.text.splitlines()[2].startswith(first), sort
    # The overview of "*" alone is the busiest products past the floor, not XRP under it.
    market = {tool.spec.name: tool for tool in build_trading_tools()}["trading_market"]
    _PRICES["ETH-EUR"] = ("2000.00", "2001.00", "2000.50")
    try:
        overview: ToolResult = await (await market.prepare({}, ctx)).run(ctx)
    finally:
        del _PRICES["ETH-EUR"]
    assert "BTC-EUR" in overview.text and "ETH-EUR" in overview.text and "XRP-EUR" not in overview.text
