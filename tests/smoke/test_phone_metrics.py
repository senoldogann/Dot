"""``POST`` and ``GET /api/phone/metrics`` (item 1926), on a served daemon: a phone sends its own performance record (a
day of MetricKit's, a time the app was open) and reads the summary of its last seven days; only a phone's own key
reaches them, a phone's record is its own, a malformed report is refused whole, and a removed phone takes its record
along. No network and no model service is used."""

import time
from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, own_home_of, pair_phone, served

from dotagent.server.phone_metrics import reports_of

pytestmark = pytest.mark.asyncio


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


def _day(ends_at: float) -> dict[str, object]:
    return {
        "source": "metrickit",
        "begins_at": ends_at - 86400.0,
        "ends_at": ends_at,
        "app_version": "1.0 (42)",
        "os_version": "iOS 26.1",
        "foreground_s": 1800.0,
        "cpu_s": 42.5,
        "hang_s": 1.25,
        "launch_ms": 420.0,
        "peak_memory_mb": 180.0,
        "disk_writes_mb": 12.0,
        "cellular_mb": 3.0,
        "wifi_mb": 9.0,
        "crashes": 0,
        "hang_reports": 1,
        "cpu_exceptions": 0,
        "disk_write_exceptions": 0,
    }


def _open(ends_at: float) -> dict[str, object]:
    return {
        "source": "app",
        "begins_at": ends_at - 600.0,
        "ends_at": ends_at,
        "app_version": "1.0 (42)",
        "os_version": "iOS 26.1",
        "open_s": 600.0,
        "bytes_in": 2 * 1024 * 1024,
        "bytes_out": 1024 * 1024,
        "requests": 30,
        "stream_opens": 4,
        "stream_breaks": 1,
        "redraws": 1200,
    }


async def test_a_phone_sends_its_record_and_reads_its_summary(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        phone_id, phone = await pair_phone(client)
        other_id, other = await pair_phone(client)
        now: float = time.time()
        # The Mac's own token is no phone: the record belongs to the phone that sends it.
        for refused in (
            await client.get("/phone/metrics", headers=MAC),
            await client.post("/phone/metrics", json={"reports": [_open(now)]}, headers=MAC),
        ):
            assert refused.status_code == 403, refused.text

        empty: httpx.Response = await client.get("/phone/metrics", headers=phone)
        assert empty.status_code == 200, empty.text
        assert empty.json()["reports"] == 0 and empty.json()["last_at"] is None

        # A day of MetricKit's, two times the app was open, and a day older than the summary's seven.
        sent: httpx.Response = await client.post(
            "/phone/metrics",
            json={"reports": [_day(now - 3600), _open(now - 60), _open(now - 30), _day(now - 10 * 86400)]},
            headers=phone,
        )
        assert sent.status_code == 204, sent.text
        summary: dict[str, object] = (await client.get("/phone/metrics", headers=phone)).json()
        assert summary["reports"] == 3, "the day older than seven days is kept but not summed"
        assert summary["foreground_min"] == 30.0 and summary["cpu_s"] == 42.5 and summary["hang_reports"] == 1
        assert summary["launch_ms"] == 420.0 and summary["peak_memory_mb"] == 180.0
        assert summary["open_min"] == 20.0 and summary["dot_in_mb"] == 4.0 and summary["dot_out_mb"] == 2.0
        assert summary["reconnects"] == 2 and summary["redraws_per_min"] == 120.0
        assert summary["app_version"] == "1.0 (42)" and summary["last_at"] == pytest.approx(now - 30)
        assert len(await reports_of(runtime.store, phone_id)) == 4
        # Another phone's record is its own.
        assert (await client.get("/phone/metrics", headers=other)).json()["reports"] == 0
        assert await reports_of(runtime.store, other_id) == []

        # Removed, the phone's record goes with it.
        assert (await client.delete(f"/phone/phones/{phone_id}", headers=MAC)).status_code == 204
        assert await reports_of(runtime.store, phone_id) == []


async def test_a_malformed_report_is_refused_whole(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        phone_id, phone = await pair_phone(client)
        now: float = time.time()
        wrong: list[dict[str, object]] = [
            {"reports": []},
            {"reports": [{**_open(now), "source": "web"}]},
            {"reports": [{**_open(now), "bytes_in": -1}]},
            {"reports": [{**_open(now), "bytes_in": "12"}]},
            {"reports": [{**_open(now), "words": "what a chat said"}]},
            {"reports": [{**_open(now), "app_version": "x" * 41}]},
            {"reports": [_open(now)] * 51},
            {"reports": [_open(now), {**_day(now), "crashes": 1.5}]},
        ]
        for body in wrong:
            refused: httpx.Response = await client.post("/phone/metrics", json=body, headers=phone)
            assert refused.status_code == 422, (body, refused.text)
        assert await reports_of(runtime.store, phone_id) == [], "nothing of a refused request is kept"
