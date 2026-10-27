"""The limits of trading (``[trading]``): what an order may be, judged before it reaches any market, in every approval
mode, yolo's too. Pure: the caller reads the portfolio, the quote and the rules first, under the lock that keeps two
orders from being judged against the same day."""

from dotagent.config import ANY_PRODUCT, TradingConfig
from dotagent.trading.types import MarketRow, Portfolio, ProductRules, Side

# What is left of a limit is said with this many decimals.
_SHOWN: int = 2


def not_allowed(config: TradingConfig, product: str, rules: ProductRules | None) -> str | None:
    """Why ``product`` is not one the owner allows, or ``None`` when it is: named among the products, or, with ``"*"``
    there (item 771), priced in the quote and trading at least ``min_volume`` of it a day (``rules`` says how much;
    without them a product not named is not allowed)."""
    if product in config.products:
        return None
    if ANY_PRODUCT not in config.products:
        return f"{product} is not among the products the owner allows ({', '.join(config.products)})"
    if not product.endswith(f"-{config.quote}"):
        return f"{product} is not priced in {config.quote}, the currency of the owner's limits"
    volume: float | None = None if rules is None else rules.quote_volume_24h
    if volume is None or volume < config.min_volume:
        traded: str = "an unknown amount" if volume is None else _money(volume, config.quote)
        return (
            f"{product} traded {traded} in the last day, under the {_money(config.min_volume, config.quote)} the owner "
            "allows a product at"
        )
    return None


def market_allows(config: TradingConfig, row: MarketRow) -> bool:
    """Whether the owner allows the product of ``row``, one of Coinbase's list in the quote: named among the products,
    or, with ``"*"`` there (item 771), trading at least ``min_volume`` a day, as :func:`not_allowed` judges an order."""
    return row.product in config.products or (
        ANY_PRODUCT in config.products and row.quote_volume_24h >= config.min_volume
    )


def _money(value: float, quote: str) -> str:
    return f"{value:.{_SHOWN}f} {quote}"


def refusal(
    config: TradingConfig,
    product: str,
    side: Side,
    notional: float,
    portfolio: Portfolio,
    rules: ProductRules,
    cash: float,
    held: float,
    held_value: float,
    closing: bool = False,
) -> str | None:
    """Why the order of ``side`` worth ``notional`` (in the quote) of ``product`` may not be placed, or ``None`` when it
    may. ``cash`` is the quote the account holds, ``held`` the base of ``product`` it holds and ``held_value`` what that
    is worth at the bid. A sell is never held back by the day's loss or the position: it lowers what is at stake. The
    sell of an exit (``closing``, item 770) is held back by none of the limits: a stop that one order's cap or the day's
    could keep from selling would protect nothing; the product need not be allowed any more either."""
    quote: str = config.quote
    if config.mode == "off":
        return "trading is off ([trading] mode); only the owner switches it on, in Settings › Trading"
    if closing:
        if side != "sell":
            return "an exit only sells"
        if not rules.trading:
            return f"Coinbase does not take orders of {product} now"
        if held <= 0 or not notional > 0:
            return f"the account holds no {product.split('-')[0]} to sell"
        return None
    disallowed: str | None = not_allowed(config, product, rules)
    if disallowed is not None:
        return disallowed
    if not rules.trading:
        return f"Coinbase does not take orders of {product} now"
    if not notional > 0:
        return "the order is worth nothing"
    if notional < rules.quote_min_size:
        return f"the order is under Coinbase's least order of {product}, {_money(rules.quote_min_size, quote)}"
    if notional > config.max_order:
        return (
            f"the order, {_money(notional, quote)}, is over the most one order may be, "
            f"{_money(config.max_order, quote)}"
        )
    left_today: float = config.max_day - portfolio.day_traded
    if notional > left_today:
        return (
            f"the order, {_money(notional, quote)}, is over what today's orders may still add up to, "
            f"{_money(max(left_today, 0.0), quote)} of {_money(config.max_day, quote)}"
        )
    if side == "sell":
        if held <= 0:
            return f"the account holds no {product.split('-')[0]} to sell"
        return None
    lost: float = -portfolio.day_change
    if lost >= config.max_day_loss:
        return (
            f"the account has lost {_money(lost, quote)} today, as much as a day may lose "
            f"({_money(config.max_day_loss, quote)}): no buy until tomorrow; a sell is still placed"
        )
    if held_value + notional > config.max_position:
        return (
            f"after the buy {product.split('-')[0]} would be worth {_money(held_value + notional, quote)}, over the "
            f"most one product may be, {_money(config.max_position, quote)}"
        )
    if notional > cash:
        return f"the account holds {_money(cash, quote)}, less than the order"
    return None
