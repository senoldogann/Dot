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
    # A sell must say how much; a buy says what to spend.
    with pytest.raises(ToolInputError):
        await order_tool.prepare({**raw, "quote_amount": None, "base_amount": 0.1}, ctx)


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
