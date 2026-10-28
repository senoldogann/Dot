"""Settings › Trading (docs/trading.md): the mode and the limits of ``[trading]``, the key of the owner's Coinbase
account, and what the account holds and did.

What widens what runs may do with money is done on the Mac only, with the installation's token (item 678's rule for a
phone's key): switching to live, raising a limit, allowing a product, saving a key. What narrows it is done from a phone
too, so that the owner can always stop it from away: switching trading off or to paper, lowering a limit, dropping a
product, removing the key, starting the paper account again. A change holds from the next order and is kept in the
config file; a config file that cannot be changed leaves every setting as it was.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr, ValidationError

from dotagent.config import ANY_PRODUCT, TradingConfig, TradingMode
from dotagent.config_edit import TradingSetting, set_setting
from dotagent.core.errors import ConfigError, InvalidValueError, NotFoundError
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel
from dotagent.server.context import ServerContext
from dotagent.server.guard import refuse_from_a_phone
from dotagent.store.models import ScheduleRecord, TradingExitRecord, TradingOrderRecord
from dotagent.trading.coinbase import KEY_ACCOUNT, CoinbaseBroker, KeyPermissions, kept_key, load_key
from dotagent.trading.limits import market_allows
from dotagent.trading.market import MarketError
from dotagent.trading.service import Trading
from dotagent.trading.types import Holding, Portfolio
from dotagent.trading.watch import LAST_WAKE_KEY, WAKES_KEY
from dotagent.vault.secretstore import SecretStore

_ORDERS_SHOWN: int = 50
_LIMITS: tuple[Literal["max_order", "max_day", "max_day_loss", "max_position"], ...] = (
    "max_order",
    "max_day",
    "max_day_loss",
    "max_position",
)
_RANK: dict[str, int] = {"off": 0, "paper": 1, "live": 2}


class TradingKeyView(FrozenModel):
    """Whether a Coinbase key is kept, and the end of its name to tell it by; never the key."""

    kept: bool
    name_end: str


class TradingPosition(FrozenModel):
    """An open exit as the page shows a position (item 777): what its buy bought (``size`` of ``product``), at what
    price (``entry``, the buy's fill; None when the buy is not kept), the bid now (``price``, from what the account's
    holding is worth; None when it could not be priced), and where Dot sells it."""

    exit_id: str
    product: str
    size: float
    entry: float | None
    price: float | None
    stop: float
    target: float | None
    ts: float


class TradingWatchView(FrozenModel):
    """What watches the market for the account (item 777): the schedule a move of the market starts (``""``: none;
    ``schedule_name`` None: the id names no schedule), whether it is on and when it fires next, the runs it started
    today and when the last one did, and how many products a run may trade now (None when Coinbase's list could not be
    read)."""

    wake_schedule: str
    schedule_name: str | None
    schedule_on: bool
    next_shift_at: float | None
    wakes_today: int
    last_wake_at: float | None
    products_now: int | None


class TradingView(FrozenModel):
    """Settings › Trading: the settings in force, whether runs have the trading tools now (``tools_on``: a switch from
    off takes Dot's restart), the key, the account of the mode, its open positions and its orders, and what watches the
    market (item 777). ``problem`` says why the account, what watches the market, or both could not be read: the page
    is drawn all the same, with the switch that turns trading off."""

    settings: TradingConfig
    tools_on: bool
    key: TradingKeyView
    portfolio: Portfolio | None
    positions: list[TradingPosition]
    watch: TradingWatchView | None
    orders: list[TradingOrderRecord]
    problem: str
    # What the paper account started with at its last start (item 785), which "since the start" is counted from; None
    # out of paper.
    paper_started_with: float | None = None


class TradingChange(FrozenModel):
    """A change of ``[trading]``: each field given is set, the others stay."""

    mode: TradingMode | None = None
    products: list[str] | None = Field(default=None, min_length=1, max_length=50)
    min_volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_order: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_day: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_day_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_position: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    paper_start: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class TradingKeyRequest(FrozenModel):
    name: str = Field(min_length=10, max_length=300, pattern=r"^organizations/[^/\s]+/apiKeys/[^/\s]+$")
    private_key: SecretStr = Field(min_length=100, max_length=2000)


def widens(before: TradingConfig, after: TradingConfig) -> list[str]:
    """What of ``after`` lets runs do more with money than ``before`` did: what a phone may not change."""
    wider: list[str] = []
    if _RANK[after.mode] > _RANK[before.mode]:
        wider.append(f"switch trading to {after.mode}")
    wider.extend(f"raise {limit}" for limit in _LIMITS if getattr(after, limit) > getattr(before, limit))
    if set(after.products) - set(before.products):
        wider.append("allow another product")
    # Lower even while "*" is not allowed: the Mac's "*" would take the lower floor up unseen (the pre-push review).
    if after.min_volume < before.min_volume:
        wider.append("allow products that trade less")
    if after.paper_start > before.paper_start:
        wider.append("raise what the paper account starts with")
    return wider


def trading_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    trading: Trading = ctx.runtime.trading
    changing: asyncio.Lock = asyncio.Lock()

    def store_of_keys() -> SecretStore:
        if trading.key_store is None:
            raise ConfigError("this installation keeps no trading key", {})
        return trading.key_store

    async def view() -> TradingView:
        settings: TradingConfig = trading.config()
        kept: tuple[str, SecretStr] | None = (
            await kept_key(trading.key_store) if trading.key_store is not None else None
        )
        portfolio: Portfolio | None = None
        problem: str = ""
        orders: list[TradingOrderRecord] = []
        positions: list[TradingPosition] = []
        watch: TradingWatchView | None = None
        if settings.mode != "off" and (settings.mode == "paper" or kept is not None):
            try:
                portfolio = await trading.portfolio()
                orders = await trading.history(_ORDERS_SHOWN)
                positions = await positions_of(portfolio)
            except Exception as error:  # noqa: BLE001 - the page shows why the account could not be read
                problem = str(getattr(error, "message", error))[:300]
        if settings.mode != "off":
            try:
                watch = await watch_of(settings)
            except Exception as error:  # noqa: BLE001 - the page is drawn all the same, with the switch to turn it off
                said: str = f"What watches the market could not be read: {getattr(error, 'message', error)}"[:300]
                problem = f"{problem.rstrip('.')}. {said}" if problem else said
        return TradingView(
            settings=settings,
            tools_on="trading_order" in ctx.runtime.registry.tools,
            key=TradingKeyView(kept=kept is not None, name_end=kept[0][-6:] if kept is not None else ""),
            portfolio=portfolio,
            positions=positions,
            watch=watch,
            orders=orders,
            problem=problem,
            paper_started_with=await trading.paper.started_with() if settings.mode == "paper" else None,
        )

    async def positions_of(portfolio: Portfolio) -> list[TradingPosition]:
        """The open exits of the account, each with its buy's price and the bid now (item 777)."""
        bids: dict[str, float] = {
            holding.currency: holding.value / holding.amount for holding in portfolio.holdings if _priced(holding)
        }
        found: list[TradingPosition] = []
        for exit_ in await trading.open_exits():
            buy: TradingOrderRecord | None = await trading.store.get_trading_order(exit_.order_id)
            found.append(_position(exit_, buy, bids.get(exit_.product.split("-")[0])))
        return found

    async def watch_of(settings: TradingConfig) -> TradingWatchView:
        """What watches the market now (item 777): the wake schedule as the store has it, the day's wakes as the watch
        counted them, and the products a run may trade at this moment."""
        name: str | None = None
        on: bool = False
        next_at: float | None = None
        if settings.wake_schedule:
            try:
                record: ScheduleRecord = await ctx.runtime.store.get_schedule(settings.wake_schedule)
                name, on, next_at = record.name, record.enabled, record.next_run_at
            except NotFoundError:
                name = None
        day: str = trading.day_start()[0]
        woken: str | None = await ctx.runtime.store.kv_get(f"{WAKES_KEY}:{day}")
        last: str | None = await ctx.runtime.store.kv_get(LAST_WAKE_KEY)
        products: int | None = len([product for product in settings.products if product != ANY_PRODUCT])
        if ANY_PRODUCT in settings.products:
            try:
                rows = await trading.market.whole_market(settings.quote)
                products = len([row for row in rows if market_allows(settings, row)])
            except MarketError:
                products = None
        return TradingWatchView(
            wake_schedule=settings.wake_schedule,
            schedule_name=name,
            schedule_on=on,
            next_shift_at=next_at,
            wakes_today=int(woken) if woken is not None and woken.isdigit() else 0,
            last_wake_at=_number(last),
            products_now=products,
        )

    @router.get("/trading")
    async def trading_state() -> TradingView:
        return await view()

    @router.patch("/trading")
    async def change_trading(change: TradingChange, request: Request) -> TradingView:
        async with changing:
            before: TradingConfig = trading.config()
            given: dict[str, object] = change.model_dump(exclude_none=True)
            try:
                after: TradingConfig = TradingConfig.model_validate({**before.model_dump(), **given})
            except ValidationError as error:
                said: str = "; ".join(str(problem["msg"]).removeprefix("Value error, ") for problem in error.errors())
                raise InvalidValueError(said[:300], {}) from error
            wider: list[str] = widens(before, after)
            if wider:
                refuse_from_a_phone(request.scope, ", ".join(wider))
            if after.mode == "live" and before.mode != "live":
                broker: object = trading.live()
                if not isinstance(broker, CoinbaseBroker):
                    raise ConfigError("live trading has no Coinbase broker in this installation", {})
                await broker.check_key()
            written: list[tuple[TradingSetting, object]] = []
            try:
                for key, value in given.items():
                    setting: TradingSetting = key  # type: ignore[assignment]
                    await asyncio.to_thread(set_setting, config_path(), "trading", setting, value)  # type: ignore[arg-type]
                    written.append((setting, getattr(before, key)))
            except (ConfigError, OSError):
                for setting, old in reversed(written):
                    await asyncio.to_thread(set_setting, config_path(), "trading", setting, old)  # type: ignore[arg-type]
                raise
            trading.settings.value = after
        return await view()

    @router.put("/trading/key")
    async def save_key(key: TradingKeyRequest, request: Request) -> TradingView:
        """Keep the Coinbase key in the login Keychain once Coinbase says it may view and trade and may not transfer;
        one it does not take is not kept."""
        refuse_from_a_phone(request.scope, "save a trading key")
        load_key(key.private_key.get_secret_value())
        keys: SecretStore = store_of_keys()
        async with changing:
            before: dict[str, SecretStr] | None = await keys.get(KEY_ACCOUNT)
            await keys.put(KEY_ACCOUNT, {"name": SecretStr(key.name), "private_key": key.private_key})
            try:
                broker: object = trading.live()
                if isinstance(broker, CoinbaseBroker):
                    allowed: KeyPermissions = await broker.check_key()
                    del allowed
            except Exception:
                if before is None:
                    await keys.remove(KEY_ACCOUNT)
                else:
                    await keys.put(KEY_ACCOUNT, before)
                raise
        return await view()

    @router.delete("/trading/key")
    async def remove_key() -> TradingView:
        """Forget the Coinbase key; live trading then places nothing until one is saved again."""
        async with changing:
            await store_of_keys().remove(KEY_ACCOUNT)
        return await view()

    @router.post("/trading/paper/reset")
    async def reset_paper() -> TradingView:
        """Start the paper account again with what it starts with; its orders stay in the record, outside it."""
        await trading.reset_paper()
        return await view()

    return router


def _number(text: str | None) -> float | None:
    """A number the watch kept as text, or None for none or for what is no number."""
    try:
        return None if text is None else float(text)
    except ValueError:
        return None


def _priced(holding: Holding) -> bool:
    return holding.amount > 0 and holding.value > 0


def _position(exit_: TradingExitRecord, buy: TradingOrderRecord | None, bid: float | None) -> TradingPosition:
    return TradingPosition(
        exit_id=exit_.id,
        product=exit_.product,
        size=exit_.size,
        entry=buy.price if buy is not None and buy.price > 0 else None,
        price=bid,
        stop=exit_.stop,
        target=exit_.target,
        ts=exit_.ts,
    )
