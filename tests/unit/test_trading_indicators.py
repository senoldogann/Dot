"""The numbers a window of candles comes to (item 765): checked against values worked out by hand, and ``None`` where
the window is too short, never a guess."""

import pytest

from dotagent.trading.indicators import Indicators, indicator_text, indicators
from dotagent.trading.types import Candle


def _candles(closes: list[float]) -> list[Candle]:
    return [
        Candle(start=3600.0 * index, low=close - 1.0, high=close + 1.0, open=close - 0.5, close=close, volume=1.0)
        for index, close in enumerate(closes)
    ]


def test_a_short_window_gives_what_it_can_and_none_for_the_rest() -> None:
    found: Indicators | None = indicators(_candles([100.0, 101.0, 102.0]))
    assert found is not None
    assert (found.candles, found.last, found.high, found.low) == (3, 102.0, 103.0, 99.0)
    assert found.change_pct == pytest.approx((102.0 - 99.5) / 99.5 * 100.0)
    assert (found.short_average, found.long_average, found.rsi) == (None, None, None)
    assert found.range_pct == pytest.approx(2.0 / 102.0 * 100.0)
    assert indicators([]) is None
    assert "needs more candles" in indicator_text(found)


def test_the_averages_and_the_rsi_are_wilders_over_the_window() -> None:
    # Fifty closes rising by one, then ten falling by two: the last twenty and fifty closes have known means.
    closes: list[float] = [float(100 + step) for step in range(50)] + [float(149 - 2 * step) for step in range(1, 11)]
    found: Indicators | None = indicators(_candles(closes))
    assert found is not None
    assert found.short_average == pytest.approx(sum(closes[-20:]) / 20)
    assert found.long_average == pytest.approx(sum(closes[-50:]) / 50)
    # The first fourteen moves are gains of one, then more gains, then ten losses of two: smoothed, the RSI has fallen
    # well under 50 but not to 0, as the smoothing keeps part of the gains.
    assert found.rsi is not None and 10.0 < found.rsi < 50.0
    only_up: Indicators | None = indicators(_candles([float(100 + step) for step in range(16)]))
    assert only_up is not None and only_up.rsi == 100.0
    text: str = indicator_text(found)
    assert "SMA20" in text and "below it" in text and "RSI14" in text
