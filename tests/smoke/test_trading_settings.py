"""Settings › Trading on a served daemon (docs/trading.md): what lets runs do more with money (trading switched on,
a limit raised, a product allowed) answers the Mac's token only, and what narrows it answers a phone's key too, so that
the owner can always stop it from away; each change is kept in the config file and holds from the next order. The
Keychain is a stand-in, and the account is never read from Coinbase: no network and no model service is used."""

import tomllib
from pathlib import Path

import httpx
import pytest
from test_phone_limits import TOKEN, _phone_key, _served, own_home  # noqa: F401 - the fixture is used by name
from vault_stand_ins import MemorySecretStore

from dotagent.core.errors import StoreError
from dotagent.runtime import Runtime
from dotagent.trading.market import CoinbaseMarket, MarketError
from dotagent.trading.types import OrderRequest
from dotagent.trading.watch import WAKES_KEY

pytestmark = pytest.mark.asyncio


async def test_a_phone_narrows_trading_and_only_the_mac_widens_it(own_home: Path) -> None:  # noqa: F811
    async with _served(own_home) as (client, _, runtime):
        typed: Runtime = runtime
        typed.trading.key_store = MemorySecretStore()
        key: str = await _phone_key(client)
        phone: dict[str, str] = {"Authorization": f"Bearer {key}"}
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        shown: httpx.Response = await client.get("/trading", headers=phone)
        assert shown.status_code == 200, shown.text
        assert shown.json()["settings"]["mode"] == "off" and shown.json()["tools_on"] is False
        assert shown.json()["key"] == {"kept": False, "name_end": ""}
        for change in (
            {"mode": "paper"},
            {"max_order": 50.0},
            {"products": ["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR"]},
        ):
            refused: httpx.Response = await client.patch("/trading", json=change, headers=phone)
            assert refused.status_code == 403 and "a phone cannot" in refused.json()["message"], (change, refused.text)
        assert typed.trading.config().mode == "off"
        switched: httpx.Response = await client.patch(
            "/trading", json={"mode": "paper", "max_order": 20.0}, headers=mac
        )
        assert switched.status_code == 200, switched.text
        assert typed.trading.config().mode == "paper" and typed.trading.config().max_order == 20.0
        # Switched on after the start, runs get the trading tools once Dot starts again; the page says so.
        assert switched.json()["tools_on"] is False
        # Narrowing from the phone: a lower limit, a product dropped, trading off.
        for change in ({"max_day": 50.0}, {"products": ["BTC-EUR"]}, {"mode": "off"}):
            narrowed: httpx.Response = await client.patch("/trading", json=change, headers=phone)
            assert narrowed.status_code == 200, (change, narrowed.text)
        kept: dict[str, object] = tomllib.loads((own_home / "config.toml").read_text(encoding="utf-8"))["trading"]
        assert kept == {"mode": "off", "max_order": 20.0, "max_day": 50.0, "products": ["BTC-EUR"]}
        # A change the limits refuse together changes nothing.
        wrong: httpx.Response = await client.patch("/trading", json={"max_order": 60.0}, headers=mac)
        assert wrong.status_code == 422 and "max_order is more than max_day" in wrong.json()["message"]
        assert typed.trading.config().max_order == 20.0
        # The key is saved on the Mac alone; removing it is open to the phone.
        saved: httpx.Response = await client.put(
            "/trading/key",
            json={"name": "organizations/o/apiKeys/k", "private_key": "x" * 120},
            headers=phone,
        )
        assert saved.status_code == 403
        assert (await client.delete("/trading/key", headers=phone)).status_code == 200


async def test_a_phone_cannot_open_the_whole_market_nor_lower_its_floor(own_home: Path) -> None:  # noqa: F811
    """Item 771: "*" among the products, or a lower min_volume, lets runs trade more products: the Mac's alone, even
    while "*" is not allowed, as the Mac's "*" would take the lower floor up unseen."""
    async with _served(own_home) as (client, _, runtime):
        typed: Runtime = runtime
        typed.trading.key_store = MemorySecretStore()
        key: str = await _phone_key(client)
        phone: dict[str, str] = {"Authorization": f"Bearer {key}"}
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        refused: httpx.Response = await client.patch("/trading", json={"products": ["*"]}, headers=phone)
        assert refused.status_code == 403 and "allow another product" in refused.json()["message"]
        floor: httpx.Response = await client.patch("/trading", json={"min_volume": 0.0}, headers=phone)
        assert floor.status_code == 403 and "products that trade less" in floor.json()["message"]
        opened: httpx.Response = await client.patch(
            "/trading", json={"products": ["*"], "min_volume": 2_000_000.0}, headers=mac
        )
        assert opened.status_code == 200, opened.text
        lower: httpx.Response = await client.patch("/trading", json={"min_volume": 500_000.0}, headers=phone)
        assert lower.status_code == 403 and "products that trade less" in lower.json()["message"]
        higher: httpx.Response = await client.patch("/trading", json={"min_volume": 5_000_000.0}, headers=phone)
        assert higher.status_code == 200, higher.text
        assert typed.trading.config().min_volume == 5_000_000.0


_PRICES: dict[str, tuple[str, str, str]] = {
    "ZEC-USDC": ("1300.00", "1300.50", "1300.20"),
    "BTC-USDC": ("80000.00", "80010.00", "80005.00"),
    "THIN-USDC": ("1.00", "1.01", "1.00"),
}
_VOLUMES: dict[str, str] = {"ZEC-USDC": "85000000", "BTC-USDC": "950000000", "THIN-USDC": "20000"}


