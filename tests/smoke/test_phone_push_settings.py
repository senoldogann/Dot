"""``GET`` and ``PUT /api/phone/push-settings`` (item 1421), on a served daemon: each phone says which of the three
kinds of push (``needs_you``, ``ready``, ``done``) it is told; all on until it says otherwise; only a phone's own key
asks, a phone's switches are its own, and a removed phone takes them along. No network and no model service is used."""

from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, own_home_of, pair_phone, served

from dotagent.notify.settings import push_settings_of

pytestmark = pytest.mark.asyncio

_ALL_ON: dict[str, bool] = {"needs_you": True, "ready": True, "done": True}


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


async def test_a_phone_reads_and_sets_the_three_kinds_of_push_it_is_told(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        phone_id, phone = await pair_phone(client)
        other_id, other = await pair_phone(client)
        # The Mac's own token is no phone: the switches belong to the phone that asks.
        for refused in (
            await client.get("/phone/push-settings", headers=MAC),
            await client.put("/phone/push-settings", json=_ALL_ON, headers=MAC),
        ):
            assert refused.status_code == 403, refused.text

        # Nothing said yet: all on.
        first: httpx.Response = await client.get("/phone/push-settings", headers=phone)
        assert first.status_code == 200 and first.json() == _ALL_ON

        # The phone switches two kinds off; the daemon answers what it kept, and says it again on a read.
        quiet: dict[str, bool] = {"needs_you": True, "ready": False, "done": False}
        put: httpx.Response = await client.put("/phone/push-settings", json=quiet, headers=phone)
        assert put.status_code == 200 and put.json() == quiet
        assert (await client.get("/phone/push-settings", headers=phone)).json() == quiet
        kept = await push_settings_of(runtime.store, phone_id)
        assert kept.model_dump() == quiet
        # Another phone's switches are its own.
        assert (await client.get("/phone/push-settings", headers=other)).json() == _ALL_ON
        assert (await push_settings_of(runtime.store, other_id)).model_dump() == _ALL_ON

        # Said again whole, a switch can come back on.
        back: httpx.Response = await client.put("/phone/push-settings", json=_ALL_ON, headers=phone)
        assert back.json() == _ALL_ON


async def test_a_body_that_is_not_the_three_booleans_is_refused_and_leaves_the_switches_as_they_were(
    own_home: Path,
) -> None:
    async with served(own_home) as (client, _, _):
        _, phone = await pair_phone(client)
        quiet: dict[str, bool] = {"needs_you": False, "ready": True, "done": True}
        assert (await client.put("/phone/push-settings", json=quiet, headers=phone)).status_code == 200
        wrong: list[dict[str, object]] = [
            {},
            {"done": False},
            {"needs_you": True, "ready": True},
            {"needs_you": "true", "ready": True, "done": True},
            {"needs_you": 1, "ready": True, "done": True},
            {"needs_you": True, "ready": True, "done": None},
            {"needs_you": True, "ready": True, "done": True, "badge": True},
        ]
        for body in wrong:
            refused: httpx.Response = await client.put("/phone/push-settings", json=body, headers=phone)
            assert refused.status_code == 422, (body, refused.text)
        assert (await client.get("/phone/push-settings", headers=phone)).json() == quiet


async def test_a_removed_phone_takes_its_switches_and_a_phone_paired_after_starts_with_all_on(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        phone_id, phone = await pair_phone(client)
        quiet: dict[str, bool] = {"needs_you": False, "ready": False, "done": False}
        assert (await client.put("/phone/push-settings", json=quiet, headers=phone)).status_code == 200
        assert await runtime.store.kv_get(f"phone.push.{phone_id}") is not None
        assert (await client.delete(f"/phone/phones/{phone_id}", headers=MAC)).status_code == 204
        assert await runtime.store.kv_get(f"phone.push.{phone_id}") is None
        # Its key is refused from then on.
        assert (await client.get("/phone/push-settings", headers=phone)).status_code == 401
        _, again = await pair_phone(client)
        assert (await client.get("/phone/push-settings", headers=again)).json() == _ALL_ON
