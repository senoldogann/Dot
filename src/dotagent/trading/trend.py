"""The trend engine's rule (item 818, docs/trading.md): what share of the account each product should be, from its
daily closes alone, and the steps that bring the account there; and the same rule played over years of closes, with
the fees, against holding Bitcoin.

The rule is the one the evidence on crypto holds up best once costs are counted, written down before it was tried so
that no number of it was picked for the result it gave: time-series momentum, a product held while its close is over
its close 20, 50, 100 and 200 days before (the share of the four that are, as a strength from 0 to 1), sized so that
it moves the account as much as a product of ``target_vol`` yearly volatility would (read over the last 60 days, never
more than its whole share), with an equal share for each product. Long or out, never short and never on credit: a
product falling holds cash instead. A share that is off its target by less than ``band`` is left as it is, as every
trade pays its fee twice over a round trip.

Pure: the engine (:mod:`dotagent.trading.engine`) reads the closes and the account, and places the steps through the
limits of ``[trading]``."""

import math
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal

from dotagent.core.types import FrozenModel

LOOKBACKS: tuple[int, ...] = (20, 50, 100, 200)
VOL_DAYS: int = 60
# The closes a product needs before the rule holds it: its close and the one 200 days before.
DAYS_NEEDED: int = max(LOOKBACKS) + 1
_YEAR_DAYS: int = 365
_DAY_S: int = 86_400


def strength(closes: Sequence[float]) -> float | None:
    """The share of the lookbacks whose close the last close is over, from 0 to 1; ``None`` with too few closes."""
    if len(closes) < DAYS_NEEDED:
        return None
    last: float = closes[-1]
    return sum(1 for days in LOOKBACKS if last > closes[-1 - days]) / len(LOOKBACKS)


def volatility(closes: Sequence[float]) -> float | None:
    """The yearly volatility of the last :data:`VOL_DAYS` daily returns; ``None`` with too few closes."""
    if len(closes) < VOL_DAYS + 1:
        return None
    window: Sequence[float] = closes[-VOL_DAYS - 1 :]
    returns: list[float] = [window[i] / window[i - 1] - 1 for i in range(1, len(window))]
    mean: float = sum(returns) / len(returns)
    variance: float = sum((one - mean) ** 2 for one in returns) / len(returns)
    return math.sqrt(variance) * math.sqrt(_YEAR_DAYS)


def target_weights(closes: Mapping[str, Sequence[float]], target_vol: float, kept: int = 0) -> dict[str, float]:
    """The share of the account each product should be: its strength, scaled down to ``target_vol``, over the number
    of products with closes enough to judge, and ``kept`` more: those the engine holds and leaves as they are at a
    look that cannot judge them, whose share is not given to the others. A product without closes enough is 0."""
    judged: dict[str, tuple[float, float]] = {}
    for product, series in closes.items():
        found: float | None = strength(series)
        vol: float | None = volatility(series)
        if found is not None and vol is not None:
            judged[product] = (found, vol)
    weights: dict[str, float] = dict.fromkeys(closes, 0.0)
    for product, (found, vol) in judged.items():
        scale: float = 1.0 if vol <= 0 else min(1.0, target_vol / vol)
        weights[product] = found * scale / (len(judged) + kept)
    return weights


class Step(FrozenModel):
    """An order that brings a product toward its share: a buy spends ``quote_amount``, a sell sells ``base_amount``;
    ``weight`` is the share it holds now and ``target`` the one it should; ``whole`` whether it brings the product to
    its target (within :data:`REACHED`), rather than as far as ``max_order`` lets it."""

    product: str
    side: Literal["buy", "sell"]
    quote_amount: float | None
    base_amount: float | None
    weight: float
    target: float
    whole: bool


# The share of what is held a sell by its worth leaves, so that a bid a little lower when the order is placed still
# covers it; the rest goes at a later look, sold whole once it is under the cap.
_SELL_MARGIN: float = 0.99
# How near its target a product being brought there (``chasing``) is left: the band starts a trade and this ends it,
# as the backtest's trade goes all the way to the target in one step (item 819).
REACHED: float = 0.01


