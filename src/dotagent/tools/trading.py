"""The trading tools (docs/trading.md): the market's prices, the account, its orders, and an order. They exist only
while ``[trading] mode`` is ``paper`` or ``live``.

An order of the owner's Coinbase account (``live``) is money: it carries ``trade``, so it waits for the owner's yes in
every mode but yolo, which places it, and a yolo run that read outside content has the review model check it (item
525). An order of the
paper account moves no money. Whatever the mode, the limits of ``[trading]`` judge it before any market sees it, and an
order is placed in the account it was judged for: one approved for paper never goes to Coinbase because the owner
switched to live while it waited.
"""

from typing import Literal

from pydantic import Field, model_validator

from dotagent.config import TradingConfig
from dotagent.policy.types import ActionInfo
from dotagent.store.models import TradingOrderRecord
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_reviewed_tool,
    make_tool,
    ok,
)
from dotagent.tools.context import ToolContext
from dotagent.trading.market import granularities, when
from dotagent.trading.service import Trading, TradingRefused, holdings_text
from dotagent.trading.types import Candle, OrderRequest, Portfolio, Quote

MARKET_TOOL: str = "trading_market"
PORTFOLIO_TOOL: str = "trading_portfolio"
ORDER_TOOL: str = "trading_order"
HISTORY_TOOL: str = "trading_history"
# An order reads the prices, the rules and the account, checks the key and follows the order, each bounded by
# [trading] timeout_s: the tool's own bound leaves room for all of them.
_TIMEOUT_S: float = 120.0
_HISTORY_DEFAULT: int = 20


def _trading(ctx: ToolContext) -> Trading:
    return ctx.runtime.trading


def _config(ctx: ToolContext) -> TradingConfig:
    return ctx.runtime.trading.config()


def _read(tool: str, summary: str) -> ActionInfo:
    return ActionInfo(tool=tool, kind="network", risk="safe", summary=summary, target="api.coinbase.com", flags=())


class MarketArgs(ToolArgs):
    product: str | None = Field(
        default=None, description="A product of the allowed list, as BTC-EUR; none: the best prices of every one."
    )
    granularity: Literal["ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE", "ONE_HOUR", "SIX_HOUR", "ONE_DAY"] | None = (
        Field(default=None, description="With a product: its candles of this period, oldest first.")
    )
    count: int = Field(default=48, ge=1, le=300, description="How many candles (with granularity).")


def _quote_line(quote: Quote, currency: str) -> str:
    spread: float = (quote.ask - quote.bid) / quote.ask * 100
    return (
        f"{quote.product}: bid {quote.bid:.8g}, ask {quote.ask:.8g} {currency} (spread {spread:.3f} %), last trade "
        f"{quote.last:.8g}"
    )


def _candle_lines(candles: tuple[Candle, ...]) -> str:
    rows: list[str] = ["start (UTC) | open | high | low | close | volume"]
    rows.extend(
        f"{when(candle.start)} | {candle.open:.8g} | {candle.high:.8g} | {candle.low:.8g} | {candle.close:.8g} | "
        f"{candle.volume:.6g}"
        for candle in candles
    )
    return "\n".join(rows)


