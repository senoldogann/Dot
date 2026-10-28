"""Market wakes (item 772): the daemon's watch reads the allowed products' prices about once a minute, with no model,
and starts the schedule ``[trading] wake_schedule`` names when one moved over fifteen minutes by ``wake_move_pct`` and
by three times what it usually moves; within the day's count and the gap between two, one move waking one run. The
market is a stand-in: no request leaves the Mac."""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest_asyncio

from dotagent.config import TradingConfig, load_default_config
from dotagent.core.errors import RunConflict
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.trading.signals import Move, PriceMemory, move_words
from dotagent.trading.types import MarketRow
from dotagent.trading.watch import LAST_WAKE_KEY, WAKES_KEY, TradingWatch

_T0: float = 1_793_000_000.0
_DAY: str = "2026-10-28"
_MINUTE: float = 60.0


def _row(product: str, price: float, volume: float = 5_000_000.0) -> MarketRow:
    return MarketRow(
        product=product, price=price, bid=price, ask=price, change_24h_pct=None, high_24h=None, low_24h=None,
        quote_volume_24h=volume,
    )  # fmt: skip


def _calm(look: int, jump_at: int = 60) -> float:
    """A product that wiggles by a tenth of a percent, then jumps by four at ``jump_at``."""
    return 104.0 if look >= jump_at else 100.0 + 0.05 * (-1) ** look