def steps_toward(
    targets: Mapping[str, float],
    values: Mapping[str, float],
    sizes: Mapping[str, float],
    equity: float,
    band: float,
    max_order: float,
    chasing: Collection[str] = (),
) -> list[Step]:
    """The orders that bring the account toward ``targets`` (shares of ``equity``), from what each product is worth
    (``values``, at the bid) and how much of it is held (``sizes``): sells first, as they free the cash the buys
    spend. A share off its target by no more than ``band`` stays; a target of 0 sells what is held, whatever the band.
    No order is worth more than ``max_order``: what is left is done at the next look, within the day's limits. A sell
    names what it brings (``quote_amount``), which is what the limits weigh, so that a bid a little higher when it is
    placed cannot take it over ``max_order``; only what is held whole, well under the cap, is sold by its size.

    A product in ``chasing`` is one a step of an earlier look did not bring to its target (``max_order`` or a limit
    held it): it is traded on until it is within :data:`REACHED` of its target rather than the band, so that the
    account ends where the backtest's would (item 819: it stopped within the band of where it started)."""
    if equity <= 0:
        return []
    sells: list[Step] = []
    buys: list[Step] = []
    near: float = REACHED * equity
    for product, target in targets.items():
        value: float = values.get(product, 0.0)
        size: float = sizes.get(product, 0.0)
        weight: float = value / equity
        gap: float = target - weight
        within: float = min(band, REACHED) if product in chasing else band
        if size > 0 and (target <= 0 or gap < -within):
            wanted: float = value if target <= 0 else -gap * equity
            if wanted >= value and value <= max_order * _SELL_MARGIN:
                sells.append(
                    Step(product=product, side="sell", quote_amount=None, base_amount=size, weight=weight,
                         target=target, whole=True)
                )  # fmt: skip
            else:
                amount: float = min(wanted, max_order, value * _SELL_MARGIN)
                sells.append(
                    Step(product=product, side="sell", quote_amount=amount, base_amount=None, weight=weight,
                         target=target, whole=wanted - amount <= near)
                )  # fmt: skip
        elif gap > within:
            spent: float = min(gap * equity, max_order)
            buys.append(
                Step(product=product, side="buy", quote_amount=spent, base_amount=None, weight=weight, target=target,
                     whole=gap * equity - spent <= near)
            )  # fmt: skip
    return [*sells, *buys]


class YearResult(FrozenModel):
    """One calendar year of a backtest: the rule's return and holding Bitcoin's, as shares (0.1 is 10%)."""

    year: int
    rule: float
    benchmark: float


class Backtest(FrozenModel):
    """The rule played over ``start``-``end`` (seconds since 1970) with ``cost`` per side, against holding the first
    product: the yearly growth, Sharpe ratio (daily returns, a year of 365 days, no risk-free rate) and deepest fall of
    each, how many times a year the rule turned the account over, and each year."""

    start: float
    end: float
    cost: float
    rule_cagr: float
    rule_sharpe: float
    rule_max_drawdown: float
    benchmark_cagr: float
    benchmark_sharpe: float
    benchmark_max_drawdown: float
    turnover_per_year: float
    years: tuple[YearResult, ...]


def _growth(curve: Sequence[float]) -> tuple[float, float, float]:
    days: int = len(curve) - 1
    returns: list[float] = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    cagr: float = curve[-1] ** (_YEAR_DAYS / days) - 1 if days > 0 and curve[-1] > 0 else -1.0
    mean: float = sum(returns) / len(returns) if returns else 0.0
    spread: float = math.sqrt(sum((one - mean) ** 2 for one in returns) / len(returns)) if returns else 0.0
    sharpe: float = mean / spread * math.sqrt(_YEAR_DAYS) if spread > 0 else 0.0
    peak: float = curve[0]
    deepest: float = 0.0
    for value in curve:
        peak = max(peak, value)
        deepest = min(deepest, value / peak - 1)
    return cagr, sharpe, deepest


