"""The trend engine (item 818, docs/trading.md): its rule over daily closes, the steps that bring the account to the
rule's shares, the rule played over years against holding, the engine placing its steps through the paper account's
limits, pausing, and keeping off any account but paper; the backtest's reading of Coinbase's days. Coinbase is a
stand-in (``httpx.MockTransport``): no request leaves the Mac."""

import json
import math
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio

from dotagent.config import TradingConfig, load_default_config
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.engine import ENGINE_RUN_ID, TrendEngine, closed_days
from dotagent.trading.engine_state import EngineState, pause, read_pause, read_state, resume
from dotagent.trading.market import CoinbaseMarket
from dotagent.trading.service import Trading, TradingSettings
from dotagent.trading.trend import DAYS_NEEDED, backtest, steps_toward, strength, target_weights, volatility
from dotagent.trading.types import Candle

_DAY: float = 86_400.0
_NOW: float = 1_793_000_000.0 - (1_793_000_000.0 % _DAY) + 3600.0  # an hour into a day


def _rising(days: int, start: float = 100.0, step: float = 0.01) -> list[float]:
    """A product that rises ``step`` a day with a small wobble, so that its volatility is not 0."""
    return [start * (1 + step) ** day * (1 + 0.02 * math.sin(day)) for day in range(days)]


def _falling(days: int) -> list[float]:
    return [100.0 * 0.99**day * (1 + 0.02 * math.sin(day)) for day in range(days)]


# ------------------------------------------------------------------------------------------------- the rule


def test_the_strength_is_the_share_of_the_lookbacks_the_last_close_is_over() -> None:
    assert strength(_rising(DAYS_NEEDED - 1)) is None
    assert strength(_rising(DAYS_NEEDED)) == 1.0
    assert strength(_falling(DAYS_NEEDED)) == 0.0
    # Over the closes 20 and 50 days before, under those 100 and 200 days before: a fall that is ending.
    closes: list[float] = [200.0] * 101 + [100.0] * 80 + [110.0] * 20
    closes[-21] = 100.0
    closes[-51] = 100.0
    assert len(closes) == DAYS_NEEDED and strength(closes) == 0.5


def test_a_share_is_its_strength_sized_to_the_volatility_over_the_products_judged() -> None:
    calm: list[float] = _rising(DAYS_NEEDED, step=0.01)
    vol: float | None = volatility(calm)
    assert vol is not None and 0 < vol < 0.5
    weights: dict[str, float] = target_weights({"A": calm, "B": _falling(DAYS_NEEDED), "C": [1.0] * 10}, 0.5)
    # Two judged (C has too few closes and takes no share): A all of its half, B nothing, C nothing.
    assert weights == {"A": 0.5, "B": 0.0, "C": 0.0}
    # A product wilder than the target is held at less than its share.
    wild: dict[str, float] = target_weights({"A": calm}, vol / 2)
    assert wild["A"] == pytest.approx(0.5, rel=1e-9)


def test_the_steps_sell_first_leave_what_is_within_the_band_and_no_order_is_over_the_cap() -> None:
    steps = steps_toward(
        targets={"BTC-EUR": 0.5, "ETH-EUR": 0.0, "SOL-EUR": 0.32, "ADA-EUR": 0.1},
        values={"ETH-EUR": 100.0, "SOL-EUR": 300.0, "ADA-EUR": 400.0},
        sizes={"ETH-EUR": 0.05, "SOL-EUR": 2.0, "ADA-EUR": 1000.0},
        equity=1000.0,
        band=0.05,
        max_order=25.0,
    )
    assert [(step.side, step.product) for step in steps] == [
        ("sell", "ETH-EUR"),  # a target of 0 sells, whatever the band
        ("sell", "ADA-EUR"),  # 40% for a target of 10%
        ("buy", "BTC-EUR"),  # SOL at 30% for 32% stays within the band
    ]
    eth, ada, btc = steps
    # Capped sells name what they bring, which a bid a little higher at the order cannot take over the cap.
    assert (eth.quote_amount, eth.base_amount) == (25.0, None)
    assert (ada.quote_amount, ada.base_amount) == (25.0, None)
    assert btc.quote_amount == 25.0
    # What is held whole well under the cap is sold by its size; a little under it, by most of its worth.
    (whole,) = steps_toward({"ETH-EUR": 0.0}, {"ETH-EUR": 20.0}, {"ETH-EUR": 0.01}, 1000.0, 0.05, 25.0)
    assert (whole.base_amount, whole.quote_amount) == (0.01, None)
    (near,) = steps_toward({"ETH-EUR": 0.0}, {"ETH-EUR": 24.9}, {"ETH-EUR": 0.01}, 1000.0, 0.05, 25.0)
    assert near.base_amount is None and near.quote_amount == pytest.approx(24.9 * 0.99)
    assert steps_toward({"BTC-EUR": 1.0}, {}, {}, 0.0, 0.05, 25.0) == []