def _market_tool() -> RegisteredTool:
    def classify(args: MarketArgs, ctx: ToolContext) -> ActionInfo:
        return _read(MARKET_TOOL, f"Read Coinbase's prices of {args.product or 'the allowed products'}")

    async def run(args: MarketArgs, ctx: ToolContext) -> ToolResult:
        config: TradingConfig = _config(ctx)
        trading: Trading = _trading(ctx)
        if args.product is not None and args.product not in config.products:
            return fail(f"{args.product} is not among the allowed products: {', '.join(config.products)}")
        if args.product is None or args.granularity is None:
            products: list[str] = [args.product] if args.product else list(config.products)
            quotes: list[str] = []
            for product in products:
                quotes.append(_quote_line(await trading.market.quote(product), config.quote))
            return ok("Best prices now (a buy pays the ask, a sell gets the bid):\n" + "\n".join(quotes))
        candles: tuple[Candle, ...] = await trading.market.candles(args.product, args.granularity, args.count)
        quote: Quote = await trading.market.quote(args.product)
        return ok(
            f"{_quote_line(quote, config.quote)}\n{len(candles)} candles of {args.granularity}:\n"
            + _candle_lines(candles)
        )

    return make_tool(
        name=MARKET_TOOL,
        description=(
            "Coinbase's prices of the products you may trade: the best bid and ask now, or with a product and a "
            f"granularity ({', '.join(granularities())}) its candles. Read only."
        ),
        args_model=MarketArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class NoArgs(ToolArgs):
    pass


def _limits_text(config: TradingConfig, portfolio: Portfolio) -> str:
    quote: str = config.quote
    left: float = max(config.max_day - portfolio.day_traded, 0.0)
    loss_left: float = max(config.max_day_loss + portfolio.day_change, 0.0)
    return (
        f"Limits (the owner's, no run changes them): one order at most {config.max_order:.2f} {quote}; today's orders "
        f"{portfolio.day_traded:.2f} of {config.max_day:.2f} {quote} ({left:.2f} left); a product held at most "
        f"{config.max_position:.2f} {quote}; buys stop for the day after a loss of {config.max_day_loss:.2f} {quote} "
        f"({loss_left:.2f} before that). Allowed products: {', '.join(config.products)}."
    )


def _portfolio_tool() -> RegisteredTool:
    def classify(args: NoArgs, ctx: ToolContext) -> ActionInfo:
        return _read(PORTFOLIO_TOOL, "Read the trading account")

    async def run(args: NoArgs, ctx: ToolContext) -> ToolResult:
        config: TradingConfig = _config(ctx)
        portfolio: Portfolio = await _trading(ctx).portfolio()
        account: str = (
            "the paper account (money that is not there, at Coinbase's real prices)"
            if portfolio.mode == "paper"
            else "the owner's Coinbase account (real money)"
        )
        return ok(
            f"Account: {account}. Worth {portfolio.equity:.2f} {config.quote} at the bids; today "
            f"{portfolio.day_change:+.2f} {config.quote} since {portfolio.day_start_equity:.2f}.\n"
            f"Holdings: {holdings_text(portfolio.holdings, config.quote)}.\n{_limits_text(config, portfolio)}"
        )

    return make_tool(
        name=PORTFOLIO_TOOL,
        description=(
            "The trading account: paper or the owner's Coinbase, what it holds and is worth, today's change, and the "
            "limits left today. Read only."
        ),
        args_model=NoArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class OrderArgs(ToolArgs):
    product: str = Field(description="A product of the allowed list, as BTC-EUR.")
    side: Literal["buy", "sell"]
    quote_amount: float | None = Field(
        default=None, gt=0, description="A buy: how much of the quote currency to spend. A sell: what it should bring."
    )
    base_amount: float | None = Field(default=None, gt=0, description="A sell: how much of the base to sell.")

    @model_validator(mode="after")
    def _one_amount(self) -> "OrderArgs":
        if (self.quote_amount is None) == (self.base_amount is None):
            raise ValueError("give quote_amount or base_amount, one of them")
        if self.side == "buy" and self.quote_amount is None:
            raise ValueError("a buy gives quote_amount, what to spend")
        return self


def _order_text(args: OrderArgs, quote: str) -> str:
    amount: str = (
        f"{args.quote_amount:.2f} {quote}" if args.quote_amount is not None else f"{args.base_amount:.8g} of the base"
    )
    return f"{'Buy' if args.side == 'buy' else 'Sell'} {amount} of {args.product} at the market"


def _order_tool() -> RegisteredTool:
    async def classify(args: OrderArgs, ctx: ToolContext) -> ActionInfo:
        config: TradingConfig = _config(ctx)
        text: str = _order_text(args, config.quote)
        if config.mode == "live":
            return ActionInfo(
                tool=ORDER_TOOL,
                kind="network",
                risk="high",
                summary=f"{text} on your Coinbase account (real money)",
                target=f"trading:live:{args.product}",
                flags=("trade",),
            )
        return ActionInfo(
            tool=ORDER_TOOL,
            kind="network",
            risk="low",
            summary=f"{text} on the paper account (no real money)",
            target=f"trading:{config.mode}:{args.product}",
            flags=(),
        )

    async def run(args: OrderArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        config: TradingConfig = _config(ctx)
        if authorized.target != f"trading:{config.mode}:{args.product}":
            # The owner switched the account while the order waited for its approval: it was judged for the other.
            return fail(
                f"the trading account is now {config.mode}, not the one this order was judged for; nothing was "
                "placed. Look at the portfolio and order again"
            )
        request: OrderRequest = OrderRequest(
            product=args.product, side=args.side, quote_amount=args.quote_amount, base_amount=args.base_amount
        )
        try:
            order: TradingOrderRecord = await _trading(ctx).place(request, ctx.run_id, config.mode)
        except TradingRefused as refused:
            return fail(f"Not placed: {refused.message}. Do not try to get around the limits; they are the owner's.")
        account: str = "paper account" if order.mode == "paper" else "Coinbase account (real money)"
        return ok(
            f"{'Bought' if order.side == 'buy' else 'Sold'} {order.size:.8g} {order.product.split('-')[0]} at "
            f"{order.price:.8g} {config.quote} on the {account}: {order.notional:.2f} {config.quote}, fee "
            f"{order.fee:.2f} {config.quote}. Order {order.id}."
        )

    return make_reviewed_tool(
        name=ORDER_TOOL,
        description=(
            "Place a market order on the trading account (paper or the owner's Coinbase, as the owner set it): a buy "
            "spends quote_amount, a sell sells base_amount or what brings quote_amount. The owner's limits judge it "
            "first and refuse what is past them. No withdrawal or transfer exists."
        ),
        args_model=OrderArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class HistoryArgs(ToolArgs):
    limit: int = Field(default=_HISTORY_DEFAULT, ge=1, le=200, description="How many orders, newest first.")


def _history_tool() -> RegisteredTool:
    def classify(args: HistoryArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=HISTORY_TOOL, kind="read", risk="safe", summary="Read the trading orders", target="trading", flags=()
        )

    async def run(args: HistoryArgs, ctx: ToolContext) -> ToolResult:
        config: TradingConfig = _config(ctx)
        orders: list[TradingOrderRecord] = await _trading(ctx).history(args.limit)
        if not orders:
            return ok("No order yet on this account.")
        lines: list[str] = [
            f"{when(order.ts)} {order.side} {order.product} {order.size:.8g} at {order.price:.8g}: "
            f"{order.notional:.2f} {config.quote}, fee {order.fee:.2f}, {order.status}"
            + (f" ({order.note})" if order.note else "")
            for order in orders
        ]
        return ok(f"{len(orders)} orders, newest first:\n" + "\n".join(lines))

    return make_tool(
        name=HISTORY_TOOL,
        description="The orders of the trading account, newest first: what each bought or sold, at what, its fee.",
        args_model=HistoryArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_trading_tools() -> tuple[RegisteredTool, ...]:
    return (_market_tool(), _portfolio_tool(), _order_tool(), _history_tool())
