"""``POST`` and ``DELETE /api/phone/live-start-token`` (item 1425), on a served daemon: the iPhone app gives the token
that starts its Live Activity by push (iOS 17.2 and later) with its phone's own key, the daemon keeps it for that phone
and no other, and a removed phone takes it along. No network and no model service is used."""

from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, own_home_of, pair_phone, served

from dotagent.notify.live_start import live_start_token_of

pytestmark = pytest.mark.asyncio

_TOKEN: str = "ab" * 40
_NEWER: str = "cd" * 40


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


async def test_the_app_gives_its_push_to_start_token_and_the_daemon_keeps_it_for_that_phone_alone(
    own_home: Path,
) -> None:
    async with served(own_home) as (client, _, runtime):
        phone_id, phone = await pair_phone(client)
        other_id, other = await pair_phone(client)
        # The Mac's own token is no phone: a token belongs to the phone that gives it.
        refused: httpx.Response = await client.post("/phone/live-start-token", json={"token": _TOKEN}, headers=MAC)
        assert refused.status_code == 403, refused.text
        for bad in ({}, {"token": "x"}, {"token": "AB" * 40}, {"token": _TOKEN, "environment": "sandbox"}):
            wrong: httpx.Response = await client.post("/phone/live-start-token", json=bad, headers=phone)
            assert wrong.status_code == 422, (bad, wrong.text)
        assert await live_start_token_of(runtime.store, phone_id) is None

        # No device was registered first: the token does not wait for it, and is kept.
        assert await runtime.store.list_apns_devices() == []
        given: httpx.Response = await client.post("/phone/live-start-token", json={"token": _TOKEN}, headers=phone)
        assert given.status_code == 204 and given.content == b""
        assert await live_start_token_of(runtime.store, phone_id) == _TOKEN
        assert await live_start_token_of(runtime.store, other_id) is None
        # A new token takes the place of the one before; registering the device (item 997) does not erase it.
        assert (await client.post("/phone/live-start-token", json={"token": _NEWER}, headers=phone)).status_code == 204
        device: dict[str, object] = {"device_token": "12" * 32, "start_token": None, "environment": "sandbox"}
        assert (await client.post("/phone/apns", json=device, headers=phone)).status_code == 204
        assert await live_start_token_of(runtime.store, phone_id) == _NEWER
        # The app that switched Live Activities off says it has none.
        assert (await client.delete("/phone/live-start-token", headers=MAC)).status_code == 403
        assert (await client.delete("/phone/live-start-token", headers=phone)).status_code == 204
        assert await live_start_token_of(runtime.store, phone_id) is None
        # Another phone's token is its own.
        assert (await client.post("/phone/live-start-token", json={"token": _TOKEN}, headers=other)).status_code == 204
        assert (await client.delete("/phone/live-start-token", headers=phone)).status_code == 204
        assert await live_start_token_of(runtime.store, other_id) == _TOKEN


async def test_a_removed_phone_takes_its_start_token_and_cannot_give_another(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        phone_id, phone = await pair_phone(client)
        assert (await client.post("/phone/live-start-token", json={"token": _TOKEN}, headers=phone)).status_code == 204
        assert (await client.delete(f"/phone/phones/{phone_id}", headers=MAC)).status_code == 204
        assert await live_start_token_of(runtime.store, phone_id) is None
        gone: httpx.Response = await client.post("/phone/live-start-token", json={"token": _TOKEN}, headers=phone)
        assert gone.status_code == 401
        assert await live_start_token_of(runtime.store, phone_id) is None
