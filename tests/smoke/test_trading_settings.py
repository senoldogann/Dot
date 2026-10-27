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

from dotagent.runtime import Runtime

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