def test_the_rule_played_over_years_counts_its_fees_and_falls_less_than_holding() -> None:
    days: int = 900
    stamps: list[float] = [1_600_000_000.0 + day * _DAY for day in range(days)]
    # Up for a year and a half, then down by two thirds: holding keeps the fall, the rule steps out of it.
    path: list[float] = [100.0 * 1.004**day if day < 550 else 100.0 * 1.004**550 * 0.997 ** (day - 550)
                         for day in range(days)]  # fmt: skip
    wobbly: list[float] = [price * (1 + 0.01 * math.sin(day)) for day, price in enumerate(path)]
    closes: dict[str, list[tuple[float, float]]] = {"BTC-EUR": list(zip(stamps, wobbly, strict=True))}
    cheap = backtest(closes, 0.0, 0.5, 0.1)
    dear = backtest(closes, 0.01, 0.5, 0.1)
    assert cheap.rule_max_drawdown > cheap.benchmark_max_drawdown + 0.2
    assert dear.rule_cagr < cheap.rule_cagr
    assert cheap.turnover_per_year > 0
    assert [one.year for one in cheap.years] == sorted({one.year for one in cheap.years})
    assert cheap.start == stamps[DAYS_NEEDED - 1]
    with pytest.raises(ValueError, match="more than"):
        backtest({"BTC-EUR": closes["BTC-EUR"][:DAYS_NEEDED]}, 0.0, 0.5, 0.1)


def test_only_the_days_that_have_ended_are_closes() -> None:
    def candle(start: float) -> Candle:
        return Candle(start=start, low=1.0, high=1.0, open=1.0, close=start, volume=0.0)

    today: float = _NOW - (_NOW % _DAY)
    assert closed_days([candle(today - _DAY), candle(today)], _NOW) == [today - _DAY]


# ------------------------------------------------------------------------------------------------- the engine

_PRICES: dict[str, list[float]] = {}
# The products whose price Coinbase does not give now (its candles still come).
_DOWN: set[str] = set()


def _row(start: float, close: float) -> dict[str, str]:
    return {"start": str(int(start)), "low": str(close), "high": str(close), "open": str(close), "close": str(close),
            "volume": "1"}  # fmt: skip


def _answer(request: httpx.Request) -> httpx.Response:
    path: str = request.url.path
    product: str = path.split("/products/")[1].split("/")[0]
    series: list[float] = _PRICES[product]
    last: float = series[-1]
    if path.endswith("/candles"):
        count: int = int(request.url.params["limit"])
        today: float = _NOW - (_NOW % _DAY)
        # The days before today, and today's period, still trading.
        days: list[dict[str, str]] = [_row(today - (len(series) - index) * _DAY, close) for index, close in
                                      enumerate(series)]  # fmt: skip
        rows: list[dict[str, str]] = [*days[-count:], _row(today, 1.0)]
        return httpx.Response(200, content=json.dumps({"candles": rows}).encode())
    if path.endswith("/ticker") and product in _DOWN:
        return httpx.Response(503, content=b"unavailable")
    if path.endswith("/ticker"):
        body: dict[str, object] = {"trades": [{"price": str(last)}], "best_bid": str(last), "best_ask": str(last)}
    else:
        body = {
            "product_id": product, "status": "online", "trading_disabled": False, "is_disabled": False,
            "cancel_only": False, "limit_only": False, "base_increment": "0.00000001", "quote_increment": "0.01",
            "quote_min_size": "1", "base_min_size": "0.00000001",
        }  # fmt: skip
    return httpx.Response(200, content=json.dumps(body).encode())


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    yield opened
    await opened.close()


def _engine(store: Store, tmp_path: Path, **changes: object) -> tuple[TrendEngine, Trading]:
    base: TradingConfig = load_default_config().trading
    config: TradingConfig = base.model_copy(
        update={
            "mode": "paper",
            "products": ["BTC-EUR", "ETH-EUR"],
            "engine": "trend",
            "engine_coins": ["BTC", "ETH"],
            "max_order": 25.0,
            "max_day": 100.0,
            "max_position": 600.0,
            **changes,
        }
    )
    trading: Trading = Trading(
        store=store,
        settings=TradingSettings(config),
        market=CoinbaseMarket(5.0, httpx.MockTransport(_answer)),
        zone=ZoneInfo("UTC"),
        failsafe_file=tmp_path / "STOP",
        clock=lambda: _NOW,
    )
    runtime: Runtime = cast(Runtime, SimpleNamespace(trading=trading, store=store))
    return TrendEngine(runtime), trading