def _wild(look: int) -> float:
    """A product that swings by four percent every quarter of an hour."""
    return 100.0 if (look // 15) % 2 == 0 else 104.0


def test_a_calm_product_that_jumps_is_a_move_and_a_restless_young_or_unread_one_is_not() -> None:
    memory: PriceMemory = PriceMemory()
    for look in range(61):
        rows: list[MarketRow] = [_row("CALM-EUR", _calm(look)), _row("WILD-EUR", _wild(look))]
        # YOUNG has been looked at for twenty minutes only; GAPPY not since the Mac slept at forty.
        if look >= 40:
            rows.append(_row("YOUNG-EUR", 104.0 if look == 60 else 100.0))
        if look <= 40 or look == 60:
            rows.append(_row("GAPPY-EUR", 104.0 if look == 60 else 100.0))
        # DROP moved as CALM did, but was not in the last look.
        if look < 60:
            rows.append(_row("DROP-EUR", _calm(look, jump_at=59)))
        memory.add(_T0 + look * _MINUTE, rows)
    (move,) = memory.moves(_T0 + 60 * _MINUTE, 3.0)
    assert (move.product, round(move.pct, 2), round(move.usual_pct, 2), move.price) == ("CALM-EUR", 4.05, 0.1, 104.0)
    # Under the owner's least move, a move three times the usual one wakes nothing.
    assert memory.moves(_T0 + 60 * _MINUTE, 5.0) == []
    assert move_words([move], "EUR") == "CALM-EUR +4.05 % in 15 minutes (it usually moves 0.10 %), now 104 EUR"
    # Two hours on, the old prices are forgotten.
    memory.add(_T0 + 200 * _MINUTE, [_row("CALM-EUR", 104.0)])
    assert memory.moves(_T0 + 200 * _MINUTE, 0.01) == []


class _Market:
    def __init__(self) -> None:
        self.rows: list[MarketRow] = []
        self.looked: int = 0

    async def whole_market(self, quote: str) -> tuple[MarketRow, ...]:
        self.looked += 1
        return tuple(self.rows)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    yield opened
    await opened.close()


def _config(**changes: object) -> TradingConfig:
    base: TradingConfig = load_default_config().trading
    return base.model_copy(
        update={"mode": "paper", "products": ["*"], "wake_schedule": "sch_trading", "wake_per_day": 2, **changes}
    )


def _watch(
    store: Store, config: TradingConfig, woke: list[tuple[str, str]], refusal: RunConflict | None = None
) -> tuple[TradingWatch, _Market]:
    market: _Market = _Market()
    trading = SimpleNamespace(config=lambda: config, market=market, day_start=lambda: (_DAY, _T0))

    async def wake(schedule_id: str, now: float, signal: str) -> None:
        if refusal is not None:
            raise refusal
        woke.append((schedule_id, signal))

    return TradingWatch(cast(Runtime, SimpleNamespace(trading=trading, store=store)), wake), market


async def _looks(watch: TradingWatch, market: _Market, looks: range, prices: Callable[[int], list[MarketRow]]) -> None:
    for look in looks:
        market.rows = prices(look)
        await watch.look_at_market(_T0 + look * _MINUTE)


async def test_a_move_wakes_the_named_schedule_once_and_the_next_waits_for_the_gap_and_the_days_count(
    store: Store,
) -> None:
    woke: list[tuple[str, str]] = []
    watch, market = _watch(store, _config(), woke)

    def prices(look: int) -> list[MarketRow]:
        # CALM jumps at an hour, SLOW five minutes later (within the gap), LATE at two hours (past it), THIN under the
        # day's volume the owner allows a product at.
        return [
            _row("CALM-EUR", _calm(look)),
            _row("SLOW-EUR", _calm(look, jump_at=65)),
            _row("LATE-EUR", _calm(look, jump_at=120)),
            _row("THIN-EUR", _calm(look), volume=10_000.0),
        ]

    await _looks(watch, market, range(66), prices)
    ((schedule_id, signal),) = woke
    assert schedule_id == "sch_trading" and signal.startswith("CALM-EUR +4.05 % in 15 minutes") and "THIN" not in signal
    assert await store.kv_get(f"{WAKES_KEY}:{_DAY}") == "1"
    assert await store.kv_get(LAST_WAKE_KEY) == repr(_T0 + 60 * _MINUTE)
    # SLOW moved within the gap: no run, and its move is not taken up later; CALM does not wake again either.
    await _looks(watch, market, range(66, 100), prices)
    assert len(woke) == 1
    await _looks(watch, market, range(100, 121), prices)
    assert [signal.split(" ")[0] for _, signal in woke] == ["CALM-EUR", "LATE-EUR"]
    # The day's count reached: a move past the gap wakes nothing more.
    later: Callable[[int], list[MarketRow]] = lambda look: [_row("NEXT-EUR", _calm(look, jump_at=200))]  # noqa: E731
    await _looks(watch, market, range(130, 201), later)
    assert len(woke) == 2 and await store.kv_get(f"{WAKES_KEY}:{_DAY}") == "2"


async def test_a_wake_that_cannot_start_counts_nothing_and_nothing_is_watched_while_off_or_unnamed(
    store: Store,
) -> None:
    def prices(look: int) -> list[MarketRow]:
        return [_row("CALM-EUR", _calm(look))]

    woke: list[tuple[str, str]] = []
    # A run of the schedule at work (or the emergency stop, or the schedule switched off): nothing is counted.
    busy, market = _watch(store, _config(), woke, RunConflict("a run of this schedule is already working", {}))
    await _looks(busy, market, range(61), prices)
    assert woke == [] and await store.kv_get(f"{WAKES_KEY}:{_DAY}") is None and market.looked == 61
    for config in (_config(mode="off"), _config(wake_schedule="")):
        idle, unread = _watch(store, config, woke)
        await _looks(idle, unread, range(61), prices)
        assert unread.looked == 0 and woke == []
    # Looked at no more than once a minute.
    watch, often = _watch(store, _config(), woke)
    for second in range(0, 120, 15):
        await watch.look_at_market(_T0 + second)
    assert often.looked == 2


def test_a_move_is_its_product_its_size_its_usual_size_and_its_price() -> None:
    move: Move = Move(product="SOL-USDC", pct=-3.456, usual_pct=0.8, price=187.25)
    assert move_words([move, move], "USDC") == "; ".join(
        ["SOL-USDC -3.46 % in 15 minutes (it usually moves 0.80 %), now 187.25 USDC"] * 2
    )
