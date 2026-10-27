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

from dotagent.config import TradingConfig, TradingMode
from dotagent.config_edit import TradingSetting, set_setting
from dotagent.core.errors import ConfigError, InvalidValueError
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel
from dotagent.server.context import ServerContext
from dotagent.server.guard import refuse_from_a_phone
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.coinbase import KEY_ACCOUNT, CoinbaseBroker, KeyPermissions, kept_key, load_key
from dotagent.trading.service import Trading
from dotagent.trading.types import Portfolio
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


class TradingView(FrozenModel):
    """Settings › Trading: the settings in force, whether runs have the trading tools now (``tools_on``: a switch from
    off takes Dot's restart), the key, the account of the mode and its orders. ``problem`` says why the account could
    not be read."""

    settings: TradingConfig
    tools_on: bool
    key: TradingKeyView
    portfolio: Portfolio | None
    orders: list[TradingOrderRecord]
    problem: str


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
        if settings.mode != "off" and (settings.mode == "paper" or kept is not None):
            try:
                portfolio = await trading.portfolio()
                orders = await trading.history(_ORDERS_SHOWN)
            except Exception as error:  # noqa: BLE001 - the page shows why the account could not be read
                problem = str(getattr(error, "message", error))[:300]
        return TradingView(
            settings=settings,
            tools_on="trading_order" in ctx.runtime.registry.tools,
            key=TradingKeyView(kept=kept is not None, name_end=kept[0][-6:] if kept is not None else ""),
            portfolio=portfolio,
            orders=orders,
            problem=problem,
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