async def _engine_orders(trading: Trading) -> list[TradingOrderRecord]:
    return [order for order in await trading.history(50) if order.run_id == ENGINE_RUN_ID]


async def test_the_engine_buys_what_rises_within_the_limits_and_is_told_what_held_the_rest(
    store: Store, tmp_path: Path
) -> None:
    _PRICES.update({"BTC-EUR": _rising(230, start=50_000.0), "ETH-EUR": _falling(230)})
    engine, trading = _engine(store, tmp_path)
    await engine.tick(_NOW)
    orders: list[TradingOrderRecord] = await _engine_orders(trading)
    # BTC alone rises: one order of 25 EUR, its fee in it, at this look (ETH falls and is not held: nothing to sell).
    assert [(order.side, order.product, round(order.notional + order.fee, 2)) for order in orders] == [
        ("buy", "BTC-EUR", 25.0)
    ]
    assert orders[0].note.startswith("Trend engine: BTC-EUR is 0% of the account, the rule wants 50%")
    state: EngineState | None = await read_state(store)
    assert state is not None and state.placed == (orders[0].id,)
    assert {share.product: share.target for share in state.shares} == {"BTC-EUR": 0.5, "ETH-EUR": 0.0}
    # Within the hour nothing more; an hour on, the next step, until the day's limit holds it and says so.
    await engine.tick(_NOW + 60)
    assert len(await _engine_orders(trading)) == 1
    for hour in range(1, 5):
        await engine.tick(_NOW + hour * 3600)
    assert len(await _engine_orders(trading)) == 4
    state = await read_state(store)
    assert state is not None and any("today's orders may still add up to" in note for note in state.notes)


async def test_a_paused_engine_and_one_out_of_paper_place_nothing(store: Store, tmp_path: Path) -> None:
    _PRICES.update({"BTC-EUR": _rising(230, start=50_000.0), "ETH-EUR": _rising(230)})
    engine, trading = _engine(store, tmp_path)
    await pause(store, "Coinbase halted withdrawals", "run_1", _NOW)
    state: EngineState = await engine.look(trading.config(), _NOW)
    assert state.placed == () and state.notes == ("paused by run_1: Coinbase halted withdrawals",)
    assert await resume(store) and await read_pause(store) is None and not await resume(store)
    live: EngineState = await engine.look(trading.config().model_copy(update={"mode": "live"}), _NOW)
    assert live.placed == () and "trades on paper only" in live.notes[0]
    assert await _engine_orders(trading) == []
    off, _ = _engine(store, tmp_path, engine="off")
    await off.tick(_NOW)
    assert await read_state(store) == live


async def test_the_engine_sells_what_stops_rising_capped_at_the_most_one_order_may_be(
    store: Store, tmp_path: Path
) -> None:
    _PRICES.update({"BTC-EUR": _rising(230, start=50_000.0), "ETH-EUR": _falling(230)})
    engine, trading = _engine(store, tmp_path, max_order=100.0, max_day=1000.0)
    for hour in range(3):
        await engine.tick(_NOW + hour * 3600)
    assert [order.side for order in await _engine_orders(trading)] == ["buy", "buy", "buy"]
    # Then it falls under its close 200 days before, and every lookback says out; the cap is lower now.
    top: float = _PRICES["BTC-EUR"][-1]
    _PRICES["BTC-EUR"] = [*_PRICES["BTC-EUR"], *(top * 0.9 * 0.99**day for day in range(1, 121))]
    trading.settings.value = trading.config().model_copy(update={"max_order": 25.0})
    await engine.tick(_NOW + 4 * 3600)
    sells: list[TradingOrderRecord] = [order for order in await _engine_orders(trading) if order.side == "sell"]
    assert len(sells) == 1 and sells[0].notional == pytest.approx(25.0, abs=0.01)  # its size floored to a step
    state: EngineState | None = await read_state(store)
    assert state is not None and state.notes == ()


