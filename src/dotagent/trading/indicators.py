"""What a window of candles says in a few numbers (item 765): where the last close stands against the window and its
moving averages, the strength of the last moves (RSI), how wide the candles are. Pure arithmetic on what the market
gave; each number is ``None`` where the window is too short for it, never a guess."""

from collections.abc import Sequence
from itertools import pairwise

from dotagent.core.types import FrozenModel
from dotagent.trading.types import Candle

SHORT_AVERAGE: int = 20
LONG_AVERAGE: int = 50
RSI_PERIODS: int = 14


class Indicators(FrozenModel):
    """The numbers of a window of candles, oldest first: the last close, how it changed over the window (in percent
    of the first open), the window's high and low, the averages of the last :data:`SHORT_AVERAGE` and
    :data:`LONG_AVERAGE` closes, the RSI of the last :data:`RSI_PERIODS` moves (0 to 100; over 70 is called
    overbought, under 30 oversold), and the average candle range in percent of the last close."""

    candles: int
    last: float
    change_pct: float
    high: float
    low: float
    short_average: float | None
    long_average: float | None
    rsi: float | None
    range_pct: float


def _average(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _rsi(closes: Sequence[float], periods: int) -> float | None:
    """Wilder's RSI over the last ``periods`` moves, smoothed over the window; ``None`` with fewer moves than that."""
    if len(closes) <= periods:
        return None
    moves: list[float] = [later - earlier for earlier, later in pairwise(closes)]
    gains: float = _average([max(move, 0.0) for move in moves[:periods]])
    losses: float = _average([max(-move, 0.0) for move in moves[:periods]])
    for move in moves[periods:]:
        gains = (gains * (periods - 1) + max(move, 0.0)) / periods
        losses = (losses * (periods - 1) + max(-move, 0.0)) / periods
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def indicators(candles: Sequence[Candle]) -> Indicators | None:
    """The :class:`Indicators` of ``candles`` (oldest first); ``None`` for an empty window."""
    if not candles:
        return None
    closes: list[float] = [candle.close for candle in candles]
    last: float = closes[-1]
    first_open: float = candles[0].open
    return Indicators(
        candles=len(candles),
        last=last,
        change_pct=(last - first_open) / first_open * 100.0,
        high=max(candle.high for candle in candles),
        low=min(candle.low for candle in candles),
        short_average=_average(closes[-SHORT_AVERAGE:]) if len(closes) >= SHORT_AVERAGE else None,
        long_average=_average(closes[-LONG_AVERAGE:]) if len(closes) >= LONG_AVERAGE else None,
        rsi=_rsi(closes, RSI_PERIODS),
        range_pct=_average([(candle.high - candle.low) / last * 100.0 for candle in candles]),
    )


def _against(last: float, average: float | None, name: str) -> str:
    if average is None:
        return f"{name} needs more candles"
    return f"{name} {average:.8g} ({'above' if last > average else 'below' if last < average else 'at'} it)"


def indicator_text(found: Indicators) -> str:
    """The numbers as one line a run reads before it decides."""
    rsi: str = f"RSI{RSI_PERIODS} {found.rsi:.0f}" if found.rsi is not None else f"RSI{RSI_PERIODS} needs more candles"
    return (
        f"Over these {found.candles} candles: close {found.last:.8g} ({found.change_pct:+.2f} % from the first open), "
        f"high {found.high:.8g}, low {found.low:.8g}; "
        f"{_against(found.last, found.short_average, f'SMA{SHORT_AVERAGE}')}, "
        f"{_against(found.last, found.long_average, f'SMA{LONG_AVERAGE}')}; {rsi}; "
        f"a candle spans {found.range_pct:.2f} % of the close on average."
    )
