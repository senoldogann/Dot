"""``POST /api/phone/activity`` (item 1420), on a served daemon: the iPhone app says it is in the foreground and which
chat it shows, the daemon keeps a lease of it, and a phone that is removed takes its lease along. Only a phone's own key
asks. The leases' clock is the test's own. No network and no model service is used."""

from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, own_home_of, pair_phone, served

from dotagent.notify.leases import PhoneLeases

pytestmark = pytest.mark.asyncio

_NOW: float = 1_800_000_000.0


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


async def test_the_app_says_it_shows_dot_and_the_daemon_holds_a_lease_that_ends_by_its_time_or_by_background(
    own_home: Path,
) -> None:
    clock: list[float] = [_NOW]
    leases = PhoneLeases(lambda: clock[0])
    async with served(own_home, leases) as (client, _, _):
        phone_id, phone = await pair_phone(client)
        # The Mac's own token is no phone: an activity lease belongs to the phone that signed it.
        refused: httpx.Response = await client.post("/phone/activity", json={"state": "foreground"}, headers=MAC)
        assert refused.status_code == 403, refused.text
        assert leases.live(phone_id) is None

        # No time named: 45 seconds, and no chat.
        said: httpx.Response = await client.post("/phone/activity", json={"state": "foreground"}, headers=phone)
        assert said.status_code == 200, said.text
        assert said.json() == {"state": "foreground", "chat": None, "ttl_s": 45.0, "expires_at": _NOW + 45.0}
        held = leases.live(phone_id)
        assert held is not None and held.chat is None

        # The chat on screen and a longer time, which is capped at 90 seconds; said again, the lease moves.
        clock[0] = _NOW + 25
        again: httpx.Response = await client.post(
            "/phone/activity", json={"state": "foreground", "chat": "thr_abc_123456", "ttl_s": 300}, headers=phone
        )
        assert again.json() == {
            "state": "foreground",
            "chat": "thr_abc_123456",
            "ttl_s": 90.0,
            "expires_at": _NOW + 25 + 90.0,
        }
        held = leases.live(phone_id)
        assert held is not None and held.chat == "thr_abc_123456" and held.expires_at == _NOW + 115.0
        clock[0] = _NOW + 114.9
        assert leases.live(phone_id) is not None
        clock[0] = _NOW + 115.0
        assert leases.live(phone_id) is None

        # Background ends the lease at once, and says so.
        clock[0] = _NOW + 200
        await client.post("/phone/activity", json={"state": "foreground", "ttl_s": 30}, headers=phone)
        assert leases.live(phone_id) is not None
        left: httpx.Response = await client.post("/phone/activity", json={"state": "background"}, headers=phone)
        assert left.status_code == 200
        assert left.json() == {"state": "background", "chat": None, "ttl_s": 0.0, "expires_at": None}
        assert leases.live(phone_id) is None


async def test_a_call_that_is_not_the_shape_is_refused_and_leaves_the_lease_as_it_was(own_home: Path) -> None:
    clock: list[float] = [_NOW]
    leases = PhoneLeases(lambda: clock[0])
    async with served(own_home, leases) as (client, _, _):
        phone_id, phone = await pair_phone(client)
        await client.post("/phone/activity", json={"state": "foreground", "chat": "thr_one"}, headers=phone)
        wrong: list[dict[str, object]] = [
            {},
            {"state": "inactive"},
            {"state": "foreground", "ttl_s": 0},
            {"state": "foreground", "ttl_s": -5},
            {"state": "foreground", "chat": ""},
            {"state": "foreground", "chat": "x" * 65},
            {"state": "foreground", "visible": True},
        ]
        for body in wrong:
            refused: httpx.Response = await client.post("/phone/activity", json=body, headers=phone)
            assert refused.status_code == 422, (body, refused.text)
        held = leases.live(phone_id)
        assert held is not None and held.chat == "thr_one"


async def test_a_removed_phone_takes_its_lease_and_another_phone_keeps_its_own(own_home: Path) -> None:
    leases = PhoneLeases(lambda: _NOW)
    async with served(own_home, leases) as (client, _, _):
        first_id, first = await pair_phone(client)
        second_id, second = await pair_phone(client)
        for phone in (first, second):
            assert (
                await client.post("/phone/activity", json={"state": "foreground"}, headers=phone)
            ).status_code == 200
        assert leases.live(first_id) is not None and leases.live(second_id) is not None
        assert (await client.delete(f"/phone/phones/{first_id}", headers=MAC)).status_code == 204
        assert leases.live(first_id) is None and leases.live(second_id) is not None
        # Its key is refused from then on, so it cannot make a lease again.
        gone: httpx.Response = await client.post("/phone/activity", json={"state": "foreground"}, headers=first)
        assert gone.status_code == 401
        assert leases.live(first_id) is None