def backtest(
    closes: Mapping[str, Sequence[tuple[float, float]]], cost: float, target_vol: float, band: float
) -> Backtest:
    """Play the rule over daily ``closes`` (each product's ``(day start, close)``, oldest first; the first product is
    the benchmark held), from the first day the benchmark has :data:`DAYS_NEEDED` closes. Each day's shares are judged
    at its close and held to the next; every share traded pays ``cost`` (the fee and the slippage of one side), as the
    engine's steps would: a target of 0 sells all, a gap of no more than ``band`` stays."""
    products: list[str] = list(closes)
    if not products:
        raise ValueError("a backtest needs closes of at least one product")
    days: list[float] = sorted({day for series in closes.values() for day, _ in series})
    price: dict[str, dict[float, float]] = {product: dict(series) for product, series in closes.items()}
    first: str = products[0]
    seen: dict[str, list[float]] = {product: [] for product in products}
    weights: dict[str, float] = dict.fromkeys(products, 0.0)
    curve: list[float] = []
    benchmark: list[float] = []
    stamps: list[float] = []
    traded: float = 0.0
    equity: float = 1.0
    held: float = 1.0
    previous: dict[str, float] = {}
    for day in days:
        today: dict[str, float] = {product: price[product][day] for product in products if day in price[product]}
        if curve:
            growth: float = 1.0 + sum(
                weights[product] * (today[product] / previous[product] - 1)
                for product in products
                if product in today and product in previous
            )
            equity *= growth
            for product in products:
                if product in today and product in previous and growth > 0:
                    weights[product] *= (today[product] / previous[product]) / growth
            if first in today and first in previous:
                held *= today[first] / previous[first]
        for product, close in today.items():
            seen[product].append(close)
        previous.update(today)
        if len(seen[first]) < DAYS_NEEDED:
            continue
        targets: dict[str, float] = target_weights(
            {product: series for product, series in seen.items() if product in today}, target_vol
        )
        turnover: float = 0.0
        for product, target in targets.items():
            gap: float = target - weights[product]
            if (target <= 0 < weights[product]) or abs(gap) > band:
                turnover += abs(gap)
                weights[product] = target
        if not curve:
            equity, held = 1.0, 1.0
        equity *= 1 - turnover * cost
        traded += turnover
        curve.append(equity)
        benchmark.append(held)
        stamps.append(day)
    if len(curve) < 2:
        raise ValueError(f"the backtest needs more than {DAYS_NEEDED} days of {first}")
    rule_cagr, rule_sharpe, rule_drawdown = _growth(curve)
    bench_cagr, bench_sharpe, bench_drawdown = _growth(benchmark)
    return Backtest(
        start=stamps[0],
        end=stamps[-1],
        cost=cost,
        rule_cagr=rule_cagr,
        rule_sharpe=rule_sharpe,
        rule_max_drawdown=rule_drawdown,
        benchmark_cagr=bench_cagr,
        benchmark_sharpe=bench_sharpe,
        benchmark_max_drawdown=bench_drawdown,
        turnover_per_year=traded / ((stamps[-1] - stamps[0]) / _DAY_S / _YEAR_DAYS),
        years=_years(stamps, curve, benchmark),
    )


def _years(stamps: Sequence[float], curve: Sequence[float], benchmark: Sequence[float]) -> tuple[YearResult, ...]:
    """Each calendar year's return of the rule and of the benchmark, from the last value of the year before."""
    ends: dict[int, int] = {}
    for index, stamp in enumerate(stamps):
        ends[datetime.fromtimestamp(stamp, UTC).year] = index
    found: list[YearResult] = []
    before: int = 0
    for year, last in sorted(ends.items()):
        found.append(
            YearResult(
                year=year, rule=curve[last] / curve[before] - 1, benchmark=benchmark[last] / benchmark[before] - 1
            )
        )
        before = last
    return tuple(found)
