"""``dotx trading backtest`` (item 818): the trend engine's rule (:mod:`dotagent.trading.trend`) played over years of
Coinbase's daily closes of ``[trading] engine_coins``, with what a trade costs, against holding the first coin. Only
public market data is read; nothing is traded."""

import sys
import time

from dotagent.cli_commands import Command, TradingBacktestCommand
from dotagent.config import TradingConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.trading.market import CoinbaseMarket
from dotagent.trading.trend import Backtest, backtest
from dotagent.trading.types import Candle

_YEAR_S: float = 365.25 * 86_400


def render(result: Backtest, products: list[str], target_vol: float, band: float) -> str:
    def day(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(ts))

    lines: list[str] = [
        f"The trend engine's rule on {', '.join(products)}, {day(result.start)} to {day(result.end)}: each side of a "
        f"trade costs {result.cost:.2%}, shares sized to {target_vol:.0%} yearly volatility, a band of {band:.0%}.",
        "",
        f"{'':22}{'a year':>10}{'Sharpe':>9}{'deepest fall':>15}",
        f"{'the rule':22}{result.rule_cagr:>10.1%}{result.rule_sharpe:>9.2f}{result.rule_max_drawdown:>15.1%}",
        f"{'holding ' + products[0]:22}{result.benchmark_cagr:>10.1%}{result.benchmark_sharpe:>9.2f}"
        f"{result.benchmark_max_drawdown:>15.1%}",
        "",
        f"The rule turned the account over {result.turnover_per_year:.1f} times a year, which cost about "
        f"{result.turnover_per_year * result.cost:.1%} of it a year.",
        "",
        f"{'year':6}{'the rule':>10}{'holding':>10}",
        *(f"{one.year:<6}{one.rule:>10.1%}{one.benchmark:>10.1%}" for one in result.years),
        "",
        "Past closes are no promise: the rule was written down before it was tried, and is held to it.",
    ]
    return "\n".join(lines)


async def cmd_backtest(command: TradingBacktestCommand) -> int:
    config: TradingConfig = load_user_config().trading
    products: list[str] = config.engine_products()
    market: CoinbaseMarket = CoinbaseMarket(config.timeout_s)
    end: float = time.time()
    start: float = end - command.years * _YEAR_S
    closes: dict[str, list[tuple[float, float]]] = {}
    for product in products:
        candles: tuple[Candle, ...] = await market.daily_history(product, start, end)
        # Today's period is still trading: its last price is no close.
        closes[product] = [(candle.start, candle.close) for candle in candles if candle.start + 86_400 <= end]
    result: Backtest = backtest(closes, command.cost, config.engine_target_vol, config.engine_band)
    sys.stdout.write(render(result, products, config.engine_target_vol, config.engine_band) + "\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case TradingBacktestCommand():
            return await cmd_backtest(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
