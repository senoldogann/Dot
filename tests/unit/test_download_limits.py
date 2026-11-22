"""What a download may take (item 1298): the room the cap and the folder's quota leave, the folder's weight, and the
defaults the config ships with."""

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from dotagent.browser.pages import (
    DownloadLimits,
    Tab,
    _megabytes,
    _on_download,
    _on_download_begun,
    _on_download_progress,
    _room,
    folder_bytes,
)
from dotagent.config import load_config, load_default_config

_MB: int = 1024 * 1024


def test_the_defaults_are_five_hundred_megabytes_a_file_and_five_gigabytes_a_folder() -> None:
    browser = load_default_config().browser

    assert browser.download_max_mb == 500
    assert browser.download_quota_mb == 5 * 1024


def test_zero_takes_a_limit_off_and_a_negative_one_is_refused(tmp_path: Path) -> None:
    settings: Path = tmp_path / "config.toml"
    settings.write_text("[browser]\ndownload_max_mb = 0\ndownload_quota_mb = 0\n", encoding="utf-8")
    assert load_config(settings).browser.download_max_mb == 0

    settings.write_text("[browser]\ndownload_max_mb = -1\n", encoding="utf-8")
    with pytest.raises(Exception, match="download_max_mb"):
        load_config(settings)


def test_with_no_limit_a_download_has_no_room_to_stay_within() -> None:
    assert _room(DownloadLimits(), held=10 * _MB) == (None, "")


def test_the_smaller_of_the_cap_and_what_the_quota_leaves_is_the_room_and_names_its_limit() -> None:
    limits = DownloadLimits(max_bytes=500 * _MB, quota_bytes=1000 * _MB)

    room, why = _room(limits, held=100 * _MB)
    assert room == 500 * _MB
    assert "download_max_mb" in why

    room, why = _room(limits, held=700 * _MB)
    assert room == 300 * _MB
    assert "download_quota_mb" in why
    assert "700 MB of 1000 MB" in why


def test_a_folder_at_its_quota_has_no_room_left() -> None:
    room, _ = _room(DownloadLimits(quota_bytes=_MB), held=3 * _MB)

    assert room == 0


def test_a_folder_is_weighed_by_every_file_under_it_but_the_one_just_taken(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"1" * 100)
    (tmp_path / "deeper").mkdir()
    (tmp_path / "deeper" / "b.bin").write_bytes(b"1" * 50)
    fresh: Path = tmp_path / "fresh.bin"
    fresh.write_bytes(b"")

    assert folder_bytes(tmp_path, leave=fresh) == 150
    assert folder_bytes(tmp_path) == 150


def test_a_size_is_said_in_megabytes_once_it_is_one() -> None:
    assert _megabytes(512) == "512 bytes"
    assert _megabytes(5 * _MB) == "5 MB"


class _Download:
    """A download as Playwright hands it over, one that Chrome reports in progress through ``progress`` while it is
    saved: ``stream`` sizes are sent to the tab's session as the bytes taken."""

    def __init__(self, tab: Tab, url: str, guid: str, stream: list[int]) -> None:
        self.tab = tab
        self.url = url
        self.guid = guid
        self.stream = stream
        self.suggested_filename = "talk.bin"
        self.cancelled = asyncio.Event()

    async def cancel(self) -> None:
        self.cancelled.set()

    async def save_as(self, target: Path) -> None:
        for size in self.stream:
            _on_download_progress(self.tab, {"guid": self.guid, "receivedBytes": size, "totalBytes": 0})
            await asyncio.sleep(0)
            if self.cancelled.is_set():
                return
        await asyncio.sleep(0.05)

    async def failure(self) -> str | None:
        return None


async def test_a_download_turned_away_at_the_quota_leaves_no_id_for_the_next_one_of_its_address(
    tmp_path: Path,
) -> None:
    """The first is refused where the folder is full; Chrome's id for it must not be handed to a later download of the
    same address, whose own progress would then go unheard and an endless stream would never be cancelled."""
    tab: Tab = Tab(page=cast(Any, MagicMock()))
    url: str = "https://files.example/talk.bin"
    (tmp_path / "old.bin").write_bytes(b"1" * 2 * _MB)
    full: DownloadLimits = DownloadLimits(quota_bytes=_MB)

    _on_download_begun(tab, {"guid": "first", "url": url})
    await _on_download(tab, tmp_path, full, cast(Any, _Download(tab, url, "first", [])))
    assert tab.download_begun == {}

    (tmp_path / "old.bin").unlink()
    roomy: DownloadLimits = DownloadLimits(max_bytes=_MB)
    _on_download_begun(tab, {"guid": "second", "url": url})
    second: _Download = _Download(tab, url, "second", [3 * _MB, 4 * _MB])
    await _on_download(tab, tmp_path, roomy, cast(Any, second))

    assert second.cancelled.is_set(), "the stream was not cancelled while it came in"
    assert tab.download_begun == {} and tab.download_ids == set()
