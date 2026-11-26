"""Watch's two qualities (item 1837): a phone on a weak link reads the Mac's screen as ``light`` (1280 pixels, 30 frames
a second, a fifth of the bit rate, a key frame every 2 s), a capture of its own beside the page's ``hd`` one, with its
own key. A real daemon in a throw-away home and a phone paired as the app pairs; no screen is captured: the capture is
a stand-in that sends one frame telling the settings it was made with."""

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
from phone_stand_ins import TAILNET_NAME, own_home_of, pair_phone, served

from dotagent.desktop import live

pytestmark = [pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _settings_told(monkeypatch: pytest.MonkeyPatch) -> None:
    """The capture says what it was made with, in one frame, and the screen counts as allowed."""

    async def frames(self: live.LiveScreen) -> AsyncGenerator[bytes]:
        settings: live.LiveSettings = self._settings
        yield json.dumps(
            {
                "max_edge": settings.max_edge,
                "fps": settings.fps,
                "bitrate": settings.bitrate,
                "key": settings.key_every_s,
            }
        ).encode()

    monkeypatch.setattr(live.LiveScreen, "frames", frames)
    monkeypatch.setattr(live.LiveScreen, "check_allowed", lambda self: None)


async def test_a_phone_reads_the_light_quality_and_the_page_still_reads_hd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with served(own_home_of(tmp_path, monkeypatch)) as (client, _port, _runtime):
        _phone, key = await pair_phone(client)
        headers: dict[str, str] = {**key, "Host": TAILNET_NAME}
        light: httpx.Response = await client.get("/screen/live", params={"quality": "light"}, headers=headers)
        assert light.status_code == 200, light.text
        assert light.headers["content-type"] == "application/octet-stream"
        assert json.loads(light.content) == {"max_edge": 1280, "fps": 30, "bitrate": 1_500_000, "key": 2}
        hd: httpx.Response = await client.get("/screen/live", headers=headers)
        assert json.loads(hd.content) == {"max_edge": 1920, "fps": 60, "bitrate": 8_000_000, "key": 4}
        unknown: httpx.Response = await client.get("/screen/live", params={"quality": "4k"}, headers=headers)
        assert unknown.status_code == 422, unknown.text
