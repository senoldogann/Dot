"""Value types of trading: what the market says, what an account holds, what an order did."""

from typing import Literal

from dotagent.core.types import FrozenModel

type Side = Literal["buy", "sell"]


class Quote(FrozenModel):
    """The best prices of ``product`` at ``ts`` (seconds since the epoch): what a buy pays (``ask``) and a sell gets
    (``bid``), and the last trade's price."""

    product: str
    bid: float
    ask: float
    last: float
    ts: float


class Candle(FrozenModel):
    """One period of a product's trading: its start (seconds since the epoch) and prices, and the base traded."""

    start: float
    low: float
    high: float
    open: float
    close: float
    volume: float


class ProductRules(FrozenModel):
    """What Coinbase takes of an order of a product: the smallest step of its base and quote amounts, the least an
    order may be, and whether it trades now."""

    product: str
    base_increment: float
    quote_increment: float
    quote_min_size: float
    base_min_size: float
    trading: bool


class Holding(FrozenModel):
    """A currency an account holds, and what it is worth in the quote currency at the bid (the quote itself: 1)."""

    currency: str
    amount: float
    value: float


class Portfolio(FrozenModel):
    """An account as the limits read it: its holdings, what they are worth, and the day so far."""

    mode: Literal["paper", "live"]
    quote: str
    holdings: tuple[Holding, ...]
    equity: float
    day_start_equity: float
    day_traded: float

    @property
    def day_change(self) -> float:
        return self.equity - self.day_start_equity


class OrderRequest(FrozenModel):
    """An order a run asks for: a market order of ``product``; a buy names what to spend (``quote_amount``), a sell
    what to sell (``base_amount``) or what it should bring (``quote_amount``)."""

    product: str
    side: Side
    quote_amount: float | None
    base_amount: float | None


class Fill(FrozenModel):
    """What an order did: the base it bought or sold, the average price, the fee and the quote it moved."""

    order_id: str
    product: str
    side: Side
    size: float
    price: float
    fee: float
    ts: float

    @property
    def notional(self) -> float:
        return self.size * self.price