async def test_a_coin_coinbase_gave_too_few_days_of_is_held_not_sold(store: Store, tmp_path: Path) -> None:
    _PRICES.update({"BTC-EUR": _rising(230, start=50_000.0), "ETH-EUR": _rising(230)})
    engine, trading = _engine(store, tmp_path, max_order=100.0, max_day=1000.0)
    await engine.tick(_NOW)
    assert len(await _engine_orders(trading)) == 2
    _PRICES["BTC-EUR"] = _PRICES["BTC-EUR"][-50:]
    await engine.tick(_NOW + 3600)
    orders: list[TradingOrderRecord] = await _engine_orders(trading)
    assert [order.side for order in orders if order.product == "BTC-EUR"] == ["buy"]
    state: EngineState | None = await read_state(store)
    assert state is not None and any("BTC-EUR: Coinbase gave 50 daily closes" in note for note in state.notes)
    # Its share is kept for it: ETH's stays half the account, and ETH is not bought up to all of it.
    assert {share.product: share.target for share in state.shares}["ETH-EUR"] == pytest.approx(0.5, abs=0.01)


async def test_an_order_whose_price_cannot_be_read_leaves_those_placed_before_it_in_the_record(
    store: Store, tmp_path: Path
) -> None:
    _PRICES.update({"BTC-EUR": _rising(230, start=50_000.0), "ETH-EUR": _rising(230)})
    engine, trading = _engine(store, tmp_path)
    _DOWN.add("ETH-EUR")
    try:
        state: EngineState = await engine.look(trading.config(), _NOW)
    finally:
        _DOWN.clear()
    assert len(state.placed) == 1 and len(state.shares) == 2
    assert any(note.startswith("buy ETH-EUR not placed") for note in state.notes)


async def test_the_backtest_reads_coinbase_days_until_now_and_stops() -> None:
    asked: list[tuple[int, int]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        start, end = int(request.url.params["start"]), int(request.url.params["end"])
        asked.append((start, end))
        rows: list[dict[str, str]] = [
            {"start": str(day), "low": "1", "high": "1", "open": "1", "close": "2", "volume": "1"}
            for day in range(start - start % 86_400, end, 86_400)
        ]
        return httpx.Response(200, content=json.dumps({"candles": rows}).encode())

    market: CoinbaseMarket = CoinbaseMarket(5.0, httpx.MockTransport(answer))
    # An end with a fraction of a second: the last window once asked for nothing, again and again.
    end: float = _NOW + 0.7
    found: tuple[Candle, ...] = await market.daily_history("BTC-EUR", end - 700 * _DAY, end)
    assert len(asked) == 3 and asked[-1][1] == int(end)
    assert len(found) == len({candle.start for candle in found}) >= 699
    assert [candle.start for candle in found] == sorted(candle.start for candle in found)


async def test_the_supervisor_reads_pauses_and_resumes_the_engine_with_a_reason(store: Store, tmp_path: Path) -> None:
    from dotagent.core.errors import ToolInputError
    from dotagent.tools.base import PreparedCall, ToolResult
    from dotagent.tools.context import ToolContext
    from dotagent.tools.trading import build_trading_tools

    _PRICES.update({"BTC-EUR": _rising(230, start=50_000.0), "ETH-EUR": _falling(230)})
    engine, trading = _engine(store, tmp_path)
    await engine.tick(_NOW)
    ctx: ToolContext = cast(
        ToolContext, SimpleNamespace(runtime=SimpleNamespace(trading=trading, store=store), run_id="run_s")
    )
    tool = {tool.spec.name: tool for tool in build_trading_tools()}["trading_engine"]
    status: PreparedCall = await tool.prepare({}, ctx)
    assert (status.action.kind, status.action.flags) == ("read", ())
    told: ToolResult = await status.run(ctx)
    assert (
        "is on, trading BTC-EUR, ETH-EUR" in told.text
        and "BTC-EUR: held 0% before that look's orders, the rule wants 50%" in told.text
    )
    with pytest.raises(ToolInputError, match="say why"):
        await tool.prepare({"action": "pause"}, ctx)
    paused: ToolResult = await (await tool.prepare({"action": "pause", "reason": "Coinbase is down"}, ctx)).run(ctx)
    assert paused.text.startswith("Paused.") and "by run_s: Coinbase is down" in paused.text
    await engine.tick(_NOW + 3600)
    assert len(await _engine_orders(trading)) == 1
    resumed: ToolResult = await (await tool.prepare({"action": "resume", "reason": "it is back"}, ctx)).run(ctx)
    assert resumed.text.startswith("Resumed") and await read_pause(store) is None
    again: ToolResult = await (await tool.prepare({"action": "resume", "reason": "twice"}, ctx)).run(ctx)
    assert again.is_error