def _market(request: httpx.Request) -> httpx.Response:
    """A stand-in of Coinbase's market: its product list, each product's ticker and its rules."""
    path: str = request.url.path
    if path.endswith("/market/products"):
        listed: list[dict[str, object]] = [
            {
                "product_id": product, "quote_currency_id": "USDC", "status": "online", "trading_disabled": False,
                "is_disabled": False, "cancel_only": False, "limit_only": False, "price": last, "best_bid_price": bid,
                "best_ask_price": ask, "approximate_quote_24h_volume": _VOLUMES[product],
            }
            for product, (bid, ask, last) in _PRICES.items()
        ]  # fmt: skip
        return httpx.Response(200, json={"products": listed})
    product: str = path.split("/products/")[1].split("/")[0]
    bid, ask, last = _PRICES[product]
    if path.endswith("/ticker"):
        return httpx.Response(200, json={"trades": [{"price": last}], "best_bid": bid, "best_ask": ask})
    return httpx.Response(200, json={
        "product_id": product, "status": "online", "trading_disabled": False, "is_disabled": False,
        "cancel_only": False, "limit_only": False, "base_increment": "0.00000001", "quote_increment": "0.01",
        "quote_min_size": "1", "base_min_size": "0.00000001", "approximate_quote_24h_volume": _VOLUMES[product],
    })  # fmt: skip


async def test_the_desk_shows_each_open_position_and_what_watches_the_market(own_home: Path) -> None:  # noqa: F811
    """Item 777: Settings › Trading is a trading desk. Each open position comes with its buy's price and the bid now,
    and the watch with the schedule a move of the market starts (or that its id names none), the day's wakes and the
    products a run may trade at this moment."""
    async with _served(own_home) as (client, _, runtime):
        typed: Runtime = runtime
        typed.trading.key_store = MemorySecretStore()
        typed.trading.market = CoinbaseMarket(5.0, httpx.MockTransport(_market))
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        made: httpx.Response = await client.post(
            "/schedules", json={"name": "Paper shift", "goal": "Trade.", "cron": "15 * * * *"}, headers=mac
        )
        assert made.status_code == 201, made.text
        shift: str = made.json()["record"]["id"]
        typed.trading.settings.value = typed.trading.config().model_copy(
            update={"mode": "paper", "quote": "USDC", "products": ["*"], "wake_schedule": shift}
        )
        await typed.trading.place(
            OrderRequest(product="ZEC-USDC", side="buy", quote_amount=20.0, base_amount=None, reason="x" * 20,
                         stop=1200.0, target=1400.0),
            "run_a",
            "paper",
        )  # fmt: skip
        bought: tuple[str, str, str] = _PRICES["ZEC-USDC"]
        _PRICES["ZEC-USDC"] = ("1310.00", "1310.50", "1310.20")
        try:
            await typed.store.kv_set(f"{WAKES_KEY}:{typed.trading.day_start()[0]}", "2")
            shown: dict[str, object] = (await client.get("/trading", headers=mac)).json()
        finally:
            _PRICES["ZEC-USDC"] = bought
        positions = shown["positions"]
        assert isinstance(positions, list) and len(positions) == 1
        (position,) = positions
        assert (position["product"], position["entry"], position["stop"], position["target"]) == (
            "ZEC-USDC", 1300.5, 1200.0, 1400.0,
        )  # fmt: skip
        assert position["price"] == pytest.approx(1310.0)
        watch = shown["watch"]
        assert isinstance(watch, dict)
        assert (watch["wake_schedule"], watch["schedule_name"], watch["schedule_on"]) == (shift, "Paper shift", True)
        # ZEC and BTC trade past the floor; THIN does not.
        assert (watch["wakes_today"], watch["last_wake_at"], watch["products_now"]) == (2, None, 2)
        assert watch["next_shift_at"] is not None
        # A wake schedule that names no schedule is said so, rather than waking nothing in silence.
        typed.trading.settings.value = typed.trading.config().model_copy(update={"wake_schedule": "sch_gone"})
        gone = (await client.get("/trading", headers=mac)).json()["watch"]
        assert (gone["wake_schedule"], gone["schedule_name"], gone["schedule_on"]) == ("sch_gone", None, False)
        # What watches the market cannot be read (the store fails): the page is drawn all the same, and says why.
        failing = typed.store.get_schedule

        async def broken(schedule_id: str) -> object:
            raise StoreError("the database is locked", {})

        typed.store.get_schedule = broken  # type: ignore[method-assign]
        typed.trading.settings.value = typed.trading.config().model_copy(update={"wake_schedule": shift})
        try:
            unread: httpx.Response = await client.get("/trading", headers=mac)
        finally:
            typed.store.get_schedule = failing  # type: ignore[method-assign]
        assert unread.status_code == 200 and unread.json()["watch"] is None
        assert unread.json()["problem"] == "What watches the market could not be read: the database is locked"
        # Neither the account nor what watches the market can be read: the page says both reasons, not the first.
        reading = typed.trading.portfolio

        async def unreachable() -> object:
            raise MarketError("Coinbase did not answer.", {})

        typed.store.get_schedule = broken  # type: ignore[method-assign]
        typed.trading.portfolio = unreachable  # type: ignore[method-assign]
        try:
            neither: dict[str, object] = (await client.get("/trading", headers=mac)).json()
        finally:
            typed.store.get_schedule = failing  # type: ignore[method-assign]
            typed.trading.portfolio = reading  # type: ignore[method-assign]
        assert (neither["portfolio"], neither["watch"]) == (None, None)
        assert neither["problem"] == (
            "Coinbase did not answer. What watches the market could not be read: the database is locked"
        )
        # Off, nothing is watched.
        typed.trading.settings.value = typed.trading.config().model_copy(update={"mode": "off"})
        off: dict[str, object] = (await client.get("/trading", headers=mac)).json()
        assert (off["watch"], off["positions"]) == (None, [])
